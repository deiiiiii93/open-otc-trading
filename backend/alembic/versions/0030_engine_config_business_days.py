"""engine config business days in year

Revision ID: 0030_engine_config_business_days
Revises: 0029_greek_landscape_runs
Create Date: 2026-06-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0030_engine_config_business_days"
down_revision = "0029_greek_landscape_runs"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("engine_config_variants", "business_days_in_year"):
        with op.batch_alter_table("engine_config_variants") as batch:
            batch.add_column(sa.Column("business_days_in_year", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column("engine_config_variants", "business_days_in_year"):
        with op.batch_alter_table("engine_config_variants") as batch:
            batch.drop_column("business_days_in_year")
