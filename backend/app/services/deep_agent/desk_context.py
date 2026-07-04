"""Desk-context propagation to persona subagents (#1).

The interactive orchestrator delegates work to persona subagents via the
deepagents ``task()`` tool, which hands the subagent only the task description
(plus non-excluded parent state) — no structured scope. Skills that declare
``required_context`` (portfolio_id, pricing_parameter_profile_id, dates) then
block with "missing required scope" even when the orchestrator already resolved
the portfolio, because the subagent has no context pack to satisfy the contract.

This module closes that gap without patching deepagents:

- ``DeskContextMiddleware.after_model`` **snoops** resolved scope from domain-tool
  call args as the desk turn runs and accumulates it into the ``desk_context``
  state key. Because that key is NOT in deepagents' ``_EXCLUDED_STATE_KEYS``, it
  propagates parent → subagent automatically (and persists across turns via the
  checkpointer).
- ``DeskContextMiddleware.wrap_model_call`` **injects** the accumulated scope as an
  authoritative context block into the system prompt, so a delegated subagent's
  ``required_context`` is satisfied structurally — independent of how the parent
  phrased the delegation.

Paired with the ``delegated-scope-policy`` fragment (which tells the persona to
trust supplied scope), this both provides the scope and instructs the model to
use it.
"""
from __future__ import annotations

import logging
from typing import Any, NotRequired, TypedDict

from langchain_core.messages import AIMessage, SystemMessage
from langchain.agents.middleware.types import AgentMiddleware

logger = logging.getLogger(__name__)

# Scope keys worth propagating — the union of the risk/trading skills'
# required_context/optional_context that identify *what* to act on. Deliberately
# excludes free-form or high-cardinality args (method, spot grids, etc.).
SCOPE_KEYS: tuple[str, ...] = (
    "portfolio_id",
    "pricing_parameter_profile_id",
    "position_ids",
    "start_date",
    "end_date",
    "engine_config_id",
)

# Tools whose args are NOT authoritative desk scope (meta/subagent plumbing).
_IGNORED_TOOLS = {"task", "read_file", "write_todos", "write_file", "edit_file", "ls"}


def extract_scope(tool_calls: list[dict]) -> dict[str, Any]:
    """Pull known scope keys from a turn's tool-call args (last-write-wins).

    Only ``SCOPE_KEYS`` are captured; ``None`` values and ignored/meta tools are
    skipped. Returns a possibly-empty dict.
    """
    scope: dict[str, Any] = {}
    for call in tool_calls or []:
        name = call.get("name", "")
        if name in _IGNORED_TOOLS:
            continue
        args = call.get("args") or {}
        if not isinstance(args, dict):
            continue
        for key in SCOPE_KEYS:
            val = args.get(key)
            if val is not None:
                scope[key] = val
    return scope


def merge_scope(existing: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Merge ``new`` over ``existing`` (last-write-wins per key); pure."""
    merged = dict(existing or {})
    merged.update(new or {})
    return merged


def render_desk_context_block(scope: dict[str, Any]) -> str:
    """Render the injectable authoritative-scope block, or "" if scope is empty."""
    if not scope:
        return ""
    lines = [
        "## Desk session context (authoritative scope)",
        "",
        "The orchestrator has already resolved this session's scope. Treat these "
        "values as satisfying any skill's `required_context` — they are supplied, "
        "not missing. Use them directly; do not block asking for them.",
        "",
    ]
    for key in SCOPE_KEYS:
        if key in scope:
            lines.append(f"- {key}: {scope[key]}")
    return "\n".join(lines)


class DeskContextState(TypedDict):
    desk_context: NotRequired[dict[str, Any]]


class DeskContextMiddleware(AgentMiddleware):
    """Snoop resolved scope into ``desk_context`` state and inject it into prompts.

    Placed on both the orchestrator (snoops its direct domain-tool calls) and each
    persona subagent (injects the inherited scope, and snoops its own calls).
    Fail-open: any error injects/records nothing rather than breaking the turn.
    """

    state_schema = DeskContextState

    # ------------------------------------------------------------------
    # after_model — snoop scope from the just-produced tool calls
    # ------------------------------------------------------------------
    def after_model(self, state, runtime, config):  # type: ignore[override]
        try:
            messages = (state or {}).get("messages") or []
            last_ai = next(
                (m for m in reversed(messages) if isinstance(m, AIMessage)), None
            )
            if last_ai is None:
                return None
            tool_calls = getattr(last_ai, "tool_calls", None) or []
            new_scope = extract_scope(tool_calls)
            if not new_scope:
                return None
            existing = (state or {}).get("desk_context") or {}
            merged = merge_scope(existing, new_scope)
            if merged == existing:
                return None
            return {"desk_context": merged}
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning("desk_context after_model snoop failed", exc_info=True)
            return None

    async def aafter_model(self, state, runtime, config):  # type: ignore[override]
        return self.after_model(state, runtime, config)

    # ------------------------------------------------------------------
    # wrap_model_call — inject the accumulated scope block
    # ------------------------------------------------------------------
    def _inject_request(self, request):
        scope = (getattr(request, "state", {}) or {}).get("desk_context")
        block = render_desk_context_block(scope or {})
        if not block:
            return None
        base = request.system_message.content if request.system_message is not None else ""
        new_content = f"{base}\n\n{block}" if base else block
        return request.override(system_message=SystemMessage(content=new_content))

    def wrap_model_call(self, request, handler):
        try:
            injected = self._inject_request(request)
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning("desk_context inject failed", exc_info=True)
            injected = None
        return handler(injected if injected is not None else request)

    async def awrap_model_call(self, request, handler):
        try:
            injected = self._inject_request(request)
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning("desk_context inject failed", exc_info=True)
            injected = None
        return await handler(injected if injected is not None else request)
