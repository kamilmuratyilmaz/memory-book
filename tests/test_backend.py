import io
import json

import litellm
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader

from memory_book import ai, design, pipeline
from memory_book.ai import (
    AIError,
    ChapterPlan,
    LLMProvider,
    GenerationInput,
    LocalProvider,
    MemoryNote,
    OpApplyTemplate,
    OpDeletePage,
    OpMovePage,
    OpMovePhoto,
    OpSetText,
    OpSetTheme,
    RevisionPlan,
    StoryPlan,
)
from memory_book.app import create_app
from memory_book.layout import PageContent, TimelineItem, apply_template, extract_content, layout_pages, materialize
from memory_book.model import PAGE_SIZES, Box, ImageCrop, ImageElement, MemoryBook, MemoryPage, TextElement, crop_rect
from memory_book.pdf import render_pdf
from conftest import jpeg
from memory_book.storage import AssetError, ConflictError

MM_TO_PT = 72 / 25.4


def make_book(store, photos, **kw) -> MemoryBook:
    inp = GenerationInput(story="We walked along the harbour at dawn. \"The sea smelled like salt and summer.\" "
                                "Later we ate grilled sardines and laughed until midnight.",
                          location="Lisbon", date="2024-06-12", photoIds=[p.id for p in photos], **kw)
    return pipeline.generate_book(store, LocalProvider(), inp)


# --- document model ----------------------------------------------------------------

def test_book_round_trip_and_page_ops(store, photos):
    book = make_book(store, photos)
    again = MemoryBook.model_validate_json(book.model_dump_json())
    assert again == book
    assert book.pages[0].kind == "cover"
    page = MemoryPage(elements=[TextElement(box=Box(x=10, y=10, w=50, h=20), text="hi")])
    book.pages.insert(1, page)
    assert book.pages[1].id == page.id
    book.pages.remove(page)
    book.pages.append(page)
    book.pages[-1].elements.append(ImageElement(box=Box(x=0, y=0, w=10, h=10), assetId=photos[0].id))
    book.pages[-1].elements[0].text = "changed"
    del book.pages[-1].elements[1]
    restored = MemoryBook.model_validate(book.model_dump())
    assert restored.pages[-1].elements[0].text == "changed" and len(restored.pages[-1].elements) == 1


def test_invalid_documents_rejected():
    with pytest.raises(Exception):
        MemoryBook.model_validate({"pageSize": "Letter"})
    with pytest.raises(Exception):
        MemoryPage.model_validate({"elements": [{"type": "video", "box": {"x": 0, "y": 0, "w": 1, "h": 1}}]})
    with pytest.raises(Exception):
        ImageCrop(zoom=0.5)


def test_crop_rect_cover_fit_and_focal_clamp():
    assert crop_rect(2000, 1000, 100, 100, ImageCrop()) == (500, 0, 1000, 1000)
    sx, sy, sw, sh = crop_rect(2000, 1000, 100, 100, ImageCrop(x=0, zoom=2))
    assert (sx, sw, sh) == (0, 500, 500)  # clamped to the left edge
    assert crop_rect(1000, 3000, 100, 50, ImageCrop(y=1))[1] == 3000 - 500


# --- layout -----------------------------------------------------------------------------

def test_templates_produce_editable_elements_inside_page(store, photos):
    book = MemoryBook()
    aspects = {p.id: p.aspect for p in photos}
    w, h = book.page_dims()
    from memory_book.layout import TEMPLATES
    for tid, tpl in TEMPLATES.items():
        c = PageContent(template=tid, title="T", subtitle="S", heading="H", body="Body text.", quote="Q", caption="C",
                        date="May", location="Rome", chapterLabel="Chapter One",
                        items=[TimelineItem(label="9:00", text="Breakfast")],
                        photoIds=[p.id for p in photos[: max(tpl["photos"], 1)]])
        page, _ = materialize(book, c, aspects)
        assert page.elements, tid
        for e in page.elements:
            assert -0.01 <= e.box.x and e.box.x + e.box.w <= w + 0.01, (tid, e)
            assert -0.01 <= e.box.y and e.box.y + e.box.h <= h + 0.01, (tid, e)


