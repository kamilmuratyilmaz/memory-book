"""HTTP API + static hosting for the built web app."""

from __future__ import annotations

import io
import logging
import re
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from ag_ui.core import RunAgentInput
from ag_ui.encoder import EventEncoder
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import design, pipeline, tracing
from .agui import run_agent
from .ai import AIError, AIProvider, GenerationInput, default_provider
from .layout import TEMPLATES, PageContent, apply_template, materialize, template_catalog
from .model import PAGE_SIZES, MemoryBook, MemoryPage
from .pdf import render_pdf
from .storage import AssetError, ConflictError, Store, default_store

log = logging.getLogger(__name__)
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


class SaveRequest(BaseModel):
    book: MemoryBook
    version: int | None  # None = overwrite after a conflict, chosen explicitly by the user


class LayoutRequest(BaseModel):
    book: MemoryBook
    page: MemoryPage | None = None  # re-template this page (keeps its id, text and photos)
    template: str
    content: PageContent | None = None


class ResizeRequest(BaseModel):
    book: MemoryBook
    pageSize: Literal["A5", "A4", "Square"]


class ExportRequest(BaseModel):
    bleed: bool = False


class Jobs:
    """In-process background jobs with user-facing stages."""

    # ponytail: in-memory job table on a small thread pool; move to a queue + worker when running >1 process
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=3)
        self.jobs: dict[str, dict] = {}
        self.lock = threading.Lock()

    def submit(self, kind: str, fn) -> str:
        job_id = uuid.uuid4().hex[:12]
        self.jobs[job_id] = {"id": job_id, "kind": kind, "status": "running", "stage": None, "result": None, "error": None}

        def run():
            try:
                result = fn(lambda stage: self.update(job_id, stage=stage))
                self.update(job_id, status="done", result=result)
            except Exception as e:  # surfaced to the user as a friendly message
                log.error("job %s failed: %s", job_id, traceback.format_exc())
                self.update(job_id, status="error", error=_friendly(e))
        self.pool.submit(run)
        return job_id

    def update(self, job_id: str, **kw) -> None:
        with self.lock:
            self.jobs[job_id].update(kw)

    def get(self, job_id: str) -> dict:
        if job_id not in self.jobs:
            raise HTTPException(404, "This task has expired. Please try again.")
        return self.jobs[job_id]


def _friendly(e: Exception) -> str:
    if isinstance(e, (ValueError, AIError)) and str(e):
        return str(e)
    return "Something went wrong on our side. Your photos and notes are safe — please try again."


