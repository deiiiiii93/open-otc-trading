import json
from datetime import date, datetime

from app.models import (AgentThread, HedgeMapEntry, Instrument, Portfolio, RiskRun,
                        Underlying)
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state
from app.services.quotes import record_quote
from app.tools.hedging import (book_hedge_tool, get_hedgeable_underlyings_tool, propose_hedge_tool)


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


def test_book_hedge_rejects_unregistered_underlying(session):
    pf = _seed(session)  # seeds 000905.SH WITHOUT the "underlying" tag
    result = book_hedge_tool.invoke({
        "portfolio_id": pf.id,
        "underlying": "000905.SH",
        "risk_run_id": 1,
        "source_artifact_id": 1,
        "artifact_generated_at": "2026-07-16T01:02:03Z",
        "valuation_as_of": "2026-07-16T01:00:00Z",
        "risk_generated_at": "2026-07-16T01:01:00Z",
        "expires_at": "2026-07-16T01:16:00Z",
        "strategy": "delta_neutral",
        "spot": 5600.0,
        "legs": [],
    })
    assert result["ok"] is False
    assert result["error"] == "underlying_not_registered"
    assert result["detail"]["symbol"] == "000905.SH"


def test_book_hedge_tool_executes_from_exact_workflow_artifact(session, settings):
    from app.services.deep_agent.cas_backend import ContentAddressedFilesystemBackend

    pf = _seed(session)
    underlying = session.query(Underlying).filter_by(symbol="000905.SH").one()
    underlying.tags = ["underlying"]
    thread = AgentThread(title="artifact backed hedge", character="trader")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    session.commit()
    config = {
        "configurable": {
            "thread_id": str(thread.id),
            "workflow_id": state.domain_workflow_id,
            "session_id": state.orchestrator_session_id,
            "envelope": "desk_workflow",
        }
    }
    proposal = propose_hedge_tool.invoke(
        {
            "portfolio_id": pf.id,
            "underlying": "000905.SH",
            "strategy": "delta_neutral",
        },
        config=config,
    )
    reference = ContentAddressedFilesystemBackend(
        root_dir=settings.artifact_dir / "artifact_blobs"
    ).capture_tool_result(
        tool_call_id="proposal-1",
        tool_name="propose_hedge",
        content=json.dumps(proposal, separators=(",", ":"), default=str),
        tool_args={
            "portfolio_id": pf.id,
            "underlying": "000905.SH",
            "strategy": "delta_neutral",
        },
        config=config,
        classification="domain_read",
    )

    result = book_hedge_tool.invoke(
        {
            "portfolio_id": pf.id,
            "underlying": "000905.SH",
            "risk_run_id": proposal["risk_run_id"],
            "source_artifact_id": reference["artifact_id"],
            "artifact_generated_at": reference["generated_at"],
            "valuation_as_of": proposal["valuation_as_of"],
            "risk_generated_at": proposal["risk_generated_at"],
            "expires_at": proposal["expires_at"],
            "strategy": proposal["strategy"],
            "spot": proposal["spot"],
            "legs": proposal["legs"],
        },
        config=config,
    )

    assert result["status"] == "booked"
    assert result["source_artifact_id"] == reference["artifact_id"]
    assert len(result["position_ids"]) == 1
