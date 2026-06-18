"""Tests for scenario_test_runner: queue + async execute. Mirrors test_batch_pricing."""
from __future__ import annotations

from app.models import Portfolio, ScenarioTestRun, TaskKind, TaskRun


def _runner_db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/runner.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def test_queue_creates_run_and_task(tmp_path, monkeypatch):
    from app.services import scenario_test_runner

    db = _runner_db(tmp_path, monkeypatch)
    monkeypatch.setattr(scenario_test_runner, "submit_async_task", lambda *a, **k: None)

    with db.SessionLocal() as session:
        pf = Portfolio(name="PF", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        run, task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=pf.id,
            pricing_parameter_profile_id=None,
            scenario_request={"predefined": ["market_crash"]},
            config={"calculate_greeks": False},
        )
        assert run.status == "queued"
        assert task.kind == TaskKind.SCENARIO_TEST.value
        assert task.scenario_test_run_id == run.id
        assert run.scenario_spec == {"predefined": ["market_crash"]}


def test_execute_marks_empty_when_no_positions(tmp_path, monkeypatch):
    from app.services import scenario_test_runner

    db = _runner_db(tmp_path, monkeypatch)

    with db.SessionLocal() as session:
        pf = Portfolio(name="PF2", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        run = ScenarioTestRun(
            portfolio_id=pf.id,
            status="queued",
            scenario_spec={"predefined": ["market_crash"]},
            config={},
            results={},
            excluded_positions=[],
            artifacts={},
        )
        session.add(run)
        session.flush()
        task = TaskRun(
            kind=TaskKind.SCENARIO_TEST.value,
            status="queued",
            portfolio_id=pf.id,
            scenario_test_run_id=run.id,
        )
        session.add(task)
        session.flush()
        session.commit()
        run_id, task_id = run.id, task.id

    with db.SessionLocal() as session:
        scenario_test_runner._execute(session, task_id, run_id)
        session.commit()

    with db.SessionLocal() as session:
        run = session.get(ScenarioTestRun, run_id)
        assert run.status == "empty"


def test_execute_marks_failed_when_pipeline_raises(tmp_path, monkeypatch):
    from app.services import scenario_test_runner
    import app.services.domains.scenario_test as scenario_test_svc

    db = _runner_db(tmp_path, monkeypatch)

    with db.SessionLocal() as session:
        pf = Portfolio(name="PF3", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        run = ScenarioTestRun(
            portfolio_id=pf.id,
            status="queued",
            scenario_spec={"predefined": ["market_crash"]},
            config={},
            results={},
            excluded_positions=[],
            artifacts={},
        )
        session.add(run)
        session.flush()
        task = TaskRun(
            kind=TaskKind.SCENARIO_TEST.value,
            status="queued",
            portfolio_id=pf.id,
            scenario_test_run_id=run.id,
        )
        session.add(task)
        session.flush()
        session.commit()
        run_id, task_id = run.id, task.id

    monkeypatch.setattr(
        scenario_test_svc,
        "run_pipeline",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with db.SessionLocal() as session:
        scenario_test_runner._execute(session, task_id, run_id)
        session.commit()

    with db.SessionLocal() as session:
        run = session.get(ScenarioTestRun, run_id)
        assert run.status == "failed"
        assert "boom" in str(run.results)
        task = session.get(TaskRun, task_id)
        assert task.status == "failed"
        assert "boom" in (task.error or "")


def test_queue_rejects_unknown_predefined_scenario(tmp_path, monkeypatch):
    """Unknown predefined names raise ValueError before any row is persisted."""
    import pytest

    from app.services import scenario_test_runner

    db = _runner_db(tmp_path, monkeypatch)
    monkeypatch.setattr(scenario_test_runner, "submit_async_task", lambda *a, **k: None)

    with db.SessionLocal() as session:
        pf = Portfolio(name="PFbad", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        with pytest.raises(ValueError, match="Unknown predefined scenario"):
            scenario_test_runner.queue_scenario_test(
                session,
                portfolio_id=pf.id,
                pricing_parameter_profile_id=None,
                scenario_request={"predefined": ["does_not_exist"]},
                config={},
            )
        # No run row should have been created
        from app.models import ScenarioTestRun
        assert session.query(ScenarioTestRun).count() == 0


def test_queue_rejects_unknown_position_ids(tmp_path, monkeypatch):
    import pytest

    from app.services import scenario_test_runner

    db = _runner_db(tmp_path, monkeypatch)
    monkeypatch.setattr(scenario_test_runner, "submit_async_task", lambda *a, **k: None)

    with db.SessionLocal() as session:
        pf = Portfolio(name="PFscope", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        # A scoped id that doesn't belong to the portfolio must raise (-> REST 400),
        # not silently produce a smaller run.
        with pytest.raises(ValueError, match="not in portfolio"):
            scenario_test_runner.queue_scenario_test(
                session,
                portfolio_id=pf.id,
                pricing_parameter_profile_id=None,
                scenario_request={"predefined": ["market_crash"]},
                config={},
                position_ids=[999999],
            )


def test_incremental_schema_adds_scenario_test_run_id(tmp_path):
    # An existing local SQLite DB (booted via create_all, no migrations) gains
    # task_runs.scenario_test_run_id through _ensure_incremental_schema; otherwise
    # startup's mark_stale_tasks_failed query crashes with "no such column".
    from sqlalchemy import create_engine, inspect, text

    from app import database

    # _ensure_incremental_schema early-returns unless a `positions` table exists
    # (it guards the position-era repairs); the real boot path always has one.
    engine = create_engine(f"sqlite:///{tmp_path}/old.db")
    with engine.begin() as cx:
        # `positions` must exist (early-return guard) and already carry the columns
        # the intermediate position-era repairs touch, so the function proceeds to
        # the task_runs block we care about without tripping unrelated backfills.
        cx.execute(
            text(
                "CREATE TABLE positions (id INTEGER PRIMARY KEY, "
                "position_kind VARCHAR(16) NOT NULL DEFAULT 'otc', "
                "rfq_id INTEGER, rfq_quote_version_id INTEGER, "
                "kwargs_migrated_at DATETIME, version INTEGER NOT NULL DEFAULT 1, "
                "trade_effective_date DATETIME, source_trade_id VARCHAR, "
                "source_payload JSON, product_id INTEGER)"
            )
        )
        cx.execute(
            text(
                "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, "
                "kind VARCHAR(80), status VARCHAR(40))"
            )
        )
    assert "scenario_test_run_id" not in {
        c["name"] for c in inspect(engine).get_columns("task_runs")
    }
    database._ensure_incremental_schema(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("task_runs")}
    assert "scenario_test_run_id" in cols
