"""Round-trip tests for migration 0058_arena_match_reasoning_effort.

Drives the migration module directly (same style as test_arena_migration.py):
configure MigrationContext + Operations against a temp SQLite, call upgrade(),
then inspect() the result.
"""
from __future__ import annotations

import importlib
import json
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


def _migration():
    return importlib.import_module(
        "backend.alembic.versions.0058_arena_match_reasoning_effort"
    )


def _pre_0058_engine(tmp_path: Path) -> sa.Engine:
    """An arena_match shaped as it was BEFORE 0058: 3-column unique constraint.

    The constraint is declared on ONE line on purpose, matching what alembic
    emits and what the live database contains. SQLAlchemy's SQLite reflection
    only recovers a constraint NAME from single-line
    ``CONSTRAINT <name> UNIQUE (...)``; split across two lines it reflects as
    unnamed, so ``drop_constraint`` by name cannot match it and the rebuild
    leaves the old key behind. This fixture used to be split, which meant the
    test passed while the migration silently failed to drop anything.
    """
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre58.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE arena_match (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL,
                workflow_id VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                status VARCHAR(40) NOT NULL,
                objective_score FLOAT,
                judged_score FLOAT,
                total_score FLOAT,
                judge_missing BOOLEAN NOT NULL DEFAULT 0,
                config JSON NOT NULL,
                score_breakdown JSON,
                transcript_path VARCHAR,
                error TEXT,
                created_at DATETIME NOT NULL,
                CONSTRAINT uq_arena_match_run_workflow_model UNIQUE (run_id, workflow_id, model_id)
            )
        """))
        conn.execute(sa.text("CREATE INDEX ix_arena_match_run_id ON arena_match (run_id)"))
    return engine


def _insert(engine: sa.Engine, *, mid: int, model: str, config: dict) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO arena_match (id, run_id, workflow_id, model_id, status,"
                " judge_missing, config, created_at) VALUES (:i, 1, 'wf', :m,"
                " 'scored', 0, :c, '2026-08-14 00:00:00')"
            ),
            {"i": mid, "m": model, "c": json.dumps(config)},
        )


def test_upgrade_adds_column_and_four_column_constraint(tmp_path: Path) -> None:
    engine = _pre_0058_engine(tmp_path)
    _run_migration(_migration(), "upgrade", engine)

    insp = inspect(engine)
    assert "reasoning_effort" in {c["name"] for c in insp.get_columns("arena_match")}
    uniques = {u["name"]: u["column_names"]
               for u in insp.get_unique_constraints("arena_match")}
    assert "uq_arena_match_run_workflow_model_effort" in uniques
    # ...and the superseded 3-column key is GONE. Without this the test passed
    # even when drop_constraint matched nothing, leaving a stricter constraint
    # behind that would forbid the second arm this migration exists to allow.
    assert [c for c in uniques.values()] == [
        ["run_id", "workflow_id", "model_id", "reasoning_effort"]]
    assert uniques["uq_arena_match_run_workflow_model_effort"] == [
        "run_id", "workflow_id", "model_id", "reasoning_effort",
    ]
    assert "uq_arena_match_run_workflow_model" not in uniques


def test_backfill_derives_effort_from_config_not_blank(tmp_path: Path) -> None:
    """A historical pinned row keeps its regime; an unpinned one becomes ''.

    Blind-filling '' would tell the DB a high board and a low board were the same
    regime, and merge_runs — which now groups on this column — would fold them.
    """
    engine = _pre_0058_engine(tmp_path)
    _insert(engine, mid=1, model="pinned-high", config={"reasoning_effort": "high"})
    _insert(engine, mid=2, model="unpinned", config={"reasoning_effort": None})
    _insert(engine, mid=3, model="no-key", config={"weights": None})

    _run_migration(_migration(), "upgrade", engine)

    with engine.begin() as conn:
        got = dict(conn.execute(
            sa.text("SELECT model_id, reasoning_effort FROM arena_match")
        ).fetchall())
    assert got == {"pinned-high": "high", "unpinned": "", "no-key": ""}


def test_rebuild_preserves_rows_and_index(tmp_path: Path) -> None:
    """batch_alter_table rebuilds the table — rows and siblings must survive."""
    engine = _pre_0058_engine(tmp_path)
    _insert(engine, mid=1, model="m", config={"reasoning_effort": "low"})

    _run_migration(_migration(), "upgrade", engine)

    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT COUNT(*) FROM arena_match")).scalar() == 1
    indexes = {i["name"] for i in inspect(engine).get_indexes("arena_match")}
    assert "ix_arena_match_run_id" in indexes


def test_upgrade_is_idempotent_on_a_fresh_orm_schema(tmp_path: Path) -> None:
    """0001 create_all already materialises the CURRENT ORM shape — must be a no-op.

    Deliberately asserts no constraint NAME. The ORM's contestant key is a moving
    target (3 columns → 4 in 0058 → 5 in 0059), so pinning today's literal here
    would fail on the next migration that extends it — the exact self-invalidating
    assertion this repo has been bitten by four times.

    The invariant that actually matters is that this migration ADDS NOTHING when
    the table is already in a later shape. It once did: 0059's 5-column
    constraint is absent under 0058's name, so 0058 cheerfully created its own
    4-column one alongside it — and the 4-column one is STRICTER, silently
    forbidding the second output-budget arm on every fresh database.
    """
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    from app.models import ArenaMatch
    ArenaMatch.__table__.create(bind=engine)

    before = {u["name"]: list(u["column_names"])
              for u in inspect(engine).get_unique_constraints("arena_match")}
    _run_migration(_migration(), "upgrade", engine)   # must not raise
    after = {u["name"]: list(u["column_names"])
             for u in inspect(engine).get_unique_constraints("arena_match")}

    assert after == before, "0058 must not alter a table already in a later shape"
    # And the ORM's key must still be the ONLY unique constraint — a second,
    # narrower one would forbid arms the current schema is designed to allow.
    assert len(after) == 1
