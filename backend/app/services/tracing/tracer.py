"""LocalTracer: LangSmith's architecture pointed at our own store.

Subclasses the same ``BaseTracer`` that LangSmith's ``LangChainTracer`` uses,
so it sees every chain/LLM/tool start+end — including inside subagent graphs,
which ``astream_events`` provably does not surface to the parent. Every hook
is exception-wrapped: tracing must never break an agent run.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tracers.base import BaseTracer
from langchain_core.tracers.schemas import Run

from .store import SpanEnd, SpanStart, TraceStore

logger = logging.getLogger(__name__)


def _json(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return json.dumps(str(obj))


def extract_token_usage(
    outputs: dict[str, Any] | None,
) -> tuple[int | None, int | None, int | None]:
    """Token counts from an LLM run's outputs; handles both payload shapes."""
    if not outputs:
        return (None, None, None)
    llm_output = outputs.get("llm_output") or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if usage:
        return (
            usage.get("prompt_tokens", usage.get("input_tokens")),
            usage.get("completion_tokens", usage.get("output_tokens")),
            usage.get("total_tokens"),
        )
    try:
        message = outputs["generations"][0][0]["message"]
        meta = message["kwargs"]["usage_metadata"]
        return (
            meta.get("input_tokens"),
            meta.get("output_tokens"),
            meta.get("total_tokens"),
        )
    except (KeyError, IndexError, TypeError):
        return (None, None, None)


class LocalTracer(BaseTracer):
    """Persists every span to the local trace store as it starts and ends."""

    name = "open_otc_local_tracer"
    run_inline = True  # keep callback ordering deterministic in async runs

    def __init__(
        self,
        store: TraceStore,
        *,
        thread_id: int | None = None,
        task_id: int | None = None,
        workflow_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._thread_id = thread_id
        self._task_id = task_id
        self._workflow_id = workflow_id
        self._message_id = message_id

    def _persist_run(self, run: Run) -> None:
        """Per-span persistence happens in _on_run_create/_on_run_update."""

    def _on_run_create(self, run: Run) -> None:
        try:
            self._store.enqueue_insert(
                SpanStart(
                    id=str(run.id),
                    trace_id=str(run.trace_id or run.id),
                    parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
                    dotted_order=run.dotted_order or str(run.id),
                    thread_id=self._thread_id,
                    task_id=self._task_id,
                    workflow_id=self._workflow_id,
                    message_id=self._message_id,
                    name=run.name,
                    run_type=run.run_type,
                    start_time=run.start_time.isoformat(),
                    inputs=_json(run.inputs),
                    extra=_json(run.extra),
                )
            )
        except Exception:
            logger.warning("Dropping trace span on create", exc_info=True)

    def _on_run_update(self, run: Run) -> None:
        try:
            prompt_t, completion_t, total_t = (
                extract_token_usage(run.outputs)
                if run.run_type == "llm"
                else (None, None, None)
            )
            self._store.enqueue_finalize(
                SpanEnd(
                    id=str(run.id),
                    trace_id=str(run.trace_id or run.id),
                    parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
                    end_time=run.end_time.isoformat() if run.end_time else "",
                    status="error" if run.error else "success",
                    outputs=_json(run.outputs),
                    error=run.error,
                    prompt_tokens=prompt_t,
                    completion_tokens=completion_t,
                    total_tokens=total_t,
                )
            )
        except Exception:
            logger.warning("Dropping trace span on update", exc_info=True)
