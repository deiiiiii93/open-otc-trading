"""position import and pricing audit schema

Revision ID: 0002_position_import_pricing
Revises: 0001_initial
Create Date: 2026-05-06
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0002_position_import_pricing"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table_name)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "positions" in tables:
        columns = _columns("positions")
        with op.batch_alter_table("positions") as batch_op:
            if "source_trade_id" not in columns:
                batch_op.add_column(sa.Column("source_trade_id", sa.String(length=160), nullable=True))
            if "source_row" not in columns:
                batch_op.add_column(sa.Column("source_row", sa.Integer(), nullable=True))
            if "mapping_status" not in columns:
                batch_op.add_column(
                    sa.Column("mapping_status", sa.String(length=40), nullable=False, server_default="manual")
                )
            if "mapping_error" not in columns:
                batch_op.add_column(sa.Column("mapping_error", sa.Text(), nullable=True))
            if "source_payload" not in columns:
                batch_op.add_column(sa.Column("source_payload", sa.JSON(), nullable=True))
        if "ix_positions_source_trade_id" not in {idx["name"] for idx in inspect(op.get_bind()).get_indexes("positions")}:
            op.create_index("ix_positions_source_trade_id", "positions", ["source_trade_id"])

    if "position_import_batches" not in tables:
        op.create_table(
            "position_import_batches",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=False),
            sa.Column("source_sheet", sa.String(length=120), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("imported_count", sa.Integer(), nullable=False),
            sa.Column("supported_count", sa.Integer(), nullable=False),
            sa.Column("unsupported_count", sa.Integer(), nullable=False),
            sa.Column("error_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_position_import_batches_portfolio_id", "position_import_batches", ["portfolio_id"])

    if "position_valuation_runs" not in tables:
        op.create_table(
            "position_valuation_runs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("market_source_path", sa.Text(), nullable=True),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("overrides", sa.JSON(), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_position_valuation_runs_portfolio_id", "position_valuation_runs", ["portfolio_id"])

    if "position_market_inputs" not in tables:
        op.create_table(
            "position_market_inputs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("position_id", sa.Integer(), nullable=True),
            sa.Column("source_trade_id", sa.String(length=160), nullable=False),
            sa.Column("symbol", sa.String(length=80), nullable=False),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("spot", sa.Float(), nullable=True),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("volatility", sa.Float(), nullable=True),
            sa.Column("source_row", sa.Integer(), nullable=True),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_position_market_inputs_portfolio_id", "position_market_inputs", ["portfolio_id"])
        op.create_index("ix_position_market_inputs_position_id", "position_market_inputs", ["position_id"])
        op.create_index("ix_position_market_inputs_source_trade_id", "position_market_inputs", ["source_trade_id"])
        op.create_index("ix_position_market_inputs_symbol", "position_market_inputs", ["symbol"])

    if "market_input_import_batches" not in tables:
        op.create_table(
            "market_input_import_batches",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("portfolio_id", sa.Integer(), nullable=False),
            sa.Column("source_path", sa.Text(), nullable=False),
            sa.Column("source_sheet", sa.String(length=120), nullable=False),
            sa.Column("row_count", sa.Integer(), nullable=False),
            sa.Column("imported_count", sa.Integer(), nullable=False),
            sa.Column("matched_position_count", sa.Integer(), nullable=False),
            sa.Column("unmatched_count", sa.Integer(), nullable=False),
            sa.Column("error_count", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_market_input_import_batches_portfolio_id", "market_input_import_batches", ["portfolio_id"])

    if "position_valuation_results" not in tables:
        op.create_table(
            "position_valuation_results",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("valuation_run_id", sa.Integer(), nullable=False),
            sa.Column("position_id", sa.Integer(), nullable=False),
            sa.Column("source_trade_id", sa.String(length=160), nullable=True),
            sa.Column("ok", sa.Boolean(), nullable=False),
            sa.Column("price", sa.Float(), nullable=True),
            sa.Column("market_value", sa.Float(), nullable=True),
            sa.Column("pnl", sa.Float(), nullable=True),
            sa.Column("market_inputs", sa.JSON(), nullable=False),
            sa.Column("result_payload", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
            sa.ForeignKeyConstraint(["valuation_run_id"], ["position_valuation_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_position_valuation_results_position_id", "position_valuation_results", ["position_id"])
        op.create_index(
            "ix_position_valuation_results_source_trade_id",
            "position_valuation_results",
            ["source_trade_id"],
        )
        op.create_index(
            "ix_position_valuation_results_valuation_run_id",
            "position_valuation_results",
            ["valuation_run_id"],
        )


def downgrade() -> None:
    tables = _tables()
    if "position_valuation_results" in tables:
        op.drop_index("ix_position_valuation_results_valuation_run_id", table_name="position_valuation_results")
        op.drop_index("ix_position_valuation_results_source_trade_id", table_name="position_valuation_results")
        op.drop_index("ix_position_valuation_results_position_id", table_name="position_valuation_results")
        op.drop_table("position_valuation_results")
    if "position_valuation_runs" in tables:
        op.drop_index("ix_position_valuation_runs_portfolio_id", table_name="position_valuation_runs")
        op.drop_table("position_valuation_runs")
    if "market_input_import_batches" in tables:
        op.drop_index("ix_market_input_import_batches_portfolio_id", table_name="market_input_import_batches")
        op.drop_table("market_input_import_batches")
    if "position_market_inputs" in tables:
        op.drop_index("ix_position_market_inputs_symbol", table_name="position_market_inputs")
        op.drop_index("ix_position_market_inputs_source_trade_id", table_name="position_market_inputs")
        op.drop_index("ix_position_market_inputs_position_id", table_name="position_market_inputs")
        op.drop_index("ix_position_market_inputs_portfolio_id", table_name="position_market_inputs")
        op.drop_table("position_market_inputs")
    if "position_import_batches" in tables:
        op.drop_index("ix_position_import_batches_portfolio_id", table_name="position_import_batches")
        op.drop_table("position_import_batches")
    if "positions" in tables:
        indexes = {idx["name"] for idx in inspect(op.get_bind()).get_indexes("positions")}
        if "ix_positions_source_trade_id" in indexes:
            op.drop_index("ix_positions_source_trade_id", table_name="positions")
        columns = _columns("positions")
        with op.batch_alter_table("positions") as batch_op:
            for column_name in ["source_payload", "mapping_error", "mapping_status", "source_row", "source_trade_id"]:
                if column_name in columns:
                    batch_op.drop_column(column_name)
