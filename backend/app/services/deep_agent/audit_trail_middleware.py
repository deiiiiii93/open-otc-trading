"""Always-on capture of dangerous tool executions (audit spec §5.2).

Sits just inside ToolErrorBoundaryMiddleware in every stack (orchestrator,
personas, async agent). Phase 1 is fail-closed: no classified write may
execute without a committed attempt row.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeAlias

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from ..audit_trail import (
    AUDIT_CONTEXT_KEY,
    AuditUnavailableError,
    record_attempt,
    record_outcome,
    record_refusal,
)
from .capability_gate import (
    CapabilityDeniedError,
    CostPreviewRequiredError,
    ToolScopeDeniedError,
)
from .write_actions import classify_write_action, write_names_by_class

logger = logging.getLogger(__name__)

_ToolResult: TypeAlias = ToolMessage | Command

_REFUSAL = (
    "Audit trail unavailable; write action '{name}' blocked (fail-closed). "
    "Read-only tools still work; retry the write shortly."
)

_DENY_REASON_BY_EXC: tuple[tuple[type[Exception], str], ...] = (
    (CapabilityDeniedError, "capability"),
    (CostPreviewRequiredError, "cost_preview"),
    (ToolScopeDeniedError, "tool_scope"),
)

# Fan-out read-only denials return an error ToolMessage with this prefix
# (fanout_readonly._DENY) rather than raising.
_FANOUT_DENY_MARKER = "Fanned-out subagents are read-only:"


def _read_audit_context() -> dict[str, Any] | None:
    try:
        from langgraph.config import get_config

        configurable = get_config().get("configurable") or {}
        ctx = configurable.get(AUDIT_CONTEXT_KEY)
        return dict(ctx) if isinstance(ctx, dict) else None
    except Exception:  # pragma: no cover — outside a runnable context
        return None


class AuditTrailMiddleware(AgentMiddleware):
    def __init__(self, tools: Sequence[BaseTool] = ()) -> None:
        super().__init__()
        self._gated = write_names_by_class(tools)

    def _classify(self, request: ToolCallRequest) -> str | None:
        call = request.tool_call
        return classify_write_action(
            call.get("name", ""),
            call.get("args") or {},
            self._gated,
            include_page_action=False,
        )

    def _refuse(self, request: ToolCallRequest, tool_class: str) -> ToolMessage:
        call = request.tool_call
        record_refusal(
            tool_name=call.get("name", ""),
            tool_class=tool_class,
            tool_call_id=call.get("id"),
            context=_read_audit_context(),
        )
        return ToolMessage(
            content=_REFUSAL.format(name=call.get("name", "")),
            tool_call_id=call["id"],
            name=call.get("name"),
            status="error",
        )

    def _attempt(self, request: ToolCallRequest, tool_class: str) -> int:
        call = request.tool_call
        return record_attempt(
            tool_name=call.get("name", ""),
            tool_class=tool_class,
            tool_call_id=call.get("id"),
            args=call.get("args") or {},
            context=_read_audit_context(),
        )

    @staticmethod
    def _record_result(row_id: int, result: _ToolResult) -> None:
        if isinstance(result, ToolMessage) and result.status == "error":
            content = str(result.content)
            if content.startswith(_FANOUT_DENY_MARKER):
                record_outcome(row_id, status="denied", deny_reason="fanout_readonly")
            else:
                record_outcome(row_id, status="error", error=content)
        else:
            preview = (
                str(result.content) if isinstance(result, ToolMessage) else repr(result)
            )
            record_outcome(row_id, status="ok", result_preview=preview)

    @staticmethod
    def _record_exception(row_id: int, exc: Exception) -> None:
        for exc_type, reason in _DENY_REASON_BY_EXC:
            if isinstance(exc, exc_type):
                record_outcome(row_id, status="denied", deny_reason=reason)
                return
        if isinstance(exc, GraphBubbleUp):
            # Belt-and-braces only: HITL proposals are durably captured at
            # projection time (spec §5.4); this just closes the execution row.
            record_outcome(row_id, status="interrupted")
        else:
            record_outcome(row_id, status="error", error=repr(exc))

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], _ToolResult],
    ) -> _ToolResult:
        tool_class = self._classify(request)
        if tool_class is None:
            return handler(request)
        try:
            row_id = self._attempt(request, tool_class)
        except AuditUnavailableError:
            return self._refuse(request, tool_class)
        try:
            result = handler(request)
        except Exception as exc:
            self._record_exception(row_id, exc)
            raise
        self._record_result(row_id, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[_ToolResult]],
    ) -> _ToolResult:
        tool_class = self._classify(request)
        if tool_class is None:
            return await handler(request)
        try:
            row_id = self._attempt(request, tool_class)
        except AuditUnavailableError:
            return self._refuse(request, tool_class)
        try:
            result = await handler(request)
        except Exception as exc:
            self._record_exception(row_id, exc)
            raise
        self._record_result(row_id, result)
        return result
