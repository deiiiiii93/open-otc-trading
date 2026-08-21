"""Round-trip tests for migration 0059_arena_output_budget_variant.

Drives the migration module directly (same style as
test_migration_0058_arena_match_effort.py): configure MigrationContext +
Operations against a temp SQLite, call upgrade(), then inspect() the result.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

_NEW_COLS = ["run_id", "workflow_id", "model_id", "reasoning_effort",
             "max_output_tokens"]


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
        "backend.alembic.versions.0059_arena_output_budget_variant"
    )


def _pre_0059_engine(tmp_path: Path) -> sa.Engine:
    """arena_run + arena_match as they were after 0058, before 0059.

    The unique constraint is declared on ONE line on purpose, matching what
    alembic emits and what the live database actually contains. SQLAlchemy's
    SQLite reflection only recovers a constraint NAME from single-line
    ``CONSTRAINT <name> UNIQUE (...)``; split across two lines it reflects as
    unnamed, so ``drop_constraint`` by name silently fails to match and the
    rebuild leaves the old key behind — a failure of the fixture, against a
    table shape the real database never has.
    """
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'pre59.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text("""
            CREATE TABLE arena_run (
                id INTEGER PRIMARY KEY,
                status VARCHAR(40) NOT NULL,
                workflow_ids JSON NOT NULL,
                model_ids JSON NOT NULL,
                weights JSON,
                trials INTEGER NOT NULL DEFAULT 1,
                reasoning_efforts JSON,
                error TEXT,
                created_at DATETIME NOT NULL
            )
        """))
        conn.execute(sa.text("""
            CREATE TABLE arena_match (
                id INTEGER PRIMARY KEY,
                run_id INTEGER NOT NULL REFERENCES arena_run (id),
                workflow_id VARCHAR NOT NULL,
                model_id VARCHAR NOT NULL,
                reasoning_effort VARCHAR(20) NOT NULL DEFAULT '',
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
                CONSTRAINT uq_arena_match_run_workflow_model_effort UNIQUE (run_id, workflow_id, model_id, reasoning_effort)
            )
        """))
        conn.execute(sa.text(
            "CREATE INDEX ix_arena_match_run_id ON arena_match (run_id)"))
        conn.execute(sa.text(
            "INSERT INTO arena_run (id, status, workflow_ids, model_ids, trials,"
            " created_at) VALUES (1, 'completed', '[\"wf\"]', '[\"m\"]', 1,"
            " '2026-08-20 00:00:00')"))
        conn.execute(sa.text(
            "INSERT INTO arena_match (id, run_id, workflow_id, model_id,"
            " reasoning_effort, status, judge_missing, config, created_at)"
            " VALUES (1, 1, 'wf', 'm', 'high', 'scored', 0, '{}',"
            " '2026-08-20 00:00:00')"))
    return engine


def test_upgrade_adds_budget_columns_and_widens_the_contestant_key(tmp_path):
    engine = _pre_0059_engine(tmp_path)
    _run_migration(_migration(), "upgrade", engine)

    insp = inspect(engine)
    assert "max_output_tokens" in {c["name"] for c in insp.get_columns("arena_run")}
    assert "max_output_tokens" in {c["name"] for c in insp.get_columns("arena_match")}

    uniques = {u["name"]: list(u["column_names"])
               for u in insp.get_unique_constraints("arena_match")}
    assert uniques == {"uq_arena_match_run_wf_model_effort_budget": _NEW_COLS}


def test_backfill_is_zero_and_preserves_the_rows_own_effort(tmp_path):
    """0 means "the process default applied", which is what every historical row
    really ran at — the setting did not exist as a run variant. Unlike 0058,
    which HAD to read each row's own config because runs #107/#108 genuinely
    recorded per-row efforts, a blanket 0 asserts nothing false here. The row's
    effort must survive the table rebuild untouched.
    """
    engine = _pre_0059_engine(tmp_path)
    _run_migration(_migration(), "upgrade", engine)
    with engine.connect() as conn:
        row = conn.execute(sa.text(
            "SELECT max_output_tokens, reasoning_effort FROM arena_match WHERE id = 1"
        )).one()
    assert row[0] == 0
    assert row[1] == "high"


def test_rebuild_preserves_rows_sibling_fk_and_indexes(tmp_path):
    """batch_alter_table rebuilds the table; the real risk is what it drops."""
    engine = _pre_0059_engine(tmp_path)
    _run_migration(_migration(), "upgrade", engine)

    insp = inspect(engine)
    with engine.connect() as conn:
        assert conn.execute(
            sa.text("SELECT COUNT(*) FROM arena_match")).scalar() == 1
    assert "ix_arena_match_run_id" in {
        i["name"] for i in insp.get_indexes("arena_match")}
    assert [fk["referred_table"] for fk in insp.get_foreign_keys("arena_match")] \
        == ["arena_run"]


def test_upgrade_is_idempotent_on_a_fresh_orm_schema(tmp_path):
    """0001 create_all materialises today's ORM, so this must be a no-op.

    Asserts no constraint NAME beyond the one this migration itself introduces:
    what matters is that a second, NARROWER unique constraint never appears. One
    did — 0058 created its 4-column key alongside this 5-column one, which would
    have silently forbidden the second output-budget arm on every fresh database.
    """
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / 'fresh.sqlite3'}")
    from app.models import ArenaMatch, ArenaRun
    ArenaRun.__table__.create(bind=engine)
    ArenaMatch.__table__.create(bind=engine)

    before = {u["name"]: list(u["column_names"])
              for u in inspect(engine).get_unique_constraints("arena_match")}
    _run_migration(_migration(), "upgrade", engine)   # must not raise
    after = {u["name"]: list(u["column_names"])
             for u in inspect(engine).get_unique_constraints("arena_match")}

    assert after == before
    assert len(after) == 1


def test_downgrade_restores_the_four_column_key_and_drops_the_columns(tmp_path):
    engine = _pre_0059_engine(tmp_path)
    module = _migration()
    _run_migration(module, "upgrade", engine)
    _run_migration(module, "downgrade", engine)

    insp = inspect(engine)
    assert "max_output_tokens" not in {
        c["name"] for c in insp.get_columns("arena_match")}
    assert "max_output_tokens" not in {
        c["name"] for c in insp.get_columns("arena_run")}
    uniques = {u["name"] for u in insp.get_unique_constraints("arena_match")}
    assert "uq_arena_match_run_workflow_model_effort" in uniques
