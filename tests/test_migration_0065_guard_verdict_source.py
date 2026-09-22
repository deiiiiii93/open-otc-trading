"""Migration 0065: sweep verdicts share the live table, told apart by `source` (D3).

Drives the migration modules directly against temp SQLite (the 0061/0063
harness) — never `head`.
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
_NEW = {"source", "state_fidelity", "audit_id"}
_NEW_INDEXES = {"ix_agent_tool_guard_verdicts_audit_id",
                "ix_agent_tool_guard_verdicts_source_created"}
_OLD_INDEXES = {"ux_agent_tool_guard_verdicts_call", "ix_agent_tool_guard_verdicts_tool_name",
                "ix_agent_tool_guard_verdicts_created_at"}


def _run(revision: str, method: str, engine: sa.Engine) -> None:
    module = importlib.import_module(f"backend.alembic.versions.{revision}")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _insert(conn, thread_id, tool_call_id):
    conn.execute(sa.text(
        "INSERT INTO agent_tool_guard_verdicts (thread_id, tool_call_id, guard_mode, "
        "tool_name, args_json, redacted, args_hash, verdict, predicates_json, action, "
        "created_at) VALUES (:t, :c, 'shadow', 'close_position', '{}', 0, 'h', "
        "'clear', '[]', 'recorded', '2026-09-22 00:00:00')"),
        {"t": thread_id, "c": tool_call_id})


def _pre_0065(tmp_path: Path) -> sa.Engine:
    """The table exactly as 0061 built it, holding one live verdict."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre65.sqlite3'}")
    _run("0061_tool_guard_verdicts", "upgrade", engine)
    with engine.begin() as conn:
        _insert(conn, 5, "c1")
    return engine


def test_upgrade_adds_the_columns_and_backfills_live(tmp_path):
    engine = _pre_0065(tmp_path)
    _run("0065_guard_verdict_source", "upgrade", engine)
    insp = inspect(engine)
    cols = {c["name"]: c for c in insp.get_columns(_TABLE)}
    assert _NEW <= set(cols)
    assert not cols["source"]["nullable"]
    assert cols["state_fidelity"]["nullable"] and cols["audit_id"]["nullable"]
    assert _NEW_INDEXES | _OLD_INDEXES <= {i["name"] for i in insp.get_indexes(_TABLE)}
    assert insp.get_foreign_keys(_TABLE) == []   # audit_id carries no FK, by design
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT source, state_fidelity, audit_id FROM agent_tool_guard_verdicts")).one()
    assert tuple(row) == ("live", None, None)


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    """0001 materialises today's ORM, so a fresh chain already has everything."""
    from app.models import AgentToolGuardVerdict

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    AgentToolGuardVerdict.__table__.create(bind=engine)
    before = {i["name"] for i in inspect(engine).get_indexes(_TABLE)}
    assert _NEW_INDEXES <= before
    _run("0065_guard_verdict_source", "upgrade", engine)
    assert {i["name"] for i in inspect(engine).get_indexes(_TABLE)} == before


def test_downgrade_keeps_rows_indexes_and_the_partial_key(tmp_path):
    engine = _pre_0065(tmp_path)
    _run("0065_guard_verdict_source", "upgrade", engine)
    _run("0065_guard_verdict_source", "downgrade", engine)
    insp = inspect(engine)
    assert not (_NEW & {c["name"] for c in insp.get_columns(_TABLE)})
    names = {i["name"] for i in insp.get_indexes(_TABLE)}
    assert _OLD_INDEXES <= names and not (_NEW_INDEXES & names)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM agent_tool_guard_verdicts")).scalar() == 1
    with engine.begin() as conn:   # structural (empty-id) rows stay exempt from the key
        _insert(conn, 5, "")
        _insert(conn, 5, "")
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            _insert(conn, 5, "c1")


def test_revision_chain():
    module = importlib.import_module("backend.alembic.versions.0065_guard_verdict_source")
    assert module.revision == "0065_guard_verdict_source"
    assert module.down_revision == "0064_limit_incident_reviews"


def test_the_orm_defaults_a_row_to_live(session):
    from app.models import AgentToolGuardVerdict

    row = AgentToolGuardVerdict(thread_id=1, tool_call_id="c", guard_mode="shadow",
                                tool_name="close_position", args_json={}, args_hash="h",
                                verdict="clear", predicates_json=[])
    session.add(row)
    session.commit()
    assert (row.source, row.state_fidelity, row.audit_id) == ("live", None, None)
