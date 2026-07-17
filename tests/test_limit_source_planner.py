from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 7, 17, 12, 0)


def _metric_contract(source_kind: str) -> dict:
    from app.services.source_evidence import source_metric_contract

    return source_metric_contract(source_kind)


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
            "scenario_set_hash": "sha256:" + ("a" * 64),
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
            "external-market-evidence/v1:fixture"
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
        "effective_valuation_as_of": NOW.isoformat(),
        "valuation_origin": "explicit",
        "effective_market_evidence_id": key.effective_market_evidence_id,
        "methodology": key.methodology,
        "source_config": key.config,
        "metric_contract": _metric_contract(source_kind),
    }
    if source_kind == "scenario_test":
        metadata["scenario_set_hash"] = key.config["scenario_set_hash"]
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
        refreshed_run = _source_run(
            session,
            source_kind,
            key,
            status="completed",
            created_at=NOW,
        )
        session.commit()
        forced = select_source(
            database.SessionLocal,
            key,
            policy="force_refresh",
            now=NOW,
            refresh=lambda _key: refreshed_run,
        )

        assert reused.run.id == reusable.id
        assert reused.reused is True
        assert forced.run.id == refreshed_run.id
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


def test_reuse_rejects_missing_or_mutated_metric_contract(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source

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
            created_at=NOW,
        )
        metadata = source.metrics["source_metadata"]
        metadata.pop("metric_contract")
        source.metrics = {**source.metrics, "source_metadata": metadata}
        session.commit()

        missing = find_reusable_source(session, key, now=NOW)

        metadata["metric_contract"] = _metric_contract("risk_run")
        metadata["metric_contract"]["metrics"]["rho"]["bump_convention"] = (
            "parallel_rate_1bp"
        )
        source.metrics = {**source.metrics, "source_metadata": metadata}
        session.commit()
        mutated = find_reusable_source(session, key, now=NOW)

        assert missing.run is None
        assert missing.reason_code == "missing_source"
        assert mutated.run is None
        assert mutated.reason_code == "missing_source"


