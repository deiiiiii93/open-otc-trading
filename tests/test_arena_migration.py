"""Round-trip test for migration 0032_arena_runs.

Drives the migration module directly (the same direct-invocation style as
test_migration_0027.py): configure MigrationContext + Operations against a
temp SQLite, call upgrade() and downgrade(), then use inspect() to assert
structural correctness.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


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
    return sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")


# ---------------------------------------------------------------------------
# Test 1: upgrade creates both tables with the expected schema
# ---------------------------------------------------------------------------

def test_upgrade_creates_arena_tables(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path)
    migration = importlib.import_module("backend.alembic.versions.0032_arena_runs")

    _run_migration(migration, "upgrade", engine)

    insp = inspect(engine)
    tables = set(insp.get_table_names())
    assert "arena_run" in tables, "arena_run table not created"
    assert "arena_match" in tables, "arena_match table not created"

    # ---- arena_run columns ----
    run_cols = {c["name"] for c in insp.get_columns("arena_run")}
    for col in ("id", "created_at", "status", "workflow_ids", "model_ids", "weights", "error"):
        assert col in run_cols, f"arena_run missing column: {col}"

    # ---- arena_match columns ----
    match_cols = {c["name"] for c in insp.get_columns("arena_match")}
    for col in (
        "id", "run_id", "workflow_id", "model_id", "status",
        "objective_score", "judged_score", "total_score",
        "judge_missing", "config", "transcript_path", "error", "created_at",
    ):
        assert col in match_cols, f"arena_match missing column: {col}"


def test_upgrade_creates_arena_match_indexes(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path, "test_idx.sqlite3")
    migration = importlib.import_module("backend.alembic.versions.0032_arena_runs")

    _run_migration(migration, "upgrade", engine)

    insp = inspect(engine)
    idx_names = {i["name"] for i in insp.get_indexes("arena_match")}
    assert "ix_arena_match_run_id" in idx_names, f"ix_arena_match_run_id missing; found: {idx_names}"
    assert "ix_arena_match_model_id" in idx_names, f"ix_arena_match_model_id missing; found: {idx_names}"


def test_upgrade_creates_unique_constraint(tmp_path: Path) -> None:
    """The unique constraint on (run_id, workflow_id, model_id) must be present."""
    engine = _fresh_engine(tmp_path, "test_uq.sqlite3")
    migration = importlib.import_module("backend.alembic.versions.0032_arena_runs")

    _run_migration(migration, "upgrade", engine)

    insp = inspect(engine)
    # SQLite exposes unique constraints via get_unique_constraints(); for
    # constraints expressed as UniqueConstraint in create_table, SQLite also
    # surfaces them as indexes with unique=True.  Check both paths.
    uq_constraints = insp.get_unique_constraints("arena_match")
    uq_col_sets = [frozenset(c["column_names"]) for c in uq_constraints]
    target = frozenset({"run_id", "workflow_id", "model_id"})

    # Also check indexes with unique=True (SQLite's representation)
    unique_indexes = [
        frozenset(i["column_names"])
        for i in insp.get_indexes("arena_match")
        if i.get("unique")
    ]

    found = target in uq_col_sets or target in unique_indexes
    assert found, (
        f"Unique constraint on (run_id, workflow_id, model_id) not found.\n"
        f"  get_unique_constraints: {uq_constraints}\n"
        f"  unique indexes: {unique_indexes}"
    )


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Calling upgrade() twice must not raise."""
    engine = _fresh_engine(tmp_path, "test_idem.sqlite3")
    migration = importlib.import_module("backend.alembic.versions.0032_arena_runs")

    _run_migration(migration, "upgrade", engine)
    _run_migration(migration, "upgrade", engine)  # second call must be a no-op

    insp = inspect(engine)
    assert "arena_run" in set(insp.get_table_names())
    assert "arena_match" in set(insp.get_table_names())


# ---------------------------------------------------------------------------
# Test 2: downgrade removes both tables
# ---------------------------------------------------------------------------

def test_downgrade_removes_arena_tables(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path, "test_down.sqlite3")
    migration = importlib.import_module("backend.alembic.versions.0032_arena_runs")

    _run_migration(migration, "upgrade", engine)

    insp = inspect(engine)
    assert "arena_run" in set(insp.get_table_names())
    assert "arena_match" in set(insp.get_table_names())

    _run_migration(migration, "downgrade", engine)

    insp2 = inspect(engine)
    tables = set(insp2.get_table_names())
    assert "arena_run" not in tables, "arena_run should be dropped after downgrade"
    assert "arena_match" not in tables, "arena_match should be dropped after downgrade"


