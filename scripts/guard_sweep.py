#!/usr/bin/env python
"""Evidence driver for the retrospective guard sweep. NOT an app path.

Spec: docs/superpowers/specs/2026-09-22-guard-sweep-design.md (D1, D9, D10).
Plan: docs/superpowers/plans/2026-09-22-guard-sweep.md (findings F4, F5).

  select  (--tools T,T... | --all-tools) --kinds arena[,desk] [--workflow W ...]
          [--since YYYY-MM-DD] (--limit N | --all) --out DIR [--artifacts PATH]
          -> DIR/cases.json    (commit it BEFORE `score`: held-out discipline, D10)
  score   DIR/cases.json     -> source="sweep" verdict rows; resumable (the verdict
                                table is the checkpoint); exit 1 if an outage left
                                cases due, 2 if the policy moved since select
  report  DIR/cases.json     -> DIR/report.md + DIR/verdicts.json

`--limit N` keeps N cases per (tool, label), evenly spread over time, so a
tool's few traps are never crowded out by its many expected calls.

Labels (F5) come from each arena match's transcript, joined by tool_call_id (or
by the call's own trace span id, which the transcript uses for a tool that raised),
and TODAY's workflow definition: `trap` = the call's step (or the session)
forbids that tool and the scorer's own evaluate_assertion fails on it;
`expected` = the step expects it; `unlabelled` otherwise; `no_match` = the call
is in no same-era transcript of its thread's run. HITL decisions add
`approved` / `rejected`. Labels live in cases.json, never on verdict rows (D9).

Everything here is model-written text under one harness's policies: direction,
never a rate. `expected` is not `correct`.

Point it at the desk's data explicitly — a worktree's ./data is not the desk's,
and a wrong DB variable silently falls back to ./data:
  OPEN_OTC_DATABASE_URL=sqlite:////ABS/open-otc-trading/data/open_otc.sqlite3 \\
  OPEN_OTC_TRACE_DB_PATH=/ABS/open-otc-trading/data/agent_traces.sqlite3 \\
  ZENMUX_API_KEY=... .venv/bin/python scripts/guard_sweep.py select --artifacts /ABS/open-otc-trading/artifacts/arena ...
Never run `init_db()` from here: create_all would touch the live schema.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import database  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.golden_workflows.assertions import AssertionContext, evaluate_assertion  # noqa: E402
from app.golden_workflows.registry import list_workflows  # noqa: E402
from app.golden_workflows.schema import normalize_tool_name  # noqa: E402
from app.models import AgentActionAudit, AgentThread, AgentToolGuardVerdict  # noqa: E402
from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL  # noqa: E402
from app.services.deep_agent.tool_guard_policy import (  # noqa: E402
    GUARD_POLICY, SWEEP_POLICY, policy_sha256, validate_policy, validate_sweep_policy,
)
from app.services.deep_agent.tool_guard_records import read_spans  # noqa: E402
from app.services.deep_agent.tool_guard_sweep import (  # noqa: E402
    ARENA, KINDS, eligible_rows, score_audit_row,
)

TITLE_PREFIX = "[arena] "
TITLE_SEPARATOR = " · "
TRAP, EXPECTED, UNLABELLED, NO_MATCH = "trap", "expected", "unlabelled", "no_match"
APPROVED, REJECTED = "approved", "rejected"
SECONDS_PER_ROW = 1.3      # measured median Jev latency (system_one/CLAUDE.md)
USD_PER_ROW = 0.003        # the spec's D8 estimate
HEADLINE = "Direction, never a rate."


# --- labels (F5) -------------------------------------------------------------

def parse_title(title: str | None) -> tuple[str, str] | None:
    """`[arena] <workflow> · <model>` -> (workflow, model); None for anything else."""
    if not title or not title.startswith(TITLE_PREFIX) or TITLE_SEPARATOR not in title:
        return None
    workflow, model = (part.strip() for part in
                       title[len(TITLE_PREFIX):].split(TITLE_SEPARATOR, 1))
    return (workflow, model) if workflow and model else None


def _norm(text: str | None) -> str:
    return " ".join((text or "").split())


def same_era(transcript: Mapping[str, Any], workflow: Any) -> bool:
    """A transcript is labelled by today's definition only if its steps ARE
    today's steps — manifest eras are not comparable."""
    steps = transcript.get("steps") or []
    return len(steps) == len(workflow.steps) and all(
        _norm(s.get("user")) == _norm(w.user) for s, w in zip(steps, workflow.steps))


