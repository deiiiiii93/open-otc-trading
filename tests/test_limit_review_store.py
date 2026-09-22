"""Insert-or-select store, due/rotation, the outage breaker, D10, D14, inert-when-off."""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from _system_one_fakes import ReviewPost
from app import database
from app.models import AgentThread, LimitIncident, LimitIncidentReview, RiskLimitVersion
from app.services.limits import incidents, review
from app.services.limits.contracts import LimitActionContext
from test_limit_incidents import CONTEXT, NOW, _evaluation, _fixture

SCORE_AT = NOW + timedelta(days=2)
#: A REST/desk action: no thread id. test_limit_incidents.CONTEXT names thread 17, which
#: has no AgentThread row, so thread_is_arena() is None and the event is skipped (D14).
DESK = LimitActionContext(actor="alice", persona="limit_manager", mode="interactive")


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _factory():
    return database.SessionLocal()


def _open_incident(session):
    limit, version, run = _fixture(session)
    version.created_at = NOW - timedelta(days=1)
    evaluation = _evaluation(session, version, run, status="breach", at=NOW, utilization=1.2)
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=CONTEXT, occurred_at=NOW)
    session.commit()
    return result.incidents[0]


def _waive(session, incident, *, rationale="stale mark on 000300", context=DESK, at=None):
    at = at or NOW + timedelta(hours=1)
    session.refresh(incident)
    incidents.waive(session, incident_id=incident.id, rationale=rationale,
                    expires_at=at + timedelta(days=10), expected_row_version=incident.row_version,
                    context=context, occurred_at=at)
    session.commit()
    session.refresh(incident)
    return [e for e in incident.events if e.event_type == "waived"][-1].id


def _comment(session, incident, text, *, context=DESK, at=None):
    at = at or NOW + timedelta(hours=2)
    session.refresh(incident)
    incidents.comment(session, incident_id=incident.id, comment=text,
                      expected_row_version=incident.row_version, context=context, occurred_at=at)
    session.commit()
    session.refresh(incident)
    return [e for e in incident.events if e.event_type == "commented"][-1].id


def _rows(session, incident_id=None):
    session.expire_all()
    stmt = select(LimitIncidentReview).order_by(LimitIncidentReview.id)
    if incident_id is not None:
        stmt = stmt.where(LimitIncidentReview.incident_id == incident_id)
    return list(session.scalars(stmt))


def test_a_waiver_scores_grade_claims_checks_and_raw_answers(live, session):
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    post = ReviewPost(score=2.0, confidence=0.9,
                      nouls={"data_error": 0.83, "authority_only": 0.04})
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=post,
                              now=SCORE_AT) == "scored"
    [row] = _rows(session)
    assert (row.kind, row.status, row.unscored_reason) == ("waiver", "scored", None)
    assert row.rationale_grade == pytest.approx(0.5) and row.rationale_confidence == 0.9
    assert row.authority_only_p == 0.04
    assert row.model == "typesafe/jev-1.13" and row.latency_ms is not None
    assert row.attempted_at == SCORE_AT
    claims = {c["claim"]: c for c in row.claims_json}
    assert list(claims) == list(review.CLAIM_QUESTIONS)
    assert claims["data_error"]["p"] == 0.83
    assert claims["data_error"]["check"] == "no_evidence"            # checked: p >= 0.70
    assert claims["data_error"]["checked_at"] == SCORE_AT.isoformat()
    assert claims["position_rolling_off"] == {"claim": "position_rolling_off", "p": 0.05,
                                              "check": None, "detail": None, "checked_at": None}
    assert row.answers_json["rationale_grade"]["score"] == 2.0
    assert row.answers_json["data_error"] == {"p": 0.83}
    assert len(post.calls) == 1 and set(post.calls[0]["questions"]) == set(review.WAIVER_QUESTIONS)


def test_a_thread_scores_argmax_and_low_confidence_is_unscored(live, session):
    incident = _open_incident(session)
    event_id = _comment(session, incident, "we are unwinding half the book today")
    assert review.score_event(event_id, "thread", session_factory=_factory,
                              post=ReviewPost(choice="remediating", choice_p=0.8),
                              now=SCORE_AT) == "scored"
    [row] = _rows(session)
    assert (row.thread_state, row.thread_state_p, row.claims_json) == ("remediating", 0.8, [])

    low_id = _comment(session, incident, "FYI", at=NOW + timedelta(hours=3))
    assert review.score_event(low_id, "thread", session_factory=_factory,
                              post=ReviewPost(choice="no_position", choice_p=0.4),
                              now=SCORE_AT) == "low_confidence"
    low = [r for r in _rows(session) if r.event_id == low_id][0]
    assert (low.status, low.unscored_reason, low.thread_state) == ("unscored", "low_confidence", None)
    assert low.answers_json["thread_state"]["choice"] == "no_position"   # raw kept for re-thresholding


