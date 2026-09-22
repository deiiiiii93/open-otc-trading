"""Call sites enqueue AFTER commit; a review can never fail a waive, a comment or a run."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from _system_one_fakes import ReviewPost
from app import database
from app.models import (
    LimitIncident,
    LimitIncidentReview,
    LimitMonitoringRun,
    Portfolio,
    RiskLimit,
    RiskLimitVersion,
    TaskRun,
)
from app.routers.limits import build_limits_router
from app.services.limits import review
from test_limits_api import NOW, _seed_monitoring_episode

#: incidents.waive checks expiry against the REAL clock; the fixture's NOW is 2026-07.
FUTURE = datetime.utcnow() + timedelta(days=5)


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


@pytest.fixture
def inline_submit(monkeypatch):
    """Run the fast path synchronously so the test can read its row."""
    ran: list[str] = []
    monkeypatch.setattr(review, "_submit", lambda fn, *a, **k: ran.append(fn(*a)))
    return ran


@pytest.fixture
def jev(monkeypatch):
    post = ReviewPost(score=3.0, nouls={"hedge_in_progress": 0.9})
    monkeypatch.setattr("app.services.system_one.client._default_post", post)
    return post


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


def _episode(session):
    portfolio = Portfolio(name="Wiring desk", base_currency="USD")
    limit = RiskLimit(key="wiring-delta", name="Wiring delta", description="", category="greek",
                      owner="market-risk", tags=[])
    session.add_all([portfolio, limit])
    session.flush()
    version = RiskLimitVersion(risk_limit_id=limit.id, version=1, state="active", metric_kind="delta",
                               source_kind="risk_run", methodology={}, scope_type="portfolio",
                               scope_config={"portfolio_ids": [portfolio.id]}, aggregation="net",
                               transform="absolute", comparator="upper", warning_upper=80.0,
                               hard_upper=100.0, unit="underlying_units", freshness_policy={},
                               effective_from=NOW - timedelta(days=1), created_at=NOW - timedelta(days=1))
    session.add(version)
    session.flush()
    limit.active_version_id = version.id
    _run, _evaluation, incident = _seed_monitoring_episode(session, portfolio=portfolio,
                                                          limit=limit, version=version)
    return portfolio, incident


def _reviews(session):
    session.expire_all()
    return list(session.scalars(select(LimitIncidentReview).order_by(LimitIncidentReview.id)))


def test_rest_waive_and_comment_enqueue_after_commit(live, inline_submit, jev, api, session):
    portfolio, incident = _episode(session)
    waived = api.post(f"/api/limit-incidents/{incident.id}/waive",
                      params={"portfolio_id": portfolio.id},
                      json={"expected_row_version": 1, "rationale": "unwinding half by Friday — LW",
                            "expires_at": FUTURE.isoformat()})
    assert waived.status_code == 200, waived.text
    assert inline_submit == ["scored"]
    commented = api.post(f"/api/limit-incidents/{incident.id}/comments",
                         params={"portfolio_id": portfolio.id},
                         json={"expected_row_version": 2, "comment": "hedge booked"})
    assert commented.status_code == 200, commented.text
    assert inline_submit == ["scored", "scored"]
    kinds = [(r.kind, r.status) for r in _reviews(session)]
    assert kinds == [("waiver", "scored"), ("thread", "scored")]
    # acknowledge/assign/resolve enqueue nothing
    acked = api.post(f"/api/limit-incidents/{incident.id}/acknowledge",
                     params={"portfolio_id": portfolio.id}, json={"expected_row_version": 3})
    assert acked.status_code == 200 and len(inline_submit) == 2


def test_tool_waive_and_comment_enqueue_after_commit(live, inline_submit, jev, session):
    from app.tools.limits import comment_limit_incident_tool, waive_limit_incident_tool
    _portfolio, incident = _episode(session)
    out = waive_limit_incident_tool.func(incident_id=incident.id, rationale="stale mark",
                                         expires_at=FUTURE.isoformat(),
                                         expected_row_version=1)
    assert out["status"] == "waived" and inline_submit == ["scored"]
    out = comment_limit_incident_tool.func(incident_id=incident.id, comment="disputing the print",
                                           expected_row_version=2)
    assert out["id"] == incident.id and inline_submit == ["scored", "scored"]


def test_a_client_that_raises_inside_the_worker_leaves_the_waive_committed(live, api, session, monkeypatch):
    portfolio, incident = _episode(session)

    def boom(*_a, **_k):
        raise RuntimeError("worker bug")

    monkeypatch.setattr(review, "_submit", boom)
    waived = api.post(f"/api/limit-incidents/{incident.id}/waive",
                      params={"portfolio_id": portfolio.id},
                      json={"expected_row_version": 1, "rationale": "r",
                            "expires_at": FUTURE.isoformat()})
    assert waived.status_code == 200, waived.text
    session.expire_all()
    assert session.get(LimitIncident, incident.id).status == "waived"
    assert _reviews(session) == []          # no row yet — still due for the next sweep (D9)


def test_a_sweep_that_raises_leaves_the_monitoring_run_completed(live, session, monkeypatch):
    """The sweep rides on _finalize; nothing it does may flip the run to failed."""
    from app.services.limits import monitoring

    calls = []

    def exploding_due(*_a, **_k):
        calls.append(1)
        raise RuntimeError("sweep bug")

    monkeypatch.setattr(review, "due_events", exploding_due)
    _episode(session)
    run = session.scalar(select(LimitMonitoringRun).order_by(LimitMonitoringRun.id.desc()))
    # Drive _finalize directly with an empty snapshot: no evaluations, so the reconciler is a no-op.
    task = TaskRun(kind="limit_monitoring", status="running", limit_monitoring_run_id=run.id)
    session.add(task)
    session.commit()
    monitoring._finalize(
        database.SessionLocal, task_id=task.id, monitoring_run_id=run.id,
        snapshot={"inputs": {"valuation_as_of": NOW.isoformat()}, "versions": [],
                  "context": {"actor": "monitor", "persona": None, "mode": "auto"}},
        groups={}, incident_reconciler=lambda *a, **k: None,
    )
    session.expire_all()
    assert session.get(LimitMonitoringRun, run.id).status == "completed"
    assert session.get(TaskRun, task.id).status == "completed"
    assert calls == [1]                      # the sweep RAN and failed, and nothing noticed
