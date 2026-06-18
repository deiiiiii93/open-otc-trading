"""market data profiles

Revision ID: 0006_market_data_profiles
Revises: 0005_global_pricing_market_profiles
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0006_market_data_profiles"
down_revision = "0005_global_pricing_market_profiles"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "market_data_profiles" not in _tables():
        op.create_table(
            "market_data_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False, server_default="akshare"),
            sa.Column("symbol", sa.String(length=80), nullable=False),
            sa.Column("asset_class", sa.String(length=40), nullable=False, server_default="index"),
            sa.Column("start_date", sa.String(length=20), nullable=False),
            sa.Column("end_date", sa.String(length=20), nullable=False),
            sa.Column("adjust", sa.String(length=20), nullable=False, server_default="qfq"),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
    if "ix_market_data_profiles_symbol" not in _indexes("market_data_profiles"):
        op.create_index("ix_market_data_profiles_symbol", "market_data_profiles", ["symbol"])


def downgrade() -> None:
    if "market_data_profiles" in _tables():
        if "ix_market_data_profiles_symbol" in _indexes("market_data_profiles"):
            op.drop_index("ix_market_data_profiles_symbol", table_name="market_data_profiles")
        op.drop_table("market_data_profiles")
