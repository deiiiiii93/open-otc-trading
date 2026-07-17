from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.config import Settings


_ROOT = Path(__file__).resolve().parents[1]
_TABLES = {
    "risk_limits",
    "risk_limit_versions",
    "limit_monitoring_runs",
    "limit_monitoring_run_versions",
    "limit_source_references",
    "limit_evaluations",
    "limit_incidents",
    "limit_incident_events",
}


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0046_risk_limits_core"
    )


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


def _engine_with_task_runs(tmp_path: Path, name: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as connection:
        for table_name in (
            "portfolios",
            "pricing_parameter_profiles",
            "market_snapshots",
            "risk_runs",
            "scenario_test_runs",
            "backtest_runs",
        ):
            connection.execute(
                sa.text(
                    f"CREATE TABLE {table_name} "
                    "(id INTEGER NOT NULL PRIMARY KEY)"
                )
            )
        connection.execute(
            sa.text(
                "CREATE TABLE engine_config_variants ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "name VARCHAR(160) NOT NULL UNIQUE, "
                "description TEXT, "
                "status VARCHAR(40) NOT NULL DEFAULT 'active', "
                "is_default BOOLEAN NOT NULL DEFAULT 0, "
                "rules JSON NOT NULL, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE task_runs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "kind VARCHAR(80) NOT NULL, "
                "status VARCHAR(40) NOT NULL)"
            )
        )
    return engine


def _alembic_config(database_url: str) -> Config:
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location",
        str(_ROOT / "backend" / "alembic"),
    )
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def test_revision_metadata() -> None:
    migration = _migration()
    assert migration.revision == "0046_risk_limits_core"
    assert migration.down_revision == "0045_arena_run_trials"


def test_upgrade_and_downgrade_isolated_sqlite(tmp_path: Path) -> None:
    engine = _engine_with_task_runs(tmp_path, "roundtrip.sqlite3")
    migration = _migration()

    _run_migration(migration, "upgrade", engine)
    inspector = inspect(engine)
    assert _TABLES <= set(inspector.get_table_names())
    assert "limit_monitoring_run_id" in {
        column["name"] for column in inspector.get_columns("task_runs")
    }

    _run_migration(migration, "downgrade", engine)
    inspector = inspect(engine)
    assert not (_TABLES & set(inspector.get_table_names()))
    assert "limit_monitoring_run_id" not in {
        column["name"] for column in inspector.get_columns("task_runs")
    }


def test_expected_columns_indexes_and_constraints(tmp_path: Path) -> None:
    engine = _engine_with_task_runs(tmp_path, "shape.sqlite3")
    _run_migration(_migration(), "upgrade", engine)
    inspector = inspect(engine)

    assert {
        "id",
        "key",
        "name",
        "description",
        "category",
        "owner",
        "tags",
        "active_version_id",
        "row_version",
        "created_by_actor",
        "created_by_persona",
        "created_at",
        "updated_at",
    } <= {
        column["name"] for column in inspector.get_columns("risk_limits")
    }
    assert {
        "metric_kind",
        "source_kind",
        "methodology",
        "scope_type",
        "scope_config",
        "aggregation",
        "transform",
        "comparator",
        "warning_lower",
        "warning_upper",
        "hard_lower",
        "hard_upper",
        "unit",
        "currency",
        "bump_convention",
        "freshness_policy",
        "effective_from",
        "effective_until",
    } <= {
        column["name"]
        for column in inspector.get_columns("risk_limit_versions")
    }
    assert {
        "definition_snapshot",
        "definition_snapshot_hash",
        "summary",
        "valuation_as_of",
        "source_policy",
    } <= {
        column["name"]
        for column in inspector.get_columns("limit_monitoring_runs")
    }
    assert {
        "observed_value",
        "adverse_value",
        "warning_lower",
        "warning_upper",
        "hard_lower",
        "hard_upper",
        "utilization",
        "headroom",
        "status",
        "reason_code",
        "coverage_count",
        "coverage_ratio",
        "evidence",
    } <= {
        column["name"]
        for column in inspector.get_columns("limit_evaluations")
    }

    version_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "risk_limit_versions"
        )
    }
    assert "uq_risk_limit_versions_limit_version" in version_constraints
    evaluation_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("limit_evaluations")
    }
    assert "uq_limit_evaluations_run_version_scope" in evaluation_constraints

    incident_indexes = {
        index["name"]: index
        for index in inspector.get_indexes("limit_incidents")
    }
    assert incident_indexes["uq_limit_incidents_active_episode"]["unique"]
    assert (
        incident_indexes["uq_limit_incidents_active_episode"]
        ["dialect_options"]["sqlite_where"]
        is not None
    )
    assert "ix_task_runs_limit_monitoring_run_id" in {
        index["name"] for index in inspector.get_indexes("task_runs")
    }


def test_orm_first_bootstrap_then_upgrade_head(tmp_path: Path, monkeypatch) -> None:
    from app import config as config_module
    from app import database

    database_url = f"sqlite+pysqlite:///{tmp_path / 'dual.sqlite3'}"
    settings = Settings(database_url=database_url)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    config = _alembic_config(database_url)

    command.upgrade(config, "0045_arena_run_trials")
    database.configure_database(settings)
    database.init_db()
    command.upgrade(config, "head")

    inspector = inspect(sa.create_engine(database_url))
    assert _TABLES <= set(inspector.get_table_names())
    with sa.create_engine(database_url).connect() as connection:
        revision = MigrationContext.configure(connection).get_current_revision()
    assert revision == "0046_risk_limits_core"


def test_boot_repair_then_upgrade_adds_task_monitoring_foreign_key(
    tmp_path: Path,
) -> None:
    from app import database

    engine = _engine_with_task_runs(tmp_path, "repair.sqlite3")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE positions ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "underlying VARCHAR(80), "
                "status VARCHAR(40), "
                "position_kind VARCHAR(16), "
                "source_payload JSON)"
            )
        )

    database._ensure_incremental_schema(engine)

    inspector = inspect(engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("task_runs")
    }
    assert columns["limit_monitoring_run_id"]["nullable"] is True
    assert "ix_task_runs_limit_monitoring_run_id" in {
        index["name"] for index in inspector.get_indexes("task_runs")
    }
    assert not any(
        foreign_key["constrained_columns"] == ["limit_monitoring_run_id"]
        for foreign_key in inspector.get_foreign_keys("task_runs")
    )

    _run_migration(_migration(), "upgrade", engine)

    foreign_keys = inspect(engine).get_foreign_keys("task_runs")
    assert any(
        foreign_key["constrained_columns"] == ["limit_monitoring_run_id"]
        and foreign_key["referred_table"] == "limit_monitoring_runs"
        and foreign_key["referred_columns"] == ["id"]
        for foreign_key in foreign_keys
    )


def test_upgrade_is_idempotent_for_orm_first_objects(tmp_path: Path) -> None:
    from app.models import Base

    engine = sa.create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'orm-first.sqlite3'}"
    )
    Base.metadata.create_all(engine)
    _run_migration(_migration(), "upgrade", engine)
    _run_migration(_migration(), "upgrade", engine)

    assert _TABLES <= set(inspect(engine).get_table_names())
