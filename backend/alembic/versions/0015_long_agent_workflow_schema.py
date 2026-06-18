"""long agent workflow schema

Revision ID: 0015_long_agent_workflow_schema
Revises: 0014_async_agent_columns
Create Date: 2026-05-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_long_agent_workflow_schema"
down_revision = "0014_async_agent_columns"
branch_labels = None
depends_on = None


NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}


def _tables() -> set[str]:
    bind = op.get_bind()
    return set(sa.inspect(bind).get_table_names())


def _columns(table_name: str) -> set[str]:
    bind = op.get_bind()
    return {column["name"] for column in sa.inspect(bind).get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if table_name not in _tables() or column.name in _columns(table_name):
        return
    with op.batch_alter_table(
        table_name,
        naming_convention=NAMING_CONVENTION,
    ) as batch:
        batch.add_column(column)


def upgrade() -> None:
    tables = _tables()
    if "workflows" not in tables:
        op.create_table(
            "workflows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("thread_id", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("intent", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("opened_by", sa.String(length=40), nullable=False),
            sa.Column("opened_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("canonical_snapshot_ids", sa.JSON(), nullable=False),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["thread_id"],
                ["agent_threads.id"],
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_workflows_thread_id_status",
            "workflows",
            ["thread_id", "status"],
        )

    if "context_pack_payloads" not in tables:
        op.create_table(
            "context_pack_payloads",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("content_hash", sa.String(length=80), nullable=False),
            sa.Column("stable_payload", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("content_hash"),
        )

    if "agent_sessions" not in tables:
        op.create_table(
            "agent_sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("persona", sa.String(length=40), nullable=False),
            sa.Column("episode_id", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("checkpointer_key", sa.String(length=160), nullable=False),
            sa.Column("current_task_id", sa.Integer(), nullable=True),
            sa.Column("lease_acquired_at", sa.DateTime(), nullable=True),
            sa.Column("opened_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.Column("closed_reason", sa.String(length=40), nullable=True),
            sa.Column("last_summary", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["workflows.id"],
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint("checkpointer_key"),
            sa.UniqueConstraint(
                "workflow_id",
                "persona",
                "episode_id",
                name="uq_agent_sessions_workflow_persona_episode",
            ),
        )
        op.create_index(
            "ix_agent_sessions_workflow_persona_status",
            "agent_sessions",
            ["workflow_id", "persona", "status"],
        )
        op.create_index(
            "uq_agent_sessions_active_workflow_persona",
            "agent_sessions",
            ["workflow_id", "persona"],
            unique=True,
            sqlite_where=sa.text("status = 'active'"),
            postgresql_where=sa.text("status = 'active'"),
        )

    if "agent_tasks" not in tables:
        op.create_table(
            "agent_tasks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("task_type", sa.String(length=80), nullable=False),
            sa.Column("inputs", sa.JSON(), nullable=False),
            sa.Column("depends_on", sa.JSON(), nullable=False),
            sa.Column("assigned_persona", sa.String(length=40), nullable=False),
            sa.Column("assigned_session_id", sa.Integer(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("context_pack_id", sa.Integer(), nullable=True),
            sa.Column("output_artifact_id", sa.Integer(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("opened_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("closed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["workflows.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["assigned_session_id"], ["agent_sessions.id"]),
        )
        op.create_index(
            "ix_agent_tasks_workflow_status",
            "agent_tasks",
            ["workflow_id", "status"],
        )
        op.create_index(
            "ix_agent_tasks_assigned_session_id",
            "agent_tasks",
            ["assigned_session_id"],
        )
        op.create_index(
            "ix_agent_tasks_context_pack_id",
            "agent_tasks",
            ["context_pack_id"],
        )

    if "context_packs" not in tables:
        op.create_table(
            "context_packs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("payload_id", sa.Integer(), nullable=False),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["workflows.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
            sa.ForeignKeyConstraint(["payload_id"], ["context_pack_payloads.id"]),
        )
        op.create_index(
            "ix_context_packs_workflow_created_at",
            "context_packs",
            ["workflow_id", "created_at"],
        )
        op.create_index(
            "ix_context_packs_payload_id",
            "context_packs",
            ["payload_id"],
        )
        op.create_index(
            "ix_context_packs_task_id",
            "context_packs",
            ["task_id"],
        )

    if "session_artifacts" not in tables:
        op.create_table(
            "session_artifacts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=True),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column(
                "schema_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("rendered_path", sa.String(length=400), nullable=True),
            sa.Column("tool_call_id", sa.String(length=80), nullable=True),
            sa.Column("tool_name", sa.String(length=80), nullable=True),
            sa.Column("context_pack_id", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column(
                "pinned",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("superseded_by", sa.Integer(), nullable=True),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["workflows.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
            sa.ForeignKeyConstraint(["context_pack_id"], ["context_packs.id"]),
            sa.ForeignKeyConstraint(["superseded_by"], ["session_artifacts.id"]),
        )
        op.create_index(
            "ix_session_artifacts_workflow_kind_created_at",
            "session_artifacts",
            ["workflow_id", "kind", "created_at"],
        )

    if "artifact_evidence_refs" not in tables:
        op.create_table(
            "artifact_evidence_refs",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("artifact_id", sa.Integer(), nullable=False),
            sa.Column("evidence_kind", sa.String(length=40), nullable=False),
            sa.Column("evidence_payload", sa.JSON(), nullable=False),
            sa.Column("bound_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["artifact_id"],
                ["session_artifacts.id"],
                ondelete="CASCADE",
            ),
        )
        op.create_index(
            "ix_artifact_evidence_refs_artifact_id",
            "artifact_evidence_refs",
            ["artifact_id"],
        )
        op.create_index(
            "ix_artifact_evidence_refs_evidence_kind",
            "artifact_evidence_refs",
            ["evidence_kind"],
        )

    if "domain_events" not in tables:
        op.create_table(
            "domain_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("workflow_id", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=True),
            sa.Column("task_id", sa.Integer(), nullable=True),
            sa.Column("artifact_id", sa.Integer(), nullable=True),
            sa.Column("kind", sa.String(length=40), nullable=False),
            sa.Column(
                "schema_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("actor", sa.String(length=40), nullable=False),
            sa.Column("occurred_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["workflow_id"],
                ["workflows.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["session_id"], ["agent_sessions.id"]),
            sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.id"]),
            sa.ForeignKeyConstraint(["artifact_id"], ["session_artifacts.id"]),
        )
        op.create_index(
            "ix_domain_events_workflow_occurred_at",
            "domain_events",
            ["workflow_id", "occurred_at"],
        )
        op.create_index(
            "ix_domain_events_kind_occurred_at",
            "domain_events",
            ["kind", "occurred_at"],
        )

    # Optional cycle-closing FKs are added on dialects that support ALTER
    # constraints. SQLite keeps the nullable columns and relies on app-level
    # validation until a batch rebuild migration is warranted.
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_agent_sessions_current_task_id_agent_tasks",
            "agent_sessions",
            "agent_tasks",
            ["current_task_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_agent_tasks_context_pack_id_context_packs",
            "agent_tasks",
            "context_packs",
            ["context_pack_id"],
            ["id"],
        )
        op.create_foreign_key(
            "fk_agent_tasks_output_artifact_id_session_artifacts",
            "agent_tasks",
            "session_artifacts",
            ["output_artifact_id"],
            ["id"],
        )

    _add_column_if_missing(
        "agent_threads",
        sa.Column("active_workflow_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_agent_threads_active_workflow_id",
        "agent_threads",
        ["active_workflow_id"],
        if_not_exists=True,
    )
    _add_column_if_missing(
        "agent_messages",
        sa.Column("workflow_id", sa.Integer(), nullable=True),
    )
    _add_column_if_missing(
        "agent_messages",
        sa.Column("session_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_agent_messages_workflow_id",
        "agent_messages",
        ["workflow_id"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_agent_messages_session_id",
        "agent_messages",
        ["session_id"],
        if_not_exists=True,
    )
    _add_column_if_missing(
        "positions",
        sa.Column("kwargs_migrated_at", sa.DateTime(), nullable=True),
    )
    _add_column_if_missing(
        "positions",
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )


def downgrade() -> None:
    for index_name, table_name in (
        ("ix_agent_messages_session_id", "agent_messages"),
        ("ix_agent_messages_workflow_id", "agent_messages"),
        ("ix_agent_threads_active_workflow_id", "agent_threads"),
    ):
        try:
            op.drop_index(index_name, table_name=table_name)
        except Exception:
            pass

    for table_name, column_name in (
        ("positions", "version"),
        ("positions", "kwargs_migrated_at"),
        ("agent_messages", "session_id"),
        ("agent_messages", "workflow_id"),
        ("agent_threads", "active_workflow_id"),
    ):
        if table_name in _tables() and column_name in _columns(table_name):
            with op.batch_alter_table(
                table_name,
                naming_convention=NAMING_CONVENTION,
            ) as batch:
                batch.drop_column(column_name)

    for table_name in (
        "artifact_evidence_refs",
        "domain_events",
        "session_artifacts",
        "context_packs",
        "agent_tasks",
        "agent_sessions",
        "context_pack_payloads",
        "workflows",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
