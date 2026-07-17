from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from sqlalchemy import func, select


def _database(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/scenario-source.db")
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

    portfolio = Portfolio(name="Scenario limits source", base_currency="USD")
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
        name="Scenario pinned profile",
        valuation_date=datetime(2026, 7, 15, 15, 0),
        source_type="xlsx",
        status="completed",
        summary={},
    )
    engine = EngineConfigVariant(
        name="Scenario limit engines",
        status="active",
        is_default=False,
        rules={"rules": []},
    )
    session.add_all([position, profile, engine])
    session.flush()
    return portfolio, position, profile, engine


def test_inline_scenario_source_matches_queue_without_child_task_and_artifacts(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import ScenarioTestRun, TaskRun
    from app.services import scenario_test_runner
    from app.services.domains import scenario_test as scenario_service

    monkeypatch.setattr(
        scenario_test_runner,
        "submit_async_task",
        lambda *_args, **_kwargs: None,
    )
    pipeline_calls: list[dict] = []

    def fake_pipeline(session, *, positions, **kwargs):
        assert not session.new
        assert not session.dirty
        assert not session.deleted
        pipeline_calls.append(
            {
                "position_ids": [position.id for position in positions],
                **kwargs,
            }
        )
        return (
            "completed",
            {
                "scenarios": [{"name": "market_crash", "pnl": -25.0}],
                "var_cvar": {"var": 20.0, "cvar": 25.0, "confidence": 0.95},
                "pricing_warnings": [],
            },
            [],
            object(),
        )

    monkeypatch.setattr(scenario_service, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(
        scenario_service,
        "write_artifacts",
        lambda **_kwargs: {"report_html_path": "queued-report.html"},
    )

    with database.SessionLocal() as session:
        portfolio, position, profile, engine = _fixture(session)
        queued, task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=portfolio.id,
            scenario_request={"predefined": ["market_crash"]},
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

    scenario_test_runner.execute_scenario_test_task(
        ids["task"],
        ids["queued"],
        session_factory=database.SessionLocal,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        inline = executor.submit(
            scenario_test_runner.run_persisted_scenario_source,
            session_factory=database.SessionLocal,
            portfolio_id=ids["portfolio"],
            scenario_request={"predefined": ["market_crash"]},
            config={"export_formats": ["json"]},
            pricing_parameter_profile_id=ids["profile"],
            engine_config_id=ids["engine"],
            position_ids=[ids["position"]],
            write_artifacts=False,
        ).result(timeout=5)

    with database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(TaskRun)) == 1
        assert (
            session.scalar(select(func.count()).select_from(ScenarioTestRun)) == 2
        )
        queued = session.get(ScenarioTestRun, ids["queued"])
        inline_run = session.get(ScenarioTestRun, inline.scenario_test_run_id)
        assert queued.status == inline_run.status == "completed"
        assert queued.scenario_spec == inline_run.scenario_spec
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
            "valuation_as_of": "2026-07-15T15:00:00",
        }
        assert queued.artifacts == {"report_html_path": "queued-report.html"}
        assert inline_run.artifacts == {}

    assert len(pipeline_calls) == 2
    assert pipeline_calls[0] == pipeline_calls[1]


def test_inline_empty_scenario_source_preserves_exclusions_and_warnings(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import ScenarioTestRun
    from app.services import scenario_test_runner
    from app.services.domains import scenario_test as scenario_service

    with database.SessionLocal() as session:
        portfolio, position, *_rest = _fixture(session)
        session.commit()
        portfolio_id = portfolio.id
        position_id = position.id

    monkeypatch.setattr(
        scenario_service,
        "run_pipeline",
        lambda *_args, **_kwargs: (
            "empty",
            {
                "scenarios": [],
                "pricing_warnings": [
                    {"position_id": position_id, "reason": "missing profile row"}
                ],
            },
            [{"position_id": position_id, "reason": "unsupported product"}],
            None,
        ),
    )
    result = scenario_test_runner.run_persisted_scenario_source(
        session_factory=database.SessionLocal,
        portfolio_id=portfolio_id,
        scenario_request={"predefined": ["market_crash"]},
        config={},
        position_ids=[position_id],
        write_artifacts=False,
    )

    with database.SessionLocal() as session:
        run = session.get(ScenarioTestRun, result.scenario_test_run_id)
        assert run.status == result.status == "empty"
        assert run.results["pricing_warnings"][0]["position_id"] == position_id
        assert run.excluded_positions == [
            {"position_id": position_id, "reason": "unsupported product"}
        ]
