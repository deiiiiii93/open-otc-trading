# tests/test_hedging_greeks.py
from datetime import datetime, timedelta

import pytest

from app.models import (Instrument, Portfolio, Position, PricingParameterProfile,
                        PricingParameterRow, RiskRun, TaskRun)
from app.services import hedging_greeks


def _profile(session, symbol, *, rate=None, dividend_yield=None, volatility=None,
             rows=2):
    p = PricingParameterProfile(name="prof", valuation_date=datetime.utcnow(),
                                source_type="default_underlying", status="completed",
                                summary={})
    session.add(p); session.flush()
    for i in range(rows):
        session.add(PricingParameterRow(
            profile_id=p.id, source_trade_id=f"t{i}", symbol=symbol,
            rate=rate, dividend_yield=dividend_yield, volatility=volatility))
    session.flush()
    return p


def _run(session, pf_id, positions, created_at=None, profile_id=None):
    run = RiskRun(portfolio_id=pf_id, status="completed",
                  metrics={"positions": positions},
                  pricing_parameter_profile_id=profile_id)
    if created_at is not None:
        run.created_at = created_at
    session.add(run); session.flush()
    return run


def test_aggregate_sums_cash_greeks_by_underlying(session):
    pf = Portfolio(name="pf", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 800000.0, "gamma_cash": 60000.0,
         "vega": 4000.0, "spot": 5600.0, "greeks_ok": True},
        {"underlying": "000905.SH", "delta_cash": 440000.0, "gamma_cash": 28000.0,
         "vega": 2500.0, "spot": 5600.0, "greeks_ok": True},
        {"underlying": "000300.SH", "delta_cash": 100000.0, "gamma_cash": 9000.0,
         "vega": 1000.0, "spot": 3900.0, "greeks_ok": True},
    ])
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["status"] == "ok"
    by = {u["underlying"]: u for u in out["underlyings"]}
    assert by["000905.SH"]["targets"] == {"delta": 1240000.0, "gamma": 88000.0, "vega": 6500.0}
    assert by["000905.SH"]["spot"] == 5600.0


def test_aggregate_rolls_hedge_positions_into_hedged_underlying(session):
    pf = Portfolio(name="pf_hedged", base_currency="CNY")
    under = Instrument(symbol="000300.SH", kind="index", status="active")
    future = Instrument(
        symbol="IF2612.CFFEX",
        kind="futures",
        series_root="IF",
        exchange="CFFEX",
        contract_code="IF2612",
        multiplier=300.0,
        parent=under,
        status="active",
    )
    session.add_all([pf, under, future])
    session.flush()
    option = Position(
        portfolio_id=pf.id,
        underlying_id=under.id,
        underlying=under.symbol,
        product_type="EuropeanVanillaOption",
        quantity=-400.0,
        entry_price=0.0,
        status="open",
        position_kind="otc",
    )
    hedge = Position(
        portfolio_id=pf.id,
        underlying_id=future.id,
        underlying=future.symbol,
        product_type="Futures",
        quantity=1.0,
        entry_price=0.0,
        status="open",
        position_kind="listed",
        source_trade_id="HEDGE:5:1",
        source_payload={
            "hedge": {
                "is_hedge": True,
                "hedged_underlying": "000300.SH",
                "instrument_id": future.id,
            }
        },
    )
    session.add_all([option, hedge])
    session.flush()
    _run(session, pf.id, [
        {"position_id": option.id, "underlying": "000300.SH", "delta_cash": -981461.4786,
         "gamma_cash": -86423.6602, "vega": -3921.0296, "spot": 4931.386,
         "greeks_ok": True},
        {"position_id": hedge.id, "underlying": "IF2612.CFFEX", "delta_cash": 1422900.0,
         "gamma_cash": 0.0, "vega": 0.0, "spot": 4743.0, "greeks_ok": True},
    ])

    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)

    assert [u["underlying"] for u in out["underlyings"]] == ["000300.SH"]
    target = out["underlyings"][0]
    assert target["targets"]["delta"] == pytest.approx(441438.5214)
    assert target["targets"]["gamma"] == pytest.approx(-86423.6602)
    assert target["targets"]["vega"] == pytest.approx(-3921.0296)
    assert target["spot"] == 4931.386


def test_aggregate_reports_missing_run(session):
    pf = Portfolio(name="pf2", base_currency="CNY"); session.add(pf); session.flush()
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["status"] == "no_risk_run"


def test_aggregate_marks_run_stale_when_created_before_today(session):
    pf = Portfolio(name="pf_stale", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}],
        created_at=datetime.utcnow() - timedelta(days=1))
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["stale"] is True


def test_aggregate_marks_todays_run_fresh(session):
    pf = Portfolio(name="pf_fresh", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}],
        created_at=datetime.utcnow())
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["stale"] is False
    assert out["valuation_as_of"]
    assert out["risk_generated_at"]
    assert out["generated_at"].endswith("Z")
    assert out["expires_at"].endswith("Z")
    assert out["age_seconds"] >= 0
    assert out["position_set_hash"].startswith("sha256:")


