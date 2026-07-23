from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.limits.contracts import LimitActionContext
from app.services.limits.errors import LimitConflictError


CONTEXT = LimitActionContext(
    actor="alice",
    persona="limit_manager",
    mode="interactive",
    thread_id=17,
    audit_ref="audit-limit-17",
)
NOW = datetime(2026, 7, 18, 9, 0)


def _fixture(session):
    from app.models import LimitMonitoringRun, Portfolio, RiskLimit, RiskLimitVersion

    portfolio = Portfolio(name="Incident desk", base_currency="USD")
    limit = RiskLimit(
        key="incident-delta",
        name="Incident delta",
        description="",
        category="greek",
        owner="market-risk",
        tags=[],
    )
    session.add_all([portfolio, limit])
    session.flush()
    version = RiskLimitVersion(
        risk_limit_id=limit.id,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="position",
        scope_config={"position_ids": [7]},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=80.0,
        hard_upper=100.0,
        unit="underlying_units",
        freshness_policy={"max_age_seconds": 60},
    )
    run = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=NOW,
        source_policy="reuse_only",
        status="running",
        summary={},
        definition_snapshot={},
        definition_snapshot_hash="0" * 64,
    )
    session.add_all([version, run])
    session.flush()
    return limit, version, run


def _evaluation(session, version, run, *, status: str, at: datetime, utilization=1.0):
    from app.models import LimitEvaluation

    evaluation = LimitEvaluation(
        monitoring_run_id=run.id,
        limit_version_id=version.id,
        scope_type="position",
        scope_key="position:7",
        scope_label="Position 7",
        observed_value=utilization * 100,
        adverse_value=utilization * 100,
        warning_upper=80.0,
        hard_upper=100.0,
        utilization=utilization,
        headroom=100 - utilization * 100,
        status=status,
        evidence={"source": "test"},
        evaluated_at=at,
    )
    session.add(evaluation)
    session.flush()
    return evaluation


def _next_run(session, run, *, at: datetime):
    from app.models import LimitMonitoringRun

    active_runs = (
        session.query(LimitMonitoringRun)
        .filter(
            LimitMonitoringRun.portfolio_id == run.portfolio_id,
            LimitMonitoringRun.status.in_(("queued", "running")),
        )
        .all()
    )
    for active_run in active_runs:
        active_run.status = "completed"
        active_run.finished_at = at
    session.flush()
    next_run = LimitMonitoringRun(
        trigger=run.trigger,
        mode=run.mode,
        portfolio_id=run.portfolio_id,
        valuation_as_of=at,
        source_policy=run.source_policy,
        status="running",
        summary={},
        definition_snapshot={},
        definition_snapshot_hash="0" * 64,
    )
    session.add(next_run)
    session.flush()
    return next_run


def _reconcile(session, run, evaluations, *, at=NOW):
    from app.services.limits.incidents import reconcile_monitoring_incidents

    return reconcile_monitoring_incidents(
        session,
        monitoring_run=run,
        evaluations=evaluations,
        context=CONTEXT,
        occurred_at=at,
    )


