"""0062 adds five nullable columns; downgrade's batch rebuild must keep the
partial ux_memory_dedup predicate and the rows."""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

COLS = {"keep_alive_score", "keep_alive_confidence", "keep_alive_scored_at",
        "keep_alive_attempted_at", "keep_alive_unscored_reason"}


def _run(method, engine):
    module = importlib.import_module("backend.alembic.versions.0062_memory_keep_alive")
    connection = engine.connect()
    original = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original
        connection.close()


def _pre_0062(tmp_path: Path) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre62.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE memory_entries (
                id INTEGER PRIMARY KEY, scope_type VARCHAR(16), scope_id VARCHAR(120),
                content TEXT, normalized_content TEXT, confidence FLOAT,
                status VARCHAR(16), category VARCHAR(64), source_error BOOLEAN,
                created_by VARCHAR(16), pinned BOOLEAN, meta JSON,
                created_at DATETIME, updated_at DATETIME
            )"""))
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX ux_memory_dedup ON memory_entries "
            "(scope_type, scope_id, normalized_content) WHERE status != 'archived'"))
        conn.execute(sa.text(
            "INSERT INTO memory_entries VALUES (1, 'user', 'desk', 'books in USD', "
            "'books in usd', 0.9, 'active', NULL, 0, 'api', 1, '{}', "
            "'2026-09-01 00:00:00', '2026-09-01 00:00:00')"))
    return engine


def test_upgrade_adds_five_nullable_columns_and_keeps_the_row(tmp_path):
    engine = _pre_0062(tmp_path)
    _run("upgrade", engine)
    cols = {c["name"]: c for c in inspect(engine).get_columns("memory_entries")}
    assert COLS <= set(cols) and all(cols[c]["nullable"] for c in COLS)
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT content, keep_alive_score, keep_alive_unscored_reason FROM memory_entries")).one()
    assert tuple(row) == ("books in USD", None, None)


def test_upgrade_is_idempotent_on_a_create_all_schema(tmp_path):
    from app.models import MemoryEntry

    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    MemoryEntry.__table__.create(bind=engine)
    _run("upgrade", engine)
    assert COLS <= {c["name"] for c in inspect(engine).get_columns("memory_entries")}


def test_downgrade_removes_them_and_keeps_rows_and_the_partial_index(tmp_path):
    engine = _pre_0062(tmp_path)
    _run("upgrade", engine)
    _run("downgrade", engine)
    assert not COLS & {c["name"] for c in inspect(engine).get_columns("memory_entries")}
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM memory_entries")).scalar() == 1
        ddl = conn.execute(sa.text(
            "SELECT sql FROM sqlite_master WHERE name = 'ux_memory_dedup'")).scalar()
    assert "WHERE status != 'archived'" in ddl
