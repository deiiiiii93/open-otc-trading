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
    min_count: int = 1


class LedgerPredicateCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["ledger_predicate"]
    tool: str
    args: dict = Field(default_factory=dict)
    expect: list[FieldPredicate]


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


def parse_goal_contract(data: dict) -> GoalContractV1:
    """Validate a framer-produced contract dict and return the model.

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
