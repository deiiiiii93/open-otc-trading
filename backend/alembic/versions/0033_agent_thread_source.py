"""agent_threads.source and arena_run_id

Revision ID: 0033_agent_thread_source
Revises: 0032_arena_runs
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0033_agent_thread_source"
down_revision = "0032_arena_runs"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    cols = _columns("agent_threads")
    if "source" not in cols:
        op.add_column(
            "agent_threads",
            sa.Column("source", sa.String(length=20), nullable=False, server_default="desk"),
        )
    if "arena_run_id" not in cols:
        op.add_column(
            "agent_threads",
            sa.Column("arena_run_id", sa.Integer(), nullable=True),
        )
    if "ix_agent_threads_arena_run_id" not in _indexes("agent_threads"):
        op.create_index("ix_agent_threads_arena_run_id", "agent_threads", ["arena_run_id"])


def downgrade() -> None:
    if "ix_agent_threads_arena_run_id" in _indexes("agent_threads"):
        op.drop_index("ix_agent_threads_arena_run_id", table_name="agent_threads")
    cols = _columns("agent_threads")
    with op.batch_alter_table("agent_threads") as batch:
        if "arena_run_id" in cols:
            batch.drop_column("arena_run_id")
        if "source" in cols:
            batch.drop_column("source")
