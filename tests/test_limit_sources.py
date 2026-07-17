from __future__ import annotations

from datetime import datetime

import pytest


def _risk_run(*, status: str = "completed"):
    from app.models import RiskRun

    return RiskRun(
        id=41,
        portfolio_id=1,
        status=status,
        method="summary",
        resolved_position_ids=[1, 2, 3],
        metrics={
            "valuation_as_of": "2026-07-17T09:00:00",
            "source_metadata": {
                "methodology": {"method": "summary"},
                "source_config": {},
                "market_evidence_complete": True,
            },
            "shared": {"delta": 12.0, "gamma": 0.3},
            "by_currency": {
                "USD": {"rho_q": 10.0, "vega": 25.0, "position_count": 2},
                "CNY": {"rho_q": 70.0, "vega": 140.0, "position_count": 1},
            },
            "positions": [
                {
                    "position_id": 1,
                    "underlying": "AAPL",
                    "product_type": "EuropeanVanillaOption",
                    "product_family": "option",
                    "currency": "USD",
                    "delta": 5.0,
                    "rho_q": 4.0,
                    "pricing_ok": True,
                    "greeks_ok": True,
                },
                {
                    "position_id": 2,
                    "underlying": "MSFT",
                    "product_type": "EuropeanVanillaOption",
                    "product_family": "option",
                    "currency": "USD",
                    "delta": 7.0,
                    "rho_q": 6.0,
                    "pricing_ok": True,
                    "greeks_ok": True,
                },
                {
                    "position_id": 3,
                    "underlying": "BABA",
                    "product_type": "SnowballOption",
                    "product_family": "autocallable",
                    "currency": "CNY",
                    "delta": 2.0,
                    "rho_q": 70.0,
                    "pricing_ok": True,
                    "greeks_ok": True,
                },
            ],
        },
        created_at=datetime(2026, 7, 17, 9, 1),
    )


def test_risk_adapter_uses_shared_totals_and_propagates_rho_q() -> None:
    from app.services.limits.sources import ObservationScope, adapt_risk_run

    run = _risk_run()
    run.resolved_position_ids = [1, 2]
    run.metrics["positions"] = run.metrics["positions"][:2]
    run.metrics["by_currency"] = {
        "USD": run.metrics["by_currency"]["USD"]
    }
    delta = adapt_risk_run(
        None,
        run,
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("portfolio", position_ids=(1, 2)),
    )
    rho_q = adapt_risk_run(
        None,
        run,
        metric_kind="rho_q",
        aggregation="net",
        unit="USD/1pct",
        currency="USD",
        scope=ObservationScope("portfolio", position_ids=(1, 2)),
        bump_convention="arbitrary-requested-convention",
    )
    gross_rho_q = adapt_risk_run(
        None,
        run,
        metric_kind="rho_q",
        aggregation="gross_abs",
        unit="USD/1pct",
        currency="USD",
        scope=ObservationScope("portfolio", position_ids=(1, 2)),
        bump_convention="arbitrary-requested-convention",
    )

    assert delta.values == (12.0,)
    assert delta.evidence["value_source"] == "shared"
    assert rho_q.values == (10.0,)
    assert rho_q.bump_convention == "parallel_dividend_yield_1pct"
    assert rho_q.unit == "USD/1pct"
    assert rho_q.evidence["value_source"] == "by_currency"
    assert gross_rho_q.values == (4.0, 6.0)
    assert gross_rho_q.evidence["value_source"] == "positions"


def test_risk_adapter_fails_closed_for_empty_or_partly_invalid_values() -> None:
    from app.services.limits.sources import ObservationScope, adapt_risk_run

    empty = _risk_run()
    empty.resolved_position_ids = []
    empty.metrics["positions"] = []
    empty.metrics["shared"] = {"delta": 0.0}
    empty_result = adapt_risk_run(
        None,
        empty,
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("portfolio"),
    )

    invalid = _risk_run()
    invalid.metrics["by_currency"]["CNY"]["rho_q"] = 10**1000
    invalid_result = adapt_risk_run(
        None,
        invalid,
        metric_kind="rho_q",
        aggregation="net",
        unit="USD/1pct",
        currency="USD",
        scope=ObservationScope("portfolio"),
        valuation_as_of=datetime(2026, 7, 17, 9, 0),
        bump_convention="arbitrary-requested-convention",
    )

    assert empty_result.values is None
    assert empty_result.reason_code == "empty_source"
    assert invalid_result.values is None
    assert invalid_result.reason_code == "invalid_value"


def test_risk_adapter_partial_failure_is_scope_aware() -> None:
    from app.services.limits.sources import ObservationScope, adapt_risk_run

    run = _risk_run(status="completed_with_errors")
    run.metrics["positions"][2]["pricing_ok"] = False
    run.metrics["positions"][2]["greeks_ok"] = False
    run.metrics["positions"][2]["pricing_error"] = "missing quote"
    unaffected = adapt_risk_run(
        None,
        run,
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("underlying", value="AAPL"),
    )
    affected = adapt_risk_run(
        None,
        run,
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("underlying", value="BABA"),
    )

    assert unaffected.values == (5.0,)
    assert unaffected.is_complete is True
    assert affected.values is None
    assert affected.reason_code == "incomplete_scope"
    assert affected.evidence["failed_position_ids"] == [3]


