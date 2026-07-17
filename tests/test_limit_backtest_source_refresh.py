from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
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
    prepared = backtest_service.PreparedBacktestPipeline(
        history={},
        configs=(),
        excluded=(),
        notes=(),
        evidence_manifest={
            "schema": "backtest-market-evidence/v1",
            "window": {"start": "2025-01-02", "end": "2025-12-31"},
            "positions": [],
            "underlyings": [],
        },
    )
    monkeypatch.setattr(
        backtest_service,
        "prepare_pipeline_inputs",
        lambda _session, **_kwargs: prepared,
    )

    def fake_pipeline(session, *, positions, progress, **kwargs):
        assert session is None
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
        metadata = queued.results["source_metadata"]
        from app.services.source_evidence import source_metric_contract

        assert metadata["pricing_parameter_profile_id"] == ids["profile"]
        assert metadata["engine_config_id"] == ids["engine"]
        assert metadata["resolved_position_ids"] == [ids["position"]]
        assert metadata["valuation_as_of"] == "2026-07-14T15:00:00"
        assert metadata["valuation_origin"] == "profile"
        assert metadata["effective_market_evidence_id"].startswith(
            "backtest-market-evidence/v1:"
        )
        assert metadata["metric_contract"] == source_metric_contract("backtest")
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
    monkeypatch.setattr(
        backtest_service,
        "prepare_pipeline_inputs",
        lambda _session, **_kwargs: backtest_service.PreparedBacktestPipeline(
            history={},
            configs=(),
            excluded=(),
            notes=(),
            evidence_manifest={
                "schema": "backtest-market-evidence/v1",
                "window": {"start": "2025-01-02", "end": "2025-12-31"},
                "positions": [position_id],
                "underlyings": [],
            },
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


def test_backtest_explicit_valuation_is_authoritative_and_compute_has_no_session(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import BacktestRun
    from app.services import backtest_runner
    from app.services.domains import backtest as backtest_service

    prepared = backtest_service.PreparedBacktestPipeline(
        history={},
        configs=(),
        excluded=(),
        notes=(),
        evidence_manifest={
            "schema": "backtest-market-evidence/v1",
            "window": {"start": "2025-01-02", "end": "2025-02-03"},
            "positions": [],
            "underlyings": [],
        },
    )
    monkeypatch.setattr(
        backtest_service,
        "prepare_pipeline_inputs",
        lambda _session, **_kwargs: prepared,
    )

    def fake_pipeline(session, **_kwargs):
        assert session is None
        return ("completed", {"portfolio": {"var_95": 1.0}}, [], [])

    monkeypatch.setattr(backtest_service, "run_pipeline", fake_pipeline)
    with database.SessionLocal() as session:
        portfolio, position, profile, _engine = _fixture(session)
        session.commit()
        ids = (portfolio.id, position.id, profile.id)

    mismatch = datetime(2025, 3, 4, 15, 0)
    explicit = datetime(2026, 7, 14, 23, 0, tzinfo=timezone(timedelta(hours=8)))
    with pytest.raises(
        ValueError,
        match="valuation_as_of must equal the selected profile valuation_date",
    ):
        backtest_runner.run_persisted_backtest_source(
            session_factory=database.SessionLocal,
            portfolio_id=ids[0],
            position_ids=[ids[1]],
            pricing_parameter_profile_id=ids[2],
            spec={"start": "2025-01-02", "end": "2025-02-03"},
            config={},
            valuation_as_of=mismatch,
            write_artifacts=False,
        )
    before = datetime.utcnow() - timedelta(seconds=2)
    result = backtest_runner.run_persisted_backtest_source(
        session_factory=database.SessionLocal,
        portfolio_id=ids[0],
        position_ids=[ids[1]],
        pricing_parameter_profile_id=ids[2],
        spec={"start": "2025-01-02", "end": "2025-02-03"},
        config={},
        valuation_as_of=explicit,
        write_artifacts=False,
    )
    after = datetime.utcnow() + timedelta(seconds=2)

    with database.SessionLocal() as session:
        run = session.get(BacktestRun, result.backtest_run_id)
        metadata = run.results["source_metadata"]
        assert before <= run.created_at <= after
        assert metadata["effective_valuation_as_of"] == "2026-07-14T15:00:00"
        assert metadata["valuation_origin"] == "profile"
        assert metadata["profile_valuation_as_of"] == "2026-07-14T15:00:00"
        assert metadata["effective_market_evidence_id"].startswith(
            "backtest-market-evidence/v1:"
        )


def test_backtest_rejects_decorative_point_in_time_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import MarketSnapshot
    from app.services import backtest_runner

    with database.SessionLocal() as session:
        portfolio, position, *_rest = _fixture(session)
        snapshot = MarketSnapshot(
            name="Point snapshot",
            source="test",
            symbol="AAPL",
            valuation_date=datetime(2026, 7, 14),
            data={"spot": 100.0},
            source_metadata={},
        )
        session.add(snapshot)
        session.commit()
        ids = (portfolio.id, position.id, snapshot.id)

    with pytest.raises(ValueError, match="not supported for historical backtest"):
        backtest_runner.run_persisted_backtest_source(
            session_factory=database.SessionLocal,
            portfolio_id=ids[0],
            position_ids=[ids[1]],
            market_snapshot_id=ids[2],
            spec={"start": "2025-01-02", "end": "2025-02-03"},
            config={},
            write_artifacts=False,
        )


def test_backtest_engine_resolution_failure_is_not_replaced_by_black_scholes(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services import backtest_runner

    with database.SessionLocal() as session:
        portfolio, position, *_rest = _fixture(session)
        session.commit()
        ids = (portfolio.id, position.id)

    monkeypatch.setattr(
        "app.services.engine_configs.resolve_pricing_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("no compatible backtest pricing engine")
        ),
    )
    with pytest.raises(ValueError, match="no compatible backtest pricing engine"):
        backtest_runner.run_persisted_backtest_source(
            session_factory=database.SessionLocal,
            portfolio_id=ids[0],
            position_ids=[ids[1]],
            spec={"start": "2025-01-02", "end": "2025-02-03"},
            config={},
            write_artifacts=False,
        )


def test_backtest_prep_failure_marks_market_evidence_incomplete(monkeypatch) -> None:
    from app.services.domains import backtest as backtest_service
    from app.services.source_evidence import finalize_market_metadata

    position = SimpleNamespace(
        id=7,
        underlying="AAPL",
        product_id=None,
        product_type="EuropeanVanillaOption",
        product_kwargs={},
        quantity=1.0,
        entry_price=0.0,
        currency="USD",
        engine_name="BlackScholesEngine",
        engine_kwargs={},
    )
    monkeypatch.setattr(
        backtest_service.mh,
        "ensure_spot_history",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("feed down")),
    )
    monkeypatch.setattr(
        backtest_service.backtest_bridge,
        "build_books",
        lambda *_args, **_kwargs: ([], []),
    )

    prepared = backtest_service.prepare_pipeline_inputs(
        object(),
        positions=[position],
        spec={"start": "2025-01-02", "end": "2025-01-03"},
    )

    manifest = prepared.evidence_manifest
    metadata = finalize_market_metadata({}, manifest, namespace="backtest-market-evidence/v1")
    assert manifest["evidence_complete"] is False
    assert manifest["missing_evidence"] == [
        "underlying:AAPL:market_data_prep_failed"
    ]
    assert manifest["underlyings"][0]["position_ids"] == [7]
    assert metadata["market_evidence_complete"] is False


def test_backtest_engine_failure_excludes_its_position_scope(monkeypatch) -> None:
    from quantark.backtest import otc

    from app.services.domains import backtest as backtest_service

    failed = SimpleNamespace(
        underlying="AAPL",
        products=[SimpleNamespace(position_id=7)],
    )
    succeeded = SimpleNamespace(
        underlying="MSFT",
        products=[SimpleNamespace(position_id=8)],
    )
    prepared = backtest_service.PreparedBacktestPipeline(
        history={},
        configs=(failed, succeeded),
        excluded=(),
        notes=(),
        evidence_manifest={},
    )

    class FakeEngine:
        def __init__(self, config):
            self.config = config

        def run(self):
            if self.config.underlying == "AAPL":
                raise RuntimeError("engine boom")
            return object()

    monkeypatch.setattr(otc, "BookAutocallableBacktestEngine", FakeEngine)
    monkeypatch.setattr(
        backtest_service,
        "_shape_underlying",
        lambda config, _result: {
            "underlying": config.underlying,
            "summary": {"total_pnl": 1.0, "hedge_pnl": 0.0, "num_trades": 1},
            "pnl_series": [{"date": "2025-01-03", "total_pnl": 1.0}],
        },
    )

    status, _results, excluded, _raw = backtest_service.run_prepared_pipeline(
        prepared,
        positions=[],
        spec={"start": "2025-01-02", "end": "2025-01-03"},
        config={},
        portfolio_name="test",
    )

    assert status == "completed"
    assert excluded == [
        {
            "position_id": 7,
            "reason": "backtest engine run failed for AAPL: engine boom",
        }
    ]
