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
}

await step("What does the latest risk say for the control portfolio?")
await step("Run a fresh risk calculation for the control portfolio using the Control Profile.")
await step("Now check the updated risk result — what's the hotspot?")
await step("Run a Greeks landscape across spot shifts for the control portfolio.")
await step("Stress-test the control portfolio using the market-crash scenario set with the Control Profile.")
await step("Run a historical backtest of the delta-hedge strategy from 2026-03-24 to 2026-06-24.")
await step("Generate a governance risk report for today's control session.")
'''
