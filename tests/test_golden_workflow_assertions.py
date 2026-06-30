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


def test_high_board_is_a_valid_persona():
    from app.golden_workflows.schema import parse_workflow
    wf = parse_workflow({
        "id": "x", "schema_version": 1, "persona": "high_board",
        "title": "t", "objective": "o", "fixtures": "x.fixtures.json",
        "steps": [{"user": "u", "expected_skill": "s", "outcome": "o", "replay": "r"}],
        "success": {"assertions": [], "rubric": []},
    })
    assert wf.persona == "high_board"
