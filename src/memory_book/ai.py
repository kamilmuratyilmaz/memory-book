"""AI providers. The AI emits structured data only — never markup or UI code.

Stages (each with an explicit, validated schema):
  analyze()    raw input        -> MemoryAnalysis  (understanding + memory extraction)
  plan_story() MemoryAnalysis   -> StoryPlan       (chapters, prose, page plans + template choice)
  revise()     book + request   -> RevisionPlan    (targeted operations on the document)

LLMProvider calls any model through LiteLLM (Claude, GPT, Gemini, Mistral, local Ollama, ...);
LocalProvider is a deterministic offline fallback used when no model is configured or a call fails.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from typing import Callable, Literal, Protocol, Union

import mlflow
from pydantic import Field, ValidationError

# Use LiteLLM's bundled model/price map instead of fetching it from the network on import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
import litellm  # noqa: E402  (must follow the environment default above)
import openai  # noqa: E402  (LiteLLM's exceptions subclass the OpenAI SDK's)

from . import design, tracing
from .layout import TEMPLATES, PageContent, TemplateId
from .model import Model, TextRole
from .storage import Asset, Store

log = logging.getLogger(__name__)
litellm.suppress_debug_info = True
DEFAULT_MODEL = "gemini/gemini-3.8-flash"


class AIError(Exception):
    pass


# --- inputs ----------------------------------------------------------------------

class MemoryNote(Model):
    text: str = ""
    date: str = ""
    location: str = ""


class GenerationInput(Model):
    title: str = ""
    author: str = ""
    story: str = ""  # free-form "tell us about this memory"
    notes: list[MemoryNote] = Field(default_factory=list)
    date: str = ""
    location: str = ""
    people: str = ""
    mood: str = ""
    themeId: str = "auto"
    pageSize: Literal["A5", "A4", "Square"] = "A5"
    orientation: Literal["portrait", "landscape"] = "portrait"
    targetPages: int | None = Field(default=None, ge=1, le=200)
    photoIds: list[str] = Field(default_factory=list)


# --- stage outputs -----------------------------------------------------------------

class PhotoInsight(Model):
    id: str
    description: str = ""  # what is in the photo, for later revisions
    caption: str = ""
    importance: int = 3  # 1..5
    eventKey: str = ""


class MemoryEvent(Model):
    key: str
    title: str
    date: str = ""
    location: str = ""
    summary: str = ""
    mood: str = ""


class MemoryAnalysis(Model):
    suggestedTitle: str = ""
    tone: str = ""
    suggestedTheme: str = ""
    people: list[str] = Field(default_factory=list)
    events: list[MemoryEvent] = Field(default_factory=list)
    photos: list[PhotoInsight] = Field(default_factory=list)


class ChapterPlan(Model):
    title: str
    summary: str = ""
    pages: list[PageContent] = Field(default_factory=list)


class StoryPlan(Model):
    title: str
    subtitle: str = ""
    themeId: str = "editorial"
    coverPhotoId: str = ""
    chapters: list[ChapterPlan] = Field(default_factory=list)
    closing: PageContent | None = None


# --- revision operations --------------------------------------------------------------

class StylePatch(Model):
    fontFamily: str | None = None
    fontSizeScale: float | None = None  # multiply current size
    color: str | None = None
    align: Literal["left", "center", "right", "justify"] | None = None
    bold: bool | None = None
    italic: bool | None = None


class OpSetText(Model):
    op: Literal["set_text"]
    elementId: str
    text: str


class OpSetBook(Model):
    op: Literal["set_book"]
    title: str | None = None
    subtitle: str | None = None
    author: str | None = None


class OpSetTheme(Model):
    op: Literal["set_theme"]
    themeId: str


class OpApplyTemplate(Model):
    op: Literal["apply_template"]
    pageId: str
    template: TemplateId


class OpReplacePage(Model):
    op: Literal["replace_page"]
    pageId: str
    content: PageContent


class OpInsertPage(Model):
    op: Literal["insert_page"]
    afterPageId: str | None = None  # None = at the end
    content: PageContent


class OpDeletePage(Model):
    op: Literal["delete_page"]
    pageId: str


class OpMovePage(Model):
    op: Literal["move_page"]
    pageId: str
    toIndex: int


class OpMovePhoto(Model):
    op: Literal["move_photo"]
    assetId: str
    fromPageId: str
    toPageId: str


class OpSetBackground(Model):
    op: Literal["set_background"]
    pageId: str
    color: str


class OpAddChapter(Model):
    op: Literal["add_chapter"]
    title: str
    summary: str = ""
    pageIds: list[str]


class OpSetStyle(Model):
    op: Literal["set_style"]
    elementId: str | None = None
    role: TextRole | None = None  # apply to every element of this role when no elementId
    style: StylePatch


RevisionOp = Union[OpSetText, OpSetBook, OpSetTheme, OpApplyTemplate, OpReplacePage, OpInsertPage, OpDeletePage,
                   OpMovePage, OpMovePhoto, OpSetBackground, OpAddChapter, OpSetStyle]


class RevisionPlan(Model):
    summary: str  # one or two sentences for the user describing the change
    operations: list[RevisionOp] = Field(default_factory=list)


# --- provider interface ---------------------------------------------------------------

# Live output while a revision is planned: emit("text", delta) for the reply, emit("reasoning", delta) for
# thinking, emit("reset", "") when a failed attempt's partial reply must be abandoned.
Emit = Callable[[str, str], None]


class AIProvider(Protocol):
    name: str

    def analyze(self, inp: GenerationInput, photos: list[Asset], store: Store) -> MemoryAnalysis: ...
    def plan_story(self, inp: GenerationInput, analysis: MemoryAnalysis, photos: list[Asset]) -> StoryPlan: ...
    def revise(self, book_summary: dict, instruction: str, scope: dict, history: list[dict] = ...,
               emit: Emit | None = None) -> RevisionPlan: ...


class SummaryStream:
    """Decode the "summary" string of a RevisionPlan while its JSON is still streaming.

    Structured output follows the schema's property order, and `summary` is the first property, so the
    user's reply can be shown token by token before the operations arrive.
    """

    def __init__(self):
        self.buf, self.start, self.sent, self.done = "", None, 0, False

    def feed(self, chunk: str) -> str:
        self.buf += chunk
        if self.done:
            return ""
        if self.start is None:
            m = re.search(r'"summary"\s*:\s*"', self.buf)
            if not m:
                return ""
            self.start = m.end()
        raw, i, end = self.buf[self.start:], 0, None
        while i < len(raw):
            if raw[i] == "\\":
                step = 6 if raw[i + 1:i + 2] == "u" else 2
                if i + step > len(raw):
                    break  # escape not complete yet
                i += step
            elif raw[i] == '"':
                end = i
                break
            else:
                i += 1
        text = json.loads(f'"{raw[:i]}"')
        if end is None and text and "\ud800" <= text[-1] <= "\udbff":
            text = text[:-1]  # wait for the low half of a surrogate pair
        self.done = end is not None
        new, self.sent = text[self.sent:], len(text)
        return new


def template_guide() -> str:
    return "\n".join(f"- {k}: {v['name']} ({v['photos']} photo{'s' if v['photos'] != 1 else ''})"
                     for k, v in TEMPLATES.items())


def theme_guide() -> str:
    return "\n".join(f"- {k}: {v['name']} — {v['description']}" for k, v in design.themes().items())


SYSTEM = """You are the author and art director of a premium personal memory book.
You turn people's photos and rough notes into a warm, well-paced printed book.
Write like a thoughtful editor: specific, sensory, concise; never generic filler,
never invent facts (names, places, events) that are not supported by the input or photos.
Write in the same language as the user's notes. Output only the requested structure."""


