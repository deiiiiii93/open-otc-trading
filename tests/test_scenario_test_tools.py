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


def test_run_scenario_test_missing_set_error_carries_absence_steering(monkeypatch):
    # The "not found" error is the one channel guaranteed to reach the model's
    # decision point (inside any persona, under any orchestrator) — it must say
    # what to do next, or the model routes around it by substituting/inventing
    # (trap research 2026-08-17: 94/99 trials ran a stand-in).
    import pytest
    from app.tools import scenario_test as st

    def _missing(*a, **kw):
        raise ValueError("Scenario set not found: stagflation-shock-2011")

    monkeypatch.setattr(st.scenario_test_runner, "queue_scenario_test", _missing)
    with pytest.raises(ValueError) as exc:
        st.run_scenario_test_tool.invoke(
            {"portfolio_id": 1, "scenario_set": "stagflation-shock-2011"})
    msg = str(exc.value)
    assert "Scenario set not found: stagflation-shock-2011" in msg
    assert "report" in msg.lower()
    assert "do not substitute" in msg.lower()


def test_run_scenario_test_unknown_predefined_error_carries_absence_steering(monkeypatch):
    import pytest
    from app.tools import scenario_test as st

    def _missing(*a, **kw):
        raise ValueError("Unknown predefined scenario 'stagflation-shock-2011'")

    monkeypatch.setattr(st.scenario_test_runner, "queue_scenario_test", _missing)
    with pytest.raises(ValueError) as exc:
        st.run_scenario_test_tool.invoke(
            {"portfolio_id": 1, "predefined": ["stagflation-shock-2011"]})
    assert "do not substitute" in str(exc.value).lower()


def test_run_scenario_test_unrelated_errors_not_steered(monkeypatch):
    import pytest
    from app.tools import scenario_test as st

    def _other(*a, **kw):
        raise ValueError("Portfolio not found: 99")

    monkeypatch.setattr(st.scenario_test_runner, "queue_scenario_test", _other)
    with pytest.raises(ValueError) as exc:
        st.run_scenario_test_tool.invoke({"portfolio_id": 99, "predefined": ["market_crash"]})
    assert str(exc.value) == "Portfolio not found: 99"


def test_set_creation_tools_scoped_to_explicit_user_request():
    # The description is what a persona chooses by (discoverability-gates lesson):
    # generation must read as "for sets the user asks to create", never as a
    # fallback for a named set that failed to resolve.
    from app.tools.scenario_test import generate_scenario_set_tool, save_scenario_set_tool
    for tool_obj in (generate_scenario_set_tool, save_scenario_set_tool):
        desc = (tool_obj.description or "").lower()
        assert "explicitly asks" in desc
        assert "report" in desc and "exist" in desc


def test_run_scenario_test_skill_has_absence_stop_condition():
    # Product-side trap guardrail (research 2026-08-17): the skill's stop
    # conditions must give ABSENCE a sanctioned script — mirror of the
    # fetch-market-data never-substitute clause. Body stays under the 500-token
    # lint cap (this clause is paid for by trims elsewhere in the body).
    from pathlib import Path
    from app.services.deep_agent.skill_lint import count_body_tokens, parse_skill_file
    from app.services.deep_agent.skills_paths import SKILLS_ROOT

    path = Path(SKILLS_ROOT) / "workflows" / "risk" / "run-scenario-test" / "SKILL.md"
    parsed = parse_skill_file(path)
    body = parsed.body.lower()
    assert "does not exist" in body
    assert "substitute" in body
    assert count_body_tokens(parsed.body) <= 500


def test_risk_manager_persona_has_absence_handling_line():
    # The persona prompt is the decision-maker for scenario work; before this
    # line it carried ZERO absence guidance (trap research 2026-08-17).
    from pathlib import Path
    import app
    prompt = (Path(app.__file__).parent
              / "services" / "deep_agent" / "prompts" / "risk_manager.md").read_text()
    low = prompt.lower()
    assert "does not resolve" in low or "does not exist" in low
    assert "substitute" in low
