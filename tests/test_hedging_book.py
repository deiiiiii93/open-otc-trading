# tests/test_hedging_book.py
import json
from datetime import date, datetime, timedelta

import pytest

from app.models import (
    AgentThread,
    HedgeBookingClaim,
    Instrument,
    Portfolio,
    Position,
    RiskRun,
)
from app.services.deep_agent.ledger import LedgerWriter
from app.services.deep_agent.workflow_state import ensure_thread_workflow_state
from app.services.domains import hedging_strategy as hs


def _fresh_run(session, portfolio_id: int, risk_run_id: int, *, spot: float = 5600.0):
    run = session.get(RiskRun, risk_run_id)
    if run is None:
        from app.services import hedging_greeks
        from app.services.risk_engine import _resolve_risk_positions

        portfolio = session.get(Portfolio, portfolio_id)
        resolved = _resolve_risk_positions(portfolio, session, position_ids=None)
        run = RiskRun(
            id=risk_run_id,
            portfolio_id=portfolio_id,
            status="completed",
            metrics={
                "valuation_as_of": datetime.utcnow().isoformat(),
                "position_set_hash": hedging_greeks.resolved_position_set_hash(
                    resolved
                ),
                "positions": [
                    {
                        "underlying": "000905.SH",
                        "delta_cash": 100.0,
                        "gamma_cash": 0.0,
                        "vega": 0.0,
                        "spot": spot,
                        "greeks_ok": True,
                    }
                ],
            },
            resolved_position_ids=[position.id for position in resolved],
        )
        session.add(run)
        session.flush()
    return run


def _book(session, **kwargs):
    _fresh_run(
        session,
        kwargs["portfolio_id"],
        kwargs["risk_run_id"],
        spot=kwargs.get("spot", 5600.0),
    )
    if kwargs.get("source_artifact_id") is None:
        artifact, state = _source_artifact(
            session,
            portfolio_id=kwargs["portfolio_id"],
            risk_run_id=kwargs["risk_run_id"],
            underlying=kwargs["underlying"],
            strategy=kwargs["strategy"],
            spot=kwargs["spot"],
            legs=kwargs["legs"],
        )
        source = json.loads(artifact.payload["content"])
        kwargs.update(
            source_artifact_id=artifact.id,
            workflow_id=state.domain_workflow_id,
            artifact_generated_at=artifact.payload["generated_at"],
            valuation_as_of=source["valuation_as_of"],
            risk_generated_at=source["risk_generated_at"],
            expires_at=source["expires_at"],
        )
    return hs.book_hedge(session, **kwargs)


def _source_artifact(
    session,
    *,
    portfolio_id: int,
    risk_run_id: int,
    underlying: str,
    strategy: str,
    spot: float,
    legs: list[dict],
    tool_name: str = "propose_hedge",
):
    thread = AgentThread(title="hedge proposal", character="risk_manager")
    session.add(thread)
    session.flush()
    state = ensure_thread_workflow_state(session, thread.id)
    from app.services import hedging_greeks

    fingerprint = hedging_greeks.position_set_hash(
        session, portfolio_id=portfolio_id
    )
    generated = datetime.utcnow().isoformat() + "Z"
    if tool_name == "get_hedgeable_underlyings":
        content = hedging_greeks.aggregate_by_underlying(
            session, portfolio_id=portfolio_id
        )
    else:
        content = {
            "status": "feasible",
            "portfolio_id": portfolio_id,
            "underlying": underlying,
            "strategy": strategy,
            "risk_run_id": risk_run_id,
            "spot": spot,
            "legs": legs,
            "position_set_hash": fingerprint,
            "valuation_as_of": datetime.utcnow().isoformat(),
            "risk_generated_at": generated,
            "proposed_at": generated,
            "expires_at": (datetime.utcnow() + timedelta(minutes=15)).isoformat() + "Z",
        }
    artifact = LedgerWriter(session).write_artifact(
        workflow_id=state.domain_workflow_id,
        session_id=state.orchestrator_session_id,
        kind="tool_result",
        title="hedge proposal",
        tool_name=tool_name,
        payload={
            "content": json.dumps(content, separators=(",", ":")),
            "generated_at": generated,
            "data_as_of": content["valuation_as_of"],
        },
    )
    session.flush()
    return artifact, state


