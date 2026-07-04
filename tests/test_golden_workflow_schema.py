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


# ---------------------------------------------------------------------------
# Flagship v2 schema additions
# ---------------------------------------------------------------------------

import pytest
from app.golden_workflows.schema import Step


class TestNewAssertionSchemas:
    def test_artifact_contains_requires_nonempty_any_of(self):
        from app.golden_workflows.schema import _ArtifactContains
        with pytest.raises(Exception):
            _ArtifactContains(type="artifact_contains", kind="text", any_of=[])
        a = _ArtifactContains(type="artifact_contains", kind="text", any_of=["cvar"])
        assert a.kind == "text"

    def test_quotes_defaults(self):
        from app.golden_workflows.schema import _ResponseQuotesToolValue
        a = _ResponseQuotesToolValue(
            type="response_quotes_tool_value", tool="t", path="p")
        assert (a.rel_tol, a.scope, a.match, a.near) == (0.02, "step", "signed", None)

    def test_quotes_rel_tol_bounds(self):
        from app.golden_workflows.schema import _ResponseQuotesToolValue
        for bad in (0, 1, -0.1, 1.5):
            with pytest.raises(Exception):
                _ResponseQuotesToolValue(
                    type="response_quotes_tool_value", tool="t", path="p",
                    rel_tol=bad)

    def test_quotes_near_nonempty_when_present(self):
        from app.golden_workflows.schema import _ResponseQuotesToolValue
        with pytest.raises(Exception):
            _ResponseQuotesToolValue(
                type="response_quotes_tool_value", tool="t", path="p", near=[])

    def test_tool_called_args_mutual_exclusion(self):
        from app.golden_workflows.schema import _ToolCalled
        with pytest.raises(Exception):
            _ToolCalled(type="tool_called", name="t", args={"a": 1},
                        args_any_of=[{"a": 1}])
        with pytest.raises(Exception):
            _ToolCalled(type="tool_called", name="t", args_any_of=[])

    def test_step_expected_skill_nullable(self):
        s = Step(user="u", expected_skill=None, outcome="o", replay="r")
        assert s.expected_skill is None
