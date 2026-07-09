"""arena_run.trials — number of trials folded per (workflow, model) match

Revision ID: 0045_arena_run_trials
Revises: 0044_hedge_tag
Create Date: 2026-07-09

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_arena_run_trials"
down_revision = "0044_hedge_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "arena_run",
        sa.Column("trials", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("arena_run", "trials")
