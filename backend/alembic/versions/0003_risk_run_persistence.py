"""risk run persistence

Revision ID: 0003_risk_run_persistence
Revises: 0002_position_import_pricing
Create Date: 2026-05-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0003_risk_run_persistence"
down_revision = "0002_position_import_pricing"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "risk_runs" in _tables():
        return
    op.create_table(
        "risk_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("portfolio_id", sa.Integer(), nullable=False),
        sa.Column("market_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=80), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("scenario_cells", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["market_snapshot_id"], ["market_snapshots.id"]),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_risk_runs_portfolio_id", "risk_runs", ["portfolio_id"])


def downgrade() -> None:
    if "risk_runs" not in _tables():
        return
    op.drop_index("ix_risk_runs_portfolio_id", table_name="risk_runs")
    op.drop_table("risk_runs")
