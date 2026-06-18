"""underlying master data

Revision ID: 0019_underlying_master_data
Revises: 0018_product_root_family_tables
Create Date: 2026-05-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0019_underlying_master_data"
down_revision = "0018_product_root_family_tables"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "underlyings" not in _tables():
        op.create_table(
            "underlyings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("symbol", sa.String(length=80), nullable=False, unique=True),
            sa.Column("display_name", sa.String(length=160), nullable=True),
            sa.Column("asset_class", sa.String(length=40), nullable=False, server_default="index"),
            sa.Column("market", sa.String(length=40), nullable=True),
            sa.Column("exchange", sa.String(length=40), nullable=True),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
            sa.Column("akshare_symbol", sa.String(length=80), nullable=True),
            sa.Column("akshare_asset_class", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="draft"),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("volatility", sa.Float(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    for name, cols, unique in (
        ("ix_underlyings_symbol", ["symbol"], True),
        ("ix_underlyings_akshare_symbol", ["akshare_symbol"], False),
        ("ix_underlyings_status", ["status"], False),
    ):
        if name not in _indexes("underlyings"):
            op.create_index(name, "underlyings", cols, unique=unique)

    for table in ("positions", "products", "market_data_profiles"):
        if table in _tables() and "underlying_id" not in _columns(table):
            op.add_column(table, sa.Column("underlying_id", sa.Integer(), nullable=True))
            op.create_index(f"ix_{table}_underlying_id", table, ["underlying_id"])

    bind = op.get_bind()
    if "underlying_pricing_defaults" in _tables():
        bind.execute(
            text(
                """
                INSERT OR IGNORE INTO underlyings
                    (symbol, display_name, asset_class, currency, akshare_symbol,
                     akshare_asset_class, status, source, rate, dividend_yield,
                     volatility, notes, created_at, updated_at)
                SELECT underlying, underlying, 'index', 'CNY',
                       CASE WHEN instr(underlying, '.') > 0
                            THEN substr(underlying, 1, instr(underlying, '.') - 1)
                            ELSE underlying END,
                       'index', 'active', 'pricing_default', rate, dividend_yield,
                       volatility, notes, created_at, updated_at
                FROM underlying_pricing_defaults
                WHERE underlying IS NOT NULL AND trim(underlying) != ''
                """
            )
        )
    for table in ("positions", "products", "market_data_profiles"):
        if table not in _tables() or "underlying_id" not in _columns(table):
            continue
        symbol_column = "symbol" if table == "market_data_profiles" else "underlying"
        bind.execute(
            text(
                f"""
                INSERT OR IGNORE INTO underlyings
                    (symbol, display_name, asset_class, currency, akshare_symbol,
                     akshare_asset_class, status, source, created_at, updated_at)
                SELECT DISTINCT {symbol_column}, {symbol_column}, 'index', 'CNY',
                       CASE WHEN instr({symbol_column}, '.') > 0
                            THEN substr({symbol_column}, 1, instr({symbol_column}, '.') - 1)
                            ELSE {symbol_column} END,
                       'index', 'draft', '{table}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM {table}
                WHERE {symbol_column} IS NOT NULL AND trim({symbol_column}) != ''
                """
            )
        )
        bind.execute(
            text(
                f"""
                UPDATE {table}
                SET underlying_id = (
                    SELECT underlyings.id FROM underlyings
                    WHERE underlyings.symbol = {table}.{symbol_column}
                )
                WHERE underlying_id IS NULL
                  AND {symbol_column} IS NOT NULL
                  AND trim({symbol_column}) != ''
                """
            )
        )


def downgrade() -> None:
    for table in ("market_data_profiles", "products", "positions"):
        if table in _tables() and "underlying_id" in _columns(table):
            with op.batch_alter_table(table) as batch:
                indexes = _indexes(table)
                if f"ix_{table}_underlying_id" in indexes:
                    batch.drop_index(f"ix_{table}_underlying_id")
                batch.drop_column("underlying_id")
    if "underlyings" in _tables():
        for index_name in (
            "ix_underlyings_status",
            "ix_underlyings_akshare_symbol",
            "ix_underlyings_symbol",
        ):
            if index_name in _indexes("underlyings"):
                op.drop_index(index_name, table_name="underlyings")
        op.drop_table("underlyings")
