"""Round-trip test for migration 0044_hedge_tag."""
from __future__ import annotations

import importlib
import json
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


def _engine_with_instruments_and_hedge_map(tmp_path: Path, name: str) -> sa.Engine:
    # Matches the FULL real `instruments` schema (backend/app/database.py's
    # _ensure_underlying_schema bootstrap CREATE TABLE) — same reason as
    # tests/test_migration_0042.py's fixture: _ensure_incremental_schema
    # always runs _ensure_underlying_schema first, which unconditionally
    # does `CREATE INDEX ... ON instruments (akshare_symbol)`; a stripped-down
    # table missing that column breaks with "no such column", unrelated to
    # anything this test is actually checking.
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
            "tags JSON NOT NULL DEFAULT '[]', "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE hedge_map_entries ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "underlying_id INTEGER, "
            "instrument_id INTEGER, "
            "exchange VARCHAR(40), "
            "contract_code VARCHAR(80), "
            "reconcile_status VARCHAR(20) NOT NULL DEFAULT 'active'"
            ")"
        ))
    return engine


def test_upgrade_tags_instrument_with_active_map_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "map.sqlite3")
    with engine.begin() as conn:
        # exchange/contract_code must be set on the instrument row itself —
        # matching is purely key-based (mirrors _active_instruments), not
        # via instrument_id, even though instrument_id is also populated
        # here as it would be by a real mark() call.
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status, exchange, contract_code) "
            "VALUES (1, 'IC2406.CFFEX', 'futures', 'active', 'CFFEX', 'IC2406')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, 1, 'CFFEX', 'IC2406', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_upgrade_tags_via_legacy_null_instrument_id_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "legacy.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status, exchange, contract_code) "
            "VALUES (1, 'IC2406.CFFEX', 'futures', 'active', 'CFFEX', 'IC2406')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, NULL, 'CFFEX', 'IC2406', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_upgrade_ignores_active_map_entry_when_instrument_itself_inactive(tmp_path: Path) -> None:
    """reconcile_status can be stale relative to the instrument's current
    status (it's only refreshed by mark/unmark/reconcile_map) — a one-time
    backfill must not trust a stale-active map entry over the instrument's
    own status column, matching sync_hedge_tag's truth condition (Task 1)."""
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "inactive_status.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status, exchange, contract_code) "
            "VALUES (1, 'IC2406.CFFEX', 'futures', 'expired', 'CFFEX', 'IC2406')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, 1, 'CFFEX', 'IC2406', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]) == []


def test_upgrade_tags_active_stock_with_no_map_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "stock.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_upgrade_ignores_stale_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "stale.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status, exchange, contract_code) "
            "VALUES (1, 'IC2403.CFFEX', 'futures', 'active', 'CFFEX', 'IC2403')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, 1, 'CFFEX', 'IC2403', 'stale')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]) == []


def test_upgrade_scrubs_pre_existing_false_hedge_tag(tmp_path: Path) -> None:
    """A hand-typed 'hedge' tag (from before this feature made it server-
    derived) on an instrument with no active map entry and not a
    self-hedging stock must be removed, not left in place — this is the
    difference between a true recomputation and an append-only backfill."""
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "scrub.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, tags) "
            "VALUES (1, 'NOTAHEDGE.SH', 'index', '[\"hedge\", \"underlying\"]')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]) == ["underlying"]


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "idem.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second call must not raise or duplicate

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]).count("hedge") == 1


def test_downgrade_removes_hedge_tag(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "down.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "downgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" not in json.loads(row[0])


def test_init_db_incremental_repair_also_syncs_hedge_tags(tmp_path: Path) -> None:
    """A DB that already has the `tags` column (past 0042) but hasn't run
    0044 yet must still get "hedge" backfilled by the boot-time repair path,
    the same duplicate-not-import convention as the underlying-tag backfill."""
    from app import database

    engine = _engine_with_instruments_and_hedge_map(tmp_path, "repair.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    database._ensure_incremental_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def _engine_with_instruments_only(tmp_path: Path, name: str) -> sa.Engine:
    """No hedge_map_entries table at all — the schema-drift case the
    migration/repair must still tolerate for the active-stock rule. Full
    real `instruments` schema, same reason as
    _engine_with_instruments_and_hedge_map above."""
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
            "tags JSON NOT NULL DEFAULT '[]', "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
    return engine


def test_upgrade_tags_active_stock_when_hedge_map_entries_table_absent(tmp_path: Path) -> None:
    engine = _engine_with_instruments_only(tmp_path, "no_map_table.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_init_db_incremental_repair_tags_active_stock_when_hedge_map_entries_table_absent(tmp_path: Path) -> None:
    from app import database

    engine = _engine_with_instruments_only(tmp_path, "repair_no_map_table.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    database._ensure_incremental_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])
