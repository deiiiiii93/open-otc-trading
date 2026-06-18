"""currency convention: position.currency, agent_threads.report_currency, fx_rates

Revision ID: 0020_currency_convention
Revises: 0019_underlying_master_data
Create Date: 2026-06-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text

revision = "0020_currency_convention"
down_revision = "0019_underlying_master_data"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()

    if "positions" in _tables():
        if "currency" not in _columns("positions"):
            op.add_column(
                "positions",
                sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
            )
        # Backfill priority: product.currency -> CNY.
        # Always run so freshly-added rows (server_default CNY) get the correct
        # product currency, whether or not the column was just added.
        if "products" in _tables():
            bind.execute(text(
                "UPDATE positions SET currency = ("
                "  SELECT p.currency FROM products p WHERE p.id = positions.product_id"
                ") WHERE product_id IS NOT NULL AND EXISTS ("
                "  SELECT 1 FROM products p WHERE p.id = positions.product_id"
                ")"
            ))
        bind.execute(text("UPDATE positions SET currency = 'CNY' WHERE currency IS NULL"))

    if "agent_threads" in _tables() and "report_currency" not in _columns("agent_threads"):
        op.add_column(
            "agent_threads",
            sa.Column(
                "report_currency", sa.String(length=16),
                nullable=False, server_default="by_position",
            ),
        )

    if "fx_rates" not in _tables():
        op.create_table(
            "fx_rates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("base_currency", sa.String(length=8), nullable=False),
            sa.Column("quote_currency", sa.String(length=8), nullable=False),
            sa.Column("rate", sa.Float(), nullable=False),
            sa.Column("as_of_date", sa.DateTime(), nullable=False),
            sa.Column("pricing_parameter_profile_id", sa.Integer(), nullable=True),
            sa.Column("source", sa.String(length=40), nullable=False, server_default="manual"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(
                ["pricing_parameter_profile_id"], ["pricing_parameter_profiles.id"]
            ),
        )
        op.create_index(
            "ix_fx_rates_pair_as_of", "fx_rates",
            ["base_currency", "quote_currency", "as_of_date"],
        )


def downgrade() -> None:
    if "fx_rates" in _tables():
        op.drop_table("fx_rates")
    if "report_currency" in _columns("agent_threads"):
        op.drop_column("agent_threads", "report_currency")
    if "currency" in _columns("positions"):
        op.drop_column("positions", "currency")
