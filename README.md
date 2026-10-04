# Memory Book

Give it photos and a few words about what happened; the AI writes the story, plans chapters, picks layouts and
builds a complete book. You refine it in a visual editor, read it as an interactive book, and export a
print-ready PDF — all from one structured document.

```
                  MemoryBook (JSON, mm + pt)
                           │
        ┌──────────────────┼──────────────────┐
        ↓                  ↓                  ↓
  Editor (React)    Reader (React)     PDF (reportlab)
        └──── web/src/render/Page.tsx ────┘     src/memory_book/pdf.py
```

## Run it

```bash
docker compose up --build        # app + PostgreSQL + RustFS + MLflow → http://localhost:8000
```

| Service | Purpose | Local URL |
|---|---|---|
| `app` | API + web app | http://localhost:8000 |
| `db` | PostgreSQL 17: books, photo metadata, MLflow tracking data | localhost:5432 |
| `rustfs` | S3-compatible object storage: photos, PDFs, MLflow artifacts | console http://localhost:9001 |
| `mlflow` | AI observability: traces, token usage, sessions | http://localhost:5000 |

Without Docker for the app itself (Postgres and RustFS still from compose):

```bash
docker compose up -d db rustfs mlflow
uv sync
(cd web && npm install && npm run build)
MLFLOW_TRACKING_URI=http://localhost:5000 uv run memory-book   # http://127.0.0.1:8000 (tracing optional)
```

