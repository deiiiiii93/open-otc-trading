"""position trade effective date

Revision ID: 0007_position_trade_effective_date
Revises: 0006_market_data_profiles
Create Date: 2026-05-11
"""
from __future__ import annotations

from datetime import datetime
import json
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0007_position_trade_effective_date"
down_revision = "0006_market_data_profiles"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "positions" not in _tables():
        return

    if "trade_effective_date" not in _columns("positions"):
        op.add_column("positions", sa.Column("trade_effective_date", sa.DateTime(), nullable=True))

    if "ix_positions_trade_effective_date" not in _indexes("positions"):
        op.create_index(
            "ix_positions_trade_effective_date",
            "positions",
            ["trade_effective_date"],
        )

    _backfill_trade_effective_date()


def downgrade() -> None:
    if "positions" not in _tables():
        return
    if "ix_positions_trade_effective_date" in _indexes("positions"):
        op.drop_index("ix_positions_trade_effective_date", table_name="positions")
    if "trade_effective_date" in _columns("positions"):
        with op.batch_alter_table("positions") as batch_op:
            batch_op.drop_column("trade_effective_date")


def _backfill_trade_effective_date() -> None:
    bind = op.get_bind()
    rows = bind.execute(sa.text("select id, source_payload from positions where trade_effective_date is null")).mappings()
    for row in rows:
        effective = _source_effective_date(row["source_payload"])
        if effective is None:
            continue
        bind.execute(
            sa.text("update positions set trade_effective_date = :value where id = :id"),
            {"id": row["id"], "value": effective},
        )


def _source_effective_date(source_payload: Any) -> datetime | None:
    payload = source_payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict):
        return None
    source_row = payload.get("row")
    if not isinstance(source_row, dict):
        return None
    return _parse_date(source_row.get("起始日"))


def _parse_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