def test_reconciliation_opens_repeats_escalates_and_recovers(session) -> None:
    from app.models import AuditEvent, LimitIncidentEvent

    _limit, version, run = _fixture(session)
    warning = _evaluation(
        session, version, run, status="warning", at=NOW, utilization=0.8
    )
    first = _reconcile(session, run, [warning])
    incident = first.incidents[0]

    assert incident.status == "open"
    assert incident.severity == "warning"
    assert incident.owner == "market-risk"
    assert incident.first_evaluation_id == warning.id
    assert [(event.event_type, event.evaluation_id) for event in first.events] == [
        ("opened", warning.id)
    ]
    opened = first.events[0]
    assert (
        opened.actor,
        opened.persona,
        opened.mode,
        opened.thread_id,
        opened.audit_ref,
    ) == ("alice", "limit_manager", "interactive", 17, "audit-limit-17")

    repeated_run = _next_run(session, run, at=NOW + timedelta(minutes=1))
    repeated = _evaluation(
        session,
        version,
        repeated_run,
        status="warning",
        at=NOW + timedelta(minutes=1),
        utilization=0.8,
    )
    _reconcile(session, repeated_run, [repeated], at=NOW + timedelta(minutes=1))
    utilization_run = _next_run(session, run, at=NOW + timedelta(minutes=2))
    utilization_escalation = _evaluation(
        session,
        version,
        utilization_run,
        status="warning",
        at=NOW + timedelta(minutes=2),
        utilization=0.85,
    )
    _reconcile(
        session,
        utilization_run,
        [utilization_escalation],
        at=NOW + timedelta(minutes=2),
    )
    escalation_run = _next_run(session, run, at=NOW + timedelta(minutes=3))
    escalation = _evaluation(
        session,
        version,
        escalation_run,
        status="breach",
        at=NOW + timedelta(minutes=3),
        utilization=1.1,
    )
    _reconcile(session, escalation_run, [escalation], at=NOW + timedelta(minutes=3))
    recovery_run = _next_run(session, run, at=NOW + timedelta(minutes=4))
    recovery = _evaluation(
        session,
        version,
        recovery_run,
        status="ok",
        at=NOW + timedelta(minutes=4),
        utilization=0.3,
    )
    _reconcile(session, recovery_run, [recovery], at=NOW + timedelta(minutes=4))

    session.refresh(incident)
    events = session.query(LimitIncidentEvent).filter_by(incident_id=incident.id).all()
    assert [event.event_type for event in events] == [
        "opened",
        "repeated",
        "escalated",
        "escalated",
        "recovered",
    ]
    assert incident.status == "recovered"
    assert incident.last_evaluation_id == recovery.id
    assert incident.row_version == 5
    assert (
        session.query(AuditEvent)
        .filter_by(event_type="limit_incident.opened", subject_id=str(incident.id))
        .one()
        .payload["audit_ref"]
        == "audit-limit-17"
    )


def test_unknown_does_not_open_or_recover_a_risk_breach_incident(session) -> None:
    from app.models import LimitIncident

    _limit, version, run = _fixture(session)
    unknown = _evaluation(session, version, run, status="unknown", at=NOW)

    assert _reconcile(session, run, [unknown]).incidents == ()
    assert session.query(LimitIncident).count() == 0

    breach_run = _next_run(session, run, at=NOW + timedelta(minutes=1))
    breach = _evaluation(
        session,
        version,
        breach_run,
        status="breach",
        at=NOW + timedelta(minutes=1),
    )
    incident = _reconcile(session, breach_run, [breach]).incidents[0]
    unknown_run = _next_run(session, run, at=NOW + timedelta(minutes=2))
    later_unknown = _evaluation(
        session,
        version,
        unknown_run,
        status="unknown",
        at=NOW + timedelta(minutes=2),
    )
    assert _reconcile(session, unknown_run, [later_unknown]).events == ()
    session.refresh(incident)
    assert incident.status == "open"
    assert incident.last_evaluation_id == breach.id


@pytest.mark.parametrize("stale_status", ["ok", "warning", "breach"])
def test_stale_cross_run_observation_cannot_mutate_active_episode(
    session,
    stale_status: str,
) -> None:
    _limit, version, base_run = _fixture(session)
    newer_run = _next_run(
        session,
        base_run,
        at=NOW + timedelta(hours=2),
    )
    newer = _evaluation(
        session,
        version,
        newer_run,
        status="breach",
        at=NOW + timedelta(hours=2),
        utilization=1.1,
    )
    incident = _reconcile(
        session,
        newer_run,
        [newer],
        at=NOW + timedelta(hours=2),
    ).incidents[0]
    original_version = incident.row_version

    stale_run = _next_run(session, base_run, at=NOW + timedelta(hours=1))
    stale = _evaluation(
        session,
        version,
        stale_run,
        status=stale_status,
        at=NOW + timedelta(hours=3),
        utilization=1.5,
    )
    result = _reconcile(
        session,
        stale_run,
        [stale],
        at=NOW + timedelta(hours=3),
    )

    session.refresh(incident)
    assert result.events == ()
    assert incident.status == "open"
    assert incident.severity == "breach"
    assert incident.last_evaluation_id == newer.id
    assert incident.row_version == original_version


