from datetime import date, datetime

from app.models import Instrument, Portfolio, RiskRun, Underlying
from app.services.domains import hedging_strategy as hs
from app.services.risk_engine import run_portfolio_risk


def test_solve_book_solve_requires_new_risk_snapshot(session):
    underlying = Underlying(
        symbol="000905.SH",
        asset_class="index",
        currency="CNY",
        status="active",
    )
    portfolio = Portfolio(name="duplicate hedge guard", base_currency="CNY")
    session.add_all([underlying, portfolio])
    session.flush()
    future = Instrument(
        symbol="IC2612.CFFEX",
        kind="futures",
        series_root="IC",
        exchange="CFFEX",
        contract_code="IC2612",
        multiplier=200.0,
        parent_id=underlying.id,
        expiry=date(2026, 12, 18),
        status="active",
    )
    session.add(future)
    session.flush()
    risk_run = RiskRun(
        portfolio_id=portfolio.id,
        status="completed",
        metrics={
            "valuation_as_of": datetime.utcnow().isoformat(),
            "positions": [{
                "underlying": underlying.symbol,
                "delta_cash": 1_120_000.0,
                "gamma_cash": 0.0,
                "vega": 0.0,
                "spot": 5600.0,
                "greeks_ok": True,
            }],
        },
    )
    session.add(risk_run)
    session.flush()
    requested_legs = [{"instrument_id": future.id, "role": "delta"}]

    first = hs.solve_hedge(
        session,
        portfolio_id=portfolio.id,
        underlying=underlying.symbol,
        strategy="delta_neutral",
        legs=requested_legs,
    )
    assert first["status"] == "feasible"
    assert first["risk_run_id"] == risk_run.id

    approved = hs.capture_desk_hedge_proposal(session, first)
    session.flush()
    workflow_id = hs.desk_hedge_artifact_workflow_id(
        session, approved["source_artifact_id"]
    )
    booked = hs.book_hedge(
        session,
        portfolio_id=portfolio.id,
        underlying=underlying.symbol,
        risk_run_id=risk_run.id,
        strategy="delta_neutral",
        legs=first["legs"],
        spot=first["spot"],
        source_artifact_id=approved["source_artifact_id"],
        workflow_id=workflow_id,
        artifact_generated_at=approved["artifact_generated_at"],
        valuation_as_of=approved["valuation_as_of"],
        risk_generated_at=approved["risk_generated_at"],
        expires_at=approved["expires_at"],
    )
    assert booked["status"] == "booked"
    assert booked["position_ids"]

    second = hs.solve_hedge(
        session,
        portfolio_id=portfolio.id,
        underlying=underlying.symbol,
        strategy="delta_neutral",
        legs=requested_legs,
    )
    assert second["status"] == "stale_risk_run"
    assert "portfolio_snapshot_changed" in second["stale_reasons"]
    assert "source_artifact_id" not in second
    assert "source_artifact_id" not in hs.capture_desk_hedge_proposal(session, second)

    duplicate = hs.book_hedge(
        session,
        portfolio_id=portfolio.id,
        underlying=underlying.symbol,
        risk_run_id=risk_run.id,
        strategy="delta_neutral",
        legs=first["legs"],
        spot=first["spot"],
        source_artifact_id=approved["source_artifact_id"],
        workflow_id=workflow_id,
        artifact_generated_at=approved["artifact_generated_at"],
        valuation_as_of=approved["valuation_as_of"],
        risk_generated_at=approved["risk_generated_at"],
        expires_at=approved["expires_at"],
    )
    assert duplicate["status"] == "stale_hedge_proposal"
    assert "portfolio_snapshot_changed" in duplicate["reasons"]

    refreshed_run = run_portfolio_risk(
        session,
        portfolio_id=portfolio.id,
        method="summary",
    )
    session.flush()
    refreshed = hs.solve_hedge(
        session,
        portfolio_id=portfolio.id,
        underlying=underlying.symbol,
        strategy="delta_neutral",
        legs=requested_legs,
    )
    assert refreshed_run.id != risk_run.id
    assert refreshed["status"] != "stale_risk_run"