def test_scenario_tail_adapter_requires_locked_methodology() -> None:
    from app.models import ScenarioTestRun
    from app.services.limits.sources import (
        SCENARIO_TAIL_METHODOLOGY,
        adapt_scenario_test_run,
    )

    run = ScenarioTestRun(
        id=51,
        portfolio_id=1,
        status="completed",
        scenario_spec={"predefined": ["market_crash"]},
        config={},
        results={
            "source_metadata": {
                "methodology": SCENARIO_TAIL_METHODOLOGY,
                "source_currencies": ["USD"],
                "scenario_set_hash": "sha256:" + ("a" * 64),
            },
            "var_cvar": {"var": -20.0, "cvar": -25.0, "confidence": 0.95},
            "scenarios": [],
        },
        excluded_positions=[],
        resolved_position_ids=[1],
        created_at=datetime(2026, 7, 17, 9, 0),
    )
    var = adapt_scenario_test_run(
        run,
        metric_kind="var",
        methodology=SCENARIO_TAIL_METHODOLOGY,
        unit="USD",
        currency="USD",
    )
    mismatch = adapt_scenario_test_run(
        run,
        metric_kind="cvar",
        methodology={**SCENARIO_TAIL_METHODOLOGY, "confidence": 0.99},
        unit="USD",
        currency="USD",
    )

    assert var.values == (-20.0,)
    assert var.evidence["confidence"] == 0.95
    assert mismatch.values is None
    assert mismatch.reason_code == "methodology_mismatch"


def test_scenario_stress_selects_exact_name_or_explicit_worst_set() -> None:
    from app.models import ScenarioTestRun
    from app.services.limits.sources import (
        SCENARIO_TAIL_METHODOLOGY,
        adapt_scenario_test_run,
    )

    run = ScenarioTestRun(
        id=52,
        portfolio_id=1,
        status="completed",
        scenario_spec={"predefined": ["down_10", "down_20"]},
        config={},
        results={
            "source_metadata": {
                "methodology": SCENARIO_TAIL_METHODOLOGY,
                "source_currencies": ["USD"],
                "scenario_set_hash": "sha256:" + ("b" * 64),
                "scenario_names": ["down_10", "down_20"],
            },
            "scenarios": [
                {"name": "down_10", "pnl": -10.0, "pnl_pct": -0.1},
                {"name": "down_20", "pnl": -25.0, "pnl_pct": -0.25},
            ]
        },
        excluded_positions=[],
        resolved_position_ids=[1],
        created_at=datetime(2026, 7, 17, 9, 0),
    )
    named = adapt_scenario_test_run(
        run,
        metric_kind="stress_pnl",
        methodology={
            "selection": "named",
            "scenario_set_hash": "sha256:" + ("b" * 64),
            "scenario_name": "down_10",
        },
        unit="USD",
        currency="USD",
    )
    worst = adapt_scenario_test_run(
        run,
        metric_kind="stress_pnl",
        methodology={
            "selection": "worst_of_set",
            "scenario_set_hash": "sha256:" + ("b" * 64),
            "scenario_names": ["down_10", "down_20"],
        },
        unit="USD",
        currency="USD",
    )
    missing = adapt_scenario_test_run(
        run,
        metric_kind="stress_pnl",
        methodology={
            "selection": "named",
            "scenario_set_hash": "sha256:" + ("b" * 64),
            "scenario_name": "renamed",
        },
        unit="USD",
        currency="USD",
    )

    assert named.values == (-10.0,)
    assert named.evidence["scenario_name"] == "down_10"
    assert worst.values == (-25.0,)
    assert worst.evidence["scenario_name"] == "down_20"
    assert missing.values is None
    assert missing.reason_code == "missing_scenario"


@pytest.mark.parametrize(
    ("metric_kind", "expected"),
    [("var", 20.0), ("cvar", 25.0)],
)
def test_backtest_tail_adapter_reads_live_portfolio_shape_only_at_locked_method(
    metric_kind: str,
    expected: float,
) -> None:
    from app.models import BacktestRun
    from app.services.limits.sources import (
        BACKTEST_TAIL_METHODOLOGY,
        adapt_backtest_run,
    )

    run = BacktestRun(
        id=61,
        portfolio_id=1,
        status="completed",
        spec={"start": "2025-01-02", "end": "2025-12-31"},
        config={},
        results={
            "source_metadata": {
                "methodology": BACKTEST_TAIL_METHODOLOGY,
                "source_currencies": ["USD"],
            },
            "portfolio": {"var_95": 20.0, "cvar_95": 25.0},
            "by_underlying": [],
        },
        excluded_positions=[],
        resolved_position_ids=[1],
        created_at=datetime(2026, 7, 17, 9, 0),
    )
    observation = adapt_backtest_run(
        run,
        metric_kind=metric_kind,
        methodology=BACKTEST_TAIL_METHODOLOGY,
        unit="USD",
        currency="USD",
    )
    mismatch = adapt_backtest_run(
        run,
        metric_kind=metric_kind,
        methodology={**BACKTEST_TAIL_METHODOLOGY, "horizon": "10d"},
        unit="USD",
        currency="USD",
    )

    assert observation.values == (expected,)
    assert observation.evidence["result_path"] == f"portfolio.{metric_kind}_95"
    assert mismatch.reason_code == "methodology_mismatch"


