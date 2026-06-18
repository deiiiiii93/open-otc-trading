"""position lifecycle event cancellation

Revision ID: 0017_position_lifecycle_event_cancellation
Revises: 0016_structured_position_terms
Create Date: 2026-05-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0017_position_lifecycle_event_cancellation"
down_revision = "0016_structured_position_terms"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in set(inspector.get_table_names()):
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    columns = _columns("position_lifecycle_events")
    if not columns:
        return
    with op.batch_alter_table("position_lifecycle_events") as batch:
        if "cancelled_at" not in columns:
            batch.add_column(sa.Column("cancelled_at", sa.DateTime(), nullable=True))
        if "cancelled_by" not in columns:
            batch.add_column(sa.Column("cancelled_by", sa.String(length=120), nullable=True))
        if "cancellation_reason" not in columns:
            batch.add_column(sa.Column("cancellation_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    columns = _columns("position_lifecycle_events")
    if not columns:
        return
    with op.batch_alter_table("position_lifecycle_events") as batch:
        if "cancellation_reason" in columns:
            batch.drop_column("cancellation_reason")
        if "cancelled_by" in columns:
            batch.drop_column("cancelled_by")
        if "cancelled_at" in columns:
            batch.drop_column("cancelled_at")
