# tests/test_hedging_tools.py
from datetime import date, datetime

from app.models import (HedgeMapEntry, Instrument, Portfolio, RiskRun, Underlying)
from app.services.quotes import record_quote
from app.tools.hedging import (get_hedgeable_underlyings_tool, propose_hedge_tool)


def _seed(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    inst = Instrument(
        symbol="IC2406.CFFEX", kind="futures", series_root="IC", exchange="CFFEX",
        contract_code="IC2406", multiplier=200.0, parent_id=u.id,
        expiry=date(2026, 6, 21), status="active", source="hedge_load")
    session.add(inst); session.flush()
    record_quote(session, instrument_id=inst.id, price=5600.0,
                 as_of=datetime.utcnow(), source="hedge_load")
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=inst.id, exchange="CFFEX",
        contract_code="IC2406", family="index_future", series_root="IC",
        instrument_type="future", reconcile_status="active"))
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 0.0,
         "vega": 0.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.commit()
    return pf


def test_get_hedgeable_tool(session):
    pf = _seed(session)
    out = get_hedgeable_underlyings_tool.invoke({"portfolio_id": pf.id})
    assert out["status"] == "ok"


def test_propose_hedge_tool(session):
    pf = _seed(session)
    out = propose_hedge_tool.invoke(
        {"portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "delta_neutral"})
    assert out["status"] == "feasible"
    assert out["legs"][0]["quantity"] == -1
