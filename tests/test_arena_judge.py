"""Tests for arena judge (GPT-5.5 via injected fake poster).

TDD approach — only the ``post`` callable is faked; the real parser and retry
logic are exercised.

Test groups:
  a. Valid structured JSON → JudgeResult with mean score, judge_missing=False
  b. Malformed JSON first, valid on second attempt → success (retry path)
  c. Always-malformed JSON → retry exhausted → judge_missing=True
  d. Missing/duplicate rubric points → treated as parse failure → retry/exhaust
  e. Empty rubric → judge_missing=True immediately (no post call)
  f. Exception from poster → retry / exhaust
"""
from __future__ import annotations

import json
import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.judge import judge_match, JudgeResult, _collect_rubric_points


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_response(rubric_points: list[str], score: float = 80.0) -> str:
    """Build a valid judge JSON response for the given rubric points."""
    scores = [
        {"point": pt, "score": score, "rationale": "Looks good."}
        for pt in rubric_points
    ]
    return json.dumps({"rubric_scores": scores, "overall_notes": "All good."})


def _flagship_setup():
    loaded = get_workflow_bundle("risk-manager-control-day")
    transcript = transcript_from_replay(loaded)
    rubric_points = _collect_rubric_points(loaded)
    return loaded, transcript, rubric_points


# ---------------------------------------------------------------------------
# a. Valid JSON → mean score
# ---------------------------------------------------------------------------


class TestValidResponse:
    def test_valid_json_returns_mean_score(self):
        loaded, transcript, rubric_points = _flagship_setup()

        responses = iter([_make_valid_response(rubric_points, score=80.0)])
        result = judge_match(transcript, loaded, post=lambda p: next(responses), retries=0)

        assert isinstance(result, JudgeResult)
        assert result.judge_missing is False
        assert result.judged_score is not None
        assert abs(result.judged_score - 80.0) < 0.01
        assert len(result.rubric_scores) == len(rubric_points)

    def test_mean_is_average_of_varied_scores(self):
        loaded, transcript, rubric_points = _flagship_setup()

        # Build varied scores: 60, 70, 80, 90 alternating
        scores_vals = [60.0 + (i % 4) * 10 for i in range(len(rubric_points))]
        expected_mean = sum(scores_vals) / len(scores_vals)

        rubric_scores = [
            {"point": pt, "score": sv, "rationale": "ok"}
            for pt, sv in zip(rubric_points, scores_vals)
        ]
        raw = json.dumps({"rubric_scores": rubric_scores, "overall_notes": "mixed"})
        result = judge_match(transcript, loaded, post=lambda p: raw, retries=0)

        assert result.judge_missing is False
        assert abs(result.judged_score - expected_mean) < 0.01

    def test_notes_returned(self):
        loaded, transcript, rubric_points = _flagship_setup()
        raw = json.dumps({
            "rubric_scores": [{"point": pt, "score": 90.0, "rationale": "r"} for pt in rubric_points],
            "overall_notes": "Excellent performance."
        })
        result = judge_match(transcript, loaded, post=lambda p: raw, retries=0)
        assert "Excellent" in result.notes

    def test_diagnosis_returned_when_present(self):
        loaded, transcript, rubric_points = _flagship_setup()
        raw = json.dumps({
            "rubric_scores": [{"point": pt, "score": 0.0, "rationale": "r"} for pt in rubric_points],
            "overall_notes": "Poor.",
            "diagnosis": "Never engaged the expected skills; stalled asking for the named profile.",
        })
        result = judge_match(transcript, loaded, post=lambda p: raw, retries=0)
        assert "stalled" in result.diagnosis

    def test_diagnosis_defaults_empty_when_omitted(self):
        """Backward compatibility: a payload without 'diagnosis' still parses,
        with diagnosis defaulting to ''."""
        loaded, transcript, rubric_points = _flagship_setup()
        raw = json.dumps({
            "rubric_scores": [{"point": pt, "score": 90.0, "rationale": "r"} for pt in rubric_points],
            "overall_notes": "Good.",
        })
        result = judge_match(transcript, loaded, post=lambda p: raw, retries=0)
        assert result.judge_missing is False
        assert result.diagnosis == ""

    def test_markdown_fenced_json_is_accepted(self):
        """Judge response wrapped in ```json ... ``` is still parsed correctly."""
        loaded, transcript, rubric_points = _flagship_setup()
        inner = _make_valid_response(rubric_points, score=75.0)
        fenced = f"```json\n{inner}\n```"
        result = judge_match(transcript, loaded, post=lambda p: fenced, retries=0)
        assert result.judge_missing is False
        assert abs(result.judged_score - 75.0) < 0.01


