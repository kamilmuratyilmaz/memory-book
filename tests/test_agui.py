"""AG-UI chat editing: streamed reply and reasoning, live state deltas, approval interrupts,
server-side threads, activity progress, messages snapshots, token usage and failures."""

import copy
import json

import jsonpatch
import litellm
from ag_ui.core import RunAgentInput
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices, Usage
from test_backend import FakeProvider, make_book

from memory_book.agui import json_diff, run_agent
from memory_book.ai import (
    AIError, LLMProvider, LocalProvider, OpApplyTemplate, OpDeletePage, OpSetText, OpSetTheme, RevisionPlan,
)

SHOW_PAGE = {"name": "show_page", "description": "Show a page", "parameters": {"type": "object"}}


def agent_input(book, text="Make the whole book feel more vintage", tools=(), focus=None, resume=None, messages=None):
    if messages is None:
        messages = [{"id": f"u-{abs(hash(text)) % 10**8}", "role": "user", "content": text}] if text else []
    return RunAgentInput.model_validate({
        "threadId": f"book-{book.id}", "runId": "run", "state": {"book": book.model_dump(mode="json")},
        "messages": messages, "tools": list(tools), "forwardedProps": {}, "resume": resume,
        "context": [{"description": "editor-focus", "value": json.dumps(focus or {})}],
    })


def run(book, provider, store, **kw):
    return list(run_agent(agent_input(book, **kw), provider, store))


def replay(book, events):
    """Apply every STATE_DELTA like an AG-UI client does."""
    state = {"book": book.model_dump(mode="json")}
    for e in events:
        if e.type == "STATE_DELTA":
            state = jsonpatch.apply_patch(state, [d.model_dump(by_alias=True, exclude_none=True) for d in e.delta])
    return state["book"]


def types(events):
    return [e.type for e in events]


def test_json_diff_round_trips_and_matches_lists_by_id():
    a = {"x": [1, {"y": 2}], "k/~": "v", "gone": 1, "list": [1, 2]}
    b = {"x": [1, {"y": 3, "z": [1]}], "k/~": "w", "new": {"a": None}, "list": [1, 2, 3]}
    assert jsonpatch.apply_patch(copy.deepcopy(a), json_diff(a, b)) == b
    assert json_diff(a, a) == []
    pages = [{"id": p, "n": i} for i, p in enumerate("abcde")]
    target = [{"id": "e", "n": 4}, {"id": "a", "n": 0}, {"id": "x", "n": 9}, {"id": "c", "n": 7}, {"id": "d", "n": 3}]
    ops = json_diff({"pages": pages}, {"pages": target})
    assert jsonpatch.apply_patch({"pages": copy.deepcopy(pages)}, ops) == {"pages": target}
    assert {o["op"] for o in ops} <= {"remove", "move", "add", "replace"}
    assert not any(o["path"] == "/pages" for o in ops)  # never the whole list
    insert = json_diff({"pages": pages}, {"pages": pages[:2] + [{"id": "new"}] + pages[2:]})
    assert insert == [{"op": "add", "path": "/pages/2", "value": {"id": "new"}}]


def test_run_streams_reply_reasoning_activity_and_snapshot(store, photos):
    book = make_book(store, photos)
    events = run(book, LocalProvider(), store)
    t = types(events)
    assert t[0] == "RUN_STARTED" and t[-1] == "RUN_FINISHED" and t[-2] == "MESSAGES_SNAPSHOT"
    order = ["STEP_STARTED", "REASONING_START", "REASONING_MESSAGE_CONTENT", "REASONING_END", "TEXT_MESSAGE_START",
             "TEXT_MESSAGE_END", "ACTIVITY_SNAPSHOT", "TOOL_CALL_START", "TOOL_CALL_RESULT", "STATE_DELTA",
             "ACTIVITY_DELTA", "MESSAGES_SNAPSHOT"]
    assert [t.index(x) for x in order] == sorted(t.index(x) for x in order)
    assert replay(book, events)["themeId"] == "vintage"
    text_id = next(e.message_id for e in events if e.type == "TEXT_MESSAGE_START")
    assert next(e for e in events if e.type == "TOOL_CALL_START").parent_message_id == text_id
    snapshot = next(e for e in events if e.type == "MESSAGES_SNAPSHOT").messages
    roles = [m.role for m in snapshot]
    assert roles == ["user", "reasoning", "assistant", "activity", "tool"]
    assert snapshot[2].tool_calls[0].function.name == "set_theme"
    assert snapshot[3].content["status"] == "done" and snapshot[3].content["items"][0]["status"] == "applied"
    assert store.get_thread(f"book-{book.id}")["messages"][2]["content"].startswith("Restyled")
    finished = events[-1]
    assert finished.outcome.type == "success" and finished.usage is None  # offline: no model tokens