def create_app(store: Store | None = None, provider: AIProvider | None = None) -> FastAPI:
    store = store or default_store()
    provider = provider or default_provider()
    tracing.init()
    jobs = Jobs()
    app = FastAPI(title="Memory Book")

    def load(book_id: str) -> tuple[MemoryBook, int]:
        found = store.get_book(book_id)
        if not found:
            raise HTTPException(404, "We couldn't find this book.")
        return found

    def aspects_for(book: MemoryBook) -> dict[str, float]:
        return {k: a.aspect for k, a in store.get_assets(set(book.metadata.assetIds) | book.asset_ids()).items()}

    @app.get("/api/design")
    def get_design():
        return {**design.design(), "templates": template_catalog(), "pageSizes": PAGE_SIZES, "ai": provider.name}

    # --- assets ----------------------------------------------------------------
    @app.post("/api/assets")
    async def upload(files: list[UploadFile] = File(...)):
        ok, errors = [], []
        for f in files:
            try:
                ok.append(store.add_asset(f.filename or "photo", await f.read()).public())
            except AssetError as e:
                errors.append(str(e))
        return {"assets": ok, "errors": errors}

    @app.post("/api/assets/lookup")
    def lookup(ids: list[str]):
        return {k: a.public() for k, a in store.get_assets(ids).items()}

    @app.get("/api/assets/{asset_id}/{variant}")
    def asset(asset_id: str, variant: Literal["thumb", "preview", "original"]):
        # ponytail: proxied through the app (one hop, no CORS/presigning); redirect to presigned URLs if bandwidth matters
        found = store.asset_bytes(asset_id, variant)
        if not found:
            raise HTTPException(404, "Photo not found")
        return Response(found[0], media_type=found[1], headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.delete("/api/assets/{asset_id}")
    def delete_asset(asset_id: str):
        store.delete_asset(asset_id)
        return {"ok": True}

    # --- books -----------------------------------------------------------------
    @app.get("/api/books")
    def list_books():
        books = store.list_books()
        assets = store.get_assets({b["coverAssetId"] for b in books if b["coverAssetId"]})
        return {"books": books, "assets": {k: a.public() for k, a in assets.items()}}

    @app.post("/api/books/generate")
    def generate(inp: GenerationInput):
        def work(progress):
            book = pipeline.generate_book(store, provider, inp, progress)
            store.create_book(book)
            return {"bookId": book.id, "notice": book.metadata.notice}
        return {"jobId": jobs.submit("generate", work), "stages": pipeline.STAGES}

    @app.post("/api/books")
    def create_empty(book: MemoryBook):
        store.create_book(book)
        return {"id": book.id, "version": 1}

    @app.get("/api/books/{book_id}")
    def get_book(book_id: str):
        book, version = load(book_id)
        return {"book": book, "version": version, "assets": {k: a.public() for k, a in store.get_assets(
            set(book.metadata.assetIds) | book.asset_ids()).items()}}

    @app.put("/api/books/{book_id}")
    def save_book(book_id: str, req: SaveRequest):
        if req.book.id != book_id:
            raise HTTPException(400, "Book id mismatch")
        try:
            return {"version": store.save_book(req.book, req.version)}
        except ConflictError as e:
            return JSONResponse({"conflict": True, "serverVersion": e.server_version}, status_code=409)
        except KeyError:
            raise HTTPException(404, "We couldn't find this book.") from None

    @app.delete("/api/books/{book_id}")
    def delete_book(book_id: str):
        store.delete_book(book_id)
        return {"ok": True}

    @app.post("/api/agent")
    def agent(inp: RunAgentInput):
        """AG-UI endpoint for chat editing: streams run events as server-sent events."""
        encoder = EventEncoder()
        return StreamingResponse((encoder.encode(e) for e in run_agent(inp, provider, store)),
                                 media_type=encoder.get_content_type())

    @app.get("/api/agent/threads/{thread_id}")
    def agent_thread(thread_id: str):
        """The stored chat thread, and the approval request that is still open, if any."""
        thread = store.get_thread(thread_id)
        return {"messages": thread["messages"], "interrupt": (thread["pending"] or {}).get("interrupt")}

    @app.post("/api/layout")
    def layout(req: LayoutRequest):
        """Stateless template application — the same layout engine the AI uses."""
        if req.template not in TEMPLATES:
            raise HTTPException(422, "Unknown layout")
        aspects = aspects_for(req.book)
        if req.page:
            if (req.page.kind == "cover") != bool(TEMPLATES[req.template].get("cover")):
                raise HTTPException(422, "Cover layouts can only be used on the cover.")
            return {"pages": apply_template(req.book, req.page, req.template, aspects)}
        content = (req.content or PageContent()).model_copy(update={"template": req.template})
        return {"pages": [materialize(req.book, content, aspects, placeholders=True)[0]]}

    @app.post("/api/resize")
    def resize(req: ResizeRequest):
        """Change the physical page size. Template pages are re-laid out; free-form pages are scaled."""
        old_w, old_h = req.book.page_dims()
        book = req.book.model_copy(deep=True, update={"pageSize": req.pageSize})
        if req.pageSize == "Square":
            book.orientation = "portrait"
        w, h = book.page_dims()
        aspects = aspects_for(book)
        pages = []
        for page in book.pages:
            if page.template in TEMPLATES:
                pages += apply_template(book, page, page.template, aspects, placeholders=True)
                continue
            for e in page.elements:
                e.box = e.box.model_copy(update={"x": e.box.x * w / old_w, "w": e.box.w * w / old_w,
                                                 "y": e.box.y * h / old_h, "h": e.box.h * h / old_h})
            pages.append(page)
        book.pages = pages
        return {"book": book}

    @app.post("/api/books/{book_id}/export")
    def export(book_id: str, req: ExportRequest):
        book, version = load(book_id)

        def work(progress):
            progress("rendering")
            name = f"{book.id}-v{version}{'-bleed' if req.bleed else ''}.pdf"
            buf = io.BytesIO()
            warnings = render_pdf(book, store, buf, bleed=3.0 if req.bleed else 0.0)
            store.put_export(name, buf.getvalue())
            return {"file": name, "warnings": warnings, "pages": len(book.pages)}
        return {"jobId": jobs.submit("export", work)}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return jobs.get(job_id)

    @app.get("/api/exports/{name}")
    def download(name: str):
        data = store.get_export(name) if re.fullmatch(r"[\w-]+\.pdf", name) else None
        if data is None:
            raise HTTPException(404, "This export has expired. Please export again.")
        title = (store.get_book(name.split("-v")[0]) or (MemoryBook(), 0))[0].title
        safe = "".join(ch for ch in title if ch.isalnum() or ch in " -_").strip() or "memory-book"
        return Response(data, media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename=memory-book.pdf; filename*=UTF-8''{quote(safe)}.pdf"})

    # --- static ----------------------------------------------------------------
    app.mount("/static", StaticFiles(directory=design.STATIC), name="static")
    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="web-assets")

        @app.get("/{path:path}")
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404)
            file = (WEB_DIST / path).resolve()
            if path and file.is_file() and file.is_relative_to(WEB_DIST):
                return FileResponse(file)
            return FileResponse(WEB_DIST / "index.html")
    return app

