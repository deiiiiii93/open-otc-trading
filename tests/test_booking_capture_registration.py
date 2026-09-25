from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
"""Every agent middleware stack must carry BookingResultMiddleware.

A booking made inside a persona subagent lives in that subagent's own
LangGraph checkpoint namespace, so it never reaches the orchestrator's result
messages — the `wrap_tool_call` seam is the only place that sees it. A factory
that forgets this middleware fails here rather than silently dropping the
booking card for gated (i.e. approved) writes.
"""
from unittest.mock import MagicMock


def _names(middleware):
    return [type(m).__name__ for m in middleware]


def test_orchestrator_stack_captures_bookings():
    from app.services.deep_agent.orchestrator import _agent_middleware

    names = _names(_agent_middleware(False, model=GenericFakeChatModel(messages=iter([])), backend=object(), tools=[]))
    assert "BookingResultMiddleware" in names
    # Inside the error boundary and the audit trail: a booking that was
    # refused by audit fail-closed must never be reported as a booking.
    assert names.index("BookingResultMiddleware") > names.index("AuditTrailMiddleware")


def test_persona_stacks_capture_bookings():
    """The persona stack is the one that actually books in practice."""
    from app.services.deep_agent.personas import all_personas

    specs = all_personas(model=None, tools=[], skills_backend=object())
    assert specs
    for spec in specs:
        names = _names(spec["middleware"])
        assert "BookingResultMiddleware" in names
        assert names.index("BookingResultMiddleware") > names.index(
            "AuditTrailMiddleware"
        )
        # The fan-out read-only guard must still sit inside the audit trail.
        assert "FanoutReadOnlyMiddleware" in names


def test_async_agent_stack_captures_bookings(monkeypatch):
    import deepagents

    import app.services.async_agents.agent as agent_mod

    captured = {}

    def _fake_create_deep_agent(**kwargs):
        captured["middleware"] = kwargs["middleware"]
        return object()

    monkeypatch.setattr(deepagents, "create_deep_agent", _fake_create_deep_agent)
    agent_mod.build_async_agent(
        model=MagicMock(), tools=[], checkpointer=None, task_id=1
    )
    assert "BookingResultMiddleware" in _names(captured["middleware"])
