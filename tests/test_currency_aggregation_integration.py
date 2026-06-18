"""Integration test: calculate_portfolio_risk emits by_currency / shared / totals."""
import pytest


def test_calculate_portfolio_risk_emits_by_currency(monkeypatch):
    """Two positions in different currencies must NOT cross-sum."""
    from types import SimpleNamespace
    from app.services import quantark

    def fake_job(job):
        # job grew to (index, snapshot, market, pricing_failure, pricing_diagnostics);
        # tolerate the extra trailing elements.
        index, snapshot, market, *_ = job
        ccy = market.currency
        row = {"currency": ccy, "market_value": 100.0 if ccy == "CNY" else 10.0}
        contribution = quantark._empty_risk_totals()
        contribution["market_value"] = 100.0 if ccy == "CNY" else 10.0
        contribution["delta"] = 1.0
        return (index, row, contribution, 0.0)

    monkeypatch.setattr(quantark, "_calculate_position_risk_job", fake_job)

    from app.schemas import PricingEnvironmentSnapshot

    p_cny = SimpleNamespace(id=1, currency="CNY")
    p_usd = SimpleNamespace(id=2, currency="USD")
    portfolio = SimpleNamespace(positions=[p_cny, p_usd])
    markets = {
        1: PricingEnvironmentSnapshot(currency="CNY"),
        2: PricingEnvironmentSnapshot(currency="USD"),
    }
    result = quantark.calculate_portfolio_risk(portfolio, position_markets=markets)

    assert set(result["by_currency"]) == {"CNY", "USD"}
    assert result["by_currency"]["CNY"]["market_value"] == 100.0
    assert result["by_currency"]["USD"]["market_value"] == 10.0
    assert result["mixed_currency"] is True
    assert result["totals"] is None
    assert result["shared"]["delta"] == 2.0
    assert all("currency" in row for row in result["positions"])
