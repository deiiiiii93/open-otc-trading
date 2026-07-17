from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import func, select

from app.services.quantark import ensure_quantark_path


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


def _database(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/limits-risk.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _source_fixture(session):
    from app.models import (
        EngineConfigVariant,
        MarketSnapshot,
        Portfolio,
        Position,
        PricingParameterProfile,
        PricingParameterRow,
    )

    portfolio = Portfolio(name="Limits risk source", base_currency="USD")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        source_trade_id="LIMIT-RISK-1",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        engine_name="BlackScholesEngine",
        quantity=2.0,
        entry_price=8.0,
        currency="USD",
    )
    profile = PricingParameterProfile(
        name="Limits pinned close",
        valuation_date=datetime(2026, 7, 16, 15, 0),
        source_type="xlsx",
        status="completed",
        summary={"row_count": 1},
    )
    engine_config = EngineConfigVariant(
        name="Limits engines",
        status="active",
        is_default=False,
        rules={"rules": []},
    )
    market_snapshot = MarketSnapshot(
        name="Limits market evidence",
        source="test",
        symbol="AAPL",
        valuation_date=datetime(2026, 7, 16, 15, 0),
        data={"spot": 100.0},
        source_metadata={"fixture": "limits"},
    )
    session.add_all([position, profile, engine_config, market_snapshot])
    session.flush()
    pricing_row = PricingParameterRow(
        profile_id=profile.id,
        source_trade_id=position.source_trade_id,
        symbol=position.underlying,
        rate=0.01,
        dividend_yield=0.02,
        volatility=0.25,
    )
    session.add(pricing_row)
    session.flush()
    return portfolio, position, profile, engine_config, market_snapshot, pricing_row


def test_inline_risk_source_matches_queued_persistence_without_child_task(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun, TaskRun
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
        run_persisted_risk_source,
    )

    with database.SessionLocal() as session:
        (
            portfolio,
            position,
            profile,
            engine_config,
            market_snapshot,
            pricing_row,
        ) = _source_fixture(session)
        queued_run, task = queue_batch_pricing(
            session,
            portfolio_id=portfolio.id,
            position_ids=[position.id],
            pricing_parameter_profile_id=profile.id,
            engine_config_id=engine_config.id,
            market_snapshot_id=market_snapshot.id,
        )
        session.commit()
        ids = {
            "portfolio": portfolio.id,
            "position": position.id,
            "profile": profile.id,
            "engine": engine_config.id,
            "market": market_snapshot.id,
            "pricing_row": pricing_row.id,
            "queued_run": queued_run.id,
            "task": task.id,
        }

    execute_batch_pricing_task(
        ids["task"],
        ids["queued_run"],
        session_factory=database.SessionLocal,
    )
    inline = run_persisted_risk_source(
        session_factory=database.SessionLocal,
        portfolio_id=ids["portfolio"],
        position_ids=[ids["position"]],
        pricing_parameter_profile_id=ids["profile"],
        engine_config_id=ids["engine"],
        market_snapshot_id=ids["market"],
    )

    with database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(TaskRun)) == 1
        assert session.scalar(select(func.count()).select_from(RiskRun)) == 2
        assert (
            session.scalar(select(func.count()).select_from(PositionValuationRun))
            == 2
        )

        task = session.get(TaskRun, ids["task"])
        queued = session.get(RiskRun, ids["queued_run"])
        queued_valuation = session.get(
            PositionValuationRun,
            task.result_payload["valuation_run_id"],
        )
        inline_run = session.get(RiskRun, inline.risk_run_id)
        inline_valuation = session.get(
            PositionValuationRun,
            inline.valuation_run_id,
        )

        assert queued.status == inline_run.status == "completed"
        assert queued.pricing_parameter_profile_id == ids["profile"]
        assert inline_run.pricing_parameter_profile_id == ids["profile"]
        assert queued.engine_config_id == inline_run.engine_config_id == ids["engine"]
        assert queued.market_snapshot_id == inline_run.market_snapshot_id == ids["market"]
        assert (
            queued.resolved_position_ids
            == inline_run.resolved_position_ids
            == [ids["position"]]
        )
        assert queued.metrics == inline_run.metrics
        assert queued_valuation.engine_config_id == ids["engine"]
        assert inline_valuation.engine_config_id == ids["engine"]
        assert queued_valuation.valuation_date == inline_valuation.valuation_date
        assert queued_valuation.resolved_position_ids == [ids["position"]]
        assert inline_valuation.resolved_position_ids == [ids["position"]]

        queued_result = queued_valuation.results[0]
        inline_result = inline_valuation.results[0]
        assert queued_result.market_inputs == inline_result.market_inputs
        assert (
            inline_result.market_inputs["pricing_parameter_row_id"]
            == ids["pricing_row"]
        )
        assert inline_result.market_inputs["valuation_date"].startswith("2026-07-16")


