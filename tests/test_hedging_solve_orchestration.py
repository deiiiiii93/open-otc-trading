# tests/test_hedging_solve_orchestration.py
from datetime import date, datetime, timedelta

from app.models import (HedgeMapEntry, Instrument, Portfolio, Position,
                        PricingParameterProfile, PricingParameterRow, RiskRun,
                        Underlying)
from app.services.domains import hedging_strategy as hs
from app.services.quotes import record_quote


def _future(session, u, code, *, mult=200.0, expiry=date(2026, 6, 21), last_price=5600.0):
    """Catalog IC future as an Instrument + an active map entry; price via quotes."""
    inst = Instrument(
        symbol=f"{code}.CFFEX", kind="futures", series_root="IC", exchange="CFFEX",
        contract_code=code, multiplier=mult, parent_id=u.id, expiry=expiry,
        status="active", source="hedge_load",
    )
    session.add(inst); session.flush()
    if last_price is not None:
        record_quote(session, instrument_id=inst.id, price=last_price,
                     as_of=datetime.utcnow(), source="hedge_load")
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=inst.id, exchange="CFFEX",
        contract_code=code, family="index_future", series_root="IC",
        instrument_type="future", reconcile_status="active"))
    session.flush()
    return inst


def _etf_option(session, u, code, *, strike, mult=10000.0, expiry, last_price=None):
    """Catalog 510500 ETF option (000905.SH's real option family) + active map."""
    inst = Instrument(
        symbol=f"{code}.SSE", kind="listed_option", series_root="510500",
        exchange="SSE", contract_code=code, option_type="C", strike=strike,
        multiplier=mult, parent_id=u.id, expiry=expiry,
        status="active", source="hedge_load",
    )
    session.add(inst); session.flush()
    if last_price is not None:
        record_quote(session, instrument_id=inst.id, price=last_price,
                     as_of=datetime.utcnow(), source="hedge_load")
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=inst.id, exchange="SSE",
        contract_code=code, family="etf_option", series_root="510500",
        instrument_type="option", option_type="C", strike=strike,
        reconcile_status="active"))
    session.flush()
    return inst


def _setup(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    inst = _future(session, u, "IC2406")
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 0.0,
         "vega": 0.0, "spot": 5600.0, "greeks_ok": True},
    ]}))
    session.flush()
    return pf, u, inst


def test_solve_hedge_delta_neutral(session):
    pf, u, inst = _setup(session)
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_neutral")
    assert out["status"] == "feasible"
    # per-contract delta_cash = 200 * 5600 = 1,120,000 ; target 1,120,000 -> q = -1
    leg = out["legs"][0]
    assert leg["quantity"] == -1
    assert out["risk_run_id"]


def test_solve_hedge_reports_no_risk_run(session):
    pf = Portfolio(name="empty", base_currency="CNY"); session.add(pf); session.flush()
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_neutral")
    assert out["status"] == "no_risk_run"


def test_solve_hedge_refuses_expired_intraday_risk(session, monkeypatch):
    from app.services import hedging_greeks

    pf, _u, _inst = _setup(session)
    run = session.query(RiskRun).filter_by(portfolio_id=pf.id).one()
    run.created_at = datetime.utcnow() - timedelta(seconds=61)
    session.flush()
    monkeypatch.setattr(
        hedging_greeks,
        "get_settings",
        lambda: type("Cfg", (), {"hedge_risk_max_age_seconds": 60})(),
    )

    out = hs.solve_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        strategy="delta_neutral",
    )

    assert out["status"] == "stale_risk_run"
    assert "risk_age_exceeded" in out["stale_reasons"]
    assert out["expires_at"].endswith("Z")


def test_solve_hedge_prices_option_leg_via_black_scholes(session):
    # Exercises the full chain INCLUDING the Black-Scholes option-greek path
    # (the future-only tests above skip it). Asserts structural properties, not
    # exact MILP lots (those depend on the real BS greeks).
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf-bs", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    _future(session, u, "IC2412", expiry=date(2026, 12, 18))
    _etf_option(session, u, "IO2412C5600", strike=5600.0, mult=100.0,
                expiry=date(2026, 12, 18), last_price=5600.0)
    prof = PricingParameterProfile(name="p", valuation_date=date.today(),
                                   source_type="default_underlying",
                                   status="completed", summary={})
    session.add(prof); session.flush()
    session.add(PricingParameterRow(profile_id=prof.id, source_trade_id="x",
        symbol="000905.SH", rate=0.03, dividend_yield=0.0,
        volatility=0.22))
    session.add(RiskRun(portfolio_id=pf.id, status="completed",
        pricing_parameter_profile_id=prof.id, metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 90000.0,
         "vega": 5000.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.flush()
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_gamma_neutral")
    assert out["status"] in ("feasible", "infeasible")
    assert out["pricing_parameter_profile_id"] == prof.id
    opt = next(l for l in out["legs"] if l["instrument_type"] == "option")
    assert opt["priced_ok"] is True
    assert opt["gamma"] != 0.0   # Black-Scholes produced a real, non-degenerate gamma


def test_solve_hedge_refuses_option_leg_without_profile(session):
    # Same instruments as the BS test, but the run has NO pricing parameter
    # profile -> option legs must refuse (excluded + warned), never default-priced.
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf-noprof", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    _future(session, u, "IC2412", expiry=date(2026, 12, 18))
    _etf_option(session, u, "IO2412C5600", strike=5600.0, mult=100.0,
                expiry=date(2026, 12, 18), last_price=5600.0)
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 90000.0,
         "vega": 5000.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.flush()
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_gamma_neutral")
    # Refused option leg is not among the solved legs, and is surfaced as a warning.
    assert [l for l in out["legs"] if l["instrument_type"] == "option"] == []
    assert any("parameter" in (w["error"] or "").lower() for w in out["warnings"])