def test_state_too_large_is_a_row_fact_and_no_call_is_made(live, session):
    incident = _open_incident(session)
    event_id = _waive(session, incident, rationale="x" * 4001)
    post = ReviewPost()
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=post) == "state_too_large"
    [row] = _rows(session)
    assert (row.status, row.unscored_reason) == ("unscored", "state_too_large")
    assert post.calls == []


@pytest.mark.parametrize("exc, reason", [
    (TimeoutError("slow"), "timeout"), (RuntimeError("HTTP 500"), "http_error"),
])
def test_transport_failures_write_the_reason(live, session, exc, reason):
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    post = ReviewPost()
    post.exc = exc
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=post) == reason
    [row] = _rows(session)
    assert (row.status, row.unscored_reason, row.model) == ("unscored", reason, "typesafe/jev-1.13")


def test_no_key_is_visible_not_silent(live, session, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=ReviewPost()) == "no_key"
    assert _rows(session)[0].unscored_reason == "no_key"


def test_a_scored_row_is_never_overwritten_but_an_unscored_one_is_retried(live, session):
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    failing = ReviewPost()
    failing.exc = TimeoutError("slow")
    assert review.score_event(event_id, "waiver", session_factory=_factory, post=failing) == "timeout"
    assert review.score_event(event_id, "waiver", session_factory=_factory,
                              post=ReviewPost(score=4.0)) == "scored"
    assert review.score_event(event_id, "waiver", session_factory=_factory,
                              post=ReviewPost(score=0.0)) == "exists"
    [row] = _rows(session)
    assert row.status == "scored" and row.rationale_grade == pytest.approx(1.0)


def test_two_workers_on_one_event_leave_one_row(live, session, monkeypatch):
    """Simulated race: the loser's INSERT hits the unique key and loads the winner."""
    incident = _open_incident(session)
    event_id = _waive(session, incident)
    original = review._write

    def racing_write(session_, **kwargs):
        # Someone else inserts the same (event_id, kind) just before we do.
        with _factory() as other:
            original(other, **{**kwargs, "values": {**kwargs["values"], "rationale_grade": 0.25}})
            other.commit()
        return original(session_, **kwargs)

    monkeypatch.setattr(review, "_write", racing_write)
    assert review.score_event(event_id, "waiver", session_factory=_factory,
                              post=ReviewPost(score=4.0)) == "scored"
    [row] = _rows(session)
    assert row.rationale_grade == 0.25       # the winner's row stands; ours was not applied


def test_arena_events_are_skipped_and_unknown_threads_are_skipped_too(live, session):
    arena = AgentThread(title="[arena] x", character="risk_manager", source="arena", arena_run_id=1)
    session.add(arena)
    session.commit()
    incident = _open_incident(session)
    arena_ctx = LimitActionContext(actor="agent", persona=None, mode="auto", thread_id=arena.id)
    arena_event = _waive(session, incident, context=arena_ctx)
    post = ReviewPost()
    assert review.score_event(arena_event, "waiver", session_factory=_factory, post=post) == "skipped"
    unknown_ctx = LimitActionContext(actor="agent", persona=None, mode="auto", thread_id=999_999)
    unknown_event = _comment(session, incident, "c", context=unknown_ctx)
    assert review.score_event(unknown_event, "thread", session_factory=_factory, post=post) == "skipped"
    assert _rows(session) == [] and post.calls == []
    with _factory() as s:
        # arena excluded in SQL; an unresolvable thread stays due (retried each sweep)
        assert review.due_events(s, 10) == [(unknown_event, "thread")]


