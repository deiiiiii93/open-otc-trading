from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


def _limit(session, *, key: str = "desk-delta"):
    from app.models import RiskLimit

    row = RiskLimit(
        key=key,
        name="Desk delta",
        description="Absolute desk delta governance",
        category="greek",
        owner="market-risk",
        tags=["intraday", "desk"],
        created_by_actor="alice",
        created_by_persona="limit_manager",
    )
    session.add(row)
    session.flush()
    return row


def _version(session, limit_id: int, *, version: int = 1):
    from app.models import RiskLimitVersion

    row = RiskLimitVersion(
        risk_limit_id=limit_id,
        version=version,
        state="draft",
        metric_kind="rho_q",
        source_kind="risk_run",
        methodology={"bump": "1bp"},
        scope_type="portfolio",
        scope_config={"portfolio_ids": [7]},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=80.0,
        hard_upper=100.0,
        unit="USD",
        currency="USD",
        bump_convention="per_1bp",
        freshness_policy={"max_age_seconds": 900},
        rationale="Dividend-rho appetite",
        created_by_actor="alice",
        created_by_persona="limit_manager",
        created_in_mode="interactive",
    )
    session.add(row)
    session.flush()
    return row


def _portfolio(session):
    from app.models import Portfolio

    row = Portfolio(name="Limits model book", base_currency="USD")
    session.add(row)
    session.flush()
    return row


def _monitoring_run(session, portfolio_id: int):
    from app.models import LimitMonitoringRun

    row = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio_id,
        valuation_as_of=datetime(2026, 7, 17, 9, 30),
        source_policy="reuse_only",
        status="completed_with_unknowns",
        summary={"ok": 1, "unknown": 1},
        definition_snapshot={"versions": [{"id": 11, "version": 1}]},
        definition_snapshot_hash="a" * 64,
    )
    session.add(row)
    session.flush()
    return row


def test_limits_model_json_and_enum_like_roundtrip(session) -> None:
    from app.models import (
        LimitEvaluation,
        LimitIncident,
        LimitIncidentEvent,
        LimitMonitoringRunVersion,
        LimitSourceReference,
    )

    limit_row = _limit(session)
    version = _version(session, limit_row.id)
    portfolio = _portfolio(session)
    run = _monitoring_run(session, portfolio.id)
    session.add(
        LimitMonitoringRunVersion(
            monitoring_run_id=run.id,
            limit_version_id=version.id,
        )
    )
    source = LimitSourceReference(
        monitoring_run_id=run.id,
        source_kind="risk_run",
        requested_parameters={"portfolio_id": portfolio.id},
        source_status="completed_with_errors",
        is_fresh=True,
        completeness_diagnostics={"failed_position_ids": [9]},
    )
    session.add(source)
    evaluation = LimitEvaluation(
        monitoring_run_id=run.id,
        limit_version_id=version.id,
        scope_type="portfolio",
        scope_key=str(portfolio.id),
        scope_label=portfolio.name,
        observed_value=None,
        adverse_value=None,
        warning_upper=80.0,
        hard_upper=100.0,
        status="unknown",
        reason_code="incomplete_scope",
        reason="One in-scope position failed",
        coverage_count=3,
        coverage_ratio=0.75,
        evidence={"source_reference_id": "pending"},
    )
    session.add(evaluation)
    session.flush()
    incident = LimitIncident(
        risk_limit_id=limit_row.id,
        scope_type="portfolio",
        scope_key=str(portfolio.id),
        scope_label=portfolio.name,
        severity="breach",
        status="open",
        first_evaluation_id=evaluation.id,
        last_evaluation_id=evaluation.id,
    )
    session.add(incident)
    session.flush()
    session.add(
        LimitIncidentEvent(
            incident_id=incident.id,
            event_type="opened",
            evaluation_id=evaluation.id,
            actor="system",
            persona="limit_manager",
            mode="auto",
            audit_ref="audit-1",
            payload={"status": "breach"},
        )
    )
    session.commit()

    assert limit_row.tags == ["intraday", "desk"]
    assert version.metric_kind == "rho_q"
    assert version.methodology == {"bump": "1bp"}
    assert run.status == "completed_with_unknowns"
    assert run.definition_snapshot["versions"][0]["version"] == 1
    assert source.completeness_diagnostics["failed_position_ids"] == [9]
    assert evaluation.reason_code == "incomplete_scope"
    assert incident.events[0].payload == {"status": "breach"}


