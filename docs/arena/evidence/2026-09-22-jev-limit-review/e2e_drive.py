"""Drive the limit review through a RUNNING desk server (uvicorn), in three phases.

  outage  server started with System One ON and NO key: a REST waive and a REST
          comment each leave an `unscored: no_key` row — an outage is a row, not a
          lost review, and it is still due.
  agent   server restarted WITH the key: a desk turn in AUTO. The user authorises
          an extension and asks the model to write the rationale itself — the first
          model-written waiver this desk has seen. The guard (shadow) and the review
          both read that one call; the fast path scores it, and the two outage rows
          stay unscored — nothing retries on its own.
  heal    risk is re-run and a monitoring run queued through REST (the arena's own
          step-6 order); that run's post-commit sweep scores the outage rows. Then
          one more comment takes the fast path.

Run in the order outage, agent, heal. Every phase appends to e2e_log.json.
Usage: python e2e_drive.py <phase> [port]
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

PHASE = sys.argv[1]
BASE = f"http://127.0.0.1:{sys.argv[2] if len(sys.argv) > 2 else 8767}"
LOG = Path(__file__).resolve().parent / "e2e_log.json"
PORTFOLIO = "Arena Limit Control Book"

WAIVER = ("Driver is the 1,000-lot AAPL call (about 560 of the 803 delta). LW sold 400 AAPL "
          "futures against it this morning; risk re-runs the book by 2026-09-24 and the cap "
          "should read about 403 once the hedge is in.")
COMMENT_OUTAGE = "Hedge booked: 400 AAPL futures sold at 09:40. Waiting on the risk re-run."
COMMENT_HEAL = "Post-hedge re-run reads inside the cap. Closing the loop with risk today — LW."
AGENT_PROMPT = (
    "The risk committee has approved extending the waiver on the net delta breach incident "
    "on the Arena Limit Control Book until 2026-10-06. Extend it, and write the waiver "
    "rationale yourself from what the incident and the book show: the cause, the "
    "remediation, who owns it and by when.")


def log(entry: dict) -> None:
    rows = json.loads(LOG.read_text()) if LOG.exists() else []
    rows.append({"phase": PHASE, "at": datetime.utcnow().isoformat(timespec="seconds"), **entry})
    LOG.write_text(json.dumps(rows, indent=1, ensure_ascii=False, default=str))
    print(json.dumps(entry, ensure_ascii=False, default=str)[:900], flush=True)


def portfolio_id(client) -> int:
    for p in client.get(f"{BASE}/api/portfolios").json():
        if p["name"] == PORTFOLIO:
            return p["id"]
    raise SystemExit("seed first")


def incident(client, pid) -> dict:
    items = client.get(f"{BASE}/api/limit-incidents", params={"portfolio_id": pid}).json()
    items = items.get("items", items) if isinstance(items, dict) else items
    return client.get(f"{BASE}/api/limit-incidents/{items[0]['id']}",
                      params={"portfolio_id": pid}).json()


def wait_reviews(client, pid, until, timeout=30.0) -> tuple[dict, float]:
    started = time.monotonic()
    while True:
        row = incident(client, pid)
        if until(row["reviews"]) or time.monotonic() - started > timeout:
            return row, time.monotonic() - started
        time.sleep(0.25)


def outage(client, pid):
    row = incident(client, pid)
    log({"step": "before", "incident": row["id"], "status": row["status"], "reviews": row["reviews"]})
    sent = time.monotonic()
    r = client.post(f"{BASE}/api/limit-incidents/{row['id']}/waive", params={"portfolio_id": pid},
                    json={"expected_row_version": row["row_version"], "rationale": WAIVER,
                          "expires_at": (datetime.utcnow() + timedelta(days=7)).isoformat()})
    r.raise_for_status()
    log({"step": "waive", "http": r.status_code, "ms": int((time.monotonic() - sent) * 1000),
         "status": r.json()["status"]})
    row, secs = wait_reviews(client, pid, lambda rv: rv["waiver"] is not None)
    log({"step": "waiver review after outage", "seconds": round(secs, 2), "reviews": row["reviews"]})
    r = client.post(f"{BASE}/api/limit-incidents/{row['id']}/comments", params={"portfolio_id": pid},
                    json={"expected_row_version": row["row_version"], "comment": COMMENT_OUTAGE})
    r.raise_for_status()
    row, secs = wait_reviews(client, pid, lambda rv: rv["thread"] is not None)
    log({"step": "thread review after outage", "seconds": round(secs, 2), "reviews": row["reviews"]})


def _db_reviews() -> list[dict]:
    from sqlalchemy import select
    from app import database
    from app.models import LimitIncidentReview

    with database.SessionLocal() as session:
        return [{"event_id": r.event_id, "kind": r.kind, "status": r.status,
                 "unscored_reason": r.unscored_reason, "rationale_grade": r.rationale_grade,
                 "thread_state": r.thread_state, "latency_ms": r.latency_ms,
                 "attempted_at": r.attempted_at}
                for r in session.scalars(select(LimitIncidentReview).order_by(LimitIncidentReview.id))]


def heal(client, pid):
    """The arena's own order (step 6): re-run risk, then monitor. The book already holds
    the 400-lot futures hedge, so the fresh run reads ~403 and the incident recovers."""
    from sqlalchemy import select
    from app import database
    from app.config import get_settings
    from app.models import PricingParameterProfile, RiskRun
    from app.services.limits.agent_support import derive_monitoring_envelope

    database.configure_database(get_settings())
    row = incident(client, pid)
    log({"step": "before heal", "status": row["status"], "api_reviews": row["reviews"],
         "db_reviews": _db_reviews()})
    with database.SessionLocal() as session:
        profile_id = session.scalar(select(PricingParameterProfile.id).where(
            PricingParameterProfile.name == "Arena Limit Control Profile"))
    risk = client.post(f"{BASE}/api/batch-pricing/runs",
                       json={"portfolio_id": pid, "pricing_parameter_profile_id": profile_id})
    risk.raise_for_status()
    risk_id, started = risk.json()["id"], time.monotonic()
    while True:
        with database.SessionLocal() as session:
            status = session.get(RiskRun, risk_id).status
        if status in ("completed", "failed") or time.monotonic() - started > 180:
            break
        time.sleep(1)
    log({"step": "risk re-run", "risk_run": risk_id, "status": status,
         "seconds": round(time.monotonic() - started, 1)})
    with database.SessionLocal() as session:
        envelope = derive_monitoring_envelope(session, pid)
    body = {"portfolio_id": pid, "source_policy": "reuse_only",
            **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in envelope.items()}}
    sent = time.monotonic()
    run = client.post(f"{BASE}/api/limit-monitoring/runs", json=body)
    log({"step": "monitoring run queued", "http": run.status_code, "body": run.json()})
    run.raise_for_status()
    finish(client, pid, run.json()["id"], sent)


def finish(client, pid, run_id: int, sent: float | None = None):
    """The tail of `heal`, resumable by run id: wait for the run, read the reviews,
    then one fast-path comment. (The first `heal` crashed here on a script bug — the
    run GET needs portfolio_id — after the run had been queued; `finish` completed it.)"""
    sent = sent if sent is not None else time.monotonic()
    while True:
        state = client.get(f"{BASE}/api/limit-monitoring/runs/{run_id}",
                           params={"portfolio_id": pid}).json()
        if state["status"] not in ("queued", "running") or time.monotonic() - sent > 120:
            break
        time.sleep(0.5)
    deadline = time.monotonic() + 30
    while any(r["status"] != "scored" for r in _db_reviews()) and time.monotonic() < deadline:
        time.sleep(0.5)
    row = incident(client, pid)
    log({"step": "after monitoring run", "run_id": run_id, "run_status": state["status"],
         "summary": state.get("summary"), "run_started_at": state.get("started_at"),
         "run_finished_at": state.get("finished_at"), "incident_status": row["status"],
         "api_reviews": row["reviews"], "db_reviews": _db_reviews()})
    sent = time.monotonic()
    r = client.post(f"{BASE}/api/limit-incidents/{row['id']}/comments", params={"portfolio_id": pid},
                    json={"expected_row_version": row["row_version"], "comment": COMMENT_HEAL})
    r.raise_for_status()
    before = (row["reviews"]["thread"] or {}).get("event_id")
    row, _ = wait_reviews(client, pid, lambda rv: rv["thread"] and rv["thread"]["event_id"] != before)
    log({"step": "fast-path comment", "seconds_to_review": round(time.monotonic() - sent, 2),
         "api_reviews": row["reviews"]})


def stream_turn(client, thread_id, content):
    tools, text, started = [], [], time.time()
    with client.stream("POST", f"{BASE}/api/chat/threads/{thread_id}/messages/stream",
                       json={"content": content, "mode": "auto"}, timeout=900) as resp:
        resp.raise_for_status()
        event = None
        for line in resp.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    data = json.loads(line[5:])
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                name = data.get("name") or data.get("tool") or data.get("tool_name")
                if event and "tool" in event and name:
                    tools.append((event, name))
                if event in {"token", "message", "delta"}:
                    text.append(str(data.get("content") or data.get("text") or ""))
    return tools, "".join(text), time.time() - started


def agent(client, pid):
    before = incident(client, pid)
    thread = client.post(f"{BASE}/api/chat/threads",
                         json={"title": "Jev limit review smoke", "character": "trader"}).json()
    tools, text, secs = stream_turn(client, thread["id"], AGENT_PROMPT)
    log({"step": "agent turn", "thread_id": thread["id"], "seconds": round(secs, 1),
         "tool_events": sorted({n for _, n in tools}), "reply": text.strip()[:1500]})
    prior = (before["reviews"]["waiver"] or {}).get("event_id")
    row, waited = wait_reviews(client, pid, lambda rv: rv["waiver"] and rv["waiver"]["event_id"] != prior)
    log({"step": "after agent turn", "status": row["status"], "db_reviews": _db_reviews(), "waiver_rationale": row.get("waiver_rationale"),
         "waiver_expires_at": row.get("waiver_expires_at"), "waited": round(waited, 2),
         "reviews": row["reviews"]})
    verdicts = client.get(f"{BASE}/api/audit/guard-verdicts",
                          params={"thread_id": thread["id"], "limit": 50}).json()
    log({"step": "guard verdicts", "verdicts": verdicts})


def main():
    # trust_env=False: a shell HTTP_PROXY would otherwise carry localhost calls (and drop them)
    with httpx.Client(timeout=60, trust_env=False) as client:
        pid = portfolio_id(client)
        if PHASE == "finish":
            from app import database
            from app.config import get_settings
            database.configure_database(get_settings())
            finish(client, pid, int(sys.argv[3]))
        else:
            {"outage": outage, "heal": heal, "agent": agent}[PHASE](client, pid)


if __name__ == "__main__":
    main()