def test_due_is_a_database_fact_and_only_the_latest_comment_is_due(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    c1 = _comment(session, incident, "first", at=NOW + timedelta(hours=2))
    c2 = _comment(session, incident, "second", at=NOW + timedelta(hours=3))
    with _factory() as s:
        assert review.due_events(s, 10) == [(w1, "waiver"), (c2, "thread")]
    assert review.score_event(w1, "waiver", session_factory=_factory, post=ReviewPost()) == "scored"
    with _factory() as s:
        assert review.due_events(s, 10) == [(c2, "thread")]
        assert c1 not in [e for e, _k in review.due_events(s, 10)]


def test_rotation_never_attempted_first_then_oldest_attempt(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident, at=NOW + timedelta(hours=1))
    failing = ReviewPost()
    failing.exc = TimeoutError("slow")
    assert review.score_event(w1, "waiver", session_factory=_factory, post=failing,
                              now=SCORE_AT) == "timeout"
    c1 = _comment(session, incident, "later", at=NOW + timedelta(hours=2))
    with _factory() as s:
        assert review.due_events(s, 10) == [(c1, "thread"), (w1, "waiver")]


def test_sweep_ends_the_batch_on_an_outage_and_continues_on_a_row_reason(live, session):
    incident = _open_incident(session)
    _waive(session, incident, rationale="bad body", at=NOW + timedelta(hours=1))
    _comment(session, incident, "c", at=NOW + timedelta(hours=2))
    post = ReviewPost()
    post.bad_for_rationale = {"bad body"}
    counters = review.sweep(_factory, post=post, now=SCORE_AT)
    assert counters == {"scored": 1, "unscored": 1, "skipped": 0, "rechecked": 0}
    assert {r.unscored_reason for r in _rows(session)} == {"bad_response", None}

    # Two more due events on the same incident (a waived incident may be re-waived):
    # the FIRST outage ends the batch, so only one request is made.
    _waive(session, incident, rationale="second", at=NOW + timedelta(hours=3))
    _comment(session, incident, "c2", at=NOW + timedelta(hours=4))
    outage = ReviewPost()
    outage.exc = TimeoutError("slow")
    counters = review.sweep(_factory, post=outage, now=SCORE_AT)
    assert counters["unscored"] == 1 and counters["scored"] == 0
    assert len(outage.calls) == 1                     # one failed request per sweep


def test_sweep_recomputes_checks_for_the_current_waiver_only(live, session):
    incident = _open_incident(session)
    _waive(session, incident)
    review.sweep(_factory, post=ReviewPost(nouls={"limit_under_review": 0.9}), now=SCORE_AT)
    [row] = _rows(session)
    assert [c for c in row.claims_json if c["claim"] == "limit_under_review"][0]["check"] == "no_evidence"
    # A new limit version appears; the next sweep flips the check without re-asking Jev.
    session.add(RiskLimitVersion(risk_limit_id=incident.risk_limit_id, version=2, state="draft",
                                 metric_kind="delta", source_kind="risk_run", methodology={},
                                 scope_type="position", scope_config={"position_ids": [7]},
                                 aggregation="net", transform="absolute", comparator="upper",
                                 hard_upper=150.0, unit="underlying_units",
                                 created_at=NOW + timedelta(days=1)))
    session.commit()
    post = ReviewPost()
    counters = review.sweep(_factory, post=post, now=SCORE_AT + timedelta(days=1))
    assert counters["rechecked"] == 1 and post.calls == []
    [row] = _rows(session)
    check = [c for c in row.claims_json if c["claim"] == "limit_under_review"][0]
    assert check["check"] == "supported"
    assert check["checked_at"] == (SCORE_AT + timedelta(days=1)).isoformat()
    assert row.answers_json["limit_under_review"] == {"p": 0.9}   # Jev answer untouched


@pytest.mark.parametrize("setup", [
    lambda mp: mp.setenv("OPEN_OTC_SYSTEM_ONE", "false"),
    lambda mp: mp.setenv("OPEN_OTC_LIMIT_REVIEW", "false"),
])
def test_inert_when_either_switch_is_off(live, session, monkeypatch, setup):
    setup(monkeypatch)
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    _comment(session, incident, "c")
    post = ReviewPost()
    assert review.score_event(w1, "waiver", session_factory=_factory, post=post) == "skipped"
    assert review.enqueue(w1, "waiver", submit=lambda fn, *a, **k: fn(*a)) is False
    assert review.sweep(_factory, post=post) == {"scored": 0, "unscored": 0, "skipped": 0, "rechecked": 0}
    assert _rows(session) == [] and post.calls == []


def test_enqueue_runs_score_event_through_submit(live, session, monkeypatch):
    incident = _open_incident(session)
    w1 = _waive(session, incident)
    ran = []
    monkeypatch.setattr("app.services.system_one.client._default_post", ReviewPost())
    assert review.enqueue(w1, "waiver", submit=lambda fn, *a, **k: ran.append(fn(*a))) is True
    assert ran == ["scored"]
    assert review.enqueue_latest(incident, "thread", submit=lambda fn, *a, **k: fn(*a)) is False


def test_an_internal_error_is_recorded_and_never_raised(live, session, monkeypatch):
    incident = _open_incident(session)
    w1 = _waive(session, incident)

    def boom(*_a, **_k):
        raise RuntimeError("bug")

    monkeypatch.setattr(review, "build_waiver_state", boom)
    assert review.score_event(w1, "waiver", session_factory=_factory, post=ReviewPost()) == "internal_error"
    [row] = _rows(session)
    assert (row.status, row.unscored_reason) == ("unscored", "internal_error")


def test_latest_reviews_returns_the_highest_event_per_kind(live, session):
    incident = _open_incident(session)
    w1 = _waive(session, incident, at=NOW + timedelta(hours=1))
    review.score_event(w1, "waiver", session_factory=_factory, post=ReviewPost(score=1.0))
    # Expire the waiver and re-waive (the service path: a monitoring run re-opens it).
    with _factory() as s:
        row = s.get(LimitIncident, incident.id)
        incidents._update(s, incident=row, expected_row_version=None, values={"status": "open"})
        s.commit()
    w2 = _waive(session, incident, at=NOW + timedelta(days=1))
    review.score_event(w2, "waiver", session_factory=_factory, post=ReviewPost(score=4.0))
    with _factory() as s:
        lookup = review.latest_reviews(s, [incident.id, 999])
        assert lookup[incident.id]["waiver"].event_id == w2
        assert lookup[incident.id]["thread"] is None
        assert lookup[999] == {"waiver": None, "thread": None}
