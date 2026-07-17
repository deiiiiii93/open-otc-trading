from __future__ import annotations


def test_source_metric_contract_is_canonical_and_detached() -> None:
    from app.services.source_evidence import (
        has_exact_source_metric_contract,
        source_metric_contract,
    )

    risk = source_metric_contract("risk_run")

    assert risk["contract_id"] == "limits-risk_run-metrics/v1"
    assert risk["version"] == 1
    assert risk["metrics"]["delta"]["unit"] == "underlying_units"
    assert (
        risk["metrics"]["gamma"]["unit"]
        == "underlying_units_per_spot_unit"
    )
    assert risk["metrics"]["vega"] == {
        "unit": "{currency}/1volpct",
        "currency_dimension": "reporting",
        "bump_convention": None,
        "calculation_convention": (
            "parallel_volatility_0.01_absolute_price_change"
        ),
    }
    assert risk["metrics"]["theta"]["unit"] == "{currency}/1day"
    assert risk["metrics"]["rho"]["bump_convention"] == "parallel_rate_1pct"
    assert (
        risk["metrics"]["rho_q"]["bump_convention"]
        == "parallel_dividend_yield_1pct"
    )
    assert has_exact_source_metric_contract(
        {"metric_contract": risk},
        "risk_run",
    )

    risk["metrics"]["rho"]["bump_convention"] = "parallel_rate_1bp"

    assert not has_exact_source_metric_contract(
        {"metric_contract": risk},
        "risk_run",
    )
    assert (
        source_metric_contract("risk_run")["metrics"]["rho"]["bump_convention"]
        == "parallel_rate_1pct"
    )


def test_tail_metric_contracts_pin_source_native_semantics() -> None:
    from app.services.source_evidence import source_metric_contract

    scenario = source_metric_contract("scenario_test")
    backtest = source_metric_contract("backtest")

    assert set(scenario["metrics"]) == {"var", "cvar", "stress_pnl"}
    assert scenario["metrics"]["var"]["unit"] == "{currency}"
    assert (
        scenario["metrics"]["var"]["calculation_convention"]
        == "scenario_distribution_negative_pnl_quantile"
    )
    assert set(backtest["metrics"]) == {"var", "cvar"}
    assert (
        backtest["metrics"]["var"]["calculation_convention"]
        == "historical_1trading_day_positive_loss_quantile"
    )
