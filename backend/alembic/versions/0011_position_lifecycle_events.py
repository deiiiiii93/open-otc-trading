"""position lifecycle events

Revision ID: 0011_position_lifecycle_events
Revises: 0010_risk_run_pricing_parameter_profile
Create Date: 2026-05-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0011_position_lifecycle_events"
down_revision = "0010_risk_run_pricing_parameter_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if "position_lifecycle_events" not in set(inspect(bind).get_table_names()):
        op.create_table(
            "position_lifecycle_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("event_type", sa.String(length=80), nullable=False),
            sa.Column("event_data", sa.JSON(), nullable=False),
            sa.Column("old_status", sa.String(length=40), nullable=True),
            sa.Column("new_status", sa.String(length=40), nullable=True),
            sa.Column("actor", sa.String(length=120), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["position_id"],
                ["positions.id"],
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_position_lifecycle_events_position_id",
            "position_lifecycle_events",
            ["position_id"],
        )
        op.create_index(
            "ix_position_lifecycle_events_event_type",
            "position_lifecycle_events",
            ["event_type"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if "position_lifecycle_events" in set(inspect(bind).get_table_names()):
        op.drop_index("ix_position_lifecycle_events_event_type", table_name="position_lifecycle_events")
        op.drop_index("ix_position_lifecycle_events_position_id", table_name="position_lifecycle_events")
        op.drop_table("position_lifecycle_events")
