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
    """An arena_match shaped as it was BEFORE 0058: 3-column unique constraint."""
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
                CONSTRAINT uq_arena_match_run_workflow_model
                    UNIQUE (run_id, workflow_id, model_id)
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
    """0001 create_all already materialises the post-0058 shape — must be a no-op."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    from app.models import ArenaMatch
    ArenaMatch.__table__.create(bind=engine)

    _run_migration(_migration(), "upgrade", engine)   # must not raise

    uniques = {u["name"] for u in inspect(engine).get_unique_constraints("arena_match")}
    assert "uq_arena_match_run_workflow_model_effort" in uniques
