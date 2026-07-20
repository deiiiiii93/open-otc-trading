from __future__ import annotations

import pytest

from app.services.quantark import ensure_quantark_path


@pytest.fixture(autouse=True)
def _quantark_on_path():
    ensure_quantark_path()


def _batch_db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return database


def _add_vanilla_portfolio(session, *, quantity=5.0, entry_price=8.0):
    from app.models import Portfolio, Position

    portfolio = Portfolio(name="P", base_currency="USD")
    session.add(portfolio)
    session.flush()
    position = Position(
        portfolio_id=portfolio.id,
        underlying="AAPL",
        source_trade_id="T-BATCH-1",
        product_type="EuropeanVanillaOption",
        product_kwargs={
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
            "contract_multiplier": 1.0,
        },
        engine_name="BlackScholesEngine",
        quantity=quantity,
        entry_price=entry_price,
    )
    session.add(position)
    session.flush()
    return portfolio, position


def test_queue_batch_pricing_creates_linked_run_and_task(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.services.batch_pricing import queue_batch_pricing

    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(session)
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()

        assert run.status == "queued"
        assert run.portfolio_id == portfolio.id
        assert run.resolved_position_ids is None
        assert task.kind == "batch_pricing"
        assert task.status == "queued"
        assert task.portfolio_id == portfolio.id
        assert task.risk_run_id == run.id


def test_queue_batch_pricing_rejects_unknown_portfolio(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.services.batch_pricing import queue_batch_pricing

    with database.SessionLocal() as session:
        with pytest.raises(ValueError, match="Portfolio not found"):
            queue_batch_pricing(session, portfolio_id=9999)


def test_queue_batch_pricing_scopes_position_ids(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import Position
    from app.services.batch_pricing import queue_batch_pricing

    with database.SessionLocal() as session:
        portfolio, pos_a = _add_vanilla_portfolio(session)
        pos_b = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=2.0,
            entry_price=12.0,
        )
        session.add(pos_b)
        session.flush()

        run, _task = queue_batch_pricing(
            session, portfolio_id=portfolio.id, position_ids=[pos_b.id]
        )
        assert run.resolved_position_ids == [pos_b.id]

        with pytest.raises(ValueError, match="not in portfolio"):
            queue_batch_pricing(
                session, portfolio_id=portfolio.id, position_ids=[987654]
            )


def test_execute_batch_pricing_writes_both_outputs(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun, TaskRun
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(
            session, quantity=5.0, entry_price=8.0
        )
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()
        run_id, task_id, pos_id = run.id, task.id, position.id
        portfolio_id = portfolio.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        # Risk side: metrics persisted, status synced.
        risk_run = session.get(RiskRun, run_id)
        assert risk_run.status == "completed"
        totals = risk_run.metrics["totals"]
        assert totals["market_value"] != 0.0
        risk_row = risk_run.metrics["positions"][0]
        assert risk_row["position_id"] == pos_id
        assert risk_row["pricing_ok"] is True
        from app.models import Position
        from app.services.hedging_greeks import resolved_position_set_hash

        assert risk_run.metrics["position_set_hash"] == resolved_position_set_hash(
            [session.get(Position, pos_id)]
        )

        # Task side: terminal status + both run ids in result_payload.
        task = session.get(TaskRun, task_id)
        assert task.status == "completed"
        assert task.progress_total == 1
        assert task.progress_current == 1
        assert task.message == "Completed 1 positions"
        assert task.result_payload["risk_run_id"] == run_id
        valuation_run_id = task.result_payload["valuation_run_id"]

        # Valuation side: same pass fanned out into a PositionValuationRun.
        valuation_run = session.get(PositionValuationRun, valuation_run_id)
        assert valuation_run.portfolio_id == portfolio_id
        assert valuation_run.status == "completed"
        assert valuation_run.summary["positions"] == 1
        assert valuation_run.summary["priced"] == 1
        assert valuation_run.summary["failed"] == 0
        # Summary totals mirror the risk totals (same single pass).
        assert valuation_run.summary["market_value"] == pytest.approx(
            totals["market_value"]
        )
        assert valuation_run.summary["pnl"] == pytest.approx(totals["pnl"])
        assert valuation_run.summary["delta"] == pytest.approx(totals["delta"])
        assert valuation_run.summary["vega"] == pytest.approx(totals["vega"])

        result = valuation_run.results[0]
        assert result.position_id == pos_id
        assert result.source_trade_id == "T-BATCH-1"
        assert result.ok is True
        assert result.price == pytest.approx(risk_row["price"])
        # quantity=5 (non-default) pins the scaling
        assert result.market_value == pytest.approx(risk_row["price"] * 5.0)
        # entry_price=8 (non-default) pins pnl = (price - entry) * qty
        assert result.pnl == pytest.approx((risk_row["price"] - 8.0) * 5.0)
        for greek in ("delta", "gamma", "vega", "theta", "rho", "rho_q"):
            assert result.result_payload[greek] == pytest.approx(risk_row[greek])
        assert result.market_inputs["spot"] == pytest.approx(100.0)
        assert result.market_inputs["volatility"] == pytest.approx(0.20)
        assert result.error is None


def test_profile_bound_run_prices_as_of_profile_valuation_date(tmp_path, monkeypatch):
    """A run bound to a historical pricing profile values as-of the profile's
    valuation date: quote as-of, market-context date, and the persisted
    PositionValuationRun.valuation_date stamp all follow it (not queue time)."""
    from datetime import datetime

    database = _batch_db(tmp_path, monkeypatch)
    from app.models import (
        Instrument,
        PositionValuationRun,
        PricingParameterProfile,
        PricingParameterRow,
        TaskRun,
    )
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )
    from app.services.quotes import record_quote

    profile_date = datetime(2026, 4, 30)
    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(session)
        instrument = Instrument(symbol="AAPL", kind="stock", status="active")
        session.add(instrument)
        session.flush()
        position.underlying_id = instrument.id
        profile = PricingParameterProfile(
            name="2026-04-30 Close",
            valuation_date=profile_date,
            source_type="xlsx",
            status="completed",
            summary={"row_count": 1},
        )
        session.add(profile)
        session.flush()
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="T-BATCH-1",
                symbol="AAPL",
                instrument_id=instrument.id,
                rate=0.01,
                dividend_yield=0.02,
                volatility=0.33,
            )
        )
        # Historical quote on the profile date plus a LATER quote: date-only
        # resolution must pick the same-day quote, not the later calendar date.
        record_quote(session, instrument_id=instrument.id, price=111.0,
                     as_of=datetime(2026, 4, 30, 15, 30), source="xlsx_import",
                     price_type="mid")
        record_quote(session, instrument_id=instrument.id, price=150.0,
                     as_of=datetime(2026, 6, 1), source="xlsx_import", price_type="mid")
        run, task = queue_batch_pricing(
            session,
            portfolio_id=portfolio.id,
            pricing_parameter_profile_id=profile.id,
        )
        session.commit()
        run_id, task_id = run.id, task.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        from app.models import RiskRun

        task = session.get(TaskRun, task_id)
        assert task.status == "completed"
        # The risk run advertises its pricing as-of so latest-risk consumers
        # can tell a historical repricing apart from current risk.
        risk_run = session.get(RiskRun, run_id)
        assert risk_run.metrics["valuation_as_of"].startswith("2026-04-30")
        valuation_run = session.get(
            PositionValuationRun, task.result_payload["valuation_run_id"]
        )
        # Stamp follows the profile date, not run.created_at.
        assert valuation_run.valuation_date == profile_date
        result = valuation_run.results[0]
        assert result.ok is True
        # Market context priced as-of the profile date...
        assert result.market_inputs["valuation_date"].startswith("2026-04-30")
        # ...so the quote store resolved the same-day quote, not the later one.
        assert result.market_inputs["spot"] == pytest.approx(111.0)
        assert result.market_inputs["quote_age_days"] == 0
        assert result.market_inputs["volatility"] == pytest.approx(0.33)


