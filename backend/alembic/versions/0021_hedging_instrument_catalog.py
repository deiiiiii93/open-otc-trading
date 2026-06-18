"""hedging instrument catalog + map

Revision ID: 0021_hedging_instrument_catalog
Revises: 0020_currency_convention
Create Date: 2026-06-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0021_hedging_instrument_catalog"
down_revision = "0020_currency_convention"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "hedge_instruments" not in _tables():
        op.create_table(
            "hedge_instruments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id"), nullable=False),
            sa.Column("family", sa.String(length=40), nullable=False),
            sa.Column("series_root", sa.String(length=40), nullable=False),
            sa.Column("exchange", sa.String(length=40), nullable=False),
            sa.Column("contract_code", sa.String(length=80), nullable=False),
            sa.Column("instrument_type", sa.String(length=20), nullable=False),
            sa.Column("option_type", sa.String(length=4), nullable=True),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("expiry", sa.Date(), nullable=True),
            sa.Column("multiplier", sa.Float(), nullable=True),
            sa.Column("last_price", sa.Float(), nullable=True),
            sa.Column("akshare_symbol", sa.String(length=80), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="live"),
            sa.Column("loaded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("exchange", "contract_code", name="uq_hedge_instruments_exchange_code"),
        )
    for name, cols in (
        ("ix_hedge_instruments_underlying_id", ["underlying_id"]),
        ("ix_hedge_instruments_status", ["status"]),
        ("ix_hedge_instruments_underlying_family_status", ["underlying_id", "family", "status"]),
    ):
        if name not in _indexes("hedge_instruments"):
            op.create_index(name, "hedge_instruments", cols)

    if "hedge_map_entries" not in _tables():
        op.create_table(
            "hedge_map_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id"), nullable=False),
            sa.Column("exchange", sa.String(length=40), nullable=False),
            sa.Column("contract_code", sa.String(length=80), nullable=False),
            sa.Column("family", sa.String(length=40), nullable=False),
            sa.Column("series_root", sa.String(length=40), nullable=False),
            sa.Column("instrument_type", sa.String(length=20), nullable=False),
            sa.Column("option_type", sa.String(length=4), nullable=True),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("expiry", sa.Date(), nullable=True),
            sa.Column("reconcile_status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("marked_by", sa.String(length=80), nullable=True),
            sa.Column("marked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("underlying_id", "exchange", "contract_code", name="uq_hedge_map_entries_underlying_contract"),
        )
    if "ix_hedge_map_entries_underlying_id" not in _indexes("hedge_map_entries"):
        op.create_index("ix_hedge_map_entries_underlying_id", "hedge_map_entries", ["underlying_id"])


def downgrade() -> None:
    if "hedge_map_entries" in _tables():
        if "ix_hedge_map_entries_underlying_id" in _indexes("hedge_map_entries"):
            op.drop_index("ix_hedge_map_entries_underlying_id", table_name="hedge_map_entries")
        op.drop_table("hedge_map_entries")
    if "hedge_instruments" in _tables():
        for name in (
            "ix_hedge_instruments_underlying_family_status",
            "ix_hedge_instruments_status",
            "ix_hedge_instruments_underlying_id",
        ):
            if name in _indexes("hedge_instruments"):
                op.drop_index(name, table_name="hedge_instruments")
        op.drop_table("hedge_instruments")
