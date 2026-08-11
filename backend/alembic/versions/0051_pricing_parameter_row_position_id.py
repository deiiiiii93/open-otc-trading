"""Bind pricing parameter rows to positions.

Adds pricing_parameter_rows.position_id (nullable FK to positions) so
curve-generated rows resolve uniquely per position even when the position has
no source_trade_id. Null for imported rows. No backfill.

HOUSE RULE: migration-local Core SQL only — no ORM models/services.

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so on a fresh
database this column already exists before the chain reaches here. Every
migration after 0001 must therefore guard its DDL on current schema state.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0051_pricing_parameter_row_position_id"
down_revision = "0050_instrument_term_structure_curves"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "position_id" not in _columns("pricing_parameter_rows"):
        op.add_column(
            "pricing_parameter_rows",
            sa.Column("position_id", sa.Integer(), nullable=True),
        )
    if "ix_pricing_parameter_rows_position_id" not in _indexes(
        "pricing_parameter_rows"
    ):
        op.create_index(
            "ix_pricing_parameter_rows_position_id",
            "pricing_parameter_rows",
            ["position_id"],
        )


def downgrade() -> None:
    if "ix_pricing_parameter_rows_position_id" in _indexes(
        "pricing_parameter_rows"
    ):
        op.drop_index(
            "ix_pricing_parameter_rows_position_id",
            table_name="pricing_parameter_rows",
        )
    if "position_id" in _columns("pricing_parameter_rows"):
        op.drop_column("pricing_parameter_rows", "position_id")
