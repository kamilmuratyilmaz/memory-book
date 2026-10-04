"""Persistence: PostgreSQL for books and asset metadata, S3-compatible object storage (RustFS) for files.

Books are stored as validated MemoryBook JSONB with an integer version used for
optimistic concurrency. Originals are never modified; previews and thumbnails
are derived copies used by the browser. Exported PDFs live in the same bucket,
so the app process itself keeps no state on disk.

Object keys: assets/{id}/original.{ext}, assets/{id}/preview.jpg, assets/{id}/thumb.jpg, exports/{name}.pdf
"""

from __future__ import annotations

import io
import logging
import os
from dataclasses import dataclass
from datetime import datetime

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from PIL import ExifTags, Image, ImageOps
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from . import faces
from .model import MemoryBook, new_id, now_iso

log = logging.getLogger(__name__)

Image.MAX_IMAGE_PIXELS = 250_000_000  # refuse decompression bombs, allow big camera files
VARIANTS = {"preview": 1800, "thumb": 480}
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "TIFF", "MPO", "BMP"}
MAX_UPLOAD_BYTES = 60 * 1024 * 1024
CONTENT_TYPES = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif",
                 "tif": "image/tiff", "bmp": "image/bmp", "pdf": "application/pdf"}

# ponytail: idempotent CREATE IF NOT EXISTS on startup; adopt a migration tool when the schema first changes
SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    doc JSONB NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS books_updated_at ON books (updated_at DESC);
CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    taken_at TEXT,
    description TEXT NOT NULL DEFAULT '',
    ext TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS agent_threads (
    thread_id TEXT PRIMARY KEY,
    messages JSONB NOT NULL DEFAULT '[]',
    pending JSONB,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now());
