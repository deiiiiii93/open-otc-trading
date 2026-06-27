"""GoalRunService over the real DB backends (ThreadColumnBackend), not dicts —
proves the duck-typed get/setitem/pop contract holds through a full lifecycle."""
from app import database
from app.services.deep_agent.goal_mode import GoalRunService
from app.services.deep_agent.goal_persistence import ThreadColumnBackend


class _FakeModel:
    def __init__(self, payload):
        self._payload = payload

    def with_structured_output(self, _schema):
        payload = self._payload

        class _S:
            def invoke(self, _messages):
                return payload

        return _S()


def _write_contract():
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


def _service():
    return GoalRunService(
        model=_FakeModel({"type": "contract", "contract": _write_contract()}),
        grader_tool_allowlist={"get_latest_risk_run"},
        run_backend=ThreadColumnBackend(database.SessionLocal, "goal_run"),
        contract_backend=ThreadColumnBackend(database.SessionLocal, "goal_contract"),
    )


def test_full_lifecycle_persists_through_db(session, agent_thread_factory):
    thread = agent_thread_factory()
    session.commit()
    tid = str(thread.id)
    svc = _service()

    state = svc.start(tid, "refresh risk", "interactive")
    assert state.status == "awaiting_ratification"
    # No rubric while awaiting ratification (activation gate).
    assert svc.grader_invocation(tid) is None

    assert svc.ratify(tid).status == "running"
    # Rubric now attaches and carries the frozen contract.
    inv = svc.grader_invocation(tid)
    assert inv is not None and "rubric" in inv

    # State survives a fresh service reading the same DB row.
    assert _service().active(tid).status == "running"

    assert svc.cancel(tid).status == "cancelled"
    assert _service().active(tid) is None  # pointer released on the row
    assert svc.grader_invocation(tid) is None


def test_second_goal_on_same_thread_rejected_via_db(session, agent_thread_factory):
    thread = agent_thread_factory()
    session.commit()
    tid = str(thread.id)
    svc = _service()
    svc.start(tid, "first", "auto")
    try:
        _service().start(tid, "second", "auto")
    except Exception as exc:  # GoalStateError
        assert "already has an active goal run" in str(exc)
        return
    raise AssertionError("expected the second goal start to be rejected")
