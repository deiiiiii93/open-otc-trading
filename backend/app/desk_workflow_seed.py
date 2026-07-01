"""Single source of truth for the seeded flagship desk workflow.

Imported by both the Alembic migration (0035) and the create_all boot-seed in
``database.py`` so the two paths never drift. Holds only constant strings — no
ORM models or services — so importing it from a migration is safe.
"""
from __future__ import annotations

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
    "params": [
        {"name": "portfolio", "label": "Portfolio", "type": "portfolio"},
        {"name": "start", "label": "Backtest start", "type": "date"},
        {"name": "end", "label": "Backtest end", "type": "date"},
    ],
}

await step(f"What does the latest risk say for the portfolio: {args.portfolio}?")
await step(f"Run a fresh risk calculation for portfolio {args.portfolio} using the Control Profile.")
await step("Now check the updated risk result — what's the hotspot?")
await step(f"Run a Greeks landscape across spot shifts for portfolio {args.portfolio}.")
await step(f"Stress-test portfolio {args.portfolio} using the market-crash scenario set with the Control Profile.")
await step(f"Run a historical backtest of the delta-hedge strategy from {args.start} to {args.end}.")
await step(f"Generate a governance risk report for today's control session on portfolio {args.portfolio}.")
'''


MORNING_COMMENTARY_SLUG = "morning-risk-breach-commentary"
MORNING_COMMENTARY_TITLE = "Morning Risk-Breach Commentary"
MORNING_COMMENTARY_PERSONA = "risk_manager"
MORNING_COMMENTARY_SCOPE = "shared"
MORNING_COMMENTARY_MODE = "yolo"
MORNING_COMMENTARY_DESCRIPTION = (
    "Scope today's limit breaches, fan out a read-only risk_manager to investigate "
    "each, and assemble a morning report."
)
MORNING_COMMENTARY_SCRIPT = '''meta = {
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


SEED_WORKFLOWS: list[dict] = [
    {
        "slug": FLAGSHIP_SLUG, "title": FLAGSHIP_TITLE, "persona": FLAGSHIP_PERSONA,
        "description": FLAGSHIP_DESCRIPTION, "scope": FLAGSHIP_SCOPE,
        "default_mode": FLAGSHIP_MODE, "script": FLAGSHIP_SCRIPT,
    },
    {
        "slug": MORNING_COMMENTARY_SLUG, "title": MORNING_COMMENTARY_TITLE,
        "persona": MORNING_COMMENTARY_PERSONA, "description": MORNING_COMMENTARY_DESCRIPTION,
        "scope": MORNING_COMMENTARY_SCOPE, "default_mode": MORNING_COMMENTARY_MODE,
        "script": MORNING_COMMENTARY_SCRIPT,
    },
]