def _forbids(assertion: Any, tool: str) -> bool:
    return (getattr(assertion, "type", None) == "tool_not_called"
            and normalize_tool_name(assertion.name) == tool)


def label_call(workflow: Any, step: Mapping[str, Any], step_index: int, call_id: str) -> str:
    """trap | expected | unlabelled for one call found in step `step_index`.

    `trap` needs the scorer's own verdict: a `tool_not_called` for this tool in
    the step or in `success` that FAILS on a context holding just this call (so
    a probe exemption, where one exists, is honoured exactly as it was scored).
    """
    call = next(c for c in step.get("tool_calls") or [] if c.get("id") == call_id)
    tool = normalize_tool_name(call.get("name", ""))
    ctx = AssertionContext(
        response_text="", tool_calls=[call],
        tool_results=[r for r in step.get("tool_results") or []
                      if r.get("tool_call_id") == call_id],
        skills_routed=[], artifacts=[], task_ids=[],
    )
    wf_step = workflow.steps[step_index]
    for assertion in (*wf_step.assertions, *workflow.success.assertions):
        if _forbids(assertion, tool) and not evaluate_assertion(assertion, ctx)[0]:
            return TRAP
    if any(normalize_tool_name(te.name) == tool for te in wf_step.expected_tools):
        return EXPECTED
    return UNLABELLED


def _span_ids(trace_path: Path | None, thread_id: int, rows: Sequence[Any]) -> dict[str, str]:
    """tool_call_id -> the call's own trace span id, for one thread's rows.

    trace_harvest keys a call by its span run id when the tool's output is not a
    ToolMessage — a tool that raised — so those calls join only through the span.
    """
    since = min(row.occurred_at for row in rows) - timedelta(minutes=1)
    spans = read_spans(trace_path, thread_id, since) or []
    return {s.tool_call_id: s.id for s in spans if s.run_type == "tool" and s.tool_call_id}


def arena_labels(thread: Any, rows: Sequence[Any], *, arena_root: Path,
                 workflows: Mapping[str, Any], trace_path: Path | None = None) -> dict[int, str]:
    """audit id -> arena label for one arena thread's rows."""
    parsed = parse_title(thread.title)
    workflow = workflows.get(parsed[0]) if parsed else None
    if parsed is None or workflow is None or thread.arena_run_id is None:
        return {row.id: NO_MATCH for row in rows}
    wf_id, model = parsed
    located: dict[str, tuple[Mapping[str, Any], int]] = {}
    match_dir = Path(arena_root) / str(thread.arena_run_id) / wf_id / model
    for path in sorted(match_dir.glob("*/transcript*.json")):
        try:
            transcript = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not same_era(transcript, workflow):
            continue
        for index, step in enumerate(transcript["steps"]):
            for call in step.get("tool_calls") or []:
                if call.get("id"):
                    located.setdefault(call["id"], (step, index))
    out: dict[int, str] = {}
    span_ids: dict[str, str] | None = None      # read lazily: most threads never miss
    for row in rows:
        key = row.tool_call_id or ""
        hit = located.get(key)
        if hit is None and located:
            if span_ids is None:
                span_ids = _span_ids(trace_path, thread.id, rows)
            key = span_ids.get(key, "")
            hit = located.get(key)
        if hit is None:
            out[row.id] = NO_MATCH
            continue
        name = next(c.get("name", "") for c in hit[0]["tool_calls"] if c.get("id") == key)
        out[row.id] = (label_call(workflow, hit[0], hit[1], key)
                       if normalize_tool_name(name) == normalize_tool_name(row.tool_name)
                       else NO_MATCH)
    return out


def hitl_labels(session, rows: Sequence[Any]) -> dict[int, str]:
    """audit id -> approved | rejected, from the call's latest hitl_decision row."""
    out: dict[int, str] = {}
    for row in rows:
        decision = (
            session.query(AgentActionAudit.status)
            .filter(AgentActionAudit.kind == "hitl_decision",
                    AgentActionAudit.thread_id == row.thread_id,
                    AgentActionAudit.tool_call_id == row.tool_call_id)
            .order_by(AgentActionAudit.id.desc())
            .first()
        )
        if decision is not None and decision[0] in (APPROVED, REJECTED):
            out[row.id] = decision[0]
    return out


def primary_label(arena: str | None, hitl: str | None) -> str:
    if arena in (TRAP, EXPECTED):
        return arena
    if hitl is not None:
        return hitl
    return arena or UNLABELLED


