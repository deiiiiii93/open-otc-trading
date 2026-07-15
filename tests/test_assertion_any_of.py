"""Unit tests for the `assertion_any_of` composite + per-assertion axis override.

The composite expresses an "either competent path" ground (e.g. a trap the model may
refuse two legitimate ways) that independent AND-ed assertions cannot — it scores as
ONE check and passes iff any member passes.
"""
from app.golden_workflows.assertions import AssertionContext, evaluate_assertion
from app.golden_workflows.schema import _AssertionAnyOf, _ArtifactContains
from app.services.arena.scoring import _axis_for_assertion


def _ctx(tool_results=None):
    return AssertionContext(
        response_text="", tool_calls=[], tool_results=tool_results or [],
        skills_routed=[], artifacts=[], task_ids=[],
    )


def _anyof(**over):
    kw = dict(type="assertion_any_of", axis="adherence", any_of=[
        {"type": "tool_result_path", "tool": "build_product", "path": "validation.ok", "equals": False},
        {"type": "tool_result_path", "tool": "check_term_completeness", "path": "complete", "equals": False},
    ])
    kw.update(over)
    return _AssertionAnyOf(**kw)


def test_any_of_passes_when_first_member_passes():
    ctx = _ctx([{"name": "build_product", "content": {"validation": {"ok": False}}}])
    ok, msg = evaluate_assertion(_anyof(), ctx)
    assert ok, msg


def test_any_of_passes_when_second_member_passes():
    ctx = _ctx([{"name": "check_term_completeness", "content": {"complete": False}}])
    ok, msg = evaluate_assertion(_anyof(), ctx)
    assert ok, msg


def test_any_of_fails_when_no_member_passes():
    # Pure-prose refusal — neither validation tool fired. Must FAIL (Codex plan finding 4).
    ok, msg = evaluate_assertion(_anyof(), _ctx([]))
    assert not ok and "no member passed" in msg


def test_any_of_axis_is_its_declared_axis():
    assert _axis_for_assertion(_anyof(axis="adherence")) == "adherence"
    assert _axis_for_assertion(_anyof(axis="synthesis")) == "synthesis"


def test_artifact_contains_axis_override():
    # Default is synthesis; override still resolves through the per-assertion axis path.
    default = _ArtifactContains(type="artifact_contains", kind="ticket", any_of=["DOWN_IN"])
    assert _axis_for_assertion(default) == "synthesis"
    overridden = _ArtifactContains(type="artifact_contains", kind="ticket",
                                   any_of=["DOWN_IN"], axis="adherence")
    assert _axis_for_assertion(overridden) == "adherence"
