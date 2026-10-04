"""Generation and revision pipeline: raw input -> AI stages -> validated MemoryBook.

    ingest -> analyze (understand) -> organize -> plan_story -> sanitize -> compose (layout) -> MemoryBook
"""

from __future__ import annotations

import copy
import logging
from typing import Callable

import mlflow

from . import design, tracing
from .ai import (
    AIError,
    AIProvider,
    Emit,
    GenerationInput,
    LocalProvider,
    MemoryAnalysis,
    OpAddChapter,
    OpApplyTemplate,
    OpDeletePage,
    OpInsertPage,
    OpMovePage,
    OpMovePhoto,
    OpReplacePage,
    OpSetBackground,
    OpSetBook,
    OpSetStyle,
    OpSetText,
    OpSetTheme,
    PhotoInsight,
    RevisionPlan,
    StoryPlan,
)
from .layout import TEMPLATES, PageContent, apply_template, best_template, extract_content, layout_pages, materialize
from .model import BookMetadata, Chapter, MemoryBook, MemoryPage
from .storage import Asset, Store
from .tracing import SpanType

log = logging.getLogger(__name__)

STAGES = [
    ("understanding", "Understanding your memories"),
    ("organizing", "Organizing your photos"),
    ("story", "Building your story"),
    ("design", "Designing your pages"),
    ("finishing", "Finishing your book"),
]


def _with_fallback(provider: AIProvider, fn: str, *args):
    """Call a provider stage; retry once, then fall back to the offline designer."""
    if provider.name == "local":
        return getattr(provider, fn)(*args), ""
    for attempt in (1, 2):
        try:
            return getattr(provider, fn)(*args), ""
        except AIError as e:
            log.warning("AI %s attempt %d failed: %s", fn, attempt, e)
            reason = str(e)
    return getattr(LocalProvider(), fn)(*args), reason


def sanitize_analysis(a: MemoryAnalysis, photos: list[Asset]) -> MemoryAnalysis:
    ids = {p.id for p in photos}
    seen: set[str] = set()
    a.photos = [p for p in a.photos if p.id in ids and not (p.id in seen or seen.add(p.id))]
    keys = {e.key for e in a.events}
    for p in a.photos:
        if p.eventKey not in keys and a.events:
            p.eventKey = a.events[0].key
    for missing in ids - seen:  # the model skipped a photo: keep it, attach to the first event
        a.photos.append(PhotoInsight(id=missing, eventKey=a.events[0].key if a.events else ""))
    a.suggestedTheme = a.suggestedTheme if a.suggestedTheme in design.themes() else "editorial"
    return a


def sanitize_plan(plan: StoryPlan, photos: list[Asset], inp: GenerationInput) -> StoryPlan:
    """Enforce invariants the model may have broken: known ids, no duplicate photos, a theme that exists."""
    ids = {p.id for p in photos}
    used: set[str] = set()
    if inp.themeId in design.themes():
        plan.themeId = inp.themeId
    elif plan.themeId not in design.themes():
        plan.themeId = "editorial"
    if plan.coverPhotoId not in ids:
        plan.coverPhotoId = photos[0].id if photos else ""
    for ch in plan.chapters:
        for pg in ch.pages:
            pg.photoIds = [i for i in pg.photoIds if i in ids and not (i in used or used.add(i))]
            pg.items = pg.items[:5]
        ch.pages = [pg for pg in ch.pages if pg.photoIds or any(
            (pg.heading, pg.body, pg.quote, pg.items, pg.caption, pg.location))]
    plan.chapters = [ch for ch in plan.chapters if ch.pages]
    if plan.closing:
        plan.closing.photoIds = [i for i in plan.closing.photoIds if i in ids][:1]
        plan.closing.template = "closing"
    if not plan.chapters and photos:
        plan.chapters = [LocalProvider().plan_story(inp, LocalProvider().analyze(inp, photos, None), photos).chapters[0]]
    plan.title = (plan.title or inp.title or "Our Memories").strip()[:120]
    return plan


