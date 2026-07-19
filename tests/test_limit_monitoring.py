from __future__ import annotations

from datetime import datetime


NOW = datetime(2026, 7, 18, 9, 30)


def _active_delta_limit(session):
    from app.models import Portfolio, Position, RiskLimit, RiskLimitVersion

    portfolio = Portfolio(name="Monitoring book", base_currency="USD")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        currency="USD",
    )
    session.add(position)
    session.flush()
    limit = RiskLimit(
        key="monitoring-delta",
        name="Monitoring delta",
        description="",
        category="greek",
        owner="market-risk",
        tags=[],
        active_version_id=None,
    )
    session.add(limit)
    session.flush()
    version = RiskLimitVersion(
        risk_limit_id=limit.id,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="position",
        scope_config={"position_ids": [position.id]},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_lower=None,
        warning_upper=10.0,
        hard_lower=None,
        hard_upper=20.0,
        unit="underlying_units",
        currency=None,
        bump_convention=None,
        freshness_policy={"max_age_seconds": 900},
        effective_from=NOW,
        activated_at=NOW,
    )
    session.add(version)
    session.flush()
    limit.active_version_id = version.id
    session.flush()
    return portfolio, position, limit, version


def _matching_risk_source(
    session,
    *,
    portfolio_id: int,
    position_id: int,
    delta: float,
    valuation_as_of: datetime = NOW,
    created_at: datetime = NOW,
):
    from app.models import RiskRun
    from app.services.source_evidence import source_metric_contract

    source = RiskRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=None,
        engine_config_id=None,
        market_snapshot_id=None,
        method="summary",
        status="completed",
        resolved_position_ids=[position_id],
        created_at=created_at,
        metrics={
            "valuation_as_of": valuation_as_of.isoformat(),
            "source_metadata": {
                "valuation_as_of": valuation_as_of.isoformat(),
                "effective_valuation_as_of": valuation_as_of.isoformat(),
                "effective_market_evidence_id": "external-market-evidence/v1:test",
                "methodology": {"method": "summary"},
                "source_config": {},
                "metric_contract": source_metric_contract("risk_run"),
                "market_evidence_complete": True,
            },
            "shared": {"delta": delta},
            "positions": [
                {
                    "position_id": position_id,
                    "underlying": "AAPL",
                    "product_type": "EuropeanVanillaOption",
                    "currency": "USD",
                    "pricing_ok": True,
                    "greeks_ok": True,
                    "delta": delta,
                }
            ],
            "by_currency": {"USD": {"position_count": 1}},
        },
    )
    session.add(source)
    session.flush()
    return source


def _queue(
    session,
    *,
    portfolio_id: int,
    trigger: str = "manual",
    valuation_as_of: datetime = NOW,
):
    from app.services.limits.contracts import LimitActionContext
    from app.services.limits.monitoring import queue_limit_monitoring

    kwargs = {"schedule_id": 17, "occurrence_id": 23} if trigger == "scheduled" else {}
    return queue_limit_monitoring(
        session,
        portfolio_id=portfolio_id,
        trigger=trigger,
        context=LimitActionContext(
            actor="alice",
            persona="limit_manager" if trigger == "agent" else None,
            mode="auto" if trigger != "manual" else "interactive",
        ),
        pricing_parameter_profile_id=None,
        engine_config_id=None,
        market_snapshot_id=None,
        effective_market_evidence_id="external-market-evidence/v1:test",
        valuation_as_of=valuation_as_of,
        source_policy="reuse_only",
        max_source_age_seconds=900,
        **kwargs,
    )


