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

Labels (F5) come from each arena match's transcript, joined by tool_call_id,
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
from datetime import datetime, timezone
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


def arena_labels(thread: Any, rows: Sequence[Any], *, arena_root: Path,
                 workflows: Mapping[str, Any]) -> dict[int, str]:
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
    for row in rows:
        hit = located.get(row.tool_call_id or "")
        if hit is None:
            out[row.id] = NO_MATCH
            continue
        name = next(c.get("name", "") for c in hit[0]["tool_calls"]
                    if c.get("id") == row.tool_call_id)
        out[row.id] = (label_call(workflow, hit[0], hit[1], row.tool_call_id)
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
    arena: dict[int, str] = {}
    for thread_id, group in by_thread.items():
        if threads[thread_id].source == ARENA:
            arena.update(arena_labels(threads[thread_id], group, arena_root=arena_root,
                                      workflows=registry))
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
    trace = Path(cfg.trace_db_path)
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