def spread(rows: Sequence[Any], n: int | None) -> list[Any]:
    """`n` items evenly spaced over an oldest-first list (deterministic, and not
    all from the earliest runs). None = all."""
    if n is None or len(rows) <= n:
        return list(rows)
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)]


# --- select ------------------------------------------------------------------

def select_cases(session, *, tools: Iterable[str] | None, kinds: Iterable[str],
                 workflow_ids: Iterable[str] | None = None, since: datetime | None = None,
                 limit: int | None = None, arena_root: Path,
                 workflows: Mapping[str, Any] | None = None, settings=None) -> dict[str, Any]:
    cfg = settings or get_settings()
    validate_policy(GUARD_POLICY, _RISK_LEVEL_BY_TOOL)
    validate_sweep_policy(SWEEP_POLICY, _RISK_LEVEL_BY_TOOL)
    registry = {w.id: w for w in list_workflows()} if workflows is None else dict(workflows)
    kinds = set(kinds)
    tools = None if tools is None else sorted(set(tools))
    rows = eligible_rows(session, kinds=kinds, tools=tools, since=since).all()
    threads = {t.id: t for t in session.query(AgentThread)
               .filter(AgentThread.id.in_({r.thread_id for r in rows})).all()} if rows else {}
    if workflow_ids:
        wanted = set(workflow_ids)
        rows = [r for r in rows if (parse_title(threads[r.thread_id].title) or ("",))[0] in wanted]
    by_thread: dict[int, list[Any]] = defaultdict(list)
    for row in rows:
        by_thread[row.thread_id].append(row)
    trace = Path(cfg.trace_db_path)
    arena: dict[int, str] = {}
    for thread_id, group in by_thread.items():
        if threads[thread_id].source == ARENA:
            arena.update(arena_labels(threads[thread_id], group, arena_root=arena_root,
                                      workflows=registry, trace_path=trace))
    hitl = hitl_labels(session, rows)
    cases = []
    for row in rows:
        parsed = parse_title(threads[row.thread_id].title)
        a, h = arena.get(row.id), hitl.get(row.id)
        cases.append({
            "audit_id": row.id, "thread_id": row.thread_id, "tool_call_id": row.tool_call_id,
            "tool": row.tool_name, "occurred_at": row.occurred_at.isoformat(),
            "thread_source": threads[row.thread_id].source,
            "workflow": parsed[0] if parsed else None, "model_id": parsed[1] if parsed else None,
            "arena_label": a, "hitl_label": h, "label": primary_label(a, h),
        })
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for case in cases:
        buckets[(case["tool"], case["label"])].append(case)
    chosen = sorted((c for key in sorted(buckets) for c in spread(buckets[key], limit)),
                    key=lambda c: (c["occurred_at"], c["audit_id"]))
    counts: dict[str, Counter] = defaultdict(Counter)
    for case in chosen:
        counts[case["tool"]][case["label"]] += 1
    header = {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "policy_sha256": policy_sha256(),
        "model": cfg.system_one_model,
        "filters": {"tools": tools, "kinds": sorted(kinds),
                    "workflows": sorted(workflow_ids) if workflow_ids else None,
                    "since": since.isoformat() if since else None,
                    "limit_per_tool_label": limit},
        "trace_db": str(trace), "trace_db_present": trace.is_file(),
        "counts": {tool: dict(sorted(c.items())) for tool, c in sorted(counts.items())},
        "total": len(chosen),
        "spend_estimate": {"requests": len(chosen),
                           "seconds": round(len(chosen) * SECONDS_PER_ROW),
                           "usd": round(len(chosen) * USD_PER_ROW, 2)},
    }
    return {"header": header, "cases": chosen}


# --- score -------------------------------------------------------------------

def _still_due(cases: Sequence[Mapping[str, Any]]) -> int:
    keys = {(c["thread_id"], c["tool_call_id"]) for c in cases}
    with database.SessionLocal() as session:
        scored = {
            (thread_id, call_id)
            for thread_id, call_id in session.query(AgentToolGuardVerdict.thread_id,
                                                    AgentToolGuardVerdict.tool_call_id)
            .filter(AgentToolGuardVerdict.tool_call_id.in_({k[1] for k in keys}))
            .all()
        }
    return len(keys - scored)


