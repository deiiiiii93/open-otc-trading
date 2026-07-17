from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
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
        assert session is None
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
        assert queued.scenario_spec["_source_snapshot_v1"]["sha256"].startswith(
            "sha256:"
        )
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
        metadata = queued.results["source_metadata"]
        assert metadata["pricing_parameter_profile_id"] == ids["profile"]
        assert metadata["engine_config_id"] == ids["engine"]
        assert metadata["resolved_position_ids"] == [ids["position"]]
        assert metadata["valuation_as_of"] == "2026-07-15T15:00:00"
        assert metadata["valuation_origin"] == "profile"
        assert metadata["scenario_set_hash"].startswith("sha256:")
        assert metadata["effective_market_evidence_id"].startswith(
            "risk-market-evidence/v1:"
        )
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


def test_scenario_run_freezes_named_set_and_valuation_metadata_at_creation(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import ScenarioTestRun
    from app.services import scenario_test_runner
    from app.services.domains import scenario_catalog
    from app.services.domains import scenario_test as scenario_service

    frozen_specs = [
        {
            "name": "frozen-down",
            "description": "",
            "stresses": [
                {
                    "param": "spot",
                    "stress_type": "PERCENTAGE",
                    "value": -0.1,
                    "level": "portfolio",
                    "target": None,
                }
            ],
        }
    ]
    monkeypatch.setattr(
        scenario_catalog,
        "resolve_scenarios",
        lambda _request: [scenario_catalog.build_custom(frozen_specs[0])],
    )
    seen: list[list[dict]] = []

    def fake_pipeline(_session, *, resolved_scenario_specs, **_kwargs):
        seen.append(resolved_scenario_specs)
        return (
            "completed",
            {
                "scenarios": [
                    {"name": resolved_scenario_specs[0]["name"], "pnl": -10.0}
                ]
            },
            [],
            None,
        )

    monkeypatch.setattr(scenario_service, "run_pipeline", fake_pipeline)
    with database.SessionLocal() as session:
        portfolio, position, profile, _engine = _fixture(session)
        session.commit()
        ids = (portfolio.id, position.id, profile.id)

    mismatch = datetime(2025, 4, 2, 11, 0)
    explicit = datetime(2026, 7, 15, 23, 0, tzinfo=timezone(timedelta(hours=8)))
    with pytest.raises(
        ValueError,
        match="valuation_as_of must equal the selected profile valuation_date",
    ):
        scenario_test_runner.run_persisted_scenario_source(
            session_factory=database.SessionLocal,
            portfolio_id=ids[0],
            position_ids=[ids[1]],
            pricing_parameter_profile_id=ids[2],
            scenario_request={"scenario_set": "desk-set"},
            config={},
            valuation_as_of=mismatch,
            write_artifacts=False,
        )
    before = datetime.utcnow() - timedelta(seconds=2)
    result = scenario_test_runner.run_persisted_scenario_source(
        session_factory=database.SessionLocal,
        portfolio_id=ids[0],
        position_ids=[ids[1]],
        pricing_parameter_profile_id=ids[2],
        scenario_request={"scenario_set": "desk-set"},
        config={},
        valuation_as_of=explicit,
        write_artifacts=False,
    )
    after = datetime.utcnow() + timedelta(seconds=2)

    with database.SessionLocal() as session:
        run = session.get(ScenarioTestRun, result.scenario_test_run_id)
        metadata = run.results["source_metadata"]
        assert before <= run.created_at <= after
        assert metadata["effective_valuation_as_of"] == "2026-07-15T15:00:00"
        assert metadata["valuation_origin"] == "profile"
        assert metadata["profile_valuation_as_of"] == "2026-07-15T15:00:00"
        assert metadata["scenario_set_name"] == "desk-set"
        assert metadata["scenario_set_hash"].startswith("sha256:")
        assert metadata["frozen_scenarios"] == frozen_specs
        assert seen == [frozen_specs]


def test_scenario_engine_resolution_failure_is_not_replaced_by_black_scholes(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services import scenario_test_runner

    with database.SessionLocal() as session:
        portfolio, position, *_rest = _fixture(session)
        session.commit()
        ids = (portfolio.id, position.id)

    monkeypatch.setattr(
        "app.services.engine_configs.resolve_pricing_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("no compatible engine")
        ),
    )
    with pytest.raises(ValueError, match="no compatible engine"):
        scenario_test_runner.run_persisted_scenario_source(
            session_factory=database.SessionLocal,
            portfolio_id=ids[0],
            position_ids=[ids[1]],
            scenario_request={"predefined": ["market_crash"]},
            config={},
            write_artifacts=False,
        )


def test_queued_named_set_uses_frozen_snapshot_and_later_content_drift_changes_hash(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import ScenarioTestRun
    from app.services import scenario_test_runner
    from app.services.domains import scenario_catalog
    from app.services.domains import scenario_test as scenario_service

    current_specs = [
        {
            "name": "desk-down-10",
            "description": "",
            "stresses": [
                {
                    "param": "spot",
                    "stress_type": "PERCENTAGE",
                    "value": -0.1,
                    "level": "portfolio",
                    "target": None,
                }
            ],
        }
    ]
    monkeypatch.setattr(
        scenario_catalog,
        "resolve_scenarios",
        lambda _request: [
            scenario_catalog.build_custom(spec) for spec in current_specs
        ],
    )
    monkeypatch.setattr(
        scenario_test_runner,
        "submit_async_task",
        lambda *_args, **_kwargs: None,
    )
    executed: list[list[dict]] = []

    def fake_pipeline(_session, *, resolved_scenario_specs, **_kwargs):
        executed.append(resolved_scenario_specs)
        return (
            "completed",
            {
                "scenarios": [
                    {
                        "name": resolved_scenario_specs[0]["name"],
                        "pnl": -10.0,
                    }
                ]
            },
            [],
            None,
        )

    monkeypatch.setattr(scenario_service, "run_pipeline", fake_pipeline)
    with database.SessionLocal() as session:
        portfolio, position, *_rest = _fixture(session)
        first, task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=portfolio.id,
            position_ids=[position.id],
            scenario_request={"scenario_set": "desk-set"},
            config={},
        )
        first_id, task_id = first.id, task.id

    original_specs = [dict(current_specs[0])]
    original_specs[0]["stresses"] = [dict(current_specs[0]["stresses"][0])]
    current_specs[0]["name"] = "desk-down-20"
    current_specs[0]["stresses"][0]["value"] = -0.2
    scenario_test_runner.execute_scenario_test_task(
        task_id,
        first_id,
        session_factory=database.SessionLocal,
    )

    with database.SessionLocal() as session:
        first = session.get(ScenarioTestRun, first_id)
        second, _task = scenario_test_runner.queue_scenario_test(
            session,
            portfolio_id=first.portfolio_id,
            position_ids=first.resolved_position_ids,
            scenario_request={"scenario_set": "desk-set"},
            config={},
        )
        first_hash = first.scenario_spec["_source_snapshot_v1"]["sha256"]
        second_hash = second.scenario_spec["_source_snapshot_v1"]["sha256"]

    assert executed == [original_specs]
    assert first_hash != second_hash
