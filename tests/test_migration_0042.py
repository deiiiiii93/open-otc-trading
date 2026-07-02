"""Round-trip test for migration 0042_instrument_tags."""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _run_migration(module, method: str, engine: sa.Engine) -> None:
    connection = engine.connect()
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original_op
        connection.close()


def _engine_with_instruments_and_positions(tmp_path: Path, name: str) -> sa.Engine:
    # Matches the FULL real `instruments` schema (backend/app/database.py's
    # _ensure_underlying_schema bootstrap CREATE TABLE) so _ensure_incremental_schema
    # (which always runs _ensure_underlying_schema first) doesn't fail trying to
    # index a column a stripped-down test table never had.
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE instruments ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "symbol VARCHAR(80) NOT NULL UNIQUE, "
            "display_name VARCHAR(160), "
            "kind VARCHAR(40) NOT NULL DEFAULT 'index', "
            "market VARCHAR(40), "
            "exchange VARCHAR(40), "
            "currency VARCHAR(8) NOT NULL DEFAULT 'CNY', "
            "akshare_symbol VARCHAR(80), "
            "akshare_asset_class VARCHAR(40), "
            "status VARCHAR(40) NOT NULL DEFAULT 'draft', "
            "source VARCHAR(40) NOT NULL DEFAULT 'manual', "
            "rate FLOAT, "
            "dividend_yield FLOAT, "
            "volatility FLOAT, "
            "notes TEXT, "
            "contract_code VARCHAR(80), "
            "series_root VARCHAR(40), "
            "expiry DATE, "
            "multiplier FLOAT, "
            "strike FLOAT, "
            "option_type VARCHAR(4), "
            "parent_id INTEGER, "
            "loaded_at DATETIME, "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE positions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " underlying VARCHAR(80),"
            " status VARCHAR(40),"
            " position_kind VARCHAR(20),"
            " source_payload JSON)"
        ))
    return engine


def test_upgrade_adds_tags_column(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "up.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    cols = {c["name"] for c in inspect(engine).get_columns("instruments")}
    assert "tags" in cols


def test_backfill_tags_open_position_underlying(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_open.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('000300.SH', 'index', 'draft')"
        ))
        conn.execute(sa.text(
            "INSERT INTO positions (underlying, status, position_kind, source_payload) "
            "VALUES ('000300.SH', 'open', 'otc', '{}')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='000300.SH'")).fetchone()
    assert row is not None
    import json
    assert "underlying" in json.loads(row[0])


def test_backfill_tags_active_root_instrument_without_open_position(tmp_path: Path) -> None:
    """The chicken-and-egg case this feature exists to fix: an active, curated
    instrument with no open position yet must still get backfilled."""
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_active.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('000905.SH', 'index', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='000905.SH'")).fetchone()
    import json
    assert "underlying" in json.loads(row[0])


def test_backfill_creates_stub_instrument_for_open_position_with_no_instrument_row(tmp_path: Path) -> None:
    """Position.underlying is free-text; an open OTC position can reference a
    symbol with no matching instruments row at all (legacy/bulk-imported data
    that predates ensure_underlying()). The migration must create a minimal
    stub row and tag it, not silently skip it -- otherwise that position's
    underlying becomes permanently unbookable once the tool gate ships."""
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_stub.sqlite3")
    with engine.begin() as conn:
        # Deliberately NO row in `instruments` for this symbol.
        conn.execute(sa.text(
            "INSERT INTO positions (underlying, status, position_kind, source_payload) "
            "VALUES ('ORPHAN.SH', 'open', 'otc', '{}')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT status, tags FROM instruments WHERE symbol='ORPHAN.SH'")).fetchone()
    assert row is not None
    import json
    assert row[0] == "active"
    assert "underlying" in json.loads(row[1])


def test_backfill_excludes_dated_derivative_contracts(tmp_path: Path) -> None:
    """An active dated futures contract (has expiry + contract_code) must NOT
    be backfilled as an underlying, even though it's active."""
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_excl.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status, expiry, contract_code) "
            "VALUES ('IC2606.CFE', 'futures', 'active', '2026-06-01', 'IC2606')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='IC2606.CFE'")).fetchone()
    import json
    assert json.loads(row[0]) == []


def test_backfill_excludes_listed_option(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_opt.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('IO2606-C-5000.CFE', 'listed_option', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='IO2606-C-5000.CFE'")).fetchone()
    import json
    assert json.loads(row[0]) == []


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "idem.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second call must not raise


def test_init_db_incremental_repair_adds_tags_column(tmp_path: Path) -> None:
    """An existing instruments table that predates 0042 gets `tags` added by
    the boot-time incremental repair (not just by Alembic) — this app boots
    local SQLite DBs via create_all(), which doesn't alter existing tables."""
    from app import database

    engine = _engine_with_instruments_and_positions(tmp_path, "old.sqlite3")
    database._ensure_incremental_schema(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("instruments")}
    assert "tags" in cols


def test_init_db_incremental_repair_also_backfills_underlying_tags(tmp_path: Path) -> None:
    """Adding the column is not enough: an old local DB with an open OTC
    position must actually get "underlying" backfilled by the incremental
    repair path too, not just by Alembic."""
    from app import database

    engine = _engine_with_instruments_and_positions(tmp_path, "old_backfill.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('000300.SH', 'index', 'draft')"
        ))
        conn.execute(sa.text(
            "INSERT INTO positions (underlying, status, position_kind, source_payload) "
            "VALUES ('000300.SH', 'open', 'otc', '{}')"
        ))
    database._ensure_incremental_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='000300.SH'")).fetchone()
    import json
    assert "underlying" in json.loads(row[0])
