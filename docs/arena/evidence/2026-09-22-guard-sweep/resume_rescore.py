"""Post-hoc re-read of the calls a HITL resume split across two trace roots.

The first run rebuilt their window by dotted_order, which a resume changes, so
each was scored on an EMPTY window at `trace` fidelity. This re-asks Jev for
exactly those calls through the production scorer, with the corrected scope,
on a COPY whose first-run verdicts for them are deleted first. The committed
verdicts.json is left as it was (D10): this is a labelled re-read, not a re-run.

Usage: ZENMUX_API_KEY=... .venv/bin/python <this file> /abs/copy.sqlite3 /abs/traces.sqlite3
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import Settings  # noqa: E402
from app.models import AgentActionAudit, AgentToolGuardVerdict  # noqa: E402
from app.services.deep_agent import tool_guard_records as rec  # noqa: E402
from app.services.deep_agent.tool_guard_sweep import score_audit_row  # noqa: E402

copy, trace = Path(sys.argv[1]).resolve(), Path(sys.argv[2])
settings = Settings(database_url=f"sqlite+pysqlite:///{copy}", system_one_enabled=True,
                    trace_db_path=str(trace))
database.configure_database(settings)
here = Path(__file__).parent
first = {r["audit_id"]: r for r in json.loads((here / "verdicts.json").read_text())}


def split_across_roots(session, row) -> bool:
    """The call's enclosing `task` appears under more than one trace root."""
    turn = rec.user_turn(session, row.thread_id, row.occurred_at)
    spans = [s for s in rec.read_spans(trace, row.thread_id, turn.started) or []
             if s.run_type == "tool" and s.start >= turn.started
             and (turn.ended is None or s.start < turn.ended)]
    own = next((s for s in spans if s.tool_call_id == row.tool_call_id), None)
    enclosing = [t for t in spans if t.name == "task" and own is not None
                 and own.dotted_order.startswith(t.dotted_order + ".")]
    if not enclosing:
        return False
    task = max(enclosing, key=lambda t: len(t.dotted_order))
    return sum(1 for s in spans if s.name == "task" and s.tool_call_id == task.tool_call_id) > 1


out = []
with database.SessionLocal() as session:
    rows = [session.get(AgentActionAudit, audit_id) for audit_id in sorted(first)
            if first[audit_id]["fidelity"] == "trace"]
    targets = [row for row in rows if split_across_roots(session, row)]
    for row in targets:
        session.query(AgentToolGuardVerdict).filter_by(
            thread_id=row.thread_id, tool_call_id=row.tool_call_id, source="sweep").delete()
    session.commit()
    for row in targets:
        result = score_audit_row(session, row, settings=settings, trace_path=trace)
        v = session.query(AgentToolGuardVerdict).filter_by(
            thread_id=row.thread_id, tool_call_id=row.tool_call_id).one()
        before = first[row.id]
        out.append({
            "audit_id": row.id, "thread_id": row.thread_id, "tool": row.tool_name,
            "outage": result.outage,
            "first_run": {"verdict": before["verdict"],
                          "predicates": {p["key"]: p["probability"] for p in before["predicates"]}},
            "corrected": {"verdict": v.verdict, "fidelity": v.state_fidelity,
                          "predicates": {p["key"]: p["probability"]
                                         for p in (v.predicates_json or [])}},
        })
(here / "resume_rescore.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
for o in out:
    print(json.dumps({"tool": o["tool"], "thread": o["thread_id"],
                      "first": o["first_run"]["verdict"], "corrected": o["corrected"]["verdict"],
                      "p_first": o["first_run"]["predicates"],
                      "p_corrected": o["corrected"]["predicates"]}))
