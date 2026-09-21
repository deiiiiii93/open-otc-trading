"""agent_tool_guard_verdicts — System One guard verdicts for AUTO tool calls

Revision ID: 0061_tool_guard_verdicts
Revises: 0060_task_run_arena_run_id

One row per guarded AUTO-mode tool call (spec 2026-09-21 §1). The partial
UNIQUE (thread_id, tool_call_id) WHERE tool_call_id != '' is what makes a
LangGraph resume deterministic: the verdict is committed before any interrupt
and read back on re-entry, never re-asked of a non-deterministic model (D10).
thread_id is NOT NULL DEFAULT 0 — SQL treats NULLs as distinct in a UNIQUE key.

IDEMPOTENT: 0001_initial materialises today's ORM, so a fresh database already
has this table and its indexes. HOUSE RULE: migration-local Core only.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0061_tool_guard_verdicts"
down_revision = "0060_task_run_arena_run_id"
branch_labels = None
depends_on = None

_TABLE = "agent_tool_guard_verdicts"
_UX_CALL = "ux_agent_tool_guard_verdicts_call"
_IX_TOOL = "ix_agent_tool_guard_verdicts_tool_name"
_IX_CREATED = "ix_agent_tool_guard_verdicts_created_at"
_EMPTY_ID_EXEMPT = "tool_call_id != ''"


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if _TABLE not in _tables():
        op.create_table(
            _TABLE,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("thread_id", sa.Integer(), nullable=False, server_default=sa.text("0")),
            sa.Column("tool_call_id", sa.String(120), nullable=False, server_default=sa.text("''")),
            sa.Column("persona", sa.String(40), nullable=True),
            sa.Column("exec_mode", sa.String(20), nullable=True),
            sa.Column("guard_mode", sa.String(10), nullable=False),
            sa.Column("tool_name", sa.String(120), nullable=False),
            sa.Column("args_json", sa.JSON(), nullable=False),
            sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("args_hash", sa.String(64), nullable=False),
            sa.Column("user_request_source", sa.String(20), nullable=True),
            sa.Column("verdict", sa.String(10), nullable=False),
            sa.Column("unscored_reason", sa.String(40), nullable=True),
            sa.Column("predicates_json", sa.JSON(), nullable=False),
            sa.Column("max_probability", sa.Float(), nullable=True),
            sa.Column("action", sa.String(12), nullable=False, server_default="recorded"),
            sa.Column("model", sa.String(160), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    existing = _indexes(_TABLE)
    if _UX_CALL not in existing:
        op.create_index(
            _UX_CALL, _TABLE, ["thread_id", "tool_call_id"], unique=True,
            sqlite_where=sa.text(_EMPTY_ID_EXEMPT),
            postgresql_where=sa.text(_EMPTY_ID_EXEMPT),
        )
    if _IX_TOOL not in existing:
        op.create_index(_IX_TOOL, _TABLE, ["tool_name"])
    if _IX_CREATED not in existing:
        op.create_index(_IX_CREATED, _TABLE, ["created_at"])


def downgrade() -> None:
    if _TABLE in _tables():
        op.drop_table(_TABLE)