def test_inline_risk_source_preserves_rho_q_in_monetary_and_position_evidence(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun
    from app.services import batch_pricing
    from app.services.batch_pricing import run_persisted_risk_source

    with database.SessionLocal() as session:
        portfolio, position, *_rest = _source_fixture(session)
        session.commit()
        portfolio_id = portfolio.id
        position_id = position.id

    def fake_calculate(portfolio, **_kwargs):
        row = {
            "position_id": position_id,
            "source_trade_id": "LIMIT-RISK-1",
            "underlying": "AAPL",
            "product_type": "EuropeanVanillaOption",
            "price": 10.0,
            "market_value": 20.0,
            "pnl": 4.0,
            "rho_q": 12.5,
            "pricing_ok": True,
            "pricing_error": None,
            "greeks_ok": True,
            "greeks_error": None,
        }
        return {
            "by_currency": {"USD": {"rho_q": 12.5, "position_count": 1}},
            "shared": {"delta": 0.5, "gamma": 0.01, "delta_proxy": 2.0},
            "totals": {"rho_q": 12.5},
            "mixed_currency": False,
            "currencies": ["USD"],
            "positions": [row],
        }

    monkeypatch.setattr(batch_pricing, "calculate_portfolio_risk", fake_calculate)
    result = run_persisted_risk_source(
        session_factory=database.SessionLocal,
        portfolio_id=portfolio_id,
        position_ids=[position_id],
    )

    with database.SessionLocal() as session:
        run = session.get(RiskRun, result.risk_run_id)
        valuation = session.get(PositionValuationRun, result.valuation_run_id)
        assert run.metrics["by_currency"]["USD"]["rho_q"] == pytest.approx(12.5)
        assert run.metrics["totals"]["rho_q"] == pytest.approx(12.5)
        assert run.metrics["positions"][0]["rho_q"] == pytest.approx(12.5)
        assert "rho_q" not in run.metrics["shared"]
        assert valuation.results[0].result_payload["rho_q"] == pytest.approx(12.5)


def test_inline_partial_risk_source_persists_coverage_diagnostics(
    tmp_path,
    monkeypatch,
) -> None:
    database = _database(tmp_path, monkeypatch)
    from app.models import Position, PositionValuationRun, RiskRun, TaskRun
    from app.services import batch_pricing
    from app.services.batch_pricing import run_persisted_risk_source

    with database.SessionLocal() as session:
        portfolio, first, *_rest = _source_fixture(session)
        second = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={
                "strike": 200.0,
                "option_type": "CALL",
                "maturity": 1.0,
            },
            engine_name="BlackScholesEngine",
            quantity=1.0,
            entry_price=5.0,
            currency="USD",
        )
        session.add(second)
        session.commit()
        portfolio_id = portfolio.id
        position_ids = [first.id, second.id]

    def fake_calculate(portfolio, **_kwargs):
        assert database.engine.pool.checkedout() == 0
        return {
            "by_currency": {"USD": {"position_count": 2}},
            "shared": {},
            "totals": {},
            "mixed_currency": False,
            "currencies": ["USD"],
            "positions": [
                {
                    "position_id": position_ids[0],
                    "underlying": "AAPL",
                    "product_type": "EuropeanVanillaOption",
                    "pricing_ok": True,
                    "greeks_ok": True,
                },
                {
                    "position_id": position_ids[1],
                    "underlying": "MSFT",
                    "product_type": "EuropeanVanillaOption",
                    "pricing_ok": False,
                    "pricing_error": "missing market evidence",
                    "greeks_ok": False,
                    "greeks_error": "pricing failed",
                },
            ],
        }

    monkeypatch.setattr(batch_pricing, "calculate_portfolio_risk", fake_calculate)
    result = run_persisted_risk_source(
        session_factory=database.SessionLocal,
        portfolio_id=portfolio_id,
        position_ids=position_ids,
    )

    with database.SessionLocal() as session:
        assert session.scalar(select(func.count()).select_from(TaskRun)) == 0
        run = session.get(RiskRun, result.risk_run_id)
        valuation = session.get(PositionValuationRun, result.valuation_run_id)
        assert run.status == result.status == "completed_with_errors"
        assert valuation.status == "completed_with_errors"
        assert run.metrics["coverage"] == {
            "requested_position_ids": position_ids,
            "resolved_position_ids": position_ids,
            "successful_position_ids": [position_ids[0]],
            "failed_position_ids": [position_ids[1]],
            "coverage_count": 1,
            "total_count": 2,
            "coverage_ratio": 0.5,
        }
