"""Round-trip test for migration 0033_agent_thread_source."""
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


def _engine_with_agent_threads(tmp_path: Path, name: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE agent_threads ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title VARCHAR(200),"
            " character VARCHAR(40))"
        ))
    return engine


def test_upgrade_adds_source_and_arena_run_id(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "up.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)

    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns("agent_threads")}
    assert "source" in cols
    assert "arena_run_id" in cols
    assert cols["arena_run_id"]["nullable"] is True
    idx = {i["name"] for i in insp.get_indexes("agent_threads")}
    assert "ix_agent_threads_arena_run_id" in idx


def test_source_defaults_to_desk(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "def.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO agent_threads (title, character) VALUES ('t', 'trader')"))
        row = conn.execute(sa.text("SELECT source, arena_run_id FROM agent_threads")).fetchone()
    assert row is not None
    assert row[0] == "desk"
    assert row[1] is None


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "idem.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second call must not raise


def test_init_db_incremental_repair_adds_thread_columns(tmp_path: Path) -> None:
    """An existing agent_threads table that predates 0033 gets source/arena_run_id
    added by the boot-time incremental repair (not just by Alembic)."""
    from app import database

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'old.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE agent_threads ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " title VARCHAR(200),"
            " character VARCHAR(40))"
        ))

    database._ensure_incremental_schema(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("agent_threads")}
    assert "source" in cols
    assert "arena_run_id" in cols
    idx = {i["name"] for i in inspect(engine).get_indexes("agent_threads")}
    assert "ix_agent_threads_arena_run_id" in idx


def test_downgrade_removes_columns(tmp_path: Path) -> None:
    engine = _engine_with_agent_threads(tmp_path, "down.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0033_agent_thread_source")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "downgrade", engine)
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("agent_threads")}
    assert "source" not in cols
    assert "arena_run_id" not in cols
