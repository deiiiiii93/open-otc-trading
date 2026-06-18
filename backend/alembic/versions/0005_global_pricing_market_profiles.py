"""global pricing parameter profiles

Revision ID: 0005_global_pricing_market_profiles
Revises: 0004_portfolio_kind_and_membership
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0005_global_pricing_market_profiles"
down_revision = "0004_portfolio_kind_and_membership"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    insp = inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "pricing_parameter_profiles" not in tables:
        op.create_table(
            "pricing_parameter_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False, server_default="xlsx"),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="completed"),
            sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index(
            "ix_pricing_parameter_profiles_valuation_date",
            "pricing_parameter_profiles",
            ["valuation_date"],
        )

    tables = _tables()
    if "pricing_parameter_rows" not in tables:
        op.create_table(
            "pricing_parameter_rows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("pricing_parameter_profiles.id"), nullable=False),
            sa.Column("source_trade_id", sa.String(length=160), nullable=False),
            sa.Column("symbol", sa.String(length=80), nullable=False),
            sa.Column("spot", sa.Float(), nullable=True),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("volatility", sa.Float(), nullable=True),
            sa.Column("source_row", sa.Integer(), nullable=True),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_pricing_parameter_rows_profile_id", "pricing_parameter_rows", ["profile_id"])
        op.create_index("ix_pricing_parameter_rows_source_trade_id", "pricing_parameter_rows", ["source_trade_id"])
        op.create_index("ix_pricing_parameter_rows_symbol", "pricing_parameter_rows", ["symbol"])

    if "pricing_parameter_profile_id" not in _columns("position_valuation_runs"):
        with op.batch_alter_table("position_valuation_runs") as batch:
            batch.add_column(
                sa.Column(
                    "pricing_parameter_profile_id",
                    sa.Integer(),
                    sa.ForeignKey(
                        "pricing_parameter_profiles.id",
                        name="fk_position_valuation_runs_pricing_parameter_profile_id",
                    ),
                    nullable=True,
                )
            )
            batch.create_index(
                "ix_position_valuation_runs_pricing_parameter_profile_id",
                ["pricing_parameter_profile_id"],
            )


def downgrade() -> None:
    if "position_valuation_runs" in _tables() and "pricing_parameter_profile_id" in _columns("position_valuation_runs"):
        with op.batch_alter_table("position_valuation_runs") as batch:
            batch.drop_index("ix_position_valuation_runs_pricing_parameter_profile_id")
            batch.drop_constraint(
                "fk_position_valuation_runs_pricing_parameter_profile_id",
                type_="foreignkey",
            )
            batch.drop_column("pricing_parameter_profile_id")

    tables = _tables()
    if "pricing_parameter_rows" in tables:
        op.drop_index("ix_pricing_parameter_rows_symbol", table_name="pricing_parameter_rows")
        op.drop_index("ix_pricing_parameter_rows_source_trade_id", table_name="pricing_parameter_rows")
        op.drop_index("ix_pricing_parameter_rows_profile_id", table_name="pricing_parameter_rows")
        op.drop_table("pricing_parameter_rows")
    if "pricing_parameter_profiles" in tables:
        op.drop_index(
            "ix_pricing_parameter_profiles_valuation_date",
            table_name="pricing_parameter_profiles",
        )
        op.drop_table("pricing_parameter_profiles")