@pytest.mark.parametrize("source_kind", ["scenario_test", "backtest"])
def test_newer_partial_tail_source_does_not_mask_older_complete_source(
    tmp_path,
    monkeypatch,
    source_kind: str,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key(source_kind, ids)
        complete = _source_run(
            session,
            source_kind,
            key,
            status="completed",
            created_at=NOW - timedelta(seconds=10),
        )
        partial = _source_run(
            session,
            source_kind,
            key,
            status="completed",
            created_at=NOW,
        )
        partial.excluded_positions = [
            {"position_id": key.position_ids[0], "reason": "engine failed"}
        ]
        session.commit()

        selected = find_reusable_source(session, key, now=NOW)

        assert selected.run.id == complete.id
        assert selected.run.id != partial.id
        assert selected.reused is True


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
    def refresh(_key):
        assert database.engine.pool.checkedout() == 0
        with database.SessionLocal() as refresh_session:
            fresh_run = _source_run(
                refresh_session,
                "risk_run",
                key,
                status="completed",
                created_at=NOW,
            )
            refresh_session.commit()
            return SimpleNamespace(risk_run_id=fresh_run.id)

    refreshed = select_source(
        database.SessionLocal,
        key,
        policy="refresh_if_stale",
        now=NOW,
        refresh=refresh,
    )

    assert stale.run is not None
    assert stale.reason_code == "stale_source"
    assert refreshed.run.created_at == NOW
    assert refreshed.reused is False


def test_newer_profile_dated_stale_source_does_not_mask_older_fresh_exact(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source

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
        )
        older_fresh = _source_run(
            session,
            "risk_run",
            key,
            status="completed",
            created_at=NOW - timedelta(seconds=10),
        )
        newer_profile_dated = _source_run(
            session,
            "risk_run",
            key,
            status="completed",
            created_at=NOW,
        )
        metadata = newer_profile_dated.metrics["source_metadata"]
        metadata["valuation_origin"] = "profile"
        newer_profile_dated.metrics = {
            **newer_profile_dated.metrics,
            "source_metadata": metadata,
        }
        session.commit()

        selection = find_reusable_source(session, key, now=NOW)

        assert selection.run.id == older_fresh.id
        assert selection.reused is True
        assert selection.reason_code is None


def test_risk_source_with_valid_granular_market_evidence_remains_reusable(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source

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
            status="completed_with_errors",
            created_at=NOW,
        )
        metadata = source.metrics["source_metadata"]
        metadata.update(
            {
                "market_evidence_complete": False,
                "missing_market_evidence": ["position:1:missing:spot"],
            }
        )
        source.metrics = {**source.metrics, "source_metadata": metadata}
        session.commit()

        selection = find_reusable_source(session, key, now=NOW)

        assert selection.run.id == source.id
        assert selection.reused is True


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

        with pytest.raises(
            ValueError,
            match="source instance type does not match requested source kind",
        ):
            persist_source_reference(
                session,
                monitoring_run_id=monitoring.id,
                key=_key("scenario_test", ids),
                source=source,
                is_fresh=True,
                diagnostics={},
            )


def test_source_plan_never_fabricates_profile_evidence_identity() -> None:
    from app.services.limits.source_planner import SourcePlanKey

    with pytest.raises(
        ValueError,
        match="market_snapshot_id or effective_market_evidence_id is required",
    ):
        SourcePlanKey.create(
            source_kind="scenario_test",
            portfolio_id=1,
            position_ids=[1],
            pricing_parameter_profile_id=7,
            engine_config_id=None,
            market_snapshot_id=None,
            effective_market_evidence_id=None,
            methodology={},
            config={},
            valuation_policy={},
            freshness_policy={"max_age_seconds": 30},
        )


def test_snapshot_only_plan_accepts_exact_canonical_snapshot_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import Position, PricingParameterRow, RiskRun
    from app.services.engine_configs import resolve_pricing_engine
    from app.services.limits.source_planner import (
        SourcePlanKey,
        find_reusable_source,
    )
    from app.services.risk_engine import _pricing_position_context
    from app.services.source_evidence import (
        build_market_evidence_manifest,
        finalize_market_metadata,
    )

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        market.data = {"spot": 100.0}
        position = Position(
            portfolio_id=portfolio.id,
            underlying="AAPL",
            source_trade_id="SNAPSHOT-ONLY-1",
            product_type="EuropeanVanillaOption",
            product_kwargs={
                "strike": 100.0,
                "option_type": "CALL",
                "maturity": 1.0,
            },
            engine_name="BlackScholesEngine",
            engine_kwargs={},
            quantity=1.0,
            entry_price=8.0,
            currency="USD",
        )
        session.add(position)
        session.flush()
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=position.source_trade_id,
                symbol=position.underlying,
                rate=0.01,
                dividend_yield=0.02,
                volatility=0.25,
            )
        )
        session.flush()
        markets, _failures, diagnostics = _pricing_position_context(
            session,
            [position],
            pricing_parameter_profile_id=profile.id,
            valuation_date=NOW,
            market_snapshot_id=market.id,
        )
        diagnostics[position.id]["resolved_engine"] = resolve_pricing_engine(
            position,
            engine,
        ).diagnostics()
        metadata = finalize_market_metadata(
            {
                "valuation_as_of": NOW.isoformat(),
                "effective_valuation_as_of": NOW.isoformat(),
                "valuation_origin": "explicit",
                "market_snapshot_id": market.id,
                "methodology": {"method": "summary"},
                "source_config": {},
                "metric_contract": _metric_contract("risk_run"),
            },
            build_market_evidence_manifest(
                session,
                positions=[position],
                position_markets=markets,
                pricing_diagnostics=diagnostics,
                valuation_as_of=NOW,
                market_snapshot_id=market.id,
            ),
        )
        source = RiskRun(
            portfolio_id=portfolio.id,
            pricing_parameter_profile_id=profile.id,
            engine_config_id=engine.id,
            market_snapshot_id=market.id,
            resolved_position_ids=[position.id],
            method="summary",
            status="completed",
            metrics={
                "valuation_as_of": NOW.isoformat(),
                "source_metadata": metadata,
            },
            created_at=NOW,
        )
        session.add(source)
        session.commit()

        def plan(evidence_id):
            return SourcePlanKey.create(
                source_kind="risk_run",
                portfolio_id=portfolio.id,
                position_ids=[position.id],
                pricing_parameter_profile_id=profile.id,
                engine_config_id=engine.id,
                market_snapshot_id=market.id,
                effective_market_evidence_id=evidence_id,
                methodology={"method": "summary"},
                config={},
                valuation_policy={"valuation_as_of": NOW.isoformat()},
                freshness_policy={"max_age_seconds": 60},
            )

        snapshot_only = find_reusable_source(session, plan(None), now=NOW)
        wrong_explicit = find_reusable_source(
            session,
            plan("risk-market-evidence/v1:" + ("f" * 64)),
            now=NOW,
        )

        assert snapshot_only.run.id == source.id
        assert wrong_explicit.run is None

        tampered = dict(source.metrics["source_metadata"])
        tampered["effective_market_evidence_id"] = (
            "risk-market-evidence/v1:" + ("e" * 64)
        )
        source.metrics = {**source.metrics, "source_metadata": tampered}
        session.commit()

        invalid_canonical = find_reusable_source(session, plan(None), now=NOW)
        assert invalid_canonical.run is None
        assert invalid_canonical.reason_code == "missing_source"


