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

total == fixed denominator (31 for the flagship).
score = 100 * passed / total  (float, rounded to 1 dp at the storage boundary).
"""
from __future__ import annotations

from app.golden_workflows.assertions import (
    AssertionContext,
    evaluate_assertion,
    match_tool,
)
from app.golden_workflows.schema import normalize_skill, ToolExpectation
from app.golden_workflows.transcript import (
    MatchTranscript,
    extract_assertion_context,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


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

        checks: list[dict] = []

        # 1. expected_skill
        want = normalize_skill(wf_step.expected_skill)
        routed = ts.skills_routed if ts is not None else []
        skill_ok = any(normalize_skill(s) == want for s in routed)
        checks.append({
            "kind": "skill",
            "label": f"skill: {wf_step.expected_skill}",
            "passed": skill_ok,
            "detail": "" if skill_ok else f"routed {list(routed)}",
        })

        # 2. ToolExpectations
        for te in wf_step.expected_tools:
            ok, msg = match_tool(te, step_ctx.tool_calls)
            checks.append({
                "kind": "tool",
                "label": f"tool: {te.name}",
                "passed": ok,
                "detail": "" if ok else (msg or "not called"),
            })

        # 3. Per-step assertions
        for assertion in wf_step.assertions:
            ok, msg = evaluate_assertion(assertion, step_ctx)
            checks.append({
                "kind": "assertion",
                "label": _assertion_label(assertion),
                "passed": ok,
                "detail": "" if ok else msg,
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
        points (7+10+8+6=31 for the flagship).
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
    return {"passed": passed, "total": total, "steps": steps, "success": success}


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
