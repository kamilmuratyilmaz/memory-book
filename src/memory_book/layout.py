"""Data-driven page templates and the layout engine.

A template is a list of slots in fractions of the page's content box (or of the
full page for bleed slots). `materialize` fills slots from a PageContent and
produces ordinary, editable page elements — templates are never baked images.
"""

from __future__ import annotations

import itertools
import math
from typing import Literal

from pydantic import Field

from . import design
from .model import (
    Box,
    ImageCrop,
    ImageElement,
    MemoryBook,
    MemoryPage,
    Model,
    ShapeElement,
    TextElement,
    TextStyle,
)

TemplateId = Literal[
    "cover", "cover-full", "cover-text", "full-photo", "photo-text", "two-photos", "two-portraits",
    "three-collage", "four-grid", "quote", "timeline", "travel", "chapter-opener", "chapter-photo",
    "minimal-text", "closing", "map",
]


class TimelineItem(Model):
    label: str = ""
    text: str = ""


class PageContent(Model):
    """Everything a template can show. Also the per-page shape the AI plans in."""

    template: TemplateId = "photo-text"
    title: str = ""
    subtitle: str = ""
    author: str = ""
    chapterLabel: str = ""
    heading: str = ""
    body: str = ""
    quote: str = ""
    attribution: str = ""
    caption: str = ""
    date: str = ""
    location: str = ""
    photoIds: list[str] = Field(default_factory=list)
    items: list[TimelineItem] = Field(default_factory=list)


# --- template data -----------------------------------------------------------

def img(i, x, y, w, h, area="content"):
    return {"t": "image", "index": i, "x": x, "y": y, "w": w, "h": h, "area": area}


def txt(field, x, y, w, h, role=None, align=None, valign="top"):
    return {"t": "text", "field": field, "role": role, "x": x, "y": y, "w": w, "h": h, "align": align, "valign": valign}


def shp(kind, x, y, w, h, color=None, opacity=1.0, area="content"):
    return {"t": "shape", "kind": kind, "x": x, "y": y, "w": w, "h": h, "color": color, "opacity": opacity, "area": area}


FIELD_ROLES = {
    "title": "title", "subtitle": "subtitle", "author": "meta", "chapterLabel": "meta", "heading": "heading",
    "body": "body", "quote": "quote", "attribution": "meta", "caption": "caption", "date": "meta",
    "location": "meta", "dateLocation": "meta",
}

