"""Pre-eval attribution gate: block every ``eval`` unless server-authorized.

Enabling ``CodeInterpreterMiddleware`` exposes a general QuickJS ``eval`` tool. This
gate rejects *every* ``eval`` call unless the run carries server-set Case-3 attribution
for an allowlisted workflow — blocking both emergent fan-out and arbitrary non-``task()``
QuickJS. Authorization comes only from ``configurable`` (set by deterministic server
code); the model, its JS, and tool args can never write it.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from . import dynamic_subagents as ds

_EVAL_TOOL_NAME = "eval"
_DENY = (
    "This run is not authorized to execute `eval`. QuickJS fan-out is restricted "
    "to allowlisted Case-3 workflows."
)


def _read_configurable() -> dict[str, Any]:
    try:
        from langgraph.config import get_config

        return get_config().get("configurable") or {}
    except Exception:
        return {}


def _authorized(cfg: dict[str, Any]) -> bool:
    if cfg.get(ds.FANOUT_ATTRIBUTION_KEY) != ds.FANOUT_ATTRIBUTION_CASE3:
        return False
    return ds.is_allowlisted(cfg.get(ds.FANOUT_WORKFLOW_ID_KEY))


def _deny(request: ToolCallRequest) -> ToolMessage:
    return ToolMessage(
        content=_DENY,
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


class EvalAttributionGateMiddleware(AgentMiddleware):
    """Reject any ``eval`` tool call lacking server-set Case-3 attribution."""

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]
    ) -> Any:
        if request.tool_call.get("name") == _EVAL_TOOL_NAME and not _authorized(
            _read_configurable()
        ):
            return _deny(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        if request.tool_call.get("name") == _EVAL_TOOL_NAME and not _authorized(
            _read_configurable()
        ):
            return _deny(request)
        return await handler(request)
