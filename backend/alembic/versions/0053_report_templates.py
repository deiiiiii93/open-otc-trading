"""report templates table and report_jobs template columns

Revision ID: 0053_report_templates
Revises: 0052_trade_confirmations
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0053_report_templates"
down_revision = "0052_trade_confirmations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "report_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("persona", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False,
                  server_default="user"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_report_templates_slug", "report_templates", ["slug"], unique=True
    )

    with op.batch_alter_table("report_jobs") as batch:
        batch.add_column(sa.Column("template_slug", sa.String(length=80), nullable=True))
        batch.add_column(sa.Column("compare_to_run_id", sa.Integer(), nullable=True))
    op.create_index(
        "ix_report_jobs_template_slug", "report_jobs", ["template_slug"]
    )


def downgrade() -> None:
    op.drop_index("ix_report_jobs_template_slug", table_name="report_jobs")
    with op.batch_alter_table("report_jobs") as batch:
        batch.drop_column("compare_to_run_id")
        batch.drop_column("template_slug")
    op.drop_index("ix_report_templates_slug", table_name="report_templates")
    op.drop_table("report_templates")