TEMPLATES: dict[str, dict] = {
    "cover": {
        "name": "Cover", "photos": 1, "cover": True, "showNumber": False,
        "slots": [img(0, 0, 0, 1, 0.72), txt("title", 0, 0.755, 1, 0.12), txt("subtitle", 0, 0.88, 1, 0.065),
                  txt("author", 0, 0.955, 1, 0.045)],
        "landscape": [img(0, 0, 0, 0.6, 1), txt("title", 0.65, 0.25, 0.35, 0.35, valign="bottom"),
                      txt("subtitle", 0.65, 0.62, 0.35, 0.18), txt("author", 0.65, 0.94, 0.35, 0.06)],
    },
    "cover-full": {
        "name": "Cover, full photo", "photos": 1, "cover": True, "showNumber": False,
        "slots": [img(0, 0, 0, 1, 1, "page"), shp("rect", 0, 0.68, 1, 0.32, "paper", 0.9, "page"),
                  txt("title", 0, 0.775, 1, 0.11), txt("subtitle", 0, 0.885, 1, 0.06), txt("author", 0, 0.955, 1, 0.045)],
    },
    "cover-text": {
        "name": "Cover, typographic", "photos": 0, "cover": True, "showNumber": False,
        "slots": [shp("ornament", 0.4, 0.355, 0.2, 0.012), txt("title", 0, 0.39, 1, 0.18, align="center"),
                  txt("subtitle", 0.1, 0.58, 0.8, 0.08, align="center"), txt("author", 0, 0.95, 1, 0.05, align="center")],
    },
    "full-photo": {
        "name": "Full-page photograph", "photos": 1, "showNumber": False,
        "slots": [img(0, 0, 0, 1, 1, "page")],
    },
    "photo-text": {
        "name": "Photo and story", "photos": 1,
        "slots": [img(0, 0, 0, 1, 0.6), txt("heading", 0, 0.645, 1, 0.08), txt("body", 0, 0.735, 1, 0.265)],
        "landscape": [img(0, 0, 0, 0.56, 1), txt("heading", 0.61, 0.06, 0.39, 0.16, valign="bottom"),
                      txt("body", 0.61, 0.25, 0.39, 0.75)],
    },
    "two-photos": {
        "name": "Two photos", "photos": 2,
        "slots": [img(0, 0, 0, 1, 0.455), img(1, 0, 0.485, 1, 0.43), txt("caption", 0, 0.935, 1, 0.065)],
        "landscape": [img(0, 0, 0, 0.485, 0.86), img(1, 0.515, 0, 0.485, 0.86), txt("caption", 0, 0.9, 1, 0.1)],
    },
    "two-portraits": {
        "name": "Two photos, side by side", "photos": 2,
        "slots": [img(0, 0, 0.04, 0.485, 0.62), img(1, 0.515, 0.2, 0.485, 0.62), txt("caption", 0, 0.86, 1, 0.14)],
    },
    "three-collage": {
        "name": "Three-photo collage", "photos": 3,
        "slots": [img(0, 0, 0, 1, 0.54), img(1, 0, 0.57, 0.485, 0.35), img(2, 0.515, 0.57, 0.485, 0.35),
                  txt("caption", 0, 0.935, 1, 0.065)],
        "landscape": [img(0, 0, 0, 0.6, 0.88), img(1, 0.63, 0, 0.37, 0.425), img(2, 0.63, 0.455, 0.37, 0.425),
                      txt("caption", 0, 0.91, 1, 0.09)],
    },
    "four-grid": {
        "name": "Four-photo grid", "photos": 4,
        "slots": [img(0, 0, 0, 0.485, 0.44), img(1, 0.515, 0, 0.485, 0.44), img(2, 0, 0.47, 0.485, 0.44),
                  img(3, 0.515, 0.47, 0.485, 0.44), txt("caption", 0, 0.935, 1, 0.065)],
    },
    "quote": {
        "name": "Quote", "photos": 0,
        "slots": [shp("ornament", 0.44, 0.3, 0.12, 0.012), txt("quote", 0.04, 0.34, 0.92, 0.32, align="center", valign="middle"),
                  txt("attribution", 0, 0.69, 1, 0.05, align="center")],
    },
    "timeline": {
        "name": "Timeline", "photos": 0,
        "slots": [txt("heading", 0, 0, 1, 0.09), shp("line", 0.034, 0.135, 0.004, 0.83, "rule")]
        + [s for i in range(5) for s in (
            shp("dot", 0.012, 0.135 + i * 0.17, 0.05, 0.02, "accent"),
            txt(f"items.{i}.label", 0.1, 0.13 + i * 0.17, 0.9, 0.035),
            txt(f"items.{i}.text", 0.1, 0.17 + i * 0.17, 0.9, 0.12, role="body"))],
    },
    "travel": {
        "name": "Travel page", "photos": 1,
        "slots": [txt("location", 0, 0, 1, 0.04), txt("heading", 0, 0.045, 1, 0.08), img(0, 0, 0.14, 1, 0.52),
                  txt("body", 0, 0.7, 0.6, 0.3), txt("date", 0.66, 0.7, 0.34, 0.04), txt("caption", 0.66, 0.75, 0.34, 0.25)],
    },
    "chapter-opener": {
        "name": "Chapter opener", "photos": 0, "showNumber": False,
        "slots": [txt("chapterLabel", 0, 0.3, 1, 0.04, align="center"),
                  txt("heading", 0, 0.35, 1, 0.16, role="title", align="center"),
                  shp("ornament", 0.44, 0.53, 0.12, 0.012), txt("body", 0.1, 0.57, 0.8, 0.3, align="center")],
    },
    "chapter-photo": {
        "name": "Chapter opener with photo", "photos": 1, "showNumber": False,
        "slots": [img(0, 0, 0, 1, 0.5, "page"), txt("chapterLabel", 0, 0.56, 1, 0.04, align="center"),
                  txt("heading", 0, 0.605, 1, 0.15, role="title", align="center"),
                  shp("ornament", 0.44, 0.77, 0.12, 0.012), txt("body", 0.08, 0.8, 0.84, 0.2, align="center")],
    },
    "minimal-text": {
        "name": "Minimal text", "photos": 0,
        "slots": [shp("ornament", 0.06, 0.06, 0.12, 0.012), txt("heading", 0.06, 0.09, 0.88, 0.09),
                  txt("body", 0.06, 0.2, 0.88, 0.8)],
    },
    "closing": {
        "name": "Closing page", "photos": 1,
        "slots": [img(0, 0.2, 0.04, 0.6, 0.42), shp("ornament", 0.44, 0.52, 0.12, 0.012),
                  txt("heading", 0, 0.555, 1, 0.09, align="center"), txt("body", 0.08, 0.66, 0.84, 0.3, align="center")],
    },
    "map": {
        "name": "Map & location", "photos": 0,
        "slots": [shp("map", 0, 0, 1, 0.55, "accent", 0.12), txt("location", 0, 0.6, 1, 0.1, role="heading"),
                  txt("date", 0, 0.71, 1, 0.04), txt("body", 0, 0.77, 1, 0.23)],
    },
}


