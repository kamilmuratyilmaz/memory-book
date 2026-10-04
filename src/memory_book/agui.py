"""Chat editing over the AG-UI protocol (https://docs.ag-ui.com), version 1.0.

One run = one user request (or one approval decision). Shared state is {"book": MemoryBook}; the chat thread
(`book-<id>`) is stored on the server. A run streams:

  RUN_STARTED
  STEP_STARTED planning
    REASONING_* ........ the model's thinking summary, live
    TEXT_MESSAGE_* ..... the reply, token by token, while the plan is still being written
  STEP_FINISHED planning
  -- large or destructive plans stop here for approval:
     ACTIVITY_SNAPSHOT (proposed changes) . MESSAGES_SNAPSHOT . RUN_FINISHED outcome=interrupt
     the next run carries RunAgentInput.resume = [{interruptId, status: resolved|cancelled}]
  STEP_STARTED applying
    ACTIVITY_SNAPSHOT .. checklist of the planned changes
    per operation: TOOL_CALL_START/ARGS/END . TOOL_CALL_RESULT . STATE_DELTA (JSON Patch) . ACTIVITY_DELTA
  STEP_FINISHED applying
  TOOL_CALL_* show_page (frontend tool, when offered by the client)
  MESSAGES_SNAPSHOT ... the thread as stored on the server
  RUN_FINISHED ........ outcome success + token usage      (or RUN_ERROR)
"""

from __future__ import annotations

import copy
import json
import logging
import queue
import threading
import uuid
from collections.abc import Callable, Iterator

