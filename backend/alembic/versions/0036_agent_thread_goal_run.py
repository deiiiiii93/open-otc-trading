"""agent_threads.goal_run and goal_contract (goal mode, spec §H)

Revision ID: 0036_agent_thread_goal_run
Revises: 0035_desk_workflows
Create Date: 2026-06-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0036_agent_thread_goal_run"
down_revision = "0035_desk_workflows"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    cols = _columns("agent_threads")
    if "goal_run" not in cols:
        op.add_column("agent_threads", sa.Column("goal_run", sa.JSON(), nullable=True))
    if "goal_contract" not in cols:
        op.add_column("agent_threads", sa.Column("goal_contract", sa.JSON(), nullable=True))


def downgrade() -> None:
    cols = _columns("agent_threads")
    if "goal_contract" in cols:
        op.drop_column("agent_threads", "goal_contract")
    if "goal_run" in cols:
        op.drop_column("agent_threads", "goal_run")