def test_long_text_flows_to_continuation_pages():
    book = MemoryBook()
    pages = layout_pages(book, [PageContent(template="minimal-text", body="A sentence that goes on. " * 600)], {})
    assert len(pages) > 2
    words = sum(len(e.text.split()) for p in pages for e in p.elements if e.type == "text")
    assert words == 600 * 5  # nothing lost


def test_apply_template_never_loses_content(store, photos):
    book = MemoryBook()
    aspects = {p.id: p.aspect for p in photos}
    page, _ = materialize(book, PageContent(template="four-grid", photoIds=[p.id for p in photos[:4]], caption="x"), aspects)
    page.elements.append(TextElement(box=Box(x=10, y=10, w=80, h=10), text="My own note"))
    pages = apply_template(book, page, "photo-text", aspects)
    assert pages[0].id == page.id
    photos_after = [e.assetId for p in pages for e in p.elements if e.type == "image" and e.assetId]
    assert sorted(photos_after) == sorted(p.id for p in photos[:4])
    assert "My own note" in " ".join(e.text for p in pages for e in p.elements if e.type == "text")


def test_extract_content_round_trip():
    book = MemoryBook()
    page, _ = materialize(book, PageContent(template="timeline", heading="Day one",
                                            items=[TimelineItem(label="8am", text="Coffee")]), {})
    c = extract_content(page)
    assert c.heading == "Day one" and c.items[0].text == "Coffee"


# --- AI generation ---------------------------------------------------------------------

def test_local_generation_uses_all_photos_and_valid_layout(store, photos):
    book = make_book(store, photos)
    used = [e.assetId for p in book.pages for e in p.elements if e.type == "image" and e.assetId]
    assert set(used) == {p.id for p in photos}
    assert any(e.type == "text" and "sardines" in e.text for p in book.pages for e in p.elements)
    assert book.pages[0].kind == "cover" and book.title == "Lisbon"
    assert book.metadata.notice


def test_generation_requires_some_input(store):
    with pytest.raises(ValueError):
        pipeline.generate_book(store, LocalProvider(), GenerationInput())


class FakeProvider:
    name = "fake"

    def __init__(self, plan=None, fail=0):
        self.plan, self.fail, self.calls = plan, fail, 0

    def analyze(self, inp, photos, store):
        self.calls += 1
        if self.fail:
            self.fail -= 1
            raise AIError("boom")
        return LocalProvider().analyze(inp, photos, store)

    def plan_story(self, inp, analysis, photos):
        return self.plan or LocalProvider().plan_story(inp, analysis, photos)

    def revise(self, summary, instruction, scope, history=(), emit=None):
        self.history = history
        return self.plan


def test_ai_structured_plan_is_sanitized(store, photos):
    bogus = StoryPlan(title="", themeId="does-not-exist", coverPhotoId="nope", chapters=[
        ChapterPlan(title="One", pages=[
            PageContent(template="four-grid", photoIds=[photos[0].id, "ghost", photos[0].id]),  # dupes + unknown
            PageContent(template="photo-text"),  # empty page dropped
        ]),
        ChapterPlan(title="Empty", pages=[]),
    ])
    book = pipeline.generate_book(store, FakeProvider(plan=bogus), GenerationInput(
        title="Trip", photoIds=[p.id for p in photos]))
    assert book.themeId == "editorial" and book.title == "Trip"
    assert len(book.chapters) == 1
    imgs = [e.assetId for p in book.pages[1:] for e in p.elements if e.type == "image"]
    assert imgs == [photos[0].id]  # mismatched template corrected to a 1-photo layout
    assert book.pages[1].template in ("full-photo", "photo-text")