def test_book_hedge_persists_tagged_legs(session):
    pf = Portfolio(name="book", base_currency="CNY")  # kind defaults to "container"
    inst = Instrument(
        symbol="IC2406.CFE", kind="futures", exchange="CFFEX", contract_code="IC2406",
        series_root="IC", expiry=date(2024, 6, 21), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
        "instrument_type": "future", "option_type": None, "strike": None,
        "multiplier": 200.0, "quantity": -3,
    }]
    out = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=42, strategy="delta_neutral", legs=legs,
                        spot=5600.0)
    session.flush()
    assert len(out["position_ids"]) == 1
    pos = session.get(Position, out["position_ids"][0])
    assert pos.source_payload["hedge"]["risk_run_id"] == 42
    assert pos.source_payload["hedge"]["is_hedge"] is True
    assert pos.source_trade_id == "HEDGE:42:1"
    assert pos.position_kind == "listed"
    assert pos.quantity == -3


def test_book_hedge_requires_instrument_id_for_listed_contracts(session):
    pf = Portfolio(name="book_requires_inst", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{
        "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
        "instrument_type": "future", "multiplier": 200.0, "quantity": -1,
    }]

    with pytest.raises(ValueError, match="must include instrument_id"):
        _book(session, portfolio_id=pf.id, underlying="000905.SH",
                      risk_run_id=41, strategy="delta_neutral", legs=legs,
                      spot=5600.0)


def test_book_hedge_canonicalizes_futures_leg_from_instrument(session):
    pf = Portfolio(name="book_canonical_future", base_currency="CNY")
    inst = Instrument(
        symbol="IC2606.CFE", kind="futures", exchange="CFFEX", contract_code="IC2606",
        series_root="IC", expiry=date(2026, 6, 22), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "CORRUPT",
        "exchange": "BAD",
        "family": "bad_family",
        "instrument_type": "future",
        "multiplier": 999.0,
        "quantity": -2,
    }]

    out = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=43, strategy="delta_neutral", legs=legs,
                        spot=8362.16)
    session.flush()
    pos = session.get(Position, out["position_ids"][0])

    assert pos.underlying == "IC2606.CFE"
    assert pos.product_type == "Futures"
    assert pos.product_kwargs["underlying"] == "IC2606.CFE"
    assert pos.product_kwargs["_otc_contract_code"] == "IC2606"
    assert pos.product_kwargs["multiplier"] == 200.0
    assert pos.underlying_id == inst.id
    assert pos.source_payload["hedge"]["hedged_underlying"] == "000905.SH"
    assert pos.source_payload["hedge"]["instrument_id"] == inst.id
    assert pos.source_payload["hedge"]["contract_code"] == "IC2606"


def test_book_hedge_persists_option_leg(session):
    pf = Portfolio(name="book_opt", base_currency="CNY")  # kind defaults to "container"
    inst = Instrument(
        symbol="IO2412C5600.CFE", kind="listed_option", exchange="CFFEX",
        contract_code="IO2412C5600", series_root="IO", expiry=date(2026, 12, 18),
        multiplier=100.0, strike=5600.0, option_type="C", status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "IO2412C5600", "exchange": "CFFEX", "family": "index_option",
        "instrument_type": "future", "option_type": None, "strike": None,
        "expiry": None, "multiplier": 999.0, "quantity": 2,
    }]
    out = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=77, strategy="delta_neutral", legs=legs,
                        spot=5600.0)
    session.flush()
    assert len(out["position_ids"]) == 1
    pos = session.get(Position, out["position_ids"][0])
    # The option Position really persisted with the gamma_vega hedge tag.
    assert pos.source_payload["hedge"]["leg_role"] == "gamma_vega"
    assert pos.source_payload["hedge"]["risk_run_id"] == 77
    assert pos.quantity == 2
    # Synthesized vanilla termsheet: contract_multiplier is a native QuantArk kwarg.
    assert pos.product_type == "EuropeanVanillaOption"
    assert pos.product_kwargs["contract_multiplier"] == 100.0
    assert pos.product_kwargs["option_type"] == "CALL"


def test_book_hedge_skips_zero_quantity_legs(session):
    pf = Portfolio(name="book2", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": 0}]
    out = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=1, strategy="delta_neutral", legs=legs, spot=5600.0)
    assert out["position_ids"] == []


