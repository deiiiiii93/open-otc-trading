from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.schemas import PricingEnvironmentSnapshot
from app.services import position_pricer
from app.services.position_pricer import MarketOverrides, _price_position
from app.services.quantark import QuantArkResult


def _make_position(*, engine_kwargs: dict | None = None) -> SimpleNamespace:
    # Default to a quad-engine snowball so the adaptive chain fires. Cases that
    # need an explicit grid_points override pass their own engine_kwargs.
    if engine_kwargs is None:
        engine_kwargs = {"params_type": "quad_params"}
    return SimpleNamespace(
        id=1,
        portfolio_id=1,
        underlying="000852.SH",
        product_type="SnowballOption",
        product_kwargs={
            "initial_price": 100.0,
            "strike": 100.0,
            "contract_multiplier": 1.0,
        },
        engine_name="SnowballQuadEngine",
        engine_kwargs=engine_kwargs,
        quantity=1.0,
        entry_price=0.0,
        status="open",
        source_trade_id="T1",
        mapping_status="supported",
        mapping_error=None,
        source_payload={"row": {"Trade Status": "Open", "Notional Unit": "CNY"}},
    )


def _make_market() -> PricingEnvironmentSnapshot:
    return PricingEnvironmentSnapshot(
        valuation_date=datetime(2026, 5, 11),
        spot=100.0,
        volatility=0.2,
        rate=0.03,
        dividend_yield=0.0,
        asset_name="000852.SH",
        currency="CNY",
    )


def _call_price_position(position, *, monkeypatch, price_by_grid):
    """Run _price_position with a stubbed price_product that returns
    QuantArkResult(ok=True, data={"price": price_by_grid[grid]}) keyed on the
    grid_points passed in engine_kwargs."""

    calls: list[int] = []

    def fake_price_product(product_type, product_kwargs, market, engine_name, engine_kwargs):
        grid = int((engine_kwargs or {}).get("params_kwargs", {}).get("grid_points", -1))
        calls.append(grid)
        value = price_by_grid.get(grid)
        if value is None:
            return QuantArkResult(ok=False, error=f"no rig for grid {grid}", data={})
        return QuantArkResult(ok=True, data={"price": float(value)})

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", fake_price_product
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    result = _price_position(
        position=position,
        pricing_rows={},
        valuation_date=datetime(2026, 5, 11),
        overrides=MarketOverrides(spot=100.0, rate=0.03, dividend_yield=0.0, volatility=0.2),
        engine_name=None,
        engine_kwargs=None,
        compute_greeks=False,
        spot_fetcher=lambda symbol, vd: (100.0, {"source": "test"}),
        symbol_spot_cache={},
    )
    return result, calls


def test_case_A_first_grid_usable_stops_immediately(monkeypatch):
    position = _make_position()
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: 50.0, 501: 50.0, 1001: 50.0},
    )
    assert result["ok"] is True
    assert calls == [201]
    assert result["result_payload"]["grid_points_used"] == 201
    assert "attempted_grids" not in result["result_payload"]


def test_case_B_escalates_when_first_grid_implausible(monkeypatch):
    position = _make_position()
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: math.inf, 501: 50.0, 1001: 50.0},
    )
    assert result["ok"] is True
    assert calls == [201, 501]
    assert result["result_payload"]["grid_points_used"] == 501
    assert result["result_payload"]["attempted_grids"] == [201, 501, 1001]


def test_case_C_escalates_to_top_grid(monkeypatch):
    position = _make_position()
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: math.inf, 501: math.inf, 1001: 50.0},
    )
    assert result["ok"] is True
    assert calls == [201, 501, 1001]
    assert result["result_payload"]["grid_points_used"] == 1001
    assert result["result_payload"]["attempted_grids"] == [201, 501, 1001]


def test_case_D_all_grids_fail_returns_implausible_error(monkeypatch):
    position = _make_position()
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={201: math.inf, 501: math.inf, 1001: math.inf},
    )
    assert result["ok"] is False
    assert result["error_type"] == "pricing"
    assert calls == [201, 501, 1001]
    assert result["result_payload"]["attempted_grids"] == [201, 501, 1001]


