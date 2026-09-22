"""Live smoke of the limit incident review through the MERGED scoring path.

Every call is `review.score_event(event_id, kind)` on an event written by the real
`incidents.waive` / `incidents.comment` in a scratch database — the function the
post-commit fast path and the monitoring sweep both call. The state Jev reads is
built by `review.build_waiver_state` / `build_thread_state` from that event; this
script never builds a request of its own. The state is rebuilt once more after
scoring only so the record shows what was sent.

  ladder   the 15-case fixture (POST-HOC: it tuned the wording) + 12 held-out cases
  pairs    one rationale on two books that differ only in database facts
  threads  8 held-out comment threads, scored on their latest comment
  gates    an arena thread, an unresolvable thread, the feature switch — no calls

Usage (see README): python review_smoke.py [repeats=3]
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app import database
from app.config import get_settings
from app.models import AgentThread, LimitIncident, LimitIncidentEvent, LimitIncidentReview
from app.services.limits import review
from app.services.limits.contracts import LimitActionContext

import world

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
FIXTURE = json.loads((REPO / "scripts/fixtures/limit_review_rationales.json").read_text())
CASES = json.loads((HERE / "cases.json").read_text())


def _row(event_id: int, kind: str) -> dict | None:
    with database.SessionLocal() as s:
        row = s.scalar(select(LimitIncidentReview).where(
            LimitIncidentReview.event_id == event_id, LimitIncidentReview.kind == kind))
        if row is None:
            return None
        return {
            "status": row.status, "unscored_reason": row.unscored_reason,
            "rationale_grade": row.rationale_grade, "rationale_confidence": row.rationale_confidence,
            "level": None if row.rationale_grade is None else round(row.rationale_grade * 4),
            "authority_only_p": row.authority_only_p, "thread_state": row.thread_state,
            "thread_state_p": row.thread_state_p, "claims": row.claims_json,
            "answers": row.answers_json, "model": row.model, "latency_ms": row.latency_ms,
        }


def _state(event_id: int, kind: str) -> dict:
    with database.SessionLocal() as s:
        event = s.get(LimitIncidentEvent, event_id)
        incident = s.get(LimitIncident, event.incident_id)
        build = review.build_waiver_state if kind == review.KIND_WAIVER else review.build_thread_state
        return build(s, incident, event)


def _score(event_id: int, kind: str) -> dict:
    started = time.monotonic()
    outcome = review.score_event(event_id, kind)
    wall_ms = int((time.monotonic() - started) * 1000)
    return {"event_id": event_id, "outcome": outcome, "wall_ms": wall_ms,
            "review": _row(event_id, kind), "state": _state(event_id, kind)}


def _fired(rec: dict) -> list[str]:
    claims = (rec.get("review") or {}).get("claims") or []
    return sorted(c["claim"] for c in claims if c["p"] >= review.review_chip_min_p)


def ladder(repeats: int) -> list[dict]:
    out = []
    sets = [
        ("fixture", FIXTURE["cases"], {
            "limit_name": FIXTURE["limit"]["name"], "metric_kind": "delta",
            "unit": FIXTURE["limit"]["unit"], "scope_type": "underlying",
            "scope_value": FIXTURE["limit"]["scope_label"],
            "utilization": FIXTURE["breach"]["utilization"],
            "days_open": FIXTURE["breach"]["days_open"]}),
        ("holdout", CASES["holdout"], CASES["holdout_incident"]),
    ]
    for name, cases, spec in sets:
        with database.SessionLocal() as s:
            incident_id = world.open_incident(s, tag=f"ladder-{name}", spec=spec,
                                              now=datetime.utcnow()).id
        for case in cases:
            for rep in range(repeats):
                with database.SessionLocal() as s:
                    event_id = world.waive(s, incident_id, case["rationale"],
                                           duration_days=case.get("duration_days", spec.get("duration_days", 12)))
                rec = _score(event_id, review.KIND_WAIVER)
                rec.update({"set": name, "case": case["id"], "repeat": rep,
                            "expected_level": case["expected_level"],
                            "expected_claims": sorted(case["expected_claims"]),
                            "expected_authority_only": case["expected_authority_only"]})
                r = rec["review"] or {}
                if r.get("status") == "scored":
                    rec["fired"] = _fired(rec)
                    rec["ok_level"] = r["level"] == case["expected_level"]
                    rec["ok_claims"] = rec["fired"] == sorted(case["expected_claims"])
                    rec["ok_authority"] = ((r["authority_only_p"] or 0) >= review.review_chip_min_p) \
                        == case["expected_authority_only"]
                print(f"  {name:8s} {case['id']:28s} r{rep} {rec['outcome']:8s} "
                      f"L{r.get('level')} (exp {case['expected_level']}) fired={rec.get('fired')} "
                      f"auth={r.get('authority_only_p') and round(r['authority_only_p'], 2)} "
                      f"{r.get('latency_ms')}ms", flush=True)
                out.append(rec)
    return out


def pairs(repeats: int) -> list[dict]:
    spec = CASES["pairs_incident"]
    now = datetime.utcnow()
    with database.SessionLocal() as s:
        with_facts = world.open_incident(s, tag="pair-with", spec=spec, now=now, coverage_ratio=0.8)
        world.book_option(s, with_facts.portfolio_id, underlying=spec["scope_value"],
                          expiry=now.date() + timedelta(days=8))
        world.draft_new_version(s, with_facts, spec)
        without = world.open_incident(s, tag="pair-without", spec=spec, now=now)
        world.book_option(s, without.portfolio_id, underlying=spec["scope_value"],
                          expiry=date(2026, 12, 18))
        books = {"with_facts": with_facts.id, "without_facts": without.id}
    out = []
    for case in CASES["pairs"]:
        for rep in range(repeats):
            for book, incident_id in books.items():
                with database.SessionLocal() as s:
                    event_id = world.waive(s, incident_id, case["rationale"],
                                           duration_days=spec["duration_days"])
                rec = _score(event_id, review.KIND_WAIVER)
                rec.update({"case": case["id"], "claim": case["claim"], "book": book, "repeat": rep})
                claims = {c["claim"]: c for c in ((rec["review"] or {}).get("claims") or [])}
                target = claims.get(case["claim"], {})
                print(f"  {case['id']:16s} {book:13s} r{rep} p={target.get('p')} "
                      f"check={target.get('check')} :: {target.get('detail')}", flush=True)
                out.append(rec)
    return out


def threads(repeats: int) -> list[dict]:
    spec = CASES["thread_incident"]
    out = []
    for case in CASES["threads"]:
        for rep in range(repeats):
            with database.SessionLocal() as s:
                incident_id = world.open_incident(s, tag=f"{case['id']}-r{rep}", spec=spec,
                                                  now=datetime.utcnow()).id
                for actor, text in case["comments"]:
                    event_id = world.comment(s, incident_id, actor, text)
            rec = _score(event_id, review.KIND_THREAD)
            rec.update({"case": case["id"], "repeat": rep, "expected": case["expected"]})
            r = rec["review"] or {}
            probs = ((r.get("answers") or {}).get("thread_state") or {}).get("probabilities") or {}
            top = max(probs, key=probs.get) if probs else None
            rec["top_choice"] = top
            rec["ok"] = r.get("status") == "scored" and r.get("thread_state") == case["expected"]
            print(f"  {case['id']:22s} r{rep} {rec['outcome']:14s} state={r.get('thread_state')} "
                  f"p={r.get('thread_state_p') and round(r['thread_state_p'], 2)} top={top} "
                  f"(exp {case['expected']}) {r.get('latency_ms')}ms", flush=True)
            out.append(rec)
    return out


def gates() -> dict:
    """The skip rules, on real events, with the switch on. None of these calls Jev."""
    spec = CASES["pairs_incident"]
    out = {}
    with database.SessionLocal() as s:
        incident = world.open_incident(s, tag="gates", spec=spec, now=datetime.utcnow())
        arena = AgentThread(title="[arena] risk-limit-breach-day · smoke", source="arena")
        s.add(arena)
        s.commit()
        arena_ctx = replace(world.DESK, thread_id=arena.id)
        ghost_ctx = replace(world.DESK, thread_id=999_999)
        arena_event = world.waive(s, incident.id, "Arena-thread waiver.", duration_days=5,
                                  context=arena_ctx)
        ghost_event = world.waive(s, incident.id, "Waiver from a thread that no longer exists.",
                                  duration_days=5, context=ghost_ctx)
    out["arena_thread"] = {"outcome": review.score_event(arena_event, review.KIND_WAIVER),
                           "row": _row(arena_event, review.KIND_WAIVER)}
    out["unresolvable_thread"] = {"outcome": review.score_event(ghost_event, review.KIND_WAIVER),
                                  "row": _row(ghost_event, review.KIND_WAIVER)}
    off = replace(get_settings(), limit_review_enabled=False)
    out["switch_off"] = {"outcome": review.score_event(ghost_event, review.KIND_WAIVER, settings=off)}
    with database.SessionLocal() as s:
        due = review.due_events(s, 1000)
    out["due_after"] = {"arena_event_due": (arena_event, "waiver") in due,
                        "unresolvable_event_due": (ghost_event, "waiver") in due}
    print("  gates:", json.dumps(out), flush=True)
    return out


def main() -> int:
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    settings = get_settings()
    assert review.is_live(settings), "OPEN_OTC_SYSTEM_ONE must be true (see README)"
    assert settings.database_url.startswith("sqlite") and "open_otc.sqlite3" not in settings.database_url, \
        "refusing to run against the live database"
    database.configure_database(settings)
    database.init_db()
    started = datetime.utcnow().isoformat(timespec="seconds")
    print("== gates"); g = gates()
    print("== ladder"); lad = ladder(repeats)
    print("== pairs"); prs = pairs(repeats)
    print("== threads"); thr = threads(repeats)
    result = {"started_at": started, "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
              "model": settings.system_one_model, "repeats": repeats,
              "chip_min_p": review.review_chip_min_p, "choice_min_p": review.review_choice_min_p,
              "gates": g, "ladder": lad, "pairs": prs, "threads": thr}
    (HERE / "review_results.json").write_text(json.dumps(result, indent=1, default=str,
                                                         ensure_ascii=False))
    calls = sum(1 for r in lad + prs + thr if (r["review"] or {}).get("model"))
    print(f"wrote review_results.json — {calls} rows carrying a model")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
