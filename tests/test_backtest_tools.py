"""Tests for backtest agent tool wrappers (Task 3.3)."""
from app.tools import backtest as bt


def test_tool_names_and_schema_forbid_extra():
    assert bt.run_backtest_tool.name == "run_backtest"
    assert bt.get_backtest_run_tool.name == "get_backtest_run"
    assert bt.list_backtest_runs_tool.name == "list_backtest_runs"
    # extra='forbid' enforced on the input schema
    assert bt.RunBacktestInput.model_config.get("extra") == "forbid"
    assert bt.GetBacktestRunInput.model_config.get("extra") == "forbid"
    assert bt.ListBacktestRunsInput.model_config.get("extra") == "forbid"


def test_run_backtest_input_defaults():
    inp = bt.RunBacktestInput(portfolio_id=1, start_date="2024-01-02", end_date="2024-06-30")
    assert inp.engine == "quad"
    assert inp.vol_source == "realized"
    assert inp.vol_window == 20
    assert inp.config == {}
    assert inp.position_ids is None
    assert inp.pricing_parameter_profile_id is None


def test_tools_in_allowlist():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert "run_backtest" in DEEP_AGENT_TOOL_NAMES
    assert "get_backtest_run" in DEEP_AGENT_TOOL_NAMES
    assert "list_backtest_runs" in DEEP_AGENT_TOOL_NAMES


def test_tools_in_quant_agent_tools():
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "run_backtest" in names
    assert "get_backtest_run" in names
    assert "list_backtest_runs" in names
