"""arena_run.reasoning_effort — record the effort regime a board was driven at

Revision ID: 0056_arena_run_reasoning_effort
Revises: 0055_settlement_cashflows

NULL means "not pinned — vendor default", which is the honest reading of every
existing row: nothing in the stack sent a `reasoning_effort` for runs #8-#104, so
backfilling any level here would fabricate provenance.

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so on a fresh
database this column already exists before the chain reaches here. Every
migration after 0001 must therefore guard its DDL on current schema state.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0056_arena_run_reasoning_effort"
down_revision = "0055_settlement_cashflows"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "arena_run" not in _tables():
        return
    if "reasoning_effort" not in _columns("arena_run"):
        op.add_column(
            "arena_run",
            sa.Column("reasoning_effort", sa.String(16), nullable=True),
        )


def downgrade() -> None:
    if "arena_run" not in _tables():
        return
    if "reasoning_effort" in _columns("arena_run"):
        # batch_alter_table, never a direct op.drop_column: SQLite refuses
        # ALTER TABLE ... DROP COLUMN while any FK definition names the column,
        # and a create_all database hands columns their ORM foreign keys even
        # when no historical migration created them.
        with op.batch_alter_table("arena_run") as batch:
            batch.drop_column("reasoning_effort")
