# tests/test_risk_row_spot.py
from types import SimpleNamespace

from app.models import Portfolio, Position
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import calculate_portfolio_risk


def test_risk_row_includes_spot_and_cash_greeks(session):
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    pos = Position(
        portfolio_id=pf.id, underlying="000905.SH",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "expiry": "2026-12-31", "option_type": "call",
                        "initial_price": 100.0},
        engine_name="BlackScholesEngine", quantity=1.0, entry_price=0.0, status="open",
    )
    session.add(pos)
    session.flush()
    portfolio = SimpleNamespace(positions=[pos])
    # Use a NON-default spot (schema default is 100.0) so the assertion would
    # actually fail if the field fell back to the default instead of the run's spot.
    market = PricingEnvironmentSnapshot(spot=123.45, volatility=0.2, rate=0.03)
    result = calculate_portfolio_risk(portfolio, market)
    row = result["positions"][0]
    assert "spot" in row and row["spot"] == 123.45
    # delta_cash / gamma_cash are the other per-underlying aggregation inputs the
    # hedging engine reads from the row (already stored via RISK_GREEK_KEYS).
    assert "delta_cash" in row and "gamma_cash" in row