def test_book_hedge_accepts_manual_strategy_tag(session):
    pf = Portfolio(name="book_manual", base_currency="CNY")
    inst = Instrument(
        symbol="IC2609.CFE", kind="futures", exchange="CFFEX", contract_code="IC2609",
        series_root="IC", expiry=date(2026, 9, 21), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    legs = [{"instrument_id": inst.id,
             "contract_code": "IC2609", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": -2}]
    out = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=20, strategy="manual", legs=legs, spot=8362.16)
    session.flush()
    pos = session.get(Position, out["position_ids"][0])
    # Desk-sized hedge: the manual tag is recorded verbatim in the hedge payload.
    assert pos.source_payload["hedge"]["strategy"] == "manual"
    assert pos.source_payload["hedge"]["is_hedge"] is True
    assert pos.source_trade_id == "HEDGE:20:1"
    assert pos.quantity == -2


def test_book_hedge_rejects_unknown_strategy(session):
    pf = Portfolio(name="book_bad_strategy", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": -1}]
    with pytest.raises(ValueError, match="Unknown hedge strategy"):
        _book(session, portfolio_id=pf.id, underlying="000905.SH",
                      risk_run_id=1, strategy="detla_neutral", legs=legs, spot=5600.0)


def test_book_hedge_refuses_second_booking_against_same_risk_run(session):
    """A booked hedge changes the portfolio, invalidating its source risk."""
    pf = Portfolio(name="book_seq", base_currency="CNY")
    inst = Instrument(
        symbol="IC2406.CFE", kind="futures", exchange="CFFEX", contract_code="IC2406",
        series_root="IC", expiry=date(2024, 6, 21), multiplier=200.0, status="active",
    )
    session.add_all([pf, inst]); session.flush()
    leg = {"instrument_id": inst.id,
           "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
           "instrument_type": "future", "multiplier": 200.0, "quantity": -2}

    first = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                          risk_run_id=21, strategy="manual", legs=[leg, dict(leg)],
                          spot=5600.0)
    session.flush()
    second = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                           risk_run_id=21, strategy="manual", legs=[dict(leg)],
                           spot=5600.0)
    session.flush()

    first_ids = [session.get(Position, pid).source_trade_id
                 for pid in first["position_ids"]]
    assert first_ids == ["HEDGE:21:1", "HEDGE:21:2"]
    assert second["status"] == "stale_hedge_proposal"
    assert "portfolio_snapshot_changed" in second["reasons"]

    # A genuinely new frozen risk snapshot can authorize the next hedge and
    # keeps its independent source-trade namespace.
    other = _book(session, portfolio_id=pf.id, underlying="000905.SH",
                          risk_run_id=2, strategy="manual", legs=[dict(leg)],
                          spot=5600.0)
    session.flush()
    other_ids = [session.get(Position, pid).source_trade_id
                 for pid in other["position_ids"]]
    assert other_ids == ["HEDGE:2:1"]


def test_booking_claim_blocks_distinct_proposals_for_same_risk_underlying(session):
    """A zero-leg first booking leaves the book unchanged, so only the DB claim
    can stop a concurrently minted proposal for the same risk boundary."""
    pf = Portfolio(name="atomic claim", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 22)

    first_artifact, first_state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=22,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
    )
    second_artifact, second_state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=22,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
    )

    def _book_from(artifact, state):
        source = json.loads(artifact.payload["content"])
        return hs.book_hedge(
            session,
            portfolio_id=pf.id,
            underlying="000905.SH",
            risk_run_id=22,
            strategy="manual",
            legs=[],
            spot=5600.0,
            source_artifact_id=artifact.id,
            workflow_id=state.domain_workflow_id,
            artifact_generated_at=artifact.payload["generated_at"],
            valuation_as_of=source["valuation_as_of"],
            risk_generated_at=source["risk_generated_at"],
            expires_at=source["expires_at"],
        )

    first = _book_from(first_artifact, first_state)
    second = _book_from(second_artifact, second_state)

    assert first["status"] == "booked"
    assert first["hedge_booking_claim_id"] > 0
    assert second["status"] == "stale_hedge_proposal"
    assert "risk_underlying_already_hedged" in second["reasons"]
    assert session.query(HedgeBookingClaim).count() == 1


