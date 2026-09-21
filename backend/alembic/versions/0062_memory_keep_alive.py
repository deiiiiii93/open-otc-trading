"""memory_entries keep-alive columns — System One's display-only score

Revision ID: 0062_memory_keep_alive
Revises: 0061_tool_guard_verdicts

Five nullable columns (spec 2026-09-21 §2). NULL means never scored, which is not
0 (D14); keep_alive_unscored_reason makes a broken setup visible (D17).

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh database already has
them. Downgrade drops through batch_alter_table, never a direct drop_column.
HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0062_memory_keep_alive"
down_revision = "0061_tool_guard_verdicts"
branch_labels = None
depends_on = None

_TABLE = "memory_entries"
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("keep_alive_score", sa.Float()),
    ("keep_alive_confidence", sa.Float()),
    ("keep_alive_scored_at", sa.DateTime()),
    ("keep_alive_attempted_at", sa.DateTime()),
    ("keep_alive_unscored_reason", sa.String(40)),
)


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        return
    existing = _columns(_TABLE)
    for name, type_ in _COLUMNS:
        if name not in existing:
            op.add_column(_TABLE, sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    if _TABLE not in _tables():
        return
    present = [name for name, _type in _COLUMNS if name in _columns(_TABLE)]
    if present:
        with op.batch_alter_table(_TABLE) as batch:
            for name in present:
                batch.drop_column(name)
