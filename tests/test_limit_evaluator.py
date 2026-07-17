from __future__ import annotations

import pytest

from app.services.limits.evaluator import (
    LimitRule,
    NormalizedObservation,
    evaluate,
)


def _rule(**overrides) -> LimitRule:
    values = {
        "metric_kind": "delta",
        "source_kind": "risk_run",
        "aggregation": "net",
        "transform": "signed",
        "comparator": "upper",
        "warning_lower": None,
        "warning_upper": 80.0,
        "hard_lower": None,
        "hard_upper": 100.0,
        "unit": "delta_units",
        "currency": None,
        "bump_convention": None,
    }
    values.update(overrides)
    return LimitRule(**values)


def _observation(value=0.0, **overrides) -> NormalizedObservation:
    values = {
        "values": (value,),
        "source_kind": "risk_run",
        "unit": "delta_units",
        "currency": None,
        "bump_convention": None,
        "source_status": "completed",
        "is_stale": False,
        "is_complete": True,
        "reason_code": None,
        "reason": None,
        "coverage_count": 1,
        "coverage_ratio": 1.0,
        "evidence": {"risk_run_id": 11},
    }
    values.update(overrides)
    return NormalizedObservation(**values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (79.999, "ok"),
        (80.0, "warning"),
        (99.999, "warning"),
        (100.0, "breach"),
    ],
)
def test_upper_limit_boundaries(value, expected) -> None:
    result = evaluate(_rule(), _observation(value))
    assert result.status == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (-79.999, "ok"),
        (-80.0, "warning"),
        (-99.999, "warning"),
        (-100.0, "breach"),
    ],
)
def test_lower_limit_boundaries(value, expected) -> None:
    result = evaluate(
        _rule(
            comparator="lower",
            warning_lower=-80.0,
            warning_upper=None,
            hard_lower=-100.0,
            hard_upper=None,
        ),
        _observation(value),
    )
    assert result.status == expected


@pytest.mark.parametrize(
    ("value", "expected", "boundary"),
    [
        (0.0, "ok", "lower"),
        (-80.0, "warning", "lower"),
        (80.0, "warning", "upper"),
        (-100.0, "breach", "lower"),
        (100.0, "breach", "upper"),
    ],
)
def test_range_boundaries(value, expected, boundary) -> None:
    result = evaluate(
        _rule(
            comparator="range",
            warning_lower=-80.0,
            warning_upper=80.0,
            hard_lower=-100.0,
            hard_upper=100.0,
        ),
        _observation(value),
    )
    assert result.status == expected
    assert result.governing_boundary == boundary


@pytest.mark.parametrize(
    ("transform", "observed", "adverse"),
    [
        ("signed", -25.0, -25.0),
        ("absolute", -25.0, 25.0),
        ("loss_magnitude", -25.0, 25.0),
        ("loss_magnitude", 25.0, 0.0),
    ],
)
def test_transforms_preserve_native_observation(transform, observed, adverse) -> None:
    result = evaluate(
        _rule(transform=transform),
        _observation(observed),
    )
    assert result.observed_value == observed
    assert result.adverse_value == adverse


@pytest.mark.parametrize("metric_kind", ["var", "cvar"])
def test_backtest_tail_losses_are_loss_positive(metric_kind) -> None:
    rule = _rule(
        metric_kind=metric_kind,
        source_kind="backtest",
        transform="loss_magnitude",
        unit="USD",
        currency="USD",
    )
    result = evaluate(
        rule,
        _observation(
            25.0,
            source_kind="backtest",
            unit="USD",
            currency="USD",
        ),
    )

    assert result.observed_value == 25.0
    assert result.adverse_value == 25.0


@pytest.mark.parametrize("metric_kind", ["var", "cvar", "stress_pnl"])
def test_scenario_tail_and_stress_losses_are_loss_negative(metric_kind) -> None:
    rule = _rule(
        metric_kind=metric_kind,
        source_kind="scenario_test",
        transform="loss_magnitude",
        unit="USD",
        currency="USD",
    )
    result = evaluate(
        rule,
        _observation(
            -25.0,
            source_kind="scenario_test",
            unit="USD",
            currency="USD",
        ),
    )

    assert result.observed_value == -25.0
    assert result.adverse_value == 25.0


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
def test_evaluator_uses_registered_aggregation(aggregation, expected) -> None:
    result = evaluate(
        _rule(aggregation=aggregation),
        _observation(values=(3.0, -6.0, 8.0)),
    )
    assert result.observed_value == expected


def test_upper_utilization_and_headroom() -> None:
    result = evaluate(_rule(), _observation(50.0))
    assert result.utilization == pytest.approx(0.5)
    assert result.headroom == pytest.approx(50.0)
    assert result.governing_boundary == "upper"