def template_catalog() -> list[dict]:
    return [{"id": k, "name": v["name"], "photos": v["photos"], "cover": v.get("cover", False)} for k, v in TEMPLATES.items()]


# --- materialization -----------------------------------------------------------

def content_box(book: MemoryBook) -> tuple[float, float, float, float]:
    w, h = book.page_dims()
    th, s = design.theme(book.themeId), design.size_scale(w, h)
    m = th["margins"]
    return m["side"] * s, m["top"] * s, w - 2 * m["side"] * s, h - (m["top"] + m["bottom"]) * s


def field_value(c: PageContent, field: str) -> str:
    if field.startswith("items."):
        _, i, key = field.split(".")
        return getattr(c.items[int(i)], key) if int(i) < len(c.items) else ""
    if field == "dateLocation":
        return " · ".join(v for v in (c.date, c.location) if v)
    return getattr(c, field, "") or ""


def _assign_photos(slots: list[dict], photo_ids: list[str], aspects: dict[str, float], boxes: dict[int, Box]) -> list[str | None]:
    """Order photos so each lands in the slot whose shape crops it least (≤4 photos: brute force)."""
    n = len(slots)
    ids = photo_ids[:n]
    if len(ids) < 2 or len(ids) > 4 or not all(i in aspects for i in ids):
        return ids + [None] * (n - len(ids))

    def cost(order):
        return sum(abs(math.log(aspects[pid] / (boxes[k].w / boxes[k].h))) for k, pid in enumerate(order))
    best = min(itertools.permutations(ids), key=cost)
    if cost(best) < cost(ids) - 0.15:  # keep the narrative order unless the gain is clear
        ids = list(best)
    return ids + [None] * (n - len(ids))


def _fit_text(el: TextElement, rt: design.ResolvedText, field: str) -> str:
    """Make text fit its box: shrink display text a little, return overflowing body text."""
    if design.text_height_mm(el.text, rt, el.box.w) <= el.box.h + 0.1:
        return ""
    if field == "body" or field.endswith(".text"):
        words = el.text.split(" ")
        lo, hi = 1, len(words)
        while lo < hi:  # largest word prefix that fits
            mid = (lo + hi + 1) // 2
            if design.text_height_mm(" ".join(words[:mid]), rt, el.box.w) <= el.box.h + 0.1:
                lo = mid
            else:
                hi = mid - 1
        cut = lo
        for k in range(lo, max(lo - 40, 0), -1):  # prefer ending on a sentence
            if words[k - 1].endswith((".", "!", "?", "…")):
                cut = k
                break
        el.text = " ".join(words[:cut])
        return " ".join(words[cut:])
    size = rt.font_size
    while size > rt.font_size * 0.6:
        size = round(size * 0.94, 2)
        smaller = design.ResolvedText(**{**rt.__dict__, "font_size": size})
        if design.text_height_mm(el.text, smaller, el.box.w) <= el.box.h + 0.1:
            break
    el.style.fontSize = size
    return ""


def photo_crop(focus: dict | None) -> ImageCrop:
    """A new photo's crop: centred on its faces (storage.Asset.focus), else a little above centre."""
    return ImageCrop(x=focus["x"], y=focus["y"]) if focus and focus.get("faces") else ImageCrop(y=0.42)


