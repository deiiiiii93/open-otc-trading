from app.golden_workflows.registry import get_workflow_bundle


def _wf():
    return get_workflow_bundle("high-board-portfolio-review-day").workflow


def test_high_board_definition_pins():
    wf = _wf()
    assert wf.id == "high-board-portfolio-review-day"
    assert wf.persona == "high_board"
    assert len(wf.steps) == 6
    assert len(wf.narration) == 6
    assert wf.tags == ["flagship", "high-board", "oversight", "reporting", "desk-workflow"]
    skills = [s.expected_skill for s in wf.steps]
    assert skills == [
        "portfolio-membership", "portfolio-maintenance", "portfolio-view-counting",
        "batch-run-reports", "display-report", "generate-report",
    ]


def test_high_board_objective_point_manifest():
    wf = _wf()
    skills = len(wf.steps)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success_assertions = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success_assertions) == (6, 7, 17, 5)
    assert skills + tools + step_assertions + success_assertions == 35
