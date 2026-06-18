# tests/test_pricing_profiles_resolution.py
from app.models import PricingParameterRow
from app.services.pricing_profiles import (
    UnderlyingMarketParams,
    resolve_underlying_market_params,
)


def _row(symbol, **kw):
    # Transient (unpersisted) row; the resolver only reads attributes.
    return PricingParameterRow(symbol=symbol, **kw)


def test_resolve_agrees_when_rows_identical():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.01, volatility=0.2)
            for _ in range(3)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert res.ok
    assert (res.rate, res.dividend_yield, res.volatility) == (0.03, 0.01, 0.2)


def test_resolve_missing_field_is_listed():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.01, volatility=None)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert not res.ok
    assert res.missing_fields == ("volatility",)
    assert res.volatility is None


def test_resolve_conflicting_field_is_ambiguous():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2),
            _row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.35)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert not res.ok
    assert res.ambiguous_fields == ("volatility",)


def test_resolve_ignores_other_symbols():
    rows = [_row("000300.SH", rate=0.9, dividend_yield=0.9, volatility=0.9),
            _row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert res.ok and res.rate == 0.03


def test_resolve_absorbs_float_noise():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2),
            _row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2 + 1e-15)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert res.ok and res.volatility is not None


def test_resolve_no_matching_rows_marks_all_missing():
    res = resolve_underlying_market_params([], "000905.SH")
    assert not res.ok
    assert set(res.missing_fields) == {"rate", "dividend_yield", "volatility"}
    assert isinstance(res, UnderlyingMarketParams)
