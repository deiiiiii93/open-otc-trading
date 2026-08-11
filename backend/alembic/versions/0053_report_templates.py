"""report templates table and report_jobs template columns

Revision ID: 0053_report_templates
Revises: 0052_trade_confirmations

IDEMPOTENT: 0001_initial materialises the live ORM metadata, so on a fresh
database these objects already exist before the chain reaches here. Every
migration after 0001 must therefore guard its DDL on current schema state.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0053_report_templates"
down_revision = "0052_trade_confirmations"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {i["name"] for i in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "report_templates" not in _tables():
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
    if "ix_report_templates_slug" not in _indexes("report_templates"):
        op.create_index(
            "ix_report_templates_slug", "report_templates", ["slug"], unique=True
        )

    missing = {"template_slug", "compare_to_run_id"} - _columns("report_jobs")
    if missing:
        with op.batch_alter_table("report_jobs") as batch:
            if "template_slug" in missing:
                batch.add_column(
                    sa.Column("template_slug", sa.String(length=80), nullable=True)
                )
            if "compare_to_run_id" in missing:
                batch.add_column(
                    sa.Column("compare_to_run_id", sa.Integer(), nullable=True)
                )
    if "ix_report_jobs_template_slug" not in _indexes("report_jobs"):
        op.create_index(
            "ix_report_jobs_template_slug", "report_jobs", ["template_slug"]
        )


def downgrade() -> None:
    if "ix_report_jobs_template_slug" in _indexes("report_jobs"):
        op.drop_index("ix_report_jobs_template_slug", table_name="report_jobs")
    present = {"template_slug", "compare_to_run_id"} & _columns("report_jobs")
    if present:
        with op.batch_alter_table("report_jobs") as batch:
            if "compare_to_run_id" in present:
                batch.drop_column("compare_to_run_id")
            if "template_slug" in present:
                batch.drop_column("template_slug")
    if "report_templates" in _tables():
        if "ix_report_templates_slug" in _indexes("report_templates"):
            op.drop_index(
                "ix_report_templates_slug", table_name="report_templates"
            )
        op.drop_table("report_templates")
