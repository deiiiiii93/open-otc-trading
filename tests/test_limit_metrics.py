from __future__ import annotations

from itertools import permutations

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
        ((10**1000,), "net", "invalid_value"),
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


@pytest.mark.parametrize("aggregation", ["net", "gross_abs"])
def test_sum_aggregations_are_permutation_stable(aggregation) -> None:
    values = (1e16, 1.0, -1e16)
    results = {
        aggregate_values(permutation, aggregation)
        for permutation in permutations(values)
    }

    expected = 1.0 if aggregation == "net" else 2e16
    assert results == {expected}


@pytest.mark.parametrize("aggregation", ["net", "gross_abs"])
def test_non_finite_aggregate_fails_closed(aggregation) -> None:
    with pytest.raises(MetricAggregationError) as exc:
        aggregate_values((1e308, 1e308), aggregation)

    assert exc.value.reason_code == "invalid_value"


def test_net_is_stable_when_some_input_orders_overflow_fsum() -> None:
    values = (1e308, 1e308, -1e308)
    results = {
        aggregate_values(permutation, "net")
        for permutation in permutations(values)
    }

    assert results == {1e308}


def test_gross_abs_still_rejects_genuinely_non_finite_permutations() -> None:
    values = (1e308, 1e308, -1e308)

    for permutation in permutations(values):
        with pytest.raises(MetricAggregationError) as exc:
            aggregate_values(permutation, "gross_abs")
        assert exc.value.reason_code == "invalid_value"
