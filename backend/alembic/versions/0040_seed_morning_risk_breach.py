"""seed morning-risk-breach-commentary dynamic-subagents pilot workflow

Revision ID: 0040_seed_morning_risk_breach
Revises: 0039_memory_extraction_runs
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

# Seed values inlined (not imported from app code) so this historical revision is
# immutable and self-contained. The runtime boot-seed in database.py shares the
# canonical constants via app.desk_workflow_seed and refreshes source='seed' rows.
revision = "0040_seed_morning_risk_breach"
down_revision = "0039_memory_extraction_runs"
branch_labels = None
depends_on = None

_SLUG = "morning-risk-breach-commentary"
_TITLE = "Morning Risk-Breach Commentary"
_PERSONA = "risk_manager"
_SCOPE = "shared"
_MODE = "yolo"
_DESCRIPTION = (
    "Scope today's limit breaches, fan out a read-only risk_manager to investigate "
    "each, and assemble a morning report."
)
_SCRIPT = '''meta = {
    "name": "morning-risk-breach-commentary",
    "title": "Morning Risk-Breach Commentary",
    "persona": "risk_manager",
    "mode": "yolo",
    "scope": "shared",
    "dynamic_subagents": True,
    "description": "Scope breaches, fan out a read-only risk_manager per breach, assemble a report.",
    "params": [
        {"name": "portfolio_id", "label": "Portfolio", "type": "portfolio"},
    ],
}

await step("Run batch risk pricing for portfolio " + args.portfolio_id +
           " and list EVERY position that breaches a risk limit today.")
await step("For EVERY breached position, use the code interpreter to fan out one "
           "read-only risk_manager subagent per breach and collect "
           "{position_id, severity, commentary} for each.")
await step("Call assemble_breach_report with portfolio_id=" + args.portfolio_id +
           " and the collected records to produce the morning report; surface any "
           "position marked 'failed' in a 'needs manual review' section.")
'''

_PARAMS = {
    "slug": _SLUG, "title": _TITLE, "persona": _PERSONA, "description": _DESCRIPTION,
    "scope": _SCOPE, "default_mode": _MODE, "script": _SCRIPT,
}


def upgrade() -> None:
    op.execute(
        text(
            "INSERT INTO desk_workflows "
            "(slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at) "
            "SELECT :slug, :title, :persona, :description, :scope, :default_mode, :script, 'seed', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "WHERE NOT EXISTS (SELECT 1 FROM desk_workflows WHERE slug = :slug)"
        ).bindparams(**_PARAMS)
    )


def downgrade() -> None:
    op.execute(
        text("DELETE FROM desk_workflows WHERE slug = :slug AND source = 'seed'").bindparams(
            slug=_SLUG
        )
    )
