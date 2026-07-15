"""Harvest canonical grounding truth values from a workflow's frozen seed.

Values are READ from real producer payloads (never invented), so Spec B's
``response_quotes_value`` / ``answer_field_quotes`` targets stay reproducible.
Paths are underlying/shift-keyed (NOT position_id — ids are not stable in a
clean DB). Re-run after any QuantArk numeric change instead of hand-editing a
truth file:

    python -m app.golden_workflows.harvest_fixtures                      # flagship
    python -m app.golden_workflows.harvest_fixtures trader-rfq-booking-day
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app import database
from app.config import Settings
from app.golden_workflows.assertions import _dig
from app.golden_workflows.determinism import (
    FLAGSHIP_ID, TRADER_RFQ_ID, seed_workflow, drive_producers,
)

_DEFN = Path(__file__).parent / "definitions"

# (name, producer_key, dig-path). Underlying/shift-keyed so they resolve in a
# clean DB. ``TARGETS`` stays the flagship list (a module global so the existing
# gate test can monkeypatch it); ``HARVEST_SPECS`` generalizes to any workflow.
TARGETS: list[tuple[str, str, str]] = [
    ("aapl_hotspot_delta", "risk", "positions[underlying=AAPL].delta"),
    ("portfolio_gamma_at_+10pct", "landscape",
     "portfolio.raw[spot_shift_pct=10.0].gamma"),
    ("portfolio_delta_at_-20pct", "landscape",
     "portfolio.raw[spot_shift_pct=-20.0].delta"),
    ("scenario_cvar", "scenario", "var_cvar.cvar"),
    ("backtest_total_pnl", "backtest", "portfolio.total_pnl"),
]

# workflow_id -> (truth_filename, targets)
HARVEST_SPECS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    FLAGSHIP_ID: ("risk-manager-control-day.truth.json", TARGETS),
    # Spot/multiplier-INVARIANT ratios (spec 2026-07-15) — the manifest grounds on
    # these so grounding survives the live arena's real market fetch.
    TRADER_RFQ_ID: ("trader-rfq-booking-day.truth.json", [
        ("premium_spot_ratio", "quote", "premium_spot_ratio"),
        ("barrier_strike_ratio", "quote", "barrier_strike_ratio"),
        ("strike_spot_ratio", "quote", "strike_spot_ratio"),
    ]),
}

TRUTH_PATH = _DEFN / HARVEST_SPECS[FLAGSHIP_ID][0]


def _harvest_targets(session, targets, workflow_id: str) -> dict[str, dict]:
    """Drive the frozen seed and dig each target from the REAL payloads.

    Raises if a target path does not resolve (a target that cannot be grounded is
    a bug, not an empty value) or is non-numeric. Pure: does not write a file."""
    payloads = drive_producers(session, seed_workflow(session, workflow_id),
                               workflow_id=workflow_id)
    truth: dict[str, dict] = {}
    for name, producer, path in targets:
        ok, val = _dig(payloads[producer], path)
        if not ok:
            raise RuntimeError(
                f"harvest target {name!r}: path {path!r} did not resolve in "
                f"{producer!r} payload")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise RuntimeError(
                f"harvest target {name!r}: value is not numeric: {val!r}")
        truth[name] = {"producer": producer, "path": path, "value": float(val)}
    return truth


def harvest(session) -> dict[str, dict]:
    """Flagship harvest (back-compat). Reads the module-level ``TARGETS`` so the
    determinism-gate test can monkeypatch it."""
    return _harvest_targets(session, TARGETS, FLAGSHIP_ID)


def harvest_for(session, workflow_id: str) -> dict[str, dict]:
    """Harvest any registered workflow's grounding targets."""
    _, targets = HARVEST_SPECS[workflow_id]
    return _harvest_targets(session, targets, workflow_id)


def write_truth_file(workflow_id: str = FLAGSHIP_ID) -> Path:
    """Build a clean throwaway DB, harvest, and write the committed truth file."""
    filename, _ = HARVEST_SPECS[workflow_id]
    d = Path(tempfile.mkdtemp())
    database.configure_database(Settings(
        database_url=f"sqlite+pysqlite:///{d / 'harvest.sqlite3'}",
        artifact_dir=d / "art", agent_checkpoint_db_path=":memory:"))
    database.init_db()
    with database.SessionLocal() as s:
        truth = harvest_for(s, workflow_id)
    path = _DEFN / filename
    path.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return path


if __name__ == "__main__":
    import sys
    wid = sys.argv[1] if len(sys.argv) > 1 else FLAGSHIP_ID
    path = write_truth_file(wid)
    print(f"wrote {path}")
    print(path.read_text())
