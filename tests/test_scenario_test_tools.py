from app.tools import QUANT_AGENT_TOOLS
from app.services.agents import DEEP_AGENT_TOOL_NAMES


def test_tools_registered():
    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert {"list_scenario_library", "run_scenario_test",
            "get_scenario_test_run", "save_scenario_set"} <= names


def test_tools_in_deep_agent_allowlist():
    assert {"list_scenario_library", "run_scenario_test",
            "get_scenario_test_run", "save_scenario_set"} <= DEEP_AGENT_TOOL_NAMES


def test_list_library_tool_runs():
    from app.tools.scenario_test import list_scenario_library_tool
    out = list_scenario_library_tool.invoke({})
    assert "predefined" in out


def test_generate_scenario_set_tool_registered():
    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "generate_scenario_set" in names
    assert "generate_scenario_set" in DEEP_AGENT_TOOL_NAMES


def test_generate_scenario_set_tool_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.services.domains.scenario_catalog._sets_dir", lambda: tmp_path)
    from app.tools.scenario_test import generate_scenario_set_tool
    out = generate_scenario_set_tool.invoke({
        "name": "tool_grid",
        "axes": [{"param": "spot", "start": -0.1, "stop": 0.1, "step": 0.1}],
    })
    assert out["num_scenarios"] == 3
    assert out["name"] == "tool_grid"