def _usage_fields(response) -> dict | None:
    """LiteLLM's OpenAI-style usage as plain counts. prompt_tokens already includes cached input."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = getattr(usage, "cache_read_input_tokens", None) or getattr(details, "cached_tokens", None) or 0
    return {"input_tokens": usage.prompt_tokens or 0, "output_tokens": usage.completion_tokens or 0,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None) or 0,
            # streamed responses carry the cost on usage, others in LiteLLM's hidden params
            "cost_usd": getattr(usage, "cost", None) or (getattr(response, "_hidden_params", None) or {}).get("response_cost")}


THEME_FIELDS = {"themeId", "suggestedTheme"}


def response_format(schema: type[Model]) -> dict:
    """The JSON schema sent to the model: every property required, no extra keys, theme ids as an enum.

    Providers differ: some (e.g. Gemini) honour `required` literally and skip any field that has a default,
    which would leave an analysis or plan silently empty. Defaults stay in the Pydantic models for validation.
    """
    js = schema.model_json_schema()
    themes = sorted(design.themes())

    def strict(node):
        if isinstance(node, list):
            for item in node:
                strict(item)
            return
        if not isinstance(node, dict):
            return
        node.pop("default", None)
        if node.get("type") == "object" and isinstance(node.get("properties"), dict):
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False
            for name, prop in node["properties"].items():
                if name in THEME_FIELDS and prop.get("type") == "string":
                    prop["enum"] = themes
        for value in node.values():  # properties, items, anyOf and $defs alike
            strict(value)
    strict(js)
    return {"type": "json_schema", "json_schema": {"name": schema.__name__, "schema": js, "strict": True}}


class LLMProvider:
    """Any chat model LiteLLM supports, named like `anthropic/claude-opus-5-5`, `openai/gpt-5` or `ollama/llama3`.

    Output is constrained to the stage's Pydantic schema (`response_format`) and validated again here.
    """

    name = "litellm"

    def __init__(self, model: str | None = None, fallbacks: list[str] | None = None, **completion_kwargs):
        self.model = model or os.environ.get("MEMORY_BOOK_MODEL", DEFAULT_MODEL)
        env_fallbacks = [m.strip() for m in os.environ.get("MEMORY_BOOK_FALLBACK_MODELS", "").split(",") if m.strip()]
        self.fallbacks = fallbacks if fallbacks is not None else env_fallbacks
        self.completion_kwargs = completion_kwargs  # e.g. mock_response in tests, api_base for self-hosted models
        self.completion = litellm.completion

    def _request(self, content: list[dict] | str, schema: type[Model], stream: bool) -> dict:
        request = dict(model=self.model, max_tokens=20000, response_format=response_format(schema), drop_params=True,
                       messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
                       **self.completion_kwargs)
        if self.fallbacks:
            request["fallbacks"] = self.fallbacks
        if effort := os.environ.get("MEMORY_BOOK_EFFORT"):
            request["reasoning_effort"] = effort
        # Gemini streams thought summaries (shown as reasoning in the chat) only with an explicit thinking budget.
        budget = os.environ.get("MEMORY_BOOK_THINKING_BUDGET") or ("1024" if self.model.startswith("gemini/") else "")
        if budget and budget != "0":
            request["thinking"] = {"type": "enabled", "budget_tokens": int(budget)}
        if (workspace := os.environ.get("ANTHROPIC_WORKSPACE_ID")) and self.model.startswith("anthropic/"):
            request["extra_headers"] = {"anthropic-workspace-id": workspace}  # for keys not scoped to a workspace
        if stream:
            request.update(stream=True, stream_options={"include_usage": True})
        return request

    def _call[T: Model](self, content: list[dict] | str, schema: type[T], emit: Emit | None = None) -> T:
        """One structured call. With `emit`, the call streams: reasoning and the reply arrive live."""
        prompt = content if isinstance(content, str) else [
            b.get("text", "") if b["type"] == "text" else f"[{b['type']}]" for b in content]  # never log image data
        with mlflow.start_span(name=f"llm.{schema.__name__}", span_type=tracing.SpanType.CHAT_MODEL) as span:
            span.set_inputs({"model": self.model, "fallbacks": self.fallbacks, "system": SYSTEM,
                             "prompt": tracing.content(prompt)})
            try:
                if emit is None:
                    response = self.completion(**self._request(content, schema, stream=False))
                else:
                    response = self._stream(self._request(content, schema, stream=True), emit)
            except litellm.ContentPolicyViolationError as e:
                raise AIError("the AI declined this request") from e
            except openai.APIConnectionError as e:  # includes timeouts
                raise AIError(f"could not reach the AI service: {e}") from e
            except openai.APIStatusError as e:
                log.warning("LLM API error %s from %s: %s", e.status_code, self.model, e.message)  # never the key
                raise AIError(f"AI service error {e.status_code}") from e
            except openai.APIError as e:
                log.warning("LLM error from %s: %s", self.model, e)
                raise AIError("AI service error") from e
            choice = response.choices[0]
            served_by = str(getattr(response, "model", None) or self.model)
            provider = (getattr(response, "_hidden_params", None) or {}).get("custom_llm_provider") \
                or self.model.split("/", 1)[0]
            tracing.set_usage(span, _usage_fields(response), served_by, provider)
            span.set_attributes({"served_by": served_by, "finish_reason": str(choice.finish_reason)})
            if choice.finish_reason == "content_filter":
                raise AIError("the AI declined this request")
            if choice.finish_reason == "length":
                raise AIError("incomplete AI output")
            message = choice.message
            raw = message.content or (message.tool_calls[0].function.arguments if message.tool_calls else "")
            try:
                result = schema.model_validate_json(raw or "")  # validate: never trust model output blindly
            except ValidationError as e:
                raise AIError(f"malformed AI output: {e.error_count()} validation errors") from e
            span.set_outputs(tracing.content(result.model_dump(mode="json")))
            return result

    def _stream(self, request: dict, emit: Emit):
        summary = SummaryStream()
        chunks = []
        for chunk in self.completion(**request):
            chunks.append(chunk)
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if text := getattr(delta, "reasoning_content", None):
                emit("reasoning", text)
            # JSON arrives as content, or as tool-call arguments on providers that implement JSON mode with a tool
            piece = delta.content or "".join(tc.function.arguments or "" for tc in (delta.tool_calls or []))
            if piece and (out := summary.feed(piece)):
                emit("text", out)
        return litellm.stream_chunk_builder(chunks, messages=request["messages"])

    def analyze(self, inp, photos, store):
        content: list[dict] = []
        for i, a in enumerate(photos):
            meta = f"Photo id={a.id} file={a.filename!r} {a.width}x{a.height}" + (f" taken={a.taken_at}" if a.taken_at else "")
            # ponytail: first 60 photos are shown to the model, the rest by metadata only; batch if books get huge
            if i < 60 and (thumb := store.asset_bytes(a.id, "thumb")):
                data = base64.b64encode(thumb[0]).decode()
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}})
            content.append({"type": "text", "text": meta})
        content.append({"type": "text", "text": f"""User input:
{inp.model_dump_json(exclude={"photoIds", "pageSize", "orientation"}, indent=1)}

