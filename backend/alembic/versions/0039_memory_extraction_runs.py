"""memory_extraction_runs — durable extraction idempotency + recovery

Revision ID: 0039_memory_extraction_runs
Revises: 0038_memory_entries_evolve
Create Date: 2026-06-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0039_memory_extraction_runs"
down_revision = "0038_memory_entries_evolve"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if not _has_table("memory_extraction_runs"):
        op.create_table(
            "memory_extraction_runs",
            sa.Column("run_key", sa.String(), primary_key=True),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=False),
            sa.Column("thread_id", sa.Integer(), nullable=True),
            sa.Column("persona", sa.String(), nullable=True),
            sa.Column("book_scope_id", sa.String(), nullable=True),
            sa.Column("trigger_message_id", sa.Integer(), nullable=True),
            sa.Column("last_extracted_message_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )
        op.create_index("ix_memory_runs_session", "memory_extraction_runs", ["session_id"])
        op.create_index("ix_memory_runs_status", "memory_extraction_runs", ["status"])


def downgrade() -> None:
    op.drop_table("memory_extraction_runs")
