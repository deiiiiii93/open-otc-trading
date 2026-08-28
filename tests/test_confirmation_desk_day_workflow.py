"""The confirmation-desk-day golden workflow.

The first arena board to exercise vision: the document-extraction sub-call routes
to the match's own model, so a grounding check here measures the CONTESTANT's
eyes rather than whichever model holds the `confirmation_extractor` tag.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena import scoring

TRUTH = Path(
    "backend/app/golden_workflows/definitions/confirmation-desk-day.truth.json"
)


@pytest.fixture
def loaded():
    return get_workflow_bundle("confirmation-desk-day")


def test_workflow_loads_with_the_expected_shape(loaded):
    wf = loaded.workflow
    assert wf.persona == "trader"
    assert len(wf.steps) == 9
    assert wf.extractor_model == "contestant"
    assert wf.requires == ["vision"]


def test_par_is_deliberately_uncalibrated(loaded):
    """An uncalibrated workflow must stay on the LEGACY HYPERBOLIC EFF curve.

    Setting a guessed par opts it into golf scoring against a denominator no live
    run justifies -- and because cards derive on read, it would re-score every
    stored board containing it.
    """
    assert loaded.workflow.par_tool_calls is None
    assert scoring.par_calibrated(loaded.workflow) is False


def test_only_the_first_step_grades_a_skill(loaded):
    """`skills_routed` records a skill only when its SKILL.md is READ, and the
    runtime never re-reads a loaded file -- so a repeat-skill check can never
    pass and would be an unwinnable point."""
    steps = loaded.workflow.steps
    assert steps[0].expected_skill == "book-trade-confirmation"
    assert all(s.expected_skill is None for s in steps[1:])


def test_the_routed_skill_actually_has_a_routing_block():
    """A skill with no `routing:` frontmatter never enters the orchestrator's
    known-skills table, so grading it measures catalog spelunking rather than
    ability (run #101: 4/36 trials routed an unrouted skill vs 75-97% for routed
    ones)."""
    text = Path(
        "backend/app/skills/workflows/positions/book-trade-confirmation/SKILL.md"
    ).read_text()
    assert "\nrouting:" in text


def test_golden_replay_scores_full_marks(loaded):
    score, passed, total = scoring.objective_score(
        transcript_from_replay(loaded), loaded
    )
    assert passed == total, f"replay earned {passed}/{total}"
    assert score == 100.0


def test_every_objective_axis_is_covered(loaded):
    """A missing axis is not neutral: _stat_from_tally returns 0 for an empty
    tally, so an uncovered axis is a CONSTANT 0 stat that drags every
    contestant's OVR down uniformly while carrying no ability signal."""
    axes = scoring.objective_breakdown(
        transcript_from_replay(loaded), loaded
    )["axes"]
    for axis in ("procedural", "adherence", "grounding", "synthesis"):
        assert axes.get(axis, {}).get("total", 0) > 0, f"{axis} has no checks"


def test_graded_vision_values_come_from_the_truth_file(loaded):
    """Guards the drift the emitted truth file exists to prevent: a manifest
    constant that no longer matches the rendered document mis-scores silently."""
    truth = json.loads(TRUTH.read_text())
    known = {
        float(v)
        for doc in truth["documents"].values()
        for v in doc.get("image_only", {}).values()
        if isinstance(v, (int, float))
    }
    graded = {
        a.value
        for step in loaded.workflow.steps
        for a in step.assertions
        if a.type == "answer_field_quotes" and a.field in {
            "strike", "notional", "barrier", "initial_price"
        }
    }
    unknown = {v for v in graded if float(v) not in known}
    assert not unknown, f"graded values not backed by truth.json: {unknown}"


def test_the_fixture_declares_every_graded_document(loaded):
    truth = json.loads(TRUTH.read_text())
    assert set(loaded.fixtures.documents) == set(truth["documents"])


def test_the_absence_trap_grades_a_recorded_null(loaded):
    """`is_null` requires the field to be RECORDED as null -- omitting it still
    fails, so the model must actively report the absence rather than stay
    silent."""
    step = loaded.workflow.steps[6]
    nulls = [
        a for a in step.assertions
        if a.type == "answer_field_equals" and getattr(a, "is_null", False)
    ]
    assert nulls, "step 7 does not grade a recorded null"
    assert nulls[0].field == "initial_price"


def test_the_session_ban_is_not_duplicated_per_step(loaded):
    """A per-step ban is gameable by booking early, and repeating a session
    assertion per step is double jeopardy."""
    banned = {a.name for a in loaded.workflow.success.assertions
              if a.type == "tool_not_called"}
    assert "book_position" in banned
    for step in loaded.workflow.steps:
        for a in step.assertions:
            if a.type == "tool_not_called":
                assert a.name not in banned, f"{a.name} banned per-step AND session"
