from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError


_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION_PATH = (
    _ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "0048_limit_monitor_active_unique.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location(
        "_migration_0048_limit_monitor_active_unique",
        _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def _engine(tmp_path: Path, name: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE limit_monitoring_runs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "portfolio_id INTEGER NOT NULL, "
                "status VARCHAR(40) NOT NULL)"
            )
        )
    return engine


def test_revision_metadata() -> None:
    migration = _migration()
    assert migration.revision == "0048_limit_monitor_active_unique"
    assert migration.down_revision == "0047_limit_incident_portfolio"


def test_upgrade_enforces_one_active_run_per_portfolio(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "active-unique.sqlite3")
    _run_migration(_migration(), "upgrade", engine)

    indexes = {
        index["name"]: index
        for index in inspect(engine).get_indexes("limit_monitoring_runs")
    }
    assert indexes["uq_limit_monitoring_runs_active_portfolio"]["unique"] == 1

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO limit_monitoring_runs "
                "(id, portfolio_id, status) VALUES (1, 7, 'queued')"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO limit_monitoring_runs "
                    "(id, portfolio_id, status) VALUES (2, 7, 'running')"
                )
            )
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO limit_monitoring_runs "
                "(id, portfolio_id, status) VALUES "
                "(3, 7, 'completed'), (4, 8, 'running')"
            )
        )

    _run_migration(_migration(), "downgrade", engine)
    assert "uq_limit_monitoring_runs_active_portfolio" not in {
        index["name"]
        for index in inspect(engine).get_indexes("limit_monitoring_runs")
    }
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO limit_monitoring_runs "
                "(id, portfolio_id, status) VALUES (5, 7, 'running')"
            )
        )


def test_upgrade_refuses_ambiguous_existing_active_runs(tmp_path: Path) -> None:
    engine = _engine(tmp_path, "active-duplicates.sqlite3")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO limit_monitoring_runs "
                "(id, portfolio_id, status) VALUES "
                "(1, 7, 'queued'), (2, 7, 'running')"
            )
        )

    with pytest.raises(
        RuntimeError,
        match="multiple active limit monitoring runs.*7",
    ):
        _run_migration(_migration(), "upgrade", engine)
