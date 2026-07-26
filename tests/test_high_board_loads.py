from app.golden_workflows.registry import get_workflow_bundle


def _wf():
    return get_workflow_bundle("high-board-portfolio-review-day").workflow


def test_high_board_definition_pins():
    wf = _wf()
    assert wf.id == "high-board-portfolio-review-day"
    assert wf.persona == "high_board"
    assert len(wf.steps) == 8
    assert len(wf.narration) == 8
    assert wf.tags == ["flagship", "high-board", "oversight", "reporting", "desk-workflow"]
    # Only skills the ORCHESTRATOR actually routes to are expected (2026-07-25 audit).
    # portfolio-membership / portfolio-view-counting / batch-run-reports have no
    # reachable routing path from this persona, so expecting them graded models on
    # violating the system's own routing policy — all three scored 0-23% across the
    # full Run #58 field and 0 in a live smoke.
    skills = [s.expected_skill for s in wf.steps]
    assert skills == [
        "portfolio-maintenance", "portfolio-maintenance", None,
        None, None, None, "display-report", "generate-report",
    ]


def test_high_board_objective_point_manifest():
    wf = _wf()
    # `skills` must count NON-NULL expected_skill steps only: scoring emits no
    # skill check for an `expected_skill: null` step, so `len(wf.steps)` overcounts
    # the denominator by one per null step (this pin read 52 for a 50-point
    # manifest before the 2026-07-25 audit).
    skills = sum(1 for s in wf.steps if s.expected_skill is not None)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success_assertions = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success_assertions) == (4, 7, 27, 2)
    assert skills + tools + step_assertions + success_assertions == 40


def test_no_step_scores_the_same_skill_twice():
    """Regression guard for the double-jeopardy defect found in the Run #58 audit.

    Scoring emits its own ``skill: X`` check from ``expected_skill``, so a step that
    ALSO declares ``skill_routed`` for that same skill scores one routing fact twice —
    inflating the denominator and doubling the penalty for a single routing miss. All
    four duplicated pairs in Run #58 had byte-identical field-wide pass rates.
    """
    wf = _wf()
    for i, step in enumerate(wf.steps):
        if step.expected_skill is None:
            continue
        dupes = [
            a for a in step.assertions
            if getattr(a, "type", None) == "skill_routed"
            and getattr(a, "name", None) == step.expected_skill
        ]
        assert not dupes, (
            f"step {i + 1} scores skill {step.expected_skill!r} twice: "
            "expected_skill already emits a check; drop the skill_routed assertion"
        )
