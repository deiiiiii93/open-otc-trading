"""record_answer — a no-op recorder that captures a model's typed answer.

Used by golden-workflow steps that ask the model to commit a structured answer.
Tolerates BOTH the canonical nested shape record_answer(answer={"hotspot": "AAPL",
"delta": 573.35}) AND a flat shape record_answer(hotspot="AAPL", delta=573.35),
because models will not reliably nest under `answer`. The args are read back at
score time via the answer_field_* assertions. It changes no state; it is
DOMAIN_READ (benign) so it is safe inside read-only fan-out and never triggers
audit-write classification.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup

# Bounds so the globally-exposed recorder can't become an unbounded capture sink
# (tool inputs are persisted by the local tracer). A benchmark answer is a handful
# of scalars; anything past these caps is truncated, not retained. Applied at the
# VALIDATION boundary (so the bounded values are what the tool layer sees) AND at the
# scoring read path (assertions.answer_fields) — the tool's return-value bound alone
# does not protect either channel (Codex code-review).
_MAX_FIELDS = 32
_MAX_STR = 256
_MAX_KEY = 128


def _bound_value(v: Any) -> Any:
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v)
    return s if len(s) <= _MAX_STR else s[:_MAX_STR] + "…"


def _bound_fields(fields: dict[str, Any]) -> dict[str, Any]:
    return {str(k)[:_MAX_KEY]: _bound_value(v)
            for k, v in list(fields.items())[:_MAX_FIELDS]}


class RecordAnswerInput(BaseModel):
    # extra="allow" so a flat call record_answer(hotspot=..., delta=...) validates
    # instead of erroring; those extras are merged into fields alongside `answer`.
    model_config = ConfigDict(extra="allow")
    answer: dict[str, Any] = Field(
        default_factory=dict,
        description="Your structured answer as key→value pairs, e.g. "
                    '{"hotspot": "AAPL", "delta": 573.35}. You may also pass the '
                    "fields directly as keyword arguments.",
    )

    @model_validator(mode="after")
    def _bound_at_validation(self) -> "RecordAnswerInput":
        """Cap the recorded answer (nested + flat) at the validation boundary so the
        bounded values are what any downstream layer sees, not just the return value."""
        if isinstance(self.answer, dict):
            self.answer = _bound_fields(self.answer)
        extras = self.__pydantic_extra__ or {}
        for k in list(extras):
            extras[k] = _bound_value(extras[k])
        # cap the number of extra flat fields too
        if len(extras) > _MAX_FIELDS:
            for k in list(extras)[_MAX_FIELDS:]:
                del extras[k]
        return self


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("record_answer", args_schema=RecordAnswerInput)
def record_answer_tool(answer: dict[str, Any] | None = None,
                       **extra: Any) -> dict[str, Any]:
    """Record your final structured answer for this question when asked to. Pass
    each requested field either inside `answer` (e.g.
    answer={"hotspot": "AAPL", "delta": 573.35}) or as direct keyword arguments.
    This does not change any state; it captures your answer verbatim for
    evaluation."""
    fields: dict[str, Any] = dict(answer or {})
    fields.update(extra)  # tolerate flat kwargs
    fields = _bound_fields(fields)  # cap field count + value size (capture-sink guard)
    return {"recorded": True, "fields": fields}
