"""C4: downstream consumers of the risk output must tolerate the currency-aware
shape — specifically `totals is None` (mixed currency) must not crash, and
delta-proxy-based hedging must read the currency-invariant `shared` block."""
from __future__ import annotations


def _mixed_risk(delta_proxy: float = 42.0) -> dict:
    return {
        "by_currency": {
            "CNY": {"market_value": 100.0, "position_count": 1},
            "USD": {"market_value": 10.0, "position_count": 1},
        },
        "shared": {"delta": 1.0, "gamma": 0.0, "delta_proxy": delta_proxy},
        "totals": None,
        "mixed_currency": True,
        "currencies": ["CNY", "USD"],
        "positions": [],
    }


def test_recommend_hedge_uses_shared_delta_proxy_for_mixed_currency():
    from app.services.quantark import recommend_hedge

    out = recommend_hedge(_mixed_risk(delta_proxy=42.0))
    assert out["target_delta_trade"] == -42.0  # from shared, no crash on totals=None


def test_recommend_hedge_single_currency_still_works():
    from app.services.quantark import recommend_hedge

    # Single-currency: totals is populated and carries delta_proxy (merged shared).
    risk = {"totals": {"delta_proxy": 5.0}, "shared": {"delta_proxy": 5.0}}
    out = recommend_hedge(risk)
    assert out["target_delta_trade"] == -5.0


def test_recommend_hedge_no_data_no_crash():
    from app.services.quantark import recommend_hedge

    out = recommend_hedge({"totals": None})
    assert out["target_delta_trade"] == 0.0


def test_write_html_tolerates_null_totals(tmp_path):
    from app.services.reports import _write_html

    path = tmp_path / "r.html"
    payload = {"risk": {"totals": None, "positions": []}}
    _write_html(path, "Mixed", payload)  # must not raise
    # totals=None now renders the mixed-currency note instead of empty top cards.
    assert path.exists() and "Mixed currency" in path.read_text()


def test_write_xlsx_tolerates_null_totals(tmp_path):
    from app.services.reports import _write_xlsx

    path = tmp_path / "r.xlsx"
    payload = {"risk": {"totals": None, "positions": []}}
    _write_xlsx(path, "Mixed", payload)  # must not raise
    assert path.exists()
