"""GoalRunService.record_evaluation maps a grader RubricEvaluation to a run transition
(spec §F): satisfied -> satisfied; max_iterations/failed/grader_error -> stuck_needs_human
with the failing criteria; needs_revision and non-running states are no-ops. This is the
on_evaluation callback the desk turn hands the grader."""
from app.services.deep_agent.goal_mode import GoalRunService


def _write_contract_dict():
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Get latest risk run onto Control",
        "summary": "Refresh risk on Control.",
        "domain_write_policy": "allowed_by_mode",
        "criteria": [
            {
                "id": "C1",
                "text": "Latest risk run used the Control portfolio.",
                "required": True,
                "check": {
                    "type": "ledger_predicate",
                    "tool": "get_latest_risk_run",
                    "args": {},
                    "expect": [{"path": "portfolio", "op": "eq", "value": "Control"}],
                },
            }
        ],
    }


class _FakeModel:
    def with_structured_output(self, _schema):
        contract = _write_contract_dict()

        class _S:
            def invoke(self, _messages):
                return {"type": "contract", "contract": contract}

        return _S()


def _running_service(tid):
    svc = GoalRunService(
        model=_FakeModel(),
        grader_tool_allowlist={"get_latest_risk_run"},
        run_backend={},
        contract_backend={},
    )
    svc.start(tid, "refresh risk", "auto")
    svc.ratify(tid)
    return svc


def test_satisfied_marks_run_satisfied_and_releases_pointer():
    svc = _running_service("t1")
    state = svc.record_evaluation("t1", {"result": "satisfied", "criteria": []})
    assert state.status == "satisfied"
    assert svc.active("t1") is None  # pointer released


def test_max_iterations_escalates_with_failing_criteria():
    svc = _running_service("t1")
    state = svc.record_evaluation(
        "t1",
        {
            "result": "max_iterations_reached",
            "criteria": [
                {"name": "C1", "passed": False, "gap": "AAPL not surfaced"},
            ],
        },
    )
    assert state.status == "stuck_needs_human"
    assert state.terminal_reason == "max_iterations_reached"
    assert [c.id for c in state.failing_criteria] == ["C1"]


def test_needs_revision_is_a_noop():
    svc = _running_service("t1")
    state = svc.record_evaluation("t1", {"result": "needs_revision", "criteria": []})
    assert state.status == "running"


def test_evaluation_on_no_active_run_is_a_noop():
    svc = GoalRunService(
        model=_FakeModel(),
        grader_tool_allowlist={"get_latest_risk_run"},
        run_backend={},
        contract_backend={},
    )
    assert svc.record_evaluation("missing", {"result": "satisfied"}) is None


def test_grader_error_escalates():
    svc = _running_service("t1")
    state = svc.record_evaluation("t1", {"result": "grader_error", "criteria": []})
    assert state.status == "stuck_needs_human"
    assert state.terminal_reason == "grader_error"
