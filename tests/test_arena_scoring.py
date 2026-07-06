"""Tests for arena objective scoring (pure, no network).

TDD approach — these tests were written before the implementation.

Test groups:
  1. Flagship pin: objective_score on a full replay == (100.0, 39, 39)
  2. total_score: judge_missing → returns objective; weight blend; edge cases
  3. Partial scoring: one step missing a skill, one tool miss
  4. Empty workflow (no steps, no success assertions) → (0.0, 0, 0) edge case
"""
from __future__ import annotations

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import (
    objective_score,
    objective_breakdown,
    diagnose_heuristic,
    total_score,
)


# ---------------------------------------------------------------------------
# 1. Flagship pin
# ---------------------------------------------------------------------------


class TestFlagshipPin:
    """The fully-passing replay must score exactly (100.0, 39, 39)."""

    def test_flagship_replay_scores_100_39_39(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)
        score, passed, total = objective_score(transcript, loaded)
        assert total == 39, f"expected denominator 39, got {total}"
        assert passed == 39, f"expected 39 passed, got {passed}"
        assert score == 100.0, f"expected 100.0, got {score}"

    def test_flagship_denominator_breakdown(self):
        """Verify point breakdown: 6 skills + 11 tools + 21 step assertions + 1 success."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        wf = loaded.workflow

        skill_points = sum(1 for st in wf.steps if st.expected_skill is not None)
        tool_points = sum(len(s.expected_tools) for s in wf.steps)
        step_assertion_points = sum(len(s.assertions) for s in wf.steps)
        success_points = len(wf.success.assertions)

        assert skill_points == 6
        assert tool_points == 11
        assert step_assertion_points == 21
        assert success_points == 1
        assert skill_points + tool_points + step_assertion_points + success_points == 39

    def test_breakdown_matches_aggregate_and_shape(self):
        """objective_breakdown's passed/total equal objective_score, and every
        manifest check appears once with a kind/label/passed/detail."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)

        _score, passed, total = objective_score(transcript, loaded)
        bd = objective_breakdown(transcript, loaded)

        assert bd["passed"] == passed == 39
        assert bd["total"] == total == 39
        assert len(bd["steps"]) == len(loaded.workflow.steps)
        assert len(bd["success"]) == len(loaded.workflow.success.assertions)

        all_checks = [c for s in bd["steps"] for c in s["checks"]] + bd["success"]
        assert len(all_checks) == total
        for c in all_checks:
            assert set(c) == {"kind", "label", "passed", "detail", "axis"}
            assert c["kind"] in {"skill", "tool", "assertion"}
            assert c["passed"] is True  # full replay → every check passes
        # First step's first check is its expected skill, labelled readably.
        assert bd["steps"][0]["checks"][0]["kind"] == "skill"
        assert "read-risk-result" in bd["steps"][0]["checks"][0]["label"]

    def test_breakdown_records_failure_detail(self):
        """A check that fails carries passed=False and a non-empty detail."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)
        # Drop a step's skills_routed so its expected_skill check fails.
        transcript.steps[0].skills_routed = []
        bd = objective_breakdown(transcript, loaded)

        skill_check = bd["steps"][0]["checks"][0]
        assert skill_check["kind"] == "skill"
        assert skill_check["passed"] is False
        assert skill_check["detail"]  # non-empty reason
        assert bd["passed"] < bd["total"]


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
    """A blank transcript (no skills, no tools, no results) earns only the
    3 prohibition points (tool_not_called passes on inaction) — the v2 floor."""

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
        assert total == 39
        assert passed == 3  # the three tool_not_called prohibitions
        assert score == pytest.approx(100.0 * 3 / 39)

    def test_missing_steps_counted_in_denominator(self):
        """Fewer steps in transcript → denominator still 39, passes reduced."""
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
        assert total == 39
        assert passed == 3  # prohibition checks pass for absent steps too
        assert score == pytest.approx(100.0 * 3 / 39)


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


class TestDiagnoseHeuristic:
    """The deterministic engagement summary behind a match diagnosis."""

    def test_full_replay_summary_reports_all_engagement(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)
        diag = diagnose_heuristic(transcript, loaded)
        # A fully-passing replay: every expected skill hit, all 39 checks pass.
        assert diag["skills_hit"] == diag["skills_total"] == 6
        assert diag["checks_passed"] == diag["checks_total"] == 39
        assert diag["tool_calls"] > 0
        assert "6/6 expected skills" in diag["summary"]
        assert "39/39 checks" in diag["summary"]

    def test_empty_transcript_summary_reports_zero_engagement(self):
        """A model that never engaged: 0 skills, 0 tools, 0 checks — the exact
        shape behind a 0.0 match (e.g. the MiniMax over-caution stall)."""
        from app.golden_workflows.transcript import MatchTranscript

        loaded = get_workflow_bundle("risk-manager-control-day")
        empty = MatchTranscript(
            schema_version=1, run_id=None, workflow_id="risk-manager-control-day",
            model_id="stall", started_at=None, finished_at=None, steps=[],
        )
        diag = diagnose_heuristic(empty, loaded)
        assert diag["skills_hit"] == 0
        assert diag["tool_calls"] == 0
        assert diag["checks_passed"] == 3  # the tool_not_called prohibitions
        assert diag["checks_total"] == 39
        assert diag["summary"].startswith("0/6 expected skills · 0 tool calls · 3/39 checks")


# ---------------------------------------------------------------------------
# 5. Flagship v2: axis subtotals, null-skill steps, session-scope grounding
# ---------------------------------------------------------------------------


def _step_dict(index, user="u", **kw):
    base = dict(index=index, user=user, messages=[], tool_calls=[],
                tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                response_text="", errors=[])
    base.update(kw)
    return base


def _mini_loaded(steps_spec, success_assertions=()):
    """Minimal LoadedWorkflow stand-in from parsed pydantic models."""
    from unittest.mock import MagicMock
    from app.golden_workflows.schema import parse_workflow
    wf = parse_workflow({
        "id": "mini-test",
        "schema_version": 1,
        "persona": "risk_manager",
        "title": "t",
        "objective": "o",
        "fixtures": "f.json",
        "steps": steps_spec,
        "success": {"assertions": list(success_assertions), "rubric": []},
    })
    loaded = MagicMock()
    loaded.workflow = wf
    return loaded


def _transcript(workflow_id, step_dicts):
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    return MatchTranscript(
        schema_version=1, run_id=None, workflow_id=workflow_id, model_id="test",
        started_at=None, finished_at=None,
        steps=[MatchStep(**d) for d in step_dicts],
    )


_LANDSCAPE_RESULT = {"name": "get_greeks_landscape_run", "tool_call_id": "t1",
                     "content": {"landscape": [
                         {"spot_shift": 0.1, "delta": -220000, "gamma": -9600}]}}


class TestAxisAndScope:
    def test_null_skill_emits_no_check(self):
        loaded = _mini_loaded([
            {"user": "u", "expected_skill": None, "outcome": "o", "replay": "r"},
        ])
        transcript = _transcript("mini-test", [_step_dict(0)])
        bd = objective_breakdown(transcript, loaded)
        kinds = [c["kind"] for c in bd["steps"][0]["checks"]]
        assert "skill" not in kinds
        assert bd["total"] == 0

    def _scope_fixture(self, scope):
        assertion = {"type": "response_quotes_tool_value",
                     "tool": "get_greeks_landscape_run",
                     "path": "landscape[spot_shift=0.1].gamma",
                     "near": ["gamma"]}
        if scope is not None:
            assertion["scope"] = scope
        loaded = _mini_loaded([
            {"user": "run it", "expected_skill": None, "outcome": "o", "replay": "r1"},
            {"user": "read it", "expected_skill": None, "outcome": "o", "replay": "r2",
             "assertions": [assertion]},
        ])
        transcript = _transcript("mini-test", [
            _step_dict(0, tool_results=[_LANDSCAPE_RESULT]),
            _step_dict(1, response_text="gamma at +10% is -9,600"),
        ])
        return objective_breakdown(transcript, loaded)

    def test_session_scope_reads_earlier_step_result(self):
        bd = self._scope_fixture("session")
        check = bd["steps"][1]["checks"][-1]
        assert check["kind"] == "assertion"
        assert check["passed"] is True

    def test_step_scope_does_not_read_earlier_step(self):
        bd = self._scope_fixture(None)  # default scope: step
        check = bd["steps"][1]["checks"][-1]
        assert check["passed"] is False

    def test_axis_subtotals_sum(self):
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)
        bd = objective_breakdown(transcript, loaded)
        axes = bd["axes"]
        assert set(axes) <= {"procedural", "adherence", "grounding", "synthesis"}
        assert sum(v["total"] for v in axes.values()) == bd["total"] == 39
        assert sum(v["passed"] for v in axes.values()) == bd["passed"]
        assert axes["procedural"]["total"] == 22
        assert axes["adherence"]["total"] == 8
        assert axes["grounding"]["total"] == 5
        assert axes["synthesis"]["total"] == 4

    def test_step5_recompute_fails_but_refetch_passes(self):
        """Re-dispatching run_greeks_landscape in the grid step fails its
        tool_not_called check; a get_greeks_landscape_run re-fetch is allowed."""
        loaded = get_workflow_bundle("risk-manager-control-day")
        transcript = transcript_from_replay(loaded)
        grid_i = 4  # step 5 (0-based)

        recompute = transcript.model_copy(deep=True)
        recompute.steps[grid_i].tool_calls = [
            {"id": "x", "name": "run_greeks_landscape_tool", "args": {}}]
        bd = objective_breakdown(recompute, loaded)
        not_called = [c for c in bd["steps"][grid_i]["checks"]
                      if "run_greeks_landscape" in c["label"] and c["kind"] == "assertion"]
        assert not_called and not_called[0]["passed"] is False

        refetch = transcript.model_copy(deep=True)
        refetch.steps[grid_i].tool_calls = [
            {"id": "x", "name": "get_greeks_landscape_run_tool", "args": {"run_id": 101}}]
        bd2 = objective_breakdown(refetch, loaded)
        assert all(c["passed"] for c in bd2["steps"][grid_i]["checks"])


def test_objective_tiebreak_prefers_grounding():
    from app.services.arena.scoring import objective_tiebreak_key
    a = {"grounding": {"passed": 5, "total": 5}, "adherence": {"passed": 1, "total": 8}}
    b = {"grounding": {"passed": 1, "total": 5}, "adherence": {"passed": 8, "total": 8}}
    assert objective_tiebreak_key(a) < objective_tiebreak_key(b)  # a wins on grounding


# --- ability card: par (Spec B, Task 2) -------------------------------------
from app.services.arena import scoring
from app.golden_workflows.registry import get_workflow as _get_wf


def test_designed_par_defaults_to_expected_tools_sum():
    wf = _get_wf("risk-manager-control-day")
    assert scoring.designed_par(wf) == sum(len(s.expected_tools) for s in wf.steps)
    assert scoring.designed_par(wf) == 11


def test_designed_par_override_wins():
    from app.golden_workflows.schema import GoldenWorkflow
    wf = _get_wf("risk-manager-control-day")
    data = wf.model_dump()
    data["par_tool_calls"] = 99
    assert scoring.designed_par(GoldenWorkflow(**data)) == 99


# --- ability card: stats + OVR (Spec B, Task 3) -----------------------------
def test_card_from_axes_stats_and_ovr():
    axes = {
        "grounding": {"passed": 10, "total": 10},   # GRD 99
        "adherence": {"passed": 5, "total": 10},    # ADH round(49.5)=50
        "synthesis": {"passed": 3, "total": 3},     # SYN 99
        "procedural": {"passed": 0, "total": 5},    # PRC 0
    }
    card = scoring.card_from_axes(axes, tool_calls=11, par=11, judged=42.0)
    s = card["stats"]
    assert s["GRD"] == 99 and s["SYN"] == 99 and s["PRC"] == 0
    assert s["ADH"] == 50
    assert s["EFF"] == 77   # C=18/23=0.7826, ratio 1 → round(77.48)=77
    assert card["ovr"] == 73
    assert card["jdg"] == 42.0


def test_card_eff_penalizes_bloat_not_leanness():
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    assert scoring.card_from_axes(axes, 11, 11)["stats"]["EFF"] == 99   # ratio 1.0
    assert scoring.card_from_axes(axes, 22, 11)["stats"]["EFF"] == 50   # ratio 0.5
    assert scoring.card_from_axes(axes, 5, 11)["stats"]["EFF"] == 99    # leaner NOT penalized


def test_card_do_nothing_scores_low_eff():
    axes = {"grounding": {"passed": 0, "total": 10}, "adherence": {"passed": 0, "total": 10},
            "synthesis": {"passed": 0, "total": 3}, "procedural": {"passed": 3, "total": 3}}
    assert scoring.card_from_axes(axes, 0, 11)["stats"]["EFF"] == 0   # C=0 → no gaming


def test_card_zero_tools_par_positive_gets_zero_eff():
    # Value-only grounding can pass WITHOUT tool calls; a zero-tool transcript that
    # quotes the truth numbers must NOT earn a free EFF pass (non-execution, not lean).
    # GRD still credits the (context-derived) numbers — that is the point-2 fix — but
    # EFF and PRC go to 0, so the card honestly reads "strong numbers, no execution".
    axes = {"grounding": {"passed": 9, "total": 10}, "adherence": {"passed": 8, "total": 10},
            "synthesis": {"passed": 2, "total": 3}, "procedural": {"passed": 0, "total": 6}}
    card = scoring.card_from_axes(axes, tool_calls=0, par=11)
    assert card["stats"]["EFF"] == 0
    assert card["stats"]["PRC"] == 0
    # Pre-fix (free EFF pass) this OVR was 73; zeroing the 16% EFF weight drops it.
    with_free_eff = round(0.32 * 89 + 0.26 * 79 + 0.16 * 66 + 0.16 * 82 + 0.10 * 0)
    assert with_free_eff == 73
    assert card["ovr"] == 60 and card["ovr"] < with_free_eff


def test_card_zero_tools_zero_par_is_full_eff():
    # A workflow that designs NO tools (par 0) and makes none is legitimately efficient.
    axes = {"grounding": {"passed": 2, "total": 2}, "adherence": {"passed": 2, "total": 2},
            "synthesis": {"passed": 1, "total": 1}, "procedural": {"passed": 1, "total": 1}}
    assert scoring.card_from_axes(axes, tool_calls=0, par=0)["stats"]["EFF"] == 99


def test_card_jdg_excluded_from_ovr():
    axes = {"grounding": {"passed": 5, "total": 10}, "adherence": {"passed": 5, "total": 10},
            "synthesis": {"passed": 1, "total": 2}, "procedural": {"passed": 2, "total": 4}}
    a = scoring.card_from_axes(axes, 11, 11, judged=0.0)
    b = scoring.card_from_axes(axes, 11, 11, judged=99.0)
    assert a["ovr"] == b["ovr"]


def test_card_tiebreak_priority():
    hi_grd = {"GRD": 90, "ADH": 10, "SYN": 10, "EFF": 10, "PRC": 10}
    hi_adh = {"GRD": 10, "ADH": 90, "SYN": 10, "EFF": 10, "PRC": 10}
    assert scoring.card_tiebreak_key(hi_grd) < scoring.card_tiebreak_key(hi_adh)