def materialize(book: MemoryBook, content: PageContent, aspects: dict[str, float], *,
                placeholders: bool = False, page: MemoryPage | None = None,
                focus: dict[str, dict] | None = None) -> tuple[MemoryPage, str]:
    """Build a page from a template. Returns the page and any body text that did not fit."""
    tpl = TEMPLATES.get(content.template) or TEMPLATES["photo-text"]
    pw, ph = book.page_dims()
    th, scale = design.theme(book.themeId), design.size_scale(pw, ph)
    cx, cy, cw, chh = content_box(book)
    slots = tpl["landscape"] if book.orientation == "landscape" and "landscape" in tpl else tpl["slots"]

    def box(s) -> Box:
        if s.get("area") == "page":
            return Box(x=s["x"] * pw, y=s["y"] * ph, w=s["w"] * pw, h=s["h"] * ph)
        return Box(x=cx + s["x"] * cw, y=cy + s["y"] * chh, w=s["w"] * cw, h=s["h"] * chh)

    img_slots = [s for s in slots if s["t"] == "image"]
    img_boxes = {s["index"]: box(s) for s in img_slots}
    photo_order = _assign_photos(img_slots, content.photoIds, aspects, img_boxes)

    page = page or MemoryPage()
    page.template, page.elements = content.template, []
    page.kind = "cover" if tpl.get("cover") else "page"
    page.showNumber = tpl.get("showNumber", True)
    overflow = ""
    tilt = th["image"]["tilt"]
    for s in slots:
        b = box(s)
        if s["t"] == "image":
            pid = photo_order[s["index"]]
            if pid is None and not placeholders:
                continue
            if tilt and s.get("area") != "page" and len(img_slots) > 1:
                b.rotation = tilt * (1 if s["index"] % 2 else -1)
            page.elements.append(ImageElement(box=b, assetId=pid, crop=photo_crop((focus or {}).get(pid)), slot=f"photo.{s['index']}",
                                              frame="none" if s.get("area") == "page" else "theme"))
        elif s["t"] == "shape":
            kind = th["ornament"] if s["kind"] == "ornament" else s["kind"]
            if kind in ("dot", "diamond"):  # square, centred on the slot
                d = (2.6 if kind == "diamond" else 2.2) * scale
                b = Box(x=b.x + b.w / 2 - d / 2, y=b.y + b.h / 2 - d / 2, w=d, h=d)
            elif kind == "line" and s["kind"] == "ornament":
                b = Box(x=b.x, y=b.y + b.h / 2 - 0.2, w=b.w, h=0.4)
            color = s["color"] or ("rule" if kind == "line" else "accent")
            page.elements.append(ShapeElement(box=b, kind=kind, color=color, opacity=s["opacity"], slot="decor"))
        else:
            field = s["field"]
            if field.startswith("items.") and int(field.split(".")[1]) >= len(content.items):
                continue  # timeline rows (incl. their dots) only for real items
            text = field_value(content, field)
            if not text and not placeholders:
                continue
            role = s["role"] or (FIELD_ROLES.get(field) or ("meta" if field.endswith(".label") else "body"))
            el = TextElement(box=b, role=role, text=text, valign=s["valign"], slot=field,
                             style=TextStyle(align=s["align"]) if s["align"] else TextStyle())
            if text:
                rest = _fit_text(el, design.resolve_text(th, role, el.style, scale), field)
                if field == "body":
                    overflow = rest
            page.elements.append(el)
    if content.template == "timeline":
        n = len(content.items)
        page.elements = [e for e in page.elements
                         if not (e.type == "shape" and e.kind == "dot" and e.box.y > cy + (0.135 + n * 0.17) * chh)]
    return page, overflow


def extract_content(page: MemoryPage, template: str | None = None) -> PageContent:
    """Recover template content from a page (used when re-templating an edited page)."""
    c = PageContent(template=template or page.template or "photo-text")
    items: dict[int, TimelineItem] = {}
    extra: list[str] = []
    for e in page.elements:
        if e.type == "image":
            if e.assetId:
                c.photoIds.append(e.assetId)
        elif e.type == "text" and e.text.strip():
            slot = e.slot or ""
            if slot.startswith("items."):
                _, i, key = slot.split(".")
                setattr(items.setdefault(int(i), TimelineItem()), key, e.text)
            elif slot in PageContent.model_fields and isinstance(getattr(c, slot), str) and not getattr(c, slot):
                setattr(c, slot, e.text)
            elif slot == "dateLocation":
                c.date = c.date or e.text
            else:
                extra.append(e.text)
    c.items = [items[k] for k in sorted(items)]
    if extra:
        c.body = "\n\n".join([c.body, *extra]).strip()
    return c


