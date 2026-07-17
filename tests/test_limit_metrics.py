from __future__ import annotations

import pytest

from app.services.limits.metrics import (
    METRIC_REGISTRY,
    MetricAggregationError,
    aggregate_values,
    get_metric,
)


def test_registry_contains_the_complete_v1_catalog() -> None:
    assert set(METRIC_REGISTRY) == {
        "delta",
        "gamma",
        "vega",
        "theta",
        "rho",
        "rho_q",
        "var",
        "cvar",
        "stress_pnl",
    }
    assert get_metric("rho_q").requires_bump_convention is True
    assert get_metric("rho_q").monetary is True
    assert get_metric("var").allowed_sources == {
        "scenario_test",
        "backtest",
    }
    assert get_metric("stress_pnl").default_transform == "loss_magnitude"


@pytest.mark.parametrize(
    ("aggregation", "expected"),
    [
        ("net", 5.0),
        ("gross_abs", 17.0),
        ("max_abs", 8.0),
        ("minimum", -6.0),
        ("maximum", 8.0),
    ],
)
def test_aggregations_are_deterministic(aggregation, expected) -> None:
    assert aggregate_values((3.0, -6.0, 8.0), aggregation) == expected


@pytest.mark.parametrize(
    "values,aggregation,reason_code",
    [
        ((), "net", "empty_observation"),
        ((1.0, float("nan")), "net", "invalid_value"),
        ((1.0,), "average", "unsupported_aggregation"),
    ],
)
def test_aggregation_errors_have_stable_reason_codes(
    values,
    aggregation,
    reason_code,
) -> None:
    with pytest.raises(MetricAggregationError) as exc:
        aggregate_values(values, aggregation)
    assert exc.value.reason_code == reason_code


def test_unknown_metric_is_rejected() -> None:
    with pytest.raises(KeyError):
        get_metric("dv01")
