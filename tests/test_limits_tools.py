"""Limits agent tools — read surface and HITL writes."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app import database, models
from app.config import Settings

SEED_VALUATION = datetime(2026, 6, 24, 15, 0, 0)


def _configure_test_db(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()


def _seed_limit_world(session) -> dict[str, int]:
    """Portfolio + activated portfolio-scoped delta cap + completed breach
    monitoring run + breach evaluation + open incident with its opened event."""
    portfolio = models.Portfolio(name="Limits Tool Book", tags=[])
    session.add(portfolio)
    session.flush()

    limit = models.RiskLimit(
        key="arena-tool-net-delta",
        name="Tool Net Delta Cap",
        category="greek",
        owner="risk_desk",
        created_by_actor="arena_seed",
    )
    session.add(limit)
    session.flush()
    version = models.RiskLimitVersion(
        risk_limit_id=limit.id,
        version=1,
        state="active",
        metric_kind="delta",
        source_kind="risk_run",
        scope_type="portfolio",
        scope_config={"portfolio_ids": [portfolio.id]},
        aggregation="net",
        transform="signed",
        comparator="upper",
        warning_upper=400.0,
        hard_upper=500.0,
        unit="underlying_units",
        activated_at=SEED_VALUATION,
        effective_from=datetime(2026, 1, 1),
    )
    session.add(version)
    session.flush()
    limit.active_version_id = version.id

    run = models.LimitMonitoringRun(
        trigger="manual",
        mode="interactive",
        portfolio_id=portfolio.id,
        valuation_as_of=SEED_VALUATION,
        source_policy="reuse_only",
        status="completed",
        summary={"breaches": 1},
        definition_snapshot={},
        definition_snapshot_hash="0" * 64,
    )
    session.add(run)
    session.flush()

    evaluation = models.LimitEvaluation(
        monitoring_run_id=run.id,
        limit_version_id=version.id,
        scope_type="portfolio",
        scope_key=f"portfolio:{portfolio.id}",
        scope_label=portfolio.name,
        observed_value=612.5,
        utilization=1.225,
        headroom=-112.5,
        warning_upper=400.0,
        hard_upper=500.0,
        governing_boundary="hard",
        status="breach",
    )
    session.add(evaluation)
    session.flush()

    incident = models.LimitIncident(
        portfolio_id=portfolio.id,
        risk_limit_id=limit.id,
        scope_type="portfolio",
        scope_key=f"portfolio:{portfolio.id}",
        scope_label=portfolio.name,
        severity="breach",
        status="open",
        first_evaluation_id=evaluation.id,
        last_evaluation_id=evaluation.id,
    )
    session.add(incident)
    session.flush()
    session.add(
        models.LimitIncidentEvent(
            incident_id=incident.id,
            event_type="opened",
            actor="system",
            payload={"severity": "breach"},
        )
    )
    session.commit()
    return {
        "portfolio": portfolio.id,
        "limit": limit.id,
        "version": version.id,
        "run": run.id,
        "evaluation": evaluation.id,
        "incident": incident.id,
    }


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


def test_list_risk_limits_reports_active_version(tmp_path):
    from app.tools.limits import list_risk_limits_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    out = list_risk_limits_tool.func(portfolio_id=ids["portfolio"])
    assert len(out["limits"]) == 1
    row = out["limits"][0]
    assert row["key"] == "arena-tool-net-delta"
    assert row["active_version"]["hard_upper"] == 500.0
    assert row["active_version"]["scope_type"] == "portfolio"


def test_get_limit_monitoring_run_latest_carries_evaluations(tmp_path):
    from app.tools.limits import get_limit_monitoring_run_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    out = get_limit_monitoring_run_tool.func(portfolio_id=ids["portfolio"])
    assert out["id"] == ids["run"]
    assert out["status"] == "completed"
    (ev,) = out["evaluations"]
    assert ev["limit_key"] == "arena-tool-net-delta"
    assert ev["status"] == "breach"
    assert ev["observed_value"] == 612.5


def test_get_limit_monitoring_run_requires_exactly_one_selector(tmp_path):
    from app.tools.limits import get_limit_monitoring_run_tool

    _configure_test_db(tmp_path)
    assert "error" in get_limit_monitoring_run_tool.func()
    assert "error" in get_limit_monitoring_run_tool.func(run_id=1, portfolio_id=1)


def test_list_and_get_incident_expose_row_version_and_events(tmp_path):
    from app.tools.limits import get_limit_incident_tool, list_limit_incidents_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    listed = list_limit_incidents_tool.func(portfolio_id=ids["portfolio"])
    (row,) = listed["incidents"]
    assert row["status"] == "open"
    assert row["row_version"] == 1
    assert row["limit_key"] == "arena-tool-net-delta"

    got = get_limit_incident_tool.func(incident_id=ids["incident"])
    assert got["row_version"] == 1
    assert [e["event_type"] for e in got["events"]] == ["opened"]


# ---------------------------------------------------------------------------
# Write tools (HITL)
# ---------------------------------------------------------------------------


def test_acknowledge_requires_matching_row_version(tmp_path):
    from app.tools.limits import acknowledge_limit_incident_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    out = acknowledge_limit_incident_tool.func(
        incident_id=ids["incident"], expected_row_version=99
    )
    assert out["ok"] is False
    assert out["error"] == "conflict"
    assert "row_version" in out["hint"]


def test_acknowledge_then_comment_appends_events(tmp_path):
    from app.tools.limits import (
        acknowledge_limit_incident_tool,
        comment_limit_incident_tool,
        get_limit_incident_tool,
    )

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    acked = acknowledge_limit_incident_tool.func(
        incident_id=ids["incident"], expected_row_version=1
    )
    assert acked["status"] == "acknowledged"
    assert acked["row_version"] == 2

    commented = comment_limit_incident_tool.func(
        incident_id=ids["incident"],
        comment="Driver is the AAPL call book; hedge already booked.",
        expected_row_version=2,
    )
    assert commented["id"] == ids["incident"]

    got = get_limit_incident_tool.func(incident_id=ids["incident"])
    assert [e["event_type"] for e in got["events"]] == [
        "opened",
        "acknowledged",
        "commented",
    ]


def test_waive_and_resolve_report_conflicts_as_dicts(tmp_path):
    from app.tools.limits import (
        resolve_limit_incident_tool,
        waive_limit_incident_tool,
    )

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        ids = _seed_limit_world(session)

    bad = waive_limit_incident_tool.func(
        incident_id=ids["incident"],
        rationale="accept until quarter end",
        expires_at="not-a-date",
        expected_row_version=1,
    )
    assert bad == {"ok": False, "error": "invalid_expires_at"}

    resolved = resolve_limit_incident_tool.func(
        incident_id=ids["incident"], expected_row_version=1
    )
    assert resolved["status"] == "resolved"

    again = resolve_limit_incident_tool.func(
        incident_id=ids["incident"], expected_row_version=2
    )
    assert again["ok"] is False
    assert again["error"] == "conflict"


def test_run_limit_monitoring_end_to_end(tmp_path, monkeypatch):
    from datetime import datetime as dt

    from app.services.batch_pricing import (
        _execute_batch_pricing_task,
        queue_batch_pricing,
    )
    from app.services.limits import monitoring
    from app.tools.limits import run_limit_monitoring_tool

    _configure_test_db(tmp_path)
    with database.SessionLocal() as session:
        portfolio = models.Portfolio(name="Monitor E2E Book", tags=[])
        session.add(portfolio)
        session.flush()
        # A REAL spot quote is load-bearing: the limits evaluator refuses
        # synthetic-default spots (missing:spot -> incomplete_scope/unknown),
        # so a quote-less book can never evaluate "ok".
        from app.services.underlyings import ensure_underlying

        instrument = ensure_underlying(session, "AAPL", source="arena_seed")
        session.add(
            models.MarketQuote(
                instrument_id=instrument.id,
                as_of=dt(2026, 6, 24),
                price=100.0,
                source="arena_seed",
            )
        )
        session.add(
            models.Position(
                portfolio_id=portfolio.id,
                underlying="AAPL",
                product_type="EuropeanVanillaOption",
                quantity=100,
                product_kwargs={
                    "strike": 100.0,
                    "option_type": "CALL",
                    "maturity": 0.5,
                },
            )
        )
        profile = models.PricingParameterProfile(
            name="Monitor E2E Profile", valuation_date=dt(2026, 6, 24)
        )
        session.add(profile)
        session.flush()
        session.add(
            models.PricingParameterRow(
                profile_id=profile.id,
                symbol="AAPL",
                source_trade_id="",
                rate=0.04,
                dividend_yield=0.005,
                volatility=0.3,
            )
        )
        limit = models.RiskLimit(
            key="arena-e2e-net-delta",
            name="E2E Net Delta Cap",
            category="greek",
            owner="risk_desk",
            created_by_actor="arena_seed",
        )
        session.add(limit)
        session.flush()
        version = models.RiskLimitVersion(
            risk_limit_id=limit.id,
            version=1,
            state="active",
            metric_kind="delta",
            source_kind="risk_run",
            scope_type="portfolio",
            scope_config={"portfolio_ids": [portfolio.id]},
            aggregation="net",
            transform="signed",
            comparator="upper",
            warning_upper=1_000_000.0,
            hard_upper=2_000_000.0,
            unit="underlying_units",
            activated_at=dt(2026, 1, 1),
            effective_from=dt(2026, 1, 1),
        )
        session.add(version)
        session.flush()
        limit.active_version_id = version.id
        session.commit()
        pid = portfolio.id
        profile_id = profile.id

    # Any tool invocation calls database.init_db(), whose product backfill
    # stamps product_id on bare positions — mirror that here so the risk run's
    # evidence manifest hashes the stabilized economic identity (without this,
    # first-pricing manifests are computed pre-stamp and can never be reused).
    database.init_db()

    with database.SessionLocal() as session:
        risk_run, risk_task = queue_batch_pricing(
            session, portfolio_id=pid, pricing_parameter_profile_id=profile_id
        )
        _execute_batch_pricing_task(session, risk_task.id, risk_run.id)
        session.commit()
        risk_run_id = risk_run.id

    monkeypatch.setattr(monitoring, "dispatch_limit_monitoring", lambda *a, **k: None)
    out = run_limit_monitoring_tool.func(portfolio_id=pid)
    assert out["ok"] is True, out

    monitoring.execute_limit_monitoring_task(out["task_id"], out["run_id"])

    from sqlalchemy import select

    with database.SessionLocal() as session:
        run = session.get(models.LimitMonitoringRun, out["run_id"])
        assert run.status in ("completed", "completed_with_unknowns")
        (source,) = session.execute(
            select(models.LimitSourceReference).where(
                models.LimitSourceReference.monitoring_run_id == run.id
            )
        ).scalars()
        assert source.risk_run_id == risk_run_id
        assert source.is_fresh is True
        evaluations = list(
            session.execute(
                select(models.LimitEvaluation).where(
                    models.LimitEvaluation.monitoring_run_id == run.id
                )
            ).scalars()
        )
        assert evaluations and all(e.status == "ok" for e in evaluations)