def test_ai_failure_retries_then_falls_back(store, photos):
    fake = FakeProvider(fail=1)
    book = pipeline.generate_book(store, fake, GenerationInput(story="Hello.", photoIds=[photos[0].id]))
    assert fake.calls == 2 and not book.metadata.notice  # recovered on retry
    fake = FakeProvider(fail=5)
    book = pipeline.generate_book(store, fake, GenerationInput(story="Hello.", photoIds=[photos[0].id]))
    assert "unavailable" in book.metadata.notice and book.pages


MODEL = "gemini/gemini-3.8-flash"


def _llm(response_json: str | None = None, finish_reason: str | None = None, exc: Exception | None = None):
    """An LLMProvider on LiteLLM's real code path: mock_response yields genuine LiteLLM response objects."""
    provider = LLMProvider(MODEL, mock_response=response_json)
    real = provider.completion

    def completion(**kw):
        if exc:
            raise exc
        response = real(**kw)
        if finish_reason:
            response.choices[0].finish_reason = finish_reason
        return response
    provider.completion = completion
    return provider


def test_llm_output_validation():
    good = ai.MemoryAnalysis(suggestedTitle="x").model_dump_json()
    assert _llm(good)._call("hi", ai.MemoryAnalysis).suggestedTitle == "x"
    for bad in (_llm("not json"),                                   # malformed output
                _llm('{"photos": [{"importance": "high"}]}'),        # valid JSON, wrong schema
                _llm(good, finish_reason="length"),                  # partial output
                _llm(good, finish_reason="content_filter"),          # refusal
                _llm(exc=litellm.BadRequestError("bad request", MODEL, "gemini")),
                _llm(exc=litellm.APIConnectionError("connection reset", "gemini", MODEL))):
        with pytest.raises(AIError):
            bad._call("hi", ai.MemoryAnalysis)


def test_llm_request_options(monkeypatch):
    monkeypatch.setenv("MEMORY_BOOK_FALLBACK_MODELS", "openai/gpt-5, anthropic/claude-opus-5-5")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "wrkspc_test")
    monkeypatch.setenv("MEMORY_BOOK_EFFORT", "low")
    gemini = LLMProvider(MODEL)._request("hi", ai.RevisionPlan, stream=True)
    assert gemini["fallbacks"] == ["openai/gpt-5", "anthropic/claude-opus-5-5"]
    fmt = gemini["response_format"]["json_schema"]
    assert fmt["name"] == "RevisionPlan" and fmt["strict"] and gemini["reasoning_effort"] == "low"
    assert gemini["stream_options"] == {"include_usage": True} and "extra_headers" not in gemini
    assert gemini["thinking"] == {"type": "enabled", "budget_tokens": 1024}  # Gemini: reasoning shown in the chat
    claude = LLMProvider("anthropic/claude-opus-5-5")._request("hi", ai.RevisionPlan, stream=False)
    assert claude["extra_headers"] == {"anthropic-workspace-id": "wrkspc_test"} and "stream" not in claude
    assert "thinking" not in claude  # other providers: MEMORY_BOOK_EFFORT / MEMORY_BOOK_THINKING_BUDGET only


def test_response_format_requires_every_field():
    """Gemini skips fields that are not required; every property must be required, theme ids constrained."""
    js = ai.response_format(ai.StoryPlan)["json_schema"]["schema"]
    objects = [js, *js.get("$defs", {}).values()]
    for obj in objects:
        if obj.get("type") == "object":
            assert set(obj["required"]) == set(obj["properties"]) and obj["additionalProperties"] is False
    assert js["properties"]["themeId"]["enum"] == sorted(design.themes())
    assert "default" not in str(js)
    ai.StoryPlan.model_validate({"title": "t"})  # our own validation still accepts omitted optional fields


