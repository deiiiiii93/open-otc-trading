"""Every agent middleware stack must carry AuditTrailMiddleware (audit spec §5.2a).

A factory that forgets the middleware fails here rather than silently
under-auditing.
"""
from unittest.mock import MagicMock


def _names(middleware):
    return [type(m).__name__ for m in middleware]


def test_orchestrator_stack_has_audit_inside_error_boundary():
    from app.services.deep_agent.orchestrator import _agent_middleware

    mw = _agent_middleware(False, model=None, backend=object(), tools=[])
    names = _names(mw)
    assert names[0] == "ToolErrorBoundaryMiddleware"
    assert names[1] == "AuditTrailMiddleware"


def test_persona_stacks_have_audit_inside_error_boundary():
    from app.services.deep_agent.personas import all_personas

    specs = all_personas(model=None, tools=[], skills_backend=object())
    assert specs  # three personas
    for spec in specs:
        names = _names(spec["middleware"])
        assert names[0] == "ToolErrorBoundaryMiddleware"
        assert names[1] == "AuditTrailMiddleware"
        assert "FanoutReadOnlyMiddleware" in names


def test_async_agent_stack_has_audit(monkeypatch):
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
    assert "AuditTrailMiddleware" in _names(captured["middleware"])
