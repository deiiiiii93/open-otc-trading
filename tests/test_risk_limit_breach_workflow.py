"""risk-limit-breach-day: structural pins, replay scoring, negative mutations.

Mirrors tests/test_high_board_workflow.py (the newest four-axis pattern).
The golden replay proves SATISFIABILITY only — live reachability is proven by
the mandatory pre-merge live smoke, never by this file.
"""
from __future__ import annotations

import json

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena import scoring

WORKFLOW_ID = "risk-limit-breach-day"


@pytest.fixture(scope="module")
def loaded():
    return get_workflow_bundle(WORKFLOW_ID)


def test_bundle_loads(loaded):
    wf = loaded.workflow
    assert wf.persona == "risk_manager"
    assert len(wf.steps) == 7
    assert [s.expected_skill for s in wf.steps] == [
        "monitor-limits",
        # null: read-risk-result declares no `routing:` frontmatter, so it never
        # enters the orchestrator's Known-skills table and is only reachable via
        # the persona catalog. Run #101 measured the consequence — 4/36 trials
        # routed it, while every routed skill on the same board scored 75-97%.
        # The step is still performed correctly WITHOUT it (18/18 models called
        # get_latest_risk_run; 36/36 recorded both answer fields), so the check
        # graded document-loading, not ability. See test_step2_skill_is_null_*.
        None,
        "handle-limit-incident",
        "generate-report",
        None,
        "run-risk",
        None,
    ]
    assert wf.par_tool_calls is None
    for step in wf.steps:
        assert step.replay in loaded.fixtures.replay


def test_point_manifest_is_38(loaded):
    wf = loaded.workflow
    skills = sum(1 for s in wf.steps if s.expected_skill is not None)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success) == (4, 11, 22, 1)
    assert skills + tools + step_assertions + success == 38


def test_step2_skill_is_null_because_read_risk_result_is_unroutable(loaded):
    """Step 2 must not grade a skill the orchestrator never advertises.

    `read-risk-result` declares no `routing:` frontmatter, so `collect_routing_rows`
    skips it and it never reaches the Known-skills table. Grading it measured the
    harness, not the model. Guards the regression: re-adding an expected_skill here
    silently reintroduces a near-unwinnable check.
    """
    step = loaded.workflow.steps[1]
    assert step.expected_skill is None
    assert [t.name for t in step.expected_tools] == ["get_latest_risk_run"]


def test_not_par_calibrated(loaded):
    assert scoring.par_calibrated(loaded.workflow) is False


def test_success_bans_waive_session_wide(loaded):
    (assertion,) = loaded.workflow.success.assertions
    assert assertion.type == "tool_not_called"
    assert assertion.name == "waive_limit_incident"
    # Double-jeopardy rule: the ban must NOT also appear per-step.
    for step in loaded.workflow.steps:
        for a in step.assertions:
            assert not (
                a.type == "tool_not_called" and a.name == "waive_limit_incident"
            )


def test_no_step_scores_its_own_skill_twice(loaded):
    for step in loaded.workflow.steps:
        for a in step.assertions:
            assert a.type != "skill_routed", (
                "scoring emits its own skill check for expected_skill — a "
                "skill_routed assertion double-scores it"
            )


def test_has_four_axes(loaded):
    transcript = transcript_from_replay(loaded)
    breakdown = scoring.objective_breakdown(transcript, loaded)
    axes = breakdown["axes"]
    assert {"grounding", "adherence", "synthesis", "procedural"} <= set(axes)
    for name in ("grounding", "adherence", "synthesis", "procedural"):
        assert axes[name]["total"] > 0, f"axis {name} has no checks"


def test_golden_replay_scores_full_marks(loaded):
    transcript = transcript_from_replay(loaded)
    score, passed, total = scoring.objective_score(transcript, loaded)
    assert passed == total
    assert score == 100.0


def test_grounding_matches_truth_file(loaded):
    truth_path = loaded.definition_path.parent / "risk-limit-breach-day.truth.json"
    truth_values = {
        entry["value"] for entry in json.loads(truth_path.read_text()).values()
    }
    manifest_values = {
        a.value
        for step in loaded.workflow.steps
        for a in step.assertions
        if a.type == "answer_field_quotes"
    }
    assert manifest_values <= truth_values, (
        f"manifest grounding values {manifest_values - truth_values} not in truth file"
    )


