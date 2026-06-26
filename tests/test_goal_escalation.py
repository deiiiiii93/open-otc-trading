"""Escalation mapping (spec §F): a grader RubricEvaluation -> FailingCriterion list,
recorded on the GoalRunState when a run terminates unsatisfied."""
from app.services.deep_agent.goal_mode import (
    FailingCriterion,
    failing_criteria_from_evaluation,
)


def test_maps_only_failed_criteria_with_their_gaps():
    evaluation = {
        "grading_run_id": "r1",
        "iteration": 2,
        "result": "max_iterations_reached",
        "explanation": "two criteria still failing",
        "criteria": [
            {"name": "C1", "passed": True},
            {"name": "C2", "passed": False, "gap": "AAPL not surfaced in the report"},
            {"name": "C3", "passed": False, "gap": "no durable report artifact"},
        ],
    }
    failing = failing_criteria_from_evaluation(evaluation)
    assert [f.id for f in failing] == ["C2", "C3"]
    assert all(isinstance(f, FailingCriterion) for f in failing)
    assert failing[0].status == "failed"
    assert failing[0].reason == "AAPL not surfaced in the report"


def test_no_criterion_detail_returns_empty():
    assert failing_criteria_from_evaluation({"criteria": []}) == []
    assert failing_criteria_from_evaluation({}) == []