def test_queue_snapshots_active_versions_and_trusted_trigger_context(session) -> None:
    from app.models import TaskKind, TaskStatus
    from app.services.limits.contracts import LimitActionContext
    from app.services.limits.monitoring import queue_limit_monitoring

    portfolio, position, _limit, version = _active_delta_limit(session)
    run, task = queue_limit_monitoring(
        session,
        portfolio_id=portfolio.id,
        trigger="agent",
        context=LimitActionContext(
            actor="alice",
            persona="limit_manager",
            mode="auto",
            thread_id=11,
            audit_ref="audit-17",
        ),
        pricing_parameter_profile_id=None,
        engine_config_id=None,
        market_snapshot_id=None,
        effective_market_evidence_id="external-market-evidence/v1:test",
        valuation_as_of=NOW,
        source_policy="reuse_only",
        max_source_age_seconds=900,
    )

    assert task.kind == TaskKind.LIMIT_MONITORING.value
    assert task.status == TaskStatus.QUEUED.value
    assert task.limit_monitoring_run_id == run.id
    assert run.definition_snapshot["context"] == {
        "actor": "alice",
        "persona": "limit_manager",
        "mode": "auto",
        "thread_id": 11,
        "audit_ref": "audit-17",
    }
    assert run.definition_snapshot["versions"][0]["id"] == version.id
    assert [link.limit_version_id for link in run.version_links] == [version.id]
    assert len(run.definition_snapshot_hash) == 64
    assert (
        position.id
        in run.definition_snapshot["versions"][0]["scope_config"]["position_ids"]
    )
    assert run.definition_snapshot["versions"][0]["scopes"][0]["scope_key"] == (
        f"position:{position.id}"
    )


def test_worker_persists_unknown_evaluation_without_failing_task(session) -> None:
    from app.models import LimitEvaluation, LimitMonitoringRun, TaskRun
    from app.services.limits.contracts import LimitActionContext
    from app.services.limits.monitoring import (
        execute_limit_monitoring_task,
        queue_limit_monitoring,
    )

    portfolio, _position, _limit, version = _active_delta_limit(session)
    run, task = queue_limit_monitoring(
        session,
        portfolio_id=portfolio.id,
        trigger="manual",
        context=LimitActionContext(actor="alice", persona=None, mode="interactive"),
        pricing_parameter_profile_id=None,
        engine_config_id=None,
        market_snapshot_id=None,
        effective_market_evidence_id="external-market-evidence/v1:test",
        valuation_as_of=NOW,
        source_policy="reuse_only",
        max_source_age_seconds=900,
    )
    session.commit()
    # The worker opens its own session and must finish the TaskRun even when a
    # definition has no reusable source: that is a business unknown, not infra
    # failure.  Use the configured test factory directly.
    from app import database

    execute_limit_monitoring_task(
        task.id,
        run.id,
        session_factory=database.SessionLocal,
    )
    session.expire_all()
    persisted_task = session.get(TaskRun, task.id)
    persisted_run = session.get(LimitMonitoringRun, run.id)
    evaluation = session.query(LimitEvaluation).one()

    assert persisted_task.status == "completed"
    assert persisted_run.status == "completed_with_unknowns"
    assert persisted_run.summary["unknown"] == 1
    assert evaluation.limit_version_id == version.id
    assert evaluation.status == "unknown"
    assert evaluation.reason_code == "missing_source"
    assert len(persisted_run.source_references) == 1
    assert persisted_run.source_references[0].source_status == "missing"


