"""Shared transcript extraction utilities for golden-workflow evaluation.

No dependency on the test tree (no _scripted_graph imports).
"""
from __future__ import annotations

from app.golden_workflows.assertions import AssertionContext, evaluate_assertion


def extract_assertion_context(step_record: dict) -> AssertionContext:
    """Build an AssertionContext from a single graph-turn result dict.

    step_record keys (all optional, default to empty):
      - tool_calls:    list[dict]  — AI-issued tool calls
      - tool_results:  list[dict]  — normalised tool results {name, tool_call_id, content, error?}
      - skills_routed: list[str]   — skill names routed this turn
      - artifacts:     list[dict]  — artefact dicts {kind, ...}
      - response_text: str         — final AI response text

    task_ids: the content.task_id of each tool_result that carries one.
    """
    tool_calls = list(step_record.get("tool_calls") or [])
    tool_results = list(step_record.get("tool_results") or [])
    skills_routed = list(step_record.get("skills_routed") or [])
    artifacts = list(step_record.get("artifacts") or [])
    response_text = step_record.get("response_text") or ""

    task_ids: list[str] = []
    for r in tool_results:
        content = r.get("content")
        if isinstance(content, dict):
            tid = content.get("task_id")
            if tid:
                task_ids.append(str(tid))

    return AssertionContext(
        response_text=response_text,
        tool_calls=tool_calls,
        tool_results=tool_results,
        skills_routed=skills_routed,
        artifacts=artifacts,
        task_ids=task_ids,
    )


def evaluate_step(step, ctx: AssertionContext) -> list[tuple[bool, str]]:
    """Evaluate all assertions for a single workflow step.

    Returns a list of (passed, message) tuples, one per assertion.
    """
    return [evaluate_assertion(a, ctx) for a in step.assertions]


def evaluate_success(success, session_ctx: AssertionContext) -> list[tuple[bool, str]]:
    """Evaluate all success assertions against the full-session context.

    Returns a list of (passed, message) tuples, one per assertion.
    """
    return [evaluate_assertion(a, session_ctx) for a in success.assertions]