def test_stale_breach_cannot_open_after_newer_evidence_recovered(session) -> None:
    from app.models import LimitIncident

    _limit, version, base_run = _fixture(session)
    breach_run = _next_run(session, base_run, at=NOW + timedelta(hours=2))
    breach = _evaluation(
        session,
        version,
        breach_run,
        status="breach",
        at=NOW + timedelta(hours=2),
    )
    incident = _reconcile(session, breach_run, [breach]).incidents[0]
    recovery_run = _next_run(
        session,
        base_run,
        at=NOW + timedelta(hours=3),
    )
    recovery = _evaluation(
        session,
        version,
        recovery_run,
        status="ok",
        at=NOW + timedelta(hours=3),
    )
    _reconcile(session, recovery_run, [recovery])
    session.refresh(incident)
    recovered_version = incident.row_version

    stale_run = _next_run(session, base_run, at=NOW + timedelta(hours=1))
    stale = _evaluation(
        session,
        version,
        stale_run,
        status="breach",
        at=NOW + timedelta(hours=4),
        utilization=1.5,
    )
    result = _reconcile(session, stale_run, [stale])

    session.refresh(incident)
    assert result.incidents == ()
    assert result.events == ()
    assert incident.status == "recovered"
    assert incident.last_evaluation_id == recovery.id
    assert incident.row_version == recovered_version
    assert session.query(LimitIncident).count() == 1


def test_cross_run_order_uses_evaluated_at_then_id_as_tie_breakers(session) -> None:
    _limit, version, base_run = _fixture(session)
    valuation = NOW + timedelta(hours=1)
    first_run = _next_run(session, base_run, at=valuation)
    first = _evaluation(
        session,
        version,
        first_run,
        status="warning",
        at=valuation,
        utilization=0.8,
    )
    incident = _reconcile(session, first_run, [first]).incidents[0]

    later_time_run = _next_run(session, base_run, at=valuation)
    later_time = _evaluation(
        session,
        version,
        later_time_run,
        status="warning",
        at=valuation + timedelta(minutes=1),
        utilization=0.85,
    )
    assert _reconcile(session, later_time_run, [later_time]).events

    stale_time_run = _next_run(session, base_run, at=valuation)
    stale_time = _evaluation(
        session,
        version,
        stale_time_run,
        status="breach",
        at=valuation + timedelta(seconds=30),
        utilization=1.5,
    )
    assert _reconcile(session, stale_time_run, [stale_time]).events == ()

    same_time_run = _next_run(session, base_run, at=valuation)
    same_time = _evaluation(
        session,
        version,
        same_time_run,
        status="breach",
        at=later_time.evaluated_at,
        utilization=1.1,
    )
    assert same_time.id > later_time.id
    assert _reconcile(session, same_time_run, [same_time]).events

    session.refresh(incident)
    current_version = incident.row_version
    assert _reconcile(session, later_time_run, [later_time]).events == ()
    session.refresh(incident)
    assert incident.last_evaluation_id == same_time.id
    assert incident.row_version == current_version


def test_actions_are_versioned_and_events_are_immutable(session) -> None:
    from app.models import LimitIncidentEvent
    from app.services.limits import incidents

    _limit, version, run = _fixture(session)
    breach = _evaluation(session, version, run, status="breach", at=NOW)
    incident = _reconcile(session, run, [breach]).incidents[0]
    opened_payload = dict(incident.events[0].payload)

    incident = incidents.acknowledge(
        session,
        incident_id=incident.id,
        expected_row_version=1,
        context=CONTEXT,
        occurred_at=NOW + timedelta(seconds=1),
    )
    incident = incidents.assign(
        session,
        incident_id=incident.id,
        assignee="bob",
        expected_row_version=2,
        context=CONTEXT,
        occurred_at=NOW + timedelta(seconds=2),
    )
    incident = incidents.comment(
        session,
        incident_id=incident.id,
        comment="Desk is investigating",
        expected_row_version=3,
        context=CONTEXT,
        occurred_at=NOW + timedelta(seconds=3),
    )
    incident = incidents.waive(
        session,
        incident_id=incident.id,
        rationale="Temporary hedge roll",
        expires_at=NOW + timedelta(minutes=5),
        expected_row_version=4,
        context=CONTEXT,
        occurred_at=NOW + timedelta(seconds=4),
    )

    assert incident.status == "waived"
    assert incident.assignee == "bob"
    assert incident.row_version == 5
    events = session.query(LimitIncidentEvent).filter_by(incident_id=incident.id).all()
    assert [event.event_type for event in events] == [
        "opened",
        "acknowledged",
        "assigned",
        "commented",
        "waived",
    ]
    assert incident.events[0].payload == opened_payload

    with pytest.raises(LimitConflictError):
        incidents.assign(
            session,
            incident_id=incident.id,
            assignee="stale",
            expected_row_version=4,
            context=CONTEXT,
        )


