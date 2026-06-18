"""add position OTC/listed kind

Revision ID: 0025_position_kind
Revises: 0024_instrument_unification
Create Date: 2026-06-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0025_position_kind"
down_revision = "0024_instrument_unification"
branch_labels = None
depends_on = None


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    if "position_kind" not in _columns("positions"):
        with op.batch_alter_table("positions") as batch:
            batch.add_column(
                sa.Column(
                    "position_kind",
                    sa.String(length=16),
                    nullable=False,
                    server_default="otc",
                )
            )
        op.create_index("ix_positions_position_kind", "positions", ["position_kind"])

    bind = op.get_bind()
    bind.execute(
        text(
            """
            UPDATE positions
            SET position_kind = 'listed'
            WHERE source_trade_id LIKE 'HEDGE:%'
               OR json_extract(source_payload, '$.hedge.is_hedge') = 1
               OR product_id IN (
                   SELECT id FROM products WHERE product_family IN ('futures', 'spot')
               )
            """
        )
    )
    bind.execute(
        text(
            """
            UPDATE positions
            SET position_kind = 'otc'
            WHERE position_kind NOT IN ('otc', 'listed') OR position_kind IS NULL
            """
        )
    )


def downgrade() -> None:
    if "position_kind" in _columns("positions"):
        with op.batch_alter_table("positions") as batch:
            batch.drop_index("ix_positions_position_kind")
            batch.drop_column("position_kind")