def test_server_keeps_the_thread_and_feeds_history(store, photos):
    book = make_book(store, photos)
    body = next(e for p in book.pages for e in p.elements if e.type == "text" and e.role == "body")
    fake = FakeProvider(plan=RevisionPlan(summary="Calmer now.", operations=[
        OpSetText(op="set_text", elementId=body.id, text="Calm.")]))
    run(book, fake, store, text="Make page 2 calmer")
    # the client sends only the new message; the server supplies the rest of the thread
    run(book, fake, store, text="now shorter")
    assert fake.history == [{"role": "user", "content": "Make page 2 calmer"}, {"role": "assistant", "content": "Calmer now. "}]
    thread = store.get_thread(f"book-{book.id}")
    assert [m["role"] for m in thread["messages"] if m["role"] in ("user", "assistant")] == ["user", "assistant"] * 2


def test_approval_interrupt_then_resume(store, photos):
    book = make_book(store, photos)
    doomed = book.pages[2].id
    fake = FakeProvider(plan=RevisionPlan(summary="I'll remove page 2.", operations=[
        OpDeletePage(op="delete_page", pageId=doomed)]))
    events = run(book, fake, store, text="remove page 2")
    finished = events[-1]
    assert finished.outcome.type == "interrupt"
    interrupt = finished.outcome.interrupts[0]
    assert interrupt.reason == "approval_required" and "removes 1 page" in interrupt.message
    assert "STATE_DELTA" not in types(events)  # nothing applied yet
    proposal = next(e for e in events if e.type == "ACTIVITY_SNAPSHOT").content
    assert proposal["status"] == "proposed" and proposal["items"][0]["label"] == "Remove page 2"
    assert store.get_thread(f"book-{book.id}")["pending"]["interrupt"]["id"] == interrupt.id

    resumed = run(book, FakeProvider(), store, text=None,
                  resume=[{"interruptId": interrupt.id, "status": "resolved", "payload": {"approved": True}}])
    assert resumed[-1].outcome.type == "success"
    assert doomed not in [p["id"] for p in replay(book, resumed)["pages"]]
    assert store.get_thread(f"book-{book.id}")["pending"] is None

    again = run(book, FakeProvider(), store, text=None,
                resume=[{"interruptId": interrupt.id, "status": "resolved"}])
    assert again[-1].type == "RUN_ERROR" and again[-1].code == "unknown_interrupt"


def test_text_rewrites_across_pages_need_approval_and_only_an_explicit_yes_applies(store, photos):
    book = make_book(store, photos)
    texts = [t for p in book.pages if (t := next((e for e in p.elements if e.type == "text"), None))]
    assert len(texts) == 4  # one text per page, over the 3-page limit
    rewrite = RevisionPlan(summary="Rewrote it all.", operations=[
        OpSetText(op="set_text", elementId=e.id, text="New words.") for e in texts])
    events = run(book, FakeProvider(plan=rewrite), store, text="rewrite every page")
    assert events[-1].outcome.type == "interrupt" and "changes 4 pages" in events[-1].outcome.interrupts[0].message
    assert "STATE_DELTA" not in types(events)
    vague = run(book, FakeProvider(), store, text=None, resume=[
        {"interruptId": events[-1].outcome.interrupts[0].id, "status": "resolved", "payload": {"approved": "no"}}])
    assert "STATE_DELTA" not in types(vague)  # anything but approved: true leaves the book alone


def test_cancel_and_superseded_approvals(store, photos):
    book = make_book(store, photos)
    big = RevisionPlan(summary="Big redesign.", operations=[
        OpApplyTemplate(op="apply_template", pageId=p.id, template="minimal-text") for p in book.pages[1:6]])
    first = run(book, FakeProvider(plan=big), store, text="redesign everything")
    assert "changes 5 pages" in first[-1].outcome.interrupts[0].message
    cancelled = run(book, FakeProvider(), store, text=None,
                    resume=[{"interruptId": first[-1].outcome.interrupts[0].id, "status": "cancelled"}])
    assert "STATE_DELTA" not in types(cancelled)
    assert "left the book" in "".join(e.delta for e in cancelled if e.type == "TEXT_MESSAGE_CONTENT")
    second = run(book, FakeProvider(plan=big), store, text="redesign everything again")
    run(book, LocalProvider(), store, text="Make the whole book feel more vintage")  # a new request instead
    assert store.get_thread(f"book-{book.id}")["pending"] is None
    stale = run(book, FakeProvider(), store, text=None,
                resume=[{"interruptId": second[-1].outcome.interrupts[0].id, "status": "resolved"}])
    assert stale[-1].code == "unknown_interrupt"


