from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

from .metrics import MetricAggregationError, aggregate_values, get_metric


_REL_TOL = 1e-12
_ABS_TOL = 1e-12
_USABLE_SOURCE_STATUSES = frozenset({"completed", "completed_with_errors"})


@dataclass(frozen=True, slots=True)
class LimitRule:
    metric_kind: str
    source_kind: str
    aggregation: str
    transform: str
    comparator: str
    warning_lower: float | None
    warning_upper: float | None
    hard_lower: float | None
    hard_upper: float | None
    unit: str
    currency: str | None = None
    bump_convention: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    values: tuple[float, ...] | None
    source_kind: str
    unit: str
    currency: str | None = None
    bump_convention: str | None = None
    source_status: str = "completed"
    is_stale: bool = False
    is_complete: bool = True
    reason_code: str | None = None
    reason: str | None = None
    coverage_count: int | None = None
    coverage_ratio: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationResult:
    observed_value: float | None
    adverse_value: float | None
    warning_lower: float | None
    warning_upper: float | None
    hard_lower: float | None
    hard_upper: float | None
    utilization: float | None
    headroom: float | None
    governing_boundary: str | None
    status: str
    reason_code: str | None
    reason: str | None
    coverage_count: int | None
    coverage_ratio: float | None
    evidence: dict[str, Any]


_REASONS = {
    "missing_source": "No eligible source evidence was available.",
    "empty_source": "The source completed without usable numeric evidence.",
    "stale_source": "The source evidence is outside the freshness policy.",
    "source_failed": "The source run failed.",
    "incomplete_scope": "The source does not cover the complete requested scope.",
    "source_kind_mismatch": "The source kind does not match the limit definition.",
    "unit_mismatch": "The source unit does not match the limit definition.",
    "currency_mismatch": "The source currency does not match the limit definition.",
    "bump_convention_mismatch": (
        "The source bump convention does not match the limit definition."
    ),
    "invalid_coverage": "The source coverage metadata is invalid.",
    "invalid_definition": "The limit definition cannot be evaluated.",
    "empty_observation": "The source returned no numeric observations.",
    "invalid_value": "The source returned a non-finite numeric observation.",
    "unsupported_aggregation": "The requested aggregation is not supported.",
}


def _unknown(
    rule: LimitRule,
    observation: NormalizedObservation,
    reason_code: str,
    reason: str | None = None,
) -> EvaluationResult:
    return EvaluationResult(
        observed_value=None,
        adverse_value=None,
        warning_lower=rule.warning_lower,
        warning_upper=rule.warning_upper,
        hard_lower=rule.hard_lower,
        hard_upper=rule.hard_upper,
        utilization=None,
        headroom=None,
        governing_boundary=None,
        status="unknown",
        reason_code=reason_code,
        reason=reason or _REASONS.get(reason_code, reason_code),
        coverage_count=observation.coverage_count,
        coverage_ratio=observation.coverage_ratio,
        evidence=dict(observation.evidence),
    )


def _at_or_above(value: float, boundary: float) -> bool:
    return value > boundary or math.isclose(
        value,
        boundary,
        rel_tol=_REL_TOL,
        abs_tol=_ABS_TOL,
    )


def _at_or_below(value: float, boundary: float) -> bool:
    return value < boundary or math.isclose(
        value,
        boundary,
        rel_tol=_REL_TOL,
        abs_tol=_ABS_TOL,
    )


def _transform(value: float, transform: str) -> float:
    if transform == "signed":
        return value
    if transform == "absolute":
        return abs(value)
    if transform == "loss_magnitude":
        return max(-value, 0.0)
    raise ValueError(f"unsupported transform {transform!r}")


def _ratio(value: float, boundary: float) -> float | None:
    if math.isclose(
        boundary,
        0.0,
        rel_tol=_REL_TOL,
        abs_tol=_ABS_TOL,
    ):
        if math.isclose(
            value,
            0.0,
            rel_tol=_REL_TOL,
            abs_tol=_ABS_TOL,
        ):
            return 1.0
        return None
    return value / boundary


