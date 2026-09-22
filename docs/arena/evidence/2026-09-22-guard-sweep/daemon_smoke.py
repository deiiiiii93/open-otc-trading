"""Live smoke: one SweepDaemon pass over a COPY of the desk DB (spec 2026-09-22 rollout 2).

Usage: ZENMUX_API_KEY=... .venv/bin/python <this file> /abs/path/to/copy.sqlite3
The lookback is widened to 60 days FOR THE SMOKE ONLY: the desk writes about one
swept call a day, so a 7-day pass may have nothing to score.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import AgentToolGuardVerdict  # noqa: E402
from app.services.deep_agent import tool_guard_sweep as sweep  # noqa: E402

copy = Path(sys.argv[1]).resolve()
settings = Settings(database_url=f"sqlite+pysqlite:///{copy}", system_one_enabled=True,
                    guard_sweep_enabled=True,
                    trace_db_path="/Users/fuxinyao/open-otc-trading/data/agent_traces.sqlite3")
database.configure_database(settings)
sweep.sweep_lookback_days = 60
print(json.dumps(sweep.SweepDaemon(settings).run_pass()))
with database.SessionLocal() as session:
    for v in (session.query(AgentToolGuardVerdict).filter_by(source="sweep")
              .order_by(AgentToolGuardVerdict.id)):
        print(json.dumps({"tool": v.tool_name, "verdict": v.verdict,
                          "fidelity": v.state_fidelity, "persona": v.persona,
                          "max_p": v.max_probability, "reason": v.unscored_reason}))
