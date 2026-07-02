"""Round-trip test for migration 0043_agent_action_audits.

Direct module invocation against an isolated temp SQLite (the repo's
migration-test style — see test_arena_migration.py): the revision body itself
is exercised, not the ORM metadata.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

_EXPECTED_COLUMNS = {
    "id", "kind", "status", "deny_reason", "tool_name", "tool_class",
    "tool_call_id", "audit_ref", "mode", "envelope", "actor", "model",
    "persona", "thread_id", "workflow_id", "session_id", "task_id",
    "message_id", "desk_workflow_slug", "args_json", "redacted",
    "result_preview", "error", "occurred_at", "completed_at",
}


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


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0043_agent_action_audits"
    )


def test_upgrade_creates_table_columns_and_indexes(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'mig.sqlite3'}")
    _run_migration(_migration(), "upgrade", engine)

    insp = inspect(engine)
    assert "agent_action_audits" in insp.get_table_names()
    cols = {c["name"] for c in insp.get_columns("agent_action_audits")}
    assert _EXPECTED_COLUMNS <= cols
    index_names = {i["name"] for i in insp.get_indexes("agent_action_audits")}
    assert "ix_agent_action_audits_tool_occurred" in index_names
    assert "ix_agent_action_audits_thread_occurred" in index_names
    assert "ix_agent_action_audits_audit_ref" in index_names


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'mig.sqlite3'}")
    _run_migration(_migration(), "upgrade", engine)
    _run_migration(_migration(), "upgrade", engine)  # guard: no duplicate-table error
    assert "agent_action_audits" in inspect(engine).get_table_names()


def test_downgrade_drops_table(tmp_path: Path) -> None:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'mig.sqlite3'}")
    _run_migration(_migration(), "upgrade", engine)
    _run_migration(_migration(), "downgrade", engine)
    assert "agent_action_audits" not in inspect(engine).get_table_names()


def test_orm_row_roundtrip_with_defaults(session) -> None:
    from app.models import AgentActionAudit

    row = AgentActionAudit(
        status="attempted",
        tool_name="book_position",
        tool_class="domain_write",
        args_json={"underlying": "AAPL"},
    )
    session.add(row)
    session.commit()
    assert row.id is not None
    assert row.kind == "execution"
    assert row.redacted is False
    assert row.occurred_at is not None
    assert row.completed_at is None
