"""Deterministic print renderer: MemoryBook JSON -> PDF (vector text, embedded fonts).

Independent of the browser and React. Geometry, style resolution, crop math,
frames and text wrapping mirror web/src/render/* so the PDF matches the editor.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass, field
from typing import BinaryIO

from PIL import Image
from reportlab.lib.colors import HexColor, white
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

from . import design
from .model import ImageElement, MemoryBook, MemoryPage, ShapeElement, TextElement, crop_rect
from .storage import Store

PRINT_DPI = 300


def _sepia(a: float) -> tuple[float, ...]:
    """The CSS Filter Effects sepia(a) colour matrix, so PDF tones match the browser's."""
    k = 1 - a
    return (0.393 + 0.607 * k, 0.769 - 0.769 * k, 0.189 - 0.189 * k, 0,
            0.349 - 0.349 * k, 0.686 + 0.314 * k, 0.168 - 0.168 * k, 0,
            0.272 - 0.272 * k, 0.534 - 0.534 * k, 0.131 + 0.869 * k, 0)


SEPIA = _sepia(0.45)  # CSS: filter: sepia(0.45)
MONO = (0.2126, 0.7152, 0.0722, 0) * 3  # CSS: filter: grayscale(1)


@dataclass
class Preflight:
    warnings: list[str] = field(default_factory=list)

    def add(self, page_no: int, msg: str) -> None:
        self.warnings.append(f"Page {page_no}: {msg}" if page_no else f"Cover: {msg}")


