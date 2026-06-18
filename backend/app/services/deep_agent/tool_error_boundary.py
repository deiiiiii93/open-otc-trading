"""Runtime-layer tool-error policy for the deep-agent graph.

deepagents' ``create_deep_agent`` (via langchain's ``create_agent``) builds the
``ToolNode`` internally and never exposes its ``handle_tool_errors`` parameter.
The default policy (``_default_handle_tool_errors``) only converts pydantic
*argument-schema* failures (``ToolInvocationError``) into a recoverable
``ToolMessage`` and re-raises every other exception. So a domain ``ValueError``
raised inside a tool body — e.g. the Snowball booking gate's
``"Incomplete SnowballOption booking terms; missing: …"`` — propagates out of the
subagent, out of the orchestrator, and crashes the whole resume (HTTP 502),
never reaching the LLM that could fix it.

``ToolErrorBoundaryMiddleware`` reinstates the "errors are data" contract at the
runtime layer: its ``wrap_tool_call`` hook catches ordinary tool-body exceptions
and returns an error ``ToolMessage`` so the agent self-corrects. It must run as
the *outermost* middleware (first in the list) so it also catches exceptions
raised by inner middleware and the tool itself.

Control-flow signals (``GraphBubbleUp`` — the base class of ``GraphInterrupt``,
which carries HITL approval interrupts), capability denials, and cost-preview
requirements are re-raised untouched; swallowing them would silently break
human-in-the-loop pauses or envelope widening.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeAlias

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from .capability_gate import CapabilityDeniedError, CostPreviewRequiredError

# Mirror langgraph.prebuilt.tool_node.TOOL_CALL_ERROR_TEMPLATE so a boundary-shaped
# error reads identically to a natively-handled one from the model's perspective.
_ERROR_TEMPLATE = "Error: {error}\n Please fix your mistakes."

_ToolResult: TypeAlias = ToolMessage | Command


def _error_tool_message(request: ToolCallRequest, exc: Exception) -> ToolMessage:
    call = request.tool_call
    return ToolMessage(
        content=_ERROR_TEMPLATE.format(error=repr(exc)),
        name=call.get("name"),
        tool_call_id=call["id"],
        status="error",
    )


class ToolErrorBoundaryMiddleware(AgentMiddleware):
    """Convert ordinary tool exceptions into error ToolMessages."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], _ToolResult],
    ) -> _ToolResult:
        try:
            return handler(request)
        except (GraphBubbleUp, CapabilityDeniedError, CostPreviewRequiredError):
            raise
        except Exception as exc:  # noqa: BLE001 — deliberate broad runtime policy
            return _error_tool_message(request, exc)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[_ToolResult]],
    ) -> _ToolResult:
        try:
            return await handler(request)
        except (GraphBubbleUp, CapabilityDeniedError, CostPreviewRequiredError):
            raise
        except Exception as exc:  # noqa: BLE001 — deliberate broad runtime policy
            return _error_tool_message(request, exc)
