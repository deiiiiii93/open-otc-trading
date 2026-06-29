"""Orchestrator splice (spec §H): the goal grader attaches to the agent middleware
only when supplied (the caller supplies it only while the run is `running`)."""
from unittest.mock import MagicMock

from app.services.deep_agent.orchestrator import _agent_middleware


def _fake_model():
    """A model whose profile satisfies the compaction/summarization middleware."""
    model = MagicMock()
    model.profile = {"max_input_tokens": 200000}
    return model


def test_goal_grader_is_appended_when_provided():
    sentinel = object()
    mw = _agent_middleware(
        False, model=_fake_model(), backend=MagicMock(), tools=[], goal_grader=sentinel
    )
    assert mw[-1] is sentinel


def test_no_goal_grader_by_default():
    sentinel = object()
    mw = _agent_middleware(False, model=_fake_model(), backend=MagicMock(), tools=[])
    assert sentinel not in mw
    assert isinstance(mw, list) and len(mw) >= 1
