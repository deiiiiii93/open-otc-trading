"""Reproducible objective scoring for arena matches.

Pure module — no network, no LLM.  Computes:

    objective_score(transcript, loaded) -> (score_0_100, passed, total)

using the §3.4/§3.5 assertion engine against the match transcript.

Scoring rules (§6.4):
  - +1 per step for expected_skill hit (skill appears in step.skills_routed)
  - +1 per ToolExpectation that passes match_tool / match_tools_subsequence
  - +1 per step Assertion that passes evaluate_assertion
  - +1 per success.assertions entry (evaluated against the SESSION context —
    all tool_calls, tool_results, skills_routed, artifacts, task_ids merged)

Steps with ``expected_skill: null`` emit NO skill check (the skills_routed
channel is structurally blind for repeat-skill steps — the runtime never
re-reads an already-loaded SKILL.md).

Per-step assertions with ``scope == "session"`` (response_quotes_tool_value)
are evaluated against a CUMULATIVE context: tool_results from steps 0..i,
response_text from step i only — so a grounding question can reference a
result computed in an earlier step.

Every check carries a derived ``axis`` (procedural / adherence / grounding /
synthesis) for reporting; the aggregate stays flat +1 per check.

total == fixed denominator (39 for the flagship: 6+11+21+1).
score = 100 * passed / total  (float, rounded to 1 dp at the storage boundary).
"""
from __future__ import annotations

