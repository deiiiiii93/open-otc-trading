"""Migration 0061 + the ORM model agree on D10's key (spec §Data model).

Drives the migration module directly against temp SQLite (same harness as
test_migration_0059_arena_output_budget.py) — never `head`.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

_TABLE = "agent_tool_guard_verdicts"
_INDEXES = {"ux_agent_tool_guard_verdicts_call", "ix_agent_tool_guard_verdicts_tool_name",
            "ix_agent_tool_guard_verdicts_created_at"}


def _run(method: str, engine: sa.Engine) -> None:
    module = importlib.import_module("backend.alembic.versions.0061_tool_guard_verdicts")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _engine(tmp_path: Path, name: str) -> sa.Engine:
    return sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")


def _insert(conn, thread_id, tool_call_id):
    conn.execute(sa.text(
        "INSERT INTO agent_tool_guard_verdicts (thread_id, tool_call_id, guard_mode, "
        "tool_name, args_json, redacted, args_hash, verdict, predicates_json, action, "
        "created_at) VALUES (:t, :c, 'shadow', 'close_position', '{}', 0, 'h', "
        "'clear', '[]', 'recorded', '2026-09-21 00:00:00')"), {"t": thread_id, "c": tool_call_id})


def _assert_key_behaviour(engine):
    with engine.begin() as conn:
        _insert(conn, 5, "c1")
        _insert(conn, 5, "")      # structural rows are exempt from the key
        _insert(conn, 5, "")
        _insert(conn, 6, "c1")    # same id on another thread is a different call
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert(conn, 5, "c1")


def test_upgrade_creates_table_indexes_and_the_partial_unique_key(tmp_path):
    engine = _engine(tmp_path, "empty.sqlite3")
    _run("upgrade", engine)
    insp = inspect(engine)
    assert _TABLE in insp.get_table_names()
    indexes = {i["name"]: i for i in insp.get_indexes(_TABLE)}
    assert _INDEXES <= set(indexes)
    assert indexes["ux_agent_tool_guard_verdicts_call"]["unique"]
    _assert_key_behaviour(engine)


def test_thread_id_defaults_to_zero_never_null(tmp_path):
    engine = _engine(tmp_path, "default.sqlite3")
    _run("upgrade", engine)
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO agent_tool_guard_verdicts (tool_call_id, guard_mode, tool_name, "
            "args_json, args_hash, verdict, predicates_json, created_at) VALUES "
            "('c9', 'shadow', 'close_position', '{}', 'h', 'clear', '[]', '2026-09-21')"))
        assert conn.execute(sa.text(
            "SELECT thread_id FROM agent_tool_guard_verdicts")).scalar() == 0


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    """0001 materialises today's ORM, so the table already exists on a fresh chain."""
    from app.models import AgentToolGuardVerdict

    engine = _engine(tmp_path, "fresh.sqlite3")
    AgentToolGuardVerdict.__table__.create(bind=engine)
    before = {i["name"] for i in inspect(engine).get_indexes(_TABLE)}
    _run("upgrade", engine)
    assert {i["name"] for i in inspect(engine).get_indexes(_TABLE)} == before
    assert _INDEXES <= before


def test_the_orm_table_carries_the_same_key(tmp_path):
    """A fresh-chain DB built by create_all must not silently lose D10's guarantee."""
    from app.models import AgentToolGuardVerdict

    engine = _engine(tmp_path, "orm.sqlite3")
    AgentToolGuardVerdict.__table__.create(bind=engine)
    _assert_key_behaviour(engine)


def test_downgrade_drops_the_table(tmp_path):
    engine = _engine(tmp_path, "down.sqlite3")
    _run("upgrade", engine)
    _run("downgrade", engine)
    assert _TABLE not in inspect(engine).get_table_names()
