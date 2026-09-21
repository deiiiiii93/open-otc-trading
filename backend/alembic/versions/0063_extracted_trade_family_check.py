"""extracted_trades.family_check — System One's cross-check of the LLM's family

Revision ID: 0063_extracted_trade_family_check
Revises: 0062_memory_keep_alive

`segment_document` picks each trade's product family with one vision LLM, and
that choice selects the schema stage 2 fills. This column records System One's
independent read of the same segment (spec 2026-09-21 §3). NULL = never checked.
It is a flag for the reviewer and never a gate.

IDEMPOTENT (0001 materialises today's ORM). Downgrade drops via
batch_alter_table (the table carries FKs). Migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0063_extracted_trade_family_check"
down_revision = "0062_memory_keep_alive"
branch_labels = None
depends_on = None

_TABLE = "extracted_trades"
_COLUMN = "family_check"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _TABLE in _tables() and _COLUMN not in _columns(_TABLE):
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.JSON(), nullable=True))


def downgrade() -> None:
    if _TABLE in _tables() and _COLUMN in _columns(_TABLE):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
