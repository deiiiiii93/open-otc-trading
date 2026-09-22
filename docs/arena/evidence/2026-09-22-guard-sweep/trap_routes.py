"""Sort the void traps by the route that led to the void (README finding 2).

Re-assembles, for every `void_settlement_cashflow` case in verdicts.json, the state
the guard sweep sent to Jev — same records, same code — and reads the lifecycle calls
in the persona's own window: a refused reopen, a successful cancellation, or neither.
No Jev call; read-only against the DB copy and the trace DB.

Usage: OPEN_OTC_DATABASE_URL=sqlite:////abs/copy.sqlite3 .venv/bin/python <this file> /abs/traces.sqlite3
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.models import AgentActionAudit  # noqa: E402
from app.services.deep_agent import tool_guard_records as rec  # noqa: E402
from app.services.deep_agent.tool_guard_state import assemble_guard_state  # noqa: E402

trace = Path(sys.argv[1])
here = Path(__file__).parent
LIFECYCLE = ("record_lifecycle_event", "cancel_lifecycle_event")


def route(earlier: list[str]) -> str:
    refused = cancelled = False
    for line in earlier:
        name, _, rest = line.partition("(")
        result = rest.split(") -> ", 1)[1] if ") -> " in rest else ""
        if name == "record_lifecycle_event" and "Cannot reopen" in result:
            refused = True
        if name == "cancel_lifecycle_event" and '"ok": true' in result:
            cancelled = True
    if refused:
        return "refused_then_void"
    return "cancelled_then_void" if cancelled else "no_lifecycle_call"


out = []
with database.SessionLocal() as session:
    for r in json.loads((here / "verdicts.json").read_text()):
        if r["tool"] != "void_settlement_cashflow":
            continue
        row = session.get(AgentActionAudit, r["audit_id"])
        turn = rec.user_turn(session, row.thread_id, row.occurred_at)
        window = rec.window_from_records(session, row, turn, trace_path=trace)
        state = assemble_guard_state(window.window, {"name": row.tool_name, "args": row.args_json})
        earlier = state["earlier_in_this_turn"]
        out.append({
            "audit_id": row.id, "model": r["model_id"], "label": r["label"],
            "fidelity": window.fidelity, "route": route(earlier),
            "lifecycle_calls": [line.split(") -> ")[0] + ")" for line in earlier
                                if line.startswith(LIFECYCLE)],
            "predicates": {p["key"]: p["probability"] for p in r["predicates"]},
        })
(here / "trap_routes.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
for o in sorted(out, key=lambda o: (o["label"], o["route"])):
    p = o["predicates"]
    print(f"{o['label']:9s} {o['fidelity']:10s} {o['route']:20s} clears_blocker={p['clears_blocker']:.2f} "
          f"unnamed_target={p['unnamed_target']:.2f}  {o['model']}")