from app.golden_workflows.assertions import (
    AssertionContext,
    evaluate_assertion,
    match_tool,
)
from app.golden_workflows.schema import normalize_skill
from app.golden_workflows.transcript import (
    MatchTranscript,
    extract_assertion_context,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


# Reporting axis per assertion type; check kinds "skill" and "tool" are always
# procedural. Unknown/new types default to procedural.
_AXIS_BY_TYPE = {
    "skill_routed": "procedural",
    "skills_routed_sequence": "procedural",
    "tools_routed_sequence": "procedural",
    "task_returned_id": "procedural",
    "tool_called": "adherence",
    "tool_not_called": "adherence",
    "response_contains": "adherence",
    "tool_result_path": "grounding",
    "response_quotes_tool_value": "grounding",
    "response_quotes_value": "grounding",
    "artifact_exists": "synthesis",
    "artifact_contains": "synthesis",
}


def _axis_for_assertion(assertion) -> str:
    return _AXIS_BY_TYPE.get(assertion.type, "procedural")


# Deterministic objective tie-break priority (spec D5a): when two models have
# equal mean objective, break the tie by sub-axis pass-rate in this order —
# grounding first (hardest to fake), then adherence, synthesis, procedural.
# Subjective NEVER breaks the tie.
_AXIS_TIEBREAK_PRIORITY = ("grounding", "adherence", "synthesis", "procedural")


def objective_tiebreak_key(axes: dict) -> tuple:
    """Sort key (ascending → better first) from aggregate objective sub-axis
    tallies ``{axis: {passed, total}}``. Higher pass-rate on a higher-priority
    axis sorts earlier (negated fractions)."""
    def frac(ax: str) -> float:
        a = axes.get(ax) or {}
        total = a.get("total", 0)
        return (a.get("passed", 0) / total) if total else 0.0
    return tuple(-frac(ax) for ax in _AXIS_TIEBREAK_PRIORITY)


def designed_par(workflow) -> int:
    """Designed complete-run tool-call count for the EFF stat. Explicit
    ``par_tool_calls`` wins; else sum of per-step expected tools."""
    explicit = getattr(workflow, "par_tool_calls", None)
    if explicit is not None:
        return explicit
    return sum(len(s.expected_tools) for s in workflow.steps)


# --- Ability card (spec B) --------------------------------------------------
# Stat ← axis map (spec B1). One axis → one stat; EFF is computed, not an axis.
_STAT_BY_AXIS = {
    "grounding": "GRD",
    "adherence": "ADH",
    "synthesis": "SYN",
    "procedural": "PRC",
}
# OVR weights (spec B2). Sum to 1.00. JDG is NOT here (advisory, out of OVR).
_OVR_WEIGHTS = {"GRD": 0.32, "ADH": 0.26, "SYN": 0.16, "EFF": 0.16, "PRC": 0.10}
# Correctness axes gating EFF's C multiplier (procedural excluded on purpose —
# EFF rewards outcome-per-cost, not ceremony).
_CORRECTNESS_AXES = ("grounding", "adherence", "synthesis")
# Card tie-break priority (spec B5): GRD → ADH → SYN → EFF → PRC. Never JDG.
_CARD_TIEBREAK_PRIORITY = ("GRD", "ADH", "SYN", "EFF", "PRC")


def _stat_from_tally(tally: dict) -> int:
    total = tally.get("total", 0)
    return round(99 * tally.get("passed", 0) / total) if total else 0


def _correctness(axes: dict) -> float:
    passed = sum(axes.get(ax, {}).get("passed", 0) for ax in _CORRECTNESS_AXES)
    total = sum(axes.get(ax, {}).get("total", 0) for ax in _CORRECTNESS_AXES)
    return (passed / total) if total else 0.0


def _card_position(stats: dict) -> str:
    """Presentation-only archetype from the dominant stat (spec B6)."""
    keys = ("GRD", "ADH", "SYN", "EFF", "PRC")
    vals = [stats[k] for k in keys]
    if max(vals) - min(vals) <= 8:
        return "All-rounder"
    dominant = max(keys, key=lambda k: stats[k])
    return {
        "GRD": "Sniper", "ADH": "Anchor", "PRC": "Anchor",
        "SYN": "Playmaker", "EFF": "Playmaker",
    }[dominant]


def card_from_axes(axes: dict, tool_calls: int, par: int,
                   judged: float | None = None) -> dict:
    """Derive the ability card from objective axis tallies + tool-call count.

    Pure. Used at write time (task.py) and derive-on-read (store.py) — the single
    stat/OVR formula, so the drilldown card and the leaderboard OVR never disagree.
    """
    stats = {stat: 0 for stat in ("GRD", "ADH", "SYN", "PRC")}
    for axis, stat in _STAT_BY_AXIS.items():
        stats[stat] = _stat_from_tally(axes.get(axis, {}))
    c = _correctness(axes)
    # Efficiency ratio: lean (fewer calls than par) is NOT penalized (capped at 1),
    # bloat scales it down. But ZERO calls when the workflow designs tools (par > 0)
    # is non-execution, NOT leanness — EFF is 0 so a transcript that merely quotes
    # the fixture-truth numbers (value-only grounding) without running the workflow
    # cannot earn a mid-card OVR via a free efficiency pass. par == 0 (no tools
    # designed) with 0 calls is legitimately full efficiency.
    if tool_calls > 0:
        ratio = min(1.0, par / tool_calls)
    elif par > 0:
        ratio = 0.0
    else:
        ratio = 1.0
    stats["EFF"] = round(c * ratio * 99)
    ovr = round(sum(_OVR_WEIGHTS[k] * stats[k] for k in _OVR_WEIGHTS))
    return {"ovr": ovr, "stats": stats, "jdg": judged,
            "position": _card_position(stats)}


def card_tiebreak_key(stats: dict) -> tuple:
    """Descending sort key (better first) over GRD→ADH→SYN→EFF→PRC."""
    return tuple(-stats.get(k, 0) for k in _CARD_TIEBREAK_PRIORITY)


def ability_card(transcript, loaded, judged: float | None = None) -> dict:
    """Convenience wrapper: evaluate the transcript once and build the card."""
    bd = objective_breakdown(transcript, loaded)
    heuristic = diagnose_heuristic(transcript, loaded)
    return card_from_axes(bd["axes"], heuristic["tool_calls"],
                          designed_par(loaded.workflow), judged=judged)


def _session_context(transcript: MatchTranscript) -> AssertionContext:
    """Merge all steps into a single session-level AssertionContext."""
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    skills_routed: list[str] = []
    artifacts: list[dict] = []
    task_ids: list[str] = []

    for step in transcript.steps:
        tool_calls.extend(step.tool_calls)
        tool_results.extend(step.tool_results)
        skills_routed.extend(step.skills_routed)
        artifacts.extend(step.artifacts)
        task_ids.extend(step.task_ids)

    return AssertionContext(
        response_text=" ".join(
            s.response_text for s in transcript.steps if s.response_text
        ),
        tool_calls=tool_calls,
        tool_results=tool_results,
        skills_routed=skills_routed,
        artifacts=artifacts,
        task_ids=task_ids,
    )


def _assertion_label(a) -> str:
    """Human-readable label for an Assertion (for the score breakdown)."""
    t = a.type
    if t == "skills_routed_sequence":
        return "skills routed in order: " + " → ".join(a.names)
    if t == "tools_routed_sequence":
        return "tools called in order: " + " → ".join(a.names)
    if t == "task_returned_id":
        return f"{a.tool} returned a task id"
    if t == "artifact_exists":
        return f"artifact produced (kind={a.kind})"
    if t == "response_contains":
        return "response contains " + " / ".join(a.any_of)
    if t == "tool_result_path":
        op = (
            f"== {a.equals!r}" if a.equals is not None
            else f">= {a.gte}" if a.gte is not None
            else f"<= {a.lte}" if a.lte is not None
            else "is not null" if a.is_not_null is not None
            else "exists"
        )
        return f"{a.tool} result {a.path} {op}"
    if t == "skill_routed":
        return f"skill routed: {a.name}"
    if t == "tool_called":
        return f"tool called: {a.name}"
    if t == "tool_not_called":
        return f"tool NOT called: {a.name}"
    if t == "artifact_contains":
        return f"artifact({a.kind}) contains " + " / ".join(a.any_of)
    if t == "response_quotes_tool_value":
        return f"response quotes {a.tool} {a.path}"
    if t == "response_quotes_value":
        return f"response quotes value {a.value}"
    return t


def _evaluate_objective(
    transcript: MatchTranscript,
    loaded,
) -> tuple[list[dict], list[dict]]:
    """Evaluate every manifest check once and return structured results.

    Returns ``(steps, success)`` where each is built from check dicts
    ``{kind, label, passed, detail}`` (detail is the failure reason, "" if
    passed). This is the single source of truth for both ``objective_score``
    (which counts checks) and ``objective_breakdown`` (which formats them).
    """
    workflow = loaded.workflow
    steps: list[dict] = []
    # Cumulative tool_results (steps 0..i) for scope=="session" assertions.
    cumulative_results: list[dict] = []

    for i, wf_step in enumerate(workflow.steps):
        if i < len(transcript.steps):
            ts = transcript.steps[i]
            step_ctx = extract_assertion_context(ts.model_dump())
        else:
            ts = None
            step_ctx = AssertionContext(
                response_text="", tool_calls=[], tool_results=[],
                skills_routed=[], artifacts=[], task_ids=[],
            )
        cumulative_results.extend(step_ctx.tool_results)

        checks: list[dict] = []

        # 1. expected_skill (skipped entirely for null-skill steps)
        if wf_step.expected_skill is not None:
            want = normalize_skill(wf_step.expected_skill)
            routed = ts.skills_routed if ts is not None else []
            skill_ok = any(normalize_skill(s) == want for s in routed)
            checks.append({
                "kind": "skill",
                "label": f"skill: {wf_step.expected_skill}",
                "passed": skill_ok,
                "detail": "" if skill_ok else f"routed {list(routed)}",
                "axis": "procedural",
            })

        # 2. ToolExpectations
        for te in wf_step.expected_tools:
            ok, msg = match_tool(te, step_ctx.tool_calls)
            checks.append({
                "kind": "tool",
                "label": f"tool: {te.name}",
                "passed": ok,
                "detail": "" if ok else (msg or "not called"),
                "axis": "procedural",
            })

        # 3. Per-step assertions
        for assertion in wf_step.assertions:
            if getattr(assertion, "scope", "step") == "session":
                ctx = AssertionContext(
                    response_text=step_ctx.response_text,
                    tool_calls=step_ctx.tool_calls,
                    tool_results=list(cumulative_results),
                    skills_routed=step_ctx.skills_routed,
                    artifacts=step_ctx.artifacts,
                    task_ids=step_ctx.task_ids,
                )
            else:
                ctx = step_ctx
            ok, msg = evaluate_assertion(assertion, ctx)
            checks.append({
                "kind": "assertion",
                "label": _assertion_label(assertion),
                "passed": ok,
                "detail": "" if ok else msg,
                "axis": _axis_for_assertion(assertion),
            })

        steps.append({"index": i, "user": wf_step.user, "checks": checks})

    # 4. success.assertions against the merged SESSION context
    session_ctx = _session_context(transcript)
    success: list[dict] = []
    for assertion in workflow.success.assertions:
        ok, msg = evaluate_assertion(assertion, session_ctx)
        success.append({
            "kind": "assertion",
            "label": _assertion_label(assertion),
            "passed": ok,
            "detail": "" if ok else msg,
            "axis": _axis_for_assertion(assertion),
        })

    return steps, success


def _count(steps: list[dict], success: list[dict]) -> tuple[int, int]:
    checks = [c for s in steps for c in s["checks"]] + success
    return sum(1 for c in checks if c["passed"]), len(checks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def objective_score(
    transcript: MatchTranscript,
    loaded,
) -> tuple[float, int, int]:
    """Compute objective score from transcript vs workflow manifest.

    Returns:
        (score_0_100, passed, total)
        where score_0_100 = 100 * passed / total, total = sum of all manifest
        points (7+10+9+6=32 for the flagship).
    """
    steps, success = _evaluate_objective(transcript, loaded)
    passed, total = _count(steps, success)
    if total == 0:
        return (0.0, 0, 0)
    return (100.0 * passed / total, passed, total)


def objective_breakdown(
    transcript: MatchTranscript,
    loaded,
) -> dict:
    """Per-check pass/fail breakdown of the objective score.

    Returns ``{passed, total, steps:[{index,user,checks:[{kind,label,passed,
    detail}]}], success:[check]}`` — the data behind the aggregate score, so a
    reviewer can see exactly which manifest points the model won and lost.
    """
    steps, success = _evaluate_objective(transcript, loaded)
    passed, total = _count(steps, success)
    axes: dict[str, dict[str, int]] = {}
    for c in [c for s in steps for c in s["checks"]] + success:
        ax = c.get("axis", "procedural")
        slot = axes.setdefault(ax, {"passed": 0, "total": 0})
        slot["total"] += 1
        slot["passed"] += int(c["passed"])
    return {"passed": passed, "total": total, "steps": steps,
            "success": success, "axes": axes}


def diagnose_heuristic(
    transcript: MatchTranscript,
    loaded,
) -> dict:
    """Deterministic, network-free engagement summary for a match.

    Returns structured counts plus a one-line ``summary`` of the form
    ``"3/7 expected skills · 12 tool calls · 18/31 checks · 1 error"`` — the
    cheap, always-present half of a match diagnosis (the LLM narrative is the
    other half). Counts come from the same objective evaluation that produces
    the per-check breakdown, so the numbers never disagree with it.
    """
    steps, success = _evaluate_objective(transcript, loaded)
    passed, total = _count(steps, success)

    # Expected-skill hits: count the per-step "skill" checks that passed.
    skill_checks = [c for s in steps for c in s["checks"] if c["kind"] == "skill"]
    skills_hit = sum(1 for c in skill_checks if c["passed"])
    skills_total = len(skill_checks)

    tool_calls = sum(len(s.tool_calls) for s in transcript.steps)
    errors = sum(len(s.errors) for s in transcript.steps)

    parts = [
        f"{skills_hit}/{skills_total} expected skills",
        f"{tool_calls} tool call" + ("" if tool_calls == 1 else "s"),
        f"{passed}/{total} checks",
    ]
    if errors:
        parts.append(f"{errors} error" + ("" if errors == 1 else "s"))
    summary = " · ".join(parts)

    return {
        "summary": summary,
        "skills_hit": skills_hit,
        "skills_total": skills_total,
        "tool_calls": tool_calls,
        "checks_passed": passed,
        "checks_total": total,
        "errors": errors,
    }


def total_score(
    objective: float,
    judged: float | None,
    weights: dict | None = None,
    judge_missing: bool = False,
) -> float:
    """Blend objective + judge scores.

    Args:
        objective:     Objective score in [0, 100].
        judged:        Judge-assigned score in [0, 100], or None.
        weights:       Dict with keys "obj" and "judge" (default 0.5/0.5).
        judge_missing: If True (or judged is None), return objective only.

    Returns:
        Blended score, rounded to 1 decimal.
    """
    if weights is None:
        weights = {"obj": 0.5, "judge": 0.5}

    if judge_missing or judged is None:
        return round(objective, 1)

    blended = weights["obj"] * objective + weights["judge"] * judged
    return round(blended, 1)
