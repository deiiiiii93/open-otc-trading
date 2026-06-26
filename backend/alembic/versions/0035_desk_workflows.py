"""desk workflows table + seed flagship

Revision ID: 0035_desk_workflows
Revises: 0034_arena_match_score_breakdown
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

# Seed values are inlined (not imported from app code) so this historical
# revision is immutable and self-contained. The runtime boot-seed in
# database.py shares constants via app.desk_workflow_seed.
FLAGSHIP_SLUG = "risk-manager-control-day"
FLAGSHIP_TITLE = "Risk Manager Control Day"
FLAGSHIP_PERSONA = "risk_manager"
FLAGSHIP_SCOPE = "shared"
FLAGSHIP_MODE = "yolo"
FLAGSHIP_DESCRIPTION = (
    "Full desk-control loop: stale-check, refresh, hotspot, Greeks landscape, "
    "stress test, backtest, governance report."
)
FLAGSHIP_SCRIPT = '''meta = {
    "name": "risk-manager-control-day",
    "title": "Risk Manager Control Day",
    "persona": "risk_manager",
    "mode": "yolo",
    "scope": "shared",
    "description": "Full desk-control loop: stale-check, refresh, hotspot, Greeks landscape, stress test, backtest, governance report.",
}

await step("What does the latest risk say for the control portfolio?")
await step("Run a fresh risk calculation for the control portfolio using the Control Profile.")
await step("Now check the updated risk result — what's the hotspot?")
await step("Run a Greeks landscape across spot shifts for the control portfolio.")
await step("Stress-test the control portfolio using the market-crash scenario set with the Control Profile.")
await step("Run a historical backtest of the delta-hedge strategy from 2026-03-24 to 2026-06-24.")
await step("Generate a governance risk report for today's control session.")
'''

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