def test_book_hedge_rejects_missing_risk_run(session):
    pf = Portfolio(name="missing risk", base_currency="CNY")
    session.add(pf)
    session.flush()

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=999,
        strategy="manual",
        legs=[],
        spot=5600.0,
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "risk_run_not_found" in out["reasons"]


def test_book_hedge_rejects_missing_source_artifact_for_fresh_risk(session):
    pf = Portfolio(name="missing source evidence", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 69)

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=69,
        strategy="manual",
        legs=[],
        spot=5600.0,
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "source_artifact_required" in out["reasons"]


def test_book_hedge_rejects_superseded_risk_run(session):
    pf = Portfolio(name="superseded risk", base_currency="CNY")
    session.add(pf)
    session.flush()
    old = _fresh_run(session, pf.id, 70)
    old.created_at = datetime.utcnow() - timedelta(minutes=1)
    _fresh_run(session, pf.id, 71)
    session.flush()

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=70,
        strategy="manual",
        legs=[],
        spot=5600.0,
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "risk_run_superseded" in out["reasons"]


def test_book_hedge_rejects_expired_source_artifact(session):
    pf = Portfolio(name="expired source", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 80)
    artifact, state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=80,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
        tool_name="get_hedgeable_underlyings",
    )
    content = json.loads(artifact.payload["content"])
    content["expires_at"] = (datetime.utcnow() - timedelta(seconds=1)).isoformat() + "Z"
    artifact.payload = {**artifact.payload, "content": json.dumps(content)}
    session.flush()

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=80,
        strategy="manual",
        legs=[],
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=state.domain_workflow_id,
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "source_artifact_expired" in out["reasons"]


def test_book_hedge_rejects_tampered_solver_legs(session):
    pf = Portfolio(name="tampered legs", base_currency="CNY")
    inst = Instrument(
        symbol="IC2712.CFE",
        kind="futures",
        exchange="CFFEX",
        contract_code="IC2712",
        series_root="IC",
        expiry=date(2027, 12, 17),
        multiplier=200.0,
        status="active",
    )
    session.add_all([pf, inst])
    session.flush()
    _fresh_run(session, pf.id, 90)
    legs = [
        {
            "instrument_id": inst.id,
            "contract_code": "IC2712",
            "instrument_type": "future",
            "multiplier": 200.0,
            "quantity": -1,
        }
    ]
    artifact, state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=90,
        underlying="000905.SH",
        strategy="delta_neutral",
        spot=5600.0,
        legs=legs,
    )
    tampered = [{**legs[0], "quantity": -2}]

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=90,
        strategy="delta_neutral",
        legs=tampered,
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=state.domain_workflow_id,
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "approved_payload_mismatch" in out["reasons"]


def test_book_hedge_rejects_tampered_approval_timestamp(session):
    pf = Portfolio(name="tampered approval time", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 91)
    artifact, state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=91,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
        tool_name="get_hedgeable_underlyings",
    )
    source = json.loads(artifact.payload["content"])

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=91,
        strategy="manual",
        legs=[],
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=state.domain_workflow_id,
        artifact_generated_at=artifact.payload["generated_at"],
        valuation_as_of="2026-01-01T00:00:00Z",
        risk_generated_at=source["risk_generated_at"],
        expires_at=source["expires_at"],
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "approved_timestamp_mismatch" in out["reasons"]


def test_book_hedge_persists_exact_source_evidence(session):
    pf = Portfolio(name="source evidence", base_currency="CNY")
    inst = Instrument(
        symbol="IC2712.CFE",
        kind="futures",
        exchange="CFFEX",
        contract_code="IC2712",
        series_root="IC",
        expiry=date(2027, 12, 17),
        multiplier=200.0,
        status="active",
    )
    session.add_all([pf, inst])
    session.flush()
    _fresh_run(session, pf.id, 92)
    legs = [{
        "instrument_id": inst.id,
        "contract_code": "IC2712",
        "instrument_type": "future",
        "multiplier": 200.0,
        "quantity": -1,
    }]
    artifact, state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=92,
        underlying="000905.SH",
        strategy="delta_neutral",
        spot=5600.0,
        legs=legs,
    )
    source = json.loads(artifact.payload["content"])

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=92,
        strategy="delta_neutral",
        legs=legs,
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=state.domain_workflow_id,
        artifact_generated_at=artifact.payload["generated_at"],
        valuation_as_of=source["valuation_as_of"],
        risk_generated_at=source["risk_generated_at"],
        expires_at=source["expires_at"],
    )

    assert out["status"] == "booked"
    position = session.get(Position, out["position_ids"][0])
    evidence = position.source_payload["hedge"]
    assert evidence["source_artifact_id"] == artifact.id
    assert evidence["artifact_generated_at"] == artifact.payload["generated_at"]
    assert evidence["valuation_as_of"] == source["valuation_as_of"]
    assert evidence["risk_generated_at"] == source["risk_generated_at"]
    assert evidence["expires_at"] == source["expires_at"]