def _threshold_result(
    rule: LimitRule,
    adverse: float,
) -> tuple[str, str, float | None, float]:
    if rule.comparator == "upper":
        if rule.warning_upper is None or rule.hard_upper is None:
            raise ValueError("upper comparator thresholds are incomplete")
        status = (
            "breach"
            if _at_or_above(adverse, rule.hard_upper)
            else "warning"
            if _at_or_above(adverse, rule.warning_upper)
            else "ok"
        )
        return (
            status,
            "upper",
            _ratio(adverse, rule.hard_upper),
            rule.hard_upper - adverse,
        )

    if rule.comparator == "lower":
        if rule.warning_lower is None or rule.hard_lower is None:
            raise ValueError("lower comparator thresholds are incomplete")
        status = (
            "breach"
            if _at_or_below(adverse, rule.hard_lower)
            else "warning"
            if _at_or_below(adverse, rule.warning_lower)
            else "ok"
        )
        return (
            status,
            "lower",
            _ratio(adverse, rule.hard_lower),
            adverse - rule.hard_lower,
        )

    if rule.comparator != "range":
        raise ValueError(f"unsupported comparator {rule.comparator!r}")
    if any(
        boundary is None
        for boundary in (
            rule.warning_lower,
            rule.warning_upper,
            rule.hard_lower,
            rule.hard_upper,
        )
    ):
        raise ValueError("range comparator thresholds are incomplete")
    hard_lower = float(rule.hard_lower)
    hard_upper = float(rule.hard_upper)
    warning_lower = float(rule.warning_lower)
    warning_upper = float(rule.warning_upper)
    status = (
        "breach"
        if (
            _at_or_below(adverse, hard_lower)
            or _at_or_above(adverse, hard_upper)
        )
        else "warning"
        if (
            _at_or_below(adverse, warning_lower)
            or _at_or_above(adverse, warning_upper)
        )
        else "ok"
    )
    lower_headroom = adverse - hard_lower
    upper_headroom = hard_upper - adverse
    if lower_headroom <= upper_headroom:
        return (
            status,
            "lower",
            _ratio(adverse, hard_lower),
            lower_headroom,
        )
    return (
        status,
        "upper",
        _ratio(adverse, hard_upper),
        upper_headroom,
    )


def _preflight_reason(
    rule: LimitRule,
    observation: NormalizedObservation,
) -> tuple[str, str | None] | None:
    if observation.reason_code:
        return observation.reason_code, observation.reason
    if observation.values is None:
        return "missing_source", None
    if observation.source_status == "failed":
        return "source_failed", None
    if observation.source_status == "empty":
        return "empty_source", None
    if observation.source_status not in _USABLE_SOURCE_STATUSES:
        return "missing_source", None
    if observation.is_stale:
        return "stale_source", None
    if not observation.is_complete:
        return "incomplete_scope", None
    if observation.coverage_ratio is not None:
        if (
            not math.isfinite(observation.coverage_ratio)
            or observation.coverage_ratio < 0.0
            or observation.coverage_ratio > 1.0
        ):
            return "invalid_coverage", None
        if observation.coverage_ratio < 1.0:
            return "incomplete_scope", None
    try:
        descriptor = get_metric(rule.metric_kind)
    except KeyError:
        return "invalid_definition", None
    if rule.source_kind not in descriptor.allowed_sources:
        return "invalid_definition", None
    if observation.source_kind != rule.source_kind:
        return "source_kind_mismatch", None
    if observation.unit != rule.unit:
        return "unit_mismatch", None
    if observation.currency != rule.currency:
        return "currency_mismatch", None
    if observation.bump_convention != rule.bump_convention:
        return "bump_convention_mismatch", None
    return None


def evaluate(
    rule: LimitRule,
    observation: NormalizedObservation,
) -> EvaluationResult:
    """Evaluate one normalized observation without database access."""
    unusable = _preflight_reason(rule, observation)
    if unusable is not None:
        reason_code, reason = unusable
        return _unknown(rule, observation, reason_code, reason)

    try:
        observed = aggregate_values(
            observation.values or (),
            rule.aggregation,
        )
        adverse = _transform(observed, rule.transform)
        status, boundary, utilization, headroom = _threshold_result(
            rule,
            adverse,
        )
    except MetricAggregationError as exc:
        return _unknown(rule, observation, exc.reason_code, str(exc))
    except (TypeError, ValueError) as exc:
        return _unknown(rule, observation, "invalid_definition", str(exc))

    return EvaluationResult(
        observed_value=observed,
        adverse_value=adverse,
        warning_lower=rule.warning_lower,
        warning_upper=rule.warning_upper,
        hard_lower=rule.hard_lower,
        hard_upper=rule.hard_upper,
        utilization=utilization,
        headroom=headroom,
        governing_boundary=boundary,
        status=status,
        reason_code=None,
        reason=None,
        coverage_count=observation.coverage_count,
        coverage_ratio=observation.coverage_ratio,
        evidence=dict(observation.evidence),
    )
