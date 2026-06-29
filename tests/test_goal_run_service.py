"""GoalRunService (spec §B/§H): orchestrates frame -> store -> ratify/freeze ->
grader-invocation. The endpoints and AgentService are thin wrappers over this."""
from app.services.deep_agent.goal_mode import (
    ClarificationResponse,
    GoalRunService,
    GoalRunStateV1,
    goal_contract_hash,
    parse_goal_contract,
    render_goal_rubric,
)


class _FakeModel:
    def __init__(self, payload):
        self._payload = payload

    def with_structured_output(self, _schema):
        model = self

        class _S:
            def invoke(self, _messages):
                return model._payload

        return _S()


def _write_contract() -> dict:
    return {
        "schema_version": "goal_contract.v1",
        "goal_text": "Get latest risk run onto Control",
        "summary": "Refresh risk on the Control portfolio.",
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


def _readonly_contract() -> dict:
    c = _write_contract()
    c["domain_write_policy"] = "forbidden"
    c["criteria"][0]["check"] = {"type": "artifact_exists", "kind": "finding"}
    return c


def _service(payload):
    return GoalRunService(
        model=_FakeModel(payload),
        grader_tool_allowlist={"get_latest_risk_run"},
        run_backend={},
        contract_backend={},
    )


def test_start_with_clarification_creates_no_run():
    svc = _service({"type": "needs_clarification", "summary": "ambiguous", "questions": ["Which?"]})
    result = svc.start("t1", "improve risk", "yolo")
    assert isinstance(result, ClarificationResponse)
    assert svc.active("t1") is None


def test_start_write_goal_awaits_ratification():
    svc = _service({"type": "contract", "contract": _write_contract()})
    state = svc.start("t1", "refresh risk", "interactive")
    assert isinstance(state, GoalRunStateV1)
    assert state.status == "awaiting_ratification"
    assert svc.grader_invocation("t1") is None  # not running yet


def test_start_readonly_goal_auto_ratifies_to_running():
    svc = _service({"type": "contract", "contract": _readonly_contract()})
    state = svc.start("t1", "investigate vega", "yolo")
    assert state.status == "running"  # forbidden -> auto-ratified


def test_ratify_freezes_hash_and_exposes_grader_invocation():
    svc = _service({"type": "contract", "contract": _write_contract()})
    svc.start("t1", "refresh risk", "yolo")
    state = svc.ratify("t1")
    assert state.status == "running"
    contract = parse_goal_contract(_write_contract())
    assert state.contract_hash == goal_contract_hash(contract)
    inv = svc.grader_invocation("t1")
    assert inv == {"rubric": render_goal_rubric(contract)}


def test_cancel_releases_the_thread():
    svc = _service({"type": "contract", "contract": _write_contract()})
    svc.start("t1", "refresh risk", "yolo")
    svc.cancel("t1")
    assert svc.active("t1") is None
    assert svc.grader_invocation("t1") is None
