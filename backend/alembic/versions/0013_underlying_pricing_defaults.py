"""underlying pricing defaults

Revision ID: 0013_underlying_pricing_defaults
Revises: 0012_rfq_quote_versions
Create Date: 2026-05-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0013_underlying_pricing_defaults"
down_revision = "0012_rfq_quote_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "underlying_pricing_defaults" in set(inspector.get_table_names()):
        return
    op.create_table(
        "underlying_pricing_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying", sa.String(length=80), nullable=False, unique=True),
        sa.Column("rate", sa.Float(), nullable=True),
        sa.Column("dividend_yield", sa.Float(), nullable=True),
        sa.Column("volatility", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_underlying_pricing_defaults_underlying",
        "underlying_pricing_defaults",
        ["underlying"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "underlying_pricing_defaults" not in set(inspector.get_table_names()):
        return
    indexes = {index["name"] for index in inspector.get_indexes("underlying_pricing_defaults")}
    if "ix_underlying_pricing_defaults_underlying" in indexes:
        op.drop_index(
            "ix_underlying_pricing_defaults_underlying",
            table_name="underlying_pricing_defaults",
        )
    op.drop_table("underlying_pricing_defaults")
