"""Task 2: the whole-eval attribution gate (deny-by-default)."""
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from app.services.deep_agent import dynamic_subagents as ds
from app.services.deep_agent.eval_gate import EvalAttributionGateMiddleware


def _req(name: str, call_id: str = "c1") -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": {}, "id": call_id}, tool=None, state={}, runtime=None
    )


def _handler_ok(request):
    return ToolMessage(
        content="ran", tool_call_id=request.tool_call["id"], name=request.tool_call["name"]
    )


def _configurable(monkeypatch, cfg):
    import app.services.deep_agent.eval_gate as gate

    monkeypatch.setattr(gate, "_read_configurable", lambda: cfg or {})


def test_eval_blocked_without_attribution(monkeypatch):
    _configurable(monkeypatch, {})
    result = EvalAttributionGateMiddleware().wrap_tool_call(_req("eval"), _handler_ok)
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "not authorized" in result.content.lower()


def test_eval_allowed_with_allowlisted_case3(monkeypatch):
    _configurable(
        monkeypatch,
        {
            ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3,
            ds.FANOUT_WORKFLOW_ID_KEY: "morning-risk-breach-commentary",
        },
    )
    result = EvalAttributionGateMiddleware().wrap_tool_call(_req("eval"), _handler_ok)
    assert isinstance(result, ToolMessage) and result.content == "ran"


def test_eval_blocked_for_non_allowlisted_workflow(monkeypatch):
    _configurable(
        monkeypatch,
        {
            ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3,
            ds.FANOUT_WORKFLOW_ID_KEY: "attacker-workflow",
        },
    )
    result = EvalAttributionGateMiddleware().wrap_tool_call(_req("eval"), _handler_ok)
    assert result.status == "error"


def test_eval_blocked_when_model_supplies_only_attribution_key(monkeypatch):
    # attribution present but no allowlisted workflow slug -> still denied
    _configurable(monkeypatch, {ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3})
    assert EvalAttributionGateMiddleware().wrap_tool_call(_req("eval"), _handler_ok).status == "error"


def test_non_eval_tool_passes_through(monkeypatch):
    _configurable(monkeypatch, {})
    result = EvalAttributionGateMiddleware().wrap_tool_call(_req("run_batch_pricing"), _handler_ok)
    assert result.content == "ran"