def test_empty_ai_results_count_as_failures(store, photos):
    empty = ai.MemoryAnalysis(suggestedTitle="x").model_dump_json()
    with pytest.raises(AIError):
        _llm(empty).analyze(GenerationInput(story="Hi."), photos, store)
    with pytest.raises(AIError):
        _llm(ai.StoryPlan(title="t").model_dump_json()).plan_story(GenerationInput(story="Hi."), ai.MemoryAnalysis(), photos)


def test_default_provider_follows_configured_model(monkeypatch):
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "MEMORY_BOOK_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert isinstance(ai.default_provider(), LocalProvider)  # default Gemini model, no key: offline
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    p = ai.default_provider()
    assert isinstance(p, LLMProvider) and p.model == ai.DEFAULT_MODEL == MODEL
    monkeypatch.setenv("MEMORY_BOOK_MODEL", "no-such-provider/model")
    assert isinstance(ai.default_provider(), LocalProvider)


# --- revisions ----------------------------------------------------------------------------

def test_revision_ops_are_targeted_and_validated(store, photos):
    book = make_book(store, photos)
    text_el = next(e for p in book.pages for e in p.elements if e.type == "text" and e.role == "body")
    photo_page = next(p for p in book.pages[1:] if any(e.type == "image" for e in p.elements))
    other = next(p for p in book.pages[1:] if p.id != photo_page.id and any(e.type == "image" for e in p.elements))
    moved = next(e.assetId for e in photo_page.elements if e.type == "image")
    plan = RevisionPlan(summary="ok", operations=[
        OpSetText(op="set_text", elementId=text_el.id, text="New words."),
        OpSetTheme(op="set_theme", themeId="vintage"),
        OpSetText(op="set_text", elementId="missing", text="x"),
        OpDeletePage(op="delete_page", pageId=book.pages[0].id),  # cover may not be deleted
        OpMovePhoto(op="move_photo", assetId=moved, fromPageId=photo_page.id, toPageId=other.id),
        OpMovePage(op="move_page", pageId=book.pages[-1].id, toIndex=1),
        OpApplyTemplate(op="apply_template", pageId=other.id, template="minimal-text"),
    ])
    new, skipped = pipeline.apply_revision(book, plan, store)
    assert len(skipped) == 2
    assert new.themeId == "vintage" and new.pages[0].kind == "cover"
    assert any(e.type == "text" and e.text == "New words." for p in new.pages for e in p.elements)
    assert new.pages[1].id == book.pages[-1].id
    assert book.themeId != "vintage"  # original untouched (undo-able)
    assert moved in {e.assetId for p in new.pages for e in p.elements if e.type == "image"}


def test_local_revision_rules(store, photos):
    book = make_book(store, photos)
    for instruction, check in [
        ("Make the whole book feel more vintage", lambda b: b.themeId == "vintage"),
        ("Turn this into a 4-page book", lambda b: len(b.pages) <= 4),
        ("Use fewer words", lambda b: True),
        ("Make this page minimalist", lambda b: True),
    ]:
        plan = pipeline.plan_revision(book, instruction, {"pageId": book.pages[2].id}, LocalProvider(), store)
        new, _ = pipeline.apply_revision(book, plan, store)
        assert check(new), instruction
        assert plan.summary


# --- storage ---------------------------------------------------------------------------------

def test_conflict_detection(store, photos):
    book = make_book(store, photos)
    store.create_book(book)
    v2 = store.save_book(book, 1)
    with pytest.raises(ConflictError) as err:
        store.save_book(book, 1)
    assert err.value.server_version == v2
    assert store.save_book(book, None) == v2 + 1  # explicit overwrite
    with pytest.raises(KeyError):
        store.save_book(MemoryBook(), 1)
    listed = store.list_books()
    assert listed[0]["id"] == book.id and listed[0]["pageCount"] == len(book.pages) and listed[0]["coverAssetId"]
    store.delete_book(book.id)
    assert store.get_book(book.id) is None


