"""Task 1: pilot constants + fixed interpreter wiring (task() via subagents, not ptc)."""
from unittest.mock import MagicMock

from app.services.deep_agent.dynamic_subagents import MAX_PTC_CALLS, is_allowlisted


def test_max_ptc_calls_is_24():
    assert MAX_PTC_CALLS == 24


def test_allowlist_only_pilot_slug():
    assert is_allowlisted("morning-risk-breach-commentary")
    assert not is_allowlisted("some-user-workflow")
    assert not is_allowlisted(None)


def _fake_model():
    model = MagicMock()
    model.profile = {"max_input_tokens": 200000}
    return model


def test_code_interpreter_wiring_uses_subagents_not_ptc_task():
    """Regression: `ptc=['task']` is invalid — task() is a subagents global, and the
    lib rejects it at model-call time. The interpreter must expose task() via the
    default subagents=True with the lowered per-eval cap."""
    from langchain_quickjs import CodeInterpreterMiddleware

    from app.services.deep_agent.orchestrator import _agent_middleware

    mw = _agent_middleware(True, model=_fake_model(), backend=MagicMock(), tools=[])
    ci = next(m for m in mw if isinstance(m, CodeInterpreterMiddleware))
    assert ci._max_ptc_calls == MAX_PTC_CALLS   # cap lowered 64 -> 24
    assert ci._subagents is True                # task() exposed as a subagent global
    assert not ci._ptc                          # NOT ptc=['task'] (would raise at model-call)
