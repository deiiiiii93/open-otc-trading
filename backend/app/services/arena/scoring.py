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
    workflow = loaded.workflow
    passed = 0
    total = 0

    for i, wf_step in enumerate(workflow.steps):
        # --- fetch matching transcript step (by index) ---
        if i < len(transcript.steps):
            ts = transcript.steps[i]
            step_ctx = extract_assertion_context(ts.model_dump())
        else:
            # Step missing → contribute zeros but still count denominator
            ts = None
            step_ctx = AssertionContext(
                response_text="",
                tool_calls=[],
                tool_results=[],
                skills_routed=[],
                artifacts=[],
                task_ids=[],
            )

        # 1. expected_skill (+1 if skill appears in step's skills_routed)
        total += 1
        want = normalize_skill(wf_step.expected_skill)
        if ts is not None and any(
            normalize_skill(s) == want for s in ts.skills_routed
        ):
            passed += 1

        # 2. ToolExpectation — one point per expected tool
        for te in wf_step.expected_tools:
            total += 1
            ok, _ = match_tool(te, step_ctx.tool_calls)
            if ok:
                passed += 1

        # 3. Per-step Assertions
        for assertion in wf_step.assertions:
            total += 1
            ok, _ = evaluate_assertion(assertion, step_ctx)
            if ok:
                passed += 1

    # 4. success.assertions — evaluated against the SESSION context
    session_ctx = _session_context(transcript)
    for assertion in workflow.success.assertions:
        total += 1
        ok, _ = evaluate_assertion(assertion, session_ctx)
        if ok:
            passed += 1

    if total == 0:
        return (0.0, 0, 0)

    score = 100.0 * passed / total
    return (score, passed, total)


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
