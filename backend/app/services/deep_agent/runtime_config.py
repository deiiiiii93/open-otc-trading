from __future__ import annotations

from typing import Any

from ...config import Settings
from ..tracing import tracing_callbacks


def graph_run_config(
    settings: Settings,
    *,
    thread_id: str | int,
    configurable_extra: dict[str, Any] | None = None,
    trace_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run config for every agent invocation.

    ``thread_id`` here is the checkpointer key (sometimes a composite string),
    NOT necessarily an AgentThread id. ``trace_meta`` carries the audit join
    keys (AgentThread ``thread_id``, ``task_id``, ``workflow_id``,
    ``message_id``) stamped onto every trace span and forwarded as run
    metadata so LangSmith can filter by them too. Tracing callbacks are
    attached here so every entry point is audited without per-call-site work.
    """
    configurable: dict[str, Any] = {"thread_id": str(thread_id)}
    if configurable_extra:
        configurable.update(configurable_extra)
    config: dict[str, Any] = {
        "configurable": configurable,
        "recursion_limit": settings.agent_recursion_limit,
    }
    meta = dict(trace_meta or {})
    callbacks = tracing_callbacks(
        settings,
        thread_id=meta.get("thread_id"),
        task_id=meta.get("task_id"),
        workflow_id=meta.get("workflow_id"),
        message_id=meta.get("message_id"),
    )
    if callbacks:
        config["callbacks"] = callbacks
        if meta:
            config["metadata"] = meta
    return config