import mlflow
from ag_ui.core import (
    ActivityDeltaEvent,
    ActivitySnapshotEvent,
    BaseEvent,
    EventType,
    MessagesSnapshotEvent,
    ReasoningEndEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageEndEvent,
    ReasoningMessageStartEvent,
    ReasoningStartEvent,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateDeltaEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.core.events import RunFinishedInterruptOutcome, RunFinishedSuccessOutcome, TokenUsage
from pydantic import ValidationError

from . import design, tracing
from .ai import AIError, AIProvider, RevisionPlan
from .layout import TEMPLATES
from .model import MemoryBook
from .pipeline import apply_revision, plan_revision
from .storage import Store
from .tracing import SpanType

log = logging.getLogger(__name__)

HISTORY_LIMIT = 12  # recent text turns passed to the model for references like "make it shorter"
APPROVAL_PAGE_LIMIT = 3  # plans that touch more pages than this (or delete any) wait for the user's approval
ACTIVITY = "book-changes"


class BadInput(Exception):
    def __init__(self, message: str, code: str = "bad_input"):
        super().__init__(message)
        self.code = code


# --- JSON Patch --------------------------------------------------------------------

def _ptr(key) -> str:
    return str(key).replace("~", "~0").replace("/", "~1")


def json_diff(a, b, path: str = "") -> list[dict]:
    """RFC 6902 patch turning a into b.

    Lists of objects with unique "id"s (pages, elements, chapters) are matched by id, so inserting,
    removing or reordering a page sends add/remove/move operations instead of the whole list.
    """
    if type(a) is not type(b) or not isinstance(a, (dict, list)):
        return [] if a == b else [{"op": "replace", "path": path, "value": b}]
    if isinstance(a, dict):
        ops = []
        for k in a.keys() | b.keys():
            p = f"{path}/{_ptr(k)}"
            if k not in b:
                ops.append({"op": "remove", "path": p})
            elif k not in a:
                ops.append({"op": "add", "path": p, "value": b[k]})
            else:
                ops += json_diff(a[k], b[k], p)
        return ops
    a_ids, b_ids = _ids(a), _ids(b)
    if a_ids is None or b_ids is None:
        if len(a) != len(b):
            return [{"op": "replace", "path": path, "value": b}]
        return [op for i, (x, y) in enumerate(zip(a, b)) for op in json_diff(x, y, f"{path}/{i}")]
    ops: list[dict] = []
    keep = set(b_ids)
    for i in range(len(a) - 1, -1, -1):  # remove from the end so earlier indexes stay valid
        if a_ids[i] not in keep:
            ops.append({"op": "remove", "path": f"{path}/{i}"})
    cur = [x for x, xid in zip(a, a_ids) if xid in keep]
    cur_ids = [x["id"] for x in cur]
    for i, item in enumerate(b):
        if i < len(cur_ids) and cur_ids[i] == item["id"]:
            ops += json_diff(cur[i], item, f"{path}/{i}")
        elif item["id"] in cur_ids:
            j = cur_ids.index(item["id"])
            ops.append({"op": "move", "from": f"{path}/{j}", "path": f"{path}/{i}"})
            cur.insert(i, cur.pop(j))
            cur_ids.insert(i, cur_ids.pop(j))
            ops += json_diff(cur[i], item, f"{path}/{i}")
        else:
            ops.append({"op": "add", "path": f"{path}/{i}", "value": item})
            cur.insert(i, item)
            cur_ids.insert(i, item["id"])
    return ops


def _ids(items: list) -> list | None:
    """The ids of a list of objects, or None when the list cannot be matched by id."""
    if not items:
        return []
    if not all(isinstance(x, dict) and "id" in x for x in items):
        return None
    ids = [x["id"] for x in items]
    return ids if len(set(ids)) == len(ids) else None


# --- plan helpers ----------------------------------------------------------------------

def _page_no(book: MemoryBook, page_id: str | None) -> str:
    i = next((i for i, p in enumerate(book.pages) if p.id == page_id), None)
    return "the cover" if i == 0 else f"page {i}" if i is not None else "a page"


def describe(op, book: MemoryBook) -> str:
    """A short label for one operation, for the activity checklist and the approval card."""
    t = op.op
    if t == "set_theme":
        return f"Change the theme to {design.theme(op.themeId)['name']}"
    if t == "apply_template":
        return f"Use the “{TEMPLATES[op.template]['name']}” layout on {_page_no(book, op.pageId)}"
    if t == "replace_page":
        return f"Redesign {_page_no(book, op.pageId)}"
    if t == "insert_page":
        return f"Add a page after {_page_no(book, op.afterPageId)}" if op.afterPageId else "Add a page at the end"
    if t == "delete_page":
        return f"Remove {_page_no(book, op.pageId)}"
    if t == "move_page":
        return f"Move {_page_no(book, op.pageId)} to position {op.toIndex}"
    if t == "move_photo":
        return f"Move a photo from {_page_no(book, op.fromPageId)} to {_page_no(book, op.toPageId)}"
    if t == "set_background":
        return f"Change the background of {_page_no(book, op.pageId)}"
    if t == "add_chapter":
        return f"Make the chapter “{op.title}”"
    if t == "set_book":
        return "Change the cover text"
    if t == "set_text":
        page = next((p for p in book.pages for e in p.elements if e.id == op.elementId), None)
        return f"Rewrite text on {_page_no(book, page.id if page else None)}"
    return "Adjust the typography"


def needs_approval(plan: RevisionPlan, book: MemoryBook) -> str | None:
    """Why this plan should wait for the user's approval, or None to apply it at once."""
    deletes = sum(op.op == "delete_page" for op in plan.operations)
    # Content changes count per page (text edits name an element, so map it to its page). Whole-book style changes
    # (set_theme, set_style by role) change no words or photos and are one undo away: they need no approval.
    page_of_element = {e.id: p.id for p in book.pages for e in p.elements}
    pages: set[str] = set()
    for n, op in enumerate(plan.operations):
        pages.update(filter(None, [getattr(op, k, None) for k in ("pageId", "fromPageId", "toPageId")]))
        pages.update(getattr(op, "pageIds", []) or [])
        if element := getattr(op, "elementId", None):
            pages.add(page_of_element.get(element, f"element-{element}"))
        if op.op == "insert_page":
            pages.add(f"new-{n}")
    if deletes:
        return f"This removes {deletes} page{'s' if deletes > 1 else ''}. Do you want to apply these changes?"
    if len(pages) > APPROVAL_PAGE_LIMIT:
        return f"This changes {len(pages)} pages. Do you want to apply these changes?"
    return None


def _usage(records: list[dict]) -> list[TokenUsage] | None:
    """Token usage per provider and model that actually answered (fallback models are listed separately)."""
    totals: dict[tuple[str, str], dict] = {}
    for r in records:
        agg = totals.setdefault((r.get("provider") or "unknown", r["model"] or "unknown"), {})
        for k, v in r.items():
            if isinstance(v, int):
                agg[k] = agg.get(k, 0) + v
    return [TokenUsage(provider=p, model=m, input_tokens=u["input_tokens"], output_tokens=u["output_tokens"],
                       total_tokens=u["total_tokens"], cached_input_tokens=u["cache_read_input_tokens"],
                       cache_write_input_tokens=u["cache_creation_input_tokens"])
            for (p, m), u in totals.items()] or None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") if isinstance(p, dict) else getattr(p, "text", "") for p in content or [])


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


# --- event + message bookkeeping for one run -----------------------------------------------

class Output:
    """Emits AG-UI events and records the messages they form, for the stored thread and MESSAGES_SNAPSHOT."""

    def __init__(self, emit: Callable[[BaseEvent], None]):
        self.emit = emit
        self.messages: list[dict] = []
        self.assistant: dict | None = None
        self.text_open = False
        self.reasoning: dict | None = None
        self.activity: dict | None = None

    def model_output(self, kind: str, delta: str) -> None:
        """The `emit` callback handed to AI providers."""
        if kind == "reasoning":
            if self.reasoning is None:
                self.reasoning = {"id": _new_id(), "role": "reasoning", "content": ""}
                self.emit(ReasoningStartEvent(type=EventType.REASONING_START, message_id=self.reasoning["id"]))
                self.emit(ReasoningMessageStartEvent(type=EventType.REASONING_MESSAGE_START,
                                                     message_id=self.reasoning["id"], role="reasoning"))
            self.reasoning["content"] += delta
            self.emit(ReasoningMessageContentEvent(type=EventType.REASONING_MESSAGE_CONTENT,
                                                   message_id=self.reasoning["id"], delta=delta))
        elif kind == "text":
            self._end_reasoning()
            if not self.text_open:
                self.assistant = {"id": _new_id(), "role": "assistant", "content": ""}
                self.messages.append(self.assistant)
                self.text_open = True
                self.emit(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=self.assistant["id"],
                                                role="assistant"))
            self.assistant["content"] += delta
            self.emit(TextMessageContentEvent(type=EventType.TEXT_MESSAGE_CONTENT, message_id=self.assistant["id"],
                                              delta=delta))
        elif kind == "reset" and self.assistant:  # a failed attempt: its partial reply leaves the thread
            self._end_text()
            self.messages.remove(self.assistant)
            self.assistant = None

    def _end_reasoning(self) -> None:
        if self.reasoning:
            mid = self.reasoning["id"]
            self.emit(ReasoningMessageEndEvent(type=EventType.REASONING_MESSAGE_END, message_id=mid))
            self.emit(ReasoningEndEvent(type=EventType.REASONING_END, message_id=mid))
            self.messages.append(self.reasoning)
            self.reasoning = None

    def _end_text(self) -> None:
        if self.text_open:
            self.emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=self.assistant["id"]))
            self.text_open = False

    def reply(self, text: str) -> None:
        """Finish the reply; stream `text` word by word if the provider did not stream one."""
        self._end_reasoning()
        if self.assistant is None and text:
            for word in text.split(" "):
                self.model_output("text", word + " ")
        self._end_text()

    def step(self, name: str, start: bool) -> None:
        if start:
            self.emit(StepStartedEvent(type=EventType.STEP_STARTED, step_name=name))
        else:
            self.emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=name))

    def tool_call(self, name: str, args: dict) -> str:
        call_id = _new_id()
        parent = self.assistant["id"] if self.assistant else None
        self.emit(ToolCallStartEvent(type=EventType.TOOL_CALL_START, tool_call_id=call_id, tool_call_name=name,
                                     parent_message_id=parent))
        self.emit(ToolCallArgsEvent(type=EventType.TOOL_CALL_ARGS, tool_call_id=call_id, delta=json.dumps(args)))
        self.emit(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=call_id))
        if self.assistant is not None:
            self.assistant.setdefault("toolCalls", []).append(
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}})
        return call_id

    def tool_result(self, call_id: str, content: str) -> None:
        msg = {"id": _new_id(), "role": "tool", "toolCallId": call_id, "content": content}
        self.emit(ToolCallResultEvent(type=EventType.TOOL_CALL_RESULT, message_id=msg["id"], tool_call_id=call_id,
                                      role="tool", content=content))
        self.messages.append(msg)

    def activity_snapshot(self, status: str, items: list[dict]) -> None:
        content = {"status": status, "done": 0, "total": len(items), "items": items}
        self.activity = {"id": _new_id(), "role": "activity", "activityType": ACTIVITY, "content": content}
        self.messages.append(self.activity)
        # a copy: activity_patch() keeps changing `content`, and the event may be encoded later (on another thread)
        self.emit(ActivitySnapshotEvent(type=EventType.ACTIVITY_SNAPSHOT, message_id=self.activity["id"],
                                        activity_type=ACTIVITY, content=copy.deepcopy(content), replace=True))

    def activity_patch(self, patch: list[dict]) -> None:
        content = self.activity["content"]
        for p in patch:  # mirror the "replace" patch locally so the stored message stays current
            *parents, last = p["path"].strip("/").split("/")
            target = content
            for k in parents:
                target = target[int(k)] if isinstance(target, list) else target[k]
            target[int(last) if isinstance(target, list) else last] = p["value"]
        self.emit(ActivityDeltaEvent(type=EventType.ACTIVITY_DELTA, message_id=self.activity["id"],
                                     activity_type=ACTIVITY, patch=patch))


