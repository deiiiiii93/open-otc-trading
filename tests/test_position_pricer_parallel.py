from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app import database
from app.config import Settings
from app.models import (
    AssumptionRow,
    AssumptionSet,
    Instrument,
    Portfolio,
    PortfolioKind,
    Position,
)
from app.services.position_pricer import price_portfolio_positions
from app.services.quantark import QuantArkResult
from app.services.quotes import record_quote


def _configure_test_db(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    database.configure_database(settings)
    database.init_db()
    return database.SessionLocal()


def _seed_portfolio(session, n: int) -> Portfolio:
    portfolio = Portfolio(name="ProgressTest", base_currency="CNY",
                         kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()
    instrument = Instrument(symbol="000852.SH", kind="index", status="active")
    session.add(instrument)
    session.flush()
    for i in range(n):
        position = Position(
            portfolio_id=portfolio.id,
            underlying="000852.SH",
            underlying_id=instrument.id,
            product_type="SnowballOption",
            product_kwargs={
                "initial_price": 100.0,
                "strike": 100.0,
                "contract_multiplier": 1.0,
            },
            engine_name="SnowballQuadEngine",
            engine_kwargs={"params_type": "quad_params"},
            quantity=1.0,
            entry_price=0.0,
            status="open",
            source_trade_id=f"T{i}",
            mapping_status="supported",
        )
        session.add(position)
        session.flush()
    # Spot via the quote store; r/q/vol via an instrument-level assumption set —
    # the same numeric inputs the folded PositionMarketInput used to carry.
    record_quote(
        session,
        instrument_id=instrument.id,
        price=100.0,
        as_of=datetime(2025, 1, 1),
        source="xlsx_import",
        price_type="mid",
    )
    assumption_set = AssumptionSet(
        name="Parallel", valuation_date=datetime(2025, 1, 1), status="completed", summary={}
    )
    session.add(assumption_set)
    session.flush()
    session.add(AssumptionRow(
        set_id=assumption_set.id,
        instrument_id=instrument.id,
        symbol="000852.SH",
        rate=0.03,
        dividend_yield=0.0,
        volatility=0.2,
    ))
    session.flush()
    return portfolio


@pytest.fixture
def session(tmp_path: Path):
    return _configure_test_db(tmp_path)


def test_progress_callback_invoked_per_position(monkeypatch, session):
    portfolio = _seed_portfolio(session, n=4)

    monkeypatch.setattr(
        "app.services.position_pricer.price_product",
        lambda *a, **k: QuantArkResult(ok=True, data={"price": 50.0}),
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    progress: list[tuple[int, int]] = []
    price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        overrides=None,
        progress_callback=lambda current, total: progress.append((current, total)),
    )
    # Expect N+1 invocations: (0, 4), (1, 4), (2, 4), (3, 4), (4, 4)
    assert progress[0] == (0, 4)
    assert progress[-1] == (4, 4)
    assert len(progress) == 5


import threading

from app import database as _db_module
from app.config import configure_settings
from app.models import PositionValuationResult


def test_inner_loop_uses_multiple_threads(monkeypatch, tmp_path: Path):
    # Re-configure with elevated worker count for this test only.
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'parallel.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        risk_parallel_workers=4,
    )
    _db_module.configure_database(settings)
    _db_module.init_db()
    configure_settings(settings)
    session = _db_module.SessionLocal()
    try:
        portfolio = _seed_portfolio(session, n=4)

        thread_ids: set[int] = set()

        def fake_price_product(*a, **k):
            thread_ids.add(threading.get_ident())
            # tiny delay so workers actually overlap
            import time
            time.sleep(0.05)
            return QuantArkResult(ok=True, data={"price": 50.0})

        monkeypatch.setattr(
            "app.services.position_pricer.price_product", fake_price_product
        )
        monkeypatch.setattr(
            "app.services.position_pricer.gross_notional_for_position",
            lambda position, market: 1_000_000.0,
        )

        price_portfolio_positions(session, portfolio_id=portfolio.id)
        assert len(thread_ids) >= 2, f"expected >=2 worker threads, got {thread_ids}"
    finally:
        configure_settings(None)


def test_one_position_exception_does_not_stop_others(monkeypatch, session):
    portfolio = _seed_portfolio(session, n=3)

    call_count = {"n": 0}
    call_lock = threading.Lock()

    def flaky_price_product(*a, **k):
        with call_lock:
            call_count["n"] += 1
            n = call_count["n"]
        if n == 2:
            raise RuntimeError("simulated pricing crash")
        return QuantArkResult(ok=True, data={"price": 50.0})

    monkeypatch.setattr(
        "app.services.position_pricer.price_product", flaky_price_product
    )
    monkeypatch.setattr(
        "app.services.position_pricer.gross_notional_for_position",
        lambda position, market: 1_000_000.0,
    )

    progress: list[tuple[int, int]] = []
    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        progress_callback=lambda c, t: progress.append((c, t)),
    )
    assert progress[-1] == (3, 3)
    results = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).all()
    assert len(results) == 3
    assert sum(1 for r in results if not r.ok) == 1
    assert sum(1 for r in results if r.ok) == 2