def frame_insets(frame: str, w: float, h: float) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) padding in mm for photo frames. Mirrors frameInsets() in TS."""
    if frame == "mat":
        p = min(max(min(w, h) * 0.035, 1.2), 4.0)
        return p, p, p, p
    if frame == "polaroid":
        p = min(max(min(w, h) * 0.05, 1.5), 5.0)
        return p, p, p, p * 3.2
    return 0, 0, 0, 0


def resolve_frame(el: ImageElement, th: dict, w: float, h: float) -> str:
    frame = th["image"]["frame"] if el.frame == "theme" else el.frame
    return frame if min(w, h) >= 12 else "none"  # frames on tiny photos only look like noise


class Renderer:
    def __init__(self, book: MemoryBook, store: Store, bleed: float):
        self.book, self.store, self.bleed = book, store, bleed
        self.w, self.h = book.page_dims()
        self.th = design.theme(book.themeId)
        self.scale = design.size_scale(self.w, self.h)
        self.images: dict[tuple, ImageReader] = {}
        self.originals: dict[str, Image.Image | None] = {}
        self.preflight = Preflight()
        self.page_no = 0

    # coordinates: model uses mm from top-left; PDF uses pt from bottom-left of the trim box
    def place(self, c, box) -> None:
        cx, cy = (box.x + box.w / 2) * mm, (self.h - box.y - box.h / 2) * mm
        c.translate(cx, cy)
        if box.rotation:
            c.rotate(-box.rotation)

    def bled(self, box):
        """Extend boxes that touch the trim edge into the bleed so prints have no white slivers."""
        if not self.bleed:
            return box
        b, e = self.bleed, 0.5
        x0, y0, x1, y1 = box.x, box.y, box.x + box.w, box.y + box.h
        x0 = x0 - b if x0 <= e else x0
        y0 = y0 - b if y0 <= e else y0
        x1 = x1 + b if x1 >= self.w - e else x1
        y1 = y1 + b if y1 >= self.h - e else y1
        return box.model_copy(update={"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})

    def render(self, out: BinaryIO) -> Preflight:
        b = self.bleed
        c = rl_canvas.Canvas(out, pagesize=((self.w + 2 * b) * mm, (self.h + 2 * b) * mm), pageCompression=1)
        c.setTitle(self.book.title)
        c.setAuthor(self.book.author)
        c.setCreator("Memory Book")
        if b:
            trim = [b * mm, b * mm, (self.w + b) * mm, (self.h + b) * mm]
            c.setTrimBox(trim)
            c.setBleedBox([0, 0, (self.w + 2 * b) * mm, (self.h + 2 * b) * mm])
        for i, page in enumerate(self.book.pages):
            self.page_no = i
            c.saveState()
            c.translate(b * mm, b * mm)
            self.draw_page(c, page)
            c.restoreState()
            c.showPage()
        c.save()
        return self.preflight

    def draw_page(self, c, page: MemoryPage) -> None:
        b = self.bleed
        c.setFillColor(HexColor(design.color(self.th, page.background.color, "paper")))
        c.rect(-b * mm, -b * mm, (self.w + 2 * b) * mm, (self.h + 2 * b) * mm, stroke=0, fill=1)
        if self.th["texture"]:
            tile = 60 * mm
            reader = self.texture()
            for tx in range(math.ceil((self.w + 2 * b) * mm / tile)):
                for ty in range(math.ceil((self.h + 2 * b) * mm / tile)):
                    # tiles anchored at the trim top-left like CSS background-position: 0 0
                    c.drawImage(reader, -b * mm + tx * tile, (self.h + b) * mm - (ty + 1) * tile, tile, tile, mask="auto")
        for el in page.elements:
            c.saveState()
            if el.type == "image":
                self.draw_image(c, el)
            elif el.type == "text":
                self.draw_text(c, el)
            else:
                self.draw_shape(c, el)
            c.restoreState()
        if page.showNumber and page.kind != "cover" and self.page_no:
            self.draw_page_number(c)

    def texture(self) -> ImageReader:
        if ("texture",) not in self.images:
            self.images["texture",] = ImageReader(str(design.PAPER_TEXTURE))
        return self.images["texture",]

    # --- images -----------------------------------------------------------------

    def original(self, asset_id: str) -> Image.Image | None:
        if asset_id not in self.originals:
            self.originals[asset_id] = self.store.load_original(asset_id)
        return self.originals[asset_id]

    def draw_image(self, c, el: ImageElement) -> None:
        box = self.bled(el.box) if not el.box.rotation else el.box
        frame = resolve_frame(el, self.th, box.w, box.h)
        left, top, right, btm = frame_insets(frame, box.w, box.h)
        iw, ih = box.w - left - right, box.h - top - btm
        self.place(c, box)
        x0, y0 = -box.w / 2 * mm, -box.h / 2 * mm  # bottom-left of the box in local coords
        if frame != "none":
            for dx, dy, a in ((0.25, -0.45, 0.10), (0.6, -1.0, 0.06)):
                c.setFillColor(HexColor("#000000"))
                c.setFillAlpha(a)
                c.rect(x0 + dx * mm, y0 + dy * mm, box.w * mm, box.h * mm, stroke=0, fill=1)
            c.setFillAlpha(1)
            c.setFillColor(HexColor("#FFFDF8"))
            c.rect(x0, y0, box.w * mm, box.h * mm, stroke=0, fill=1)
        src = self.original(el.assetId) if el.assetId else None
        ix, iy = x0 + left * mm, y0 + btm * mm
        if src is None:
            if el.assetId:
                self.preflight.add(self.page_no, "a photo is missing and was left blank")
            c.setFillColor(HexColor("#000000"))
            c.setFillAlpha(0.06)
            c.rect(ix, iy, iw * mm, ih * mm, stroke=0, fill=1)
            return
        sx, sy, sw, sh = crop_rect(src.width, src.height, iw, ih, el.crop)
        dpi = sw / (iw / 25.4)
        if dpi < 150:
            self.preflight.add(self.page_no, f"a photo prints at {dpi:.0f} dpi and may look soft")
        reader = self.cropped(el, src, (sx, sy, sw, sh), iw, ih)
        radius = self.th["image"]["radius"] if frame == "none" else 0
        path = c.beginPath()
        if radius:
            path.roundRect(ix, iy, iw * mm, ih * mm, radius * mm)
        else:
            path.rect(ix, iy, iw * mm, ih * mm)
        c.clipPath(path, stroke=0, fill=0)
        c.drawImage(reader, ix, iy, iw * mm, ih * mm)
        if self.th["image"]["tape"] and frame != "none":
            c.restoreState()
            c.saveState()
            self.place(c, box)
            tw = min(box.w * 0.38, 24 * self.scale)
            c.translate(0, box.h / 2 * mm)
            c.rotate(4 if el.box.rotation <= 0 else -4)
            c.setFillColor(HexColor("#E9DDBF"))
            c.setFillAlpha(0.78)
            c.rect(-tw / 2 * mm, -3.2 * mm, tw * mm, 6.4 * mm, stroke=0, fill=1)

    def cropped(self, el: ImageElement, src: Image.Image, rect, iw: float, ih: float) -> ImageReader:
        tone = self.th["image"]["tone"]
        key = (el.assetId, tuple(round(v) for v in rect), round(iw, 1), round(ih, 1), tone)
        if key not in self.images:
            sx, sy, sw, sh = rect
            im = src.crop((round(sx), round(sy), round(sx + sw), round(sy + sh)))
            target = (max(1, round(iw / 25.4 * PRINT_DPI)), max(1, round(ih / 25.4 * PRINT_DPI)))
            if im.width > target[0] * 1.1:
                im = im.resize(target, Image.Resampling.LANCZOS)
            if tone == "sepia":
                im = im.convert("RGB", SEPIA)
            elif tone == "mono":
                im = im.convert("RGB", MONO)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=92, subsampling=0)
            buf.seek(0)
            self.images[key] = ImageReader(buf)
        return self.images[key]

    # --- text ----------------------------------------------------------------------

    def draw_text(self, c, el: TextElement) -> None:
        if not el.text.strip():
            return
        rt = design.resolve_text(self.th, el.role, el.style, self.scale)
        text = design.element_text(el, rt)
        font = design.font_name(rt.font_family, rt.bold, rt.italic)
        size, width_pt = rt.font_size, el.box.w * mm
        while True:
            tracking = rt.letter_spacing * size
            lines = design.wrap_lines(text, font, size, width_pt, tracking)
            lh = size * rt.line_height
            if len(lines) * lh <= el.box.h * mm + 1 or size <= 4:
                break
            size *= 0.95  # safety net only: the editor flags overflow before export
        if size != rt.font_size:
            self.preflight.add(self.page_no, f"text “{el.text[:30]}…” was reduced to fit its box")
        asc, desc = design.font_metrics(font)
        total = len(lines) * lh
        offset = {"top": 0, "middle": (el.box.h * mm - total) / 2, "bottom": el.box.h * mm - total}[el.valign]
        self.place(c, el.box)
        left, top = -width_pt / 2, el.box.h / 2 * mm
        c.setFillColor(HexColor(rt.color))
        for i, (line, last) in enumerate(lines):
            baseline = top - offset - i * lh - (lh - (asc + desc) * size) / 2 - asc * size
            lw = design.text_width(line, font, size, tracking)
            word_space = 0.0
            x = left
            if rt.align == "center":
                x = left + (width_pt - lw) / 2
            elif rt.align == "right":
                x = left + width_pt - lw
            elif rt.align == "justify" and not last and line.count(" "):
                word_space = (width_pt - lw) / line.count(" ")
            t = c.beginText(x, baseline)
            t.setFont(font, size)
            t.setCharSpace(tracking)
            t.setWordSpace(word_space)
            t.textOut(line)
            c.drawText(t)

    def draw_page_number(self, c) -> None:
        pn = self.th["pageNumber"]
        font = design.font_name(pn["fontFamily"], False, False)
        size = pn["fontSize"] * self.scale
        asc, desc = design.font_metrics(font)
        center_y = self.th["margins"]["bottom"] * self.scale / 2  # from the bottom trim edge
        baseline = center_y * mm - (asc - desc) * size / 2
        c.setFillColor(HexColor(design.color(self.th, pn["color"])))
        c.setFont(font, size)
        c.drawCentredString(self.w / 2 * mm, baseline, str(self.page_no))

    # --- shapes ----------------------------------------------------------------------

    def draw_shape(self, c, el: ShapeElement) -> None:
        box = self.bled(el.box) if el.kind == "rect" and not el.box.rotation else el.box
        col = HexColor(design.color(self.th, el.color, "accent"))
        self.place(c, box)
        w, h = box.w * mm, box.h * mm
        c.setFillColor(col)
        c.setStrokeColor(col)
        c.setFillAlpha(el.opacity)
        if el.kind in ("rect", "line"):
            c.rect(-w / 2, -h / 2, w, h, stroke=0, fill=1)
        elif el.kind == "dot":
            c.circle(0, 0, min(w, h) / 2, stroke=0, fill=1)
        elif el.kind == "diamond":
            p = c.beginPath()
            p.moveTo(0, h / 2)
            p.lineTo(w / 2, 0)
            p.lineTo(0, -h / 2)
            p.lineTo(-w / 2, 0)
            p.close()
            c.drawPath(p, stroke=0, fill=1)
        elif el.kind == "map":
            c.rect(-w / 2, -h / 2, w, h, stroke=0, fill=1)
            step = 8 * self.scale * mm
            c.setStrokeAlpha(min(1, el.opacity * 2.2))
            c.setLineWidth(0.25 * mm)
            x = step
            while x < w:
                c.line(-w / 2 + x, -h / 2, -w / 2 + x, h / 2)
                x += step
            y = step
            while y < h:  # grid lines measured from the top edge, like the SVG
                c.line(-w / 2, h / 2 - y, w / 2, h / 2 - y)
                y += step
            c.setFillAlpha(1)
            r = 2.6 * self.scale * mm
            p = c.beginPath()  # pin: circle head with a point below, tip at the centre
            p.moveTo(0, 0)
            p.lineTo(-r * 0.75, r * 1.6)
            p.lineTo(r * 0.75, r * 1.6)
            p.close()
            c.drawPath(p, stroke=0, fill=1)
            c.circle(0, r * 2.1, r, stroke=0, fill=1)
            c.setFillColor(white)
            c.circle(0, r * 2.1, r * 0.4, stroke=0, fill=1)


def render_pdf(book: MemoryBook, store: Store, out: BinaryIO, bleed: float = 0.0) -> list[str]:
    """Render the book; returns preflight warnings (missing photos, low resolution, shrunk text)."""
    return Renderer(book, store, bleed).render(out).warnings
