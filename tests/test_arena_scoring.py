"""Tests for arena objective scoring (pure, no network).

TDD approach — these tests were written before the implementation.

Test groups:
  1. Flagship pin: objective_score on a full replay == (100.0, 31, 31)
  2. total_score: judge_missing → returns objective; weight blend; edge cases
  3. Partial scoring: one step missing a skill, one tool miss
  4. Empty workflow (no steps, no success assertions) → (0.0, 0, 0) edge case
"""
from __future__ import annotations

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score, total_score


# ---------------------------------------------------------------------------
# 1. Flagship pin
# ---------------------------------------------------------------------------


class TestFlagshipPin:
    """The fully-passing replay must score exactly (100.0, 31, 31)."""

    def test_flagship_replay_scores_100_31_31(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)
        score, passed, total = objective_score(transcript, loaded)
        assert total == 31, f"expected denominator 31, got {total}"
        assert passed == 31, f"expected 31 passed, got {passed}"
        assert score == 100.0, f"expected 100.0, got {score}"

    def test_flagship_denominator_breakdown(self):
        """Verify point breakdown: 7 skills + 10 tools + 8 step assertions + 6 success."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        wf = loaded.workflow

        skill_points = len(wf.steps)  # one per step
        tool_points = sum(len(s.expected_tools) for s in wf.steps)
        step_assertion_points = sum(len(s.assertions) for s in wf.steps)
        success_points = len(wf.success.assertions)

        assert skill_points == 7
        assert tool_points == 10
        assert step_assertion_points == 8
        assert success_points == 6
        assert skill_points + tool_points + step_assertion_points + success_points == 31


# ---------------------------------------------------------------------------
# 2. total_score
# ---------------------------------------------------------------------------


class TestTotalScore:
    def test_judge_missing_returns_objective(self):
        result = total_score(75.0, None, judge_missing=True)
        assert result == 75.0

    def test_judged_none_returns_objective(self):
        result = total_score(60.0, None, judge_missing=False)
        assert result == 60.0

    def test_weight_blend_default(self):
        # 0.5 * 80 + 0.5 * 60 = 70.0
        result = total_score(80.0, 60.0, judge_missing=False)
        assert result == 70.0

    def test_weight_blend_custom(self):
        # 0.7 * 80 + 0.3 * 60 = 74.0
        result = total_score(80.0, 60.0, weights={"obj": 0.7, "judge": 0.3}, judge_missing=False)
        assert result == 74.0

    def test_rounding_to_1_decimal(self):
        # 0.5 * 77 + 0.5 * 80 = 78.5
        result = total_score(77.0, 80.0, judge_missing=False)
        assert result == 78.5

    def test_rounding_odd_blend(self):
        # 0.5 * 33 + 0.5 * 34 = 33.5
        result = total_score(33.0, 34.0, judge_missing=False)
        assert result == 33.5

    def test_judge_missing_flag_overrides_judged_score(self):
        """Even if judged is provided, judge_missing=True returns objective only."""
        result = total_score(50.0, 90.0, judge_missing=True)
        assert result == 50.0

    def test_perfect_score(self):
        result = total_score(100.0, 100.0, judge_missing=False)
        assert result == 100.0

    def test_zero_score(self):
        result = total_score(0.0, 0.0, judge_missing=False)
        assert result == 0.0


# ---------------------------------------------------------------------------
# 3. Partial scoring: zeroed transcript
# ---------------------------------------------------------------------------


class TestPartialScoring:
    """A blank transcript (no skills, no tools, no results) scores 0."""

    def test_blank_transcript_scores_zero(self):
        from app.golden_workflows.transcript import MatchTranscript, MatchStep

        loaded = get_workflow_bundle("risk-manager-control-day")
        wf = loaded.workflow

        # Build a transcript with the right number of steps but empty content
        steps = [
            MatchStep(
                index=i,
                user=wf.steps[i].user,
                messages=[],
                tool_calls=[],
                tool_results=[],
                skills_routed=[],
                artifacts=[],
                task_ids=[],
                response_text="",
                errors=[],
            )
            for i in range(len(wf.steps))
        ]
        transcript = MatchTranscript(
            schema_version=1,
            run_id=None,
            workflow_id=wf.id,
            model_id="test",
            started_at=None,
            finished_at=None,
            steps=steps,
        )
        score, passed, total = objective_score(transcript, loaded)
        assert total == 31
        assert passed == 0
        assert score == 0.0

    def test_missing_steps_counted_in_denominator(self):
        """Fewer steps in transcript → denominator still 31, passes reduced."""
        from app.golden_workflows.transcript import MatchTranscript

        loaded = get_workflow_bundle("risk-manager-control-day")
        # Transcript with no steps at all
        transcript = MatchTranscript(
            schema_version=1,
            run_id=None,
            workflow_id=loaded.workflow.id,
            model_id="test",
            started_at=None,
            finished_at=None,
            steps=[],
        )
        score, passed, total = objective_score(transcript, loaded)
        assert total == 31
        assert passed == 0
        assert score == 0.0


# ---------------------------------------------------------------------------
# 4. Empty total edge case
# ---------------------------------------------------------------------------


class TestEmptyWorkflow:
    """If a workflow has no steps and no success assertions, total==0 → (0.0,0,0)."""

    def test_zero_denominator_returns_zero_score(self):
        """Synthetic: inject a minimal workflow with no points."""
        from unittest.mock import MagicMock
        from app.golden_workflows.transcript import MatchTranscript

        # Fake workflow with empty steps and success
        mock_workflow = MagicMock()
        mock_workflow.steps = []
        mock_workflow.success.assertions = []

        mock_loaded = MagicMock()
        mock_loaded.workflow = mock_workflow

        transcript = MatchTranscript(
            schema_version=1,
            run_id=None,
            workflow_id="empty-test",
            model_id="test",
            started_at=None,
            finished_at=None,
            steps=[],
        )
        score, passed, total = objective_score(transcript, mock_loaded)
        assert total == 0
        assert passed == 0
        assert score == 0.0
