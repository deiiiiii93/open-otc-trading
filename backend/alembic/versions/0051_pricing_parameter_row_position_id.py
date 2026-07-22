"""Bind pricing parameter rows to positions.

Adds pricing_parameter_rows.position_id (nullable FK to positions) so
curve-generated rows resolve uniquely per position even when the position has
no source_trade_id. Null for imported rows. No backfill.

HOUSE RULE: migration-local Core SQL only — no ORM models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0051_pricing_parameter_row_position_id"
down_revision = "0050_instrument_term_structure_curves"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "pricing_parameter_rows",
        sa.Column("position_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_pricing_parameter_rows_position_id",
        "pricing_parameter_rows",
        ["position_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_pricing_parameter_rows_position_id",
        table_name="pricing_parameter_rows",
    )
    op.drop_column("pricing_parameter_rows", "position_id")
