"""Goal-mode criteria contract (spec docs/superpowers/specs/2026-06-26-goal-mode-design.md §C).

The framer emits a structured ``GoalContractV1`` (not free prose) so the grader can verify
each criterion against the ledger. ``parse_goal_contract`` enforces the cross-field rules that
keep the acceptance gate trustworthy — most importantly that a write-capable goal verifies a
real end-state, not merely that an artifact exists.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class ContractValidationError(ValueError):
    """Raised when a framer-produced contract violates a §C validation rule."""


class FieldPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    op: Literal[
        "exists", "not_exists", "eq", "neq", "lt", "lte", "gt", "gte", "in", "contains"
    ]
    value: str | float | bool | list[str] | list[float] | None = None


class ArtifactExistsCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["artifact_exists"]
    kind: Literal["plan", "finding", "report", "persisted_run"]
    selector: list[FieldPredicate] | None = None
    min_count: int = Field(default=1, ge=1)


class LedgerPredicateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ledger_predicate"]
    tool: str
    args: dict = Field(default_factory=dict)
    # At least one predicate, else the criterion verifies nothing yet still
    # satisfies the allowed_by_mode end-state rule (a trust-hinge bypass).
    expect: list[FieldPredicate] = Field(min_length=1)


class MeasurableCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["measurable"]
    tool: str
    args: dict = Field(default_factory=dict)
    metric_path: str
    transform: Literal["identity", "abs"] = "identity"
    op: Literal["<", "<=", ">", ">=", "==", "!="]
    threshold: float
    units: str | None = None


Check = Annotated[
    Union[ArtifactExistsCheck, LedgerPredicateCheck, MeasurableCheck],
    Field(discriminator="type"),
]

_END_STATE_TYPES = {"ledger_predicate", "measurable"}


class GoalCriterionV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    required: Literal[True] = True
    check: Check


class GoalContractV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["goal_contract.v1"]
    goal_text: str
    summary: str
    domain_write_policy: Literal["forbidden", "allowed_by_mode"]
    criteria: list[GoalCriterionV1] = Field(min_length=1, max_length=10)


def _criterion_tool(check: Check) -> str | None:
    """The grader tool a predicate/measurable criterion will call, if any."""
    if isinstance(check, (LedgerPredicateCheck, MeasurableCheck)):
        return check.tool
    return None


def parse_goal_contract(
    data: dict, *, grader_tool_allowlist: set[str] | None = None
) -> GoalContractV1:
    """Validate a framer-produced contract dict and return the model.

    ``grader_tool_allowlist``, when provided, is the set of tool names the goal-mode
    grader is permitted to call (see ``GOAL_GRADER_READ``); any criterion referencing a
    tool outside it is rejected before freeze.

    Raises ``ContractValidationError`` on any structural or §C rule violation.
    """
    try:
        contract = GoalContractV1.model_validate(data)
    except Exception as exc:  # pydantic ValidationError -> our error type
        raise ContractValidationError(str(exc)) from exc

    if contract.domain_write_policy == "allowed_by_mode":
        if not any(c.check.type in _END_STATE_TYPES for c in contract.criteria):
            raise ContractValidationError(
                "allowed_by_mode contract must contain at least one end-state predicate "
                "(ledger_predicate or measurable), not artifact_exists alone"
            )

    if grader_tool_allowlist is not None:
        for criterion in contract.criteria:
            tool = _criterion_tool(criterion.check)
            if tool is not None and tool not in grader_tool_allowlist:
                raise ContractValidationError(
                    f"criterion {criterion.id} references tool '{tool}', which is not in "
                    f"the grader allowlist (GOAL_GRADER_READ, DOMAIN_READ only)"
                )
    return contract


def _render_check(check: Check) -> str:
    """One-line, grader-readable description of what verifies a criterion."""
    if isinstance(check, ArtifactExistsCheck):
        return f"verify a `{check.kind}` artifact exists (min_count={check.min_count})"
    if isinstance(check, LedgerPredicateCheck):
        preds = ", ".join(f"{p.path} {p.op} {p.value!r}" for p in check.expect)
        return f"verify via tool `{check.tool}`: {preds}"
    # MeasurableCheck
    metric = f"{check.transform}({check.metric_path})" if check.transform != "identity" else check.metric_path
    return f"verify via tool `{check.tool}`: {metric} {check.op} {check.threshold}"


def render_goal_rubric(contract: GoalContractV1) -> str:
    """Deterministically render the contract into RubricMiddleware's rubric string.

    The grader sees only this string, so each line carries the criterion text *and*
    the machine-checkable verification (tool + predicate) it must confirm.
    """
    return "\n".join(
        f"- [{c.id}] {c.text} ({_render_check(c.check)})" for c in contract.criteria
    )


def goal_contract_hash(contract: GoalContractV1) -> str:
    """Stable sha256 over the contract's canonical JSON (freeze identity)."""
    canonical = json.dumps(
        contract.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Goal run lifecycle & activation gate (spec §F/§H) ---------------------

GoalRunStatus = Literal[
    "awaiting_ratification", "running", "satisfied", "stuck_needs_human", "cancelled"
]
GoalMode = Literal["interactive", "auto", "yolo"]
TerminalReason = Literal[
    "max_iterations_reached", "failed", "grader_error", "context_ceiling"
]

# Statuses for which the thread keeps `active_goal_run_id` set (a run is in flight
# or awaiting a human); satisfied/cancelled release the pointer.
_POINTER_HELD: frozenset[str] = frozenset(
    {"awaiting_ratification", "running", "stuck_needs_human"}
)


class GoalStateError(RuntimeError):
    """Raised on an illegal goal-run state transition."""


class FailingCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str = ""
    status: Literal["failed", "unverified"] = "unverified"
    reason: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class PartialLedgerRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_artifact_ids: list[str] = Field(default_factory=list)
    finding_artifact_ids: list[str] = Field(default_factory=list)
    report_artifact_ids: list[str] = Field(default_factory=list)
    persisted_run_ids: list[str] = Field(default_factory=list)


class GoalRunStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["goal_run_state.v1"] = "goal_run_state.v1"
    goal_run_id: str
    contract_hash: str | None = None  # set on freeze; absent while awaiting_ratification
    mode: GoalMode
    status: GoalRunStatus = "awaiting_ratification"
    terminal_reason: TerminalReason | None = None
    last_verdict: Literal["satisfied", "needs_revision", "failed", "grader_error"] | None = None
    failing_criteria: list[FailingCriterion] = Field(default_factory=list)
    partial_ledger_refs: PartialLedgerRefs = Field(default_factory=PartialLedgerRefs)


def new_goal_run(*, goal_run_id: str, contract_hash: str | None, mode: GoalMode) -> GoalRunStateV1:
    """Create a run in ``awaiting_ratification``. The pointer is set from here."""
    return GoalRunStateV1(goal_run_id=goal_run_id, contract_hash=contract_hash, mode=mode)


def ratify_goal_run(
    state: GoalRunStateV1, *, contract_hash: str | None = None
) -> GoalRunStateV1:
    """awaiting_ratification -> running, freezing the contract identity.

    The frozen ``contract_hash`` is established here (spec §H): pass it explicitly,
    or rely on one already set on the state. A run may not reach ``running`` without
    a freeze identity binding it to the accepted contract/rubric.
    """
    if state.status != "awaiting_ratification":
        raise GoalStateError(f"cannot ratify a run in status '{state.status}'")
    frozen = contract_hash if contract_hash is not None else state.contract_hash
    if not frozen:
        raise GoalStateError("ratification requires a frozen contract_hash")
    return state.model_copy(update={"status": "running", "contract_hash": frozen})


def mark_goal_satisfied(state: GoalRunStateV1) -> GoalRunStateV1:
    """running -> satisfied (releases the pointer)."""
    if state.status != "running":
        raise GoalStateError(f"cannot satisfy a run in status '{state.status}'")
    return state.model_copy(update={"status": "satisfied", "last_verdict": "satisfied"})


def escalate_goal_run(
    state: GoalRunStateV1,
    *,
    terminal_reason: TerminalReason,
    failing_criteria: list[FailingCriterion] | None = None,
    partial_ledger_refs: PartialLedgerRefs | None = None,
) -> GoalRunStateV1:
    """running -> stuck_needs_human (keeps the pointer until resume/cancel)."""
    if state.status != "running":
        raise GoalStateError(f"cannot escalate a run in status '{state.status}'")
    update: dict = {"status": "stuck_needs_human", "terminal_reason": terminal_reason}
    if failing_criteria is not None:
        update["failing_criteria"] = failing_criteria
    if partial_ledger_refs is not None:
        update["partial_ledger_refs"] = partial_ledger_refs
    return state.model_copy(update=update)


def resume_goal_run(state: GoalRunStateV1) -> GoalRunStateV1:
    """stuck_needs_human -> running (same frozen contract)."""
    if state.status != "stuck_needs_human":
        raise GoalStateError(f"cannot resume a run in status '{state.status}'")
    return state.model_copy(update={"status": "running", "terminal_reason": None})


def cancel_goal_run(state: GoalRunStateV1) -> GoalRunStateV1:
    """Any non-terminal status -> cancelled (releases the pointer)."""
    if state.status in {"satisfied", "cancelled"}:
        raise GoalStateError(f"cannot cancel a run in terminal status '{state.status}'")
    return state.model_copy(update={"status": "cancelled"})


def rubric_active(state: GoalRunStateV1) -> bool:
    """The activation gate: the rubric attaches (grader runs) ONLY while running."""
    return state.status == "running"


def pointer_held(state: GoalRunStateV1) -> bool:
    """Whether the thread keeps ``active_goal_run_id`` set for this run."""
    return state.status in _POINTER_HELD