-- where the faces are (faces.focus); NULL = not analysed yet
ALTER TABLE assets ADD COLUMN IF NOT EXISTS focus JSONB;
"""
THREAD_MESSAGE_LIMIT = 200  # ponytail: keep the newest messages only; summarise old turns if threads get long


class ConflictError(Exception):
    def __init__(self, server_version: int):
        super().__init__(f"book changed on server (version {server_version})")
        self.server_version = server_version


class AssetError(ValueError):
    pass


@dataclass
class Asset:
    id: str
    filename: str
    width: int
    height: int
    taken_at: str | None
    description: str
    focus: dict | None = None  # {"x", "y", "faces"} from faces.focus

    @property
    def aspect(self) -> float:
        return self.width / self.height

    def public(self) -> dict:
        return {"id": self.id, "filename": self.filename, "width": self.width, "height": self.height,
                "takenAt": self.taken_at, "description": self.description, "focus": self.focus}


class Blobs:
    """A bucket on an S3-compatible server (RustFS in docker compose)."""

    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str):
        self.bucket = bucket
        self.s3 = boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=access_key,
                               aws_secret_access_key=secret_key, region_name="us-east-1",
                               config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}))
        try:
            self.s3.head_bucket(Bucket=bucket)
        except ClientError:
            self.s3.create_bucket(Bucket=bucket)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes | None:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None
            raise

    def delete_prefix(self, prefix: str) -> None:
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})


class Store:
    def __init__(self, database_url: str, blobs: Blobs, **connect_kwargs):
        self.blobs = blobs
        self.pool = ConnectionPool(database_url, min_size=1, max_size=10, open=True,
                                   kwargs={"row_factory": dict_row, **connect_kwargs})
        with self.pool.connection() as conn:
            conn.execute(SCHEMA)

    def close(self) -> None:
        self.pool.close()

    def _one(self, sql: str, params=()) -> dict | None:
        with self.pool.connection() as conn:
            cur = conn.execute(sql, params)
            return cur.fetchone() if cur.description else None

    def _all(self, sql: str, params=()) -> list[dict]:
        with self.pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    # --- books -----------------------------------------------------------------

    def create_book(self, book: MemoryBook) -> int:
        self._one("INSERT INTO books (id, title, doc) VALUES (%s, %s, %s)",
                  (book.id, book.title, Jsonb(book.model_dump(mode="json"))))
        return 1

    def get_book(self, book_id: str) -> tuple[MemoryBook, int] | None:
        row = self._one("SELECT doc, version FROM books WHERE id = %s", (book_id,))
        return (MemoryBook.model_validate(row["doc"]), row["version"]) if row else None

    def save_book(self, book: MemoryBook, base_version: int | None) -> int:
        """Persist a book. base_version=None forces the write (user chose to overwrite)."""
        book.metadata.updatedAt = now_iso()
        # the version check and the write are one atomic statement: no lock, no lost updates
        row = self._one("""
            UPDATE books SET title = %s, doc = %s, version = version + 1, updated_at = now()
            WHERE id = %s AND (%s::int IS NULL OR version = %s)
            RETURNING version""", (book.title, Jsonb(book.model_dump(mode="json")), book.id, base_version, base_version))
        if row:
            return row["version"]
        current = self._one("SELECT version FROM books WHERE id = %s", (book.id,))
        if current is None:
            raise KeyError(book.id)
        raise ConflictError(current["version"])

    def list_books(self) -> list[dict]:
        rows = self._all("""
            SELECT id, title, doc->>'subtitle' AS subtitle, doc->>'themeId' AS "themeId",
                   doc->>'pageSize' AS "pageSize", doc->>'orientation' AS orientation,
                   jsonb_array_length(doc->'pages') AS "pageCount", doc->'pages'->0 AS "coverPage", updated_at
            FROM books ORDER BY updated_at DESC""")
        for r in rows:
            cover = r["coverPage"] or {"elements": []}
            r["coverAssetId"] = next((e.get("assetId") for e in cover["elements"]
                                      if e["type"] == "image" and e.get("assetId")), None)
            r["updatedAt"] = r.pop("updated_at").isoformat(timespec="seconds")
        return rows

    def delete_book(self, book_id: str) -> None:
        self._one("DELETE FROM books WHERE id = %s", (book_id,))

    # --- chat threads (AG-UI) ---------------------------------------------------

    def get_thread(self, thread_id: str) -> dict:
        """{"messages": [...AG-UI messages], "pending": interrupt awaiting approval or None}."""
        row = self._one("SELECT messages, pending FROM agent_threads WHERE thread_id = %s", (thread_id,))
        return {"messages": row["messages"], "pending": row["pending"]} if row else {"messages": [], "pending": None}

    def save_thread(self, thread_id: str, messages: list[dict], pending: dict | None) -> None:
        self._one("""
            INSERT INTO agent_threads (thread_id, messages, pending) VALUES (%s, %s, %s)
            ON CONFLICT (thread_id) DO UPDATE SET messages = EXCLUDED.messages, pending = EXCLUDED.pending,
                                                  updated_at = now()""",
                  (thread_id, Jsonb(messages[-THREAD_MESSAGE_LIMIT:]), Jsonb(pending) if pending else None))

    # --- assets ----------------------------------------------------------------

    def add_asset(self, filename: str, data: bytes) -> Asset:
        if len(data) > MAX_UPLOAD_BYTES:
            raise AssetError(f"{filename} is larger than {MAX_UPLOAD_BYTES // 2**20} MB")
        try:
            im = Image.open(io.BytesIO(data))
            fmt = im.format
            im.load()
        except Image.DecompressionBombError:
            raise AssetError(f"{filename} is too large to process") from None
        except Exception:
            raise AssetError(f"{filename} is not an image we can read (JPEG, PNG, WebP, GIF or TIFF)") from None
        if fmt not in ALLOWED_FORMATS:
            raise AssetError(f"{filename}: {fmt} images are not supported yet")
        taken_at = _exif_date(im)
        im = ImageOps.exif_transpose(im)
        asset_id = new_id("img")
        ext = {"JPEG": "jpg", "MPO": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "TIFF": "tif", "BMP": "bmp"}[fmt]
        self.blobs.put(f"assets/{asset_id}/original.{ext}", data, CONTENT_TYPES[ext])
        rgb = _flatten(im)
        for name, size in VARIANTS.items():
            v = rgb.copy()
            v.thumbnail((size, size), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            v.save(buf, "JPEG", quality=84, optimize=True, progressive=True)
            self.blobs.put(f"assets/{asset_id}/{name}.jpg", buf.getvalue(), "image/jpeg")
        asset = Asset(asset_id, filename, im.width, im.height, taken_at, "", _focus(rgb, asset_id))
        self._one("INSERT INTO assets (id, filename, width, height, taken_at, ext, focus) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (asset.id, filename, asset.width, asset.height, taken_at, ext, Jsonb(asset.focus) if asset.focus else None))
        return asset

    def get_assets(self, ids: list[str] | set[str]) -> dict[str, Asset]:
        if not ids:
            return {}
        rows = self._all("SELECT id, filename, width, height, taken_at, description, focus FROM assets WHERE id = ANY(%s)",
                         (list(ids),))
        return {r["id"]: Asset(**r) for r in rows}

    def backfill_focus(self) -> int:
        """Find the faces in photos uploaded before face detection existed (runs once, in the background)."""
        done = 0
        for row in self._all("SELECT id FROM assets WHERE focus IS NULL"):
            found = self.asset_bytes(row["id"], "preview")
            spot = _focus(Image.open(io.BytesIO(found[0])), row["id"]) if found else {"faces": 0}
            if spot:
                self._one("UPDATE assets SET focus = %s WHERE id = %s", (Jsonb(spot), row["id"]))
                done += 1
        return done

    def set_asset_description(self, asset_id: str, description: str) -> None:
        self._one("UPDATE assets SET description = %s WHERE id = %s", (description, asset_id))

    def asset_bytes(self, asset_id: str, variant: str) -> tuple[bytes, str] | None:
        """(file bytes, content type) for 'original', 'preview' or 'thumb'; None if missing."""
        if variant == "original":
            row = self._one("SELECT ext FROM assets WHERE id = %s", (asset_id,))
            if not row:
                return None
            key, ctype = f"assets/{asset_id}/original.{row['ext']}", CONTENT_TYPES[row["ext"]]
        elif variant in VARIANTS:
            key, ctype = f"assets/{asset_id}/{variant}.jpg", "image/jpeg"
        else:
            return None
        data = self.blobs.get(key)
        return (data, ctype) if data is not None else None

    def load_original(self, asset_id: str) -> Image.Image | None:
        found = self.asset_bytes(asset_id, "original")
        if found is None:
            return None
        try:
            return _flatten(ImageOps.exif_transpose(Image.open(io.BytesIO(found[0]))))
        except Exception:
            return None

    def delete_asset(self, asset_id: str) -> None:
        self._one("DELETE FROM assets WHERE id = %s", (asset_id,))
        self.blobs.delete_prefix(f"assets/{asset_id}/")

    # --- exports ---------------------------------------------------------------

    def put_export(self, name: str, data: bytes) -> None:
        self.blobs.put(f"exports/{name}", data, "application/pdf")

    def get_export(self, name: str) -> bytes | None:
        return self.blobs.get(f"exports/{name}")


def _focus(im: Image.Image, asset_id: str) -> dict | None:
    """faces.focus, but a photo is never refused because detection failed (None = try again later)."""
    try:
        return faces.focus(im)
    except Exception:
        log.exception("face detection failed for %s", asset_id)
        return None


def _flatten(im: Image.Image) -> Image.Image:
    """RGB copy with transparency composited on white (print has no alpha)."""
    im.seek(0) if hasattr(im, "seek") else None
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im.convert("RGB")


def _exif_date(im: Image.Image) -> str | None:
    try:
        exif = im.getexif()
        raw = exif.get_ifd(ExifTags.IFD.Exif).get(ExifTags.Base.DateTimeOriginal) or exif.get(ExifTags.Base.DateTime)
        return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S").isoformat() if raw else None
    except Exception:
        return None


def default_blobs(bucket: str | None = None) -> Blobs:
    env = os.environ.get
    return Blobs(env("S3_ENDPOINT", "http://localhost:9000"), env("S3_ACCESS_KEY", "rustfsadmin"),
                 env("S3_SECRET_KEY", "rustfsadmin"), bucket or env("S3_BUCKET", "memory-book"))


def default_store() -> Store:
    url = os.environ.get("DATABASE_URL", "postgresql://memory_book:memory_book@localhost:5432/memory_book")
    return Store(url, default_blobs())