MODEL = "gemini/gemini-3.8-flash"


def _chunk(content=None, reasoning=None, finish=None):
    return ModelResponseStream(model=MODEL, choices=[StreamingChoices(
        index=0, delta=Delta(content=content, reasoning_content=reasoning), finish_reason=finish)])


def _llm(chunks, fail=False):
    """An LLMProvider whose LiteLLM stream yields these chunks (then optionally drops the connection)."""
    provider = LLMProvider(MODEL)

    def completion(**kw):
        assert kw["stream"] and kw["response_format"]["json_schema"]["name"] == "RevisionPlan"

        def stream():
            yield from chunks
            if fail:
                raise litellm.APIConnectionError("connection reset", "gemini", MODEL)
        return stream()
    provider.completion = completion
    return provider


def test_llm_streams_tokens_reasoning_and_usage(store, photos):
    book = make_book(store, photos)
    plan = RevisionPlan(summary="Giving the book a vintage feel.", operations=[OpSetTheme(op="set_theme", themeId="vintage")])
    raw = plan.model_dump_json()
    chunks = [_chunk(reasoning="The user wants an older look. "), _chunk(reasoning="Vintage fits.")]
    chunks += [_chunk(content=raw[i:i + 5]) for i in range(0, len(raw), 5)] + [_chunk(finish="stop")]
    usage = ModelResponseStream(model=MODEL, choices=[])
    usage.usage = Usage(prompt_tokens=1000, completion_tokens=120, total_tokens=1120, cache_read_input_tokens=100)
    events = run(book, _llm(chunks + [usage]), store)
    reply = [e.delta for e in events if e.type == "TEXT_MESSAGE_CONTENT"]
    assert len(reply) > 3 and "".join(reply) == plan.summary  # token by token, not one block
    assert types(events).index("TEXT_MESSAGE_END") < types(events).index("TOOL_CALL_START")
    assert "".join(e.delta for e in events if e.type == "REASONING_MESSAGE_CONTENT") == \
        "The user wants an older look. Vintage fits."
    assert replay(book, events)["themeId"] == "vintage"
    u = events[-1].usage[0]
    assert (u.provider, u.model, u.input_tokens, u.output_tokens, u.total_tokens, u.cached_input_tokens) == \
        ("gemini", MODEL, 1000, 120, 1120, 100)


def test_failed_stream_drops_partial_reply_and_falls_back(store, photos):
    book = make_book(store, photos)
    events = run(book, _llm([_chunk(content='{"summary": "Half a sen')], fail=True), store)
    assert events[-1].type == "RUN_FINISHED"
    snapshot = next(e for e in events if e.type == "MESSAGES_SNAPSHOT").messages
    replies = [m.content for m in snapshot if m.role == "assistant"]
    assert replies and not any("Half a sen" in r for r in replies)  # the abandoned text is gone
    assert replay(book, events)["themeId"] == "vintage"  # offline rules did the work


def test_frontend_tool_show_page_when_offered(store, photos):
    book = make_book(store, photos)
    page = book.pages[3]
    fake = FakeProvider(plan=RevisionPlan(summary="Ok.", operations=[
        OpApplyTemplate(op="apply_template", pageId=page.id, template="minimal-text")]))
    events = run(book, fake, store, text="tidy page 3", tools=[SHOW_PAGE])
    call = next(e for e in events if e.type == "TOOL_CALL_START" and e.tool_call_name == "show_page")
    args = next(e for e in events if e.type == "TOOL_CALL_ARGS" and e.tool_call_id == call.tool_call_id)
    assert page.id in args.delta
    assert not any(e.type == "TOOL_CALL_RESULT" and e.tool_call_id == call.tool_call_id for e in events)  # client answers


def test_failures_become_run_errors(store, photos):
    book = make_book(store, photos)

    class Broken(FakeProvider):
        def revise(self, *a, **k):
            raise AIError("timeout")
    events = run(book, Broken(), store, text="write it like a poem")
    assert events[-1].type == "RUN_ERROR" and events[-1].code == "ai_error" and "unchanged" in events[-1].message
    assert "STATE_DELTA" not in types(events)
    assert store.get_thread(f"book-{book.id}")["messages"][0]["content"] == "write it like a poem"  # kept
    bad = agent_input(book)
    bad.state = {"book": {"pageSize": "Letter"}}
    assert list(run_agent(bad, LocalProvider(), store))[-1].code == "bad_input"
    assert run(make_book(store, photos), LocalProvider(), store, text=None)[-1].code == "bad_input"
