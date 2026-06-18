"""Argument-aware HITL middleware for long-running YOLO tool calls."""
from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from langchain.agents.middleware.human_in_the_loop import (
    ActionRequest,
    HITLRequest,
    HumanInTheLoopMiddleware,
    ReviewConfig,
)
from langchain.agents.middleware.types import AgentState, ContextT, ResponseT, StateT
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.runtime import Runtime
from langgraph.types import interrupt

from .capability_gate import COST_ESTIMATOR_ATTR, LONG_RUNNING_SECONDS

CostEstimator = Callable[[Any], float]


class LongRunningCostHITLMiddleware(
    HumanInTheLoopMiddleware[StateT, ContextT, ResponseT]
):
    """Pause YOLO auto-approval when a tool exceeds the long-run threshold."""

    def __init__(
        self,
        *,
        tools: Sequence[BaseTool],
        enabled: bool = True,
    ) -> None:
        self.estimators = _cost_estimators_by_tool(tools)
        super().__init__(
            {
                name: {"allowed_decisions": ["approve", "reject"]}
                for name in self.estimators
            }
        )
        self.enabled = enabled

    def after_model(
        self,
        state: AgentState[Any],
        runtime: Runtime[ContextT],
    ) -> dict[str, Any] | None:
        if (
            not self.enabled
            or not self.estimators
            or _confirmed_cost_preview()
        ):
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

        for idx, tool_call in enumerate(last_ai_msg.tool_calls):
            tool_name = tool_call["name"]
            estimator = self.estimators.get(tool_name)
            if estimator is None:
                continue
            estimated_seconds = _estimate_seconds(estimator, tool_call.get("args") or {})
            if estimated_seconds is None or estimated_seconds < LONG_RUNNING_SECONDS:
                continue
            action_requests.append(
                ActionRequest(
                    name=tool_name,
                    args=tool_call.get("args") or {},
                    description=_description(tool_call, estimated_seconds),
                )
            )
            review_configs.append(
                ReviewConfig(
                    action_name=tool_name,
                    allowed_decisions=self.interrupt_on[tool_name]["allowed_decisions"],
                )
            )
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
                "Number of human decisions does not match long-running tool interrupts."
            )

        revised_tool_calls: list[ToolCall] = []
        artificial_tool_messages: list[ToolMessage] = []
        decision_idx = 0
        for idx, tool_call in enumerate(last_ai_msg.tool_calls):
            if idx not in interrupt_indices:
                revised_tool_calls.append(tool_call)
                continue
            tool_name = tool_call["name"]
            revised_tool_call, tool_message = self._process_decision(
                decisions[decision_idx],
                tool_call,
                self.interrupt_on[tool_name],
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


def _cost_estimators_by_tool(tools: Sequence[BaseTool]) -> dict[str, CostEstimator]:
    estimators: dict[str, CostEstimator] = {}
    for tool in tools:
        estimator = getattr(tool, COST_ESTIMATOR_ATTR, None)
        if callable(estimator):
            estimators[tool.name] = estimator
    return estimators


def _estimate_seconds(estimator: CostEstimator, args: Any) -> float | None:
    try:
        return float(estimator(args) or 0.0)
    except Exception:
        return None


def _confirmed_cost_preview() -> bool:
    try:
        from langgraph.config import get_config

        config = get_config()
    except Exception:
        return False
    configurable = config.get("configurable") or {}
    return bool(configurable.get("confirmed_cost_preview"))


def _description(tool_call: ToolCall, estimated_seconds: float) -> str:
    args = tool_call.get("args") or {}
    try:
        rendered_args = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered_args = str(args)
    return (
        f"YOLO auto-approval is blocked because `{tool_call['name']}` is "
        f"estimated at ~{estimated_seconds:.1f}s, above the "
        f"{LONG_RUNNING_SECONDS:.0f}s threshold. Confirm to run it "
        f"synchronously, or reject and choose a narrower or async path. "
        f"Args: {rendered_args}"
    )