def test_seeded_breach_metrics_match_harvest(loaded):
    """The authored seeded RiskRun/evaluation numbers must equal the harvested
    breach-side payload — seeded numbers are pasted from harvest, never invented."""
    truth_path = loaded.definition_path.parent / "risk-limit-breach-day.truth.json"
    truth = json.loads(truth_path.read_text())
    seed = loaded.fixtures.seed
    run_metrics = seed["risk_runs"][0]["metrics"]
    assert run_metrics["totals"]["delta"] == truth["breach_net_delta"]["value"]
    aapl_row = next(
        r for r in run_metrics["positions"] if r["underlying"] == "AAPL"
    )
    assert aapl_row["delta"] == truth["driver_delta"]["value"]
    ev = next(
        e for e in seed["limit_evaluations"] if e["alias"] == "ev_net_delta"
    )
    assert ev["observed_value"] == truth["breach_net_delta"]["value"]


def test_limit_boundaries_sit_between_clean_and_breach(loaded):
    """comparator=upper validity + discrimination: clean < warning < hard < breach."""
    truth_path = loaded.definition_path.parent / "risk-limit-breach-day.truth.json"
    truth = json.loads(truth_path.read_text())
    clean = truth["clean_net_delta"]["value"]
    breach = truth["breach_net_delta"]["value"]
    version = next(
        v
        for v in loaded.fixtures.seed["risk_limit_versions"]
        if v["alias"] == "net_delta_cap_v1"
    )
    assert clean < version["warning_upper"] < version["hard_upper"] < breach


# ---------------------------------------------------------------------------
# Negative mutations — each must drop the score below full marks.
# ---------------------------------------------------------------------------


def _score_mutated(loaded, mutate) -> tuple[int, int]:
    transcript = transcript_from_replay(loaded)
    mutate(transcript)
    _score, passed, total = scoring.objective_score(transcript, loaded)
    return passed, total


def _step(transcript, index):
    return transcript.steps[index]


def test_neg_missing_acknowledge_fails(loaded):
    def mutate(t):
        step = _step(t, 2)
        step.tool_calls = [
            c for c in step.tool_calls
            if c["name"] != "acknowledge_limit_incident"
        ]
        step.tool_results = [
            r for r in step.tool_results
            if r["name"] != "acknowledge_limit_incident"
        ]

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_conflicted_acknowledge_fails(loaded):
    """A conflict dict (failed mutation) must NOT pass the success-sensitive
    tool_result_path check even though the tool name was called."""

    def mutate(t):
        step = _step(t, 2)
        for r in step.tool_results:
            if r["name"] == "acknowledge_limit_incident":
                r["content"] = {"ok": False, "error": "conflict"}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_waive_in_any_step_fails_success(loaded):
    def mutate(t):
        step = _step(t, 3)
        step.tool_calls.append(
            {"id": "cx_waive", "name": "waive_limit_incident",
             "args": {"incident_id": 1, "rationale": "q-end", "expires_at": "2026-09-30T00:00:00", "expected_row_version": 3}}
        )
        step.tool_results.append(
            {"name": "waive_limit_incident", "tool_call_id": "cx_waive",
             "content": {"id": 1, "status": "waived", "row_version": 4}}
        )

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_waive_answer_fails_step5(loaded):
    def mutate(t):
        step = _step(t, 4)
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"action": "waive"}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_redundant_resolve_fails_step7(loaded):
    def mutate(t):
        step = _step(t, 6)
        step.tool_calls.append(
            {"id": "cx_resolve", "name": "resolve_limit_incident",
             "args": {"incident_id": 1, "expected_row_version": 4}}
        )
        step.tool_results.append(
            {"name": "resolve_limit_incident", "tool_call_id": "cx_resolve",
             "content": {"ok": False, "error": "conflict"}}
        )

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_wrong_net_delta_fails_grounding(loaded):
    def mutate(t):
        step = _step(t, 5)
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"net_delta_now": 999.0, "limit_status": "ok"}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_open_incident_status_fails_step7(loaded):
    def mutate(t):
        step = _step(t, 6)
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"incident_status": "open"}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total
