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


def test_system_prompt_carries_anchor_discipline():
    from app.services.arena.judge import _build_prompt
    from app.golden_workflows.registry import get_workflow_bundle
    from app.golden_workflows.transcript import transcript_from_replay
    loaded = get_workflow_bundle("risk-manager-control-day")
    msgs = _build_prompt(transcript_from_replay(loaded), loaded)
    assert "anchor" in msgs[0]["content"].lower()


# ---------------------------------------------------------------------------
# Jury (judge_panel) — network-free via injected post_for
# ---------------------------------------------------------------------------


def _post_for_fixed(scores_by_model):
    """post_for factory: each model scores every rubric point with a fixed value
    (int) or per-point list."""
    import re

    def factory(model_id):
        def post(payload):
            points = re.findall(r"^\d+\.\s+(.*)$", payload["messages"][1]["content"], re.M)
            sc = scores_by_model[model_id]
            vals = sc if isinstance(sc, list) else [sc] * len(points)
            return json.dumps({"rubric_scores": [
                {"point": p, "score": vals[i], "rationale": ""} for i, p in enumerate(points)],
                "overall_notes": "", "diagnosis": ""})
        return post
    return factory


class TestJuryPanel:
    def test_panel_averages_and_stdev(self):
        from app.services.arena.judge import judge_panel
        loaded, transcript, _ = _flagship_setup()
        r = judge_panel(transcript, loaded,
                        judge_models=["deepseek-v4-pro", "claude-opus-4-8", "qwen-3-7-max"],
                        post_for=_post_for_fixed(
                            {"deepseek-v4-pro": 60, "claude-opus-4-8": 90, "qwen-3-7-max": 30}),
                        retries=0)
        assert r.subjective_mode == "panel"
        assert r.judged_score == 60.0            # mean of 60/90/30
        assert round(r.judged_stdev, 1) == 24.5  # population stdev
        assert len(r.per_judge) == 3

    def test_panel_rubric_points_are_per_point_mean(self):
        """Judges disagree per point → panel rubric must be by-label mean, not judge[0]."""
        from app.services.arena.judge import judge_panel
        loaded, transcript, _ = _flagship_setup()
        r = judge_panel(transcript, loaded,
                        judge_models=["deepseek-v4-pro", "claude-opus-4-8"], min_judges=2,
                        post_for=_post_for_fixed(
                            {"deepseek-v4-pro": [80, 20], "claude-opus-4-8": [20, 80]}),
                        retries=0)
        assert [round(s["score"], 1) for s in r.rubric_scores] == [50.0, 50.0]  # NOT [80, 20]

    def test_panel_excludes_contestant(self):
        from app.services.arena.judge import judge_panel
        loaded, transcript, _ = _flagship_setup()
        r = judge_panel(transcript, loaded,
                        judge_models=["deepseek-v4-pro", "claude-opus-4-8", "qwen-3-7-max"],
                        exclude_model="claude-opus-4-8",
                        post_for=_post_for_fixed(
                            {"deepseek-v4-pro": 60, "qwen-3-7-max": 40}),
                        retries=0)
        assert {j["model"] for j in r.per_judge} == {"deepseek-v4-pro", "qwen-3-7-max"}

    def test_panel_postfailure_below_min_escalates_to_self_consistency(self):
        """3-judge pool, 2 fail, 1 survives → self-consistency, NOT proceed-on-one."""
        from app.services.arena.judge import judge_panel
        loaded, transcript, _ = _flagship_setup()
        calls = {"n": 0}

        def post_for(model_id):
            def post(payload):
                if model_id in ("claude-opus-4-8", "qwen-3-7-max"):
                    raise RuntimeError("402 quote_exceeded")
                calls["n"] += 1
                import re
                points = re.findall(r"^\d+\.\s+(.*)$", payload["messages"][1]["content"], re.M)
                return json.dumps({"rubric_scores": [
                    {"point": p, "score": 55, "rationale": ""} for p in points],
                    "overall_notes": "", "diagnosis": ""})
            return post
        r = judge_panel(transcript, loaded,
                        judge_models=["deepseek-v4-pro", "claude-opus-4-8", "qwen-3-7-max"],
                        min_judges=2, self_consistency_k=3, post_for=post_for, retries=0)
        assert r.subjective_mode == "self_consistency"
        assert calls["n"] == 3   # k samples on the surviving deepseek judge

    def test_panel_zero_survivors_is_missing(self):
        from app.services.arena.judge import judge_panel
        loaded, transcript, _ = _flagship_setup()

        def post_for(model_id):
            def post(payload):
                raise RuntimeError("402 quote_exceeded")
            return post
        r = judge_panel(transcript, loaded,
                        judge_models=["deepseek-v4-pro", "claude-opus-4-8"],
                        post_for=post_for, retries=0)
        assert r.judge_missing and r.subjective_mode == "missing" and r.judged_score is None
