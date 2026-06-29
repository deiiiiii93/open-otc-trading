"""Deterministic regression proof: flagship replay through the real _ScriptedGraph.

Drives ``risk-manager-control-day`` through the scripted-graph adapter
(not live LLM routing) to prove the format→graph wiring is correct.

Phase 2 (LLM arena eval) will test real routing; here we only verify that
the transcript extraction and assertion logic produce green results when fed
the canned fixture data.

_ScriptedGraph lives in tests/_scripted_graph.py (test-only); it is
imported HERE, never by app code.
"""
from __future__ import annotations

from langchain_core.messages import HumanMessage

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import (
    extract_assertion_context,
    evaluate_step,
    evaluate_success,
)
from app.golden_workflows.assertions import match_tools_subsequence
from _scripted_graph import _ScriptedGraph, _ai  # noqa: PLC0415  # test-only helper


# ---------------------------------------------------------------------------
# Adapter functions (keep _ScriptedGraph out of app code)
# ---------------------------------------------------------------------------

def build_scripted_graph_from_replay(loaded) -> _ScriptedGraph:
    """Build a _ScriptedGraph whose script replays each step's fixture entry.

    For each step we look up the ReplayEntry via step.replay and produce a
    plain dict carrying the keys that extract_assertion_context expects.
    We do NOT use AIMessage wrappers in the dict — the extractor works on
    plain dicts, not LangChain message objects.
    """
    script = []
    for step in loaded.workflow.steps:
        entry = loaded.fixtures.replay[step.replay]
        script.append({
            "tool_calls": entry.ai.get("tool_calls", []),
            "tool_results": entry.tool_results,
            "skills_routed": entry.skills_routed,
            "artifacts": entry.artifacts,
            "response_text": entry.response_text,
        })
    return _ScriptedGraph(script)


def replay_record_from_graph_turn(turn_output: dict) -> dict:
    """Normalise a graph.invoke() result to the standard step-record shape.

    _ScriptedGraph returns the script entry verbatim, which already carries
    the keys we built in build_scripted_graph_from_replay.  Pass through
    (or re-key defensively).
    """
    return {
        "tool_calls": turn_output.get("tool_calls", []),
        "tool_results": turn_output.get("tool_results", []),
        "skills_routed": turn_output.get("skills_routed", []),
        "artifacts": turn_output.get("artifacts", []),
        "response_text": turn_output.get("response_text", ""),
    }


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------

def test_flagship_replays_green_through_scripted_graph():
    """Drive the flagship workflow through the real _ScriptedGraph and assert
    every step's tools + assertions pass, then verify session-level success."""
    loaded = get_workflow_bundle("risk-manager-control-day")

    # Build a real _ScriptedGraph — MANDATORY per spec
    graph = build_scripted_graph_from_replay(loaded)

    session_records: list[dict] = []

    for step in loaded.workflow.steps:
        # .invoke() is the driving API (_ScriptedGraph has no run_turn)
        turn = graph.invoke({"messages": [HumanMessage(content=step.user)]})
        rec = replay_record_from_graph_turn(turn)
        ctx = extract_assertion_context(rec)

        # 1. Tool subsequence
        ok, msg = match_tools_subsequence(step.expected_tools, ctx.tool_calls)
        assert ok, f"[{step.replay}] tool subsequence: {msg}"

        # 2. Per-step assertions
        for passed, m in evaluate_step(step, ctx):
            assert passed, f"[{step.replay}] assertion failed: {m}"

        session_records.append(rec)

    # 3. Session-level success assertions (uses all turns aggregated)
    session_ctx = extract_assertion_context({
        "tool_calls": [c for r in session_records for c in r["tool_calls"]],
        "tool_results": [t for r in session_records for t in r["tool_results"]],
        "skills_routed": [s for r in session_records for s in r["skills_routed"]],
        "artifacts": [a for r in session_records for a in r["artifacts"]],
        "response_text": " ".join(r["response_text"] for r in session_records),
    })

    for passed, m in evaluate_success(loaded.workflow.success, session_ctx):
        assert passed, f"[success] assertion failed: {m}"
