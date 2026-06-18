from app.services.risk_currency import build_currency_aware_totals


def _contrib(**kw):
    base = {
        "market_value": 0.0, "gross_notional": 0.0, "pnl": 0.0,
        "vega": 0.0, "theta": 0.0, "rho": 0.0, "rho_q": 0.0,
        "delta_cash": 0.0, "gamma_cash": 0.0, "one_day_var_proxy": 0.0,
        "delta": 0.0, "gamma": 0.0, "delta_proxy": 0.0,
    }
    base.update(kw)
    return base


def test_money_grouped_by_currency_shared_pooled():
    per_position = [
        ("CNY", _contrib(market_value=100.0, delta=2.0, one_day_var_proxy=5.0)),
        ("CNY", _contrib(market_value=50.0, delta=1.0, one_day_var_proxy=1.0)),
        ("USD", _contrib(market_value=10.0, delta=0.5, one_day_var_proxy=0.2)),
    ]
    out = build_currency_aware_totals(per_position)

    assert out["currencies"] == ["CNY", "USD"]
    assert out["by_currency"]["CNY"]["market_value"] == 150.0
    assert out["by_currency"]["CNY"]["one_day_var_proxy"] == 6.0
    assert out["by_currency"]["CNY"]["position_count"] == 2
    assert out["by_currency"]["USD"]["market_value"] == 10.0
    assert out["shared"]["delta"] == 3.5
    assert out["mixed_currency"] is True
    assert out["totals"] is None


def test_single_currency_keeps_flat_totals_for_backcompat():
    per_position = [
        ("CNY", _contrib(market_value=100.0, delta=2.0, gross_notional=200.0)),
        ("CNY", _contrib(market_value=50.0, delta=1.0, gross_notional=80.0)),
    ]
    out = build_currency_aware_totals(per_position)
    assert out["mixed_currency"] is False
    assert out["totals"]["market_value"] == 150.0
    assert out["totals"]["gross_notional"] == 280.0
    assert out["totals"]["delta"] == 3.0
    assert out["totals"]["market_value"] == out["by_currency"]["CNY"]["market_value"]
