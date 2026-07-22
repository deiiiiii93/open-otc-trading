from __future__ import annotations

from app.services.deep_agent.hitl import (
    INTERRUPT_TOOL_NAMES,
    interrupt_on_config,
    run_python_requires_hitl,
)


def test_interrupt_tool_names_covers_all_state_mutating_tools():
    assert set(INTERRUPT_TOOL_NAMES) == {
        "run_batch_pricing",
        "run_greeks_landscape",
        "create_report",
        "create_or_update_rfq_draft",
        "quote_rfq",
        "submit_rfq_for_approval",
        "approve_rfq",
        "reject_rfq",
        "release_rfq",
        "mark_rfq_client_accepted",
        "book_rfq_to_position",
        "book_position",
        "book_hedge",
        "register_underlying",
        "set_hedge_bands",
        "import_otc_positions",
        "close_position",
        "settle_position",
        "mark_knockout",
        "cancel_lifecycle_event",
        "delete_portfolio",
        "set_portfolio_rule",
        "remove_positions_from_portfolio",
        "create_portfolio",
        "update_portfolio",
        "add_positions_to_portfolio",
        "add_portfolio_sources",
        "remove_portfolio_sources",
        "create_pricing_parameter_profile",
        "generate_pricing_parameters_from_curves",
        "update_pricing_parameter_profile",
        "upsert_pricing_parameter_rows",
        "delete_pricing_parameter_rows",
        "delete_pricing_parameter_profile",
        "set_instrument_pricing_defaults",
        "build_assumption_set",
        "run_python",
    }


def test_interrupt_tools_are_bound_deep_agent_tools():
    """A HITL card must never surface for a tool the runtime cannot execute.

    The interrupt middleware matches tool-CALL names, but execution requires
    the tool in select_deep_agent_tools' allowlist — a gap here means the user
    approves a card and then gets "book_hedge is not a valid tool" (found live
    in thread 44's smoke replay). No exceptions: every interrupt-listed tool
    must be bound (the former portfolio-CRUD rest_only pin was closed
    2026-06-05 by binding the three tools).
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    unbound = set(INTERRUPT_TOOL_NAMES) - DEEP_AGENT_TOOL_NAMES
    assert unbound == set(), (
        f"Interrupt-listed tools not bound to the deep agent: {sorted(unbound)}"
    )


def test_hedge_portfolio_skill_tools_are_bound():
    """The hedge-portfolio workflow's whole procedure must be executable:
    guard (get_hedgeable_underlyings) -> solve (propose_hedge, get_hedge_bands)
    -> book (book_hedge, set_hedge_bands)."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    required = {
        "get_hedgeable_underlyings",
        "propose_hedge",
        "get_hedge_bands",
        "book_hedge",
        "set_hedge_bands",
    }
    missing = required - DEEP_AGENT_TOOL_NAMES
    assert missing == set(), f"hedge-portfolio tools not bound: {sorted(missing)}"


def test_interrupt_on_config_only_allows_approve_and_reject():
    config = interrupt_on_config()

    for tool_name, cfg in config.items():
        assert tool_name in INTERRUPT_TOOL_NAMES
        assert cfg["allowed_decisions"] == ["approve", "reject"]

    assert "run_python" not in config


def test_run_python_hitl_is_argument_aware():
    assert run_python_requires_hitl({"writes_artifacts": True}) is True
    assert run_python_requires_hitl({"writes_artifacts": False}) is False
    assert run_python_requires_hitl({}) is False


def test_yolo_mode_uses_langchain_auto_approval_for_write_tools():
    config = interrupt_on_config(yolo_mode=True)

    # LangChain auto-approves tools omitted from interrupt_on, so YOLO removes
    # ordinary write tools while keeping irreversible operations gated.
    assert "run_batch_pricing" not in config
    assert "create_report" not in config
    assert "run_python" not in config
    assert "set_hedge_bands" not in config
    assert "create_portfolio" not in config  # portfolio writes are "write"-level

    for tool_name in (
        "approve_rfq",
        "release_rfq",
        "book_rfq_to_position",
        "book_hedge",
        "register_underlying",
        "cancel_lifecycle_event",
        "delete_portfolio",
        "remove_positions_from_portfolio",
    ):
        assert config[tool_name]["allowed_decisions"] == ["approve", "reject"]


def test_headless_mode_clears_all_hitl_including_irreversible():
    # The new "yolo" (headless) mode omits ALL HITL — unlike "auto"
    # (yolo_mode=True) which keeps irreversible operations gated. With an empty
    # interrupt_on, LangChain auto-approves every tool, so a headless arena run
    # can complete irreversible operations like book_position / approve_rfq.
    assert interrupt_on_config(headless=True) == {}
    # headless dominates the auto-level yolo_mode flag.
    assert interrupt_on_config(yolo_mode=True, headless=True) == {}


