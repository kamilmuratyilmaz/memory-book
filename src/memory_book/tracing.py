"""AI observability with MLflow tracing.

Every AI job is one trace: a book generation ("generate_book") or a chat run ("agent_run"). Pipeline stages
and document operations are child spans; each model call is a CHAT_MODEL span carrying token usage, which MLflow
totals per trace. Traces are grouped into sessions — one per book (`book-<id>`), the same id the AG-UI chat uses
as its thread — so the MLflow UI shows all AI work and token spend for a book together.

Tracing is off unless MLFLOW_TRACKING_URI is set; all helpers are then no-ops.
MLFLOW_TRACE_CONTENT=false keeps steps, timings and token counts but redacts users' text and model output.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

import mlflow
from mlflow.entities import SpanType
from mlflow.tracing.constant import SpanAttributeKey

log = logging.getLogger(__name__)

__all__ = ["SpanType", "content", "init", "session_for_book", "set_session", "set_usage", "usage_meter"]

_meter: ContextVar[list[dict] | None] = ContextVar("usage_meter", default=None)


@contextmanager
def usage_meter():
    """Collect the token usage of every model call made inside this block (for AG-UI RUN_FINISHED.usage)."""
    records: list[dict] = []
    token = _meter.set(records)
    try:
        yield records
    finally:
        _meter.reset(token)


def init() -> bool:
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        mlflow.tracing.disable()
        return False
    try:
        mlflow.set_tracking_uri(uri)
        mlflow.set_experiment(os.environ.get("MLFLOW_EXPERIMENT", "memory-book"))
        mlflow.tracing.enable()
        return True
    except Exception as e:  # observability must never take the product down
        log.warning("MLflow tracing disabled: %s", e)
        mlflow.tracing.disable()
        return False


def content(value: Any) -> Any:
    """User text and model output, unless content tracing is switched off."""
    return value if os.environ.get("MLFLOW_TRACE_CONTENT", "true").lower() != "false" else "[redacted]"


def session_for_book(book_id: str) -> str:
    return f"book-{book_id}"


def set_session(session_id: str, **tags: str) -> None:
    if mlflow.get_current_active_span() is None:  # tracing is off: nothing to label
        return
    try:
        mlflow.update_current_trace(session_id=session_id, tags={k: str(v) for k, v in tags.items()})
    except Exception as e:  # no active trace (tracing disabled) or backend hiccup
        log.debug("trace session not set: %s", e)


def set_usage(s, usage: dict | None, model: str = "", provider: str = "") -> None:
    """Record normalised token usage on a span in MLflow's format (summed per trace by MLflow)
    and in the active usage meter, if any. `input_tokens` already includes cached input."""
    if not usage:
        return
    tokens = {k: int(usage.get(k) or 0) for k in
              ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}
    tokens["total_tokens"] = tokens["input_tokens"] + tokens["output_tokens"]
    s.set_attribute(SpanAttributeKey.CHAT_USAGE, tokens)
    if usage.get("cost_usd") is not None:
        s.set_attribute("cost_usd", float(usage["cost_usd"]))
    if (meter := _meter.get()) is not None:
        meter.append({**tokens, "model": model, "provider": provider})