def test_waiver_expiry_manual_resolution_reopen_and_later_breach(session) -> None:
    from app.services.limits import incidents

    _limit, version, run = _fixture(session)
    first_breach = _evaluation(session, version, run, status="breach", at=NOW)
    incident = _reconcile(session, run, [first_breach]).incidents[0]
    incident = incidents.waive(
        session,
        incident_id=incident.id,
        rationale="Approved until close",
        expires_at=NOW + timedelta(minutes=1),
        expected_row_version=1,
        context=CONTEXT,
        occurred_at=NOW + timedelta(seconds=1),
    )
    expiry_run = _next_run(session, run, at=NOW + timedelta(minutes=2))
    after_expiry = _evaluation(
        session,
        version,
        expiry_run,
        status="breach",
        at=NOW + timedelta(minutes=2),
        utilization=1.2,
    )
    _reconcile(session, expiry_run, [after_expiry], at=NOW + timedelta(minutes=2))
    session.refresh(incident)
    assert incident.status == "open"
    assert [event.event_type for event in incident.events] == [
        "opened",
        "waived",
        "waiver_expired",
        "escalated",
    ]

    incident = incidents.resolve(
        session,
        incident_id=incident.id,
        expected_row_version=4,
        context=CONTEXT,
        occurred_at=NOW + timedelta(minutes=3),
    )
    assert incident.status == "resolved"
    incident = incidents.reopen(
        session,
        incident_id=incident.id,
        expected_row_version=5,
        context=CONTEXT,
        occurred_at=NOW + timedelta(minutes=4),
    )
    later_run = _next_run(session, run, at=NOW + timedelta(minutes=5))
    later_breach = _evaluation(
        session,
        version,
        later_run,
        status="breach",
        at=NOW + timedelta(minutes=5),
        utilization=1.3,
    )
    _reconcile(session, later_run, [later_breach], at=NOW + timedelta(minutes=5))

    assert incident.status == "open"
    assert [event.event_type for event in incident.events] == [
        "opened",
        "waived",
        "waiver_expired",
        "escalated",
        "resolved",
        "reopened",
        "escalated",
    ]


def test_later_breach_after_recovery_opens_one_new_episode(session) -> None:
    from app.models import LimitIncident

    _limit, version, run = _fixture(session)
    breach = _evaluation(session, version, run, status="breach", at=NOW)
    first = _reconcile(session, run, [breach]).incidents[0]
    clean_run = _next_run(session, run, at=NOW + timedelta(minutes=1))
    clean = _evaluation(
        session, version, clean_run, status="ok", at=NOW + timedelta(minutes=1)
    )
    _reconcile(session, clean_run, [clean], at=NOW + timedelta(minutes=1))
    later_run = _next_run(session, run, at=NOW + timedelta(minutes=2))
    later = _evaluation(
        session, version, later_run, status="breach", at=NOW + timedelta(minutes=2)
    )
    second = _reconcile(
        session, later_run, [later], at=NOW + timedelta(minutes=2)
    ).incidents[0]

    assert first.id != second.id
    assert (
        session.query(LimitIncident)
        .filter(
            LimitIncident.risk_limit_id == first.risk_limit_id,
            LimitIncident.scope_key == "position:7",
            LimitIncident.status.in_(("open", "acknowledged", "assigned", "waived")),
        )
        .count()
        == 1
    )


def test_same_limit_and_scope_open_independent_portfolio_episodes(session) -> None:
    from app.models import LimitIncident, LimitMonitoringRun, Portfolio

    _limit, version, first_run = _fixture(session)
    first_evaluation = _evaluation(
        session,
        version,
        first_run,
        status="breach",
        at=NOW,
    )
    first_evaluation.scope_type = "underlying"
    first_evaluation.scope_key = "underlying:SPX"
    first_evaluation.scope_label = "SPX"
    session.flush()
    first = _reconcile(session, first_run, [first_evaluation]).incidents[0]

    second_portfolio = Portfolio(
        name="Second incident desk",
        base_currency="USD",
    )
    session.add(second_portfolio)
    session.flush()
    second_run = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=second_portfolio.id,
        valuation_as_of=NOW,
        source_policy="reuse_only",
        status="running",
        summary={},
        definition_snapshot={},
        definition_snapshot_hash="2" * 64,
    )
    session.add(second_run)
    session.flush()
    second_evaluation = _evaluation(
        session,
        version,
        second_run,
        status="breach",
        at=NOW,
    )
    second_evaluation.scope_type = "underlying"
    second_evaluation.scope_key = "underlying:SPX"
    second_evaluation.scope_label = "SPX"
    session.flush()
    second = _reconcile(
        session,
        second_run,
        [second_evaluation],
    ).incidents[0]

    assert first.id != second.id
    assert {first.portfolio_id, second.portfolio_id} == {
        first_run.portfolio_id,
        second_run.portfolio_id,
    }
    assert (
        session.query(LimitIncident)
        .filter(
            LimitIncident.risk_limit_id == version.risk_limit_id,
            LimitIncident.scope_key == "underlying:SPX",
            LimitIncident.status == "open",
        )
        .count()
        == 2
    )


