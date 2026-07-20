# tests/test_hedging_strategy_api.py
from datetime import date, datetime

import pytest

from app.models import (
    AgentThread,
    HedgeMapEntry,
    Instrument,
    Portfolio,
    Position,
    RiskRun,
    SessionArtifact,
    Underlying,
    Workflow,
)
from app.services.quotes import record_quote


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
    return pf, u, inst


def test_hedgeable_endpoint(client, session):
    pf, u, _inst = _seed(session)
    r = client.get(f"/api/hedging/hedgeable?portfolio_id={pf.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["underlyings"][0]["underlying"] == "000905.SH"


def test_solve_endpoint(client, session):
    pf, u, _inst = _seed(session)
    r = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "delta_neutral"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "feasible"
    assert body["legs"][0]["quantity"] == -1
    assert body["source_artifact_id"] > 0
    assert body["artifact_generated_at"]
    artifact = session.get(SessionArtifact, body["source_artifact_id"])
    assert artifact is not None
    assert artifact.pinned is True
    workflow = session.get(Workflow, artifact.workflow_id)
    evidence_thread = session.get(AgentThread, workflow.thread_id)
    assert evidence_thread.source == "hedge_evidence"
    again = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "delta_neutral"})
    assert again.status_code == 200
    assert session.query(AgentThread).filter_by(source="hedge_evidence").count() == 1


def test_solve_endpoint_explains_integer_lot_band_infeasibility(client, session):
    pf, u, _inst = _seed(session)
    run = session.query(RiskRun).filter(RiskRun.portfolio_id == pf.id).one()
    run.metrics = {"positions": [
        {"underlying": "000905.SH", "delta_cash": -6035022.3, "gamma_cash": 0.0,
         "vega": 0.0, "spot": 5600.0, "greeks_ok": True}]}
    session.commit()
    client.put(f"/api/hedging/bands/{u.id}", json={
        "delta": 10000.0, "gamma": 50000.0, "vega": 10000.0})

    r = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "delta_neutral"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "infeasible"
    assert body["binding"][0]["greek"] == "delta"
    assert body["binding"][0]["shortfall"] == pytest.approx(425022.3)
    diagnostic = body["diagnostics"][0]
    assert diagnostic["kind"] == "hard_band_residual"
    assert diagnostic["greek"] == "delta"
    assert diagnostic["target"] == -6035022.3
    assert diagnostic["band"] == 10000.0
    assert diagnostic["residual"] == pytest.approx(-435022.3)
    assert diagnostic["shortfall"] == pytest.approx(425022.3)
    assert diagnostic["suggested_band"] == 435023.0
    assert diagnostic["terms"] == [{
        "contract_code": "IC2406",
        "quantity": 5,
        "per_lot": 1120000.0,
        "contribution": 5600000.0,
    }]


def test_solve_rejects_unknown_strategy(client, session):
    pf, u, _inst = _seed(session)
    r = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "bogus"})
    assert r.status_code == 422


def test_book_endpoint_rejects_missing_evidence(client, session):
    pf, u, inst = _seed(session)
    r = client.post("/api/hedging/book", json={
        "portfolio_id": pf.id, "underlying": "000905.SH",
        "risk_run_id": session.query(RiskRun).filter_by(portfolio_id=pf.id).one().id,
        "strategy": "delta_neutral", "spot": 5600.0,
        "legs": [{"instrument_id": inst.id, "contract_code": "IC2406", "exchange": "CFFEX",
                  "family": "index_future", "instrument_type": "future",
                  "multiplier": 200.0, "quantity": -1}]})
    assert r.status_code == 422


def test_solve_then_book_endpoint_uses_server_issued_evidence(client, session):
    pf, _u, _inst = _seed(session)
    solved = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id,
        "underlying": "000905.SH",
        "strategy": "delta_neutral",
    })
    assert solved.status_code == 200
    proposal = solved.json()

    booked = client.post("/api/hedging/book", json={
        "portfolio_id": proposal["portfolio_id"],
        "underlying": proposal["underlying"],
        "risk_run_id": proposal["risk_run_id"],
        "source_artifact_id": proposal["source_artifact_id"],
        "artifact_generated_at": proposal["artifact_generated_at"],
        "valuation_as_of": proposal["valuation_as_of"],
        "risk_generated_at": proposal["risk_generated_at"],
        "expires_at": proposal["expires_at"],
        "strategy": proposal["strategy"],
        "spot": proposal["spot"],
        "legs": proposal["legs"],
    })

    assert booked.status_code == 200
    assert booked.json()["status"] == "booked"
    assert booked.json()["source_artifact_id"] == proposal["source_artifact_id"]
    assert len(booked.json()["position_ids"]) == 1


def test_book_endpoint_returns_conflict_for_tampered_evidence(client, session):
    pf, _u, _inst = _seed(session)
    solved = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id,
        "underlying": "000905.SH",
        "strategy": "delta_neutral",
    })
    proposal = solved.json()

    rejected = client.post("/api/hedging/book", json={
        "portfolio_id": proposal["portfolio_id"],
        "underlying": proposal["underlying"],
        "risk_run_id": proposal["risk_run_id"],
        "source_artifact_id": proposal["source_artifact_id"],
        "artifact_generated_at": "2026-01-01T00:00:00Z",
        "valuation_as_of": proposal["valuation_as_of"],
        "risk_generated_at": proposal["risk_generated_at"],
        "expires_at": proposal["expires_at"],
        "strategy": proposal["strategy"],
        "spot": proposal["spot"],
        "legs": proposal["legs"],
    })

    assert rejected.status_code == 409
    detail = rejected.json()["detail"]
    assert detail["status"] == "stale_hedge_proposal"
    assert "approved_timestamp_mismatch" in detail["reasons"]
    assert session.query(Position).count() == 0


def test_bands_get_and_put(client, session):
    pf, u, _inst = _seed(session)
    put = client.put(f"/api/hedging/bands/{u.id}", json={
        "delta": 500000.0, "gamma": 50000.0, "vega": 10000.0})
    assert put.status_code == 200
    got = client.get(f"/api/hedging/bands?underlying_id={u.id}")
    assert got.json()["delta"] == 500000.0


def test_default_bands_get_and_put_without_underlying(client, session):
    # The portfolio-wide defaults row is addressable with no underlying_id.
    put = client.put("/api/hedging/bands", json={
        "delta": 250000.0, "gamma": 25000.0, "vega": 7000.0})
    assert put.status_code == 200
    assert put.json()["delta"] == 250000.0
    got = client.get("/api/hedging/bands")
    assert got.status_code == 200
    assert got.json() == {"delta": 250000.0, "gamma": 25000.0, "vega": 7000.0}


def test_default_bands_apply_when_underlying_has_no_override(client, session):
    pf, u, _inst = _seed(session)
    client.put("/api/hedging/bands", json={
        "delta": 123.0, "gamma": 45.0, "vega": 6.0})
    # No per-underlying override for u -> resolves to the defaults row.
    got = client.get(f"/api/hedging/bands?underlying_id={u.id}")
    assert got.json() == {"delta": 123.0, "gamma": 45.0, "vega": 6.0}
