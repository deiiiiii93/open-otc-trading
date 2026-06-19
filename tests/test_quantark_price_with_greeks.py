from __future__ import annotations

from unittest.mock import patch

from app.schemas import PricingEnvironmentSnapshot
from app.services import quantark


def _market() -> PricingEnvironmentSnapshot:
    return PricingEnvironmentSnapshot(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0)


def test_price_product_with_greeks_returns_price_and_greeks():
    result = quantark.price_product_with_greeks(
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100, "option_type": "CALL", "maturity": 1.0},
        market=_market(),
        engine_name="BlackScholesEngine",
        compute_greeks=True,
    )
    assert result.ok is True
    assert result.error is None
    price = result.data["price"]
    assert isinstance(price, float) and price > 0
    greeks = result.data["greeks"]
    assert greeks is not None
    assert set(greeks) == {"delta", "gamma", "vega", "theta", "rho", "rho_q"}
    assert all(isinstance(v, float) for v in greeks.values())
    assert result.data["greeks_error"] is None


def test_price_product_with_greeks_skips_greeks_when_disabled():
    result = quantark.price_product_with_greeks(
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100, "option_type": "CALL", "maturity": 1.0},
        market=_market(),
        compute_greeks=False,
    )
    assert result.ok is True
    assert result.data["greeks"] is None
    assert result.data["greeks_error"] is None


def test_price_product_with_greeks_degrades_when_greeks_raise():
    # Price succeeds, but the Greek calculator blows up -> price still returned.
    with patch("quantark.asset.equity.riskmeasures.greeks_calculator.GreeksCalculator") as calc_cls:
        calc_cls.return_value.calculate.side_effect = RuntimeError("greek boom")
        result = quantark.price_product_with_greeks(
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 100, "option_type": "CALL", "maturity": 1.0},
            market=_market(),
            compute_greeks=True,
        )
    assert result.ok is True
    assert result.data["price"] > 0
    assert result.data["greeks"] is None
    assert "greek boom" in result.data["greeks_error"]


def test_price_product_with_greeks_returns_error_on_bad_build():
    result = quantark.price_product_with_greeks(
        product_type="NotARealProduct",
        product_kwargs={},
        market=_market(),
    )
    assert result.ok is False
    assert result.error
    assert result.data["price"] == 0.0
    assert result.data["greeks"] is None
    assert result.data["greeks_error"] is None