AI: any model [LiteLLM](https://docs.litellm.ai/docs/providers) supports, default `gemini/gemini-3.8-flash`. Put the
provider key (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, ...) in a `.env` file (copy `.env.example`) or
the environment; choose another model with `MEMORY_BOOK_MODEL` (e.g. `anthropic/claude-opus-5-5`, `openai/gpt-5`,
`ollama/llama3`) and optional `MEMORY_BOOK_FALLBACK_MODELS`. LiteLLM is pinned to an exact, advisory-free version
(1.104.0) with hashes in `uv.lock`: versions 1.82.7/1.82.8 were malicious (MAL-2026-2144) — never loosen the pin
without checking OSV. Without a model key the app runs an offline designer that
keeps your own words and does the organisation and layout itself; the UI says which mode is active. `uv run
memory-book` loads `.env` from the working directory and `docker compose` reads it for variable substitution; real
environment variables win, and `.env` is git- and docker-ignored. Tests never load it.

Books (JSONB) and photo metadata live in PostgreSQL (`DATABASE_URL`, defaults to the compose database on
`localhost:5432`). Original photos, previews/thumbnails and exported PDFs live in an S3 bucket on
[RustFS](https://github.com/rustfs/rustfs) (`S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`; defaults
match compose, console at http://localhost:9001). Any S3-compatible store works. The app keeps no state on disk.
Coming from the earlier SQLite version? Import books and photos once with
`uv run python -m memory_book.migrate_sqlite data`.

Development: `uv run memory-book` + `cd web && npm run dev` (Vite on :5173 proxies `/api` and `/static`).

## Tests

```bash
uv run pytest                    # needs `docker compose up -d db rustfs mlflow`; throwaway schema/bucket/experiment per test
cd web && npm test               # history/undo, document ops, autosave, reader spreads, TS↔Python parity
cd web && npx tsc -b && npm run lint
```

## How it fits together

| Layer | Where | Notes |
|---|---|---|
| Document model | `src/memory_book/model.py`, mirrored in `web/src/model.ts` | Pages have explicit physical size (A5/A4/Square, portrait/landscape). Elements: text (role-styled), image (asset + focal-point crop), shape. |
| Themes | `src/memory_book/static/themes.json` | Six themes (Editorial, Minimal, Travel Journal, Scrapbook, Vintage, Romantic): fonts, palette, margins, frames, tone, ornaments. Add a theme by adding an entry. |
| Templates | `src/memory_book/layout.py` | 17 data-driven layouts (slots in fractions of the content box). The same engine serves AI generation, AI revisions and the editor's layout picker; re-templating never drops photos or text — overflow spills onto new pages. |
| AI pipeline | `src/memory_book/ai.py`, `pipeline.py` | Model calls go through LiteLLM with a strict JSON schema per stage (every field required, theme ids as an enum — some providers skip optional fields otherwise); empty results count as failures. `analyze` (understanding) → organise → `plan_story` (chapters, prose, page plans) → sanitise → layout → validated `MemoryBook`. Revisions return a small set of typed operations applied to the document (undoable). Every model output is schema-validated and sanitised; failures retry once, then fall back to the offline designer. |
| Faces | `src/memory_book/faces.py` | Each photo is scanned once at upload with the YuNet face detector (OpenCV, MIT model in `models/`); the centre of the main faces is stored on the asset (`focus`). New layouts, AI revisions and photos added in the editor crop around it; the editor's "Centre on faces" (per photo) and "Centre photos on faces" (Book tab) re-apply it. Photos uploaded earlier are scanned once in the background at server start. |
| Persistence | `src/memory_book/storage.py` | PostgreSQL via a psycopg connection pool; files in RustFS via boto3 (photos are proxied through the API). Versioned saves with optimistic concurrency done atomically in one `UPDATE … WHERE version = …` (409 → user chooses), debounced autosave, local draft recovery after refresh/offline. |
| Chat editing | `src/memory_book/agui.py`, `web/src/editor/ChatPanel.tsx` | [AG-UI](https://docs.ag-ui.com) 1.0 over SSE at `POST /api/agent`. Streams the model's reasoning and reply token by token, a `book-changes` activity checklist, one backend tool call + id-aware JSON-Patch `STATE_DELTA` per document operation, `MESSAGES_SNAPSHOT`, and token usage in `RUN_FINISHED`. Plans that delete pages or touch >3 pages end with an `interrupt`; the user's approve/cancel comes back as `resume`. Threads (and pending plans) live in Postgres (`GET /api/agent/threads/{id}`). One run = one undo step. Full description: `docs/memory-book-report.html`. |
| Observability | `src/memory_book/tracing.py` | MLflow tracing: one trace per generation (`understanding → organizing → story → design`) and per chat run (`plan_revision` + one TOOL span per operation); every model call is a CHAT_MODEL span with token usage (incl. cache reads) and cost, summed per trace. Traces share the session `book-<id>`, so the Sessions view shows all AI work and spend per book. Off unless `MLFLOW_TRACKING_URI` is set; `MLFLOW_TRACE_CONTENT=false` keeps steps and tokens but drops users' text. |
| Web UI | `web/src/` | React + Tailwind CSS 4 (theme tokens in `index.css` `@theme`; the page renderer's print-geometry CSS stays plain in `render/page.css`). Reader: two-page spread with a 3D page turn whenever the screen is wider than two pages (desktop, landscape phones), one page on portrait screens. Full screen uses the Fullscreen API (browser and system bars hidden); iPhone Safari has none, so there the book is full screen only when opened from the home screen (`public/manifest.webmanifest`). |
| PDF | `src/memory_book/pdf.py` | Deterministic vector renderer: embedded TTFs (the same files the browser uses), identical wrapping, crop maths and frames; 300 dpi image resampling; optional 3 mm bleed with TrimBox/BleedBox; preflight warnings (missing photos, low resolution, shrunk text). |

Fidelity notes: browser text uses `font-kerning: none` and no ligatures because the PDF renderer places glyphs
by advance width; vertical metrics are read from each font's hhea/OS-2 tables so baselines match.
`tests/fixtures/parity.json` is checked by both test suites so the TS and Python crop/style maths can't drift.

## License

MIT — see [LICENSE](LICENSE). Bundled third-party files keep their own licenses:

| Files | License |
|---|---|
| `src/memory_book/static/fonts/*.ttf` (Caveat, Cormorant Garamond, EB Garamond, Inter, Josefin Sans, Lora, Playfair Display) | SIL Open Font License 1.1 — texts in `src/memory_book/static/fonts/licenses/` |
| `src/memory_book/models/face_detection_yunet_2023mar.onnx` (YuNet, OpenCV model zoo) | MIT — `src/memory_book/models/LICENSE-yunet` |