Understand these memories. For every photo give: a factual one-sentence description, a short evocative caption
(max 12 words; empty if nothing meaningful to say), importance 1-5 (5 = cover-worthy, sharp, emotional), and the
eventKey of the event it belongs to. Identify the distinct events/moments (chronological), people named by the
user, the overall tone, a suggested book title, and the best fitting theme id from:
{theme_guide()}"""})
        analysis = self._call(content, MemoryAnalysis)
        if not analysis.events or (photos and not analysis.photos):  # schema-valid but empty: not usable
            raise AIError("the AI returned an empty analysis")
        return analysis

    def plan_story(self, inp, analysis, photos):
        target = f"about {inp.targetPages} pages" if inp.targetPages else "a length that suits the material (typically 8-24 pages)"
        prompt = f"""Plan the memory book.

Analysis of the user's memories:
{analysis.model_dump_json(indent=1)}

User input:
{inp.model_dump_json(exclude={"photoIds"}, indent=1)}

Templates (choose per page; photo counts must match photoIds):
{template_guide()}

Themes:
{theme_guide()}

Rules:
- Book length: {target}. Organize chapters chronologically; one chapter per meaningful event (1-6 chapters).
- Start each chapter with a 'chapter-opener' (or 'chapter-photo' with one strong photo) page: set chapterLabel
  (e.g. "Chapter One" in the user's language), heading = chapter title, body = 1-2 sentence intro.
- Use each photo at most once; favour important photos in large templates (full-photo, photo-text, chapter-photo).
- Turn rough notes into polished, personal prose. Body text: 40-140 words per page. Captions: under 12 words.
- Vary the rhythm: alternate big single photos, collages and text/quote pages. Use 'quote' for a memorable line,
  'travel' or 'map' for places, 'timeline' (items: label=date/time, text=what happened) for itineraries.
- themeId: honour the user's choice "{inp.themeId}" unless it is "auto".
- coverPhotoId: the single best photo (or "" if none). closing: a short, warm closing page (template 'closing').
- Never invent names, places or events the input does not support."""
        plan = self._call(prompt, StoryPlan)
        if not any(ch.pages for ch in plan.chapters):
            raise AIError("the AI returned an empty story plan")
        return plan

    def revise(self, book_summary, instruction, scope, history=(), emit=None):
        talk = "\n".join(f"{m['role']}: {m['content']}" for m in history) or "(none)"
        prompt = f"""Here is the current memory book (structured document; ids are stable):
{json.dumps(book_summary, ensure_ascii=False)}

Earlier in this conversation (resolve references like "it" or "that page" against it):
{talk}

The user is currently looking at: {json.dumps(scope)}
User request: "{instruction}"

Return the smallest set of operations that fulfils the request. Prefer targeted edits (set_text, set_style,
apply_template, replace_page) over rewriting the whole book; only touch pages outside the user's focus when the
request is clearly about the whole book. replace_page/insert_page take full page content (template + text +
photoIds) and are re-laid out automatically. Available templates:
{template_guide()}
Themes:
{theme_guide()}
In summary, tell the user in one friendly sentence what you are changing."""
        return self._call(prompt, RevisionPlan, emit)


# --- offline provider -----------------------------------------------------------------

def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text.strip()) if s.strip()]


def _short_title(text: str, fallback: str) -> str:
    """A short first sentence makes a good title; otherwise use the place or a neutral label."""
    first = _sentences(text)[0] if text.strip() else ""
    words = re.sub(r"[.!?…,;:]+$", "", first).split()
    return " ".join(words) if 0 < len(words) <= 6 and "," not in first else fallback


def _fmt_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).strftime("%-d %B %Y")
    except ValueError:
        return value


NUMBER_WORDS = ["One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]


class LocalProvider:
    """Deterministic designer used offline. Organizes and lays out; keeps the user's own words."""

    name = "local"

    def analyze(self, inp, photos, store):
        notes = [n for n in inp.notes if n.text.strip()]
        if inp.story.strip():
            notes = [MemoryNote(text=inp.story, date=inp.date, location=inp.location), *notes]
        events = [MemoryEvent(key=f"e{i}", title=_short_title(n.text, n.location or f"Moment {i + 1}"),
                              date=n.date, location=n.location, summary=n.text.strip()) for i, n in enumerate(notes)]
        if not events:
            events = [MemoryEvent(key="e0", title=inp.location or "Our moments", date=inp.date, location=inp.location)]
        ordered = sorted(photos, key=lambda a: (a.taken_at or "", photos.index(a)))
        insights = []
        for i, a in enumerate(ordered):  # spread photos across events in order
            ev = events[min(i * len(events) // max(len(ordered), 1), len(events) - 1)]
            insights.append(PhotoInsight(id=a.id, importance=4 if a.aspect >= 1 else 3, eventKey=ev.key,
                                         caption=_fmt_date(a.taken_at[:10]) if a.taken_at else ""))
        theme = inp.themeId if inp.themeId in design.themes() else _theme_from_mood(inp.mood + " " + inp.story)
        title = inp.title or (f"{inp.location}" if inp.location else "Our Memories")
        return MemoryAnalysis(suggestedTitle=title, tone=inp.mood, suggestedTheme=theme, events=events, photos=insights)

    def plan_story(self, inp, analysis, photos):
        aspects = {a.id: a.aspect for a in photos}
        by_event: dict[str, list[str]] = {}
        for p in analysis.photos:
            by_event.setdefault(p.eventKey, []).append(p.id)
        captions = {p.id: p.caption for p in analysis.photos}
        cover = max(analysis.photos, key=lambda p: (p.importance, aspects.get(p.id, 1) < 1.1), default=None)
        chapters = []
        multi = len(analysis.events) > 1
        for ci, ev in enumerate(analysis.events):
            ids = [i for i in by_event.get(ev.key, []) if not cover or i != cover.id or len(analysis.photos) == 1]
            pages: list[PageContent] = []
            if multi:
                pages.append(PageContent(template="chapter-opener", chapterLabel=f"Chapter {NUMBER_WORDS[ci % 10]}",
                                         heading=ev.title, body=" · ".join(v for v in (ev.date and _fmt_date(ev.date), ev.location) if v)))
            body, quote, attribution = ev.summary.strip(), "", ""
            if m := re.search(r'["“]([^"”]{3,200}?)[,.!?]?["”][,.]?\s*([^.!?"“]{0,60})[.!?]?', body):
                quote, attribution = m.group(1).strip(), m.group(2).strip()
                body = re.sub(r" {2,}", " ", body[:m.start()] + body[m.end():]).strip()
            if not multi and body.startswith(ev.title):  # the title came from the first sentence
                body = body[len(ev.title):].lstrip(" .!?…")
            if ids or body:
                head = ids[:1]
                ids = ids[1:]
                tpl = "travel" if ev.location and head else "photo-text"
                pages.append(PageContent(template=tpl, heading="" if multi else ev.title, body=body, photoIds=head,
                                         location=ev.location, date=_fmt_date(ev.date) if ev.date else "",
                                         caption=captions.get(head[0], "") if head else ""))
            if quote:
                pages.append(PageContent(template="quote", quote=quote, attribution=attribution))
            pattern = [1, 2, 3, 1, 4, 2]
            k = 0
            while ids:
                n = min(pattern[k % len(pattern)], len(ids))
                chunk, ids = ids[:n], ids[n:]
                tpl = {1: "full-photo", 2: "two-photos", 3: "three-collage", 4: "four-grid"}[n]
                cap = " · ".join(c for c in (captions.get(i, "") for i in chunk) if c)
                pages.append(PageContent(template=tpl, photoIds=chunk, caption=cap if n > 1 else ""))
                k += 1
            chapters.append(ChapterPlan(title=ev.title, summary=ev.summary[:200], pages=pages))
        closing = PageContent(template="closing", heading="Until next time",
                              body=f"{inp.people.strip()}, always." if inp.people.strip() else "")
        subtitle = _subtitle(inp).replace(analysis.suggestedTitle, "").strip(" ·")
        return StoryPlan(title=analysis.suggestedTitle, subtitle=subtitle, themeId=analysis.suggestedTheme,
                         coverPhotoId=cover.id if cover else "", chapters=chapters, closing=closing)

    def revise(self, book_summary, instruction, scope, history=(), emit=None):
        plan = local_revision(book_summary, instruction, scope)
        if emit:
            ops = sorted({op.op for op in plan.operations})
            emit("reasoning", "No AI model is configured, so I matched the request against the offline editing rules"
                 + (f" and planned: {', '.join(ops)}." if ops else ", and no rule fits it."))
            for word in plan.summary.split(" "):
                emit("text", word + " ")
        return plan


def _subtitle(inp: GenerationInput) -> str:
    return " · ".join(v for v in (inp.location, _fmt_date(inp.date) if inp.date else "") if v)


THEME_WORDS = {
    "vintage": ["vintage", "retro", "old", "nostalgic", "classic", "sepia"],
    "minimal": ["minimal", "clean", "simple", "modern"],
    "romantic": ["romantic", "love", "wedding", "anniversary", "soft", "honeymoon"],
    "scrapbook": ["scrapbook", "playful", "fun", "kids", "family fun", "polaroid"],
    "travel": ["travel", "trip", "journey", "journal", "adventure", "holiday", "vacation", "road"],
    "editorial": ["editorial", "elegant", "magazine", "sophisticated", "premium"],
}


def _theme_from_mood(text: str) -> str:
    text = text.lower()
    for theme_id, words in THEME_WORDS.items():
        if any(re.search(rf"\b{re.escape(w)}", text) for w in words):
            return theme_id
    return "editorial"


def local_revision(book: dict, instruction: str, scope: dict) -> RevisionPlan:
    """Keyword-driven revisions for offline use. Handles the common structural requests."""
    text = instruction.lower()
    ops: list = []
    pages = book["pages"]
    page_id = scope.get("pageId")
    focus = [p for p in pages if p["id"] == page_id] if page_id and re.search(r"\b(this|page)\b", text) else pages

    if m := re.search(r"(\d+)[- ]page", text):
        target = int(m.group(1))
        removable = [p for p in pages[1:] if p.get("template") in ("quote", "minimal-text", "timeline", "map")] + \
                    [p for p in reversed(pages[1:-1])]
        seen: set[str] = set()
        for p in removable:
            if len(pages) - len(seen) <= target:
                break
            if p["id"] not in seen:
                seen.add(p["id"])
                ops.append(OpDeletePage(op="delete_page", pageId=p["id"]))
        if ops:
            return RevisionPlan(summary=f"Trimmed the book to {len(pages) - len(seen)} pages.", operations=ops)
    for theme_id, words in THEME_WORDS.items():
        if any(w in text for w in words) and any(k in text for k in ("theme", "feel", "style", "look", "more", "make")) \
                and not ("minimal" in text and page_id and "page" in text):
            ops.append(OpSetTheme(op="set_theme", themeId=theme_id))
            return RevisionPlan(summary=f"Restyled the whole book with the {design.theme(theme_id)['name']} theme.", operations=ops)
    if any(k in text for k in ("fewer words", "shorter", "less text", "concise", "shorten")):
        for p in focus:
            for e in p["elements"]:
                if e.get("role") == "body" and len(s := _sentences(e.get("text", ""))) > 1:
                    ops.append(OpSetText(op="set_text", elementId=e["id"], text=" ".join(s[: max(1, len(s) // 2)])))
        return RevisionPlan(summary="Tightened the text to its strongest sentences." if ops else
                            "The text is already short.", operations=ops)
    if "minimal" in text:
        for p in focus:
            photos = [e["assetId"] for e in p["elements"] if e["type"] == "image" and e.get("assetId")]
            if p.get("kind") == "cover":
                continue
            ops.append(OpApplyTemplate(op="apply_template", pageId=p["id"],
                                       template="full-photo" if photos else "minimal-text"))
        return RevisionPlan(summary="Simplified the layout to let the content breathe.", operations=ops)
    if re.search(r"(bigger|larger) (text|font|type)", text) or re.search(r"(smaller) (text|font|type)", text):
        factor = 0.9 if "smaller" in text else 1.12
        for role in ("body", "caption", "heading"):
            ops.append(OpSetStyle(op="set_style", role=role, style=StylePatch(fontSizeScale=factor)))
        return RevisionPlan(summary="Adjusted the text size across the book.", operations=ops)
    if "photo" in text and any(k in text for k in ("more", "add")):
        used = {e.get("assetId") for p in pages for e in p["elements"] if e["type"] == "image"}
        unused = [a for a in book.get("library", []) if a not in used]
        for i in range(0, len(unused), 4):
            chunk = unused[i:i + 4]
            ops.append(OpInsertPage(op="insert_page", afterPageId=pages[-2]["id"] if len(pages) > 1 else None,
                                    content=PageContent(template="four-grid", photoIds=chunk)))
        return RevisionPlan(summary=f"Added {len(unused)} more photos." if unused else
                            "Every photo you uploaded is already in the book.", operations=ops)
    return RevisionPlan(summary="I can only make structural changes (theme, length, layout, text size) without the "
                                "AI service. Configure a model (MEMORY_BOOK_MODEL and its API key) for writing changes like this one.",
                        operations=[])


def default_provider() -> AIProvider:
    """The configured model if its provider's credentials are present (none needed for e.g. Ollama), else offline."""
    model = os.environ.get("MEMORY_BOOK_MODEL", DEFAULT_MODEL)
    try:
        litellm.get_llm_provider(model)  # unknown provider prefixes raise here (validate_environment alone accepts them)
        check = litellm.validate_environment(model)
    except Exception as e:  # unknown model name or provider: keep the app usable
        log.warning("Model %s unavailable: %s", model, e)
        return LocalProvider()
    if check.get("keys_in_environment"):
        return LLMProvider(model)
    log.info("Model %s needs %s; using the offline designer", model, ", ".join(check.get("missing_keys", [])))
    return LocalProvider()

