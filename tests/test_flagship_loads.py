from app.golden_workflows.registry import get_workflow, get_workflow_bundle

def test_flagship_has_nine_steps_and_narration():
    wf = get_workflow("risk-manager-control-day")
    assert wf.persona == "risk_manager"
    assert len(wf.steps) == 9
    assert len(wf.narration) == 9
    assert wf.steps[1].expected_tools[0].name == "run_batch_pricing"

def test_flagship_objective_point_manifest_is_39():
    wf = get_workflow("risk-manager-control-day")
    skills = sum(1 for s in wf.steps if s.expected_skill is not None)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success_assertions = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success_assertions) == (6, 11, 21, 1)
    assert skills + tools + step_assertions + success_assertions == 39

def test_flagship_exact_ordered_manifest():
    """Pin the exact v2 manifest content, not just counts."""
    wf = get_workflow("risk-manager-control-day")
    skills = [s.expected_skill for s in wf.steps]
    # None = repeat-skill / no-skill steps (skills_routed dedup blind spot)
    assert skills == ["read-risk-result", "run-risk", None,
                      "run-greeks-landscape", None, "run-scenario-test",
                      "run-backtest", None, "generate-report"]
    tools_per_step = [[t.name for t in s.expected_tools] for s in wf.steps]
    assert tools_per_step == [
        ["get_latest_risk_run"], ["run_batch_pricing"], ["get_latest_risk_run"],
        ["run_greeks_landscape", "get_greeks_landscape_run"], [],
        ["run_scenario_test", "get_scenario_test_run"],
        ["run_backtest", "get_backtest_run"], ["list_scenario_library"],
        ["write_report_artifact"]]
    replays = [s.replay for s in wf.steps]
    assert len(set(replays)) == 9  # all distinct
    success_types = sorted(a.type for a in wf.success.assertions)
    assert success_types == ["tools_routed_sequence"]
    assert len(wf.success.rubric) == 2  # judge rubric reduced to 2 subjective points

def test_flagship_rubric_is_two_subjective_points():
    """The judge rubric is reduced to genuinely-subjective points only; the five
    deterministic-redundant points now live solely in the objective checks."""
    from app.services.arena.judge import _collect_rubric_points
    loaded = get_workflow_bundle("risk-manager-control-day")
    pts = _collect_rubric_points(loaded)
    assert len(pts) == 2
    joined = " ".join(pts).lower()
    assert "synthesis" in joined and "analytical" in joined
    assert "reasoning depth" not in joined  # forbidden: rewards verbosity


def test_flagship_grounding_and_trap_assertions():
    """Pin the discrimination-bearing assertion details."""
    wf = get_workflow("risk-manager-control-day")
    # Step 5 (grid): two session-scope grounding checks + recompute guard
    grid = wf.steps[4]
    quotes = [a for a in grid.assertions if a.type == "response_quotes_tool_value"]
    assert [q.scope for q in quotes] == ["session", "session"]
    assert quotes[0].path == "results.portfolio.raw[spot_shift_pct=10.0].gamma"
    assert quotes[1].path == "results.portfolio.raw[spot_shift_pct=-20.0].delta"
    assert any(a.type == "tool_not_called" and a.name == "run_greeks_landscape"
               for a in grid.assertions)
    # Step 6 (scenario): exact-args with both conventions + exclusive carriers
    scen = wf.steps[5]
    called = [a for a in scen.assertions if a.type == "tool_called"][0]
    assert called.args_any_of == [{"predefined": ["market_crash"]},
                                  {"scenario_set": "market-crash"}]
    assert called.exclusive_keys == ["predefined", "custom", "scenario_set"]
    cvar = [a for a in scen.assertions
            if a.type == "response_quotes_tool_value"][0]
    assert cvar.match == "magnitude"
    # Step 8 (trap): verification is mandatory, launching is forbidden
    trap = wf.steps[7]
    assert trap.expected_tools[0].name == "list_scenario_library"
    assert any(a.type == "tool_not_called" and a.name == "run_scenario_test"
               for a in trap.assertions)
    # Step 9 (report): legacy job forbidden, synthesis coverage checked
    report = wf.steps[8]
    assert any(a.type == "tool_not_called" and a.name == "create_report"
               for a in report.assertions)
    contains = [a for a in report.assertions if a.type == "artifact_contains"]
    assert len(contains) == 3

def test_flagship_report_step_names_a_format():
    """The generate-report skill asks for a format when none is given — the
    benchmark prompt must name one so synthesis points measure synthesis,
    not willingness to skip the skill's clarification step."""
    wf = get_workflow("risk-manager-control-day")
    report = wf.steps[8]
    assert any(fmt in report.user.lower() for fmt in ("markdown", "docx", "html"))
    assert any(a.type == "artifact_contains" for a in report.assertions)