def best_template(c: PageContent, aspects: dict[str, float], is_cover: bool = False) -> str:
    """Pick a template whose photo count matches the content (fixes AI or user mismatches)."""
    n = len(c.photoIds)
    tpl = TEMPLATES.get(c.template)
    if is_cover:
        if tpl and tpl.get("cover") and tpl["photos"] == min(n, 1):
            return c.template
        return "cover" if n else "cover-text"
    if tpl and not tpl.get("cover") and (tpl["photos"] == n or (c.template == "closing" and n <= 1)):
        return c.template
    if n == 0:
        if c.items:
            return "timeline"
        if c.quote and not c.body:
            return "quote"
        if c.chapterLabel and len(c.body) < 400:
            return "chapter-opener"
        return "minimal-text"
    if n == 1:
        return "photo-text" if (c.body or c.heading) else "full-photo"
    if n == 2:
        portrait = all(aspects.get(p, 1.5) < 0.95 for p in c.photoIds)
        return "two-portraits" if portrait else "two-photos"
    return "three-collage" if n == 3 else "four-grid"


def layout_pages(book: MemoryBook, contents: list[PageContent], aspects: dict[str, float],
                 chapter_id: str | None = None, focus: dict[str, dict] | None = None) -> list[MemoryPage]:
    """Materialize a run of planned pages, splitting oversized photo sets and overflowing text."""
    pages: list[MemoryPage] = []
    for c in contents:
        queue = [c]
        if len(c.photoIds) > 4:  # spill extra photos into grid pages
            queue = [c.model_copy(update={"photoIds": c.photoIds[:4]})] + [
                PageContent(template="four-grid", photoIds=c.photoIds[i:i + 4]) for i in range(4, len(c.photoIds), 4)]
        for q in queue:
            q.template = best_template(q, aspects)
            page, rest = materialize(book, q, aspects, focus=focus)
            page.chapterId = chapter_id
            pages.append(page)
            # Never drop planned words: text the template has no slot for goes on a following page.
            fields = {s.get("field") for s in TEMPLATES[q.template]["slots"]}
            if q.quote and "quote" not in fields:
                pages.append(materialize(book, PageContent(template="quote", quote=q.quote, attribution=q.attribution), aspects, focus=focus)[0])
            lost = {k: getattr(q, k) for k in ("heading", "body") if getattr(q, k) and k not in fields}
            if lost:
                page, rest = materialize(book, PageContent(template="minimal-text", **lost), aspects, focus=focus)
                pages.append(page)
            for p in pages:
                p.chapterId = p.chapterId or chapter_id
            guard = 0
            while rest and guard < 20:
                page, rest = materialize(book, PageContent(template="minimal-text", body=rest), aspects, focus=focus)
                page.chapterId = chapter_id
                pages.append(page)
                guard += 1
    return pages



def apply_template(book: MemoryBook, page: MemoryPage, template: str, aspects: dict[str, float], *,
                   placeholders: bool = True, focus: dict[str, dict] | None = None) -> list[MemoryPage]:
    """Re-template a page exactly as asked. Photos and text that no longer fit spill onto
    following pages, so switching layouts never loses content."""
    tpl = TEMPLATES[template]
    c = extract_content(page, template)
    keep = 1 if template == "closing" else tpl["photos"]
    spill_photos, c.photoIds = c.photoIds[keep:], c.photoIds[:keep]
    fields = {s.get("field") for s in tpl["slots"]}
    lost = {k: getattr(c, k) for k in ("heading", "body") if getattr(c, k) and k not in fields}
    quote = c.quote if c.quote and "quote" not in fields else ""
    out, rest = materialize(book, c, aspects, placeholders=placeholders, page=page, focus=focus)
    extra = [PageContent(template="minimal-text", **lost)] if lost else []
    if rest:
        extra.append(PageContent(template="minimal-text", body=rest))
    if quote:
        extra.append(PageContent(template="quote", quote=quote, attribution=c.attribution))
    if spill_photos:
        extra.append(PageContent(template="four-grid", photoIds=spill_photos))
    return [out, *layout_pages(book, extra, aspects, page.chapterId, focus)]
