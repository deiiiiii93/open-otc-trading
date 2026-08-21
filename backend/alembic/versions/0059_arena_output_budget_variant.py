"""arena: output-token budget as a run variant, and part of the contestant key

Revision ID: 0059_arena_output_budget_variant
Revises: 0058_arena_match_reasoning_effort

Budget is a REGIME, not a detail. Runs #118/#119 measured 0/8 vs 7/8 artifacts
produced and +16.4 mean objective on the output budget alone, so a board that
cannot say which budget it ran at cannot be compared with one that can — exactly
the argument that put reasoning_effort in the contestant key in 0058.

`arena_run.max_output_tokens` is the per-model arm map {slug: [budget | null]},
mirroring `reasoning_efforts`. It lives in a column rather than an env var
because a resume must re-supply every process-level setting:
OPEN_OTC_AGENT_MAX_OUTPUT_TOKENS had no column, so a resume that omitted it
silently finished a run at a DIFFERENT budget than it started with, and nothing
in the stored data would reveal it.

`arena_match.max_output_tokens` is 0 for unpinned, never NULL — SQL treats NULLs
as DISTINCT in a UNIQUE constraint, so a nullable column would silently stop
protecting unpinned pairs at the DB level while the Python-level upsert still
dedups. 0 is a safe sentinel: a zero-token budget is not a meaningful setting.

The backfill is a blanket 0, and that is correct HERE where 0058's was not:
0058 had to read each row's own config because runs #107/#108 really had
recorded a per-row effort, so flattening would have asserted two regimes were
one. No historical row carries a per-match budget — the setting did not exist as
a run variant — so 0 ("the process default applied") is what every one of them
actually ran at. Which default that was is a property of the run's era, not of
the row, and the CHANGELOG records the cutover at run #115.

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so a fresh database
already has both columns and the 5-column constraint.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0059_arena_output_budget_variant"
down_revision = "0058_arena_match_reasoning_effort"
branch_labels = None
depends_on = None

_OLD_UQ = "uq_arena_match_run_workflow_model_effort"
_NEW_UQ = "uq_arena_match_run_wf_model_effort_budget"
_OLD_COLS = ["run_id", "workflow_id", "model_id", "reasoning_effort"]
_NEW_COLS = [*_OLD_COLS, "max_output_tokens"]


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _uniques(table: str) -> set[str]:
    return {u["name"] for u in inspect(op.get_bind()).get_unique_constraints(table)}


def upgrade() -> None:
    tables = _tables()

    if "arena_run" in tables and "max_output_tokens" not in _columns("arena_run"):
        op.add_column(
            "arena_run", sa.Column("max_output_tokens", sa.JSON(), nullable=True)
        )

    if "arena_match" not in tables:
        return

    if "max_output_tokens" not in _columns("arena_match"):
        op.add_column(
            "arena_match",
            sa.Column("max_output_tokens", sa.Integer(),
                      nullable=False, server_default="0"),
        )

    uniques = _uniques("arena_match")
    if _NEW_UQ not in uniques:
        # batch_alter_table, never a direct constraint op: SQLite cannot alter a
        # constraint in place, so alembic rebuilds the table (create, copy, drop,
        # rename) and carries the rows, sibling FKs and other indexes across.
        with op.batch_alter_table("arena_match") as batch:
            if _OLD_UQ in uniques:
                batch.drop_constraint(_OLD_UQ, type_="unique")
            batch.create_unique_constraint(_NEW_UQ, _NEW_COLS)


def downgrade() -> None:
    tables = _tables()

    if "arena_match" in tables:
        uniques = _uniques("arena_match")
        if _OLD_UQ not in uniques:
            with op.batch_alter_table("arena_match") as batch:
                if _NEW_UQ in uniques:
                    batch.drop_constraint(_NEW_UQ, type_="unique")
                batch.create_unique_constraint(_OLD_UQ, _OLD_COLS)

        if "max_output_tokens" in _columns("arena_match"):
            # batch mode, never a direct drop_column: a create_all database hands
            # a column its ORM foreign keys, and SQLite refuses DROP COLUMN while
            # any FK definition names the column.
            with op.batch_alter_table("arena_match") as batch:
                batch.drop_column("max_output_tokens")

    if "arena_run" in tables and "max_output_tokens" in _columns("arena_run"):
        with op.batch_alter_table("arena_run") as batch:
            batch.drop_column("max_output_tokens")
