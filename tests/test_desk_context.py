"""Tests for desk-context propagation to persona subagents (#1)."""
from __future__ import annotations

from app.services.deep_agent.desk_context import (
    SCOPE_KEYS,
    extract_scope,
    merge_scope,
    render_desk_context_block,
)


def _call(name, args):
    return {"name": name, "args": args}


def test_extract_scope_pulls_known_keys_from_tool_args():
    calls = [
        _call("get_latest_risk_run", {"portfolio_id": 2}),
        _call("run_batch_pricing", {"portfolio_id": 2, "pricing_parameter_profile_id": 2}),
    ]
    scope = extract_scope(calls)
    assert scope == {"portfolio_id": 2, "pricing_parameter_profile_id": 2}


def test_extract_scope_ignores_unknown_keys_and_none_values():
    calls = [_call("run_backtest", {"portfolio_id": 2, "method": "summary", "position_ids": None})]
    scope = extract_scope(calls)
    assert scope == {"portfolio_id": 2}  # method not a scope key; None position_ids dropped


def test_extract_scope_captures_dates_and_position_ids():
    calls = [_call("run_backtest", {"portfolio_id": 2, "start_date": "2026-03-24", "end_date": "2026-06-24"}),
             _call("run_greeks_landscape", {"portfolio_id": 2, "position_ids": [8]})]
    scope = extract_scope(calls)
    assert scope["start_date"] == "2026-03-24"
    assert scope["end_date"] == "2026-06-24"
    assert scope["position_ids"] == [8]


def test_merge_scope_last_write_wins_per_key():
    existing = {"portfolio_id": 2, "pricing_parameter_profile_id": 1}
    new = {"pricing_parameter_profile_id": 2, "start_date": "2026-03-24"}
    merged = merge_scope(existing, new)
    assert merged == {"portfolio_id": 2, "pricing_parameter_profile_id": 2, "start_date": "2026-03-24"}
    # inputs not mutated
    assert existing == {"portfolio_id": 2, "pricing_parameter_profile_id": 1}


def test_render_block_lists_scope_and_is_authoritative():
    block = render_desk_context_block({"portfolio_id": 2, "pricing_parameter_profile_id": 2})
    assert "portfolio_id" in block and "2" in block
    assert "authoritative" in block.lower() or "required_context" in block.lower()
    assert block.startswith("## ")


def test_render_block_empty_scope_returns_empty_string():
    assert render_desk_context_block({}) == ""


def test_scope_keys_cover_required_context_fields():
    # the fields the failing skills demand must all be capturable
    for k in ("portfolio_id", "pricing_parameter_profile_id", "start_date", "end_date"):
        assert k in SCOPE_KEYS


# --- middleware behavior ---------------------------------------------------

def test_middleware_after_model_snoops_scope_into_state():
    from app.services.deep_agent.desk_context import DeskContextMiddleware
    from langchain_core.messages import AIMessage

    mw = DeskContextMiddleware()
    ai = AIMessage(content="", tool_calls=[
        {"name": "run_batch_pricing", "args": {"portfolio_id": 2, "pricing_parameter_profile_id": 2}, "id": "c1"},
    ])
    out = mw.after_model({"messages": [ai]}, None, None)
    assert out == {"desk_context": {"portfolio_id": 2, "pricing_parameter_profile_id": 2}}


def test_middleware_after_model_merges_with_existing_and_noops_when_unchanged():
    from app.services.deep_agent.desk_context import DeskContextMiddleware
    from langchain_core.messages import AIMessage

    mw = DeskContextMiddleware()
    ai = AIMessage(content="", tool_calls=[{"name": "get_latest_risk_run", "args": {"portfolio_id": 2}, "id": "c1"}])
    # already have portfolio_id=2 -> no change -> None (no state write)
    assert mw.after_model({"messages": [ai], "desk_context": {"portfolio_id": 2}}, None, None) is None


def test_middleware_injects_block_into_system_prompt():
    from app.services.deep_agent.desk_context import DeskContextMiddleware
    from langchain_core.messages import SystemMessage

    class _Req:
        def __init__(self):
            self.state = {"desk_context": {"portfolio_id": 2}}
            self.system_message = SystemMessage(content="BASE PROMPT")
        def override(self, *, system_message):
            self.system_message = system_message
            return self

    mw = DeskContextMiddleware()
    captured = {}
    def handler(req):
        captured["sys"] = req.system_message.content
        return "ok"
    mw.wrap_model_call(_Req(), handler)
    assert "BASE PROMPT" in captured["sys"]
    assert "Desk session context" in captured["sys"]
    assert "portfolio_id: 2" in captured["sys"]


def test_middleware_no_injection_when_no_desk_context():
    from app.services.deep_agent.desk_context import DeskContextMiddleware
    from langchain_core.messages import SystemMessage

    class _Req:
        state = {}
        system_message = SystemMessage(content="BASE")
        def override(self, **k):  # pragma: no cover - should not be called
            raise AssertionError("override should not be called")

    mw = DeskContextMiddleware()
    seen = {}
    def handler(req):
        seen["req"] = req
        return "ok"
    mw.wrap_model_call(_Req(), handler)
    assert seen["req"].system_message.content == "BASE"  # unchanged, passthrough
