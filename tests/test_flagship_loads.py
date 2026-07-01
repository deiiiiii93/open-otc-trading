from app.golden_workflows.registry import get_workflow

def test_flagship_has_seven_steps_and_narration():
    wf = get_workflow("risk-manager-control-day")
    assert wf.persona == "risk_manager"
    assert len(wf.steps) == 7
    assert len(wf.narration) == 7
    assert wf.steps[1].expected_tools[0].name == "run_batch_pricing"

def test_flagship_objective_point_manifest_is_31():
    wf = get_workflow("risk-manager-control-day")
    skills = len(wf.steps)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success_assertions = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success_assertions) == (7, 10, 8, 6)
    assert skills + tools + step_assertions + success_assertions == 31

def test_flagship_exact_ordered_manifest():
    """Pin the exact spec §4 content, not just counts."""
    wf = get_workflow("risk-manager-control-day")
    skills = [s.expected_skill for s in wf.steps]
    assert skills == ["read-risk-result", "run-risk", "read-risk-result",
                      "run-greeks-landscape", "run-scenario-test", "run-backtest",
                      "generate-report"]
    tools_per_step = [[t.name for t in s.expected_tools] for s in wf.steps]
    assert tools_per_step == [
        ["get_latest_risk_run"], ["run_batch_pricing"], ["get_latest_risk_run"],
        ["run_greeks_landscape", "get_greeks_landscape_run"],
        ["run_scenario_test", "get_scenario_test_run"],
        ["run_backtest", "get_backtest_run"], ["write_report_artifact"]]
    replays = [s.replay for s in wf.steps]
    assert len(set(replays)) == 7  # all distinct
    success_types = sorted(a.type for a in wf.success.assertions)
    assert success_types == ["artifact_exists", "skills_routed_sequence",
                             "task_returned_id", "task_returned_id",
                             "task_returned_id", "task_returned_id"]
    assert len(wf.success.rubric) == 4
