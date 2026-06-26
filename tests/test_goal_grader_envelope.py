"""GOAL_GRADER_READ envelope (spec §D) — the grader observes the ledger, fail-closed."""
from app.services.deep_agent.envelopes import Envelope, ToolGroup, tool_allowed


def test_grader_envelope_grants_domain_read_only():
    env = Envelope.GOAL_GRADER_READ
    assert tool_allowed(env, ToolGroup.DOMAIN_READ) is True


def test_grader_envelope_denies_writes_python_and_async():
    env = Envelope.GOAL_GRADER_READ
    # fail closed: no mutation, no side-effecting python, no spawning
    assert tool_allowed(env, ToolGroup.DOMAIN_WRITE) is False
    assert tool_allowed(env, ToolGroup.DETERMINISTIC_PY) is False
    assert tool_allowed(env, ToolGroup.ASYNC_DISPATCH) is False
    assert tool_allowed(env, ToolGroup.PAGE_ACTION) is False
