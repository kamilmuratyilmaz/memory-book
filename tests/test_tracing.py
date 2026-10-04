"""MLflow tracing: stage/tool spans, token usage per trace, and one session per book.

Runs against the compose MLflow server (docker compose up -d mlflow), like the Postgres/RustFS tests.
"""

import os
import uuid

import mlflow
import pytest
from ag_ui.core import RunAgentInput

from memory_book import ai, pipeline, tracing
from memory_book.agui import run_agent

MLFLOW_URI = os.environ.get("TEST_MLFLOW_URI", "http://localhost:5000")


@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_URI", MLFLOW_URI)
    monkeypatch.setenv("MLFLOW_EXPERIMENT", f"test-{uuid.uuid4().hex[:8]}")
    if not tracing.init():
        pytest.fail(f"MLflow is not reachable at {MLFLOW_URI} (run: docker compose up -d mlflow)")
    yield
    mlflow.tracing.disable()


def fake_llm(store, photos, inp):
    """An LLMProvider on LiteLLM's real code path (mock_response) that answers with the offline designer's output.
    `provider.usage` collects the usage LiteLLM reported for every call, to compare with MLflow."""
    local = ai.LocalProvider()
    provider = ai.LLMProvider("gemini/gemini-3.8-flash")
    provider.usage = []
    real = provider.completion

    def completion(**kw):
        schema = kw["response_format"]["json_schema"]["name"]
        if schema == "MemoryAnalysis":
            out = local.analyze(inp, photos, store)
        elif schema == "StoryPlan":
            out = local.plan_story(inp, local.analyze(inp, photos, store), photos)
        else:
            out = ai.RevisionPlan(summary="Restyled.", operations=[ai.OpSetTheme(op="set_theme", themeId="vintage")])
        response = real(**kw, mock_response=out.model_dump_json())
        if not kw.get("stream"):
            provider.usage.append(response.usage)
            return response

        def stream():
            for chunk in response:
                if getattr(chunk, "usage", None):
                    provider.usage.append(chunk.usage)
                yield chunk
        return stream()
    provider.completion = completion
    return provider


def get_trace():
    mlflow.flush_trace_async_logging()
    return mlflow.get_trace(mlflow.get_last_active_trace_id())


def test_generation_and_chat_runs_are_traced_per_book_session(traced, store, photos):
    inp = ai.GenerationInput(story="A week by the sea. We swam every morning.", photoIds=[p.id for p in photos])
    provider = fake_llm(store, photos, inp)
    book = pipeline.generate_book(store, provider, inp)
    reported = provider.usage[:]

    gen = get_trace()
    names = [s.name for s in gen.data.spans]
    assert names[0] == "generate_book"
    assert {"understanding", "organizing", "story", "design"} <= set(names)
    assert names.count("llm.MemoryAnalysis") == 1 and names.count("llm.StoryPlan") == 1
    assert gen.info.trace_metadata["mlflow.trace.session"] == f"book-{book.id}"
    usage = gen.info.token_usage  # what LiteLLM reported, summed by MLflow
    assert len(reported) == 2 and usage["input_tokens"] == sum(u.prompt_tokens for u in reported) > 0
    assert usage["output_tokens"] == sum(u.completion_tokens for u in reported) > 0
    llm = next(s for s in gen.data.spans if s.name == "llm.StoryPlan")
    assert llm.span_type == "CHAT_MODEL" and "gemini-3.8-flash" in llm.attributes["served_by"]
    assert llm.attributes["cost_usd"] >= 0
    analysis = next(s for s in gen.data.spans if s.name == "llm.MemoryAnalysis")
    assert "[image_url]" in str(analysis.inputs) and "base64" not in str(analysis.inputs)  # photo bytes never logged

    run = RunAgentInput.model_validate({
        "threadId": f"book-{book.id}", "runId": "r1", "state": {"book": book.model_dump(mode="json")},
        "messages": [{"id": "m", "role": "user", "content": "make it vintage"}], "tools": [], "context": [],
        "forwardedProps": {}})
    events = list(run_agent(run, provider, store))
    assert events[-1].type == "RUN_FINISHED"
    chat = get_trace()
    assert [s.name for s in chat.data.spans][:2] == ["agent_run", "plan_revision"]
    tool = next(s for s in chat.data.spans if s.name == "set_theme")
    assert tool.span_type == "TOOL" and tool.outputs["result"] == "applied"
    assert chat.info.trace_metadata["mlflow.trace.session"] == f"book-{book.id}"  # same session as generation
    assert chat.info.token_usage["total_tokens"] == sum(u.total_tokens for u in provider.usage[len(reported):]) > 0


def test_content_redaction(traced, store, photos, monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACE_CONTENT", "false")
    inp = ai.GenerationInput(story="Private diary entry.", photoIds=[photos[0].id])
    provider = fake_llm(store, photos[:1], inp)
    pipeline.generate_book(store, provider, inp)
    trace = get_trace()
    assert "Private diary" not in str([s.inputs for s in trace.data.spans] + [s.outputs for s in trace.data.spans])
    assert trace.info.token_usage["total_tokens"] == sum(u.total_tokens for u in provider.usage) > 0  # still recorded
