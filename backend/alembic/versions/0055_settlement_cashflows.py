"""settlement cashflows, transition log and notices

Revision ID: 0055_settlement_cashflows
Revises: 0054_seed_report_templates

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so on a fresh
database these tables already exist before the chain reaches here. Every
migration after 0001 must therefore guard its DDL on current schema state.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0055_settlement_cashflows"
down_revision = "0054_seed_report_templates"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def _create_index(name: str, table: str, columns: list[str]) -> None:
    if name not in _indexes(table):
        op.create_index(name, table, columns)


def _drop_index(name: str, table: str) -> None:
    if name in _indexes(table):
        op.drop_index(name, table_name=table)


def upgrade() -> None:
    tables = _tables()

    if "settlement_cashflows" not in tables:
        op.create_table(
            "settlement_cashflows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "lifecycle_event_id",
                sa.Integer(),
                sa.ForeignKey("position_lifecycle_events.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("leg_key", sa.String(length=40), nullable=False),
            sa.Column(
                "position_id",
                sa.Integer(),
                sa.ForeignKey("positions.id"),
                nullable=False,
            ),
            sa.Column("currency", sa.String(length=8), nullable=False,
                      server_default="CNY"),
            sa.Column("counterparty", sa.String(length=255), nullable=True),
            sa.Column("direction", sa.String(length=8), nullable=False),
            sa.Column("derived_amount", sa.Float(), nullable=True),
            sa.Column("derived_value_date", sa.Date(), nullable=True),
            sa.Column("derived_basis", sa.String(length=80), nullable=True),
            sa.Column("amount", sa.Float(), nullable=True),
            sa.Column("value_date", sa.Date(), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False,
                      server_default="needs_amount"),
            sa.Column("stale", sa.Boolean(), nullable=False, server_default="0"),
            sa.Column("stale_reason", sa.JSON(), nullable=True),
            sa.Column("last_checked_at", sa.DateTime(), nullable=True),
            sa.Column("block_reason", sa.Text(), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("row_version", sa.Integer(), nullable=False,
                      server_default="1"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint(
                "lifecycle_event_id", "leg_key",
                name="uq_settlement_cashflow_event_leg",
            ),
        )
    _create_index(
        "ix_settlement_cashflows_lifecycle_event_id",
        "settlement_cashflows",
        ["lifecycle_event_id"],
    )
    _create_index(
        "ix_settlement_cashflows_position_id",
        "settlement_cashflows",
        ["position_id"],
    )
    _create_index(
        "ix_settlement_cashflows_status", "settlement_cashflows", ["status"]
    )

    if "settlement_cashflow_events" not in tables:
        op.create_table(
            "settlement_cashflow_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "cashflow_id",
                sa.Integer(),
                sa.ForeignKey("settlement_cashflows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("action", sa.String(length=24), nullable=False),
            sa.Column("from_status", sa.String(length=20), nullable=True),
            sa.Column("to_status", sa.String(length=20), nullable=True),
            sa.Column("actor", sa.String(length=120), nullable=False,
                      server_default="desk_user"),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    _create_index(
        "ix_settlement_cashflow_events_cashflow_id",
        "settlement_cashflow_events",
        ["cashflow_id"],
    )

    if "settlement_notices" not in tables:
        op.create_table(
            "settlement_notices",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "cashflow_id",
                sa.Integer(),
                sa.ForeignKey("settlement_cashflows.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("artifact_path", sa.String(length=255), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("payload_snapshot", sa.JSON(), nullable=False,
                      server_default="{}"),
            sa.Column("status", sa.String(length=16), nullable=False,
                      server_default="generated"),
            sa.Column("rendered_at", sa.DateTime(), nullable=False),
            sa.Column("rendered_by", sa.String(length=120), nullable=False,
                      server_default="desk_user"),
            sa.UniqueConstraint(
                "cashflow_id", "version", name="uq_settlement_notice_version"
            ),
        )
    _create_index(
        "ix_settlement_notices_cashflow_id", "settlement_notices", ["cashflow_id"]
    )


def downgrade() -> None:
    tables = _tables()

    if "settlement_notices" in tables:
        _drop_index("ix_settlement_notices_cashflow_id", "settlement_notices")
        op.drop_table("settlement_notices")
    if "settlement_cashflow_events" in tables:
        _drop_index(
            "ix_settlement_cashflow_events_cashflow_id",
            "settlement_cashflow_events",
        )
        op.drop_table("settlement_cashflow_events")
    if "settlement_cashflows" in tables:
        _drop_index("ix_settlement_cashflows_status", "settlement_cashflows")
        _drop_index("ix_settlement_cashflows_position_id", "settlement_cashflows")
        _drop_index(
            "ix_settlement_cashflows_lifecycle_event_id", "settlement_cashflows"
        )
        op.drop_table("settlement_cashflows")
