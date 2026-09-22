"""Export the live session's evidence straight from its scratch database.

Every review row (all columns), the incident's event timeline, the guard verdict on
the model's waive call, the audit rows, and the turn's messages. Plus one read
through the agent's own `get_limit_incident` tool, to record that a review is not
part of what the agent sees (spec D13).
Usage: OPEN_OTC_DATABASE_URL=<the session DB> python e2e_export.py
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text

from app import database
from app.config import get_settings

TABLES = {
    "reviews": "SELECT * FROM limit_incident_reviews ORDER BY id",
    "incident_events": (
        "SELECT id, incident_id, event_type, actor, persona, mode, thread_id, payload, created_at "
        "FROM limit_incident_events ORDER BY id"),
    "guard_verdicts": "SELECT * FROM agent_tool_guard_verdicts ORDER BY id",
    "audit_actions": (
        "SELECT id, kind, status, tool_name, tool_class, mode, model, persona, thread_id, "
        "args_json, error, occurred_at FROM agent_action_audits ORDER BY id"),
    "messages": "SELECT id, role, character, substr(content, 1, 2500) AS content FROM agent_messages ORDER BY id",
}


def main():
    settings = get_settings()
    assert "open_otc.sqlite3" not in settings.database_url, "refusing to read the live database"
    engine = create_engine(settings.database_url)
    out = {}
    with engine.connect() as conn:
        for key, sql in TABLES.items():
            out[key] = [dict(row._mapping) for row in conn.execute(text(sql))]
    database.configure_database(settings)
    from app.tools.limits import get_limit_incident_tool
    seen = get_limit_incident_tool.func(incident_id=1)
    out["agent_tool_view"] = {"keys": sorted(seen), "mentions_review": "review" in json.dumps(seen)}
    with open("e2e_results.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str, ensure_ascii=False)
    print({k: len(v) for k, v in out.items() if isinstance(v, list)}, out["agent_tool_view"])


if __name__ == "__main__":
    main()
