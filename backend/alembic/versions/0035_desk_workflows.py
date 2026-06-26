"""desk workflows table + seed flagship

Revision ID: 0035_desk_workflows
Revises: 0034_arena_match_score_breakdown
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from app.desk_workflow_seed import (
    FLAGSHIP_DESCRIPTION,
    FLAGSHIP_MODE,
    FLAGSHIP_PERSONA,
    FLAGSHIP_SCOPE,
    FLAGSHIP_SCRIPT,
    FLAGSHIP_SLUG,
    FLAGSHIP_TITLE,
)

revision = "0035_desk_workflows"
down_revision = "0034_arena_match_score_breakdown"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("desk_workflows"):
        op.create_table(
            "desk_workflows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("slug", sa.String(length=80), nullable=False, unique=True),
            sa.Column("title", sa.String(length=160), nullable=False),
            sa.Column("persona", sa.String(length=40), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("scope", sa.String(length=16), nullable=False, server_default="local"),
            sa.Column("default_mode", sa.String(length=16), nullable=False, server_default="auto"),
            sa.Column("script", sa.Text(), nullable=False),
            sa.Column("source", sa.String(length=16), nullable=False, server_default="user"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        )
        op.create_index("ix_desk_workflows_slug", "desk_workflows", ["slug"], unique=True)

    op.get_bind().execute(
        text(
            "INSERT INTO desk_workflows "
            "(slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at) "
            "SELECT :slug, :title, :persona, :description, :scope, :default_mode, :script, 'seed', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM desk_workflows WHERE slug = :slug)"
        ),
        {
            "slug": FLAGSHIP_SLUG,
            "title": FLAGSHIP_TITLE,
            "persona": FLAGSHIP_PERSONA,
            "description": FLAGSHIP_DESCRIPTION,
            "scope": FLAGSHIP_SCOPE,
            "default_mode": FLAGSHIP_MODE,
            "script": FLAGSHIP_SCRIPT,
        },
    )


def downgrade() -> None:
    if _has_table("desk_workflows"):
        op.drop_table("desk_workflows")