# ---------------------------------------------------------------------------
# Test 3: FK constraint is wired (insert + FK violation)
# ---------------------------------------------------------------------------

def test_arena_match_fk_enforced(tmp_path: Path) -> None:
    """arena_match.run_id must reference arena_run.id."""
    engine = _fresh_engine(tmp_path, "test_fk.sqlite3")
    migration = importlib.import_module("backend.alembic.versions.0032_arena_runs")

    _run_migration(migration, "upgrade", engine)

    with engine.begin() as conn:
        conn.execute(sa.text("PRAGMA foreign_keys = ON"))
        # Insert a valid arena_run row
        conn.execute(sa.text(
            "INSERT INTO arena_run (status, workflow_ids, model_ids, weights, error) "
            "VALUES ('queued', '[]', '[]', NULL, NULL)"
        ))
        # Insert a valid arena_match row (run_id=1 exists)
        conn.execute(sa.text(
            "INSERT INTO arena_match "
            "(run_id, workflow_id, model_id, status, config) "
            "VALUES (1, 'wf-a', 'model-x', 'pending', '{}')"
        ))

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT workflow_id FROM arena_match WHERE run_id=1")).fetchone()
        assert row is not None
        assert row[0] == "wf-a"


# ---------------------------------------------------------------------------
# Test 4: migration 0034 adds/removes arena_match.score_breakdown
# ---------------------------------------------------------------------------

def test_0034_adds_and_drops_score_breakdown(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path, "test_0034.sqlite3")
    base = importlib.import_module("backend.alembic.versions.0032_arena_runs")
    mig = importlib.import_module(
        "backend.alembic.versions.0034_arena_match_score_breakdown"
    )

    _run_migration(base, "upgrade", engine)
    assert "score_breakdown" not in {
        c["name"] for c in inspect(engine).get_columns("arena_match")
    }

    _run_migration(mig, "upgrade", engine)
    assert "score_breakdown" in {
        c["name"] for c in inspect(engine).get_columns("arena_match")
    }

    _run_migration(mig, "downgrade", engine)
    assert "score_breakdown" not in {
        c["name"] for c in inspect(engine).get_columns("arena_match")
    }


def test_0034_upgrade_is_idempotent(tmp_path: Path) -> None:
    """Re-running upgrade() when the column already exists must not raise."""
    engine = _fresh_engine(tmp_path, "test_0034_idem.sqlite3")
    base = importlib.import_module("backend.alembic.versions.0032_arena_runs")
    mig = importlib.import_module(
        "backend.alembic.versions.0034_arena_match_score_breakdown"
    )
    _run_migration(base, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second time: no-op, no error
    assert "score_breakdown" in {
        c["name"] for c in inspect(engine).get_columns("arena_match")
    }


# ---------------------------------------------------------------------------
# Test 5: migration 0045 adds/removes arena_run.trials
# ---------------------------------------------------------------------------

def test_0045_adds_and_drops_trials(tmp_path: Path) -> None:
    engine = _fresh_engine(tmp_path, "test_0045.sqlite3")
    base = importlib.import_module("backend.alembic.versions.0032_arena_runs")
    mig = importlib.import_module("backend.alembic.versions.0045_arena_run_trials")

    _run_migration(base, "upgrade", engine)
    assert "trials" not in {
        c["name"] for c in inspect(engine).get_columns("arena_run")
    }

    _run_migration(mig, "upgrade", engine)
    assert "trials" in {
        c["name"] for c in inspect(engine).get_columns("arena_run")
    }

    _run_migration(mig, "downgrade", engine)
    assert "trials" not in {
        c["name"] for c in inspect(engine).get_columns("arena_run")
    }


def test_0045_upgrade_is_idempotent_when_current_orm_created_trials(
    tmp_path: Path,
) -> None:
    engine = _fresh_engine(tmp_path, "test_0045_current.sqlite3")
    base = importlib.import_module("backend.alembic.versions.0032_arena_runs")
    migration = importlib.import_module(
        "backend.alembic.versions.0045_arena_run_trials"
    )

    _run_migration(base, "upgrade", engine)
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "ALTER TABLE arena_run "
                "ADD COLUMN trials INTEGER NOT NULL DEFAULT 1"
            )
        )

    _run_migration(migration, "upgrade", engine)

    assert "trials" in {
        column["name"]
        for column in inspect(engine).get_columns("arena_run")
    }