def test_scenario_and_backtest_require_persisted_methodology_currency_and_set_identity() -> None:
    from app.models import BacktestRun, ScenarioTestRun
    from app.services.limits.sources import (
        BACKTEST_TAIL_METHODOLOGY,
        SCENARIO_TAIL_METHODOLOGY,
        adapt_backtest_run,
        adapt_scenario_test_run,
    )

    scenario = ScenarioTestRun(
        id=71,
        portfolio_id=1,
        status="completed",
        scenario_spec={},
        config={},
        results={
            "source_metadata": {
                "methodology": SCENARIO_TAIL_METHODOLOGY,
                "source_currencies": ["USD"],
                "scenario_set_hash": "sha256:" + ("c" * 64),
                "scenario_names": ["down"],
            },
            "scenarios": [{"name": "down", "pnl": -5.0}],
        },
        excluded_positions=[],
        resolved_position_ids=[1],
        created_at=datetime(2026, 7, 17, 9, 0),
    )
    drifted = adapt_scenario_test_run(
        scenario,
        metric_kind="stress_pnl",
        methodology={
            "selection": "named",
            "scenario_set_hash": "sha256:" + ("d" * 64),
            "scenario_name": "down",
        },
        unit="USD",
        currency="USD",
    )
    scenario.results["source_metadata"]["source_currencies"] = ["USD", "CNY"]
    mixed = adapt_scenario_test_run(
        scenario,
        metric_kind="stress_pnl",
        methodology={
            "selection": "named",
            "scenario_set_hash": "sha256:" + ("c" * 64),
            "scenario_name": "down",
        },
        unit="USD",
        currency="USD",
    )
    backtest = BacktestRun(
        id=72,
        portfolio_id=1,
        status="completed",
        spec={},
        config={},
        results={
            "source_metadata": {
                "methodology": {**BACKTEST_TAIL_METHODOLOGY, "horizon": "1d"},
                "source_currencies": ["USD"],
            },
            "portfolio": {"var_95": 5.0},
        },
        excluded_positions=[],
        resolved_position_ids=[1],
        created_at=datetime(2026, 7, 17, 9, 0),
    )
    bad_method = adapt_backtest_run(
        backtest,
        metric_kind="var",
        methodology=BACKTEST_TAIL_METHODOLOGY,
        unit="USD",
        currency="USD",
    )

    assert drifted.reason_code == "methodology_mismatch"
    assert mixed.reason_code == "incomplete_scope"
    assert bad_method.reason_code == "methodology_mismatch"


def test_product_family_scope_uses_canonical_persisted_family() -> None:
    from app.services.limits.sources import ObservationScope, adapt_risk_run

    run = _risk_run()
    observation = adapt_risk_run(
        None,
        run,
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("product_family", value="autocallable"),
    )

    assert observation.values == (2.0,)
    assert observation.evidence["covered_position_ids"] == [3]


def test_risk_adapter_fails_closed_when_manifest_marks_synthetic_defaults() -> None:
    from app.services.limits.sources import ObservationScope, adapt_risk_run

    run = _risk_run()
    run.metrics["source_metadata"].update(
        {
            "market_evidence_complete": False,
            "missing_market_evidence": [
                "position:1:missing:spot",
                "position:1:missing:parameters",
            ],
        }
    )
    observation = adapt_risk_run(
        None,
        run,
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("portfolio"),
    )

    assert observation.values is None
    assert observation.reason_code == "incomplete_scope"
    assert observation.evidence["missing_market_evidence"] == [
        "position:1:missing:spot",
        "position:1:missing:parameters",
    ]


@pytest.mark.parametrize(
    ("status", "reason_code"),
    [("failed", "source_failed"), ("empty", "empty_source")],
)
def test_source_statuses_fail_closed(status: str, reason_code: str) -> None:
    from app.services.limits.sources import ObservationScope, adapt_risk_run

    observation = adapt_risk_run(
        None,
        _risk_run(status=status),
        metric_kind="delta",
        aggregation="net",
        unit="shares",
        scope=ObservationScope("portfolio"),
    )

    assert observation.values is None
    assert observation.reason_code == reason_code
