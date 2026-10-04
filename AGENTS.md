# AGENTS.md

Guidance for AI coding agents (and humans) working in this repository. Read it before you change code.

## What this is

Memory Book turns photos and a few notes into a finished photo book. The AI writes the story and lays out the pages;
the user refines it in an editor, reads it with a 3D page turn, edits it by chat (AG-UI), and exports a print PDF.
Everything works on **one document model** (`MemoryBook`, millimetres and points): the editor, the reader and the PDF
must always show the same page.

## Repository map

| Path | What lives there |
|---|---|
| `src/memory_book/model.py` | `MemoryBook` schema (Pydantic) and `crop_rect` |
| `src/memory_book/layout.py` | 17 data-driven templates, `materialize`, `apply_template`, `photo_crop` |
| `src/memory_book/ai.py` | LiteLLM provider, offline `LocalProvider`, strict JSON schemas, the 12 revision operation types |
| `src/memory_book/pipeline.py` | Book generation stages, `plan_revision`, `apply_revision` |
| `src/memory_book/agui.py` | AG-UI 1.0 agent: event stream, approval interrupts, JSON-Patch state deltas |
| `src/memory_book/faces.py` | YuNet face detection → photo `focus` for face-aware crops |
| `src/memory_book/storage.py` | PostgreSQL (psycopg pool) + RustFS/S3 (boto3), schema, image variants, threads |
| `src/memory_book/pdf.py` | Deterministic reportlab renderer |
| `src/memory_book/tracing.py` | MLflow traces, sessions, token usage and cost |
| `src/memory_book/app.py` | FastAPI routes, background jobs, SPA serving |
| `web/src/model.ts` | TypeScript mirror of the document model and its maths |
| `web/src/render/` | Page renderer shared by editor, thumbnails and reader (`page.css` = print geometry) |
| `web/src/editor/` | Editor, canvas, inspector, page list, chat panel (AG-UI client), undo history, autosave |
| `web/src/reader/` | Reader: spreads, page turn, full screen |
| `tests/` | pytest suites; `tests/fixtures/parity.json` is shared with the web tests |
| `docs/` | `memory-book-report.html` (system description), `ag-ui-guide.html` (chat editing guide) |

## Commands

```bash
docker compose up -d db rustfs mlflow     # services the server and the Python tests need
uv sync                                   # Python deps (Python 3.13, uv)
uv run memory-book                        # API + built web app on http://127.0.0.1:8000
(cd web && npm install && npm run dev)    # Vite dev server on :5173, proxies /api and /static
docker compose up -d --build              # whole stack; app on :8000 (and :8080 for phones on the LAN)
```

Checks — run all of them before you call a change done:

```bash
uv run pytest                             # needs the compose services; throwaway schema + bucket per test
uvx ruff check src tests                  # line length 120
cd web && npx tsc -b && npm run lint && npm test && npm run build
```

## Rules that keep the system correct

**One model, two languages.** `model.py` ↔ `model.ts`, `crop_rect` ↔ `cropRect`, `layout.photo_crop` ↔ `photoCrop`.
Change both sides in the same commit. If the maths changes, update `tests/fixtures/parity.json`; both test suites
read it.

**Print fidelity.** `web/src/render/page.css` is plain CSS on purpose: it reproduces the PDF geometry (mm boxes, no
kerning, no ligatures, the same TTF files as `pdf.py`). Do not restyle `.mb-*` classes with Tailwind utilities.

**AI calls.** Every model call goes through LiteLLM with a strict JSON schema (`ai.response_format`: all fields
required, enums for ids). Validate and sanitise every result; an empty result is a failure. `LocalProvider` (offline)
must keep working — the app and most tests depend on it.

**Chat editing (AG-UI).**
- The model can only return the typed operations in `ai.py`; it never runs code or fetches URLs.
- A resume applies **only the plan stored on the server**, and only on an explicit `{"approved": true}`.
- `needs_approval` counts element edits by their page. Keep that when you add an operation type.
- Events that carry mutable data (activity snapshots) are sent as copies: the stream is encoded on another thread.

**Storage.** The schema is idempotent SQL in `storage.SCHEMA` (`CREATE … IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`),
run at start-up. There is no migration tool. Saves use optimistic concurrency (`UPDATE … WHERE version = …`, 409 on
conflict). Originals are never modified.

**Faces.** Detection runs once at upload; a failed detection must never block an upload (it is retried at the next
server start).

## Security and secrets

- **Never read, print or commit `.env`.** It holds real provider keys. Check only that a variable name exists.
  `docker compose config` without `--quiet` prints secrets — do not run it.
- LiteLLM is pinned exactly (`litellm==1.104.0`). Versions 1.82.7 and 1.82.8 were malicious (MAL-2026-2144). Never
  loosen the pin without checking OSV.
- New dependencies: pin the newest release after an advisory check (OSV); no "wait for it to age" holdback.
- The app has **no authentication**. Do not expose it beyond a trusted network. The compose file publishes the app on
  `:8000` and `:8080` (LAN phone testing) — remove `:8080` for anything shared.
- Tests must never call a real model: use `litellm.mock_response`, `FakeProvider` or `LocalProvider`.

## Frontend conventions

- React 19 + TypeScript + Tailwind CSS 4, CSS-first: theme tokens in `web/src/index.css` (`@theme`); repeated controls
  as small component classes there (`.btn`, `.input`, `.segment`, …); everything else as utilities in the markup.
- Small screens: the editor's page list and inspector are bottom sheets (`max-lg:`). Check 390×844 and 844×390.
- Reader: spread mode whenever two pages fit side by side (`Reader.tsx`); full screen uses the Fullscreen API, with an
  immersive fallback and a home-screen manifest for iPhone.

## Tests

- Python: `tests/conftest.py` gives each test its own Postgres schema and RustFS bucket and removes them after.
- Non-trivial logic gets one small test that fails if the logic breaks. Mock the detector, the model or the network;
  never depend on personal data.
- The web suite covers history/undo, document operations, autosave, reader spreads and TS↔Python parity.

## Documentation

- `docs/*.html` are written in **ASD-STE100 Simplified Technical English** (short sentences, active voice,
  imperative procedures, NOTE / CAUTION / WARNING callouts).
- Each document has an issue number and a change record. Current: Issue 1. When the content changes, add a row and
  raise the issue number.
- Every numbered `h2`/`h3` has an `id` and an entry in the table of contents.
- Diagrams are inline SVG that follow the light/dark theme tokens; no external scripts.

## Commits

Imperative subject line, a body that says what changed and why. Run the checks first. Do not commit `.env`, build
output (`web/dist`), `.venv`, `node_modules` or `.playwright-mcp`.
