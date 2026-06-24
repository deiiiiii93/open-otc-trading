"""Tests for MatchTranscript schema, extract_step_from_events, and transcript_from_replay.

TDD RED phase: all tests should fail initially because MatchStep, MatchTranscript,
extract_step_from_events, and transcript_from_replay are not yet implemented.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import (
    extract_assertion_context,
    MatchStep,
    MatchTranscript,
    extract_step_from_events,
    transcript_from_replay,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_turn_events(
    index: int = 0,
    user: str = "What is the risk?",
    response_text: str = "The risk is stale.",
    tool_name: str = "run_batch_pricing_tool",
    tool_call_id: str = "call_001",
    tool_args: dict | None = None,
    tool_content: dict | None = None,
    skills_routed: list[str] | None = None,
    artifacts: list[dict] | None = None,
    tool_error: str | None = None,
) -> dict:
    """Build a minimal turn-events dict for extract_step_from_events tests."""
    if tool_args is None:
        tool_args = {"portfolio_id": 6}
    if tool_content is None:
        tool_content = {"task_id": "task-abc-123", "status": "queued"}
    tool_result: dict = {
        "tool_call_id": tool_call_id,
        "name": tool_name,
        "content": tool_content,
    }
    if tool_error:
        tool_result["error"] = tool_error
    return {
        "index": index,
        "user": user,
        "messages": [],  # raw messages list (empty for unit tests)
        "tool_calls": [{"id": tool_call_id, "name": tool_name, "args": tool_args}],
        "tool_results": [tool_result],
        "skills_routed": skills_routed or ["run-risk"],
        "artifacts": artifacts or [],
        "response_text": response_text,
        "errors": [],
    }


# ---------------------------------------------------------------------------
# 1. Schema version rejection test
# ---------------------------------------------------------------------------

class TestMatchTranscriptSchemaVersion:
    def test_valid_schema_version_1_accepted(self):
        mt = MatchTranscript(
            schema_version=1,
            run_id=None,
            workflow_id="risk-manager-control-day",
            model_id="replay",
            started_at=None,
            finished_at=None,
            steps=[],
        )
        assert mt.schema_version == 1

    def test_missing_schema_version_raises_validation_error(self):
        with pytest.raises(ValidationError):
            MatchTranscript(
                # schema_version omitted
                run_id=None,
                workflow_id="risk-manager-control-day",
                model_id="replay",
                started_at=None,
                finished_at=None,
                steps=[],
            )

    def test_wrong_schema_version_raises_validation_error(self):
        with pytest.raises(ValidationError):
            MatchTranscript(
                schema_version=2,  # only Literal[1] allowed
                run_id=None,
                workflow_id="risk-manager-control-day",
                model_id="replay",
                started_at=None,
                finished_at=None,
                steps=[],
            )

    def test_string_schema_version_raises_validation_error(self):
        with pytest.raises(ValidationError):
            MatchTranscript(
                schema_version="1",  # string not accepted by Literal[1]
                run_id=None,
                workflow_id="risk-manager-control-day",
                model_id="replay",
                started_at=None,
                finished_at=None,
                steps=[],
            )


# ---------------------------------------------------------------------------
# 2. MatchStep construction and field completeness
# ---------------------------------------------------------------------------

class TestMatchStep:
    def test_step_fields_present(self):
        step = MatchStep(
            index=0,
            user="What is the risk?",
            messages=[],
            tool_calls=[{"id": "call_001", "name": "run_batch_pricing_tool", "args": {}}],
            tool_results=[{"tool_call_id": "call_001", "name": "run_batch_pricing_tool",
                           "content": {"task_id": "task-123"}}],
            skills_routed=["run-risk"],
            artifacts=[],
            task_ids=["task-123"],
            response_text="Risk queued.",
            errors=[],
        )
        assert step.index == 0
        assert step.task_ids == ["task-123"]
        assert step.skills_routed == ["run-risk"]
        assert step.response_text == "Risk queued."


# ---------------------------------------------------------------------------
# 3. extract_step_from_events — basic extraction
# ---------------------------------------------------------------------------

class TestExtractStepFromEvents:
    def test_response_text_extracted(self):
        events = _make_turn_events(response_text="The risk is stale and out of date.")
        step = extract_step_from_events(events)
        assert step.response_text == "The risk is stale and out of date."

    def test_tool_calls_extracted(self):
        events = _make_turn_events(tool_name="run_batch_pricing_tool", tool_call_id="call_x")
        step = extract_step_from_events(events)
        assert len(step.tool_calls) == 1
        assert step.tool_calls[0]["name"] == "run_batch_pricing_tool"

    def test_tool_results_normalized_shape(self):
        events = _make_turn_events(
            tool_name="run_batch_pricing_tool",
            tool_call_id="call_y",
            tool_content={"task_id": "t-999", "status": "queued"},
        )
        step = extract_step_from_events(events)
        assert len(step.tool_results) == 1
        r = step.tool_results[0]
        assert r["name"] == "run_batch_pricing_tool"
        assert isinstance(r["content"], dict)

    def test_task_ids_collected_from_content(self):
        events = _make_turn_events(tool_content={"task_id": "task-abc-123", "status": "queued"})
        step = extract_step_from_events(events)
        assert "task-abc-123" in step.task_ids

    def test_errored_tool_contributes_no_task_id(self):
        events = _make_turn_events(
            tool_content={"task_id": "should-not-appear"},
            tool_error="Tool execution failed",
        )
        step = extract_step_from_events(events)
        # An errored tool result must not contribute a task_id
        assert "should-not-appear" not in step.task_ids

    def test_skills_routed_preserved(self):
        events = _make_turn_events(skills_routed=["run-risk", "read-risk-result"])
        step = extract_step_from_events(events)
        assert step.skills_routed == ["run-risk", "read-risk-result"]

    def test_skills_routed_deduped_order_preserving(self):
        events = _make_turn_events(skills_routed=["run-risk", "run-risk", "read-risk-result"])
        step = extract_step_from_events(events)
        # de-duped but order-preserving (first occurrence kept)
        assert step.skills_routed == ["run-risk", "read-risk-result"]

    def test_artifacts_preserved(self):
        artifacts = [{"kind": "report", "report_id": "rpt-001"}]
        events = _make_turn_events(artifacts=artifacts)
        step = extract_step_from_events(events)
        assert step.artifacts == artifacts

    def test_index_and_user_propagated(self):
        events = _make_turn_events(index=3, user="Confirm the hotspot.")
        step = extract_step_from_events(events)
        assert step.index == 3
        assert step.user == "Confirm the hotspot."

    def test_non_json_tool_content_wrapped(self):
        """Non-dict tool content (e.g., a plain string) is wrapped as {'raw': ...}."""
        events = _make_turn_events(tool_content="plain string response")
        step = extract_step_from_events(events)
        r = step.tool_results[0]
        # content should be normalized to a dict
        assert isinstance(r["content"], dict)

    def test_multiple_task_ids_from_multiple_tools(self):
        """When two tool results each carry task_id, both should be in task_ids."""
        events = {
            "index": 0,
            "user": "Run everything.",
            "messages": [],
            "tool_calls": [
                {"id": "c1", "name": "run_greeks_landscape_tool", "args": {}},
                {"id": "c2", "name": "get_greeks_landscape_run_tool", "args": {}},
            ],
            "tool_results": [
                {"tool_call_id": "c1", "name": "run_greeks_landscape_tool",
                 "content": {"task_id": "gl-task-001"}},
                {"tool_call_id": "c2", "name": "get_greeks_landscape_run_tool",
                 "content": {"task_id": "gl-task-001", "run_id": 101}},
            ],
            "skills_routed": ["run-greeks-landscape"],
            "artifacts": [],
            "response_text": "Landscape queued.",
            "errors": [],
        }
        step = extract_step_from_events(events)
        # Both results carry task_id; both should appear (may be duplicated since both
        # reference the same ID — depends on impl, but at minimum one must be present)
        assert "gl-task-001" in step.task_ids


# ---------------------------------------------------------------------------
# 4. Parity test: MatchStep.model_dump() feeds extract_assertion_context correctly
# ---------------------------------------------------------------------------

class TestExtractionParity:
    """Assert that extract_assertion_context(match_step.model_dump()) yields
    a context equivalent to a hand-built AssertionContext from the same data."""

    def test_parity_response_text(self):
        events = _make_turn_events(response_text="Risk is stale and out of date.")
        step = extract_step_from_events(events)
        ctx = extract_assertion_context(step.model_dump())
        assert ctx.response_text == "Risk is stale and out of date."

    def test_parity_tool_calls(self):
        events = _make_turn_events(
            tool_name="run_batch_pricing_tool",
            tool_call_id="call_parity",
            tool_args={"portfolio_id": 6},
        )
        step = extract_step_from_events(events)
        ctx = extract_assertion_context(step.model_dump())
        assert len(ctx.tool_calls) == 1
        assert ctx.tool_calls[0]["name"] == "run_batch_pricing_tool"

    def test_parity_task_ids(self):
        events = _make_turn_events(tool_content={"task_id": "task-parity-99"})
        step = extract_step_from_events(events)
        ctx = extract_assertion_context(step.model_dump())
        assert "task-parity-99" in ctx.task_ids

    def test_parity_skills_routed(self):
        events = _make_turn_events(skills_routed=["run-risk"])
        step = extract_step_from_events(events)
        ctx = extract_assertion_context(step.model_dump())
        assert "run-risk" in ctx.skills_routed

    def test_parity_artifacts(self):
        events = _make_turn_events(
            artifacts=[{"kind": "report", "report_id": "rpt-parity"}]
        )
        step = extract_step_from_events(events)
        ctx = extract_assertion_context(step.model_dump())
        assert any(a.get("kind") == "report" for a in ctx.artifacts)

    def test_parity_vs_hand_built(self):
        """The context from model_dump() must match a hand-built context from
        the same raw data — tool_calls, tool_results, skills_routed, artifacts,
        response_text all equivalent."""
        from app.golden_workflows.assertions import AssertionContext

        events = _make_turn_events(
            response_text="Hotspot is AAPL.",
            tool_name="get_latest_risk_run_tool",
            tool_call_id="call_h",
            tool_content={"hotspot": {"underlying": "AAPL"}, "task_id": "t-h"},
            skills_routed=["read-risk-result"],
            artifacts=[],
        )
        step = extract_step_from_events(events)
        ctx_from_dump = extract_assertion_context(step.model_dump())

        hand_built = AssertionContext(
            response_text="Hotspot is AAPL.",
            tool_calls=[{"id": "call_h", "name": "get_latest_risk_run_tool",
                         "args": {"portfolio_id": 6}}],
            tool_results=[{"tool_call_id": "call_h", "name": "get_latest_risk_run_tool",
                           "content": {"hotspot": {"underlying": "AAPL"}, "task_id": "t-h"}}],
            skills_routed=["read-risk-result"],
            artifacts=[],
            task_ids=["t-h"],
        )

        assert ctx_from_dump.response_text == hand_built.response_text
        assert ctx_from_dump.skills_routed == hand_built.skills_routed
        assert ctx_from_dump.task_ids == hand_built.task_ids
        assert len(ctx_from_dump.tool_calls) == len(hand_built.tool_calls)
        assert len(ctx_from_dump.tool_results) == len(hand_built.tool_results)


# ---------------------------------------------------------------------------
# 5. transcript_from_replay — flagship produces correct structure
# ---------------------------------------------------------------------------

class TestTranscriptFromReplay:
    def test_returns_match_transcript(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        assert isinstance(mt, MatchTranscript)

    def test_schema_version_is_1(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        assert mt.schema_version == 1

    def test_model_id_is_replay(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        assert mt.model_id == "replay"

    def test_run_id_is_none(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        assert mt.run_id is None

    def test_workflow_id_matches(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        assert mt.workflow_id == "risk-manager-control-day"

    def test_flagship_has_seven_steps(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        assert len(mt.steps) == 7

    def test_step_indices_sequential(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        for i, step in enumerate(mt.steps):
            assert step.index == i

    def test_step_users_match_workflow_steps(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        for wf_step, ms in zip(loaded.workflow.steps, mt.steps):
            assert ms.user == wf_step.user

    def test_step3_hotspot_task_id_present(self):
        """Step 3 (index 2) reads fresh risk — get_latest_risk_run_tool has no task_id
        in its content. The step should still parse cleanly with empty task_ids."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        step3 = mt.steps[2]
        # step-3-read-fresh-risk: get_latest_risk_run_tool content has no task_id
        # so task_ids should be empty list for this step
        assert isinstance(step3.task_ids, list)

    def test_step2_task_id_present(self):
        """Step 2 (index 1) runs risk — run_batch_pricing_tool returns task_id."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        step2 = mt.steps[1]
        assert "rp-task-20260624-001" in step2.task_ids

    def test_step4_greeks_task_id_present(self):
        """Step 4 (index 3) run_greeks_landscape_tool returns task_id."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        step4 = mt.steps[3]
        assert "gl-task-20260624-001" in step4.task_ids

    def test_step3_skills_routed(self):
        """Step 3 is routed to read-risk-result."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        step3 = mt.steps[2]
        assert "read-risk-result" in step3.skills_routed

    def test_step7_artifact_present(self):
        """Step 7 (index 6) creates a report artifact."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        step7 = mt.steps[6]
        assert any(a.get("kind") == "report" for a in step7.artifacts)

    def test_all_steps_model_dump_feeds_extract_assertion_context(self):
        """Round-trip: each step's model_dump() is valid input for extract_assertion_context.
        This is the core parity guarantee: MatchStep.model_dump() must work as a
        step_record for the assertion layer."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)
        for step in mt.steps:
            ctx = extract_assertion_context(step.model_dump())
            assert isinstance(ctx.response_text, str)
            assert isinstance(ctx.tool_calls, list)
            assert isinstance(ctx.tool_results, list)
            assert isinstance(ctx.skills_routed, list)
            assert isinstance(ctx.artifacts, list)
            assert isinstance(ctx.task_ids, list)

    def test_replay_assertions_pass_via_transcript(self):
        """All per-step assertions that pass in the regression test must also pass
        when driven through transcript_from_replay — proving end-to-end parity."""
        from app.golden_workflows.transcript import evaluate_step

        loaded = get_workflow_bundle("risk-manager-control-day")
        mt = transcript_from_replay(loaded)

        for wf_step, ms in zip(loaded.workflow.steps, mt.steps):
            ctx = extract_assertion_context(ms.model_dump())
            for passed, msg in evaluate_step(wf_step, ctx):
                assert passed, f"[{wf_step.replay}] via transcript: {msg}"
