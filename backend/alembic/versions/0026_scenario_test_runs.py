"""scenario test runs

Revision ID: 0026_scenario_test_runs
Revises: 0025_position_kind
Create Date: 2026-06-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0026_scenario_test_runs"
down_revision = "0025_position_kind"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    if not _has_table("scenario_test_runs"):
        op.create_table(
            "scenario_test_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
            sa.Column(
                "pricing_parameter_profile_id", sa.Integer(),
                sa.ForeignKey("pricing_parameter_profiles.id"), nullable=True,
            ),
            sa.Column("resolved_position_ids", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="queued"),
            sa.Column("scenario_spec", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("config", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("results", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("excluded_positions", sa.JSON(), nullable=True),
            sa.Column("artifacts", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_scenario_test_runs_portfolio_id", "scenario_test_runs", ["portfolio_id"])
        op.create_index(
            "ix_scenario_test_runs_pricing_parameter_profile_id",
            "scenario_test_runs", ["pricing_parameter_profile_id"],
        )

    if "scenario_test_run_id" not in _columns("task_runs"):
        with op.batch_alter_table("task_runs") as batch:
            batch.add_column(sa.Column("scenario_test_run_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_task_runs_scenario_test_run_id",
                "scenario_test_runs",
                ["scenario_test_run_id"],
                ["id"],
            )
        op.create_index(
            "ix_task_runs_scenario_test_run_id", "task_runs", ["scenario_test_run_id"]
        )


def downgrade() -> None:
    if "scenario_test_run_id" in _columns("task_runs"):
        op.drop_index("ix_task_runs_scenario_test_run_id", table_name="task_runs")
        with op.batch_alter_table("task_runs") as batch:
            batch.drop_column("scenario_test_run_id")
    if _has_table("scenario_test_runs"):
        op.drop_table("scenario_test_runs")
