"""async_agent columns on task_runs

Revision ID: 0014_async_agent_columns
Revises: 0013_underlying_pricing_defaults
Create Date: 2026-05-16
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_async_agent_columns"
down_revision = "0013_underlying_pricing_defaults"
branch_labels = None
depends_on = None


NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "pk": "pk_%(table_name)s",
}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "task_runs" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("task_runs")}
    missing = {
        "parent_thread_id",
        "description",
        "result_payload",
        "cancel_requested",
    } - columns
    if not missing:
        indexes = {index["name"] for index in inspector.get_indexes("task_runs")}
        if "ix_task_runs_parent_thread_id" not in indexes:
            op.create_index(
                "ix_task_runs_parent_thread_id",
                "task_runs",
                ["parent_thread_id"],
            )
        return

    # NAMING_CONVENTION + the explicit FK name are required for SQLite's
    # batch_alter_table to reflect task_runs' pre-existing unnamed FKs
    # (portfolios, risk_runs, report_jobs). Without them, alembic raises
    # "Constraint must have a name" while re-emitting the reflected FKs.
    with op.batch_alter_table(
        "task_runs", naming_convention=NAMING_CONVENTION
    ) as batch:
        if "parent_thread_id" in missing:
            batch.add_column(
                sa.Column(
                    "parent_thread_id",
                    sa.Integer(),
                    sa.ForeignKey(
                        "agent_threads.id",
                        name="fk_task_runs_parent_thread_id_agent_threads",
                        ondelete="SET NULL",
                    ),
                    nullable=True,
                )
            )
        if "description" in missing:
            batch.add_column(
                sa.Column("description", sa.String(length=120), nullable=True)
            )
        if "result_payload" in missing:
            batch.add_column(sa.Column("result_payload", sa.JSON(), nullable=True))
        if "cancel_requested" in missing:
            batch.add_column(
                sa.Column(
                    "cancel_requested",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
    indexes = {index["name"] for index in sa.inspect(bind).get_indexes("task_runs")}
    if "ix_task_runs_parent_thread_id" not in indexes:
        op.create_index(
            "ix_task_runs_parent_thread_id",
            "task_runs",
            ["parent_thread_id"],
        )


def downgrade() -> None:
    op.drop_index("ix_task_runs_parent_thread_id", table_name="task_runs")
    with op.batch_alter_table(
        "task_runs", naming_convention=NAMING_CONVENTION
    ) as batch:
        batch.drop_column("cancel_requested")
        batch.drop_column("result_payload")
        batch.drop_column("description")
        batch.drop_column("parent_thread_id")
