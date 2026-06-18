"""Argument-aware HITL middleware for run_python artifact writes."""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware.human_in_the_loop import (
    ActionRequest,
    HITLRequest,
    HumanInTheLoopMiddleware,
    ReviewConfig,
)
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT, StateT
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from .hitl import run_python_requires_hitl


class RunPythonArtifactHITLMiddleware(
    HumanInTheLoopMiddleware[StateT, ContextT, ResponseT]
):
    """Pause only run_python calls that intentionally write artifacts."""

    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__(
            {
                "run_python": {
                    "allowed_decisions": ["approve", "reject"],
                    "description": _run_python_description,
                }
            }
        )
        self.enabled = enabled

    def after_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        if not self.enabled:
            return None

        messages = state["messages"]
        if not messages:
            return None

        last_ai_msg = next(
            (msg for msg in reversed(messages) if isinstance(msg, AIMessage)),
            None,
        )
        if not last_ai_msg or not last_ai_msg.tool_calls:
            return None

        action_requests: list[ActionRequest] = []
        review_configs: list[ReviewConfig] = []
        interrupt_indices: list[int] = []
        config = self.interrupt_on["run_python"]

        for idx, tool_call in enumerate(last_ai_msg.tool_calls):
            if tool_call["name"] != "run_python":
                continue
            if not run_python_requires_hitl(tool_call.get("args") or {}):
                continue
            action_request, review_config = self._create_action_and_config(
                tool_call,
                config,
                state,
                runtime,
            )
            action_requests.append(action_request)
            review_configs.append(review_config)
            interrupt_indices.append(idx)

        if not action_requests:
            return None

        decisions = interrupt(
            HITLRequest(
                action_requests=action_requests,
                review_configs=review_configs,
            )
        )["decisions"]
        if len(decisions) != len(interrupt_indices):
            raise ValueError(
                "Number of human decisions does not match run_python interrupts."
            )

        revised_tool_calls: list[ToolCall] = []
        artificial_tool_messages: list[ToolMessage] = []
        decision_idx = 0
        for idx, tool_call in enumerate(last_ai_msg.tool_calls):
            if idx not in interrupt_indices:
                revised_tool_calls.append(tool_call)
                continue
            revised_tool_call, tool_message = self._process_decision(
                decisions[decision_idx],
                tool_call,
                config,
            )
            decision_idx += 1
            if revised_tool_call is not None:
                revised_tool_calls.append(revised_tool_call)
            if tool_message:
                artificial_tool_messages.append(tool_message)

        last_ai_msg.tool_calls = revised_tool_calls
        return {"messages": [last_ai_msg, *artificial_tool_messages]}

    async def aafter_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


def _run_python_description(
    tool_call: ToolCall,
    _state: AgentState[Any],
    _runtime: Runtime[Any],
) -> str:
    args = tool_call.get("args") or {}
    description = args.get("description")
    if isinstance(description, str) and description:
        return description
    return "Run Python script that writes downloadable artifacts."
