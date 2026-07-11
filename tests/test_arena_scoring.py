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
            {"id": "x", "name": "get_greeks_landscape_run_tool", "args": {"run_id": 101}},
            # a compliant refetch still records its structured answer (the grid
            # step's grounding is now answer_field_quotes, not a response scan)
            {"id": "a", "name": "record_answer_tool", "args": {"answer": {
                "gamma_at_+10pct": 16.403033928381223,
                "delta_at_-20pct": 391.1919745962153}}}]
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
    # The fallback (no explicit par_tool_calls) is sum(expected_tools). Strip the
    # flagship's explicit par to exercise it — the flagship itself now declares 24.
    from app.golden_workflows.schema import GoldenWorkflow
    wf = _get_wf("risk-manager-control-day")
    data = wf.model_dump()
    data["par_tool_calls"] = None
    stripped = GoldenWorkflow(**data)
    assert scoring.designed_par(stripped) == sum(len(s.expected_tools) for s in wf.steps)
    assert scoring.designed_par(stripped) == 11


def test_flagship_par_is_calibrated_24():
    wf = _get_wf("risk-manager-control-day")
    assert wf.par_tool_calls == 24
    assert scoring.designed_par(wf) == 24
    assert scoring.par_calibrated(wf) is True


def test_derive_card_uses_linear_for_calibrated_flagship():
    # A stored breakdown for the flagship (calibrated par=24) derives a LINEAR EFF on
    # read, not the hyperbolic one. c=1.0 → EFF == round(99 * linear_ratio).
    from app.services.arena.store import _derive_card
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    bd = {"objective": {"axes": axes},
          "diagnosis": {"counts_detail": {"tool_calls": 36}}}
    card, reason = _derive_card(bd, "risk-manager-control-day")
    assert reason is None
    # 36 calls, par 24, span 24 → 1-(12/24)=0.5 → EFF 50 (linear).
    # (Hyperbolic would be round(99*24/36)=66 — proving the gate is on.)
    assert card["stats"]["EFF"] == 50


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


def test_par_calibrated_helper():
    from app.golden_workflows.schema import GoldenWorkflow
    wf = _get_wf("risk-manager-control-day")  # declares par_tool_calls → calibrated
    assert scoring.par_calibrated(wf) is True
    data = wf.model_dump()
    data["par_tool_calls"] = None             # strip → falls back to sum(expected_tools)
    assert scoring.par_calibrated(GoldenWorkflow(**data)) is False


def test_card_eff_uncalibrated_keeps_hyperbolic():
    # A workflow without a calibrated par keeps TODAY's hyperbolic EFF exactly.
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    # default par_calibrated=False → ratio = min(1, par/tool_calls)
    assert scoring.card_from_axes(axes, 22, 11)["stats"]["EFF"] == 50   # 11/22
    assert scoring.card_from_axes(axes, 11, 11)["stats"]["EFF"] == 99
    assert scoring.card_from_axes(axes, 5, 11)["stats"]["EFF"] == 99    # leaner not penalized


def test_card_eff_linear_when_calibrated():
    # c = 1.0 (all correctness axes full) so EFF == round(99 * ratio).
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    f = lambda tc, par: scoring.card_from_axes(axes, tc, par, par_calibrated=True)["stats"]["EFF"]
    assert f(24, 24) == 99          # at par → full
    assert f(20, 24) == 99          # under par → full (leaner not penalized)
    assert f(36, 24) == 50          # +12 over par of 24, span 24 → 1-0.5 → round(49.5)=50
    assert f(48, 24) == 0           # 2×par → 0
    assert f(60, 24) == 0           # beyond 2×par → floored at 0


def test_card_eff_calibrated_guards_unchanged():
    # The non-execution / zero-par guards precede the calibration branch.
    axes = {"grounding": {"passed": 9, "total": 10}, "adherence": {"passed": 8, "total": 10},
            "synthesis": {"passed": 2, "total": 3}, "procedural": {"passed": 0, "total": 6}}
    assert scoring.card_from_axes(axes, 0, 24, par_calibrated=True)["stats"]["EFF"] == 0
    full = {"grounding": {"passed": 2, "total": 2}, "adherence": {"passed": 2, "total": 2},
            "synthesis": {"passed": 1, "total": 1}, "procedural": {"passed": 1, "total": 1}}
    assert scoring.card_from_axes(full, 0, 0, par_calibrated=True)["stats"]["EFF"] == 99


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


