from app.services.fx import convert_risk_currency


def _rate_lookup(base, quote):
    table = {("CNY", "USD"): 0.14, ("USD", "USD"): 1.0}
    return table.get((base, quote))


def test_converts_money_sums_into_target_and_lists_missing():
    by_currency = {
        "CNY": {"market_value": 100.0, "vega": 10.0, "position_count": 2},
        "USD": {"market_value": 5.0, "vega": 1.0, "position_count": 1},
        "JPY": {"market_value": 9.0, "vega": 0.0, "position_count": 1},  # no rate
    }
    out = convert_risk_currency(by_currency, "USD", _rate_lookup)
    assert out["totals"]["market_value"] == 19.0  # 100*0.14 + 5
    assert out["totals"]["vega"] == 10.0 * 0.14 + 1.0
    assert out["fx_rates_used"]["CNY->USD"] == 0.14
    assert out["missing"] == ["JPY->USD"]
    assert out["totals"]["position_count"] == 3  # CNY(2)+USD(1); JPY excluded


def test_identity_only():
    by_currency = {"USD": {"market_value": 7.0, "position_count": 1}}
    out = convert_risk_currency(by_currency, "USD", _rate_lookup)
    assert out["totals"]["market_value"] == 7.0
    assert out["missing"] == []
