"""Read-only tracing & audit API.

Serves the local trace store to the /tracing viewer. Strictly read-only by
design — the trace store is an append-only audit record; no mutating
endpoints exist or should ever be added here.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.services.tracing.config import resolve_tracing_mode
from app.services.tracing.store import TraceStore, get_trace_store

_PREVIEW_CHARS = 2000


def _preview(raw: str | None) -> tuple[str | None, bool]:
    if raw is None:
        return None, False
    if len(raw) <= _PREVIEW_CHARS:
        return raw, False
    return raw[:_PREVIEW_CHARS], True


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "trace_id": row["trace_id"],
        "name": row["name"],
        "run_type": row["run_type"],
        "status": row["status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "total_tokens": row["total_tokens"],
        "thread_id": row["thread_id"],
        "task_id": row["task_id"],
        "workflow_id": row["workflow_id"],
    }


def _tree_node(row: dict[str, Any]) -> dict[str, Any]:
    inputs_preview, inputs_truncated = _preview(row["inputs"])
    outputs_preview, outputs_truncated = _preview(row["outputs"])
    return {
        **_summary(row),
        "parent_run_id": row["parent_run_id"],
        "dotted_order": row["dotted_order"],
        "error": row["error"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "inputs_preview": inputs_preview,
        "inputs_truncated": inputs_truncated,
        "outputs_preview": outputs_preview,
        "outputs_truncated": outputs_truncated,
    }


def build_tracing_router(
    get_store: Callable[[], TraceStore] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/tracing", tags=["tracing"])

    def _store() -> TraceStore:
        if get_store is not None:
            return get_store()
        return get_trace_store(get_settings())

    @router.get("/config")
    def tracing_config() -> dict[str, Any]:
        mode = resolve_tracing_mode(get_settings())
        return {
            "mode": mode.value,
            "langsmith_url": os.environ.get("LANGSMITH_PROJECT_URL")
            or "https://smith.langchain.com",
        }

    @router.get("/recent")
    def recent_traces(limit: int = 50, offset: int = 0) -> dict[str, Any]:
        rows = _store().list_recent_traces(limit=limit, offset=offset)
        return {"traces": [_summary(r) for r in rows]}

    @router.get("/threads/{thread_id}/traces")
    def thread_traces(
        thread_id: int, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        rows = _store().list_thread_traces(thread_id, limit=limit, offset=offset)
        return {"thread_id": thread_id, "traces": [_summary(r) for r in rows]}

    @router.get("/traces/{trace_id}")
    def trace_tree(trace_id: str) -> dict[str, Any]:
        rows = _store().get_trace(trace_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Trace not found")
        return {"trace_id": trace_id, "runs": [_tree_node(r) for r in rows]}

    @router.get("/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        row = _store().get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return row

    return router