# --- Consistency (CON) ----------------------------------------------------

def test_consistency_none_below_two_matches():
    # No dispersion to measure with 0 or 1 sample → invalid (greyed in the UI).
    assert scoring.consistency_stat([]) is None
    assert scoring.consistency_stat([80]) is None


def test_consistency_identical_scores_is_max():
    assert scoring.consistency_stat([70, 70, 70]) == 99


def test_consistency_linear_to_zero_at_15_pstdev():
    # Two OVRs 30 apart → pstdev 15 → CON 0 (the CON_ZERO_STDEV anchor).
    assert scoring.consistency_stat([50, 80]) == 0
    # A wider spread stays clamped at 0, never negative.
    assert scoring.consistency_stat([40, 90]) == 0
    # pstdev 5 (10 apart) → 99*(1-5/15) = round(66.0) = 66.
    assert scoring.consistency_stat([70, 80]) == 66


def test_blend_ovr_discounts_never_boosts():
    # Discount form: base * (0.82 + 0.18*con/99).
    assert scoring.blend_ovr(80, 40) == round(80 * (0.82 + 0.18 * 40 / 99))
    # Perfect consistency returns the base untouched.
    assert scoring.blend_ovr(80, 99) == 80
    # Max inconsistency shaves 18% off — never below the floor, never a boost.
    assert scoring.blend_ovr(80, 0) == round(80 * 0.82)  # 66
    # None con (single match) leaves the base untouched.
    assert scoring.blend_ovr(80, None) == 80


def test_consistency_cannot_boost_a_failing_model():
    # The Codex finding: a consistently-BAD model must not earn OVR from low
    # dispersion. Zero base × any CON stays zero — no free floor from consistency.
    assert scoring.consistency_stat([0, 0]) == 99      # perfectly consistent...
    assert scoring.blend_ovr(0, 99) == 0               # ...but earns nothing.
    # A steady-mediocre model is only ever discounted from its base, never lifted:
    # base 40, con 99 → 40 (not 40 + a CON bonus).
    assert scoring.blend_ovr(40, 99) == 40
    assert scoring.blend_ovr(40, 0) == round(40 * 0.82)  # 33 — erratic is penalized


def _trial_card(ovr, jdg=None):
    return {"ovr": ovr, "stats": {"GRD": ovr, "ADH": ovr, "SYN": ovr,
                                  "PRC": ovr, "EFF": ovr}, "jdg": jdg}


def test_aggregate_card_averages_stats_and_folds_trial_con():
    # Multi-trial match: stats are the per-trial mean, CON is trial dispersion, and
    # the final OVR discounts the base mean by CON.
    cards = [_trial_card(80), _trial_card(70)]
    agg = scoring.aggregate_card_from_trials(cards)
    con = scoring.consistency_stat([80, 70])           # pstdev 5 → 66
    assert agg["con"] == con
    assert agg["base_ovr"] == 75                        # mean of 80, 70
    assert agg["ovr"] == scoring.blend_ovr(75, con)
    assert agg["stats"]["GRD"] == 75                    # (80+70)/2


def test_aggregate_card_single_trial_is_grey():
    # One trial → no dispersion → CON None (greyed), OVR at the base.
    agg = scoring.aggregate_card_from_trials([_trial_card(85)])
    assert agg["con"] is None
    assert agg["ovr"] == 85 and agg["base_ovr"] == 85


def test_aggregate_card_inconsistent_trials_are_discounted():
    # The real deepseek-v4-pro case: trials 67/37/30/63 swing hard → CON 0 → the
    # aggregate OVR is the base mean discounted 18%.
    cards = [_trial_card(v) for v in (67, 37, 30, 63)]
    agg = scoring.aggregate_card_from_trials(cards)
    assert agg["con"] == 0
    assert agg["base_ovr"] == round((67 + 37 + 30 + 63) / 4)   # 49
    assert agg["ovr"] == scoring.blend_ovr(49.25, 0)          # 40

    assert scoring.aggregate_card_from_trials([]) is None


def test_aggregate_card_averages_available_judge_scores():
    agg = scoring.aggregate_card_from_trials(
        [_trial_card(80, jdg=70), _trial_card(70, jdg=None), _trial_card(75, jdg=90)])
    assert agg["jdg"] == 80.0        # mean of the non-null 70, 90
    assert scoring.aggregate_card_from_trials(
        [_trial_card(80), _trial_card(70)])["jdg"] is None


