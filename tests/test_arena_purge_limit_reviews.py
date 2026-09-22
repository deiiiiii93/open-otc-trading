"""A review row on an arena fixture incident must never wedge the post-board purge."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from app.models import LimitIncident, LimitIncidentEvent, LimitIncidentReview, Portfolio
from app.services.arena.runner import _delete_portfolios_with_dependents
from test_limit_review_wiring import _episode


def test_purge_deletes_reviews_before_events_and_incidents(session):
    portfolio, incident = _episode(session)
    event = session.scalar(select(LimitIncidentEvent).where(LimitIncidentEvent.incident_id == incident.id))
    session.add(LimitIncidentReview(incident_id=incident.id, event_id=event.id, kind="waiver",
                                    status="unscored", unscored_reason="no_key", claims_json=[],
                                    answers_json={}, created_at=datetime(2026, 9, 22)))
    session.commit()
    _delete_portfolios_with_dependents(session, [portfolio.id])
    session.commit()
    assert session.scalar(select(LimitIncidentReview.id)) is None
    assert session.scalar(select(LimitIncidentEvent.id)) is None
    assert session.scalar(select(LimitIncident.id)) is None
    # session.get would answer from the identity map; ask the database.
    assert session.scalar(select(Portfolio.id).where(Portfolio.id == portfolio.id)) is None
