"""task runs

Revision ID: 0008_task_runs
Revises: 0007_position_trade_effective_date
Create Date: 2026-05-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0008_task_runs"
down_revision = "0007_position_trade_effective_date"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "task_runs" in _tables():
        return
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=True),
        sa.Column("risk_run_id", sa.Integer(), nullable=True),
        sa.Column("report_job_id", sa.Integer(), nullable=True),
        sa.Column("progress_current", sa.Integer(), nullable=False),
        sa.Column("progress_total", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.ForeignKeyConstraint(["report_job_id"], ["report_jobs.id"]),
        sa.ForeignKeyConstraint(["risk_run_id"], ["risk_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_runs_kind", "task_runs", ["kind"])
    op.create_index("ix_task_runs_portfolio_id", "task_runs", ["portfolio_id"])
    op.create_index("ix_task_runs_report_job_id", "task_runs", ["report_job_id"])
    op.create_index("ix_task_runs_risk_run_id", "task_runs", ["risk_run_id"])
    op.create_index("ix_task_runs_status", "task_runs", ["status"])


def downgrade() -> None:
    if "task_runs" not in _tables():
        return
    op.drop_index("ix_task_runs_status", table_name="task_runs")
    op.drop_index("ix_task_runs_risk_run_id", table_name="task_runs")
    op.drop_index("ix_task_runs_report_job_id", table_name="task_runs")
    op.drop_index("ix_task_runs_portfolio_id", table_name="task_runs")
    op.drop_index("ix_task_runs_kind", table_name="task_runs")
    op.drop_table("task_runs")
