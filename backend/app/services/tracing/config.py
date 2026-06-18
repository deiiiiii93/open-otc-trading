"""Tracing mode resolution and callback-handler composition.

``OPEN_OTC_TRACING`` is the single authority: in ``langsmith``/``both`` mode
the ``LangChainTracer`` is attached explicitly per run (project from
``LANGSMITH_PROJECT``), so the legacy global ``LANGSMITH_TRACING`` /
``LANGCHAIN_TRACING_V2`` env vars should stay false — no double tracing.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TracingMode(str, Enum):
    LOCAL = "local"
    LANGSMITH = "langsmith"
    BOTH = "both"
    OFF = "off"


def resolve_tracing_mode(settings) -> TracingMode:
    raw = (getattr(settings, "tracing_mode", None) or "local").strip().lower()
    try:
        return TracingMode(raw)
    except ValueError:
        logger.warning("Unknown OPEN_OTC_TRACING=%r — falling back to 'local'", raw)
        return TracingMode.LOCAL


def tracing_callbacks(
    settings,
    *,
    thread_id: int | None = None,
    task_id: int | None = None,
    workflow_id: int | None = None,
    message_id: int | None = None,
) -> list[Any]:
    """Callback handlers to attach to one agent run. Never raises."""
    mode = resolve_tracing_mode(settings)
    handlers: list[Any] = []
    if mode in (TracingMode.LOCAL, TracingMode.BOTH):
        try:
            from .store import get_trace_store
            from .tracer import LocalTracer

            handlers.append(
                LocalTracer(
                    get_trace_store(settings),
                    thread_id=thread_id,
                    task_id=task_id,
                    workflow_id=workflow_id,
                    message_id=message_id,
                )
            )
        except Exception:
            logger.warning("Local tracer unavailable — skipping", exc_info=True)
    if mode in (TracingMode.LANGSMITH, TracingMode.BOTH):
        try:
            from langchain_core.tracers.langchain import LangChainTracer

            handlers.append(
                LangChainTracer(
                    project_name=os.environ.get("LANGSMITH_PROJECT")
                    or "open-otc-trading"
                )
            )
        except Exception:
            logger.warning("LangSmith tracer unavailable — skipping", exc_info=True)
    return handlers