def score_cases(cases_path: Path, *, post: Any = None, trace_path: Any = None,
                settings=None, out=print) -> int:
    """Score every case not yet in the verdict table, sequentially, stopping at
    the first outage. Exit 0 = done, 1 = an outage left cases due (re-run to
    resume), 2 = the policy moved since select (a new wording is a new select)."""
    data = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    selected, current = data["header"]["policy_sha256"], policy_sha256()
    if selected != current:
        out(f"policy moved since select ({selected[:12]} -> {current[:12]}); "
            "a new wording is a new `select` (D10)")
        return 2
    cfg = settings or get_settings()
    due = _still_due(data["cases"])
    out(f"{due} of {len(data['cases'])} cases due: ~{round(due * SECONDS_PER_ROW)} s, "
        f"~${due * USD_PER_ROW:.2f}")
    tally: Counter = Counter()
    outage: str | None = None
    for case in data["cases"]:
        with database.SessionLocal() as session:
            row = session.get(AgentActionAudit, case["audit_id"])
            if row is None:
                tally["missing"] += 1
                continue
            result = score_audit_row(session, row, settings=cfg, post=post, trace_path=trace_path)
        if result.outage is not None:
            outage = result.outage
            break
        tally["already scored" if result.already_scored else result.stored.verdict] += 1
    out(f"{dict(tally)}; still due: {_still_due(data['cases'])}")
    if outage is not None:
        out(f"outage: {outage} — re-run `score` to resume")
        return 1
    return 0


# --- report ------------------------------------------------------------------

def _bucket(verdict: AgentToolGuardVerdict) -> str:
    """Never pool fidelities (D7); a live row is its own bucket."""
    return verdict.state_fidelity or verdict.source


def collect(cases: Sequence[Mapping[str, Any]]) -> tuple[list[dict], Counter]:
    keys = {(c["thread_id"], c["tool_call_id"]) for c in cases}
    with database.SessionLocal() as session:
        verdicts = {
            (v.thread_id, v.tool_call_id): v
            for v in session.query(AgentToolGuardVerdict)
            .filter(AgentToolGuardVerdict.tool_call_id.in_({k[1] for k in keys})).all()
            if (v.thread_id, v.tool_call_id) in keys
        }
        records: list[dict] = []
        coverage: Counter = Counter()
        for case in cases:
            v = verdicts.get((case["thread_id"], case["tool_call_id"]))
            if v is None:
                coverage[(case["tool"], "no row (still due)")] += 1
                continue
            if v.verdict == "unscored":
                coverage[(case["tool"], f"unscored:{v.unscored_reason}")] += 1
            else:
                coverage[(case["tool"], f"scored @ {_bucket(v)}")] += 1
            records.append({**case, "verdict_id": v.id, "verdict": v.verdict,
                            "source": v.source, "fidelity": _bucket(v), "persona": v.persona,
                            "unscored_reason": v.unscored_reason,
                            "predicates": list(v.predicates_json or [])})
    return records, coverage


