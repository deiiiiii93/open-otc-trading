"""arena_match.score_breakdown — per-check objective + judge breakdown

Revision ID: 0034_arena_match_score_breakdown
Revises: 0033_agent_thread_source
Create Date: 2026-06-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0034_arena_match_score_breakdown"
down_revision = "0033_agent_thread_source"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if "arena_match" in set(inspect(op.get_bind()).get_table_names()):
        if "score_breakdown" not in _columns("arena_match"):
            op.add_column(
                "arena_match",
                sa.Column("score_breakdown", sa.JSON(), nullable=True),
            )


def downgrade() -> None:
    if "score_breakdown" in _columns("arena_match"):
        with op.batch_alter_table("arena_match") as batch:
            batch.drop_column("score_breakdown")
