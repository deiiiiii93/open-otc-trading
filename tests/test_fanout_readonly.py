"""Read-only enforcement inside fanned-out subagents (capability-group aware).

Writes are blocked by capability group (DOMAIN_WRITE / PAGE_ACTION / ASYNC_DISPATCH)
plus the deepagents FS/shell writes and argument-aware run_python. Everything else —
including ungated reads like get_position_summaries — is allowed, because a
deny-by-default guard stalls the fan-out by blocking the reads the investigator needs.
"""
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from app.services.agents import select_deep_agent_tools
from app.services.deep_agent import dynamic_subagents as ds
from app.services.deep_agent.fanout_readonly import FanoutReadOnlyMiddleware

# The real capability-gated tool set, so writes are classified authoritatively.
_TOOLS = select_deep_agent_tools()


def _mw():
    return FanoutReadOnlyMiddleware(tools=_TOOLS)


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


# --- writes are blocked -------------------------------------------------------

def test_irreversible_domain_write_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = _mw().wrap_tool_call(_req("book_position"), _ok)
    assert isinstance(r, ToolMessage) and r.status == "error"


def test_domain_write_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert _mw().wrap_tool_call(_req("create_report"), _ok).status == "error"
    assert _mw().wrap_tool_call(_req("run_batch_pricing"), _ok).status == "error"


def test_page_action_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert _mw().wrap_tool_call(_req("propose_reply_options"), _ok).status == "error"


def test_async_dispatch_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    assert _mw().wrap_tool_call(_req("start_async_agent"), _ok).status == "error"


def test_fs_writes_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    for name in ("write_file", "edit_file", "execute"):
        assert _mw().wrap_tool_call(_req(name), _ok).status == "error", name


def test_run_python_writes_artifacts_blocked(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = _mw().wrap_tool_call(_req("run_python", {"writes_artifacts": True}), _ok)
    assert r.status == "error"


# --- reads are allowed (the regression the live smoke found) ------------------

def test_domain_reads_allowed(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    for name in ("get_positions", "get_latest_risk_run", "get_portfolio", "list_portfolios",
                 "list_pricing_parameter_profiles"):
        assert _mw().wrap_tool_call(_req(name, {"portfolio_id": 1}), _ok).content == "ran", name


def test_ungated_read_allowed(monkeypatch):
    # get_position_summaries is a plain @tool (no capability group); a deny-by-default
    # guard wrongly blocked it and stalled the fan-out. It must be allowed.
    _cfg(monkeypatch, _FANOUT)
    assert _mw().wrap_tool_call(_req("get_position_summaries", {"portfolio_id": 1}), _ok).content == "ran"


def test_fs_reads_allowed(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    for name in ("read_file", "ls", "glob", "grep"):
        assert _mw().wrap_tool_call(_req(name, {"path": "/skills/"}), _ok).content == "ran", name


def test_run_python_pure_analysis_allowed(monkeypatch):
    _cfg(monkeypatch, _FANOUT)
    r = _mw().wrap_tool_call(_req("run_python", {"writes_artifacts": False}), _ok)
    assert r.content == "ran"


def test_unclassified_tool_allowed_as_read(monkeypatch):
    # Unknown/ungated tools are treated as reads (allow-by-default). The eval gate is
    # the confinement; this guard only keeps positively-identified writes out.
    _cfg(monkeypatch, _FANOUT)
    assert _mw().wrap_tool_call(_req("brand_new_tool"), _ok).content == "ran"


# --- guard only applies inside a fanned-out subagent --------------------------

def test_top_level_scope_step_unaffected(monkeypatch):
    # fanout attribution present but NOT a subagent -> the scope/assemble step runs
    # the write tool normally.
    _cfg(monkeypatch, {ds.FANOUT_ATTRIBUTION_KEY: ds.FANOUT_ATTRIBUTION_CASE3})
    assert _mw().wrap_tool_call(_req("run_batch_pricing"), _ok).content == "ran"


def test_normal_chat_unaffected(monkeypatch):
    _cfg(monkeypatch, {})
    assert _mw().wrap_tool_call(_req("book_position"), _ok).content == "ran"
