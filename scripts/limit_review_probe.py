#!/usr/bin/env python
"""Evidence probe for the limit-incident review predicates. NOT an app path.

1. Counts the model-written corpus: waive_limit_incident / comment_limit_incident
   calls in risk-limit-breach-day arena matches (trace DB, read-only), split by the
   step-5 outcome the scorer already recorded ("held" = tool NOT called passed).
2. Scores each through the SAME questions the feature uses.
3. Scores the committed hand-written fixture, so a wording change is re-run
   against the same cases.

Output is direction, never a rate: model-written text, one workflow's policy,
tiny N. A predicate moves from `untested` only by an edit that cites a run.

Usage:  ZENMUX_API_KEY=... .venv/bin/python scripts/limit_review_probe.py [--fixture-only] [--limit 50]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app import database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import AgentThread, ArenaMatch  # noqa: E402
from app.services.limits import review  # noqa: E402
from app.services.system_one import SystemOneUnavailable, ask  # noqa: E402

WORKFLOW = "risk-limit-breach-day"
TITLE_PREFIX = f"[arena] {WORKFLOW} · "
STEP5_LABEL = "tool NOT called: waive_limit_incident"
FIXTURE = ROOT / "scripts" / "fixtures" / "limit_review_rationales.json"


def _find_check(node: Any, label: str) -> bool | None:
    """Depth-first search of a score_breakdown for a check with `label`."""
    if isinstance(node, dict):
        if node.get("label") == label and "passed" in node:
            return bool(node["passed"])
        for value in node.values():
            found = _find_check(value, label)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_check(item, label)
            if found is not None:
                return found
    return None


def _corpus(limit: int) -> list[dict[str, Any]]:
    """Every waive/comment tool call in the workflow's arena threads, with its label."""
    trace_path = Path(get_settings().trace_db_path)
    if not trace_path.exists():
        print(f"trace DB not found at {trace_path}; corpus = 0")
        return []
    rows: list[dict[str, Any]] = []
    conn = sqlite3.connect(f"file:{trace_path}?mode=ro", uri=True)
    try:
        with database.SessionLocal() as session:
            threads = session.scalars(select(AgentThread).where(
                AgentThread.source == "arena", AgentThread.title.like(TITLE_PREFIX + "%")
            )).all()
            for thread in threads:
                model = thread.title[len(TITLE_PREFIX):]
                match = session.scalar(select(ArenaMatch).where(
                    ArenaMatch.run_id == thread.arena_run_id, ArenaMatch.workflow_id == WORKFLOW,
                    ArenaMatch.model_id == model))
                held = _find_check(match.score_breakdown, STEP5_LABEL) if match is not None else None
                outcome = "unknown" if held is None else ("held" if held else "waived")
                spans = conn.execute(
                    "SELECT name, inputs, start_time FROM trace_runs WHERE thread_id=? AND run_type='tool' "
                    "AND name IN ('waive_limit_incident','comment_limit_incident') ORDER BY start_time",
                    (thread.id,)).fetchall()
                for name, inputs, start_time in spans:
                    try:
                        args = json.loads(inputs) if inputs else {}
                    except ValueError:
                        args = {}
                    text = args.get("rationale") if name == "waive_limit_incident" else args.get("comment")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    rows.append({"thread_id": thread.id, "run_id": thread.arena_run_id, "model": model,
                                 "tool": name, "text": text, "expires_at": args.get("expires_at"),
                                 "at": start_time, "step5": outcome})
    finally:
        conn.close()
    return rows[:limit]


def _waiver_state(limit: dict, breach: dict, rationale: str, duration_days: int | None) -> dict:
    return {"limit": limit, "breach": breach,
            "waiver": {"rationale": rationale, "duration_days": duration_days}}


def _level(answer) -> int:
    return round(answer.normalized * 4)


def _score_waiver(state: dict) -> dict[str, Any] | str:
    try:
        result = ask(state, review.WAIVER_QUESTIONS)
    except SystemOneUnavailable as exc:
        return exc.reason
    answers = result.answers
    return {
        "level": _level(answers["rationale_grade"]),
        "confidence": round(answers["rationale_grade"].confidence, 2),
        "claims": {key: round(answers[key].probability, 2) for key in review.CLAIM_QUESTIONS},
        "authority_only": round(answers["authority_only"].probability, 2),
        "latency_ms": result.latency_ms,
    }


def _print_corpus(rows: list[dict[str, Any]]) -> None:
    print(f"\n== arena corpus: {len(rows)} text rows ==")
    by = Counter((r["tool"], r["step5"]) for r in rows)
    for (tool, outcome), n in sorted(by.items()):
        print(f"  {tool:24s} step5={outcome:8s} n={n}")
    limit = {"name": "Tool Net Delta Cap", "metric_kind": "delta", "unit": "underlying_units",
             "scope_type": "portfolio", "scope_label": "arena book"}
    breach = {"severity": "breach", "utilization": None, "days_open": 0}
    levels: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        if row["tool"] != "waive_limit_incident":
            continue
        scored = _score_waiver(_waiver_state(limit, breach, row["text"], None))
        if isinstance(scored, str):
            print(f"  ! {scored} on thread {row['thread_id']}")
            continue
        levels[row["step5"]][scored["level"]] += 1
        fired = [k for k, p in scored["claims"].items() if p >= review.review_chip_min_p]
        print(f"  [{row['step5']}] {row['model']} L{scored['level']} auth={scored['authority_only']} "
              f"claims={fired} :: {row['text'][:90]!r}")
    for outcome, counter in levels.items():
        print(f"  level distribution step5={outcome}: {dict(sorted(counter.items()))}")


def _print_fixture() -> int:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(f"\n== fixture: {len(data['cases'])} cases ==")
    misses = 0
    for case in data["cases"]:
        scored = _score_waiver(_waiver_state(data["limit"], data["breach"], case["rationale"],
                                             case["duration_days"]))
        if isinstance(scored, str):
            print(f"  ! {scored}: {case['id']}")
            misses += 1
            continue
        fired = sorted(k for k, p in scored["claims"].items() if p >= review.review_chip_min_p)
        ok_level = scored["level"] == case["expected_level"]
        ok_claims = fired == sorted(case["expected_claims"])
        ok_auth = (scored["authority_only"] >= review.review_chip_min_p) == case["expected_authority_only"]
        miss = not (ok_level and ok_claims and ok_auth)
        misses += 1 if miss else 0
        print(f"  {case['id']:28s} L{scored['level']} (exp {case['expected_level']}) "
              f"claims={fired} (exp {sorted(case['expected_claims'])}) auth={scored['authority_only']}"
              f"{'  <-- MISS' if miss else ''}")
    print(f"  misses: {misses}/{len(data['cases'])} — direction, not a rate")
    return misses


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    if not args.fixture_only:
        # Read-only: never init_db() here — create_all would touch the live schema.
        _print_corpus(_corpus(args.limit))
    _print_fixture()
    print("\nEvery predicate stays `untested` until an edit cites this run (spec §Evidence probe).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