def test_limit_version_number_is_unique_per_identity(session) -> None:
    limit_row = _limit(session, key="unique-version")
    _version(session, limit_row.id)

    with pytest.raises(IntegrityError), session.begin_nested():
        _version(session, limit_row.id)


def test_limit_identity_and_version_history_cannot_be_hard_deleted(
    session,
) -> None:
    from app.models import (
        RiskLimitHistoryDeletionError,
        RiskLimitVersion,
    )

    limit_row = _limit(session, key="immutable-history")
    version = _version(session, limit_row.id)
    version.state = "retired"
    session.commit()

    session.delete(limit_row)
    with pytest.raises(
        RiskLimitHistoryDeletionError,
        match="risk limit identities cannot be deleted",
    ):
        session.flush()
    session.rollback()

    session.expire_all()
    persisted = session.scalar(
        select(RiskLimitVersion).where(RiskLimitVersion.id == version.id)
    )
    assert persisted is not None
    assert persisted.state == "retired"


def test_empty_limit_identity_cannot_be_deleted_through_orm(session) -> None:
    from app.models import RiskLimitHistoryDeletionError

    limit_row = _limit(session, key="immutable-empty-identity")
    limit_id = limit_row.id
    session.commit()

    session.delete(limit_row)
    with pytest.raises(
        RiskLimitHistoryDeletionError,
        match="risk limit identities cannot be deleted",
    ):
        session.flush()
    session.rollback()

    assert session.get(type(limit_row), limit_id) is not None


def test_limit_version_cannot_be_deleted_through_orm(session) -> None:
    from app.models import (
        RiskLimitHistoryDeletionError,
        RiskLimitVersion,
    )

    limit_row = _limit(session, key="immutable-version")
    version = _version(session, limit_row.id)
    limit_row.active_version_id = version.id
    limit_id = limit_row.id
    version_id = version.id
    session.commit()

    session.delete(version)
    with pytest.raises(
        RiskLimitHistoryDeletionError,
        match="risk limit versions cannot be deleted",
    ):
        session.flush()
    session.rollback()

    assert session.get(RiskLimitVersion, version_id) is not None
    assert session.get(type(limit_row), limit_id).active_version_id == version_id


def test_evaluation_is_unique_per_run_version_scope(session) -> None:
    from app.models import LimitEvaluation

    limit_row = _limit(session, key="unique-evaluation")
    version = _version(session, limit_row.id)
    portfolio = _portfolio(session)
    run = _monitoring_run(session, portfolio.id)
    kwargs = {
        "monitoring_run_id": run.id,
        "limit_version_id": version.id,
        "scope_type": "portfolio",
        "scope_key": str(portfolio.id),
        "scope_label": portfolio.name,
        "status": "ok",
        "evidence": {},
    }
    session.add(LimitEvaluation(**kwargs))
    session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(LimitEvaluation(**kwargs))
        session.flush()


def test_only_one_non_terminal_incident_per_limit_scope(session) -> None:
    from app.models import LimitIncident

    limit_row = _limit(session, key="unique-incident")
    kwargs = {
        "risk_limit_id": limit_row.id,
        "scope_type": "portfolio",
        "scope_key": "7",
        "scope_label": "Book",
        "severity": "warning",
        "status": "open",
    }
    session.add(LimitIncident(**kwargs))
    session.flush()

    with pytest.raises(IntegrityError), session.begin_nested():
        session.add(LimitIncident(**kwargs))
        session.flush()

    session.add(LimitIncident(**{**kwargs, "status": "resolved"}))
    session.flush()


def test_task_run_links_to_limit_monitoring_run(session) -> None:
    from app.models import TaskKind, TaskRun

    portfolio = _portfolio(session)
    run = _monitoring_run(session, portfolio.id)
    task = TaskRun(
        kind=TaskKind.LIMIT_MONITORING.value,
        status="completed",
        portfolio_id=portfolio.id,
        limit_monitoring_run_id=run.id,
    )
    session.add(task)
    session.commit()

    assert task.limit_monitoring_run is run
    assert run.task_runs == [task]
