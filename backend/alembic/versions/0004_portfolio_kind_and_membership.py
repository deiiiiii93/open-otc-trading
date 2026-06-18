"""portfolio kind + membership columns

Revision ID: 0004_portfolio_kind_and_membership
Revises: 0003_risk_run_persistence
Create Date: 2026-05-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0004_portfolio_kind_and_membership"
down_revision = "0003_risk_run_persistence"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    insp = inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    cols = _columns("portfolios")
    with op.batch_alter_table("portfolios") as batch:
        if "kind" not in cols:
            batch.add_column(sa.Column("kind", sa.String(length=20), nullable=False, server_default="container"))
        if "filter_rule" not in cols:
            batch.add_column(sa.Column("filter_rule", sa.JSON(), nullable=True))
        if "manual_include_ids" not in cols:
            batch.add_column(sa.Column("manual_include_ids", sa.JSON(), nullable=False, server_default="[]"))
        if "manual_exclude_ids" not in cols:
            batch.add_column(sa.Column("manual_exclude_ids", sa.JSON(), nullable=False, server_default="[]"))
        if "source_portfolio_ids" not in cols:
            batch.add_column(sa.Column("source_portfolio_ids", sa.JSON(), nullable=False, server_default="[]"))
        if "tags" not in cols:
            batch.add_column(sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"))
        if "description" not in cols:
            batch.add_column(sa.Column("description", sa.Text(), nullable=True))

    if "resolved_position_ids" not in _columns("position_valuation_runs"):
        op.add_column("position_valuation_runs", sa.Column("resolved_position_ids", sa.JSON(), nullable=True))
    if "resolved_position_ids" not in _columns("risk_runs"):
        op.add_column("risk_runs", sa.Column("resolved_position_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    if "resolved_position_ids" in _columns("risk_runs"):
        op.drop_column("risk_runs", "resolved_position_ids")
    if "resolved_position_ids" in _columns("position_valuation_runs"):
        op.drop_column("position_valuation_runs", "resolved_position_ids")
    cols = _columns("portfolios")
    with op.batch_alter_table("portfolios") as batch:
        for name in (
            "description", "tags", "source_portfolio_ids",
            "manual_exclude_ids", "manual_include_ids", "filter_rule", "kind",
        ):
            if name in cols:
                batch.drop_column(name)