# ---------------------------------------------------------------------------
# b. Malformed first, valid on retry
# ---------------------------------------------------------------------------


class TestRetrySuccess:
    def test_malformed_then_valid_succeeds(self):
        loaded, transcript, rubric_points = _flagship_setup()

        call_count = [0]

        def fake_post(payload):
            call_count[0] += 1
            if call_count[0] == 1:
                return "this is not json {"
            return _make_valid_response(rubric_points, score=70.0)

        result = judge_match(transcript, loaded, post=fake_post, retries=2)

        assert result.judge_missing is False
        assert abs(result.judged_score - 70.0) < 0.01
        assert call_count[0] == 2  # first failed, second succeeded

    def test_two_failures_then_valid_succeeds(self):
        loaded, transcript, rubric_points = _flagship_setup()

        call_count = [0]

        def fake_post(payload):
            call_count[0] += 1
            if call_count[0] < 3:
                return "garbage"
            return _make_valid_response(rubric_points, score=65.0)

        result = judge_match(transcript, loaded, post=fake_post, retries=2)

        assert result.judge_missing is False
        assert abs(result.judged_score - 65.0) < 0.01
        assert call_count[0] == 3


# ---------------------------------------------------------------------------
# c. Always malformed → retry exhausted → judge_missing
# ---------------------------------------------------------------------------


class TestRetryExhausted:
    def test_always_malformed_returns_judge_missing(self):
        loaded, transcript, rubric_points = _flagship_setup()

        call_count = [0]

        def always_bad(payload):
            call_count[0] += 1
            return "not json at all"

        result = judge_match(transcript, loaded, post=always_bad, retries=2)

        assert result.judge_missing is True
        assert result.judged_score is None
        assert call_count[0] == 3  # 1 initial + 2 retries

    def test_always_exception_returns_judge_missing(self):
        loaded, transcript, rubric_points = _flagship_setup()

        call_count = [0]

        def always_raise(payload):
            call_count[0] += 1
            raise RuntimeError("Simulated timeout")

        result = judge_match(transcript, loaded, post=always_raise, retries=2)

        assert result.judge_missing is True
        assert result.judged_score is None
        assert call_count[0] == 3

    def test_judge_missing_note_mentions_attempts(self):
        loaded, transcript, _ = _flagship_setup()

        result = judge_match(transcript, loaded, post=lambda p: "bad", retries=1)

        assert result.judge_missing is True
        assert "2" in result.notes or "attempt" in result.notes.lower()


# ---------------------------------------------------------------------------
# d. Missing/duplicate/extra rubric points → parse failure
# ---------------------------------------------------------------------------