def test_grouped_source_reuse_serves_versions_and_manual_schedule_match(
    session,
) -> None:
    from app import database
    from app.models import LimitEvaluation, RiskLimit, RiskLimitVersion, TaskRun
    from app.services.limits.monitoring import execute_limit_monitoring_task

    portfolio, position, _limit, version = _active_delta_limit(session)
    second_limit = RiskLimit(
        key="monitoring-delta-second",
        name="Monitoring delta second",
        description="",
        category="greek",
        owner="market-risk",
        tags=[],
    )
    session.add(second_limit)
    session.flush()
    second_version = RiskLimitVersion(
        risk_limit_id=second_limit.id,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="position",
        scope_config={"position_ids": [position.id]},
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=10.0,
        hard_upper=20.0,
        unit="underlying_units",
        freshness_policy={"max_age_seconds": 900},
        effective_from=NOW,
        activated_at=NOW,
    )
    session.add(second_version)
    session.flush()
    second_limit.active_version_id = second_version.id
    _matching_risk_source(
        session,
        portfolio_id=portfolio.id,
        position_id=position.id,
        delta=15.0,
    )
    manual, manual_task = _queue(session, portfolio_id=portfolio.id)
    scheduled, scheduled_task = _queue(
        session,
        portfolio_id=portfolio.id,
        trigger="scheduled",
    )
    session.commit()

    execute_limit_monitoring_task(
        manual_task.id,
        manual.id,
        database.SessionLocal,
        selection_now=NOW,
    )
    execute_limit_monitoring_task(
        scheduled_task.id,
        scheduled.id,
        database.SessionLocal,
        selection_now=NOW,
    )
    session.expire_all()
    manual_evaluations = (
        session.query(LimitEvaluation)
        .filter(LimitEvaluation.monitoring_run_id == manual.id)
        .order_by(LimitEvaluation.limit_version_id)
        .all()
    )
    scheduled_evaluations = (
        session.query(LimitEvaluation)
        .filter(LimitEvaluation.monitoring_run_id == scheduled.id)
        .order_by(LimitEvaluation.limit_version_id)
        .all()
    )

    assert session.get(TaskRun, manual_task.id).status == "completed"
    assert [row.status for row in manual_evaluations] == ["warning", "warning"]
    assert len(session.get(type(manual), manual.id).source_references) == 1
    assert len(session.get(type(scheduled), scheduled.id).source_references) == 1
    diagnostics = (
        session.get(type(manual), manual.id)
        .source_references[0]
        .completeness_diagnostics
    )
    assert diagnostics["source_status"] == "completed"
    assert diagnostics["market_evidence_complete"] is True
    assert diagnostics["resolved_position_ids"] == [position.id]
    assert [
        (row.limit_version_id, row.observed_value, row.status)
        for row in manual_evaluations
    ] == [
        (row.limit_version_id, row.observed_value, row.status)
        for row in scheduled_evaluations
    ]
    assert {row.limit_version_id for row in manual_evaluations} == {
        version.id,
        second_version.id,
    }


def test_evaluator_failure_keeps_committed_source_references(
    monkeypatch, session
) -> None:
    from app import database
    from app.models import LimitMonitoringRun, TaskRun
    from app.services.limits import monitoring

    portfolio, position, _limit, _version = _active_delta_limit(session)
    _matching_risk_source(
        session,
        portfolio_id=portfolio.id,
        position_id=position.id,
        delta=1.0,
    )
    run, task = _queue(session, portfolio_id=portfolio.id)
    session.commit()
    monkeypatch.setattr(
        monitoring,
        "evaluate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("evaluator down")),
    )

    monitoring.execute_limit_monitoring_task(task.id, run.id, database.SessionLocal)
    session.expire_all()

    assert session.get(TaskRun, task.id).status == "failed"
    assert session.get(LimitMonitoringRun, run.id).status == "failed"
    assert len(session.get(LimitMonitoringRun, run.id).source_references) == 1


def test_duplicate_worker_delivery_is_a_terminal_noop(session) -> None:
    from app import database
    from app.models import (
        LimitEvaluation,
        LimitMonitoringRun,
        LimitSourceReference,
        TaskRun,
    )
    from app.services.limits import monitoring

    portfolio, position, _limit, _version = _active_delta_limit(session)
    _matching_risk_source(
        session,
        portfolio_id=portfolio.id,
        position_id=position.id,
        delta=1.0,
    )
    run, task = _queue(session, portfolio_id=portfolio.id)
    session.commit()

    monitoring.execute_limit_monitoring_task(
        task.id,
        run.id,
        database.SessionLocal,
        selection_now=NOW,
    )
    first_reference_count = session.query(LimitSourceReference).count()
    first_evaluation_count = session.query(LimitEvaluation).count()

    monitoring.execute_limit_monitoring_task(
        task.id,
        run.id,
        database.SessionLocal,
        selection_now=NOW,
    )
    session.expire_all()

    assert session.get(TaskRun, task.id).status == "completed"
    assert session.get(LimitMonitoringRun, run.id).status == "completed"
    assert session.query(LimitSourceReference).count() == first_reference_count
    assert session.query(LimitEvaluation).count() == first_evaluation_count


