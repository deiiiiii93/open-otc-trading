"""Task 5: read-only enforcement inside fanned-out subagents (argument-aware)."""
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from app.services.deep_agent import dynamic_subagents as ds
from app.services.deep_agent.fanout_readonly import FanoutReadOnlyMiddleware


def _req(name, args=None, call_id="c1"):
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": call_id}, tool=None, state={}, runtime=None
    )


def _ok(r):
    return ToolMessage(content="ran", tool_call_id=r.tool_call["id"], name=r.tool_call["name"])


def _cfg(monkeypatch, cfg):
    import app.services.deep_agent.fanout_readonly as m

    monkeypatch.setattr(m, "_read_configurable", lambda: cfg)


_FANOUT = {ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3, "ls_agent_type": "subagent"}


def test_irreversible_tool_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = FanoutReadOnlyMiddleware().wrap_tool_call(_req("book_position"), _ok)
    assert isinstance(r, ToolMessage) and r.status == "error"


def test_write_tool_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("create_report"), _ok).status == "error"


def test_run_python_pure_analysis_allowed(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = FanoutReadOnlyMiddleware().wrap_tool_call(_req("run_python", {"writes_artifacts": False}), _ok)
    assert r.content == "ran"


def test_run_python_writes_artifacts_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = FanoutReadOnlyMiddleware().wrap_tool_call(_req("run_python", {"writes_artifacts": True}), _ok)
    assert r.status == "error"


def test_unlisted_card_tool_denied_by_default(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("propose_reply_options"), _ok).status == "error"


def test_unclassified_tool_denied_by_default(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("brand_new_tool"), _ok).status == "error"


def test_top_level_scope_step_unaffected(monkeypatch):
    # fanout attribution present but NOT a subagent -> the scope/assemble step
    _cfg(monkeypatch, {ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3})
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("run_batch_pricing"), _ok).content == "ran"


def test_normal_chat_unaffected(monkeypatch):
    _cfg(monkeypatch, {})
    assert FanoutReadOnlyMiddleware().wrap_tool_call(_req("book_position"), _ok).content == "ran"
