"""rfq quote versions

Revision ID: 0012_rfq_quote_versions
Revises: 0011_position_lifecycle_events
Create Date: 2026-05-12
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0012_rfq_quote_versions"
down_revision = "0011_position_lifecycle_events"
branch_labels = None
depends_on = None


def _columns(table_name: str) -> set[str]:
    return {col["name"] for col in inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "rfq_quote_versions" not in tables:
        op.create_table(
            "rfq_quote_versions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("rfq_id", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("quote_mode", sa.String(length=20), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("request_payload", sa.JSON(), nullable=False),
            sa.Column("quote_payload", sa.JSON(), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=120), nullable=False),
            sa.Column("approved_by", sa.String(length=120), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("released_at", sa.DateTime(), nullable=True),
            sa.Column("valid_until", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["rfq_id"], ["rfqs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_rfq_quote_versions_rfq_id", "rfq_quote_versions", ["rfq_id"])
    _backfill_quote_versions()
    if "positions" in tables:
        with op.batch_alter_table("positions") as batch_op:
            columns = _columns("positions")
            indexes = _indexes("positions")
            if "rfq_id" not in columns:
                batch_op.add_column(sa.Column("rfq_id", sa.Integer(), sa.ForeignKey("rfqs.id"), nullable=True))
            if "rfq_quote_version_id" not in columns:
                batch_op.add_column(sa.Column("rfq_quote_version_id", sa.Integer(), sa.ForeignKey("rfq_quote_versions.id"), nullable=True))
            if "ix_positions_rfq_id" not in indexes:
                batch_op.create_index("ix_positions_rfq_id", ["rfq_id"])
            if "ix_positions_rfq_quote_version_id" not in indexes:
                batch_op.create_index("ix_positions_rfq_quote_version_id", ["rfq_quote_version_id"])


def _backfill_quote_versions() -> None:
    connection = op.get_bind()
    tables = set(inspect(connection).get_table_names())
    if "rfqs" not in tables or "rfq_quote_versions" not in tables:
        return
    rows = connection.execute(
        sa.text(
            "SELECT id, status, request_payload, quote_payload, created_at "
            "FROM rfqs WHERE NOT EXISTS ("
            "SELECT 1 FROM rfq_quote_versions WHERE rfq_quote_versions.rfq_id = rfqs.id)"
        )
    ).mappings()
    for row in rows:
        quote_payload = _json_payload(row["quote_payload"])
        status = str(row["status"] or "pending_approval")
        if (
            isinstance(quote_payload, dict)
            and quote_payload.get("quantark_ok") is False
            and status not in {"approved", "rejected", "released", "client_accepted", "booked"}
        ):
            status = "pricing_failed"
        error = (
            str(quote_payload.get("quantark_error"))
            if isinstance(quote_payload, dict) and quote_payload.get("quantark_error")
            else None
        )
        connection.execute(
            sa.text(
                "INSERT INTO rfq_quote_versions "
                "(rfq_id, version, quote_mode, status, request_payload, quote_payload, "
                "error, created_by, created_at) "
                "VALUES (:rfq_id, 1, 'solve', :status, :request_payload, "
                ":quote_payload, :error, 'legacy', :created_at)"
            ),
            {
                "rfq_id": row["id"],
                "status": status,
                "request_payload": _json_text(row["request_payload"]),
                "quote_payload": _json_text(row["quote_payload"]),
                "error": error,
                "created_at": row["created_at"],
            },
        )


def _json_payload(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _json_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or {})


def downgrade() -> None:
    tables = set(inspect(op.get_bind()).get_table_names())
    if "positions" in tables:
        with op.batch_alter_table("positions") as batch_op:
            indexes = _indexes("positions")
            columns = _columns("positions")
            if "ix_positions_rfq_quote_version_id" in indexes:
                batch_op.drop_index("ix_positions_rfq_quote_version_id")
            if "ix_positions_rfq_id" in indexes:
                batch_op.drop_index("ix_positions_rfq_id")
            if "rfq_quote_version_id" in columns:
                batch_op.drop_column("rfq_quote_version_id")
            if "rfq_id" in columns:
                batch_op.drop_column("rfq_id")
    if "rfq_quote_versions" in tables:
        op.drop_index("ix_rfq_quote_versions_rfq_id", table_name="rfq_quote_versions")
        op.drop_table("rfq_quote_versions")