def test_reopen_rejects_a_terminal_episode_when_a_newer_episode_is_active(
    session,
) -> None:
    from app.services.limits import incidents

    _limit, version, run = _fixture(session)
    first_breach = _evaluation(session, version, run, status="breach", at=NOW)
    first = _reconcile(session, run, [first_breach]).incidents[0]
    incidents.resolve(
        session,
        incident_id=first.id,
        expected_row_version=1,
        context=CONTEXT,
        occurred_at=NOW + timedelta(seconds=1),
    )
    later_run = _next_run(session, run, at=NOW + timedelta(minutes=1))
    later_breach = _evaluation(
        session,
        version,
        later_run,
        status="breach",
        at=NOW + timedelta(minutes=1),
    )
    _reconcile(session, later_run, [later_breach], at=NOW + timedelta(minutes=1))

    with pytest.raises(LimitConflictError, match="another active incident"):
        incidents.reopen(
            session,
            incident_id=first.id,
            expected_row_version=2,
            context=CONTEXT,
        )


def test_incident_events_reject_direct_mutation_and_deletion(session) -> None:
    from sqlalchemy import delete, update

    from app.models import LimitIncidentEvent, LimitIncidentEventMutationError

    _limit, version, run = _fixture(session)
    breach = _evaluation(session, version, run, status="breach", at=NOW)
    event = _reconcile(session, run, [breach]).events[0]
    session.commit()

    event = session.get(LimitIncidentEvent, event.id)
    event.payload = {"tampered": True}
    with pytest.raises(LimitIncidentEventMutationError):
        session.flush()
    session.rollback()

    event = session.get(LimitIncidentEvent, event.id)
    session.delete(event)
    with pytest.raises(LimitIncidentEventMutationError):
        session.flush()
    session.rollback()

    with pytest.raises(LimitIncidentEventMutationError):
        session.execute(
            update(LimitIncidentEvent)
            .where(LimitIncidentEvent.id == event.id)
            .values(event_type="tampered")
        )
    with pytest.raises(LimitIncidentEventMutationError):
        session.execute(
            delete(LimitIncidentEvent).where(LimitIncidentEvent.id == event.id)
        )


def test_incident_parent_rejects_bulk_delete(session) -> None:
    from sqlalchemy import delete

    from app.models import LimitIncident, LimitIncidentHistoryDeletionError

    _limit, version, run = _fixture(session)
    breach = _evaluation(session, version, run, status="breach", at=NOW)
    incident = _reconcile(session, run, [breach]).incidents[0]
    session.commit()

    with pytest.raises(LimitIncidentHistoryDeletionError):
        session.execute(delete(LimitIncident).where(LimitIncident.id == incident.id))


def test_incident_bulk_delete_allowed_during_arena_fixture_purge(session) -> None:
    """The arena fixture purge owns its seeded portfolios wholesale: with
    ARENA_FIXTURE_PURGE_INFO_KEY set on session.info the bulk-delete guard
    exempts that cleanup scope (desk paths without the flag stay protected —
    see test_incident_parent_rejects_bulk_delete)."""
    from sqlalchemy import delete

    from app.models import ARENA_FIXTURE_PURGE_INFO_KEY, LimitIncident

    _limit, version, run = _fixture(session)
    breach = _evaluation(session, version, run, status="breach", at=NOW)
    incident = _reconcile(session, run, [breach]).incidents[0]
    session.commit()

    session.info[ARENA_FIXTURE_PURGE_INFO_KEY] = True
    try:
        session.execute(delete(LimitIncident).where(LimitIncident.id == incident.id))
    finally:
        session.info.pop(ARENA_FIXTURE_PURGE_INFO_KEY, None)

    assert session.query(LimitIncident).filter_by(id=incident.id).count() == 0