def _flagship_transcript(calls_by_index):
    """A MatchTranscript over the flagship's step count with tool_calls placed at the
    given 0-based step indices (all other steps empty)."""
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    from app.golden_workflows.registry import get_workflow_bundle
    loaded = get_workflow_bundle("risk-manager-control-day")
    n = len(loaded.workflow.steps)
    steps = [MatchStep(index=i, user="u", messages=[], tool_calls=calls_by_index.get(i, []),
                       tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                       response_text="", errors=[]) for i in range(n)]
    return MatchTranscript(schema_version=1, run_id=None,
                           workflow_id="risk-manager-control-day", model_id="x",
                           started_at=None, finished_at=None, steps=steps), loaded


def test_record_answer_one_expected_recorder_is_exempt_from_eff():
    """The ONE record_answer a structured-answer step expects (incl. the _tool-suffixed
    trace form) does not inflate the EFF tool count — complying is not penalized."""
    from app.services.arena.scoring import diagnose_heuristic
    risk = {"name": "get_latest_risk_run", "args": {}}
    ra = {"name": "record_answer_tool", "args": {"answer": {"hotspot": "AAPL"}}}
    # step index 2 (the hotspot step) declares answer_field_* → 1 recorder exempt
    t, loaded = _flagship_transcript({2: [risk, ra]})
    assert diagnose_heuristic(t, loaded)["tool_calls"] == 1


def test_record_answer_spam_and_offstep_calls_count_toward_eff():
    """Extra recorder calls on an answer step, and any recorder call on a step that
    declares no answer_field_* assertion, all COUNT — the recorder cannot hide churn
    or inflate EFF (Codex code-review)."""
    from app.services.arena.scoring import diagnose_heuristic
    risk = {"name": "get_latest_risk_run", "args": {}}
    ra = {"name": "record_answer", "args": {"answer": {"x": 1}}}
    # step 2 (answer step): 1 risk + 3 recorders → 1 exempt, 2 count → 3
    # step 0 (non-answer step): 1 recorder → counts → 1
    # total = 4 (step2) + 1 (step0) = 5; exempt 1 → 4
    t, loaded = _flagship_transcript({2: [risk, ra, ra, ra], 0: [ra]})
    assert diagnose_heuristic(t, loaded)["tool_calls"] == 4


def test_answer_fields_bounds_oversized_and_spam_payload():
    """answer_fields caps field count and value/key length so an oversized/spam recorder
    input cannot be scored or retained in full (Codex code-review [high])."""
    from app.golden_workflows.assertions import answer_fields, AssertionContext
    big = {"blob": "x" * 5000, **{f"k{i}": i for i in range(50)}}
    ctx = AssertionContext(response_text="", tool_calls=[
        {"name": "record_answer", "args": {"answer": big}}],
        tool_results=[], skills_routed=[], artifacts=[], task_ids=[])
    fields = answer_fields(ctx)
    assert len(fields) <= 32
    assert all(not isinstance(v, str) or len(v) <= 257 for v in fields.values())
    assert all(len(k) <= 128 for k in fields)


def test_fold_trial_breakdowns_means_and_shape():
    from app.services.arena import scoring
    trials = [
        {"objective": {"axes": {"grounding": 1}}, "objective_score": 80.0,
         "subjective_mode": "disabled", "card": {"ovr": 70}},
        {"objective": {"axes": {"grounding": 0}}, "objective_score": 90.0,
         "subjective_mode": "disabled", "card": {"ovr": 72}},
    ]
    agg = scoring.fold_trial_breakdowns(trials)
    assert agg["n_trials"] == 2
    assert agg["aggregate"] == trials
    assert agg["objective"] == trials[0]["objective"]
    assert agg["objective_score"] == 85.0
    assert agg["objective_stdev"] == 5.0
    assert agg["total_score"] == 85.0
    assert agg["subjective_mode"] == "disabled"


def test_fold_trial_breakdowns_single_trial_zero_stdev():
    from app.services.arena import scoring
    agg = scoring.fold_trial_breakdowns([
        {"objective": {}, "objective_score": 88.5, "subjective_mode": "disabled"}])
    assert agg["n_trials"] == 1
    assert agg["objective_stdev"] == 0.0
    assert agg["objective_score"] == 88.5
