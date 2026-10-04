"""Face-aware crops: the detector result, its storage, and new photos laid out centred on the faces."""

import json

import numpy as np
from conftest import jpeg
from PIL import Image

from memory_book import faces
from memory_book.layout import PageContent, materialize
from memory_book.model import MemoryBook


def _face(x, y, w, h):
    return [x, y, w, h] + [0] * 10 + [0.95]  # YuNet row: box, 5 landmarks, score


class FakeDetector:
    def __init__(self, rows):
        self.rows = np.array(rows, dtype=np.float32) if rows else None

    def setInputSize(self, size):
        pass

    def detect(self, image):
        return 1, self.rows


def test_focus_is_the_centre_of_the_main_faces(monkeypatch):
    monkeypatch.setattr(faces, "_detector", lambda: FakeDetector([
        _face(100, 50, 100, 100), _face(300, 60, 90, 90),
        _face(560, 400, 40, 40)]))  # a small face in the background: ignored
    found = faces.focus(Image.new("RGB", (640, 480)))
    assert found == {"x": round(245 / 640, 4), "y": round(100 / 480, 4), "faces": 2}
    assert json.dumps(found)  # stored as JSON: plain floats, not numpy


def test_real_detector_loads_and_finds_no_face_in_a_blank_photo():
    assert faces.focus(Image.new("RGB", (800, 600), (200, 180, 160))) == {"faces": 0}


def test_upload_stores_focus_and_new_layouts_centre_on_it(store, monkeypatch):
    monkeypatch.setattr(faces, "focus", lambda im: {"x": 0.3, "y": 0.2, "faces": 1})
    asset = store.add_asset("selfie.jpg", jpeg(1000, 1500))
    assert store.get_assets([asset.id])[asset.id].focus == {"x": 0.3, "y": 0.2, "faces": 1}
    assert asset.public()["focus"]["faces"] == 1

    book = MemoryBook()
    content = PageContent(template="full-photo", photoIds=[asset.id])
    page, _ = materialize(book, content, {asset.id: asset.aspect}, focus={asset.id: asset.focus})
    crop = next(e for e in page.elements if e.type == "image").crop
    assert (crop.x, crop.y) == (0.3, 0.2)
    no_faces, _ = materialize(book, content, {asset.id: asset.aspect}, focus={asset.id: {"faces": 0}})
    assert next(e for e in no_faces.elements if e.type == "image").crop.y == 0.42  # the old default


def test_failed_detection_never_blocks_an_upload_and_is_retried(store, monkeypatch):
    def broken(im):
        raise RuntimeError("model missing")
    monkeypatch.setattr(faces, "focus", broken)
    asset = store.add_asset("p.jpg", jpeg(800, 600))
    assert store.get_assets([asset.id])[asset.id].focus is None
    monkeypatch.setattr(faces, "focus", lambda im: {"x": 0.5, "y": 0.4, "faces": 3})
    assert store.backfill_focus() == 1
    assert store.get_assets([asset.id])[asset.id].focus["faces"] == 3
    assert store.backfill_focus() == 0  # done once