def test_aggregate_marks_same_day_run_stale_after_action_ttl(
    session, monkeypatch
):
    pf = Portfolio(name="pf_intraday_stale", base_currency="CNY")
    session.add(pf)
    session.flush()
    _run(
        session,
        pf.id,
        [
            {
                "underlying": "000905.SH",
                "delta_cash": 5.0,
                "gamma_cash": 1.0,
                "vega": 1.0,
                "spot": 5600.0,
                "greeks_ok": True,
            }
        ],
        created_at=datetime.utcnow() - timedelta(minutes=16),
    )
    monkeypatch.setattr(
        hedging_greeks,
        "get_settings",
        lambda: type("Cfg", (), {"hedge_risk_max_age_seconds": 900})(),
    )

    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)

    assert out["stale"] is True
    assert "risk_age_exceeded" in out["stale_reasons"]


def test_aggregate_marks_historical_valuation_stale_even_when_generated_today(session):
    pf = Portfolio(name="pf_historical", base_currency="CNY")
    session.add(pf)
    session.flush()
    run = _run(
        session,
        pf.id,
        [
            {
                "underlying": "000905.SH",
                "delta_cash": 5.0,
                "gamma_cash": 1.0,
                "vega": 1.0,
                "spot": 5600.0,
                "greeks_ok": True,
            }
        ],
        created_at=datetime.utcnow(),
    )
    run.metrics = {
        **run.metrics,
        "valuation_as_of": (datetime.utcnow() - timedelta(days=1)).isoformat(),
    }
    session.flush()

    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)

    assert out["stale"] is True
    assert "historical_valuation" in out["stale_reasons"]


def test_aggregate_rejects_invalid_explicit_valuation_time(session):
    pf = Portfolio(name="pf_bad_valuation_time", base_currency="CNY")
    session.add(pf)
    session.flush()
    run = _run(
        session,
        pf.id,
        [{
            "underlying": "000905.SH",
            "delta_cash": 5.0,
            "gamma_cash": 1.0,
            "vega": 1.0,
            "spot": 5600.0,
            "greeks_ok": True,
        }],
    )
    run.metrics = {**run.metrics, "valuation_as_of": "not-a-time"}
    session.flush()

    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)

    assert out["stale"] is True
    assert "valuation_time_invalid" in out["stale_reasons"]


def test_risk_generation_time_uses_latest_completed_task(session):
    pf = Portfolio(name="pf_completed_time", base_currency="CNY")
    session.add(pf)
    session.flush()
    run = _run(
        session,
        pf.id,
        [{
            "underlying": "000905.SH",
            "delta_cash": 5.0,
            "gamma_cash": 1.0,
            "vega": 1.0,
            "spot": 5600.0,
            "greeks_ok": True,
        }],
        created_at=datetime.utcnow() - timedelta(hours=1),
    )
    older = datetime.utcnow() - timedelta(minutes=2)
    latest = datetime.utcnow() - timedelta(minutes=1)
    session.add_all([
        TaskRun(
            kind="batch_pricing",
            status="completed",
            portfolio_id=pf.id,
            risk_run_id=run.id,
            finished_at=older,
        ),
        TaskRun(
            kind="batch_pricing",
            status="completed",
            portfolio_id=pf.id,
            risk_run_id=run.id,
            finished_at=latest,
        ),
        TaskRun(
            kind="batch_pricing",
            status="failed",
            portfolio_id=pf.id,
            risk_run_id=run.id,
            finished_at=datetime.utcnow(),
        ),
    ])
    session.flush()

    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)

    assert out["risk_generated_at"] == latest.isoformat() + "Z"
    assert out["stale"] is False


def test_aggregate_skips_rows_without_greeks(session):
    pf = Portfolio(name="pf3", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True},
        {"underlying": "000905.SH", "delta_cash": 9999.0, "gamma_cash": 9999.0,
         "vega": 9999.0, "spot": 5600.0, "greeks_ok": False},
    ])
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    by = {u["underlying"]: u for u in out["underlyings"]}
    assert by["000905.SH"]["targets"]["delta"] == 5.0


def test_aggregate_attaches_profile_market(session):
    pf = Portfolio(name="pf_prof", base_currency="CNY"); session.add(pf); session.flush()
    p = _profile(session, "000905.SH", rate=0.025, dividend_yield=0.01, volatility=0.22)
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}], profile_id=p.id)
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["pricing_parameter_profile_id"] == p.id
    u = {x["underlying"]: x for x in out["underlyings"]}["000905.SH"]
    assert u["params_ok"] is True
    assert u["market"] == {"spot": 5600.0, "rate": 0.025,
                           "dividend_yield": 0.01, "volatility": 0.22}


def test_aggregate_profileless_run_marks_params_missing(session):
    pf = Portfolio(name="pf_noprof", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}])
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["pricing_parameter_profile_id"] is None
    u = {x["underlying"]: x for x in out["underlyings"]}["000905.SH"]
    assert u["params_ok"] is False
    assert u["market"] is None
    assert u["missing_params"]  # non-empty reason


def test_aggregate_conflicting_vol_refuses(session):
    pf = Portfolio(name="pf_amb", base_currency="CNY"); session.add(pf); session.flush()
    p = PricingParameterProfile(name="amb", valuation_date=datetime.utcnow(),
                                source_type="xlsx", status="completed", summary={})
    session.add(p); session.flush()
    session.add(PricingParameterRow(profile_id=p.id, source_trade_id="a",
        symbol="000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.20))
    session.add(PricingParameterRow(profile_id=p.id, source_trade_id="b",
        symbol="000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.35))
    session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}], profile_id=p.id)
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    u = {x["underlying"]: x for x in out["underlyings"]}["000905.SH"]
    assert u["params_ok"] is False
    assert any("volatility" in m for m in u["missing_params"])
