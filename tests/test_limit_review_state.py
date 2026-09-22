"""State is built from the EVENT (D6), numbers are computed by code (spec §Predicates)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.models import LimitIncidentEvent
from app.services.deep_agent.tool_guard_policy import EVIDENCE_LEVELS
from app.services.limits import incidents, review
from app.services.limits.contracts import LimitActionContext
from app.services.system_one.client import validate_questions
from test_limit_incidents import CONTEXT, NOW, _evaluation, _fixture, _next_run


def _open_incident(session, *, utilization=1.18):
    limit, version, run = _fixture(session)
    evaluation = _evaluation(session, version, run, status="breach", at=NOW,
                             utilization=utilization)
    result = incidents.reconcile_monitoring_incidents(
        session, monitoring_run=run, evaluations=[evaluation], context=CONTEXT,
        occurred_at=NOW)
    session.commit()
    return result.incidents[0], limit, version, run


def _waive(session, incident, *, rationale, at, days):
    incidents.waive(session, incident_id=incident.id, rationale=rationale,
                    expires_at=at + timedelta(days=days),
                    expected_row_version=incident.row_version, context=CONTEXT,
                    occurred_at=at)
    session.commit()
    session.refresh(incident)
    return [e for e in incident.events if e.event_type == "waived"][-1]


def test_every_question_validates_and_is_tagged_untested():
    validate_questions(review.WAIVER_QUESTIONS)
    validate_questions(review.THREAD_QUESTIONS)
    assert list(review.WAIVER_QUESTIONS) == [
        "rationale_grade", "position_rolling_off", "data_error", "limit_under_review",
        "hedge_in_progress", "client_flow_expected", "market_reversion", "authority_only"]
    assert list(review.THREAD_QUESTIONS) == ["thread_state"]
    assert len(review.RATIONALE_LEVELS) == 5
    assert list(review.THREAD_STATE_OPTIONS) == [
        "disputes_number", "remediating", "requests_limit_change", "requests_more_time",
        "root_cause_only", "no_position"]
    assert set(review.QUESTION_EVIDENCE) == set(review.WAIVER_QUESTIONS) | set(review.THREAD_QUESTIONS)
    assert set(review.QUESTION_EVIDENCE.values()) == {"untested"} <= EVIDENCE_LEVELS


def test_thresholds_are_the_spec_values():
    assert (review.review_rationale_chars, review.review_comment_chars,
            review.review_thread_comments, review.review_chip_min_p,
            review.review_choice_min_p, review.review_batch) == (4000, 600, 20, 0.70, 0.50, 10)
    assert review.OUTAGE_REASONS == frozenset({"no_key", "timeout", "http_error"})


def test_day_arithmetic_floors_open_days_and_ceils_duration():
    t0 = datetime(2026, 9, 1, 9, 0)
    assert review.floor_days(t0 + timedelta(days=3, hours=23), t0) == 3
    assert review.ceil_days(t0 + timedelta(days=11, hours=1), t0) == 12
    assert review.ceil_days(t0 + timedelta(days=12), t0) == 12
    assert review.floor_days(t0 - timedelta(hours=1), t0) == 0   # clamps, never negative


def test_waiver_state_is_read_from_the_event(session):
    incident, limit, version, run = _open_incident(session)
    first = _waive(session, incident, rationale="stale mark", at=NOW + timedelta(days=3), days=12)
    # Expire, re-open and re-waive: the incident's columns now hold the SECOND rationale.
    context = LimitActionContext(actor="monitor", persona=None, mode="auto")
    later_run = _next_run(session, run, at=NOW + timedelta(days=20))
    later = _evaluation(session, version, later_run, status="breach",
                        at=NOW + timedelta(days=20), utilization=1.30)
    incidents.reconcile_monitoring_incidents(session, monitoring_run=later_run,
                                             evaluations=[later], context=context,
                                             occurred_at=NOW + timedelta(days=20))
    session.commit()
    session.refresh(incident)
    _waive(session, incident, rationale="second reason", at=NOW + timedelta(days=21), days=5)
    session.refresh(incident)
    assert incident.waiver_rationale == "second reason"

    state = review.build_waiver_state(session, incident, first)
    assert state["waiver"] == {"rationale": "stale mark", "written_on": "2026-07-21",
                               "expires_on": "2026-08-02", "duration_days": 12}
    assert state["breach"] == {"severity": "breach", "utilization": 1.18, "days_open": 3}
    assert state["limit"] == {"name": limit.name, "metric_kind": "delta",
                              "unit": "underlying_units", "scope_type": "position",
                              "scope_label": "Position 7"}
    # No author identity anywhere in the state (D16).
    assert "actor" not in str(state) and "persona" not in str(state)


def test_waiver_state_severity_is_as_of_the_event_not_of_scoring(session):
    """The evaluation stamped on the latest event at or before the waive (D6)."""
    incident, _limit, version, run = _open_incident(session, utilization=1.10)
    waived = _waive(session, incident, rationale="r", at=NOW + timedelta(hours=1), days=3)
    later_run = _next_run(session, run, at=NOW + timedelta(days=1))
    later = _evaluation(session, version, later_run, status="breach",
                        at=NOW + timedelta(days=1), utilization=1.50)
    incidents.reconcile_monitoring_incidents(
        session, monitoring_run=later_run, evaluations=[later],
        context=LimitActionContext(actor="monitor", persona=None, mode="auto"),
        occurred_at=NOW + timedelta(days=1))
    session.commit()
    assert review.build_waiver_state(session, incident, waived)["breach"]["utilization"] == 1.10


def test_rationale_over_the_cap_is_rejected_never_truncated(session):
    incident, *_ = _open_incident(session)
    waived = _waive(session, incident, rationale="x" * (review.review_rationale_chars + 1),
                    at=NOW + timedelta(hours=1), days=3)
    with pytest.raises(review.ReviewStateTooLarge):
        review.build_waiver_state(session, incident, waived)


def test_thread_state_takes_the_last_twenty_comments_cut_to_600(session):
    incident, limit, *_ = _open_incident(session)
    at = NOW
    for i in range(25):
        at = at + timedelta(minutes=1)
        session.refresh(incident)
        incidents.comment(session, incident_id=incident.id, comment=f"c{i} " + "y" * 700,
                          expected_row_version=incident.row_version, context=CONTEXT,
                          occurred_at=at)
        session.commit()
    session.refresh(incident)
    comments = [e for e in incident.events if e.event_type == "commented"]
    state = review.build_thread_state(session, incident, comments[-1])
    assert len(state["comments"]) == 20
    assert state["comments"][0]["text"].startswith("c5 ")
    assert all(len(c["text"]) == 600 and c["text"].endswith("…") for c in state["comments"])
    assert state["comments"][0] == {"at": comments[5].created_at.isoformat(),
                                    "actor": "alice", "text": state["comments"][0]["text"]}
    assert state["incident"] == {"severity": "breach", "status": "open", "days_open": 0}
    assert state["limit"] == {"name": limit.name, "scope_label": "Position 7"}


def test_thread_state_for_an_older_comment_stops_at_that_comment(session):
    incident, *_ = _open_incident(session)
    incidents.comment(session, incident_id=incident.id, comment="first",
                      expected_row_version=incident.row_version, context=CONTEXT,
                      occurred_at=NOW + timedelta(minutes=1))
    session.commit()
    session.refresh(incident)
    incidents.acknowledge(session, incident_id=incident.id,
                          expected_row_version=incident.row_version, context=CONTEXT,
                          occurred_at=NOW + timedelta(minutes=2))
    session.commit()
    session.refresh(incident)
    incidents.comment(session, incident_id=incident.id, comment="second",
                      expected_row_version=incident.row_version, context=CONTEXT,
                      occurred_at=NOW + timedelta(minutes=3))
    session.commit()
    session.refresh(incident)
    first, second = [e for e in incident.events if e.event_type == "commented"]
    assert [c["text"] for c in review.build_thread_state(session, incident, first)["comments"]] == ["first"]
    assert review.build_thread_state(session, incident, first)["incident"]["status"] == "open"
    assert review.build_thread_state(session, incident, second)["incident"]["status"] == "acknowledged"


def test_status_as_of_replays_the_timeline():
    def ev(event_type):
        return LimitIncidentEvent(event_type=event_type, actor="a", payload={})
    assert review.status_as_of([ev("opened")]) == "open"
    assert review.status_as_of([ev("opened"), ev("waived"), ev("assigned")]) == "waived"
    assert review.status_as_of([ev("opened"), ev("waived"), ev("waiver_expired")]) == "open"
    assert review.status_as_of([ev("opened"), ev("resolved"), ev("reopened")]) == "open"
    assert review.status_as_of([ev("opened"), ev("recovered")]) == "recovered"


def test_latest_event_id_picks_the_newest_of_the_kind(session):
    incident, *_ = _open_incident(session)
    assert review.latest_event_id(incident, review.KIND_WAIVER) is None
    first = _waive(session, incident, rationale="a", at=NOW + timedelta(hours=1), days=1)
    assert review.latest_event_id(incident, review.KIND_WAIVER) == first.id
    assert review.latest_event_id(incident, review.KIND_THREAD) is None
