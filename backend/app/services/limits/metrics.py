from __future__ import annotations

from dataclasses import dataclass
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
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in collected
    ):
        raise MetricAggregationError(
            "invalid_value",
            "the source returned a non-finite numeric observation",
        )

    try:
        if aggregation == "net":
            result = float(math.fsum(collected))
        elif aggregation == "gross_abs":
            result = float(math.fsum(abs(value) for value in collected))
        elif aggregation == "max_abs":
            result = float(max(abs(value) for value in collected))
        elif aggregation == "minimum":
            result = float(min(collected))
        else:
            result = float(max(collected))
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