def test_assets_bad_and_huge(store):
    with pytest.raises(AssetError):
        store.add_asset("notes.txt", b"hello")
    big = store.add_asset("pano.jpg", jpeg(9000, 1200))
    assert (big.width, big.height) == (9000, 1200)
    thumb, ctype = store.asset_bytes(big.id, "thumb")
    assert Image.open(io.BytesIO(thumb)).width == 480 and ctype == "image/jpeg"
    assert store.asset_bytes(big.id, "original")[1] == "image/jpeg"
    assert store.asset_bytes(big.id, "nonsense") is None
    store.delete_asset(big.id)
    assert store.load_original(big.id) is None


# --- PDF ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("size,orientation", [("A5", "portrait"), ("A4", "portrait"), ("Square", "portrait"),
                                              ("A5", "landscape")])
def test_pdf_geometry_and_content(store, photos, size, orientation):
    book = make_book(store, photos, pageSize=size, orientation=orientation)
    buf = io.BytesIO()
    warnings = render_pdf(book, store, buf)
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    assert len(reader.pages) == len(book.pages)
    w, h = PAGE_SIZES[size]
    if orientation == "landscape":
        w, h = h, w
    for page in reader.pages:
        assert abs(float(page.mediabox.width) - w * MM_TO_PT) < 0.5
        assert abs(float(page.mediabox.height) - h * MM_TO_PT) < 0.5
    first_texts = [e.text for e in book.pages[0].elements if e.type == "text" and e.text]
    assert first_texts[0].split()[0] in reader.pages[0].extract_text()
    assert "/Image" in str(reader.pages[0]["/Resources"]["/XObject"].get_object().values()) or reader.pages[0].images
    fonts = {f for p in reader.pages for f in p["/Resources"].get("/Font", {}).values()}
    assert fonts  # fonts embedded
    assert not [w for w in warnings if "missing" in w]


def test_pdf_page_order_bleed_and_edge_cases(store, photos):
    book = make_book(store, photos)
    for i, p in enumerate(book.pages[1:], start=1):
        p.elements.append(TextElement(box=Box(x=5, y=5, w=40, h=8), text=f"MARK{i:03d}"))
    book.pages.append(MemoryPage())  # empty page
    book.pages.append(MemoryPage(elements=[ImageElement(box=Box(x=10, y=10, w=50, h=50), assetId="img_deleted")]))
    book.pages.append(MemoryPage(elements=[TextElement(box=Box(x=10, y=10, w=60, h=20), text="long " * 2000)]))
    buf = io.BytesIO()
    warnings = render_pdf(book, store, buf, bleed=3)
    reader = PdfReader(io.BytesIO(buf.getvalue()))
    assert len(reader.pages) == len(book.pages)
    for i in range(1, len(book.pages) - 3):
        assert f"MARK{i:03d}" in reader.pages[i].extract_text()
    assert abs(float(reader.pages[0].mediabox.width) - (148 + 6) * MM_TO_PT) < 0.5
    assert abs(float(reader.pages[0].trimbox.width) - 148 * MM_TO_PT) < 0.5
    assert any("missing" in w for w in warnings) and any("reduced" in w for w in warnings)


def test_large_book_and_single_page(store, photos):
    book = make_book(store, photos)
    book.pages = book.pages + [p.model_copy(deep=True) for p in book.pages[1:]] * 10
    assert len(book.pages) > 50
    buf = io.BytesIO()
    render_pdf(book, store, buf)
    assert len(PdfReader(io.BytesIO(buf.getvalue())).pages) == len(book.pages)
    one = MemoryBook(pages=[book.pages[0]])
    buf = io.BytesIO()
    render_pdf(one, store, buf)
    assert len(PdfReader(io.BytesIO(buf.getvalue())).pages) == 1


