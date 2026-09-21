"""Export the live session's evidence straight from its database.

e2e_drive.py originally fetched `/api/audit` (a 404; the list route is
`/api/audit/actions`), and the guard-verdict API omits `user_request_source`.
So the record in e2e_results.json is read from the session DB instead: every
guard verdict (all columns), every audit row, and the turn messages.
Usage: OPEN_OTC_DATABASE_URL=<the session DB> python e2e_export.py
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, text

from app.config import get_settings

TABLES = {
    "guard_verdicts": "SELECT * FROM agent_tool_guard_verdicts ORDER BY id",
    "audit_actions": (
        "SELECT id, kind, status, tool_name, tool_class, tool_call_id, mode, model, persona, "
        "thread_id, message_id, args_json, error, occurred_at FROM agent_action_audits ORDER BY id"
    ),
    "messages": "SELECT id, role, character, substr(content, 1, 1200) AS content FROM agent_messages ORDER BY id",
    "cashflows_after": "SELECT id, status FROM settlement_cashflows ORDER BY id",
}


def main():
    engine = create_engine(get_settings().database_url)
    out = {}
    with engine.connect() as conn:
        for key, sql in TABLES.items():
            out[key] = [dict(row._mapping) for row in conn.execute(text(sql))]
    with open("e2e_results.json", "w") as fh:
        json.dump(out, fh, indent=1, default=str, ensure_ascii=False)
    print({k: len(v) for k, v in out.items()})


if __name__ == "__main__":
    main()
