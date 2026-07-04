from app.golden_workflows.assertions import (
    AssertionContext, evaluate_assertion, match_tool, resolve_seed_refs,
)
from app.golden_workflows.schema import ToolExpectation, _ResponseContains, _ToolResultPath, _TaskReturnedId

def ctx(**kw):
    base = dict(response_text="", tool_calls=[], tool_results=[],
                skills_routed=[], artifacts=[], task_ids=[])
    base.update(kw); return AssertionContext(**base)

def test_response_contains_case_insensitive():
    a = _ResponseContains(type="response_contains", any_of=["stale"])
    ok, _ = evaluate_assertion(a, ctx(response_text="This run is STALE."))
    assert ok

def test_arg_subset_no_coercion():
    exp = ToolExpectation(name="run_batch_pricing", args={"portfolio_id": 6})
    ok, _ = match_tool(exp, [{"name": "run_batch_pricing", "args": {"portfolio_id": 6, "method": "summary"}}])
    assert ok
    bad, _ = match_tool(exp, [{"name": "run_batch_pricing", "args": {"portfolio_id": "6"}}])
    assert not bad  # str "6" != int 6

def test_match_normalizes_tool_suffix_on_both_sides():
    exp = ToolExpectation(name="run_batch_pricing", args=None)
    ok, _ = match_tool(exp, [{"name": "run_batch_pricing_tool", "args": {}}])
    assert ok  # observed '_tool' suffix still matches the expectation

def test_tool_result_path_lte():
    a = _ToolResultPath(type="tool_result_path", tool="get_scenario_test_run", path="pnl", lte=0)
    ok, _ = evaluate_assertion(a, ctx(tool_results=[{"name": "get_scenario_test_run", "content": {"pnl": -1200.0}}]))
    assert ok

def test_task_returned_id_reads_content_task_id():
    a = _TaskReturnedId(type="task_returned_id", tool="run_batch_pricing")
    ok, _ = evaluate_assertion(a, ctx(tool_results=[{"name": "run_batch_pricing", "content": {"task_id": "task_9"}}]))
    assert ok

def test_resolve_seed_refs_preserves_type():
    out = resolve_seed_refs({"portfolio_id": "$seed.portfolios.control.id"},
                            {"$seed.portfolios.control.id": 6})
    assert out == {"portfolio_id": 6} and isinstance(out["portfolio_id"], int)


def test_tool_not_called_passes_when_absent():
    from app.golden_workflows.schema import _ToolNotCalled
    a = _ToolNotCalled(type="tool_not_called", name="create_report")
    ok, _ = evaluate_assertion(a, ctx(tool_calls=[{"name": "write_report_artifact"}]))
    assert ok is True


def test_tool_not_called_fails_when_present_normalized():
    from app.golden_workflows.schema import _ToolNotCalled
    a = _ToolNotCalled(type="tool_not_called", name="create_report")
    # normalize_tool_name strips a trailing _tool suffix on the observed call
    ok, msg = evaluate_assertion(a, ctx(tool_calls=[{"name": "create_report_tool"}]))
    assert ok is False
    assert "create_report" in msg


def test_tools_routed_sequence_passes_on_ordered_subsequence():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["get_latest_risk_run", "run_batch_pricing", "run_greeks_landscape"],
    )
    calls = [
        {"name": "list_portfolios"}, {"name": "get_latest_risk_run"},
        {"name": "run_batch_pricing"}, {"name": "get_positions"},
        {"name": "run_greeks_landscape"},
    ]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is True


def test_tools_routed_sequence_captures_repeated_tool():
    # The dedup-noise fix: a tool called twice (read risk, then re-read fresh
    # risk) must be observable in order — unlike read_file-based skills_routed,
    # which can't see a re-read of an already-loaded skill.
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["get_latest_risk_run", "run_batch_pricing", "get_latest_risk_run"],
    )
    calls = [
        {"name": "get_latest_risk_run"}, {"name": "run_batch_pricing"},
        {"name": "get_latest_risk_run"},
    ]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is True


def test_tools_routed_sequence_fails_when_tool_missing():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["run_batch_pricing", "run_greeks_landscape", "run_backtest"],
    )
    # run_backtest never called -> genuine procedural failure
    calls = [{"name": "run_batch_pricing"}, {"name": "run_greeks_landscape"}]
    ok, msg = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is False
    assert "run_backtest" in msg


def test_tools_routed_sequence_fails_on_wrong_order():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["run_batch_pricing", "run_greeks_landscape"],
    )
    # greeks before batch pricing -> out of designed order
    calls = [{"name": "run_greeks_landscape"}, {"name": "run_batch_pricing"}]
    ok, _ = evaluate_assertion(a, ctx(tool_calls=calls))
    assert ok is False


def test_tools_routed_sequence_normalizes_tool_suffix():
    from app.golden_workflows.schema import _ToolsRoutedSequence
    a = _ToolsRoutedSequence(
        type="tools_routed_sequence",
        names=["run_batch_pricing"],
    )
    ok, _ = evaluate_assertion(a, ctx(tool_calls=[{"name": "run_batch_pricing_tool"}]))
    assert ok is True


def test_tool_called_scores_backtest_date_window_explicitly():
    """A `tool_called` assertion carrying the requested start/end dates passes
    only when run_backtest is invoked with those exact dates — so a model that
    substitutes its own window (the Sonnet 5 / Opus 4.8 failure) fails this
    check explicitly rather than only via downstream numbers."""
    from app.golden_workflows.schema import _ToolCalled

    a = _ToolCalled(
        type="tool_called",
        name="run_backtest",
        args={"start_date": "2026-03-24", "end_date": "2026-06-24"},
    )
    correct = ctx(tool_calls=[{
        "name": "run_backtest",
        "args": {"portfolio_id": 2, "start_date": "2026-03-24",
                 "end_date": "2026-06-24", "engine": "quad"},
    }])
    substituted = ctx(tool_calls=[{
        "name": "run_backtest",
        "args": {"portfolio_id": 2, "start_date": "2026-04-01",
                 "end_date": "2026-07-03"},
    }])
    assert evaluate_assertion(a, correct)[0] is True
    assert evaluate_assertion(a, substituted)[0] is False


def test_flagship_backtest_step_asserts_requested_dates():
    """The risk-manager flagship backtest step must carry the date-args check."""
    from app.golden_workflows.registry import get_workflow

    wf = get_workflow("risk-manager-control-day")
    step = wf.steps[5]  # step 6 (0-indexed): the backtest step
    assert "2026-03-24" in step.user and "2026-06-24" in step.user
    date_checks = [
        a for a in step.assertions
        if a.type == "tool_called" and a.name == "run_backtest"
        and (a.args or {}).get("start_date") == "2026-03-24"
        and (a.args or {}).get("end_date") == "2026-06-24"
    ]
    assert len(date_checks) == 1


def test_high_board_is_a_valid_persona():
    from app.golden_workflows.schema import parse_workflow
    wf = parse_workflow({
        "id": "x", "schema_version": 1, "persona": "high_board",
        "title": "t", "objective": "o", "fixtures": "x.fixtures.json",
        "steps": [{"user": "u", "expected_skill": "s", "outcome": "o", "replay": "r"}],
        "success": {"assertions": [], "rubric": []},
    })
    assert wf.persona == "high_board"
