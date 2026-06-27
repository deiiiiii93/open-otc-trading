"""goal_grader_for_turn assembles the per-desk-turn grader (spec §G): when a thread has a
running goal run it returns (RubricMiddleware, {rubric: ...}); otherwise (None, None). The
grader's tools are the DOMAIN_READ subset and its on_evaluation records the verdict back."""
from app.services.deep_agent.envelopes import ToolGroup
from app.services.deep_agent.goal_mode import goal_grader_for_turn


class _Tool:
    def __init__(self, name, group):
        self.name = name
        if group is not None:
            self.__capability_group__ = group


class _FakeModel:
    """Minimal model: RubricMiddleware construction only needs an object to hold."""


class _FakeGoalService:
    def __init__(self, fragment):
        self._fragment = fragment
        self.recorded = []

    def grader_invocation(self, thread_id):
        return self._fragment

    def record_evaluation(self, thread_id, evaluation):
        self.recorded.append((thread_id, evaluation))


_TOOLS = [
    _Tool("get_latest_risk_run", ToolGroup.DOMAIN_READ),
    _Tool("book_trade", ToolGroup.DOMAIN_WRITE),
]


def test_no_service_returns_none_pair():
    assert goal_grader_for_turn(None, model=_FakeModel(), tools=_TOOLS, thread_id="t1") == (None, None)


def test_no_running_run_returns_none_pair():
    svc = _FakeGoalService(fragment=None)
    assert goal_grader_for_turn(svc, model=_FakeModel(), tools=_TOOLS, thread_id="t1") == (None, None)


def test_running_run_returns_grader_and_fragment():
    svc = _FakeGoalService(fragment={"rubric": "C1: ..."})
    grader, fragment = goal_grader_for_turn(svc, model=_FakeModel(), tools=_TOOLS, thread_id="t1")
    assert grader is not None
    assert fragment == {"rubric": "C1: ..."}


def test_on_evaluation_records_back_to_the_service():
    svc = _FakeGoalService(fragment={"rubric": "C1: ..."})
    grader, _ = goal_grader_for_turn(svc, model=_FakeModel(), tools=_TOOLS, thread_id="t1")
    # The middleware was constructed with our on_evaluation; invoke it to prove the wire.
    grader._on_evaluation({"result": "satisfied", "criteria": []})
    assert svc.recorded == [("t1", {"result": "satisfied", "criteria": []})]