def test_case_E_explicit_grid_is_honored_single_attempt(monkeypatch):
    position = _make_position(
        engine_kwargs={"params_type": "quad_params", "params_kwargs": {"grid_points": 501}}
    )
    result, calls = _call_price_position(
        position, monkeypatch=monkeypatch,
        price_by_grid={501: math.inf, 1001: 50.0},
    )
    assert result["ok"] is False
    assert calls == [501]
    assert result["result_payload"]["attempted_grids"] == [501]


def test_case_F_greeks_reuse_winning_grid(monkeypatch):
    from app.services.quantark import QuantArkResult
    position = _make_position()  # default: quad_params, no explicit grid → adaptive chain

    def fake_price_product(product_type, product_kwargs, market, engine_name, engine_kwargs):
        grid = int((engine_kwargs or {}).get("params_kwargs", {}).get("grid_points", -1))
        if grid == 201:
            return QuantArkResult(ok=True, data={"price": float("inf")})  # implausible
        if grid == 501:
            return QuantArkResult(ok=True, data={"price": 50.0})
        return QuantArkResult(ok=False, error="not rigged", data={})

    captured_greeks_kwargs: dict = {}

    def fake_compute_greeks(position, market, *, engine_kwargs=None):
        captured_greeks_kwargs["engine_kwargs"] = dict(engine_kwargs or {})
        return {"ok": True, "delta": 0.5, "gamma": 0.0, "vega": 0.0,
                "theta": 0.0, "rho": 0.0, "rho_q": 0.0}

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", fake_price_product
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )
    monkeypatch.setattr(
        "app.services.position_pricer.compute_position_greeks", fake_compute_greeks
    )

    result = _price_position(
        position=position,
        pricing_rows={},
        valuation_date=datetime(2026, 5, 11),
        overrides=MarketOverrides(spot=100.0, rate=0.03, dividend_yield=0.0, volatility=0.2),
        engine_name=None,
        engine_kwargs=None,
        compute_greeks=True,
        spot_fetcher=lambda symbol, vd: (100.0, {"source": "test"}),
        symbol_spot_cache={},
    )

    assert result["ok"] is True
    assert result["result_payload"]["grid_points_used"] == 501
    assert captured_greeks_kwargs["engine_kwargs"]["params_kwargs"]["grid_points"] == 501


def test_price_position_greeks_are_signed_by_quantity(monkeypatch):
    position = _make_position()
    position.quantity = -2.0

    monkeypatch.setattr(
        "app.services.position_pricer.price_product",
        lambda *a, **k: QuantArkResult(ok=True, data={"price": 50.0}),
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )
    monkeypatch.setattr(
        "app.services.position_pricer.compute_position_greeks",
        lambda position, market, *, engine_kwargs=None: {
            "ok": True,
            "delta": 0.5,
            "gamma": -0.1,
            "vega": 2.0,
            "theta": -3.0,
            "rho": 4.0,
            "rho_q": -5.0,
        },
    )

    result = _price_position(
        position=position,
        pricing_rows={},
        valuation_date=datetime(2026, 5, 11),
        overrides=MarketOverrides(spot=100.0, rate=0.03, dividend_yield=0.0, volatility=0.2),
        engine_name=None,
        engine_kwargs=None,
        compute_greeks=True,
        spot_fetcher=lambda symbol, vd: (100.0, {"source": "test"}),
        symbol_spot_cache={},
    )

    assert result["ok"] is True
    assert result["result_payload"]["delta"] == pytest.approx(-1.0)
    assert result["result_payload"]["gamma"] == pytest.approx(0.2)
    assert result["result_payload"]["vega"] == pytest.approx(-4.0)
    assert result["result_payload"]["theta"] == pytest.approx(6.0)
    assert result["result_payload"]["rho"] == pytest.approx(-8.0)
    assert result["result_payload"]["rho_q"] == pytest.approx(10.0)