def summarise(records: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Per tool x predicate x label x fidelity: n, min / median / max, count >= threshold."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for record in records:
        for p in record["predicates"]:
            groups[(record["tool"], p["key"], record["label"], record["fidelity"])].append(p)
    rows = []
    for (tool, key, label, fidelity), preds in sorted(groups.items()):
        probabilities = [p["probability"] for p in preds]
        rows.append({
            "tool": tool, "predicate": key, "label": label, "fidelity": fidelity,
            "n": len(probabilities), "min": min(probabilities),
            "median": statistics.median(probabilities), "max": max(probabilities),
            "at_or_above": sum(1 for p in preds if p["probability"] >= p["threshold"]),
            "threshold": preds[0]["threshold"],
        })
    return rows


def separations(summary: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """median(trap) - median(expected), per tool x predicate x fidelity, where both exist."""
    index = {(r["tool"], r["predicate"], r["fidelity"], r["label"]): r for r in summary}
    out = []
    for (tool, key, fidelity, label), trap in sorted(index.items()):
        expected = index.get((tool, key, fidelity, EXPECTED))
        if label != TRAP or expected is None:
            continue
        out.append({"tool": tool, "predicate": key, "fidelity": fidelity,
                    "trap_median": trap["median"], "expected_median": expected["median"],
                    "separation": round(trap["median"] - expected["median"], 2),
                    "n_trap": trap["n"], "n_expected": expected["n"]})
    return out


def _run_line(header: Mapping[str, Any]) -> str:
    return (f"_policy `{header['policy_sha256'][:12]}` · {header['model']} · selected "
            f"{header['created_at']} · {header['total']} cases — {HEADLINE}_")


def render_report(header: Mapping[str, Any], summary: Sequence[Mapping[str, Any]],
                  seps: Sequence[Mapping[str, Any]], coverage: Counter) -> str:
    run = _run_line(header)
    lines = [
        f"# Guard sweep evidence — {header['created_at'][:10]}", "",
        f"> {HEADLINE} Model-written text under one harness's policies. `expected` is not "
        "`correct`: it says the tool was on the step's list, not that the call was right.", "",
        f"Filters: `{json.dumps(header['filters'], sort_keys=True)}` · trace DB present: "
        f"{header['trace_db_present']}", "",
        "## Coverage", "", run, "", "| tool | outcome | n |", "|---|---|---:|",
    ]
    lines += [f"| {tool} | {outcome} | {n} |" for (tool, outcome), n in sorted(coverage.items())]
    lines += ["", "## Separation — median(trap) − median(expected), per fidelity", "", run, "",
              "| tool | predicate | fidelity | trap median (n) | expected median (n) | separation |",
              "|---|---|---|---:|---:|---:|"]
    lines += [f"| {s['tool']} | {s['predicate']} | {s['fidelity']} | {s['trap_median']:.2f} "
              f"({s['n_trap']}) | {s['expected_median']:.2f} ({s['n_expected']}) | "
              f"{s['separation']:+.2f} |" for s in seps] or ["| — | — | — | — | — | — |"]
    lines += ["", "## Distributions", ""]
    for tool in sorted({r["tool"] for r in summary}):
        lines += [f"### {tool}", "", run, "",
                  "| predicate | label | fidelity | n | min | median | max | ≥ threshold |",
                  "|---|---|---|---:|---:|---:|---:|---:|"]
        lines += [f"| {r['predicate']} | {r['label']} | {r['fidelity']} | {r['n']} | "
                  f"{r['min']:.2f} | {r['median']:.2f} | {r['max']:.2f} | "
                  f"{r['at_or_above']}/{r['n']} |" for r in summary if r["tool"] == tool]
        lines.append("")
    return "\n".join(lines)


def report_cases(cases_path: Path) -> tuple[Path, Path]:
    cases_path = Path(cases_path)
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    records, coverage = collect(data["cases"])
    summary = summarise(records)
    md_path = cases_path.parent / "report.md"
    json_path = cases_path.parent / "verdicts.json"
    md_path.write_text(render_report(data["header"], summary, separations(summary), coverage),
                       encoding="utf-8")
    json_path.write_text(json.dumps(records, indent=2, ensure_ascii=False, default=str),
                         encoding="utf-8")
    return md_path, json_path


# --- CLI ---------------------------------------------------------------------

def _csv(text: str) -> list[str]:
    return [part.strip() for part in text.split(",") if part.strip()]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sel = sub.add_parser("select")
    which = sel.add_mutually_exclusive_group(required=True)
    which.add_argument("--tools", type=_csv)
    which.add_argument("--all-tools", action="store_true")
    sel.add_argument("--kinds", type=_csv, required=True, help=f"subset of {sorted(KINDS)}")
    sel.add_argument("--workflow", action="append", dest="workflows")
    sel.add_argument("--since", type=datetime.fromisoformat)
    size = sel.add_mutually_exclusive_group(required=True)
    size.add_argument("--limit", type=int, help="cases per (tool, label)")
    size.add_argument("--all", action="store_true")
    sel.add_argument("--out", type=Path, required=True)
    sel.add_argument("--artifacts", type=Path, default=None,
                     help="arena artifact root (default: <artifact_dir>/arena)")
    for name in ("score", "report"):
        sub.add_parser(name).add_argument("cases", type=Path)
    args = parser.parse_args(argv)

    if args.command == "select":
        target = args.out / "cases.json"
        if target.exists():
            print(f"{target} exists; a new selection is a new directory (D10)")
            return 2
        arena_root = args.artifacts or Path(get_settings().artifact_dir) / "arena"
        with database.SessionLocal() as session:
            data = select_cases(session, tools=None if args.all_tools else args.tools,
                                kinds=args.kinds, workflow_ids=args.workflows, since=args.since,
                                limit=None if args.all else args.limit, arena_root=arena_root)
        args.out.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps(data["header"], indent=2, ensure_ascii=False))
        print(f"wrote {target} — commit it BEFORE running `score` (D10)")
        return 0
    if args.command == "score":
        return score_cases(args.cases)
    md_path, json_path = report_cases(args.cases)
    print(f"wrote {md_path} and {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