def compose_book(plan: StoryPlan, inp: GenerationInput, photos: list[Asset], generated_by: str) -> MemoryBook:
    aspects = {p.id: p.aspect for p in photos}
    focus = {p.id: p.focus for p in photos if p.focus}
    book = MemoryBook(title=plan.title, subtitle=plan.subtitle, author=inp.author, pageSize=inp.pageSize,
                      orientation=inp.orientation, themeId=plan.themeId,
                      metadata=BookMetadata(generatedBy=generated_by, mood=inp.mood, assetIds=[p.id for p in photos]))
    cover = PageContent(template="cover", title=book.title, subtitle=book.subtitle, author=book.author,
                        photoIds=[plan.coverPhotoId] if plan.coverPhotoId else [])
    cover.template = best_template(cover, aspects, is_cover=True)
    book.pages.append(materialize(book, cover, aspects, focus=focus)[0])
    for ch in plan.chapters:
        chapter = Chapter(title=ch.title, summary=ch.summary)
        book.chapters.append(chapter)
        book.pages += layout_pages(book, ch.pages, aspects, chapter.id, focus)
    if plan.closing and (plan.closing.heading or plan.closing.body):
        book.pages += layout_pages(book, [plan.closing], aspects, focus=focus)
    return MemoryBook.model_validate(book.model_dump())  # final schema check before it is accepted


def generate_book(store: Store, provider: AIProvider, inp: GenerationInput,
                  progress: Callable[[str], None] = lambda s: None) -> MemoryBook:
    assets = store.get_assets(inp.photoIds)
    photos = [assets[i] for i in dict.fromkeys(inp.photoIds) if i in assets]  # keep upload order, drop unknown ids
    if not photos and not (inp.story.strip() or any(n.text.strip() for n in inp.notes)):
        raise ValueError("Add at least one photo or a few words about the memory.")

    def stage(name: str):  # one span per user-facing stage, so traces read like the progress screen
        progress(name)
        return mlflow.start_span(name=name, span_type=SpanType.CHAIN)

    with mlflow.start_span(name="generate_book", span_type=SpanType.AGENT) as root:
        root.set_inputs({"photos": len(photos), "notes": len(inp.notes), "theme": inp.themeId, "pageSize": inp.pageSize,
                         "orientation": inp.orientation, "targetPages": inp.targetPages,
                         "story": tracing.content(inp.story), "mood": tracing.content(inp.mood)})
        root.set_attribute("provider", provider.name)
        with stage("understanding") as s:
            analysis, why1 = _with_fallback(provider, "analyze", inp, photos, store)
            s.set_outputs({"events": len(analysis.events), "photos": len(analysis.photos), "fallback": why1 or None})
        with stage("organizing") as s:
            analysis = sanitize_analysis(analysis, photos)
            for p in analysis.photos:
                if p.description:
                    store.set_asset_description(p.id, p.description[:500])
            s.set_outputs({"events": [tracing.content(e.title) for e in analysis.events], "theme": analysis.suggestedTheme})
        with stage("story") as s:
            plan, why2 = _with_fallback(provider, "plan_story", inp, analysis, photos)
            plan = sanitize_plan(plan, photos, inp)
            s.set_outputs({"chapters": len(plan.chapters), "plannedPages": sum(len(c.pages) for c in plan.chapters),
                           "theme": plan.themeId, "fallback": why2 or None})
        with stage("design") as s:
            reason = why1 or why2
            book = compose_book(plan, inp, photos, generated_by=f"{provider.name}" + (" (fallback: local)" if reason else ""))
            s.set_outputs({"pages": len(book.pages), "templates": sorted({p.template or "free" for p in book.pages})})
        if reason or provider.name == "local":
            book.metadata.notice = ("Our AI writer was unavailable, so this draft keeps your own words. "
                                    "You can refine it with the AI later.") if reason else \
                "Created with the offline designer: your own words, arranged into pages."
        progress("finishing")
        tracing.set_session(tracing.session_for_book(book.id), kind="generation", provider=provider.name,
                            fallback=str(bool(reason)))
        root.set_outputs({"bookId": book.id, "pages": len(book.pages), "chapters": len(book.chapters)})
    return book