# --- the run ---------------------------------------------------------------------------------

def run_agent(inp: RunAgentInput, provider: AIProvider, store: Store) -> Iterator[BaseEvent]:
    """Stream one run. The work happens in a worker thread so model tokens and patches stream live, and the
    whole MLflow trace stays in one thread (Starlette advances this generator from a thread pool)."""
    events: queue.Queue[BaseEvent | None] = queue.Queue()

    def work():
        try:
            _run(inp, provider, store, events.put)
        except Exception:
            log.exception("agent run failed")
            events.put(RunErrorEvent(type=EventType.RUN_ERROR, code="internal",
                                     message="Something went wrong on our side. Your book is unchanged."))
        finally:
            events.put(None)

    # ponytail: one thread per run; a run started before the client disconnects still finishes and is saved
    threading.Thread(target=work, daemon=True).start()
    while (event := events.get()) is not None:
        yield event


def _run(inp: RunAgentInput, provider: AIProvider, store: Store, emit: Callable[[BaseEvent], None]) -> None:
    emit(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=inp.thread_id, run_id=inp.run_id))
    try:
        book = MemoryBook.model_validate((inp.state if isinstance(inp.state, dict) else {}).get("book"))
    except ValidationError:
        emit(RunErrorEvent(type=EventType.RUN_ERROR, message="The book sent with this request is not valid.",
                           code="bad_input"))
        return
    scope = next((json.loads(c.value) for c in inp.context or [] if c.description == "editor-focus"), {})
    thread = store.get_thread(inp.thread_id)
    known = {m["id"] for m in thread["messages"]}
    messages = thread["messages"] + [m.model_dump(mode="json", by_alias=True, exclude_none=True)
                                     for m in inp.messages if m.id not in known]
    out = Output(emit)
    with tracing.usage_meter() as usage:
        try:
            with mlflow.start_span(name="agent_run", span_type=SpanType.AGENT) as root:
                root.set_attributes({"thread_id": inp.thread_id, "run_id": inp.run_id, "provider": provider.name,
                                     "resume": bool(inp.resume)})
                tracing.set_session(tracing.session_for_book(book.id), kind="chat", run_id=inp.run_id,
                                    provider=provider.name)
                if inp.resume:
                    pending = _resume(inp, book, thread["pending"], out, store, root)
                else:
                    pending = _request(inp, book, messages, scope, provider, store, out, root)
        except (AIError, BadInput) as e:
            out.reply("")
            store.save_thread(inp.thread_id, messages + out.messages, thread["pending"])
            if isinstance(e, BadInput):
                message, code = str(e), e.code
            else:
                message, code = f"The AI couldn't make that change ({e}). Your book is unchanged.", "ai_error"
            emit(RunErrorEvent(type=EventType.RUN_ERROR, message=message, code=code, usage=_usage(usage)))
            return
    thread_messages = messages + out.messages
    store.save_thread(inp.thread_id, thread_messages, pending)
    emit(MessagesSnapshotEvent(type=EventType.MESSAGES_SNAPSHOT, messages=thread_messages))
    outcome = (RunFinishedInterruptOutcome(type="interrupt", interrupts=[pending["interrupt"]]) if pending
               else RunFinishedSuccessOutcome(type="success"))
    emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=inp.thread_id, run_id=inp.run_id, outcome=outcome,
                          usage=_usage(usage), result={"awaitingApproval": bool(pending)}))


