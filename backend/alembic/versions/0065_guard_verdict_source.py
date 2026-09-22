"""agent_tool_guard_verdicts.source / state_fidelity / audit_id — the retrospective sweep

Revision ID: 0065_guard_verdict_source
Revises: 0064_limit_incident_reviews

Spec 2026-09-22-guard-sweep D3: sweep verdicts share the live table and its
UNIQUE (thread_id, tool_call_id) key, told apart by `source`. Every existing row
is live, so the server default backfills it. `audit_id` names the execution row
a sweep verdict describes — deliberately NO FK (audit rows are append-only).

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh chain already has
the columns and indexes. Downgrade drops via batch_alter_table and restores the
PARTIAL unique key by hand (its WHERE clause is D10's structural-row exemption
and must not depend on reflection). HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0065_guard_verdict_source"
down_revision = "0064_limit_incident_reviews"
branch_labels = None
depends_on = None

_TABLE = "agent_tool_guard_verdicts"
_UX_CALL = "ux_agent_tool_guard_verdicts_call"
_IX_AUDIT = "ix_agent_tool_guard_verdicts_audit_id"
_IX_SOURCE = "ix_agent_tool_guard_verdicts_source_created"
_EMPTY_ID_EXEMPT = "tool_call_id != ''"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        return
    columns = _columns(_TABLE)
    if "source" not in columns:
        op.add_column(_TABLE, sa.Column(
            "source", sa.String(10), nullable=False, server_default=sa.text("'live'")))
    if "state_fidelity" not in columns:
        op.add_column(_TABLE, sa.Column("state_fidelity", sa.String(12), nullable=True))
    if "audit_id" not in columns:
        op.add_column(_TABLE, sa.Column("audit_id", sa.Integer(), nullable=True))
    existing = _indexes(_TABLE)
    if _IX_AUDIT not in existing:
        op.create_index(_IX_AUDIT, _TABLE, ["audit_id"])
    if _IX_SOURCE not in existing:
        op.create_index(_IX_SOURCE, _TABLE, ["source", "created_at"])


def downgrade() -> None:
    if _TABLE not in _tables():
        return
    existing = _indexes(_TABLE)
    for name in (_IX_SOURCE, _IX_AUDIT):
        if name in existing:
            op.drop_index(name, table_name=_TABLE)
    drop = [c for c in ("audit_id", "state_fidelity", "source") if c in _columns(_TABLE)]
    if not drop:
        return
    if _UX_CALL in existing:
        op.drop_index(_UX_CALL, table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        for column in drop:
            batch.drop_column(column)
    op.create_index(
        _UX_CALL, _TABLE, ["thread_id", "tool_call_id"], unique=True,
        sqlite_where=sa.text(_EMPTY_ID_EXEMPT),
        postgresql_where=sa.text(_EMPTY_ID_EXEMPT),
    )
