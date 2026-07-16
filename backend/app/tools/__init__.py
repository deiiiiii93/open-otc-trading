"""LangChain @tool wrappers calling into app.services.domains.

Each module is a thin LLM adapter: parse args, call service, shape JSON.
Target ≤30 lines per tool body.

``QUANT_AGENT_TOOLS`` is the curated tool list exposed to LangChain-based
agents. It pulls every domain tool, the sandbox Python tool, the
async-agent dispatch tools, and the reply-options UI tool. The legacy
``services/langchain_tools.py`` re-exports were folded into this package
in P1.8 (Phase 1 cleanup) so domain consumers import their tools from
``app.tools.<domain>`` directly.
"""
from __future__ import annotations

from app.services.async_agents.tools import (
    CancelAsyncAgentTool,
    ListAsyncAgentsTool,
    StartAsyncAgentTool,
)
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.reply_options.tool import ProposeReplyOptionsTool
from app.services.term_form.tool import ProposeTermFormTool
from app.services.sandbox_tool import run_python_tool

from .assemble_breach_report import build_assemble_breach_report_tool
from .desk_workflows import save_desk_workflow_tool
from .market_data import fetch_market_snapshot_tool, list_market_data_profiles_tool
from .portfolios import (
    add_portfolio_sources_tool,
    add_positions_to_portfolio_tool,
    create_portfolio_tool,
    delete_portfolio_tool,
    get_portfolio_tool,
    list_portfolios_tool,
    remove_portfolio_sources_tool,
    remove_positions_from_portfolio_tool,
    set_portfolio_rule_tool,
    update_portfolio_tool,
)
from .positions import (
    book_position_tool,
    cancel_lifecycle_event_tool,
    capture_asian_fixings_tool,
    close_position_tool,
    generate_asian_fixing_schedule_tool,
    get_asian_schedule_tool,
    get_barrier_terms_tool,
    get_latest_position_valuations_tool,
    get_option_core_terms_tool,
    get_product_details_tool,
    get_position_summaries_tool,
    get_positions_tool,
    get_sharkfin_terms_tool,
    get_snowball_ko_schedule_tool,
    get_snowball_terms_tool,
    import_otc_positions_tool,
    mark_knockout_tool,
    query_autocallable_observations_tool,
    query_positions_tool,
    query_positions_near_barrier_tool,
    query_products_tool,
    query_snowball_ko_from_spot_tool,
    settle_position_tool,
)
from .pricing import price_product_tool
from .pricing_profiles import (
    create_pricing_parameter_profile_tool,
    delete_pricing_parameter_profile_tool,
    delete_pricing_parameter_rows_tool,
    get_pricing_parameter_profile_tool,
    list_pricing_parameter_profiles_tool,
    update_pricing_parameter_profile_tool,
    upsert_pricing_parameter_rows_tool,
)
from .assumptions import (
    build_assumption_set_tool,
    get_assumption_set_tool,
    get_instrument_pricing_defaults_tool,
    list_assumption_sets_tool,
    set_instrument_pricing_defaults_tool,
)
from .products import build_product_tool
from .reporting import (
    create_report_tool,
    get_report_tool,
    list_reports_tool,
    run_report_batch_tool,
    write_report_artifact_tool,
)
from .rfq import (
    approve_rfq_tool,
    book_rfq_to_position_tool,
    create_or_update_rfq_draft_tool,
    get_rfq_catalog_tool,
    mark_rfq_client_accepted_tool,
    quote_rfq_tool,
    reject_rfq_tool,
    release_rfq_tool,
    solve_rfq_tool,
    submit_rfq_for_approval_tool,
    validate_rfq_terms_tool,
)
from .hedging import (
    book_hedge_tool,
    get_hedge_bands_tool,
    get_hedgeable_underlyings_tool,
    propose_hedge_tool,
    set_hedge_bands_tool,
)
from .underlyings import register_underlying_tool
from .risk import (
    calculate_risk_tool,
    convert_currency_tool,
    get_latest_risk_run_tool,
    recommend_hedge_tool,
    run_batch_pricing_tool,
)
from .scenario_test import (
    generate_scenario_set_tool,
    get_scenario_test_run_tool,
    list_scenario_library_tool,
    run_scenario_test_tool,
    save_scenario_set_tool,
)
from .backtest import (
    get_backtest_run_tool,
    list_backtest_runs_tool,
    run_backtest_tool,
)
from .greeks_landscape import (
    get_greeks_landscape_run_tool,
    get_latest_greeks_landscape_run_tool,
    run_greeks_landscape_tool,
)
from .product_reference import get_product_reference_doc
from .product_term_schema import get_product_term_schema
from .term_completeness import check_term_completeness
from .record_answer import record_answer_tool


