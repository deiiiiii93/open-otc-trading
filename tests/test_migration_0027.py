"""Migration 0027 — backtest_runs table + task_runs.backtest_run_id.

Uses the same direct-invocation style as test_migration_0025.py: build
a minimal pre-0027 schema in a temp DB, run the migration module's
upgrade() against it, and assert the table and column exist.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _build_pre_0027_schema(engine: sa.Engine) -> None:
    """Reconstruct the minimal schema needed for 0027 to run."""
    md = sa.MetaData()
    sa.Table(
        "portfolios", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
    )
    sa.Table(
        "pricing_parameter_profiles", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("valuation_date", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(40), nullable=False, server_default="completed"),
    )
    sa.Table(
        "task_runs", md,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    md.create_all(engine)


def test_upgrade_creates_backtest_runs(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}")
    _build_pre_0027_schema(engine)

    migration = importlib.import_module("backend.alembic.versions.0027_backtest_runs")
    connection = engine.connect()
    original_op = migration.op
    migration.op = Operations(MigrationContext.configure(connection))
    try:
        migration.upgrade()
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    insp = inspect(engine)
    assert "backtest_runs" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("task_runs")}
    assert "backtest_run_id" in cols

    # Verify backtest_runs has the expected columns
    br_cols = {c["name"] for c in insp.get_columns("backtest_runs")}
    for expected in ("id", "portfolio_id", "pricing_parameter_profile_id",
                     "resolved_position_ids", "status", "spec", "config",
                     "results", "excluded_positions", "artifacts", "created_at"):
        assert expected in br_cols, f"backtest_runs missing column: {expected}"

    # Verify indexes were created
    idx_names = {i["name"] for i in insp.get_indexes("backtest_runs")}
    assert "ix_backtest_runs_portfolio_id" in idx_names
    assert "ix_backtest_runs_pricing_parameter_profile_id" in idx_names
    tr_idx_names = {i["name"] for i in insp.get_indexes("task_runs")}
    assert "ix_task_runs_backtest_run_id" in tr_idx_names


def test_upgrade_idempotent_if_table_exists(tmp_path: Path) -> None:
    """Upgrade should no-op gracefully if backtest_runs already exists."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'test2.sqlite3'}")
    _build_pre_0027_schema(engine)

    # Pre-create backtest_runs and backtest_run_id to simulate init_db() state
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE backtest_runs ("
            "  id INTEGER NOT NULL PRIMARY KEY, "
            "  portfolio_id INTEGER NOT NULL, "
            "  pricing_parameter_profile_id INTEGER, "
            "  resolved_position_ids JSON, "
            "  status VARCHAR(40) NOT NULL DEFAULT 'queued', "
            "  spec JSON NOT NULL DEFAULT '{}', "
            "  config JSON NOT NULL DEFAULT '{}', "
            "  results JSON NOT NULL DEFAULT '{}', "
            "  excluded_positions JSON, "
            "  artifacts JSON NOT NULL DEFAULT '{}', "
            "  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(sa.text(
            "ALTER TABLE task_runs ADD COLUMN backtest_run_id INTEGER"
        ))

    migration = importlib.import_module("backend.alembic.versions.0027_backtest_runs")
    connection = engine.connect()
    original_op = migration.op
    migration.op = Operations(MigrationContext.configure(connection))
    try:
        migration.upgrade()  # must not raise
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    insp = inspect(engine)
    assert "backtest_runs" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("task_runs")}
    assert "backtest_run_id" in cols


def test_downgrade_removes_backtest_runs(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'test3.sqlite3'}")
    _build_pre_0027_schema(engine)

    migration = importlib.import_module("backend.alembic.versions.0027_backtest_runs")
    connection = engine.connect()
    original_op = migration.op
    migration.op = Operations(MigrationContext.configure(connection))
    try:
        migration.upgrade()
        connection.commit()
        migration.downgrade()
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    insp = inspect(engine)
    assert "backtest_runs" not in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("task_runs")}
    assert "backtest_run_id" not in cols
