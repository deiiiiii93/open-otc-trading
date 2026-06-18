"""Typed task registry for the workflow-routing control plane."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: str
    inputs: dict[str, Any]
    depends_on: list[int] = Field(default_factory=list)
    assigned_persona: str


class FetchPositionSummariesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    fields: Literal["summary", "all"] = "summary"


class ComputeBarrierProximityInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    spot: dict[str, float]
    within_pct: float = Field(gt=0)


class QueryPositionsNearBarrierInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    spot: dict[str, float]
    within_pct: float = Field(gt=0)
    status: str | None = "open"


class QuerySnowballKoFromSpotInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    spot: dict[str, float] = Field(default_factory=dict)
    within_pct: float = Field(default=5.0, gt=0)
    status: str | None = "open"
    as_of: str | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class QueryPositionsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    filter: list[dict[str, Any]] = Field(default_factory=list)
    select: list[str]
    order_by: tuple[str, str] | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class PositionIdsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_ids: list[int]


class PositionIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_id: int


class SnowballKoScheduleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_id: int
    from_date: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class InterpretSnowballTermsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position_ids: list[int]


class ProposeRunBatchPricingInput(BaseModel):
    """Mirrors the run_batch_pricing tool input: profile-driven, no market
    overrides or valuation_date (the profile carries the valuation date)."""

    model_config = ConfigDict(extra="forbid")

    portfolio_id: int
    position_ids: list[int] | None = None
    pricing_parameter_profile_id: int | None = None


class SynthesiseWorkflowResponseInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_artifact_ids: list[int]


class ConverseWithUserInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str


class PlanWorkflowStepInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_message: str


class RunAnalyticScriptInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    payload: dict[str, Any]
    writes_artifacts: bool


@dataclass(frozen=True)
class TaskRegistration:
    task_type: str
    assigned_personas: tuple[str, ...]
    input_model: type[BaseModel]
    output_artifact_kind: str
    tools_scope: tuple[str, ...]
    freshness_window_seconds: int = 24 * 60 * 60

    _ANY_PERSONA: ClassVar[str] = "*"

    def accepts_persona(self, persona: str) -> bool:
        return self._ANY_PERSONA in self.assigned_personas or persona in self.assigned_personas


TASK_REGISTRY: dict[str, TaskRegistration] = {
    "fetch_position_summaries": TaskRegistration(
        task_type="fetch_position_summaries",
        assigned_personas=("trader",),
        input_model=FetchPositionSummariesInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_positions",),
    ),
    "compute_barrier_proximity": TaskRegistration(
        task_type="compute_barrier_proximity",
        assigned_personas=("risk_manager",),
        input_model=ComputeBarrierProximityInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_positions",),
    ),
    "query_positions_near_barrier": TaskRegistration(
        task_type="query_positions_near_barrier",
        assigned_personas=("trader", "risk_manager"),
        input_model=QueryPositionsNearBarrierInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("query_positions_near_barrier",),
    ),
    "query_snowball_ko_from_spot": TaskRegistration(
        task_type="query_snowball_ko_from_spot",
        assigned_personas=("trader", "risk_manager"),
        input_model=QuerySnowballKoFromSpotInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("query_snowball_ko_from_spot",),
    ),
    "query_positions": TaskRegistration(
        task_type="query_positions",
        assigned_personas=("trader", "risk_manager"),
        input_model=QueryPositionsInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("query_positions",),
    ),
    "get_option_core_terms": TaskRegistration(
        task_type="get_option_core_terms",
        assigned_personas=("trader", "risk_manager"),
        input_model=PositionIdsInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_option_core_terms",),
    ),
    "get_barrier_terms": TaskRegistration(
        task_type="get_barrier_terms",
        assigned_personas=("trader", "risk_manager"),
        input_model=PositionIdsInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_barrier_terms",),
    ),
    "get_sharkfin_terms": TaskRegistration(
        task_type="get_sharkfin_terms",
        assigned_personas=("trader", "risk_manager"),
        input_model=PositionIdsInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_sharkfin_terms",),
    ),
    "get_asian_schedule": TaskRegistration(
        task_type="get_asian_schedule",
        assigned_personas=("trader", "risk_manager"),
        input_model=PositionIdInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_asian_schedule",),
    ),
    "get_snowball_terms": TaskRegistration(
        task_type="get_snowball_terms",
        assigned_personas=("trader", "risk_manager"),
        input_model=PositionIdsInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_snowball_terms",),
    ),
    "get_snowball_ko_schedule": TaskRegistration(
        task_type="get_snowball_ko_schedule",
        assigned_personas=("trader", "risk_manager"),
        input_model=SnowballKoScheduleInput,
        output_artifact_kind="deterministic_query",
        tools_scope=("get_snowball_ko_schedule",),
    ),
    "interpret_snowball_terms": TaskRegistration(
        task_type="interpret_snowball_terms",
        assigned_personas=("trader",),
        input_model=InterpretSnowballTermsInput,
        output_artifact_kind="claim",
        tools_scope=("get_positions",),
    ),
    # Trader proposes via price-portfolio (pricing lens); risk_manager via
    # run-risk (risk lens). Both queue the same unified batch-pricing run.
    "propose_run_batch_pricing": TaskRegistration(
        task_type="propose_run_batch_pricing",
        assigned_personas=("trader", "risk_manager"),
        input_model=ProposeRunBatchPricingInput,
        output_artifact_kind="plan",
        tools_scope=("run_batch_pricing", "convert_currency"),
    ),
    "synthesise_workflow_response": TaskRegistration(
        task_type="synthesise_workflow_response",
        assigned_personas=("orchestrator",),
        input_model=SynthesiseWorkflowResponseInput,
        output_artifact_kind="finding",
        tools_scope=("propose_reply_options",),
    ),
    "converse_with_user": TaskRegistration(
        task_type="converse_with_user",
        assigned_personas=(TaskRegistration._ANY_PERSONA,),
        input_model=ConverseWithUserInput,
        output_artifact_kind="claim",
        tools_scope=("propose_reply_options",),
    ),
    "plan_workflow_step": TaskRegistration(
        task_type="plan_workflow_step",
        assigned_personas=("orchestrator",),
        input_model=PlanWorkflowStepInput,
        output_artifact_kind="plan",
        tools_scope=("propose_reply_options",),
    ),
    "run_analytic_script": TaskRegistration(
        task_type="run_analytic_script",
        assigned_personas=("trader", "risk_manager"),
        input_model=RunAnalyticScriptInput,
        output_artifact_kind="sandbox_output",
        tools_scope=("run_python",),
    ),
}

TOOL_SCOPES_BY_TASK_TYPE: dict[str, tuple[str, ...]] = {
    task_type: registration.tools_scope
    for task_type, registration in TASK_REGISTRY.items()
}


# Renamed task types: persisted task rows created before a rename must still
# resolve (task_registration runs on HITL resume and executor paths), and
# validate_task_spec canonicalizes specs so downstream consumers (e.g. the
# TOOL_SCOPES_BY_TASK_TYPE lookup in context assembly) only see current names.
_TASK_TYPE_ALIASES: dict[str, str] = {
    "propose_run_risk": "propose_run_batch_pricing",
}


def task_registration(task_type: str) -> TaskRegistration:
    task_type = _TASK_TYPE_ALIASES.get(task_type, task_type)
    try:
        return TASK_REGISTRY[task_type]
    except KeyError as exc:
        raise ValueError(f"Unknown task type: {task_type}") from exc


def _migrate_legacy_inputs(task_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
    """Translate pre-rename persisted input shapes to the current input model."""
    if task_type == "propose_run_risk":
        migrated = dict(inputs)
        if "profile_id" in migrated:
            migrated.setdefault(
                "pricing_parameter_profile_id", migrated.pop("profile_id")
            )
        # Post-unification the pricing profile carries the valuation date.
        migrated.pop("valuation_date", None)
        return migrated
    return inputs


def validate_task_spec(spec: TaskSpec) -> TaskSpec:
    canonical_type = _TASK_TYPE_ALIASES.get(spec.task_type, spec.task_type)
    raw_inputs = _migrate_legacy_inputs(spec.task_type, spec.inputs)
    registration = task_registration(canonical_type)
    if not registration.accepts_persona(spec.assigned_persona):
        expected = ", ".join(registration.assigned_personas)
        raise ValueError(
            f"Task {canonical_type} cannot be assigned to {spec.assigned_persona}; "
            f"expected one of: {expected}"
        )
    inputs = registration.input_model.model_validate(raw_inputs)
    return spec.model_copy(
        update={
            "task_type": canonical_type,
            "inputs": inputs.model_dump(mode="json"),
        }
    )
