"""Envelope derivation from the latest completed risk run (agent monitoring seam)."""
from datetime import datetime

import pytest

from app import models
from app.services.limits.agent_support import derive_monitoring_envelope
from app.services.limits.errors import LimitValidationError


def _mk_portfolio(session, name="AS Book"):
    p = models.Portfolio(name=name, tags=[])
    session.add(p)
    session.flush()
    return p


def test_derive_raises_without_completed_risk_run(offline_session_factory):
    with offline_session_factory() as session:
        p = _mk_portfolio(session)
        with pytest.raises(LimitValidationError):
            derive_monitoring_envelope(session, p.id)


def test_derive_raises_without_evidence_id(offline_session_factory):
    with offline_session_factory() as session:
        p = _mk_portfolio(session)
        session.add(
            models.RiskRun(
                portfolio_id=p.id,
                status="completed",
                metrics={"positions": []},
            )
        )
        session.flush()
        with pytest.raises(LimitValidationError):
            derive_monitoring_envelope(session, p.id)


def test_derive_returns_latest_run_identity(offline_session_factory):
    with offline_session_factory() as session:
        p = _mk_portfolio(session)
        stale = models.RiskRun(
            portfolio_id=p.id,
            status="completed",
            metrics={
                "valuation_as_of": "2026-06-23T15:00:00",
                "source_metadata": {
                    "effective_market_evidence_id": "risk-market-evidence/v1:old"
                },
            },
        )
        fresh = models.RiskRun(
            portfolio_id=p.id,
            status="completed",
            pricing_parameter_profile_id=None,
            engine_config_id=None,
            metrics={
                "valuation_as_of": "2026-06-24T15:00:00",
                "source_metadata": {
                    "effective_market_evidence_id": "risk-market-evidence/v1:abc"
                },
            },
        )
        session.add(stale)
        session.flush()
        session.add(fresh)
        session.flush()

        env = derive_monitoring_envelope(session, p.id)

        assert env["effective_market_evidence_id"] == "risk-market-evidence/v1:abc"
        assert env["market_snapshot_id"] is None
        assert env["max_source_age_seconds"] is None
        assert env["valuation_as_of"] == datetime(2026, 6, 24, 15, 0, 0)
        assert env["pricing_parameter_profile_id"] is None
        assert env["engine_config_id"] is None
