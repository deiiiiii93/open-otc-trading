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
from sqlalchemy.exc import IntegrityError

from app.config import Settings


_ROOT = Path(__file__).resolve().parents[1]


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0047_limit_incident_portfolio"
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


def _old_0046_engine(tmp_path: Path, name: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE portfolios ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "name VARCHAR(160) NOT NULL UNIQUE)"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE limit_monitoring_runs ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "portfolio_id INTEGER NOT NULL, "
                "valuation_as_of DATETIME NOT NULL, "
                "FOREIGN KEY(portfolio_id) REFERENCES portfolios (id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE limit_evaluations ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "monitoring_run_id INTEGER NOT NULL, "
                "FOREIGN KEY(monitoring_run_id) "
                "REFERENCES limit_monitoring_runs (id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE limit_incidents ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "risk_limit_id INTEGER NOT NULL, "
                "scope_type VARCHAR(32) NOT NULL, "
                "scope_key VARCHAR(200) NOT NULL, "
                "scope_label VARCHAR(200) NOT NULL, "
                "severity VARCHAR(16) NOT NULL, "
                "status VARCHAR(24) NOT NULL, "
                "first_evaluation_id INTEGER, "
                "last_evaluation_id INTEGER, "
                "first_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "row_version INTEGER NOT NULL DEFAULT 1, "
                "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
                "FOREIGN KEY(first_evaluation_id) "
                "REFERENCES limit_evaluations (id), "
                "FOREIGN KEY(last_evaluation_id) "
                "REFERENCES limit_evaluations (id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE TABLE limit_incident_events ("
                "id INTEGER NOT NULL PRIMARY KEY, "
                "incident_id INTEGER NOT NULL, "
                "evaluation_id INTEGER, "
                "FOREIGN KEY(incident_id) REFERENCES limit_incidents (id), "
                "FOREIGN KEY(evaluation_id) "
                "REFERENCES limit_evaluations (id))"
            )
        )
        connection.execute(
            sa.text(
                "CREATE UNIQUE INDEX "
                "uq_limit_incidents_active_episode "
                "ON limit_incidents (risk_limit_id, scope_key) "
                "WHERE status IN "
                "('open', 'acknowledged', 'assigned', 'waived')"
            )
        )
    return engine


def _seed_linked_incident(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO portfolios (id, name) "
                "VALUES (7, 'Existing 0046 portfolio')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_monitoring_runs "
                "(id, portfolio_id, valuation_as_of) "
                "VALUES (11, 7, '2026-07-17 09:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_evaluations "
                "(id, monitoring_run_id) VALUES (13, 11)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_incidents "
                "(id, risk_limit_id, scope_type, scope_key, scope_label, "
                "severity, status, first_evaluation_id, last_evaluation_id) "
                "VALUES (17, 19, 'underlying', 'underlying:SPX', 'SPX', "
                "'breach', 'open', 13, 13)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_incident_events "
                "(id, incident_id, evaluation_id) VALUES (23, 17, 13)"
            )
        )


def _seed_mixed_portfolio_incident(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO portfolios (id, name) VALUES "
                "(7, 'Mixed portfolio A'), (8, 'Mixed portfolio B')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_monitoring_runs "
                "(id, portfolio_id, valuation_as_of) VALUES "
                "(11, 7, '2026-07-17 09:00:00'), "
                "(12, 8, '2026-07-17 09:05:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_evaluations "
                "(id, monitoring_run_id) VALUES (13, 11), (14, 12)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_incidents "
                "(id, risk_limit_id, scope_type, scope_key, scope_label, "
                "severity, status, first_evaluation_id, last_evaluation_id) "
                "VALUES (17, 19, 'underlying', 'underlying:SPX', 'SPX', "
                "'breach', 'open', 14, 14)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_incident_events "
                "(id, incident_id, evaluation_id) VALUES "
                "(23, 17, 14), (24, 17, 13)"
            )
        )


def _stamp_0046(engine: sa.Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO alembic_version (version_num) "
                "VALUES ('0046_risk_limits_core')"
            )
        )


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
    assert migration.revision == "0047_limit_incident_portfolio"
    assert migration.down_revision == "0046_risk_limits_core"


def test_upgrade_backfills_and_rekeys_existing_incident_history(
    tmp_path: Path,
) -> None:
    engine = _old_0046_engine(tmp_path, "backfill.sqlite3")
    _seed_linked_incident(engine)

    _run_migration(_migration(), "upgrade", engine)

    inspector = inspect(engine)
    portfolio_column = next(
        column
        for column in inspector.get_columns("limit_incidents")
        if column["name"] == "portfolio_id"
    )
    assert portfolio_column["nullable"] is False
    assert any(
        foreign_key["constrained_columns"] == ["portfolio_id"]
        and foreign_key["referred_table"] == "portfolios"
        for foreign_key in inspector.get_foreign_keys("limit_incidents")
    )
    indexes = {
        index["name"]: index for index in inspector.get_indexes("limit_incidents")
    }
    assert indexes["uq_limit_incidents_active_episode"]["column_names"] == [
        "portfolio_id",
        "risk_limit_id",
        "scope_key",
    ]
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT portfolio_id FROM limit_incidents WHERE id = 17")
            )
            == 7
        )

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO portfolios (id, name) " "VALUES (8, 'Second portfolio')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO limit_incidents "
                "(id, portfolio_id, risk_limit_id, scope_type, scope_key, "
                "scope_label, severity, status) "
                "VALUES (18, 8, 19, 'underlying', 'underlying:SPX', "
                "'SPX', 'breach', 'open')"
            )
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO limit_incidents "
                    "(id, portfolio_id, risk_limit_id, scope_type, "
                    "scope_key, scope_label, severity, status) "
                    "VALUES (19, NULL, 20, 'portfolio', 'portfolio:7', "
                    "'Book', 'warning', 'open')"
                )
            )