@pytest.mark.parametrize(
    ("value", "boundary"),
    [(-90.0, "lower"), (90.0, "upper")],
)
def test_range_uses_nearest_adverse_boundary(value, boundary) -> None:
    result = evaluate(
        _rule(
            comparator="range",
            warning_lower=-80.0,
            warning_upper=80.0,
            hard_lower=-100.0,
            hard_upper=100.0,
        ),
        _observation(value),
    )
    assert result.governing_boundary == boundary
    assert result.utilization == pytest.approx(0.9)
    assert result.headroom == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("value", "boundary", "utilization", "headroom"),
    [
        (-20.0, "lower", 0.1, 180.0),
        (10.0, "upper", 0.2, 40.0),
        (-200.0, "lower", 1.0, 0.0),
        (50.0, "upper", 1.0, 0.0),
    ],
)
def test_asymmetric_range_uses_larger_directional_utilization(
    value,
    boundary,
    utilization,
    headroom,
) -> None:
    result = evaluate(
        _rule(
            comparator="range",
            warning_lower=-80.0,
            hard_lower=-200.0,
            warning_upper=20.0,
            hard_upper=50.0,
        ),
        _observation(value),
    )

    assert result.governing_boundary == boundary
    assert result.utilization == pytest.approx(utilization)
    assert result.headroom == pytest.approx(headroom)


def test_lower_utilization_and_headroom() -> None:
    result = evaluate(
        _rule(
            comparator="lower",
            warning_lower=-80.0,
            warning_upper=None,
            hard_lower=-100.0,
            hard_upper=None,
        ),
        _observation(-90.0),
    )
    assert result.governing_boundary == "lower"
    assert result.utilization == pytest.approx(0.9)
    assert result.headroom == pytest.approx(10.0)


@pytest.mark.parametrize(
    ("observation", "reason_code"),
    [
        (_observation(values=None), "missing_source"),
        (_observation(values=()), "empty_observation"),
        (_observation(is_stale=True), "stale_source"),
        (_observation(source_status="failed"), "source_failed"),
        (_observation(is_complete=False), "incomplete_scope"),
        (
            _observation(
                reason_code="methodology_mismatch",
                reason="Wrong confidence",
            ),
            "methodology_mismatch",
        ),
        (_observation(values=(float("inf"),)), "invalid_value"),
        (
            _observation(values=(1e308, 1e308)),
            "invalid_value",
        ),
        (
            _observation(values=None, source_status="failed"),
            "source_failed",
        ),
        (
            _observation(values=None, source_status="empty"),
            "empty_source",
        ),
    ],
)
def test_unusable_observations_are_unknown(observation, reason_code) -> None:
    result = evaluate(_rule(), observation)
    assert result.status == "unknown"
    assert result.reason_code == reason_code
    assert result.observed_value is None
    assert result.adverse_value is None
    assert result.utilization is None
    assert result.headroom is None


@pytest.mark.parametrize(
    ("rule", "observation", "reason_code"),
    [
        (
            _rule(source_kind="risk_run"),
            _observation(source_kind="scenario_test"),
            "source_kind_mismatch",
        ),
        (
            _rule(unit="USD"),
            _observation(unit="CNY"),
            "unit_mismatch",
        ),
        (
            _rule(unit="USD", currency="USD"),
            _observation(unit="USD", currency="CNY"),
            "currency_mismatch",
        ),
        (
            _rule(
                metric_kind="rho_q",
                unit="USD_per_1bp",
                currency="USD",
                bump_convention="parallel_dividend_yield_1bp",
            ),
            _observation(
                unit="USD_per_1bp",
                currency="USD",
                bump_convention="parallel_dividend_yield_10bp",
            ),
            "bump_convention_mismatch",
        ),
    ],
)
def test_incompatible_observations_are_unknown(rule, observation, reason_code) -> None:
    result = evaluate(rule, observation)
    assert result.status == "unknown"
    assert result.reason_code == reason_code


@pytest.mark.parametrize(
    "rule",
    [
        _rule(
            comparator="lower",
            warning_lower=50.0,
            hard_lower=25.0,
            warning_upper=None,
            hard_upper=None,
        ),
        _rule(warning_upper=-50.0, hard_upper=-25.0),
        _rule(warning_upper=-1.0, hard_upper=0.0),
        _rule(
            comparator="range",
            warning_lower=20.0,
            hard_lower=10.0,
            warning_upper=30.0,
            hard_upper=40.0,
        ),
        _rule(
            transform="absolute",
            comparator="lower",
            warning_lower=-80.0,
            hard_lower=-100.0,
            warning_upper=None,
            hard_upper=None,
        ),
    ],
)
def test_directionally_invalid_rules_fail_closed(rule) -> None:
    result = evaluate(rule, _observation(10.0))

    assert result.status == "unknown"
    assert result.reason_code == "invalid_definition"


