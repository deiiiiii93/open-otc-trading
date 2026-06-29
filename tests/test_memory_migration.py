"""Migration tests for 0038_memory_entries_evolve + 0039_memory_extraction_runs.

Uses the direct-module execution pattern (same as test_arena_migration.py) to
avoid the 0001_initial migration coupling that calls Base.metadata.create_all
against the current ORM and would create memory_entries with the new schema
rather than the legacy namespace-based schema.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def _run_migration(module, method: str, engine: sa.Engine) -> None:
    """Execute upgrade() or downgrade() on the given module against *engine*."""
    connection = engine.connect()
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original_op
        connection.close()


def _fresh_engine(tmp_path: Path, name: str = "test.sqlite3") -> sa.Engine:
    return create_engine(f"sqlite+pysqlite:///{tmp_path / name}")


def _create_legacy_memory_entries(engine: sa.Engine) -> None:
    """Create pre-0038 memory_entries schema (namespace-based) directly."""
    with engine.begin() as c:
        c.execute(text("""
            CREATE TABLE memory_entries (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                namespace VARCHAR(120),
                content TEXT NOT NULL DEFAULT '',
                meta JSON NOT NULL DEFAULT '{}',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
        c.execute(text(
            "CREATE INDEX ix_memory_entries_namespace ON memory_entries (namespace)"
        ))


def test_backfill_resolves_invalid_pairs_and_dups(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path)
    _create_legacy_memory_entries(engine)

    with engine.begin() as c:
        c.execute(text(
            "INSERT INTO memory_entries (namespace, content, meta, created_at) VALUES "
            "('domain:global', 'ACT/365 for CNH', '{}', '2026-01-01'),"
            "('user:desk', 'books in USD', '{}', '2026-01-02'),"
            "('user:desk', 'Books   in  USD', '{}', '2026-01-03'),"
            "('weird:xx', 'fallback row', '{}', '2026-01-04'),"
            "('user:desk', '   ', '{}', '2026-01-05'),"
            "('correction:desk', 'user said EUR not USD', '{}', '2026-01-06')"
        ))

    m38 = importlib.import_module("backend.alembic.versions.0038_memory_entries_evolve")
    m39 = importlib.import_module("backend.alembic.versions.0039_memory_extraction_runs")
    _run_migration(m38, "upgrade", engine)
    _run_migration(m39, "upgrade", engine)

    with engine.begin() as c:
        rows = list(c.execute(text(
            "SELECT scope_type, scope_id, status, normalized_content, source_error "
            "FROM memory_entries"
        )))

    for scope_type, _sid, status, _nc, source_error in rows:
        if scope_type == "domain":
            assert status in {"proposed", "approved", "archived"}, \
                f"domain row has unexpected status {status!r}"
        else:
            assert status in {"active", "archived"}, \
                f"non-domain row has unexpected status {status!r}"
        assert bool(source_error) == (scope_type == "correction"), \
            f"source_error invariant violated: scope_type={scope_type!r} source_error={source_error!r}"

    active = [r for r in rows if r[2] != "archived"]
    keys = [(r[0], r[1], r[3]) for r in active]
    assert len(keys) == len(set(keys)), "duplicate active (scope_type, scope_id, normalized_content) keys"

    domain = [r for r in rows if r[0] == "domain"]
    assert domain and domain[0][2] == "proposed", \
        f"domain row should be proposed, got {domain[0][2]!r}"

    # I-1: explicitly exercise the source_error=True branch (correction namespace).
    correction = [r for r in rows if r[0] == "correction"]
    assert correction, "correction row missing — True branch of source_error backfill untested"
    assert bool(correction[0][4]), \
        f"correction row should have source_error=True, got {correction[0][4]!r}"
    assert correction[0][2] == "active", \
        f"correction row should have status='active', got {correction[0][2]!r}"

    # NOT NULL tightening matches the ORM; category stays nullable.
    cols = {c["name"]: c for c in sa.inspect(engine).get_columns("memory_entries")}
    for name in ("scope_type", "scope_id", "normalized_content", "confidence",
                 "status", "source_error", "created_by", "pinned", "updated_at"):
        assert cols[name]["nullable"] is False, f"column {name!r} should be NOT NULL"
    assert cols["category"]["nullable"] is True, "category should remain nullable"
    assert "namespace" not in cols, "namespace column should have been dropped"


def test_migration_chain() -> None:
    """I-2: validate revision / down_revision without a DB (no tmp_path needed)."""
    m38 = importlib.import_module("backend.alembic.versions.0038_memory_entries_evolve")
    m39 = importlib.import_module("backend.alembic.versions.0039_memory_extraction_runs")

    assert m38.revision == "0038_memory_entries_evolve", \
        f"0038 revision mismatch: {m38.revision!r}"
    assert m38.down_revision == "0037_gateway_tables", \
        f"0038 down_revision mismatch: {m38.down_revision!r}"

    assert m39.revision == "0039_memory_extraction_runs", \
        f"0039 revision mismatch: {m39.revision!r}"
    assert m39.down_revision == "0038_memory_entries_evolve", \
        f"0039 down_revision mismatch: {m39.down_revision!r}"


def test_extraction_runs_table_exists(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path, "r.sqlite3")
    _create_legacy_memory_entries(engine)

    m38 = importlib.import_module("backend.alembic.versions.0038_memory_entries_evolve")
    m39 = importlib.import_module("backend.alembic.versions.0039_memory_extraction_runs")
    _run_migration(m38, "upgrade", engine)
    _run_migration(m39, "upgrade", engine)

    insp = sa.inspect(engine)
    cols = {c["name"] for c in insp.get_columns("memory_extraction_runs")}
    assert {"run_key", "kind", "session_id", "book_scope_id",
            "last_extracted_message_id", "status", "attempts"} <= cols
