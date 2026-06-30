import copy

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score  # NOTE: lives in arena.scoring


def _loaded():
    return get_workflow_bundle("high-board-portfolio-review-day")


def test_baseline_replay_passes():
    loaded = _loaded()
    tx = transcript_from_replay(loaded)
    # objective_score signature is (transcript, loaded) -> (score, passed, total)
    _score, passed, total = objective_score(tx, loaded)
    assert passed == total  # all objective assertions pass on the honest replay


def test_leaking_view_fails_scope_discriminator():
    loaded = _loaded()
    # Simulate a leaking hybrid view: the view's full membership resolves to 6,
    # not the seeded desk count of 5.
    loaded = copy.deepcopy(loaded)
    loaded.fixtures.replay["step-3-count"].tool_results[0]["content"]["portfolio_total_count"] = 6
    tx = transcript_from_replay(loaded)
    _, passed, total = objective_score(tx, loaded)
    assert passed < total  # portfolio_total_count == 5 assertion now fails


def test_calling_create_report_fails_tool_not_called():
    loaded = _loaded()
    loaded = copy.deepcopy(loaded)
    # Step 6 also calls create_report before write_report_artifact.
    entry = loaded.fixtures.replay["step-6-generate"]
    entry.ai["tool_calls"].insert(
        0, {"id": "tc6x", "name": "create_report", "args": {"report_type": "portfolio_governance"}}
    )
    entry.tool_results.insert(
        0, {"name": "create_report", "tool_call_id": "tc6x", "content": {"id": 77}}
    )
    tx = transcript_from_replay(loaded)
    _, passed, total = objective_score(tx, loaded)
    assert passed < total  # tool_not_called create_report now fails


def test_planned_list_reports_args_validate_against_real_tool(session):
    """Replay scoring does not prove the live tool accepts the args. Invoke the
    REAL list_reports tool with the workflow's planned Step-5 args to prove they
    pass the tool's input schema (report_type marker is NOT used as a filter).
    The `session` fixture configures the global DB so the tool query has tables."""
    from app.tools.reporting import list_reports_tool
    # Must not raise a pydantic/LangChain validation error:
    out = list_reports_tool.invoke({"status": "completed"})
    assert "reports" in out


def test_wrong_report_selection_fails_step5_discriminator():
    """The marker report_type is the SELECTION enforcer: list_reports filters only
    by status (the marker is not a valid tool filter), so if the agent fetches the
    wrong completed report, get_report's report_type != marker and Step 5 fails."""
    loaded = _loaded()
    loaded = copy.deepcopy(loaded)
    gr = loaded.fixtures.replay["step-5-display"].tool_results[1]["content"]
    gr["report_type"] = "portfolio"  # not the arena marker
    tx = transcript_from_replay(loaded)
    _, passed, total = objective_score(tx, loaded)
    assert passed < total  # tool_result_path get_report report_type == marker now fails