def test_manual_hedge_accepts_real_guard_artifact_shape(session):
    pf = Portfolio(name="manual guard evidence", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 93)
    artifact, state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=93,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
        tool_name="get_hedgeable_underlyings",
    )
    source = json.loads(artifact.payload["content"])

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=93,
        strategy="manual",
        legs=[],
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=state.domain_workflow_id,
        artifact_generated_at=artifact.payload["generated_at"],
        valuation_as_of=source["valuation_as_of"],
        risk_generated_at=source["risk_generated_at"],
        expires_at=source["expires_at"],
    )

    assert out["status"] == "booked"
    assert out["position_ids"] == []


def test_book_hedge_rejects_source_artifact_from_another_workflow(session):
    pf = Portfolio(name="workflow scoped evidence", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 94)
    artifact, _source_state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=94,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
        tool_name="get_hedgeable_underlyings",
    )
    assert hs.desk_hedge_artifact_workflow_id(session, artifact.id) is None
    other_thread = AgentThread(title="different workflow", character="trader")
    session.add(other_thread)
    session.flush()
    other_state = ensure_thread_workflow_state(session, other_thread.id)
    source = json.loads(artifact.payload["content"])

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=94,
        strategy="manual",
        legs=[],
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=other_state.domain_workflow_id,
        artifact_generated_at=artifact.payload["generated_at"],
        valuation_as_of=source["valuation_as_of"],
        risk_generated_at=source["risk_generated_at"],
        expires_at=source["expires_at"],
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "source_artifact_workflow_mismatch" in out["reasons"]


def test_book_hedge_rejects_changed_portfolio_snapshot(session):
    pf = Portfolio(name="changed portfolio evidence", base_currency="CNY")
    session.add(pf)
    session.flush()
    _fresh_run(session, pf.id, 95)
    artifact, state = _source_artifact(
        session,
        portfolio_id=pf.id,
        risk_run_id=95,
        underlying="000905.SH",
        strategy="manual",
        spot=5600.0,
        legs=[],
        tool_name="get_hedgeable_underlyings",
    )
    source = json.loads(artifact.payload["content"])
    session.add(Position(
        portfolio_id=pf.id,
        underlying="000905.SH",
        product_type="SpotInstrument",
        quantity=1.0,
        entry_price=0.0,
        status="open",
        position_kind="listed",
    ))
    session.flush()

    out = hs.book_hedge(
        session,
        portfolio_id=pf.id,
        underlying="000905.SH",
        risk_run_id=95,
        strategy="manual",
        legs=[],
        spot=5600.0,
        source_artifact_id=artifact.id,
        workflow_id=state.domain_workflow_id,
        artifact_generated_at=artifact.payload["generated_at"],
        valuation_as_of=source["valuation_as_of"],
        risk_generated_at=source["risk_generated_at"],
        expires_at=source["expires_at"],
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "portfolio_snapshot_changed" in out["reasons"]


def test_book_hedge_rejects_risk_run_from_another_portfolio(session):
    source_pf = Portfolio(name="source portfolio", base_currency="CNY")
    target_pf = Portfolio(name="target portfolio", base_currency="CNY")
    session.add_all([source_pf, target_pf])
    session.flush()
    _fresh_run(session, source_pf.id, 96)

    out = hs.book_hedge(
        session,
        portfolio_id=target_pf.id,
        underlying="000905.SH",
        risk_run_id=96,
        strategy="manual",
        legs=[],
        spot=5600.0,
    )

    assert out["status"] == "stale_hedge_proposal"
    assert "risk_run_portfolio_mismatch" in out["reasons"]
