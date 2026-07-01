"""Deny write/irreversible/unclassified tools inside fanned-out subagents.

Argument-aware: ``run_python`` is read-like for pure analysis but writes when
``writes_artifacts=True``, so we inspect args, not just the tool name. Deny-by-default:
anything not positively classified ``read`` is treated as a write.
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from . import dynamic_subagents as ds
from .hitl import _RISK_LEVEL_BY_TOOL

_DENY = (
    "Fanned-out subagents are read-only: `{name}` may write or is unclassified, "
    "so it is blocked in a dynamic-subagents fan-out."
)


def _read_configurable() -> dict[str, Any]:
    try:
        from langgraph.config import get_config

        return get_config().get("configurable") or {}
    except Exception:
        return {}


def _in_fanout_subagent(cfg: dict[str, Any]) -> bool:
    # A fanned-out investigator: authorized Case-3 run AND running inside a
    # deepagents subagent (task() stamps ls_agent_type='subagent').
    return (
        cfg.get(ds.FANOUT_ATTRIBUTION_KEY) == ds.FANOUT_ATTRIBUTION_CASE3
        and cfg.get("ls_agent_type") == "subagent"
    )


def _is_fanout_write(name: str, args: dict[str, Any] | None) -> bool:
    # Argument-aware escalation: run_python writes when writes_artifacts=True.
    if name == "run_python":
        return bool((args or {}).get("writes_artifacts"))
    # Deny-by-default: anything not positively classified "read" is a write.
    return _RISK_LEVEL_BY_TOOL.get(name, "write") != "read"


class FanoutReadOnlyMiddleware(AgentMiddleware):
    def _maybe_deny(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        if _in_fanout_subagent(_read_configurable()) and _is_fanout_write(name, args):
            return ToolMessage(
                content=_DENY.format(name=name),
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )
        return None

    def wrap_tool_call(self, request, handler):
        return self._maybe_deny(request) or handler(request)

    async def awrap_tool_call(self, request, handler):
        return self._maybe_deny(request) or await handler(request)
