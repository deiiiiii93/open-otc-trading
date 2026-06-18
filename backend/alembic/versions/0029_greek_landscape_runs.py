"""greek landscape runs and task linkage

Revision ID: 0029_greek_landscape_runs
Revises: 0028_engine_configs
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0029_greek_landscape_runs"
down_revision = "0028_engine_configs"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def _indexes(table: str) -> set[str]:
    inspector = inspect(op.get_bind())
    if table not in set(inspector.get_table_names()):
        return set()
    return {i["name"] for i in inspector.get_indexes(table)}


def upgrade() -> None:
    if not _has_table("greek_landscape_runs"):
        op.create_table(
            "greek_landscape_runs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("portfolio_id", sa.Integer(), sa.ForeignKey("portfolios.id"), nullable=False),
            sa.Column("pricing_parameter_profile_id", sa.Integer(), sa.ForeignKey("pricing_parameter_profiles.id"), nullable=True),
            sa.Column("engine_config_id", sa.Integer(), sa.ForeignKey("engine_config_variants.id"), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("config", sa.JSON(), nullable=False),
            sa.Column("results", sa.JSON(), nullable=False),
            sa.Column("excluded_positions", sa.JSON(), nullable=True),
            sa.Column("resolved_position_ids", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    greek_landscape_indexes = _indexes("greek_landscape_runs")
    if "ix_greek_landscape_runs_portfolio_id" not in greek_landscape_indexes:
        op.create_index("ix_greek_landscape_runs_portfolio_id", "greek_landscape_runs", ["portfolio_id"])
    if "ix_greek_landscape_runs_pricing_parameter_profile_id" not in greek_landscape_indexes:
        op.create_index("ix_greek_landscape_runs_pricing_parameter_profile_id", "greek_landscape_runs", ["pricing_parameter_profile_id"])
    if "ix_greek_landscape_runs_engine_config_id" not in greek_landscape_indexes:
        op.create_index("ix_greek_landscape_runs_engine_config_id", "greek_landscape_runs", ["engine_config_id"])
    if "greeks_landscape_run_id" not in _columns("task_runs"):
        with op.batch_alter_table("task_runs") as batch:
            batch.add_column(sa.Column("greeks_landscape_run_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(
                "fk_task_runs_greeks_landscape_run_id",
                "greek_landscape_runs",
                ["greeks_landscape_run_id"],
                ["id"],
            )
        op.create_index("ix_task_runs_greeks_landscape_run_id", "task_runs", ["greeks_landscape_run_id"])


def downgrade() -> None:
    if "ix_task_runs_greeks_landscape_run_id" in _indexes("task_runs"):
        op.drop_index("ix_task_runs_greeks_landscape_run_id", table_name="task_runs")
    if "greeks_landscape_run_id" in _columns("task_runs"):
        with op.batch_alter_table("task_runs") as batch:
            batch.drop_column("greeks_landscape_run_id")
    if _has_table("greek_landscape_runs"):
        op.drop_table("greek_landscape_runs")