def _request(inp, book, messages, scope, provider, store, out: Output, root) -> dict | None:
    talk = [{"role": m["role"], "content": _text(m.get("content"))} for m in messages
            if m["role"] in ("user", "assistant") and _text(m.get("content")).strip()]
    if not talk or talk[-1]["role"] != "user":
        raise BadInput("Send a message describing the change you want.")
    instruction, history = talk[-1]["content"], talk[:-1][-HISTORY_LIMIT:]
    root.set_inputs({"instruction": tracing.content(instruction), "historyTurns": len(history), "focus": scope})
    out.step("planning", True)
    with mlflow.start_span(name="plan_revision", span_type=SpanType.CHAIN) as s:
        plan = plan_revision(book, instruction, scope, provider, store, history, out.model_output)
        s.set_outputs({"operations": [op.op for op in plan.operations], "summary": tracing.content(plan.summary)})
    out.reply(plan.summary)
    out.step("planning", False)
    if reason := needs_approval(plan, book):
        interrupt = {"id": _new_id(), "reason": "approval_required", "message": reason,
                     "responseSchema": {"type": "object", "properties": {"approved": {"type": "boolean"}}}}
        out.activity_snapshot("proposed", [{"op": op.op, "label": describe(op, book), "status": "proposed"}
                                           for op in plan.operations])
        root.set_outputs({"awaitingApproval": True, "operations": len(plan.operations)})
        return {"interrupt": interrupt, "plan": plan.model_dump(mode="json")}
    _apply(plan, book, scope, inp, store, out, root)
    return None  # a new request replaces any approval that was still open