class TestRubricAlignment:
    def test_too_few_rubric_scores_causes_failure(self):
        loaded, transcript, rubric_points = _flagship_setup()

        # Only return first half of expected rubric points
        half = rubric_points[: len(rubric_points) // 2]
        bad_raw = json.dumps({
            "rubric_scores": [{"point": pt, "score": 80, "rationale": "ok"} for pt in half],
            "overall_notes": ""
        })
        result = judge_match(transcript, loaded, post=lambda p: bad_raw, retries=0)
        assert result.judge_missing is True

    def test_too_many_rubric_scores_causes_failure(self):
        loaded, transcript, rubric_points = _flagship_setup()

        # Return extra rubric point
        extra = rubric_points + ["This extra point was not asked for"]
        bad_raw = json.dumps({
            "rubric_scores": [{"point": pt, "score": 80, "rationale": "ok"} for pt in extra],
            "overall_notes": ""
        })
        result = judge_match(transcript, loaded, post=lambda p: bad_raw, retries=0)
        assert result.judge_missing is True

    def test_duplicate_rubric_points_causes_failure(self):
        loaded, transcript, rubric_points = _flagship_setup()

        # Replace second entry with a duplicate of the first
        dup = [rubric_points[0]] + rubric_points[1:]
        dup[1] = rubric_points[0]  # duplicate first point

        # Pad/truncate to same length as original
        dup = (dup + rubric_points)[: len(rubric_points)]

        bad_raw = json.dumps({
            "rubric_scores": [{"point": pt, "score": 80, "rationale": "ok"} for pt in dup],
            "overall_notes": ""
        })
        result = judge_match(transcript, loaded, post=lambda p: bad_raw, retries=0)
        # duplicate → parse failure → judge_missing
        assert result.judge_missing is True

    def test_invalid_score_out_of_range_causes_failure(self):
        loaded, transcript, rubric_points = _flagship_setup()

        bad_scores = [
            {"point": pt, "score": 150, "rationale": "way too high"}  # score > 100
            for pt in rubric_points
        ]
        bad_raw = json.dumps({"rubric_scores": bad_scores, "overall_notes": ""})
        result = judge_match(transcript, loaded, post=lambda p: bad_raw, retries=0)
        assert result.judge_missing is True

    def test_missing_rubric_scores_key_causes_failure(self):
        loaded, transcript, _ = _flagship_setup()
        bad_raw = json.dumps({"overall_notes": "No scores here"})
        result = judge_match(transcript, loaded, post=lambda p: bad_raw, retries=0)
        assert result.judge_missing is True

    def test_rubric_score_entry_missing_score_key_causes_failure(self):
        loaded, transcript, rubric_points = _flagship_setup()
        bad_scores = [
            {"point": pt, "rationale": "no score key"}
            for pt in rubric_points
        ]
        bad_raw = json.dumps({"rubric_scores": bad_scores, "overall_notes": ""})
        result = judge_match(transcript, loaded, post=lambda p: bad_raw, retries=0)
        assert result.judge_missing is True

    def test_retry_malformed_then_correct_alignment_succeeds(self):
        loaded, transcript, rubric_points = _flagship_setup()

        call_count = [0]

        def flip_post(payload):
            call_count[0] += 1
            if call_count[0] == 1:
                # Wrong number of points
                return json.dumps({"rubric_scores": [], "overall_notes": ""})
            return _make_valid_response(rubric_points, score=55.0)

        result = judge_match(transcript, loaded, post=flip_post, retries=2)
        assert result.judge_missing is False
        assert abs(result.judged_score - 55.0) < 0.01


# ---------------------------------------------------------------------------
# e. Empty rubric → skip immediately
# ---------------------------------------------------------------------------


class TestEmptyRubric:
    def test_empty_rubric_skips_post_call(self):
        """When loaded workflow has no rubric points, post is never called."""
        from unittest.mock import MagicMock
        from app.golden_workflows.transcript import MatchTranscript

        mock_workflow = MagicMock()
        mock_workflow.steps = []  # No step rubrics
        mock_workflow.success.rubric = []  # No success rubric
        mock_workflow.title = "Empty"
        mock_workflow.objective = "none"

        mock_loaded = MagicMock()
        mock_loaded.workflow = mock_workflow

        transcript = MatchTranscript(
            schema_version=1,
            run_id=None,
            workflow_id="empty",
            model_id="test",
            started_at=None,
            finished_at=None,
            steps=[],
        )

        post_calls = [0]

        def counting_post(payload):
            post_calls[0] += 1
            return "{}"

        result = judge_match(transcript, mock_loaded, post=counting_post, retries=2)

        assert result.judge_missing is True
        assert result.judged_score is None
        assert post_calls[0] == 0, "post should never be called for empty rubric"