@pytest.mark.parametrize(
    ("rule", "observation"),
    [
        (
            _rule(
                metric_kind="var",
                source_kind="scenario_test",
                transform="absolute",
                unit="USD",
                currency="USD",
            ),
            _observation(
                -10.0,
                source_kind="scenario_test",
                unit="USD",
                currency="USD",
            ),
        ),
        (
            _rule(
                metric_kind="stress_pnl",
                source_kind="scenario_test",
                transform="signed",
                unit="USD",
                currency="USD",
            ),
            _observation(
                -10.0,
                source_kind="scenario_test",
                unit="USD",
                currency="USD",
            ),
        ),
        (
            _rule(
                metric_kind="rho",
                unit="USD_per_1bp",
                currency="USD",
                bump_convention="",
            ),
            _observation(
                unit="USD_per_1bp",
                currency="USD",
                bump_convention="",
            ),
        ),
        (
            _rule(
                metric_kind="rho_q",
                unit="USD_per_1bp",
                currency="USD",
                bump_convention="parallel_dividend_yield_1bp",
            ),
            _observation(
                unit="USD_per_1bp",
                currency="USD",
                bump_convention=None,
            ),
        ),
    ],
)
def test_registry_safety_metadata_corruption_is_invalid_definition(
    rule,
    observation,
) -> None:
    result = evaluate(rule, observation)

    assert result.status == "unknown"
    assert result.reason_code == "invalid_definition"


def test_unknown_keeps_coverage_and_evidence() -> None:
    result = evaluate(
        _rule(),
        _observation(
            is_complete=False,
            coverage_count=3,
            coverage_ratio=0.75,
            evidence={"risk_run_id": 22, "failed_position_ids": [9]},
        ),
    )
    assert result.coverage_count == 3
    assert result.coverage_ratio == pytest.approx(0.75)
    assert result.evidence == {
        "risk_run_id": 22,
        "failed_position_ids": [9],
    }


def test_success_keeps_thresholds_coverage_and_evidence() -> None:
    result = evaluate(
        _rule(),
        _observation(
            85.0,
            coverage_count=4,
            coverage_ratio=1.0,
            evidence={"risk_run_id": 33, "position_ids": [1, 2, 3, 4]},
        ),
    )
    assert result.status == "warning"
    assert result.warning_upper == 80.0
    assert result.hard_upper == 100.0
    assert result.coverage_count == 4
    assert result.evidence["risk_run_id"] == 33


@pytest.mark.parametrize("unknown", [False, True])
def test_evidence_is_a_canonical_deep_snapshot(unknown) -> None:
    evidence = {
        "source": {
            "ids": [3, 1],
            "diagnostics": {"failed": [9]},
        }
    }
    observation = _observation(
        values=None if unknown else (10.0,),
        evidence=evidence,
    )

    result = evaluate(_rule(), observation)
    evidence["source"]["ids"].append(7)
    evidence["source"]["diagnostics"]["failed"].append(10)

    assert result.evidence == {
        "source": {
            "diagnostics": {"failed": [9]},
            "ids": [3, 1],
        }
    }


@pytest.mark.parametrize(
    ("metric_kind", "source_kind", "value"),
    [
        ("delta", "risk_run", 10.0),
        ("gamma", "risk_run", 10.0),
        ("vega", "risk_run", 10.0),
        ("theta", "risk_run", 10.0),
        ("rho", "risk_run", 10.0),
        ("rho_q", "risk_run", 10.0),
        ("var", "scenario_test", -10.0),
        ("cvar", "backtest", 10.0),
        ("stress_pnl", "scenario_test", -10.0),
    ],
)
def test_all_metrics_evaluate(metric_kind, source_kind, value) -> None:
    monetary = metric_kind in {
        "vega",
        "theta",
        "rho",
        "rho_q",
        "var",
        "cvar",
        "stress_pnl",
    }
    bump = (
        "parallel_rate_1bp"
        if metric_kind == "rho"
        else "parallel_dividend_yield_1bp"
        if metric_kind == "rho_q"
        else None
    )
    loss = metric_kind in {"var", "cvar", "stress_pnl"}
    rule = _rule(
        metric_kind=metric_kind,
        source_kind=source_kind,
        transform="loss_magnitude" if loss else "absolute",
        unit="USD" if monetary else "units",
        currency="USD" if monetary else None,
        bump_convention=bump,
    )
    observation = _observation(
        value,
        source_kind=source_kind,
        unit=rule.unit,
        currency=rule.currency,
        bump_convention=bump,
    )

    result = evaluate(rule, observation)
    assert result.status == "ok"
    assert result.reason_code is None
