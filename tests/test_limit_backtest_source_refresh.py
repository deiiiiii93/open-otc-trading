from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import func, select


def _database(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/backtest-source.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _fixture(session):
    from app.models import (
        EngineConfigVariant,
        Portfolio,
        Position,
        PricingParameterProfile,
    )

    portfolio = Portfolio(name="Backtest limits source", base_currency="USD")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
        },
        engine_name="BlackScholesEngine",
        quantity=1.0,
        entry_price=8.0,
        currency="USD",
    )
    profile = PricingParameterProfile(
        name="Backtest pinned profile",
        valuation_date=datetime(2026, 7, 14, 15, 0),
        source_type="xlsx",
        status="completed",
        summary={},
    )
    engine = EngineConfigVariant(
        name="Backtest limit engines",
        status="active",
        is_default=False,
        rules={"rules": []},
    )
    session.add_all([position, profile, engine])
    session.flush()
    return portfolio, position, profile, engine


def test_inline_backtest_source_matches_queue_without_child_task_and_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import BacktestRun, TaskRun
    from app.services import backtest_runner
    from app.services.domains import backtest as backtest_service

    monkeypatch.setattr(
        backtest_runner,
        "submit_async_task",
        lambda *_args, **_kwargs: None,
    )
    pipeline_calls: list[dict] = []

    def fake_pipeline(session, *, positions, progress, **kwargs):
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        progress(1, 1)
        pipeline_calls.append(
            {
                "position_ids": [position.id for position in positions],
                **kwargs,
            }
        )
        return (
            "completed",
            {
                "portfolio_summary": {
                    "var_95": 20.0,
                    "cvar_95": 25.0,
                    "total_pnl": -5.0,
                },
                "warnings": [],
            },
            [],
            [object()],
        )

    monkeypatch.setattr(backtest_service, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(
        backtest_service,
        "write_artifacts",
        lambda **_kwargs: {"html": "queued-backtest.html"},
    )
    spec = {"start": "2025-01-02", "end": "2025-12-31"}

    with database.SessionLocal() as session:
        portfolio, position, profile, engine = _fixture(session)
        queued, task = backtest_runner.queue_backtest(
            session,
            portfolio_id=portfolio.id,
            spec=spec,
            config={"export_formats": ["json"]},
            pricing_parameter_profile_id=profile.id,
            engine_config_id=engine.id,
            position_ids=[position.id],
        )
        ids = {
            "portfolio": portfolio.id,
            "position": position.id,
            "profile": profile.id,
            "engine": engine.id,
            "queued": queued.id,
            "task": task.id,
        }

    backtest_runner.execute_backtest_task(
        ids["task"],
        ids["queued"],
        session_factory=database.SessionLocal,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        inline = executor.submit(
            backtest_runner.run_persisted_backtest_source,
            session_factory=database.SessionLocal,
            portfolio_id=ids["portfolio"],
            spec=spec,
            config={"export_formats": ["json"]},
            pricing_parameter_profile_id=ids["profile"],
            engine_config_id=ids["engine"],
            position_ids=[ids["position"]],
            write_artifacts=False,
        ).result(timeout=5)

    with database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(TaskRun)) == 1
        assert session.scalar(select(func.count()).select_from(BacktestRun)) == 2
        queued = session.get(BacktestRun, ids["queued"])
        inline_run = session.get(BacktestRun, inline.backtest_run_id)
        assert queued.status == inline_run.status == "completed"
        assert queued.spec == inline_run.spec == spec
        assert queued.config == inline_run.config
        assert queued.pricing_parameter_profile_id == ids["profile"]
        assert inline_run.pricing_parameter_profile_id == ids["profile"]
        assert queued.engine_config_id == inline_run.engine_config_id == ids["engine"]
        assert (
            queued.resolved_position_ids
            == inline_run.resolved_position_ids
            == [ids["position"]]
        )
        assert queued.results == inline_run.results
        assert queued.results["source_metadata"] == {
            "pricing_parameter_profile_id": ids["profile"],
            "engine_config_id": ids["engine"],
            "resolved_position_ids": [ids["position"]],
            "valuation_as_of": "2026-07-14T15:00:00",
        }
        assert queued.artifacts == {"html": "queued-backtest.html"}
        assert inline_run.artifacts == {}

    assert len(pipeline_calls) == 2
    assert pipeline_calls[0] == pipeline_calls[1]


def test_inline_empty_backtest_source_preserves_exclusions_and_warnings(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import BacktestRun
    from app.services import backtest_runner
    from app.services.domains import backtest as backtest_service

    with database.SessionLocal() as session:
        portfolio, position, *_rest = _fixture(session)
        session.commit()
        portfolio_id = portfolio.id
        position_id = position.id

    monkeypatch.setattr(
        backtest_service,
        "run_pipeline",
        lambda *_args, **_kwargs: (
            "empty",
            {"warnings": [{"position_id": position_id, "reason": "no history"}]},
            [{"position_id": position_id, "reason": "unsupported product"}],
            [],
        ),
    )
    result = backtest_runner.run_persisted_backtest_source(
        session_factory=database.SessionLocal,
        portfolio_id=portfolio_id,
        spec={"start": "2025-01-02", "end": "2025-12-31"},
        config={},
        position_ids=[position_id],
        write_artifacts=False,
    )

    with database.SessionLocal() as session:
        run = session.get(BacktestRun, result.backtest_run_id)
        assert run.status == result.status == "empty"
        assert run.results["warnings"][0]["position_id"] == position_id
        assert run.excluded_positions == [
            {"position_id": position_id, "reason": "unsupported product"}
        ]
