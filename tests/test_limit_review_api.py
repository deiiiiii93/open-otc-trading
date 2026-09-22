"""Every served review field is asserted at the HTTP layer (three swallowing layers)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from _system_one_fakes import ReviewPost
from app import database
from app.routers.limits import build_limits_router
from app.services.limits import review
from test_limit_review_wiring import _episode

EMPTY = {"waiver": None, "thread": None}
#: incidents.waive checks expiry against the REAL clock; the fixture's NOW is 2026-07.
FUTURE = datetime.utcnow() + timedelta(days=5)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    monkeypatch.setattr(review, "_submit", lambda fn, *a, **k: fn(*a))


@pytest.fixture
def api(session):
    app = FastAPI()

    def get_db():
        with database.SessionLocal() as db:
            yield db

    app.include_router(build_limits_router(get_db=get_db,
                                           dispatch_limit_monitoring_fn=lambda *_: None))
    with TestClient(app) as client:
        yield client


def test_an_unreviewed_incident_serves_explicit_nulls(api, session):
    portfolio, incident = _episode(session)
    body = api.get(f"/api/limit-incidents/{incident.id}", params={"portfolio_id": portfolio.id}).json()
    assert body["reviews"] == EMPTY
    listed = api.get("/api/limit-incidents", params={"portfolio_id": portfolio.id}).json()
    assert listed["items"][0]["reviews"] == EMPTY
    dashboard = api.get("/api/limit-monitoring/dashboard", params={"portfolio_id": portfolio.id}).json()
    assert dashboard["active_incidents"][0]["reviews"] == EMPTY


def test_get_serves_every_review_field(live, api, session, monkeypatch):
    monkeypatch.setattr("app.services.system_one.client._default_post",
                        ReviewPost(score=2.0, confidence=0.9,
                                   nouls={"data_error": 0.83, "authority_only": 0.04},
                                   choice="disputes_number", choice_p=0.7))
    portfolio, incident = _episode(session)
    waived = api.post(f"/api/limit-incidents/{incident.id}/waive", params={"portfolio_id": portfolio.id},
                      json={"expected_row_version": 1, "rationale": "stale mark on 000300",
                            "expires_at": FUTURE.isoformat()})
    assert waived.status_code == 200, waived.text
    # The action response itself already carries the review (the fast path ran inline).
    waiver = waived.json()["reviews"]["waiver"]
    assert waived.json()["reviews"]["thread"] is None
    assert set(waiver) == {
        "id", "incident_id", "event_id", "kind", "status", "unscored_reason",
        "rationale_grade", "rationale_confidence", "authority_only_p", "thread_state",
        "thread_state_p", "claims", "chip_min_p", "model", "latency_ms", "attempted_at", "created_at"}
    assert (waiver["kind"], waiver["status"], waiver["unscored_reason"]) == ("waiver", "scored", None)
    assert waiver["incident_id"] == incident.id
    assert waiver["rationale_grade"] == pytest.approx(0.5)
    assert waiver["rationale_confidence"] == 0.9 and waiver["authority_only_p"] == 0.04
    assert waiver["chip_min_p"] == 0.70 and waiver["model"] == "typesafe/jev-1.13"
    assert [c["claim"] for c in waiver["claims"]] == list(review.CLAIM_QUESTIONS)
    data_error = [c for c in waiver["claims"] if c["claim"] == "data_error"][0]
    assert data_error["p"] == 0.83 and data_error["check"] == "no_evidence"
    assert data_error["detail"] and data_error["checked_at"]
    assert waiver["thread_state"] is None and waiver["thread_state_p"] is None

    commented = api.post(f"/api/limit-incidents/{incident.id}/comments", params={"portfolio_id": portfolio.id},
                         json={"expected_row_version": 2, "comment": "the print is wrong"})
    thread = commented.json()["reviews"]["thread"]
    assert (thread["kind"], thread["thread_state"], thread["thread_state_p"]) == ("thread", "disputes_number", 0.7)
    assert thread["claims"] == [] and thread["rationale_grade"] is None

    got = api.get(f"/api/limit-incidents/{incident.id}", params={"portfolio_id": portfolio.id}).json()
    assert got["reviews"]["waiver"]["event_id"] == waiver["event_id"]
    assert got["reviews"]["thread"]["event_id"] == thread["event_id"]


def test_an_unscored_review_serves_its_reason(live, api, session, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    portfolio, incident = _episode(session)
    body = api.post(f"/api/limit-incidents/{incident.id}/waive", params={"portfolio_id": portfolio.id},
                    json={"expected_row_version": 1, "rationale": "r",
                          "expires_at": FUTURE.isoformat()}).json()
    waiver = body["reviews"]["waiver"]
    assert (waiver["status"], waiver["unscored_reason"], waiver["rationale_grade"]) == ("unscored", "no_key", None)
