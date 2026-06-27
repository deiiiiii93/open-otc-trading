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
