"""asian averaging date weight

Revision ID: 0031_asian_averaging_weight
Revises: 0030_engine_config_business_days
Create Date: 2026-06-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0031_asian_averaging_weight"
down_revision = "0030_engine_config_business_days"
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return False
    return column in {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_column("asian_averaging_dates", "weight"):
        with op.batch_alter_table("asian_averaging_dates") as batch:
            batch.add_column(sa.Column("weight", sa.Float(), nullable=True))


def downgrade() -> None:
    if _has_column("asian_averaging_dates", "weight"):
        with op.batch_alter_table("asian_averaging_dates") as batch:
            batch.drop_column("weight")