def test_unbound_run_keeps_queue_time_valuation_date(tmp_path, monkeypatch):
    """Runs without a pricing profile keep the old contract: valuation
    as-of queue time (run.created_at)."""
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun, TaskRun
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    with database.SessionLocal() as session:
        portfolio, _position = _add_vanilla_portfolio(session)
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()
        run_id, task_id = run.id, task.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        risk_run = session.get(RiskRun, run_id)
        task = session.get(TaskRun, task_id)
        valuation_run = session.get(
            PositionValuationRun, task.result_payload["valuation_run_id"]
        )
        assert valuation_run.valuation_date == risk_run.created_at


def test_execute_batch_pricing_completed_with_errors(tmp_path, monkeypatch):
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import PositionValuationRun, RiskRun, TaskRun
    from app.services import batch_pricing
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        return {
            "positions": [
                {
                    "position_id": position.id,
                    "underlying": position.underlying,
                    "product_type": position.product_type,
                    "price": 0.0,
                    "market_value": 0.0,
                    "pnl": 0.0,
                    "pricing_ok": False,
                    "pricing_error": "no market quote for AAPL",
                    "greeks_ok": True,
                    "greeks_error": None,
                }
                for position in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        batch_pricing, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )

    with database.SessionLocal() as session:
        portfolio, position = _add_vanilla_portfolio(session)
        run, task = queue_batch_pricing(session, portfolio_id=portfolio.id)
        session.commit()
        run_id, task_id, pos_id = run.id, task.id, position.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        assert task.status == "completed_with_errors"
        # Both run ids AND the structured error payload coexist.
        assert task.result_payload["risk_run_id"] == run_id
        failing = task.result_payload["errors"]["positions"]
        assert failing[0]["position_id"] == pos_id
        assert failing[0]["pricing_error"] == "no market quote for AAPL"

        risk_run = session.get(RiskRun, run_id)
        assert risk_run.status == "completed_with_errors"

        valuation_run = session.get(
            PositionValuationRun, task.result_payload["valuation_run_id"]
        )
        assert valuation_run.status == "completed_with_errors"
        assert valuation_run.summary["failed"] == 1
        result = valuation_run.results[0]
        assert result.ok is False
        assert result.price is None
        assert result.error == "no market quote for AAPL"


def test_scoped_batch_run_records_position_ids_in_overrides(tmp_path, monkeypatch):
    """Scoped runs must carry overrides.position_ids — the Positions page keys
    full-portfolio header summaries off its absence (isFullPortfolioRun)."""
    database = _batch_db(tmp_path, monkeypatch)
    from app.models import Position, PositionValuationRun, TaskRun
    from app.services import batch_pricing
    from app.services.batch_pricing import (
        execute_batch_pricing_task,
        queue_batch_pricing,
    )

    def fake_calculate_portfolio_risk(portfolio, **kwargs):
        return {
            "positions": [
                {"position_id": p.id, "pricing_ok": True, "greeks_ok": True}
                for p in portfolio.positions
            ],
            "totals": {},
        }

    monkeypatch.setattr(
        batch_pricing, "calculate_portfolio_risk", fake_calculate_portfolio_risk
    )

    with database.SessionLocal() as session:
        portfolio, _pos_a = _add_vanilla_portfolio(session)
        pos_b = Position(
            portfolio_id=portfolio.id,
            underlying="MSFT",
            product_type="EuropeanVanillaOption",
            product_kwargs={"strike": 200.0, "option_type": "CALL", "maturity": 1.0},
            engine_name="BlackScholesEngine",
            quantity=2.0,
            entry_price=12.0,
        )
        session.add(pos_b)
        session.flush()
        run, task = queue_batch_pricing(
            session, portfolio_id=portfolio.id, position_ids=[pos_b.id]
        )
        session.commit()
        run_id, task_id, pos_b_id = run.id, task.id, pos_b.id

    execute_batch_pricing_task(task_id, run_id, session_factory=database.SessionLocal)

    with database.SessionLocal() as session:
        task = session.get(TaskRun, task_id)
        valuation_run = session.get(
            PositionValuationRun, task.result_payload["valuation_run_id"]
        )
        # Scoping marker present and exact; resolved ids match the scope.
        assert valuation_run.overrides["position_ids"] == [pos_b_id]
        assert valuation_run.resolved_position_ids == [pos_b_id]
        assert valuation_run.summary["positions"] == 1
