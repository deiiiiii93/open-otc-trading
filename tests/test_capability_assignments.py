"""CI guard: every tool exposed to the agent has been wrapped with the gate.

A missing ``__capability_group__`` means the tool was added without going
through ``@capability_gated``, which silently lets it run under any
envelope — including ``pet_page``. Fail loudly here.
"""
from __future__ import annotations

import pytest

from app.services.deep_agent.envelopes import ToolGroup
from app.tools import QUANT_AGENT_TOOLS


def test_quant_agent_tools_count_unchanged():
    """Keep the exposed tool registry intentional as new gated tools land."""
    # 79 == 80 prior - 1: price_positions and run_risk were unified into the
    # single run_batch_pricing tool (one queued run writes valuations + risk).
    # +5 scenario-test tools (list_scenario_library, run_scenario_test,
    #   get_scenario_test_run, save_scenario_set, generate_scenario_set).
    # +3 backtest tools (run_backtest, get_backtest_run, list_backtest_runs).
    # +3 Greeks Landscape tools (run + read by id/latest).
    # +2 Asian fixing tools (generate_asian_fixing_schedule, capture_asian_fixings).
    # +1 desk-workflow authoring tool (save_desk_workflow).
    # +1 dynamic-subagents reconciler (assemble_breach_report).
    # +1 underlying-tag registration tool (register_underlying).
    # +1 product-semantics reader (get_product_reference_doc).
    # +1 contract-grounded completeness checker (check_term_completeness).
    assert len(QUANT_AGENT_TOOLS) == 97


def test_every_tool_has_capability_group():
    missing = [t.name for t in QUANT_AGENT_TOOLS if not hasattr(t, "__capability_group__")]
    assert missing == [], (
        f"Tools missing __capability_group__ (wrap with @capability_gated): {missing}"
    )


def test_capability_group_is_a_valid_tool_group():
    for t in QUANT_AGENT_TOOLS:
        group = getattr(t, "__capability_group__", None)
        assert isinstance(group, ToolGroup), (
            f"Tool {t.name!r} has __capability_group__={group!r}, expected a ToolGroup"
        )


@pytest.mark.parametrize(
    "tool_name, expected_group",
    [
        # Spot-check the most security-relevant assignments.
        ("create_portfolio", ToolGroup.DOMAIN_WRITE),
        ("delete_portfolio", ToolGroup.DOMAIN_WRITE),
        ("list_portfolios", ToolGroup.DOMAIN_READ),
        ("run_batch_pricing", ToolGroup.DOMAIN_WRITE),
        ("run_greeks_landscape", ToolGroup.DOMAIN_WRITE),
        ("get_greeks_landscape_run", ToolGroup.DOMAIN_READ),
        ("get_latest_greeks_landscape_run", ToolGroup.DOMAIN_READ),
        ("calculate_risk", ToolGroup.DOMAIN_READ),
        ("close_position", ToolGroup.DOMAIN_WRITE),
        ("settle_position", ToolGroup.DOMAIN_WRITE),
        ("mark_knockout", ToolGroup.DOMAIN_WRITE),
        ("cancel_lifecycle_event", ToolGroup.DOMAIN_WRITE),
        ("price_product", ToolGroup.DOMAIN_READ),
        ("list_pricing_parameter_profiles", ToolGroup.DOMAIN_READ),
        ("query_snowball_ko_from_spot", ToolGroup.DOMAIN_READ),
        ("create_report", ToolGroup.DOMAIN_WRITE),
        ("write_report_artifact", ToolGroup.DOMAIN_WRITE),
        ("list_reports", ToolGroup.DOMAIN_READ),
        ("run_python", ToolGroup.DETERMINISTIC_PY),
        ("start_async_agent", ToolGroup.ASYNC_DISPATCH),
        # list_async_agents is read-only polling, not dispatch — pet_page must
        # be able to poll background-task status without escalating.
        ("list_async_agents", ToolGroup.TASK_POLL),
        ("cancel_async_agent", ToolGroup.ASYNC_DISPATCH),
        ("propose_reply_options", ToolGroup.PAGE_ACTION),
        # Hedging strategy tools: reads are loopable, writes persist (HITL-gated).
        ("get_hedgeable_underlyings", ToolGroup.DOMAIN_READ),
        ("propose_hedge", ToolGroup.DOMAIN_READ),
        ("get_hedge_bands", ToolGroup.DOMAIN_READ),
        ("book_hedge", ToolGroup.DOMAIN_WRITE),
        ("set_hedge_bands", ToolGroup.DOMAIN_WRITE),
        # Pricing parameter tools: profile CRUD + assumption pipeline.
        ("get_pricing_parameter_profile", ToolGroup.DOMAIN_READ),
        ("list_assumption_sets", ToolGroup.DOMAIN_READ),
        ("get_instrument_pricing_defaults", ToolGroup.DOMAIN_READ),
        ("create_pricing_parameter_profile", ToolGroup.DOMAIN_WRITE),
        ("delete_pricing_parameter_profile", ToolGroup.DOMAIN_WRITE),
        ("set_instrument_pricing_defaults", ToolGroup.DOMAIN_WRITE),
        ("build_assumption_set", ToolGroup.DOMAIN_WRITE),
    ],
)
def test_spot_check_assignments(tool_name, expected_group):
    matching = [t for t in QUANT_AGENT_TOOLS if t.name == tool_name]
    assert matching, f"tool {tool_name!r} not found in QUANT_AGENT_TOOLS"
    assert matching[0].__capability_group__ is expected_group
