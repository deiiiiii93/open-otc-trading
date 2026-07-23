"""Tests for the quantark.execution phase-1 adoption (spec 2026-07-21).

Covers the registered scenario callables in
``app.services.quantark_execution`` and the migrated
``risk_engine.run_portfolio_scenarios`` grid. All pricing runs through the
real quantark build chain (no engine mocks) so parity assertions are
end-to-end.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import (
    ensure_quantark_path,
    gross_notional_for_position,
    price_product,
    risk_pricing_exclusion,
    usable_model_value,
)
from app.services.quantark_execution import (
    FACTORY_ID,
    RUNNER_ID,
    TRANSFORMER_ID,
    run_market_shift_grid,
)

SPOT_SHIFTS = [-5.0, 0.0, 5.0]
VOL_SHIFTS = [0.0, 0.02]


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


def _market() -> PricingEnvironmentSnapshot:
    return PricingEnvironmentSnapshot(spot=100.0, volatility=0.20, rate=0.03)


def _vanilla_item(strike: float = 100.0) -> dict:
    return {
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {
            "strike": strike,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        "market": _market().model_dump(mode="json"),
        "engine_name": "BlackScholesEngine",
        "engine_kwargs": {},
    }


def _direct_price(product_kwargs: dict, market: PricingEnvironmentSnapshot) -> float:
    priced = price_product(
        "EuropeanVanillaOption",
        product_kwargs,
        market,
        "BlackScholesEngine",
        {},
    )
    assert priced.ok, priced.error
    return float(priced.data["price"])


def _cell_values(outcomes, index):
    from quantark.execution.contracts import PricingFailure

    return [
        None if isinstance(cell, PricingFailure) else cell.value
        for cell in outcomes[index]
    ]


def test_registered_callables_resolve():
    from quantark.execution.scenario import registries

    assert registries.get_factory(FACTORY_ID).fn is not None
    assert registries.get_transformer(TRANSFORMER_ID).fn is not None
    runner = registries.get_runner(RUNNER_ID)
    assert runner.value_kind == "float"


def test_base_cell_matches_price_product():
    outcomes = run_market_shift_grid(
        [_vanilla_item()], [0.0], [0.0], backend="serial", workers=1
    )
    (base, shifted) = _cell_values(outcomes, 0)
    expected = _direct_price(_vanilla_item()["product_kwargs"], _market())
    assert base == pytest.approx(expected, rel=1e-12)
    assert shifted == pytest.approx(expected, rel=1e-12)


def test_grid_cells_match_direct_repricing():
    outcomes = run_market_shift_grid(
        [_vanilla_item()], SPOT_SHIFTS, VOL_SHIFTS, backend="serial", workers=1
    )
    values = _cell_values(outcomes, 0)
    base_market = _market()
    expected = []
    for vol_shift in VOL_SHIFTS:
        for spot_shift in SPOT_SHIFTS:
            shifted = base_market.model_copy(
                update={
                    "spot": base_market.spot * (1.0 + spot_shift / 100.0),
                    "volatility": max(base_market.volatility + vol_shift, 1e-6),
                }
            )
            expected.append(_direct_price(_vanilla_item()["product_kwargs"], shifted))
    assert values[0] == pytest.approx(
        _direct_price(_vanilla_item()["product_kwargs"], base_market), rel=1e-12
    )
    assert values[1:] == pytest.approx(expected, rel=1e-12)


def test_processes_backend_matches_serial():
    items = [_vanilla_item(), _vanilla_item(strike=95.0)]
    serial = run_market_shift_grid(
        items, SPOT_SHIFTS, VOL_SHIFTS, backend="serial", workers=1
    )
    processes = run_market_shift_grid(
        items, SPOT_SHIFTS, VOL_SHIFTS, backend="processes", workers=2
    )
    for index in range(len(items)):
        assert _cell_values(serial, index) == _cell_values(processes, index)


def test_failure_isolated_to_broken_item():
    broken = _vanilla_item()
    broken["product_kwargs"] = {"option_type": "CALL"}  # no strike/maturity
    outcomes = run_market_shift_grid(
        [broken, _vanilla_item()], [0.0], [0.0], backend="serial", workers=1
    )
    from quantark.execution.contracts import PricingFailure

    assert all(
        isinstance(cell, PricingFailure) for cell in outcomes[0]
    ), "every cell of the broken item must fail"
    good = _cell_values(outcomes, 1)
    assert all(value is not None and value > 0.0 for value in good)


def _position(strike: float, quantity: float) -> SimpleNamespace:
    return SimpleNamespace(
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": strike,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        engine_name="BlackScholesEngine",
        engine_kwargs={},
        quantity=quantity,
        status="open",
        mapping_status="manual",
        mapping_error=None,
        source_payload=None,
        currency="USD",
    )


def _legacy_portfolio_value(positions, base_markets, spot_shift, vol_shift) -> float:
    """The pre-migration inline reprice math, kept here as the oracle."""
    total = 0.0
    for pos, base_market in zip(positions, base_markets):
        shifted = base_market.model_copy(
            update={
                "spot": base_market.spot * (1.0 + spot_shift / 100.0),
                "volatility": max(base_market.volatility + vol_shift, 1e-6),
            }
        )
        if risk_pricing_exclusion(pos):
            continue
        price = _direct_price(pos.product_kwargs, shifted)
        value = price * float(pos.quantity)
        if not usable_model_value(value, gross_notional_for_position(pos, shifted)):
            continue
        total += value
    return total


def test_run_portfolio_scenarios_matches_legacy_math(monkeypatch):
    from app.services import risk_engine

    settings = Settings(quantark_execution_backend="serial", risk_parallel_workers=2)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.risk_engine.get_settings", lambda: settings)

    positions = [_position(100.0, 3.0), _position(105.0, -2.0)]
    portfolio = SimpleNamespace(id=7, positions=positions)
    market = _market()

    result = risk_engine.run_portfolio_scenarios(
        portfolio, market, SPOT_SHIFTS, VOL_SHIFTS
    )

    base_markets = [market, market]
    base_value = _legacy_portfolio_value(positions, base_markets, 0.0, 0.0)
    assert result["portfolio_id"] == 7
    assert result["base_pnl"] == 0.0
    assert len(result["cells"]) == len(VOL_SHIFTS)
    for row_index, vol_shift in enumerate(VOL_SHIFTS):
        assert len(result["cells"][row_index]) == len(SPOT_SHIFTS)
        for column_index, spot_shift in enumerate(SPOT_SHIFTS):
            cell = result["cells"][row_index][column_index]
            expected = (
                _legacy_portfolio_value(positions, base_markets, spot_shift, vol_shift)
                - base_value
            )
            assert cell["spot_shift_pct"] == spot_shift
            assert cell["vol_shift_abs"] == vol_shift
            assert cell["pnl"] == pytest.approx(expected, rel=1e-12)


def test_run_portfolio_scenarios_empty_portfolio(monkeypatch):
    from app.services import risk_engine

    settings = Settings(quantark_execution_backend="serial", risk_parallel_workers=2)
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    monkeypatch.setattr("app.services.risk_engine.get_settings", lambda: settings)

    portfolio = SimpleNamespace(id=9, positions=[])
    result = risk_engine.run_portfolio_scenarios(
        portfolio, _market(), SPOT_SHIFTS, VOL_SHIFTS
    )
    for row in result["cells"]:
        for cell in row:
            assert cell["pnl"] == 0.0
