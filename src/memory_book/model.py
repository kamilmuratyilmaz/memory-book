"""The MemoryBook document model — the single source of truth.

Editor, reader and PDF renderer all consume this exact structure. All geometry
is in millimetres on a page of fixed physical size; font sizes are in points.
The TypeScript mirror lives in web/src/model.ts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

# Logical page dimensions in mm (portrait). Never derived from the viewport.
PAGE_SIZES: dict[str, tuple[float, float]] = {
    "A5": (148.0, 210.0),
    "A4": (210.0, 297.0),
    "Square": (210.0, 210.0),
}

PageSize = Literal["A5", "A4", "Square"]
Orientation = Literal["portrait", "landscape"]
TextRole = Literal["title", "subtitle", "heading", "body", "caption", "quote", "meta"]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Box(Model):
    x: float
    y: float
    w: float = Field(gt=0)
    h: float = Field(gt=0)
    rotation: float = 0


class TextStyle(Model):
    """Per-element overrides; anything unset falls back to the theme role style."""

    fontFamily: str | None = None
    fontSize: float | None = Field(default=None, gt=0, le=400)
    color: str | None = None
    align: Literal["left", "center", "right", "justify"] | None = None
    bold: bool | None = None
    italic: bool | None = None
    lineHeight: float | None = Field(default=None, gt=0.5, le=4)
    letterSpacing: float | None = None
    uppercase: bool | None = None


class TextElement(Model):
    type: Literal["text"] = "text"
    id: str = Field(default_factory=lambda: new_id("el"))
    box: Box
    role: TextRole = "body"
    text: str = ""
    style: TextStyle = Field(default_factory=TextStyle)
    valign: Literal["top", "middle", "bottom"] = "top"
    slot: str | None = None  # template field this element was filled from


class ImageCrop(Model):
    """Focal point (0..1 of the source image) and zoom (>=1) for cover-fit cropping."""

    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    zoom: float = Field(default=1.0, ge=1, le=5)


class ImageElement(Model):
    type: Literal["image"] = "image"
    id: str = Field(default_factory=lambda: new_id("el"))
    box: Box
    assetId: str | None = None
    crop: ImageCrop = Field(default_factory=ImageCrop)
    frame: Literal["theme", "none", "mat", "polaroid"] = "theme"
    alt: str = ""
    slot: str | None = None


class ShapeElement(Model):
    type: Literal["shape"] = "shape"
    id: str = Field(default_factory=lambda: new_id("el"))
    box: Box
    kind: Literal["line", "rect", "dot", "diamond", "map"] = "rect"
    color: str | None = None  # palette key or hex; defaults to theme accent
    opacity: float = Field(default=1.0, ge=0, le=1)
    slot: str | None = None


PageElement = Annotated[Union[TextElement, ImageElement, ShapeElement], Field(discriminator="type")]


class Background(Model):
    color: str | None = None  # palette key or hex; None = theme paper


class MemoryPage(Model):
    id: str = Field(default_factory=lambda: new_id("pg"))
    kind: Literal["cover", "page"] = "page"
    chapterId: str | None = None
    template: str | None = None
    elements: list[PageElement] = Field(default_factory=list)
    background: Background = Field(default_factory=Background)
    showNumber: bool = True


class Chapter(Model):
    id: str = Field(default_factory=lambda: new_id("ch"))
    title: str
    summary: str = ""


class BookMetadata(Model):
    createdAt: str = Field(default_factory=now_iso)
    updatedAt: str = Field(default_factory=now_iso)
    generatedBy: str = "manual"
    mood: str = ""
    language: str = "en"
    assetIds: list[str] = Field(default_factory=list)  # the book's photo library, incl. unused photos
    notice: str = ""  # user-facing note about how the book was made (e.g. AI fallback)


class MemoryBook(Model):
    schemaVersion: int = SCHEMA_VERSION
    id: str = Field(default_factory=lambda: new_id("bk"))
    title: str = "Untitled memories"
    subtitle: str = ""
    author: str = ""
    pageSize: PageSize = "A5"
    orientation: Orientation = "portrait"
    themeId: str = "editorial"
    chapters: list[Chapter] = Field(default_factory=list)
    pages: list[MemoryPage] = Field(default_factory=list)
    metadata: BookMetadata = Field(default_factory=BookMetadata)

    def page_dims(self) -> tuple[float, float]:
        w, h = PAGE_SIZES[self.pageSize]
        return (h, w) if self.orientation == "landscape" else (w, h)

    def asset_ids(self) -> set[str]:
        return {e.assetId for p in self.pages for e in p.elements if e.type == "image" and e.assetId}


def crop_rect(img_w: float, img_h: float, box_w: float, box_h: float, crop: ImageCrop) -> tuple[float, float, float, float]:
    """Source rectangle (sx, sy, sw, sh) in image pixels shown by a cover-fit box.

    Mirrored exactly by cropRect() in web/src/model.ts so editor and PDF crop identically.
    """
    box_ar, img_ar = box_w / box_h, img_w / img_h
    if img_ar > box_ar:
        sh, sw = img_h, img_h * box_ar
    else:
        sw, sh = img_w, img_w / box_ar
    sw, sh = sw / crop.zoom, sh / crop.zoom
    sx = min(max(crop.x * img_w - sw / 2, 0), img_w - sw)
    sy = min(max(crop.y * img_h - sh / 2, 0), img_h - sh)
    return sx, sy, sw, sh