def test_mismatched_worker_delivery_cannot_mutate_either_pair(session) -> None:
    from app import database
    from app.models import LimitEvaluation, LimitSourceReference, TaskRun
    from app.services.limits import monitoring

    portfolio, _position, _limit, _version = _active_delta_limit(session)
    first_run, first_task = _queue(session, portfolio_id=portfolio.id)
    second_run, second_task = _queue(session, portfolio_id=portfolio.id)
    session.commit()

    monitoring.execute_limit_monitoring_task(
        first_task.id,
        second_run.id,
        database.SessionLocal,
        selection_now=NOW,
    )
    session.expire_all()

    assert session.get(TaskRun, first_task.id).status == "queued"
    assert session.get(type(first_run), first_run.id).status == "queued"
    assert session.get(TaskRun, second_task.id).status == "queued"
    assert session.get(type(second_run), second_run.id).status == "queued"
    assert session.query(LimitSourceReference).count() == 0
    assert session.query(LimitEvaluation).count() == 0


def test_queue_uses_version_effective_at_valuation_not_current_pointer(session) -> None:
    from datetime import timedelta

    portfolio, _position, limit, current = _active_delta_limit(session)
    current.effective_from = NOW + timedelta(days=1)
    from app.models import RiskLimitVersion
    from app.services.limits.contracts import LimitActionContext
    from app.services.limits.monitoring import queue_limit_monitoring

    historical = RiskLimitVersion(
        risk_limit_id=limit.id,
        version=2,
        state="superseded",
        metric_kind="delta",
        source_kind="risk_run",
        methodology={},
        scope_type="position",
        scope_config=current.scope_config,
        aggregation="net",
        transform="absolute",
        comparator="upper",
        warning_upper=10.0,
        hard_upper=20.0,
        unit="underlying_units",
        freshness_policy={},
        effective_from=NOW - timedelta(days=1),
        effective_until=None,
        activated_at=NOW - timedelta(days=1),
    )
    session.add(historical)
    session.flush()

    run, _task = queue_limit_monitoring(
        session,
        portfolio_id=portfolio.id,
        trigger="manual",
        context=LimitActionContext(actor="alice", persona=None, mode="interactive"),
        pricing_parameter_profile_id=None,
        engine_config_id=None,
        market_snapshot_id=None,
        effective_market_evidence_id="external-market-evidence/v1:test",
        valuation_as_of=NOW,
        source_policy="reuse_only",
        max_source_age_seconds=900,
    )

    assert [row["id"] for row in run.definition_snapshot["versions"]] == [historical.id]


def test_queue_skips_definition_scoped_to_another_portfolio(session) -> None:
    from app.models import Portfolio, Position

    portfolio, _position, _limit, version = _active_delta_limit(session)
    other = Portfolio(name="Other monitoring book", base_currency="USD")
    session.add(other)
    session.flush()
    other_position = Position(
        portfolio_id=other.id,
        underlying="MSFT",
        product_type="EuropeanVanillaOption",
        product_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        currency="USD",
    )
    session.add(other_position)
    session.flush()
    version.scope_config = {"position_ids": [other_position.id]}

    run, _task = _queue(session, portfolio_id=portfolio.id)

    assert run.definition_snapshot["versions"] == []
    assert run.version_links == []


def test_historical_valuation_source_freshness_uses_selection_clock(session) -> None:
    from app import database
    from app.models import LimitMonitoringRun
    from app.services.limits import monitoring

    historical_as_of = datetime(2025, 7, 18, 9, 30)
    portfolio, position, _limit, version = _active_delta_limit(session)
    version.effective_from = historical_as_of
    _matching_risk_source(
        session,
        portfolio_id=portfolio.id,
        position_id=position.id,
        delta=1.0,
        valuation_as_of=historical_as_of,
        created_at=NOW,
    )
    run, task = _queue(
        session,
        portfolio_id=portfolio.id,
        valuation_as_of=historical_as_of,
    )
    session.commit()

    monitoring.execute_limit_monitoring_task(
        task.id,
        run.id,
        database.SessionLocal,
        selection_now=NOW,
    )
    session.expire_all()
    persisted = session.get(LimitMonitoringRun, run.id)

    assert persisted.status == "completed"
    assert persisted.source_references[0].is_fresh is True