def test_freshness_uses_creation_age_and_profile_origin_requires_opt_in(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source

    aware_now = NOW.replace(tzinfo=timezone.utc)
    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        disallowed_key = _key(
            "risk_run",
            ids,
            freshness_policy={"max_age_seconds": 60},
            valuation_policy={"valuation_as_of": "2020-01-02T12:00:00"},
        )
        source = _source_run(
            session,
            "risk_run",
            disallowed_key,
            status="completed",
            created_at=NOW - timedelta(seconds=10),
        )
        metadata = source.metrics["source_metadata"]
        metadata["valuation_as_of"] = "2020-01-02T12:00:00"
        metadata["effective_valuation_as_of"] = "2020-01-02T12:00:00"
        metadata["valuation_origin"] = "profile"
        source.metrics = {
            **source.metrics,
            "valuation_as_of": "2020-01-02T12:00:00",
            "source_metadata": metadata,
        }
        session.commit()

        disallowed = find_reusable_source(
            session,
            disallowed_key,
            now=aware_now,
        )
        allowed = find_reusable_source(
            session,
            _key(
                "risk_run",
                ids,
                freshness_policy={
                    "max_age_seconds": 60,
                    "allow_profile_dated": True,
                },
                valuation_policy={"valuation_as_of": "2020-01-02T12:00:00"},
            ),
            now=aware_now,
        )
        metadata["valuation_origin"] = "explicit"
        source.metrics = {**source.metrics, "source_metadata": metadata}
        session.commit()
        explicit_historical = find_reusable_source(
            session,
            disallowed_key,
            now=aware_now,
        )

        assert disallowed.is_fresh is False
        assert disallowed.reason_code == "stale_source"
        assert allowed.is_fresh is True
        assert explicit_historical.is_fresh is True


@pytest.mark.parametrize(
    ("refresh_value", "expected_reason"),
    [
        (None, "refresh_failed"),
        ("wrong-kind", "refresh_failed"),
    ],
)
def test_refresh_never_blindly_declares_success(
    tmp_path,
    monkeypatch,
    refresh_value,
    expected_reason: str,
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
        session.commit()

    selected = select_source(
        database.SessionLocal,
        key,
        policy="force_refresh",
        now=NOW,
        refresh=lambda _key: refresh_value,
    )

    assert selected.run is None
    assert selected.is_fresh is False
    assert selected.reason_code == expected_reason


def test_missing_source_reference_persists_nullable_evidence(
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
        monitoring = LimitMonitoringRun(
            trigger="manual",
            mode="interactive",
            portfolio_id=portfolio.id,
            valuation_as_of=NOW,
            source_policy="reuse_only",
            status="running",
            summary={},
            definition_snapshot={},
            definition_snapshot_hash="b" * 64,
        )
        session.add(monitoring)
        session.flush()
        reference = persist_source_reference(
            session,
            monitoring_run_id=monitoring.id,
            key=key,
            source=None,
            source_status="missing",
            is_fresh=False,
            diagnostics={"reason_code": "missing_source"},
        )
        session.commit()

        row = session.get(LimitSourceReference, reference.id)
        assert row.source_status == "missing"
        assert row.risk_run_id is None
        assert row.source_created_at is None
        assert row.source_valuation_at is None


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        ("failed", "source_failed"),
        ("empty", "empty_source"),
    ],
)
def test_refresh_failed_or_empty_source_is_not_fresh(
    tmp_path,
    monkeypatch,
    status: str,
    expected_reason: str,
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
        refreshed = _source_run(
            session,
            "risk_run",
            key,
            status=status,
            created_at=NOW,
        )
        session.commit()
        refreshed_id = refreshed.id

    selection = select_source(
        database.SessionLocal,
        key,
        policy="force_refresh",
        now=NOW,
        refresh=lambda _key: SimpleNamespace(risk_run_id=refreshed_id),
    )

    assert selection.run.id == refreshed_id
    assert selection.is_fresh is False
    assert selection.reason_code == expected_reason


@pytest.mark.parametrize("changed_field", ["product_kwargs", "engine_kwargs"])
def test_backtest_reuse_revalidates_position_and_engine_content_hashes(
    tmp_path,
    monkeypatch,
    changed_field: str,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import BacktestRun, EngineConfigVariant, Portfolio, Position
    from app.services.limits.source_planner import SourcePlanKey, find_reusable_source
    from app.services.limits.sources import BACKTEST_TAIL_METHODOLOGY
    from app.services.source_evidence import canonical_hash

    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Backtest evidence", base_currency="USD")
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
            engine_kwargs={"steps": 101},
            quantity=2.0,
            entry_price=7.5,
            currency="USD",
        )
        engine = EngineConfigVariant(
            name="Backtest evidence engines",
            status="active",
            is_default=False,
            rules={"rules": []},
        )
        session.add_all([position, engine])
        session.flush()
        manifest = {
            "schema": "backtest-market-evidence/v1",
            "window": {"start": "2025-01-02", "end": "2025-12-31"},
            "positions": [
                {
                    "position_id": position.id,
                    "economic_input_hash": canonical_hash(
                        {
                            "product_id": position.product_id,
                            "product_type": position.product_type,
                            "product_kwargs": position.product_kwargs,
                            "quantity": position.quantity,
                            "entry_price": position.entry_price,
                            "currency": position.currency,
                        }
                    ),
                    "resolved_engine_hash": canonical_hash(
                        {
                            "engine_name": position.engine_name,
                            "engine_kwargs": position.engine_kwargs,
                        }
                    ),
                }
            ],
            "underlyings": [],
        }
        evidence_hash = canonical_hash(manifest)
        evidence_id = (
            "backtest-market-evidence/v1:"
            f"{evidence_hash.removeprefix('sha256:')}"
        )
        config = {
            "spec": {"start": "2025-01-02", "end": "2025-12-31"},
            "config": {"export_formats": []},
        }
        metadata = {
            "valuation_as_of": NOW.isoformat(),
            "effective_valuation_as_of": NOW.isoformat(),
            "valuation_origin": "explicit",
            "effective_market_evidence_id": evidence_id,
            "market_evidence_hash": evidence_hash,
            "market_evidence_manifest": manifest,
            "methodology": BACKTEST_TAIL_METHODOLOGY,
            "source_config": config,
            "metric_contract": _metric_contract("backtest"),
        }
        run = BacktestRun(
            portfolio_id=portfolio.id,
            engine_config_id=engine.id,
            resolved_position_ids=[position.id],
            status="completed",
            spec=config["spec"],
            config=config["config"],
            results={"source_metadata": metadata},
            excluded_positions=[],
            artifacts={},
            created_at=NOW,
        )
        session.add(run)
        session.commit()
        key = SourcePlanKey.create(
            source_kind="backtest",
            portfolio_id=portfolio.id,
            position_ids=[position.id],
            pricing_parameter_profile_id=None,
            engine_config_id=engine.id,
            market_snapshot_id=None,
            effective_market_evidence_id=evidence_id,
            methodology=BACKTEST_TAIL_METHODOLOGY,
            config=config,
            valuation_policy={"valuation_as_of": NOW.isoformat()},
            freshness_policy={"max_age_seconds": 60},
        )

        assert find_reusable_source(session, key, now=NOW).run.id == run.id

        if changed_field == "product_kwargs":
            position.product_kwargs = {**position.product_kwargs, "strike": 101.0}
        else:
            position.engine_kwargs = {**position.engine_kwargs, "steps": 202}
        session.commit()

        selection = find_reusable_source(session, key, now=NOW)
        assert selection.run is None
        assert selection.reason_code == "missing_source"


def test_scenario_stress_reuse_matches_frozen_set_identity_and_names(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source
    from app.services.limits.sources import SCENARIO_TAIL_METHODOLOGY

    scenario_hash = "sha256:" + ("a" * 64)
    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key(
            "scenario_test",
            ids,
            methodology={
                "selection": "named",
                "scenario_set_hash": scenario_hash,
                "scenario_name": "Market Crash",
            },
        )
        source = _source_run(
            session,
            "scenario_test",
            key,
            status="completed",
            created_at=NOW,
        )
        metadata = source.results["source_metadata"]
        metadata.update(
            {
                "methodology": SCENARIO_TAIL_METHODOLOGY,
                "scenario_set_hash": scenario_hash,
                "scenario_names": ["Market Crash", "Vol Spike"],
            }
        )
        source.results = {**source.results, "source_metadata": metadata}
        session.commit()

        selected = find_reusable_source(session, key, now=NOW)
        renamed = find_reusable_source(
            session,
            _key(
                "scenario_test",
                ids,
                methodology={
                    "selection": "named",
                    "scenario_set_hash": scenario_hash,
                    "scenario_name": "Renamed Crash",
                },
            ),
            now=NOW,
        )

        assert selected.run.id == source.id
        assert renamed.run is None
        assert renamed.reason_code == "missing_source"


def test_scenario_tail_reuse_requires_exact_frozen_set_hash(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.services.limits.source_planner import find_reusable_source

    with database.SessionLocal() as session:
        portfolio, profile, engine, market = _fixture(session)
        ids = {
            "portfolio": portfolio.id,
            "profile": profile.id,
            "engine": engine.id,
            "market": market.id,
        }
        key = _key("scenario_test", ids)
        source = _source_run(
            session,
            "scenario_test",
            key,
            status="completed",
            created_at=NOW,
        )
        session.commit()

        selected = find_reusable_source(session, key, now=NOW)
        assert selected.run.id == source.id

        metadata = dict(source.results["source_metadata"])
        metadata["scenario_set_hash"] = "sha256:" + ("b" * 64)
        source.results = {**source.results, "source_metadata": metadata}
        session.commit()

        changed_set = find_reusable_source(session, key, now=NOW)
        assert changed_set.run is None
        assert changed_set.reason_code == "missing_source"
