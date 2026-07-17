from __future__ import annotations

from datetime import datetime, timedelta

import pytest


NOW = datetime(2026, 7, 17, 12, 0)


def _database(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/source-plan.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _fixture(session):
    from app.models import (
        EngineConfigVariant,
        MarketSnapshot,
        Portfolio,
        PricingParameterProfile,
    )

    portfolio = Portfolio(name="Source plan", base_currency="USD")
    profile = PricingParameterProfile(
        name="Pinned",
        valuation_date=NOW,
        source_type="xlsx",
        status="completed",
        summary={},
    )
    engine = EngineConfigVariant(
        name="Pinned engines",
        status="active",
        is_default=False,
        rules={"rules": []},
    )
    market = MarketSnapshot(
        name="Pinned market",
        source="test",
        symbol="AAPL",
        valuation_date=NOW,
        data={},
        source_metadata={},
    )
    session.add_all([portfolio, profile, engine, market])
    session.flush()
    return portfolio, profile, engine, market


def _key(
    source_kind: str,
    ids: dict[str, int],
    **overrides,
):
    from app.services.limits.source_planner import SourcePlanKey
    from app.services.limits.sources import (
        BACKTEST_TAIL_METHODOLOGY,
        SCENARIO_TAIL_METHODOLOGY,
    )

    methodologies = {
        "risk_run": {"method": "summary"},
        "scenario_test": SCENARIO_TAIL_METHODOLOGY,
        "backtest": BACKTEST_TAIL_METHODOLOGY,
    }
    configs = {
        "risk_run": {},
        "scenario_test": {
            "scenario_request": {"predefined": ["market_crash"]},
            "config": {"calculate_greeks": True},
        },
        "backtest": {
            "spec": {"start": "2025-01-02", "end": "2025-12-31"},
            "config": {"export_formats": []},
        },
    }
    values = {
        "source_kind": source_kind,
        "portfolio_id": ids["portfolio"],
        "position_ids": [3, 1],
        "pricing_parameter_profile_id": ids["profile"],
        "engine_config_id": ids["engine"],
        "market_snapshot_id": ids["market"] if source_kind == "risk_run" else None,
        "effective_market_evidence_id": (
            None
        ),
        "methodology": methodologies[source_kind],
        "config": configs[source_kind],
        "valuation_policy": {"valuation_as_of": NOW.isoformat()},
        "freshness_policy": {"max_age_seconds": 3600},
    }
    values.update(overrides)
    return SourcePlanKey.create(**values)


def _source_run(session, source_kind: str, key, *, status: str, created_at: datetime):
    from app.models import BacktestRun, RiskRun, ScenarioTestRun

    metadata = {
        "valuation_as_of": NOW.isoformat(),
        "effective_market_evidence_id": key.effective_market_evidence_id,
    }
    if source_kind == "risk_run":
        row = RiskRun(
            portfolio_id=key.portfolio_id,
            pricing_parameter_profile_id=key.pricing_parameter_profile_id,
            engine_config_id=key.engine_config_id,
            market_snapshot_id=key.market_snapshot_id,
            resolved_position_ids=list(key.position_ids),
            method=key.methodology["method"],
            status=status,
            metrics={
                "valuation_as_of": NOW.isoformat(),
                "source_metadata": metadata,
            },
            created_at=created_at,
        )
    elif source_kind == "scenario_test":
        row = ScenarioTestRun(
            portfolio_id=key.portfolio_id,
            pricing_parameter_profile_id=key.pricing_parameter_profile_id,
            engine_config_id=key.engine_config_id,
            resolved_position_ids=list(key.position_ids),
            status=status,
            scenario_spec=key.config["scenario_request"],
            config=key.config["config"],
            results={"source_metadata": metadata},
            excluded_positions=[],
            artifacts={},
            created_at=created_at,
        )
    else:
        row = BacktestRun(
            portfolio_id=key.portfolio_id,
            pricing_parameter_profile_id=key.pricing_parameter_profile_id,
            engine_config_id=key.engine_config_id,
            resolved_position_ids=list(key.position_ids),
            status=status,
            spec=key.config["spec"],
            config=key.config["config"],
            results={"source_metadata": metadata},
            excluded_positions=[],
            artifacts={},
            created_at=created_at,
        )
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize("source_kind", ["risk_run", "scenario_test", "backtest"])
def test_reuse_only_and_force_refresh_for_all_sources(
    tmp_path,
    monkeypatch,
    source_kind: str,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import select_source

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key(source_kind, ids)
        reusable = _source_run(
            session,
            source_kind,
            key,
            status="completed",
            created_at=NOW - timedelta(minutes=10),
        )
        session.commit()

        reused = select_source(
            session,
            key,
            policy="reuse_only",
            now=NOW,
        )
        refreshed_run = object()
        forced = select_source(
            session,
            key,
            policy="force_refresh",
            now=NOW,
            refresh=lambda _key: refreshed_run,
        )

        assert reused.run.id == reusable.id
        assert reused.reused is True
        assert forced.run is refreshed_run
        assert forced.reused is False


def test_newer_queued_run_does_not_mask_older_eligible_terminal(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import select_source

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key("risk_run", ids)
        terminal = _source_run(
            session,
            "risk_run",
            key,
            status="completed_with_errors",
            created_at=NOW - timedelta(minutes=20),
        )
        _source_run(
            session,
            "risk_run",
            key,
            status="queued",
            created_at=NOW - timedelta(minutes=1),
        )
        session.commit()

        selection = select_source(
            session,
            key,
            policy="reuse_only",
            now=NOW,
        )

        assert selection.run.id == terminal.id
        assert selection.reused is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pricing_parameter_profile_id", 9991),
        ("engine_config_id", 9992),
        ("market_snapshot_id", 9993),
        ("effective_market_evidence_id", "different-evidence"),
        ("position_ids", [1, 4]),
        ("methodology", {"method": "full_revaluation"}),
        ("config", {"unexpected": True}),
        ("valuation_policy", {"valuation_as_of": "2026-07-16T12:00:00"}),
    ],
)
def test_reuse_requires_exact_source_identity(
    tmp_path,
    monkeypatch,
    field: str,
    value,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import select_source

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        stored_key = _key(
            "risk_run",
            ids,
            effective_market_evidence_id="risk-evidence",
        )
        _source_run(
            session,
            "risk_run",
            stored_key,
            status="completed",
            created_at=NOW - timedelta(minutes=10),
        )
        session.commit()
        requested_overrides = {
            "effective_market_evidence_id": "risk-evidence",
            field: value,
        }
        requested_key = _key("risk_run", ids, **requested_overrides)

        selection = select_source(
            session,
            requested_key,
            policy="reuse_only",
            now=NOW,
        )

        assert selection.run is None
        assert selection.reason_code == "missing_source"


def test_refresh_if_stale_and_reuse_only_stale_diagnostics(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import select_source

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key(
            "risk_run",
            ids,
            freshness_policy={"max_age_seconds": 60},
            valuation_policy={},
        )
        _source_run(
            session,
            "risk_run",
            key,
            status="completed",
            created_at=NOW - timedelta(minutes=2),
        )
        session.commit()
        stale = select_source(
            session,
            key,
            policy="reuse_only",
            now=NOW,
        )
        fresh_run = object()
        refreshed = select_source(
            session,
            key,
            policy="refresh_if_stale",
            now=NOW,
            refresh=lambda _key: fresh_run,
        )

        assert stale.run is None
        assert stale.reason_code == "stale_source"
        assert refreshed.run is fresh_run
        assert refreshed.reused is False


def test_canonical_grouping_sorts_scope_and_mapping_keys() -> None:
    from app.services.limits.source_planner import (
        SourcePlanRequest,
        group_source_plans,
    )

    ids = {"portfolio": 1, "profile": 2, "engine": 3, "market": 4}
    first = _key(
        "risk_run",
        ids,
        position_ids=[3, 1],
        methodology={"method": "summary", "nested": {"b": 2, "a": 1}},
    )
    second = _key(
        "risk_run",
        ids,
        position_ids=[1, 3],
        methodology={"nested": {"a": 1, "b": 2}, "method": "summary"},
    )

    groups = group_source_plans(
        [
            SourcePlanRequest(limit_version_id=11, key=first),
            SourcePlanRequest(limit_version_id=12, key=second),
        ]
    )

    assert len(groups) == 1
    assert groups[0].limit_version_ids == (11, 12)
    assert groups[0].key.position_ids == (1, 3)


def test_persisted_source_reference_contains_requested_and_diagnostics(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import LimitMonitoringRun, LimitSourceReference
    from app.services.limits.source_planner import persist_source_reference

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key("risk_run", ids)
        source = _source_run(
            session,
            "risk_run",
            key,
            status="completed",
            created_at=NOW - timedelta(minutes=5),
        )
        monitoring = LimitMonitoringRun(
            trigger="manual",
            mode="interactive",
            portfolio_id=portfolio.id,
            pricing_parameter_profile_id=profile.id,
            engine_config_id=engine.id,
            market_snapshot_id=market.id,
            valuation_as_of=NOW,
            source_policy="reuse_only",
            max_source_age_seconds=3600,
            status="running",
            summary={},
            definition_snapshot={},
            definition_snapshot_hash="a" * 64,
        )
        session.add(monitoring)
        session.flush()
        reference = persist_source_reference(
            session,
            monitoring_run_id=monitoring.id,
            key=key,
            source=source,
            is_fresh=True,
            diagnostics={
                "reason_code": None,
                "requested_position_ids": [1, 3],
                "covered_position_ids": [1, 3],
            },
        )
        session.commit()

        persisted = session.get(LimitSourceReference, reference.id)
        assert persisted.risk_run_id == source.id
        assert persisted.scenario_test_run_id is None
        assert persisted.backtest_run_id is None
        assert persisted.source_status == "completed"
        assert persisted.is_fresh is True
        assert persisted.requested_parameters["position_ids"] == [1, 3]
        assert persisted.completeness_diagnostics["covered_position_ids"] == [1, 3]
        assert persisted.source_valuation_at == NOW