def _resume(inp, book, pending, out: Output, store, root) -> None:
    entry = inp.resume[0]
    if not pending or pending["interrupt"]["id"] != entry.interrupt_id:
        raise BadInput("This approval request is no longer open. Ask for the change again.", "unknown_interrupt")
    plan = RevisionPlan.model_validate(pending["plan"])  # the plan we stored, never one sent by the client
    approved = entry.status == "resolved" and (entry.payload or {}).get("approved") is True  # only an explicit yes
    root.set_inputs({"interrupt": entry.interrupt_id, "approved": approved})
    if not approved:
        out.reply("OK, I left the book as it was.")
        root.set_outputs({"approved": False})
        return None
    out.reply("Applying the changes you approved.")
    _apply(plan, book, {}, inp, store, out, root)
    return None


def _apply(plan: RevisionPlan, book: MemoryBook, scope: dict, inp, store, out: Output, root) -> None:
    out.step("applying", True)
    out.activity_snapshot("applying", [{"op": op.op, "label": describe(op, book), "status": "pending"}
                                       for op in plan.operations])
    changed_pages: list[str] = []
    skipped_count = 0
    current = book.model_dump(mode="json")
    for i, op in enumerate(plan.operations):
        args = op.model_dump(mode="json", exclude={"op"})
        with mlflow.start_span(name=op.op, span_type=SpanType.TOOL) as t:
            t.set_inputs(tracing.content(args))
            call_id = out.tool_call(op.op, args)
            book, skipped = apply_revision(book, RevisionPlan(summary="", operations=[op]), store)
            after = book.model_dump(mode="json")
            delta = json_diff(current, after, "/book")
            result = f"skipped: {skipped[0]}" if skipped else "applied"
            t.set_outputs({"result": result, "patchOperations": len(delta)})
        skipped_count += bool(skipped)
        out.tool_result(call_id, result)
        if delta:
            out.emit(StateDeltaEvent(type=EventType.STATE_DELTA, delta=delta))
            current = after
            if pid := getattr(op, "pageId", None):
                changed_pages.append(pid)
        out.activity_patch([{"op": "replace", "path": f"/items/{i}/status", "value": "skipped" if skipped else "applied"},
                            {"op": "replace", "path": "/done", "value": i + 1}])
    out.activity_patch([{"op": "replace", "path": "/status", "value": "done"}])
    out.step("applying", False)

    # Frontend tool: turn the editor to the page we changed, if the client offered that tool.
    page_ids = {p.id for p in book.pages}
    target = next((p for p in reversed(changed_pages) if p in page_ids and p != scope.get("pageId")), None)
    if target and any(t.name == "show_page" for t in inp.tools or []):
        out.tool_call("show_page", {"pageId": target})
    root.set_outputs({"summary": tracing.content(plan.summary), "operations": len(plan.operations),
                      "skipped": skipped_count})
