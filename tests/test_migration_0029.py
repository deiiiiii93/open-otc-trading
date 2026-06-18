from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _build_pre_0029_schema(engine: sa.Engine) -> None:
    metadata = sa.MetaData()
    for name in ("portfolios", "pricing_parameter_profiles", "engine_config_variants"):
        sa.Table(name, metadata, sa.Column("id", sa.Integer(), primary_key=True))
    sa.Table(
        "task_runs",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
    )
    metadata.create_all(engine)


def test_upgrade_and_downgrade_greek_landscape_runs(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'landscape.sqlite3'}")
    _build_pre_0029_schema(engine)
    migration = importlib.import_module("backend.alembic.versions.0029_greek_landscape_runs")

    with engine.connect() as connection:
        original_op = migration.op
        migration.op = Operations(MigrationContext.configure(connection))
        try:
            migration.upgrade()
            connection.commit()
            inspector = inspect(connection)
            assert "greek_landscape_runs" in inspector.get_table_names()
            assert "greeks_landscape_run_id" in {
                column["name"] for column in inspector.get_columns("task_runs")
            }

            migration.downgrade()
            connection.commit()
        finally:
            migration.op = original_op

    inspector = inspect(engine)
    assert "greek_landscape_runs" not in inspector.get_table_names()
    assert "greeks_landscape_run_id" not in {
        column["name"] for column in inspector.get_columns("task_runs")
    }
