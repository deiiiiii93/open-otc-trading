"""risk run pricing parameter profile

Revision ID: 0010_risk_run_pricing_parameter_profile
Revises: 0009_strip_default_quad_grid_points
Create Date: 2026-05-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0010_risk_run_pricing_parameter_profile"
down_revision = "0009_strip_default_quad_grid_points"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {col["name"] for col in inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if "risk_runs" not in set(inspect(bind).get_table_names()):
        return
    with op.batch_alter_table("risk_runs") as batch_op:
        if "pricing_parameter_profile_id" not in _columns("risk_runs"):
            batch_op.add_column(
                sa.Column(
                    "pricing_parameter_profile_id",
                    sa.Integer(),
                    sa.ForeignKey("pricing_parameter_profiles.id"),
                    nullable=True,
                ),
            )
        if "ix_risk_runs_pricing_parameter_profile_id" not in _indexes("risk_runs"):
            batch_op.create_index(
                "ix_risk_runs_pricing_parameter_profile_id",
                ["pricing_parameter_profile_id"],
            )


def downgrade() -> None:
    if "risk_runs" not in set(inspect(op.get_bind()).get_table_names()):
        return
    with op.batch_alter_table("risk_runs") as batch_op:
        if "ix_risk_runs_pricing_parameter_profile_id" in _indexes("risk_runs"):
            batch_op.drop_index("ix_risk_runs_pricing_parameter_profile_id")
        if "pricing_parameter_profile_id" in _columns("risk_runs"):
            batch_op.drop_column("pricing_parameter_profile_id")
