"""Deny write/irreversible/dispatch tools inside fanned-out subagents.

A fanned-out investigator must be able to READ freely (that is the whole point of
per-breach judgment) but must never perform a non-idempotent WRITE — on resume/retry
the QuickJS ``eval`` re-runs and re-dispatches every subagent, so a write would repeat.

Classification is by the authoritative capability taxonomy, not a heuristic:

- A tool is a WRITE iff its ``__capability_group__`` (stashed by ``capability_gated``)
  is a mutating group — ``DOMAIN_WRITE`` / ``PAGE_ACTION`` / ``ASYNC_DISPATCH`` — OR it
  is a deepagents filesystem/shell write (``write_file`` / ``edit_file`` / ``execute``),
  which are built-ins and carry no capability group.
- ``run_python`` is argument-aware: read-like for pure analysis, a write when
  ``writes_artifacts=True``.
- Everything else is a READ and is allowed.

**Allow-by-default (not deny-by-default), deliberately.** The read surface is
open-ended and includes ungated reads (e.g. ``get_position_summaries`` is a plain
``@tool`` with no capability group), so a deny-by-default guard blocks legitimate
reads and stalls the fan-out — the exact failure a live smoke surfaced. Writes, by
contrast, are positively identifiable: every mutating domain tool is
``capability_gated`` into a write group, and the only ungated writes are the three FS
built-ins above. Confinement is provided upstream by the eval gate
(``EvalAttributionGateMiddleware``), which only lets the single allowlisted Case-3
workflow fan out at all; this guard's sole job inside that trusted context is to keep
writes out of the re-dispatchable path.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from . import dynamic_subagents as ds
from .envelopes import ToolGroup

_DENY = (
    "Fanned-out subagents are read-only: `{name}` writes (or dispatches async work), "
    "so it is blocked in a dynamic-subagents fan-out. Read tools are allowed."
)

# Mutating capability groups: any tool gated into one of these is a write.
_WRITE_GROUPS = frozenset(
    {ToolGroup.DOMAIN_WRITE, ToolGroup.PAGE_ACTION, ToolGroup.ASYNC_DISPATCH}
)

# deepagents filesystem/shell built-ins are NOT capability-gated, so classify by name.
# Reads (read_file/ls/glob/grep) are allowed; these mutate the workspace or shell out.
_FS_WRITE_TOOLS = frozenset({"write_file", "edit_file", "execute"})


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


class FanoutReadOnlyMiddleware(AgentMiddleware):
    def __init__(self, tools: Sequence[BaseTool] = ()) -> None:
        super().__init__()
        # Build the write-name set from the persona's capability-gated tools. Reads
        # (and ungated tools) are intentionally absent -> allowed.
        self._write_names = frozenset(
            t.name
            for t in tools
            if getattr(t, "__capability_group__", None) in _WRITE_GROUPS
        )

    def _is_fanout_write(self, name: str, args: dict[str, Any] | None) -> bool:
        if name == "run_python":
            return bool((args or {}).get("writes_artifacts"))
        if name in _FS_WRITE_TOOLS:
            return True
        return name in self._write_names

    def _maybe_deny(self, request: ToolCallRequest) -> ToolMessage | None:
        name = request.tool_call.get("name", "")
        args = request.tool_call.get("args") or {}
        if _in_fanout_subagent(_read_configurable()) and self._is_fanout_write(name, args):
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
