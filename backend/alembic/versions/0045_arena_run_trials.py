"""arena_run.trials — number of trials folded per (workflow, model) match

Revision ID: 0045_arena_run_trials
Revises: 0044_hedge_tag
Create Date: 2026-07-09

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0045_arena_run_trials"
down_revision = "0044_hedge_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("arena_run")
    }
    if "trials" in columns:
        return
    op.add_column(
        "arena_run",
        sa.Column("trials", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    columns = {
        column["name"]
        for column in inspect(op.get_bind()).get_columns("arena_run")
    }
    if "trials" not in columns:
        return
    op.drop_column("arena_run", "trials")