QUANT_AGENT_TOOLS = [
    price_product_tool,
    get_product_reference_doc,
    get_product_term_schema,
    check_term_completeness,
    solve_rfq_tool,
    get_rfq_catalog_tool,
    build_product_tool,
    validate_rfq_terms_tool,
    create_or_update_rfq_draft_tool,
    quote_rfq_tool,
    submit_rfq_for_approval_tool,
    get_positions_tool,
    get_position_summaries_tool,
    query_snowball_ko_from_spot_tool,
    query_positions_near_barrier_tool,
    query_positions_tool,
    query_products_tool,
    get_product_details_tool,
    query_autocallable_observations_tool,
    get_option_core_terms_tool,
    get_barrier_terms_tool,
    get_sharkfin_terms_tool,
    get_asian_schedule_tool,
    get_snowball_terms_tool,
    get_snowball_ko_schedule_tool,
    calculate_risk_tool,
    convert_currency_tool,
    recommend_hedge_tool,
    get_hedgeable_underlyings_tool,
    propose_hedge_tool,
    get_hedge_bands_tool,
    run_report_batch_tool,
    fetch_market_snapshot_tool,
    list_market_data_profiles_tool,
    list_pricing_parameter_profiles_tool,
    get_pricing_parameter_profile_tool,
    list_assumption_sets_tool,
    get_assumption_set_tool,
    get_instrument_pricing_defaults_tool,
    get_latest_position_valuations_tool,
    get_latest_risk_run_tool,
    record_answer_tool,
    list_reports_tool,
    get_report_tool,
    write_report_artifact_tool,
    # Persisted-action / HITL-gated:
    import_otc_positions_tool,
    book_position_tool,
    close_position_tool,
    settle_position_tool,
    mark_knockout_tool,
    cancel_lifecycle_event_tool,
    generate_asian_fixing_schedule_tool,
    capture_asian_fixings_tool,
    run_batch_pricing_tool,
    # Scenario test tools (read + write / HITL-gated):
    list_scenario_library_tool,
    get_scenario_test_run_tool,
    run_scenario_test_tool,
    save_scenario_set_tool,
    generate_scenario_set_tool,
    # Backtest tools (read + write / HITL-gated):
    run_backtest_tool,
    get_backtest_run_tool,
    list_backtest_runs_tool,
    # Greeks Landscape tools (read + write / HITL-gated):
    get_greeks_landscape_run_tool,
    get_latest_greeks_landscape_run_tool,
    run_greeks_landscape_tool,
    # hedging writes (persisted / HITL-gated):
    book_hedge_tool,
    set_hedge_bands_tool,
    register_underlying_tool,
    create_report_tool,
    # Pricing parameter writes (persisted / HITL-gated):
    create_pricing_parameter_profile_tool,
    update_pricing_parameter_profile_tool,
    upsert_pricing_parameter_rows_tool,
    delete_pricing_parameter_rows_tool,
    delete_pricing_parameter_profile_tool,
    set_instrument_pricing_defaults_tool,
    build_assumption_set_tool,
    approve_rfq_tool,
    reject_rfq_tool,
    release_rfq_tool,
    mark_rfq_client_accepted_tool,
    book_rfq_to_position_tool,
    # Portfolio CRUD (read + create):
    list_portfolios_tool,
    get_portfolio_tool,
    create_portfolio_tool,
    update_portfolio_tool,
    delete_portfolio_tool,
    set_portfolio_rule_tool,
    add_positions_to_portfolio_tool,
    remove_positions_from_portfolio_tool,
    add_portfolio_sources_tool,
    remove_portfolio_sources_tool,
    # Sandboxed Python execution (HITL-gated):
    run_python_tool,
    # Async-subagent dispatch (NOT HITL-gated — dispatching is free):
    capability_gated(group=ToolGroup.ASYNC_DISPATCH)(StartAsyncAgentTool()),
    # list_async_agents is read-only status polling; ASYNC_DISPATCH would
    # block it from pet_page (which has no escalation for long_running_work),
    # so a "is my background task done?" query would fail closed. Cancel,
    # however, mutates the task and stays under ASYNC_DISPATCH.
    capability_gated(group=ToolGroup.TASK_POLL)(ListAsyncAgentsTool()),
    capability_gated(group=ToolGroup.ASYNC_DISPATCH)(CancelAsyncAgentTool()),
    # UI-control tool. Available to personas as well as the orchestrator so
    # delegated persona replies can offer the same pickable-choice UX.
    capability_gated(group=ToolGroup.PAGE_ACTION)(ProposeReplyOptionsTool()),
    capability_gated(group=ToolGroup.PAGE_ACTION)(ProposeTermFormTool()),
    save_desk_workflow_tool,
    # Deterministic reconciler for the dynamic-subagents pilot: read-only (no side
    # effects), so it is allowed inside the read-only fan-out assemble step.
    capability_gated(group=ToolGroup.DOMAIN_READ)(build_assemble_breach_report_tool()),
]


def all_agent_tools():
    """Return the registered agent tool objects (each exposes `.name`)."""
    from app.tools import QUANT_AGENT_TOOLS
    return list(QUANT_AGENT_TOOLS)


__all__ = ["QUANT_AGENT_TOOLS", "all_agent_tools"]