# --- revisions ----------------------------------------------------------------------

def book_summary(book: MemoryBook, store: Store) -> dict:
    """Compact view of the document for the AI (no geometry, stable ids)."""
    assets = store.get_assets(book.metadata.assetIds or book.asset_ids())
    return {
        "title": book.title, "subtitle": book.subtitle, "author": book.author, "themeId": book.themeId,
        "pageSize": book.pageSize, "chapters": [c.model_dump() for c in book.chapters],
        "library": list(book.metadata.assetIds or book.asset_ids()),
        "photos": {k: {"description": a.description, "orientation": "portrait" if a.aspect < 0.95 else
                       "landscape" if a.aspect > 1.05 else "square"} for k, a in assets.items()},
        "pages": [{
            "id": p.id, "index": i, "kind": p.kind, "template": p.template, "chapterId": p.chapterId,
            "elements": [{"id": e.id, "type": e.type, **({"role": e.role, "text": e.text} if e.type == "text" else {}),
                          **({"assetId": e.assetId} if e.type == "image" else {})}
                         for e in p.elements if e.type != "shape"],
        } for i, p in enumerate(book.pages)],
    }


def apply_revision(book: MemoryBook, plan: RevisionPlan, store: Store) -> tuple[MemoryBook, list[str]]:
    """Apply validated operations to a copy of the book. Invalid ops are skipped, never half-applied."""
    book = copy.deepcopy(book)
    library = set(book.metadata.assetIds) | book.asset_ids()
    assets = store.get_assets(library)
    aspects = {k: a.aspect for k, a in assets.items()}
    focus = {k: a.focus for k, a in assets.items() if a.focus}
    skipped: list[str] = []

    def page_of(pid: str) -> MemoryPage | None:
        return next((p for p in book.pages if p.id == pid), None)

    def relayout(page: MemoryPage, content: PageContent) -> list[MemoryPage]:
        content.photoIds = [i for i in content.photoIds if i in library]
        content.template = best_template(content, aspects, is_cover=page.kind == "cover")
        pages = layout_pages(book, [content], aspects, page.chapterId, focus) if page.kind != "cover" else \
            [materialize(book, content, aspects, focus=focus)[0]]
        pages[0].id, pages[0].background = page.id, page.background
        return pages

    def replace(page: MemoryPage, new: list[MemoryPage]) -> None:
        i = book.pages.index(page)
        book.pages[i:i + 1] = new

    for op in plan.operations:
        try:
            if isinstance(op, OpSetText):
                el = next((e for p in book.pages for e in p.elements if e.id == op.elementId and e.type == "text"), None)
                if not el:
                    raise LookupError(op.elementId)
                el.text = op.text
            elif isinstance(op, OpSetBook):
                for k in ("title", "subtitle", "author"):
                    if (v := getattr(op, k)) is not None:
                        setattr(book, k, v)
                        for e in book.pages[0].elements if book.pages else []:
                            if e.type == "text" and e.slot == k:
                                e.text = v
            elif isinstance(op, OpSetTheme):
                if op.themeId not in design.themes():
                    raise LookupError(op.themeId)
                book.themeId = op.themeId
            elif isinstance(op, OpApplyTemplate):
                page = page_of(op.pageId)
                if not page or (page.kind == "cover") != bool(TEMPLATES[op.template].get("cover")):
                    raise LookupError(op.pageId)
                replace(page, apply_template(book, page, op.template, aspects, placeholders=False, focus=focus))
            elif isinstance(op, OpReplacePage):
                page = page_of(op.pageId)
                if not page:
                    raise LookupError(op.pageId)
                replace(page, relayout(page, op.content))
            elif isinstance(op, OpInsertPage):
                after = page_of(op.afterPageId) if op.afterPageId else None
                if op.afterPageId and not after:
                    raise LookupError(op.afterPageId)
                content = op.content
                content.photoIds = [i for i in content.photoIds if i in library]
                content.template = best_template(content, aspects)
                new = layout_pages(book, [content], aspects, after.chapterId if after else None, focus)
                at = book.pages.index(after) + 1 if after else len(book.pages)
                book.pages[at:at] = new
            elif isinstance(op, OpDeletePage):
                page = page_of(op.pageId)
                if not page or page.kind == "cover":
                    raise LookupError(op.pageId)
                book.pages.remove(page)
            elif isinstance(op, OpMovePage):
                page = page_of(op.pageId)
                if not page or page.kind == "cover":
                    raise LookupError(op.pageId)
                book.pages.remove(page)
                book.pages.insert(max(1, min(op.toIndex, len(book.pages))), page)
            elif isinstance(op, OpMovePhoto):
                src, dst = page_of(op.fromPageId), page_of(op.toPageId)
                if not src or not dst or op.assetId not in extract_content(src).photoIds:
                    raise LookupError(op.assetId)
                c_src, c_dst = extract_content(src), extract_content(dst)
                c_src.photoIds.remove(op.assetId)
                c_dst.photoIds.append(op.assetId)
                replace(dst, relayout(dst, c_dst))
                replace(src, relayout(src, c_src))
            elif isinstance(op, OpSetBackground):
                page = page_of(op.pageId)
                if not page:
                    raise LookupError(op.pageId)
                page.background.color = op.color
            elif isinstance(op, OpAddChapter):
                targets = [p for p in book.pages if p.id in op.pageIds and p.kind != "cover"]
                if not targets:
                    raise LookupError("pages")
                chapter = Chapter(title=op.title, summary=op.summary)
                book.chapters.append(chapter)
                for p in targets:
                    p.chapterId = chapter.id
                opener = materialize(book, PageContent(template="chapter-opener", heading=op.title, body=op.summary,
                                                       chapterLabel="Chapter"), aspects, focus=focus)[0]
                opener.chapterId = chapter.id
                book.pages.insert(book.pages.index(targets[0]), opener)
            elif isinstance(op, OpSetStyle):
                th = design.theme(book.themeId)
                scale = design.size_scale(*book.page_dims())
                for e in (e for p in book.pages for e in p.elements if e.type == "text"):
                    if (op.elementId and e.id != op.elementId) or (not op.elementId and op.role and e.role != op.role):
                        continue
                    s = op.style
                    if s.fontSizeScale:
                        current = e.style.fontSize or design.resolve_text(th, e.role, e.style, scale).font_size
                        e.style.fontSize = round(min(max(current * s.fontSizeScale, 4), 120), 2)
                    for k in ("fontFamily", "color", "align", "bold", "italic"):
                        if (v := getattr(s, k)) is not None:
                            setattr(e.style, k, v)
        except LookupError as e:
            skipped.append(f"{op.op}: unknown {e}")
    if not book.pages or book.pages[0].kind != "cover":
        skipped.append("cover must stay first")
    return MemoryBook.model_validate(book.model_dump()), skipped


def plan_revision(book: MemoryBook, instruction: str, scope: dict, provider: AIProvider, store: Store,
                  history: list[dict] = (), emit: Emit | None = None) -> RevisionPlan:  # type: ignore[assignment]
    """Ask the AI for targeted edits; if it fails, fall back to the offline rules when they apply."""
    summary = book_summary(book, store)
    try:
        return provider.revise(summary, instruction, scope, list(history), emit)
    except AIError as e:
        log.warning("AI revision failed (%s); trying offline rules", e)
        if emit:
            emit("reset", "")  # drop any half-streamed reply from the failed attempt
        plan = LocalProvider().revise(summary, instruction, scope, emit=emit)
        if not plan.operations:
            raise
        return plan