def test_yolo_mode_cost_preview_middleware_interrupts_long_write(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool

    from app.services.deep_agent.capability_gate import (
        LONG_RUNNING_SECONDS,
        capability_gated,
    )
    from app.services.deep_agent.cost_preview_hitl import LongRunningCostHITLMiddleware
    from app.services.deep_agent.envelopes import ToolGroup

    @capability_gated(
        group=ToolGroup.DOMAIN_WRITE,
        cost_estimator=lambda _args: LONG_RUNNING_SECONDS + 2,
    )
    @tool("slow_yolo_write")
    def slow_yolo_write_tool(portfolio_id: int) -> dict:
        """Test tool."""
        return {"portfolio_id": portfolio_id}

    captured = []

    def fake_interrupt(request):
        captured.append(request)
        return {"decisions": [{"type": "approve"}]}

    monkeypatch.setattr(
        "app.services.deep_agent.cost_preview_hitl.interrupt",
        fake_interrupt,
    )
    middleware = LongRunningCostHITLMiddleware(
        tools=[slow_yolo_write_tool],
        enabled=True,
    )
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "slow_yolo_write",
                "args": {"portfolio_id": 7},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )

    result = middleware.after_model({"messages": [message]}, None)  # type: ignore[arg-type]

    assert result is not None
    assert captured
    action = captured[0]["action_requests"][0]
    assert action["name"] == "slow_yolo_write"
    assert action["args"] == {"portfolio_id": 7}
    assert "above the 30s threshold" in action["description"]


def test_yolo_mode_cost_preview_middleware_skips_after_confirmation(monkeypatch):
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool

    from app.services.deep_agent.capability_gate import (
        LONG_RUNNING_SECONDS,
        capability_gated,
    )
    from app.services.deep_agent.cost_preview_hitl import LongRunningCostHITLMiddleware
    from app.services.deep_agent.envelopes import ToolGroup

    @capability_gated(
        group=ToolGroup.DOMAIN_WRITE,
        cost_estimator=lambda _args: LONG_RUNNING_SECONDS + 2,
    )
    @tool("confirmed_slow_write")
    def confirmed_slow_write_tool(portfolio_id: int) -> dict:
        """Test tool."""
        return {"portfolio_id": portfolio_id}

    monkeypatch.setattr(
        "app.services.deep_agent.cost_preview_hitl._confirmed_cost_preview",
        lambda: True,
    )
    middleware = LongRunningCostHITLMiddleware(
        tools=[confirmed_slow_write_tool],
        enabled=True,
    )
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "confirmed_slow_write",
                "args": {"portfolio_id": 7},
                "id": "call-1",
                "type": "tool_call",
            }
        ],
    )

    assert middleware.after_model({"messages": [message]}, None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Task 5: pending_actions_from_interrupts + build_resume_command
# ---------------------------------------------------------------------------

from langgraph.types import Command, Interrupt

from app.schemas import AgentActionProposal
from app.services.deep_agent.hitl import (
    build_resume_command,
    pending_actions_from_interrupts,
)


def _fake_interrupt(interrupt_id: str, action_name: str, args: dict) -> Interrupt:
    # Mirrors langchain.agents.middleware.human_in_the_loop.HumanInTheLoopMiddleware:
    # interrupt({"action_requests": [...], "review_configs": [...]})
    return Interrupt(
        value={
            "action_requests": [
                {"name": action_name, "args": args, "description": f"Run {action_name}"}
            ],
            "review_configs": [
                {"action_name": action_name, "allowed_decisions": ["approve", "reject"]}
            ],
        },
        id=interrupt_id,
    )


def test_pending_actions_from_interrupts_projects_each_action_request():
    interrupts = [
        _fake_interrupt("intr-1", "run_batch_pricing", {"portfolio_id": 7, "method": "summary"})
    ]

    actions = pending_actions_from_interrupts(interrupts, persona="risk_manager")

    assert len(actions) == 1
    proposal = actions[0]
    assert isinstance(proposal, AgentActionProposal)
    assert proposal.id == "intr-1:0"
    assert proposal.tool_name == "run_batch_pricing"
    assert proposal.payload == {"portfolio_id": 7, "method": "summary"}
    assert proposal.requires_confirmation is True
    assert proposal.status == "pending"
    assert proposal.persona == "risk_manager"
    # Description from the interrupt becomes the summary fallback
    assert "run_batch_pricing" in proposal.summary


def test_pending_actions_from_interrupts_attaches_task_source_meta():
    interrupts = [
        _fake_interrupt("intr-1", "run_batch_pricing", {"portfolio_id": 7, "method": "summary"})
    ]

    actions = pending_actions_from_interrupts(
        interrupts,
        persona="risk_manager",
        source_meta={
            "task_id": 42,
            "session_id": 17,
            "context_pack_id": 91,
            "checkpointer_key": "workflow:5:persona:risk_manager:episode:1:task:42",
            "workflow_id": 5,
            "envelope_final": "desk_workflow",
            "agent_runtime": "deepagents",
        },
    )

    source_meta = actions[0].source_meta
    assert source_meta["task_id"] == 42
    assert source_meta["session_id"] == 17
    assert source_meta["context_pack_id"] == 91
    assert source_meta["checkpointer_key"].endswith(":task:42")
    assert source_meta["workflow_id"] == 5
    assert source_meta["agent_runtime"] == "deepagents"
    assert source_meta["audit"]["tool_call_id"] == "intr-1"
    assert source_meta["audit"]["tool_name"] == "run_batch_pricing"
    assert source_meta["audit"]["persona"] == "risk_manager"
    assert source_meta["audit"]["emitted_at"]


def test_pending_actions_from_interrupts_assigns_risk_level_per_tool():
    interrupts = [
        _fake_interrupt("intr-2", "cancel_lifecycle_event", {"lifecycle_event_id": 42}),
    ]

    actions = pending_actions_from_interrupts(interrupts, persona="high_board")

    assert actions[0].risk_level == "irreversible"
    assert actions[0].label == "Cancel lifecycle event"


def test_build_resume_command_for_approve_decision():
    cmd = build_resume_command("approve")

    assert isinstance(cmd, Command)
    assert cmd.resume == {"decisions": [{"type": "approve"}]}


def test_build_resume_command_for_reject_decision_with_message():
    cmd = build_resume_command("reject", message="User dismissed the action.")

    assert cmd.resume == {
        "decisions": [{"type": "reject", "message": "User dismissed the action."}]
    }


def test_build_resume_command_rejects_unknown_decision():
    import pytest

    with pytest.raises(ValueError, match="HITL decision"):
        build_resume_command("ignore")


def test_hitl_registers_portfolio_tools():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES, _LABEL_BY_TOOL, _RISK_LEVEL_BY_TOOL,
    )
    for tool_name in ("delete_portfolio", "set_portfolio_rule", "remove_positions_from_portfolio"):
        assert tool_name in INTERRUPT_TOOL_NAMES
        assert tool_name in _LABEL_BY_TOOL
        assert tool_name in _RISK_LEVEL_BY_TOOL


