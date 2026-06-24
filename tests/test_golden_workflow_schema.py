import pytest
from app.golden_workflows.schema import (
    GoldenWorkflow, Step, ToolExpectation, normalize_tool_name, normalize_skill, WorkflowError,
)

def test_normalize_tool_name_strips_single_tool_suffix():
    assert normalize_tool_name("run_batch_pricing_tool") == "run_batch_pricing"
    assert normalize_tool_name("run_batch_pricing") == "run_batch_pricing"
    assert normalize_tool_name("get_tool_tool") == "get_tool"  # only one suffix

def test_normalize_skill_lowercases_and_trims():
    assert normalize_skill("  Run-Risk ") == "run-risk"

def test_tool_expectation_null_args_is_wildcard():
    te = ToolExpectation(name="run_batch_pricing", args=None)
    assert te.args is None

def test_parse_workflow_min_one_step_raises_workflow_error():
    from app.golden_workflows.schema import parse_workflow
    with pytest.raises(WorkflowError):
        parse_workflow(dict(id="x", schema_version=1, persona="risk_manager",
                            title="t", objective="o", fixtures="x.fixtures.json",
                            steps=[], success={"assertions": [], "rubric": []}))

def test_parse_workflow_empty_user_raises_workflow_error():
    from app.golden_workflows.schema import parse_workflow
    with pytest.raises(WorkflowError):
        parse_workflow(dict(id="x", schema_version=1, persona="risk_manager",
                            title="t", objective="o", fixtures="x.fixtures.json",
                            steps=[dict(user="", expected_skill="run-risk", outcome="x", replay="r1")],
                            success={"assertions": [], "rubric": []}))
