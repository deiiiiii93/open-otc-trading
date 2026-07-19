from __future__ import annotations

from datetime import datetime


def test_task_runner_preserves_completed_with_unknowns_domain_status(session) -> None:
    from app.models import LimitMonitoringRun, Portfolio, TaskKind, TaskRun
    from app.services.task_runner import mark_task_finished

    portfolio = Portfolio(name="Task monitoring book", base_currency="USD")
    session.add(portfolio)
    session.flush()
    run = LimitMonitoringRun(
        trigger="scheduled",
        mode="auto",
        portfolio_id=portfolio.id,
        valuation_as_of=datetime(2026, 7, 18, 9, 30),
        source_policy="reuse_only",
        status="completed_with_unknowns",
        summary={"unknown": 1},
        definition_snapshot={"versions": []},
        definition_snapshot_hash="a" * 64,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.LIMIT_MONITORING.value,
        status="running",
        portfolio_id=portfolio.id,
        limit_monitoring_run_id=run.id,
    )
    session.add(task)
    session.flush()

    mark_task_finished(session, task.id, status="completed")

    assert task.status == "completed"
    assert run.status == "completed_with_unknowns"


def test_dispatch_submits_only_the_composite_worker(monkeypatch, session) -> None:
    from concurrent.futures import Future

    from app.models import LimitMonitoringRun, Portfolio, TaskKind, TaskRun
    from app.services.limits import monitoring

    portfolio = Portfolio(name="Dispatch monitoring book", base_currency="USD")
    session.add(portfolio)
    session.flush()
    run = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=datetime(2026, 7, 18, 9, 30),
        source_policy="reuse_only",
        status="queued",
        summary={},
        definition_snapshot={"versions": [], "context": {}},
        definition_snapshot_hash="b" * 64,
    )
    task = TaskRun(
        kind=TaskKind.LIMIT_MONITORING.value,
        status="queued",
        portfolio_id=portfolio.id,
        limit_monitoring_run=run,
    )
    session.add(task)
    session.flush()
    calls = []
    future: Future[None] = Future()
    monkeypatch.setattr(
        monitoring,
        "submit_async_task",
        lambda fn, *args: calls.append((fn, args)) or future,
    )

    monitoring.dispatch_limit_monitoring(task.id, run.id)
    future.set_result(None)

    assert calls == [(monitoring.execute_limit_monitoring_task, (task.id, run.id))]


def test_failed_worker_future_releases_queued_portfolio_slot(
    monkeypatch,
    session,
) -> None:
    from concurrent.futures import Future

    from app.models import LimitMonitoringRun, Portfolio, TaskKind, TaskRun
    from app.services.limits import monitoring

    portfolio = Portfolio(name="Pre-claim failure book", base_currency="USD")
    session.add(portfolio)
    session.flush()
    run = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=datetime(2026, 7, 18, 9, 30),
        source_policy="reuse_only",
        status="queued",
        summary={},
        definition_snapshot={"versions": [], "context": {}},
        definition_snapshot_hash="d" * 64,
    )
    task = TaskRun(
        kind=TaskKind.LIMIT_MONITORING.value,
        status="queued",
        portfolio_id=portfolio.id,
        limit_monitoring_run=run,
    )
    session.add(task)
    session.commit()
    future: Future[None] = Future()
    monkeypatch.setattr(
        monitoring,
        "submit_async_task",
        lambda _fn, *_args: future,
    )

    monitoring.dispatch_limit_monitoring(task.id, run.id)
    future.set_exception(RuntimeError("worker bootstrap failed"))

    session.expire_all()
    assert session.get(TaskRun, task.id).status == "failed"
    assert session.get(TaskRun, task.id).error == "worker bootstrap failed"
    assert session.get(LimitMonitoringRun, run.id).status == "failed"

    retry = LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=datetime(2026, 7, 18, 9, 31),
        source_policy="reuse_only",
        status="queued",
        summary={},
        definition_snapshot={"versions": [], "context": {}},
        definition_snapshot_hash="e" * 64,
    )
    session.add(retry)
    session.flush()
    assert retry.id != run.id


def test_stale_monitoring_task_marks_linked_run_failed(session) -> None:
    from app.models import LimitMonitoringRun, Portfolio, TaskKind, TaskRun
    from app.services.task_runner import mark_stale_tasks_failed

    portfolio = Portfolio(name="Restart monitoring book", base_currency="USD")
    session.add(portfolio)
    session.flush()
    run = LimitMonitoringRun(
        trigger="scheduled",
        mode="auto",
        portfolio_id=portfolio.id,
        valuation_as_of=datetime(2026, 7, 18, 9, 30),
        source_policy="reuse_only",
        status="running",
        summary={},
        definition_snapshot={"versions": [], "context": {}},
        definition_snapshot_hash="c" * 64,
    )
    task = TaskRun(
        kind=TaskKind.LIMIT_MONITORING.value,
        status="running",
        portfolio_id=portfolio.id,
        limit_monitoring_run=run,
    )
    session.add(task)
    session.flush()

    assert mark_stale_tasks_failed(session) == 1
    assert task.status == "failed"
    assert run.status == "failed"


def test_execute_worker_never_submits_a_nested_task(monkeypatch, session) -> None:
    from app import database
    from app.models import Portfolio, Position, RiskLimit, RiskLimitVersion, TaskRun
    from app.services.limits import monitoring
    from app.services.limits.contracts import LimitActionContext

    portfolio = Portfolio(name="No nested worker book", base_currency="USD")
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
        key="no-nested-delta",
        name="No nested delta",
        description="",
        category="greek",
        owner="market-risk",
        tags=[],
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
        warning_upper=10.0,
        hard_upper=20.0,
        unit="underlying_units",
        freshness_policy={},
        effective_from=datetime(2026, 7, 18, 9, 30),
        activated_at=datetime(2026, 7, 18, 9, 30),
    )
    session.add(version)
    session.flush()
    limit.active_version_id = version.id
    run, task = monitoring.queue_limit_monitoring(
        session,
        portfolio_id=portfolio.id,
        trigger="manual",
        context=LimitActionContext(actor="alice", persona=None, mode="interactive"),
        pricing_parameter_profile_id=None,
        engine_config_id=None,
        market_snapshot_id=None,
        effective_market_evidence_id="external-market-evidence/v1:test",
        valuation_as_of=datetime(2026, 7, 18, 9, 30),
        source_policy="reuse_only",
        max_source_age_seconds=900,
    )
    session.commit()
    monkeypatch.setattr(
        monitoring,
        "submit_async_task",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("nested task")),
    )

    monitoring.execute_limit_monitoring_task(task.id, run.id, database.SessionLocal)

    session.expire_all()
    assert session.get(TaskRun, task.id).status == "completed"
