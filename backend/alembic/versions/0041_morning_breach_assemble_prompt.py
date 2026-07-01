"""refresh morning-risk-breach-commentary script: funnel finalize to assemble_breach_report

Revision ID: 0041_morning_breach_assemble_prompt
Revises: 0040_seed_morning_risk_breach

Updates the seeded pilot script so step 2 keeps subagents read-only-and-return-records
and step 3 prescriptively funnels the finalize turn to the ``assemble_breach_report``
tool (the deterministic coverage reconciliation), rather than the model authoring a
report via ``write_report_artifact``. Only touches ``source='seed'`` rows whose script
still differs, so a user-authored slug collision is never clobbered (mirrors the boot
seed's self-healing UPDATE in database.py).

Seed values inlined (not imported) so this historical revision stays immutable.
"""
from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0041_morning_breach_assemble_prompt"
down_revision = "0040_seed_morning_risk_breach"
branch_labels = None
depends_on = None

_SLUG = "morning-risk-breach-commentary"

_NEW_SCRIPT = '''meta = {
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

# TOP-LEVEL await — the runner lifts this whole body into `async def __workflow__()`.
# Do NOT define your own async def here (it would be a no-op).
await step("Run batch risk pricing for portfolio " + args.portfolio_id +
           " and list EVERY position that breaches a risk limit today.")
await step("For EVERY breached position, use the code interpreter to fan out one "
           "read-only risk_manager subagent per breach. Each subagent must ONLY READ "
           "(existing risk run, greeks, position details) and RETURN a "
           "{position_id, severity, commentary} record — it must NOT run pricing, book, "
           "or write any artifact. Collect the record for every breach.")
await step("Finalize by calling the assemble_breach_report tool with portfolio_id=" +
           args.portfolio_id + " and the collected {position_id, severity, commentary} "
           "records. You MUST use assemble_breach_report to produce the morning report — "
           "do NOT use write_report_artifact, write_file, or run_python to build it "
           "yourself. assemble_breach_report is the ONLY authorized way to finalize: it "
           "reconciles your records against the authoritative breach list server-side and "
           "marks any uncovered breach 'failed'. Present its result, surfacing any 'failed' "
           "position in a 'needs manual review' section.")
'''

_OLD_SCRIPT = '''meta = {
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

_UPDATE = (
    "UPDATE desk_workflows SET script = :script, updated_at = CURRENT_TIMESTAMP "
    "WHERE slug = :slug AND source = 'seed' AND script != :script"
)


def upgrade() -> None:
    op.execute(text(_UPDATE).bindparams(slug=_SLUG, script=_NEW_SCRIPT))


def downgrade() -> None:
    op.execute(text(_UPDATE).bindparams(slug=_SLUG, script=_OLD_SCRIPT))
