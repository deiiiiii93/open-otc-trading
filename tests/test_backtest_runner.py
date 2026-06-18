"""Tests for backtest_runner: validate_spec + async execute FAILED path. Mirrors test_scenario_test_runner."""
from __future__ import annotations

import pytest

from app.services import backtest_runner


# ---------------------------------------------------------------------------
# Unit: _validate_spec
# ---------------------------------------------------------------------------


def test_queue_rejects_bad_window():
    with pytest.raises(ValueError):
        backtest_runner._validate_spec({"start": "2024-04-30", "end": "2024-01-02"})


def test_queue_rejects_equal_dates():
    with pytest.raises(ValueError):
        backtest_runner._validate_spec({"start": "2024-01-02", "end": "2024-01-02"})


def test_queue_rejects_bad_engine():
    with pytest.raises(ValueError):
        backtest_runner._validate_spec(
            {"start": "2024-01-02", "end": "2024-04-30", "engine": "bogus"}
        )

    with pytest.raises(ValueError):
        backtest_runner._validate_spec(
            {"start": "2024-01-02", "end": "2024-04-30", "other_engine": "bogus"}
        )

    with pytest.raises(ValueError):
        backtest_runner._validate_spec(
            {"start": "2024-01-02", "end": "2024-04-30", "fallback_engine": "analytical"}
        )


def test_queue_rejects_bad_engine_family():
    with pytest.raises(ValueError):
        backtest_runner._validate_spec(
            {"start": "2024-01-02", "end": "2024-04-30", "engine_family": "bogus"}
        )


def test_queue_rejects_bad_vol_source():
    with pytest.raises(ValueError):
        backtest_runner._validate_spec(
            {"start": "2024-01-02", "end": "2024-04-30", "vol_source": "unknown"}
        )


def test_valid_spec_passes():
    backtest_runner._validate_spec(
        {
            "start": "2024-01-02",
            "end": "2024-04-30",
            "engine_family": "autocallable",
            "engine": "quad",
            "other_engine": "analytical",
            "fallback_engine": "pde",
            "autocallable_engine": "quad",
            "vol_source": "flat",
        }
    )


def test_valid_spec_defaults_pass():
    # engine and vol_source omitted → defaults quad / realized
    backtest_runner._validate_spec({"start": "2024-01-01", "end": "2024-12-31"})


# ---------------------------------------------------------------------------
# Integration-ish: _execute persists FAILED when pipeline raises
# ---------------------------------------------------------------------------


def _runner_db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/runner.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def test_execute_marks_failed_when_pipeline_raises(tmp_path, monkeypatch):
    import app.services.domains.backtest as backtest_svc
    from app.models import BacktestRun, Portfolio, TaskKind, TaskRun

    db = _runner_db(tmp_path, monkeypatch)

    with db.SessionLocal() as session:
        pf = Portfolio(name="BacktestPF", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        run = BacktestRun(
            portfolio_id=pf.id,
            status="queued",
            spec={"start": "2024-01-02", "end": "2024-04-30"},
            config={},
            results={},
            excluded_positions=[],
            artifacts={},
        )
        session.add(run)
        session.flush()
        task = TaskRun(
            kind=TaskKind.BACKTEST.value,
            status="queued",
            portfolio_id=pf.id,
            backtest_run_id=run.id,
        )
        session.add(task)
        session.flush()
        session.commit()
        run_id, task_id = run.id, task.id

    monkeypatch.setattr(
        backtest_svc,
        "run_pipeline",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    with db.SessionLocal() as session:
        backtest_runner._execute(session, task_id, run_id)
        session.commit()

    with db.SessionLocal() as session:
        run = session.get(BacktestRun, run_id)
        assert run.status == "failed"
        assert "boom" in str(run.results)
        task = session.get(TaskRun, task_id)
        assert task.status == "failed"
        assert "boom" in (task.error or "")


def test_mark_stale_task_marks_linked_backtest_failed(tmp_path, monkeypatch):
    from app.models import BacktestRun, Portfolio, TaskKind, TaskRun
    from app.services.task_runner import mark_stale_tasks_failed

    db = _runner_db(tmp_path, monkeypatch)

    with db.SessionLocal() as session:
        pf = Portfolio(name="BacktestPF", base_currency="USD", kind="container")
        session.add(pf)
        session.flush()
        run = BacktestRun(
            portfolio_id=pf.id,
            status="running",
            spec={"start": "2024-01-02", "end": "2024-04-30"},
            config={},
            results={},
            excluded_positions=[],
            artifacts={},
        )
        session.add(run)
        session.flush()
        task = TaskRun(
            kind=TaskKind.BACKTEST.value,
            status="running",
            portfolio_id=pf.id,
            backtest_run_id=run.id,
        )
        session.add(task)
        session.commit()
        run_id = run.id

    with db.SessionLocal() as session:
        assert mark_stale_tasks_failed(session) == 1
        session.commit()

    with db.SessionLocal() as session:
        run = session.get(BacktestRun, run_id)
        assert run.status == "failed"
