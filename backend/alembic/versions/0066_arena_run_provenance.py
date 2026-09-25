"""arena_run.provenance — which manifests and which app produced a run

Revision ID: 0066_arena_run_provenance
Revises: 0065_guard_verdict_source

A board is comparable only with a board that ran the same manifests on the same
harness, and nothing recorded either. Existing rows stay NULL: they are runs from
before stamping, whose versions are unknown — backfilling today's values would
claim a manifest they may not have run.

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh chain already has
the column. Downgrade drops via batch_alter_table. HOUSE RULE: migration-local
Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0066_arena_run_provenance"
down_revision = "0065_guard_verdict_source"
branch_labels = None
depends_on = None

_TABLE = "arena_run"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        return
    if "provenance" not in _columns(_TABLE):
        op.add_column(_TABLE, sa.Column("provenance", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _TABLE not in _tables() or "provenance" not in _columns(_TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("provenance")