def test_text_only_book(store):
    book = pipeline.generate_book(store, LocalProvider(), GenerationInput(
        title="Letters", notes=[MemoryNote(text="First letter. " * 50), MemoryNote(text="Second letter.")]))
    assert not book.asset_ids() and book.pages[0].template == "cover-text"
    buf = io.BytesIO()
    render_pdf(book, store, buf)
    assert len(PdfReader(io.BytesIO(buf.getvalue())).pages) == len(book.pages)


# --- API ------------------------------------------------------------------------------------------

def test_api_flow(store):
    client = TestClient(create_app(store, LocalProvider()))
    up = client.post("/api/assets", files=[("files", ("a.jpg", jpeg(1200, 800), "image/jpeg")),
                                           ("files", ("bad.txt", b"nope", "text/plain"))]).json()
    assert len(up["assets"]) == 1 and len(up["errors"]) == 1
    aid = up["assets"][0]["id"]
    assert client.get(f"/api/assets/{aid}/thumb").status_code == 200
    job = client.post("/api/books/generate", json={"story": "A lovely day.", "photoIds": [aid]}).json()
    import time
    for _ in range(100):
        state = client.get(f"/api/jobs/{job['jobId']}").json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    book_id = state["result"]["bookId"]
    data = client.get(f"/api/books/{book_id}").json()
    assert aid in data["assets"]
    book = data["book"]
    book["title"] = "Renamed Kaş"
    assert client.put(f"/api/books/{book_id}", json={"book": book, "version": 1}).json()["version"] == 2
    assert client.put(f"/api/books/{book_id}", json={"book": book, "version": 1}).status_code == 409
    laid = client.post("/api/layout", json={"book": book, "page": book["pages"][1], "template": "four-grid"}).json()
    assert laid["pages"][0]["id"] == book["pages"][1]["id"]
    assert client.post("/api/layout", json={"book": book, "page": book["pages"][0], "template": "quote"}).status_code == 422
    run = client.post("/api/agent", json={
        "threadId": "t1", "runId": "r1", "state": {"book": book}, "tools": [], "context": [], "forwardedProps": {},
        "messages": [{"id": "m1", "role": "user", "content": "make it feel vintage"}]})
    assert run.headers["content-type"].startswith("text/event-stream")
    events = [json.loads(line[6:]) for line in run.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "RUN_STARTED" and events[-1]["type"] == "RUN_FINISHED"
    assert any(e["type"] == "STATE_DELTA" and e["delta"][0]["value"] == "vintage" for e in events)
    job = client.post(f"/api/books/{book_id}/export", json={"bleed": False}).json()
    for _ in range(100):
        state = client.get(f"/api/jobs/{job['jobId']}").json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    pdf = client.get(f"/api/exports/{state['result']['file']}")
    assert pdf.headers["content-type"] == "application/pdf" and "Renamed" in pdf.headers["content-disposition"]
    assert pdf.content.startswith(b"%PDF")
    assert client.get("/api/exports/..%2Fmemory_book.db").status_code == 404
    assert client.get(f"/api/assets/{aid}/original").content == jpeg(1200, 800)  # originals are kept byte-for-byte


def test_parity_fixture_matches_python():
    """web/src/model.test.ts checks the same fixture, so TS and Python crop/style math cannot drift apart."""
    import json
    from pathlib import Path

    from memory_book import design
    from memory_book.model import TextStyle
    data = json.loads((Path(__file__).parent / "fixtures" / "parity.json").read_text())
    for case in data["crops"]:
        assert list(crop_rect(*case["img"], *case["box"], ImageCrop(**case["crop"]))) == pytest.approx(case["rect"])
    for case in data["texts"]:
        rt = design.resolve_text(design.theme(case["theme"]), case["role"], TextStyle(**case["style"]), case["scale"])
        assert rt.font_size == pytest.approx(case["resolved"]["fontSize"]) and rt.color == case["resolved"]["color"]
