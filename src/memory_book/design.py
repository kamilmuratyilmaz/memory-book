"""Themes, fonts and text measurement shared by the layout engine and the PDF renderer.

Style resolution mirrors resolveTextStyle() in web/src/model.ts; both use the
same TTF files, so wrapping in the browser and in the PDF uses identical metrics.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from .model import TextElement, TextRole, TextStyle

STATIC = Path(__file__).parent / "static"
PAPER_TEXTURE = STATIC / "paper.png"
FONT_DIR = STATIC / "fonts"
MM_PER_PT = 25.4 / 72


@cache
def design() -> dict:
    return json.loads((STATIC / "themes.json").read_text())


def themes() -> dict[str, dict]:
    return design()["themes"]


def theme(theme_id: str) -> dict:
    return themes().get(theme_id) or themes()["editorial"]


def size_scale(page_w: float, page_h: float) -> float:
    """Theme sizes are authored for A5; larger pages scale type and margins up."""
    return min(page_w, page_h) / 148.0


def color(th: dict, value: str | None, default: str = "text") -> str:
    value = value or default
    return th["colors"].get(value, value)


@dataclass(frozen=True)
class ResolvedText:
    font_family: str
    font_size: float  # pt
    bold: bool
    italic: bool
    line_height: float  # multiple of font size
    letter_spacing: float  # em
    uppercase: bool
    align: str
    color: str


def resolve_text(th: dict, role: TextRole, style: TextStyle, scale: float) -> ResolvedText:
    base = th["text"][role]
    pick = lambda k, d=None: v if (v := getattr(style, k)) is not None else base.get(k, d)  # noqa: E731
    return ResolvedText(
        font_family=pick("fontFamily"),
        font_size=style.fontSize if style.fontSize is not None else round(base["fontSize"] * scale, 2),
        bold=bool(pick("bold", False)),
        italic=bool(pick("italic", False)),
        line_height=pick("lineHeight", 1.4),
        letter_spacing=pick("letterSpacing", 0.0),
        uppercase=bool(pick("uppercase", False)),
        align=pick("align", "left"),
        color=color(th, style.color or base.get("color")),
    )


def element_text(el: TextElement, rt: ResolvedText) -> str:
    return el.text.upper() if rt.uppercase else el.text


# --- fonts -----------------------------------------------------------------

def _font_entry(family: str) -> dict:
    for f in design()["fonts"]:
        if f["name"] == family:
            return f
    return design()["fonts"][1]  # Lora: safe serif fallback for unknown families


@cache
def font_name(family: str, bold: bool, italic: bool) -> str:
    """Register (once) and return the reportlab font name for a family variant."""
    entry = _font_entry(family)
    italic = italic and entry["italic"]  # no synthetic italics (CSS sets font-synthesis: none)
    suffix = {(False, False): "Regular", (True, False): "Bold", (False, True): "Italic", (True, True): "BoldItalic"}[(bold, italic)]
    name = f"{entry['file']}-{suffix}"
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / f"{name}.ttf")))
    return name


@cache
def font_metrics(name: str) -> tuple[float, float]:
    """(ascent, descent) per 1pt of font size, as browsers use them for line boxes.

    Browsers take hhea metrics (OS/2 typo metrics when USE_TYPO_METRICS is set);
    reportlab only exposes typo metrics, so read the tables directly.
    """
    data = (FONT_DIR / f"{name}.ttf").read_bytes()
    tables = {data[o:o + 4]: struct.unpack(">I", data[o + 8:o + 12])[0]
              for o in range(12, 12 + 16 * struct.unpack(">H", data[4:6])[0], 16)}
    upm = struct.unpack(">H", data[tables[b"head"] + 18:][:2])[0]
    os2 = tables[b"OS/2"]
    if struct.unpack(">H", data[os2 + 62:os2 + 64])[0] & 0x80:
        asc, desc = struct.unpack(">hh", data[os2 + 68:os2 + 72])
    else:
        asc, desc = struct.unpack(">hh", data[tables[b"hhea"] + 4:tables[b"hhea"] + 8])
    return asc / upm, -desc / upm


def text_width(s: str, font: str, size: float, tracking_pt: float) -> float:
    return pdfmetrics.stringWidth(s, font, size) + tracking_pt * len(s)


def wrap_lines(text: str, font: str, size: float, width_pt: float, tracking_pt: float = 0.0) -> list[tuple[str, bool]]:
    """Greedy word wrap matching the browser (white-space: pre-wrap; overflow-wrap: anywhere).

    Returns (line, ends_paragraph) pairs; justified text leaves paragraph-final lines ragged.
    """
    lines: list[tuple[str, bool]] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, font, size, tracking_pt) <= width_pt + 0.01:
                line = candidate
                continue
            if line:
                lines.append((line, False))
            line = word
            while line and text_width(line, font, size, tracking_pt) > width_pt:  # break overlong words
                cut = len(line) - 1
                while cut > 1 and text_width(line[:cut], font, size, tracking_pt) > width_pt:
                    cut -= 1
                lines.append((line[:cut], False))
                line = line[cut:]
        lines.append((line, True))
    return lines


def text_height_mm(text: str, rt: ResolvedText, width_mm: float) -> float:
    font = font_name(rt.font_family, rt.bold, rt.italic)
    lines = wrap_lines(text.upper() if rt.uppercase else text, font, rt.font_size, width_mm / MM_PER_PT, rt.letter_spacing * rt.font_size)
    return len(lines) * rt.font_size * rt.line_height * MM_PER_PT
