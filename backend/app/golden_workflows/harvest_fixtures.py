"""Harvest canonical grounding truth values from the frozen flagship seed.

Values are READ from real producer payloads (never invented), so Spec B's
``response_quotes_value`` targets stay reproducible. Paths are underlying/shift-
keyed (NOT position_id — ids are not stable in a clean DB). Re-run after any
QuantArk numeric change instead of hand-editing the truth file:

    python -m app.golden_workflows.harvest_fixtures
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app import database
from app.config import Settings
from app.golden_workflows.assertions import _dig
from app.golden_workflows.determinism import seed_flagship, drive_producers

TRUTH_PATH = (
    Path(__file__).parent / "definitions" / "risk-manager-control-day.truth.json"
)

# (name, producer, dig-path). Underlying/shift-keyed so they resolve in a clean DB.
TARGETS: list[tuple[str, str, str]] = [
    ("aapl_hotspot_delta", "risk", "positions[underlying=AAPL].delta"),
    ("portfolio_gamma_at_+10pct", "landscape",
     "portfolio.raw[spot_shift_pct=10.0].gamma"),
    ("portfolio_delta_at_-20pct", "landscape",
     "portfolio.raw[spot_shift_pct=-20.0].delta"),
    ("scenario_cvar", "scenario", "var_cvar.cvar"),
    ("backtest_total_pnl", "backtest", "portfolio.total_pnl"),
]


def harvest(session) -> dict[str, dict]:
    """Drive the frozen flagship seed and dig each target from the REAL payloads.

    Raises if a target path does not resolve (a target that cannot be grounded is
    a bug, not an empty value) or is non-numeric. Pure: does not write the file."""
    payloads = drive_producers(session, seed_flagship(session))
    truth: dict[str, dict] = {}
    for name, producer, path in TARGETS:
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


def write_truth_file() -> Path:
    """Build a clean throwaway DB, harvest, and write the committed truth file."""
    d = Path(tempfile.mkdtemp())
    database.configure_database(Settings(
        database_url=f"sqlite+pysqlite:///{d / 'harvest.sqlite3'}",
        artifact_dir=d / "art", agent_checkpoint_db_path=":memory:"))
    database.init_db()
    with database.SessionLocal() as s:
        truth = harvest(s)
    TRUTH_PATH.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return TRUTH_PATH


if __name__ == "__main__":
    path = write_truth_file()
    print(f"wrote {path}")
    print(path.read_text())
