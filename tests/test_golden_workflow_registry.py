import pytest
from app.golden_workflows.registry import load_workflow, skill_names
from app.golden_workflows.schema import NarrationMismatchError, MissingReplayError

NARR_OK = """---
id: t-wf
schema_version: 1
persona: risk_manager
title: T
objective: O
fixtures: t-wf.fixtures.json
steps:
  - user: hi
    expected_skill: read-risk-result
    outcome: ok
    replay: r1
success: {assertions: [], rubric: []}
---
## Step 1 — Orient
The risk manager opens the book.
"""

def _make(tmp_path, md, fixtures='{"schema_version":1,"seed":{},"replay":{"r1":{"ai":{"content":"","tool_calls":[]},"tool_results":[],"skills_routed":[],"artifacts":[],"response_text":""}}}'):
    (tmp_path / "t-wf.md").write_text(md)
    (tmp_path / "t-wf.fixtures.json").write_text(fixtures)
    return tmp_path / "t-wf.md"

def test_load_attaches_narration(tmp_path):
    wf = load_workflow(_make(tmp_path, NARR_OK))
    assert len(wf.narration) == 1 and "opens the book" in wf.narration[0]

def test_narration_count_mismatch(tmp_path):
    md = NARR_OK.replace("## Step 1 — Orient\nThe risk manager opens the book.\n", "")
    with pytest.raises(NarrationMismatchError):
        load_workflow(_make(tmp_path, md))

def test_missing_replay_ref(tmp_path):
    md = NARR_OK.replace("replay: r1", "replay: nope")
    with pytest.raises(MissingReplayError):
        load_workflow(_make(tmp_path, md))

def test_skill_names_are_recursive_and_include_run_risk():
    assert "run-risk" in skill_names()

def test_seed_refs_resolved_at_load_in_args_and_assertions(tmp_path):
    from app.golden_workflows.registry import load_workflow_bundle
    md = NARR_OK.replace(
        "    outcome: ok\n    replay: r1",
        "    outcome: ok\n"
        "    expected_tools:\n"
        "      - name: get_latest_risk_run\n"
        "        args: {portfolio_id: $seed.portfolios.control.id}\n"
        "    assertions:\n"
        "      - type: tool_result_path\n"
        "        tool: get_latest_risk_run\n"
        "        path: portfolio_id\n"
        "        equals: $seed.portfolios.control.id\n"
        "    replay: r1",
    )
    fixtures = ('{"schema_version":1,'
        '"seed":{"portfolios":[{"alias":"control","id":6,"name":"B"}]},'
        '"replay":{"r1":{"ai":{"content":"","tool_calls":[]},"tool_results":[],'
        '"skills_routed":[],"artifacts":[],"response_text":""}}}')
    b = load_workflow_bundle(_make(tmp_path, md, fixtures))
    step = b.workflow.steps[0]
    assert step.expected_tools[0].args["portfolio_id"] == 6        # arg resolved
    assert step.assertions[0].equals == 6                          # comparator resolved
    assert isinstance(step.assertions[0].equals, int)             # type preserved


def test_null_expected_skill_step_loads(tmp_path):
    """A step with expected_skill: null must not crash skill-name validation."""
    import shutil
    from pathlib import Path
    from app.golden_workflows import registry

    src = Path("backend/app/golden_workflows/definitions")
    for f in ("risk-manager-control-day.md", "risk-manager-control-day.fixtures.json"):
        shutil.copy(src / f, tmp_path / f)
    md_path = tmp_path / "risk-manager-control-day.md"
    md = md_path.read_text()
    md = md.replace("expected_skill: read-risk-result", "expected_skill: null", 1)
    md_path.write_text(md)
    loaded = registry.load_workflow_bundle(md_path)
    assert loaded.workflow.steps[0].expected_skill is None
