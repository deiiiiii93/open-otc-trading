"""agent_action_audits — dangerous-action audit trail (audit spec §4)."""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0042_agent_action_audits"
down_revision = "0041_morning_breach_assemble_prompt"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if _has_table("agent_action_audits"):
        return
    op.create_table(
        "agent_action_audits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "kind", sa.String(length=20), nullable=False, server_default="execution"
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("deny_reason", sa.String(length=30), nullable=True),
        sa.Column("tool_name", sa.String(length=120), nullable=False),
        sa.Column("tool_class", sa.String(length=30), nullable=False),
        sa.Column("tool_call_id", sa.String(length=120), nullable=True),
        sa.Column("audit_ref", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=True),
        sa.Column("envelope", sa.String(length=40), nullable=True),
        sa.Column(
            "actor", sa.String(length=80), nullable=False, server_default="agent"
        ),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("persona", sa.String(length=40), nullable=True),
        sa.Column(
            "thread_id", sa.Integer(), sa.ForeignKey("agent_threads.id"), nullable=True
        ),
        sa.Column(
            "workflow_id", sa.Integer(), sa.ForeignKey("workflows.id"), nullable=True
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("agent_sessions.id"),
            nullable=True,
        ),
        sa.Column(
            "task_id", sa.Integer(), sa.ForeignKey("agent_tasks.id"), nullable=True
        ),
        sa.Column("message_id", sa.Integer(), nullable=True),
        sa.Column("desk_workflow_slug", sa.String(length=120), nullable=True),
        sa.Column("args_json", sa.JSON(), nullable=False),
        sa.Column("redacted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("result_preview", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    for name, cols in [
        ("ix_agent_action_audits_kind", ["kind"]),
        ("ix_agent_action_audits_status", ["status"]),
        ("ix_agent_action_audits_tool_name", ["tool_name"]),
        ("ix_agent_action_audits_tool_class", ["tool_class"]),
        ("ix_agent_action_audits_tool_call_id", ["tool_call_id"]),
        ("ix_agent_action_audits_audit_ref", ["audit_ref"]),
        ("ix_agent_action_audits_thread_id", ["thread_id"]),
        ("ix_agent_action_audits_occurred_at", ["occurred_at"]),
        ("ix_agent_action_audits_tool_occurred", ["tool_name", "occurred_at"]),
        ("ix_agent_action_audits_thread_occurred", ["thread_id", "occurred_at"]),
    ]:
        op.create_index(name, "agent_action_audits", cols)


def downgrade() -> None:
    if _has_table("agent_action_audits"):
        op.drop_table("agent_action_audits")