def test_book_position_summary_is_human_readable_not_raw_json():
    """The confirmation card summary must read like a sentence, not dump the
    nested product dict (terms + synthesized schedules) as raw JSON.
    """
    from app.services.deep_agent.hitl import _summary_for

    action_request = {
        "name": "book_position",
        "args": {
            "portfolio_id": 6,
            "product": {
                "product_family": "snowball",
                "quantark_class": "SnowballOption",
                "underlying": "000905.SH",
                "terms": {"barrier_config": {"ko_observation_schedule": {"records": [1, 2, 3]}}},
                "components": [],
            },
            "quantity": 100,
            "entry_price": 5800.0,
            "engine_name": "SnowballQuadEngine",
        },
    }
    summary = _summary_for(action_request)

    # No raw dict/JSON braces leaking into the human-facing line.
    assert "{" not in summary and "}" not in summary
    assert "000905.SH" in summary
    assert "portfolio 6" in summary
    assert "100" in summary


def test_summary_compacts_nested_dict_args_generically():
    """Any tool with a dict/list arg gets a compact placeholder, never a blob."""
    from app.services.deep_agent.hitl import _summary_for

    summary = _summary_for(
        {"name": "some_tool", "args": {"spec": {"a": 1, "b": 2}, "rows": [1, 2], "n": 3}}
    )
    assert "{" not in summary and "}" not in summary
    assert "n=3" in summary


def test_register_underlying_is_irreversible_risk_not_write():
    from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL

    # "write" risk bypasses confirmation under BOTH auto and yolo mode
    # (interrupt_on_config's yolo_mode flag) -- that would let auto mode
    # silently persist an unvetted underlying, contradicting the requirement
    # that only yolo auto-adds. "irreversible" stays gated under auto.
    assert _RISK_LEVEL_BY_TOOL["register_underlying"] == "irreversible"


def test_summarize_register_underlying_distinguishes_create_vs_tag(session):
    """Uses the shared `session` fixture from tests/conftest.py:63, which
    already calls database.configure_database()+init_db() against a temp
    SQLite file — _summarize_register_underlying's own internal
    database.SessionLocal() call transparently hits that same temp DB, no
    monkeypatching needed (this file otherwise has no DB fixtures, unlike
    test_tools_positions.py's autouse `_db`, so `session` must be requested
    as an explicit test parameter here to trigger that configuration)."""
    from app.models import Instrument
    from app.services.deep_agent import hitl

    new_summary = hitl._summarize_register_underlying({"symbol": "BRANDNEW.SH"})
    assert "NEW" in new_summary
    assert "BRANDNEW.SH" in new_summary

    session.add(Instrument(symbol="UNTAGGED.SH", kind="index", status="draft", tags=[]))
    session.commit()
    tag_summary = hitl._summarize_register_underlying({"symbol": "UNTAGGED.SH"})
    assert "UNTAGGED.SH" in tag_summary
    assert "underlying" in tag_summary.lower()


def test_generate_from_curves_is_hitl_write():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    name = "generate_pricing_parameters_from_curves"
    assert name in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL[name] == "write"
    assert name in _LABEL_BY_TOOL
