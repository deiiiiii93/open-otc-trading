from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def test_alembic_0021_creates_hedging_tables(tmp_path: Path):
    db_path = tmp_path / "migration.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    # underlyings must exist for the FKs; create a minimal stand-in.
    metadata = sa.MetaData()
    sa.Table(
        "underlyings", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False),
    )
    metadata.create_all(engine)

    migration = importlib.import_module(
        "backend.alembic.versions.0021_hedging_instrument_catalog"
    )
    connection = engine.connect()
    context = MigrationContext.configure(connection)
    original_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"hedge_instruments", "hedge_map_entries"} <= tables
    instr_cols = {c["name"] for c in inspector.get_columns("hedge_instruments")}
    assert {"exchange", "contract_code", "status", "loaded_at"} <= instr_cols
    uniques = {
        u["name"] for u in inspector.get_unique_constraints("hedge_instruments")
    }
    assert "uq_hedge_instruments_exchange_code" in uniques

    map_cols = {c["name"] for c in inspector.get_columns("hedge_map_entries")}
    assert {"underlying_id", "exchange", "contract_code", "reconcile_status", "marked_by"} <= map_cols
    map_uniques = {
        u["name"] for u in inspector.get_unique_constraints("hedge_map_entries")
    }
    assert "uq_hedge_map_entries_underlying_contract" in map_uniques
    instr_indexes = {i["name"] for i in inspector.get_indexes("hedge_instruments")}
    assert "ix_hedge_instruments_underlying_family_status" in instr_indexes
    map_indexes = {i["name"] for i in inspector.get_indexes("hedge_map_entries")}
    assert "ix_hedge_map_entries_underlying_id" in map_indexes


def test_alembic_0021_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "migration.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    sa.Table(
        "underlyings", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False),
    )
    metadata.create_all(engine)
    migration = importlib.import_module(
        "backend.alembic.versions.0021_hedging_instrument_catalog"
    )
    connection = engine.connect()
    context = MigrationContext.configure(connection)
    original_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
        migration.upgrade()  # second run must not raise
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()