def test_upgrade_downgrade_reupgrade_preserves_unambiguous_history(
    tmp_path: Path,
) -> None:
    engine = _old_0046_engine(tmp_path, "roundtrip.sqlite3")
    _seed_linked_incident(engine)
    migration = _migration()

    _run_migration(migration, "upgrade", engine)
    _run_migration(migration, "downgrade", engine)

    inspector = inspect(engine)
    assert "portfolio_id" not in {
        column["name"] for column in inspector.get_columns("limit_incidents")
    }
    indexes = {
        index["name"]: index for index in inspector.get_indexes("limit_incidents")
    }
    assert indexes["uq_limit_incidents_active_episode"]["column_names"] == [
        "risk_limit_id",
        "scope_key",
    ]
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text(
                    "SELECT evaluation_id FROM limit_incident_events " "WHERE id = 23"
                )
            )
            == 13
        )

    _run_migration(migration, "upgrade", engine)
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT portfolio_id FROM limit_incidents WHERE id = 17")
            )
            == 7
        )


def test_stamped_0046_database_runs_forward_migration(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app import config as config_module

    path = tmp_path / "stamped.sqlite3"
    database_url = f"sqlite+pysqlite:///{path}"
    engine = _old_0046_engine(tmp_path, path.name)
    _seed_linked_incident(engine)
    _stamp_0046(engine)

    settings = Settings(database_url=database_url)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    command.upgrade(_alembic_config(database_url), "head")

    with engine.connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0047_limit_incident_portfolio"
        )
        assert (
            connection.scalar(
                sa.text("SELECT portfolio_id FROM limit_incidents WHERE id = 17")
            )
            == 7
        )


def test_stamped_0046_upgrade_rejects_mixed_portfolio_event_history(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app import config as config_module

    path = tmp_path / "mixed-stamped.sqlite3"
    database_url = f"sqlite+pysqlite:///{path}"
    engine = _old_0046_engine(tmp_path, path.name)
    _seed_mixed_portfolio_incident(engine)
    _stamp_0046(engine)

    settings = Settings(database_url=database_url)
    monkeypatch.setattr(config_module, "get_settings", lambda: settings)
    with pytest.raises(
        RuntimeError,
        match="conflicting portfolio evidence",
    ):
        command.upgrade(_alembic_config(database_url), "head")

    with engine.connect() as connection:
        assert (
            MigrationContext.configure(connection).get_current_revision()
            == "0046_risk_limits_core"
        )


def test_upgrade_fails_closed_when_history_has_no_portfolio_evidence(
    tmp_path: Path,
) -> None:
    engine = _old_0046_engine(tmp_path, "orphan.sqlite3")
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO limit_incidents "
                "(id, risk_limit_id, scope_type, scope_key, scope_label, "
                "severity, status) "
                "VALUES (1, 2, 'portfolio', 'portfolio:missing', 'Missing', "
                "'warning', 'open')"
            )
        )

    with pytest.raises(RuntimeError, match="cannot infer portfolio_id"):
        _run_migration(_migration(), "upgrade", engine)


def test_incremental_bootstrap_rejects_mixed_portfolio_event_history(
    tmp_path: Path,
) -> None:
    from app import database

    engine = _old_0046_engine(tmp_path, "incremental-mixed.sqlite3")
    _seed_mixed_portfolio_incident(engine)

    with pytest.raises(
        RuntimeError,
        match="conflicting portfolio evidence",
    ):
        database._ensure_incremental_schema(engine)


def test_incremental_bootstrap_repairs_old_local_schema(
    tmp_path: Path,
) -> None:
    from app import database

    engine = _old_0046_engine(tmp_path, "incremental.sqlite3")
    _seed_linked_incident(engine)

    database._ensure_incremental_schema(engine)

    indexes = {
        index["name"]: index for index in inspect(engine).get_indexes("limit_incidents")
    }
    assert indexes["uq_limit_incidents_active_episode"]["column_names"] == [
        "portfolio_id",
        "risk_limit_id",
        "scope_key",
    ]
    with engine.connect() as connection:
        assert (
            connection.scalar(
                sa.text("SELECT portfolio_id FROM limit_incidents WHERE id = 17")
            )
            == 7
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO limit_incidents "
                    "(id, portfolio_id, risk_limit_id, scope_type, "
                    "scope_key, scope_label, severity, status) "
                    "VALUES (20, NULL, 21, 'portfolio', 'portfolio:7', "
                    "'Book', 'warning', 'open')"
                )
            )
