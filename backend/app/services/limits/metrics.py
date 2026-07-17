from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import math
from typing import Iterable


@dataclass(frozen=True, slots=True)
class MetricDescriptor:
    kind: str
    allowed_sources: frozenset[str]
    default_transform: str
    monetary: bool
    requires_bump_convention: bool


_RISK_ONLY = frozenset({"risk_run"})
_TAIL_SOURCES = frozenset({"scenario_test", "backtest"})
_SCENARIO_ONLY = frozenset({"scenario_test"})

METRIC_REGISTRY: dict[str, MetricDescriptor] = {
    "delta": MetricDescriptor(
        "delta", _RISK_ONLY, "signed", False, False
    ),
    "gamma": MetricDescriptor(
        "gamma", _RISK_ONLY, "signed", False, False
    ),
    "vega": MetricDescriptor(
        "vega", _RISK_ONLY, "signed", True, False
    ),
    "theta": MetricDescriptor(
        "theta", _RISK_ONLY, "signed", True, False
    ),
    "rho": MetricDescriptor(
        "rho", _RISK_ONLY, "signed", True, True
    ),
    "rho_q": MetricDescriptor(
        "rho_q", _RISK_ONLY, "signed", True, True
    ),
    "var": MetricDescriptor(
        "var", _TAIL_SOURCES, "loss_magnitude", True, False
    ),
    "cvar": MetricDescriptor(
        "cvar", _TAIL_SOURCES, "loss_magnitude", True, False
    ),
    "stress_pnl": MetricDescriptor(
        "stress_pnl",
        _SCENARIO_ONLY,
        "loss_magnitude",
        True,
        False,
    ),
}

AGGREGATIONS = frozenset(
    {"net", "gross_abs", "max_abs", "minimum", "maximum"}
)


class MetricAggregationError(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def get_metric(metric_kind: str) -> MetricDescriptor:
    try:
        return METRIC_REGISTRY[metric_kind]
    except KeyError:
        raise KeyError(f"unsupported limit metric {metric_kind!r}") from None


def _coerce_finite_values(values: Iterable[float]) -> tuple[float, ...]:
    coerced: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MetricAggregationError(
                "invalid_value",
                "the source returned a non-numeric observation",
            )
        try:
            numeric = float(value)
        except (OverflowError, TypeError, ValueError) as exc:
            raise MetricAggregationError(
                "invalid_value",
                "the source returned an out-of-range numeric observation",
            ) from exc
        if not math.isfinite(numeric):
            raise MetricAggregationError(
                "invalid_value",
                "the source returned a non-finite numeric observation",
            )
        coerced.append(numeric)
    return tuple(coerced)


def _deterministic_net(values: tuple[float, ...]) -> float:
    ordered = tuple(sorted(values, key=lambda value: (abs(value), value)))
    try:
        return float(math.fsum(ordered))
    except OverflowError:
        exact = sum(
            (Fraction.from_float(value) for value in ordered),
            start=Fraction(),
        )
        try:
            return float(exact)
        except OverflowError as exc:
            raise MetricAggregationError(
                "invalid_value",
                "the exact numeric aggregate is out of range",
            ) from exc


def aggregate_values(values: Iterable[float], aggregation: str) -> float:
    if aggregation not in AGGREGATIONS:
        raise MetricAggregationError(
            "unsupported_aggregation",
            f"unsupported aggregation {aggregation!r}",
        )
    collected = tuple(values)
    if not collected:
        raise MetricAggregationError(
            "empty_observation",
            "the source returned no numeric observations",
        )
    normalized = _coerce_finite_values(collected)

    try:
        if aggregation == "net":
            result = _deterministic_net(normalized)
        elif aggregation == "gross_abs":
            result = float(
                math.fsum(sorted(abs(value) for value in normalized))
            )
        elif aggregation == "max_abs":
            result = float(max(abs(value) for value in normalized))
        elif aggregation == "minimum":
            result = float(min(normalized))
        else:
            result = float(max(normalized))
    except OverflowError as exc:
        raise MetricAggregationError(
            "invalid_value",
            "the numeric aggregate overflowed",
        ) from exc
    if not math.isfinite(result):
        raise MetricAggregationError(
            "invalid_value",
            "the numeric aggregate is non-finite",
        )
    return result
