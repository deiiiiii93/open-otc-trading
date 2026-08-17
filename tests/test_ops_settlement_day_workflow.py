"""ops-settlement-day: structural pins, replay scoring, negative mutations.

The golden replay proves SATISFIABILITY only — live reachability is proven by
the mandatory pre-merge live smoke, never by this file.
"""
from __future__ import annotations

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena import scoring

WORKFLOW_ID = "ops-settlement-day"


@pytest.fixture(scope="module")
def loaded():
    return get_workflow_bundle(WORKFLOW_ID)


def test_bundle_loads(loaded):
    wf = loaded.workflow
    assert wf.persona == "trader"
    assert len(wf.steps) == 8
    assert [s.expected_skill for s in wf.steps] == [
        "record-lifecycle-event", None, "manage-settlement-cashflows",
        None, None, None, None, None,
    ]
    assert wf.par_tool_calls is None
    for step in wf.steps:
        assert step.replay in loaded.fixtures.replay


def test_point_manifest_is_44(loaded):
    wf = loaded.workflow
    skills = sum(1 for s in wf.steps if s.expected_skill is not None)
    tools = sum(len(s.expected_tools) for s in wf.steps)
    step_assertions = sum(len(s.assertions) for s in wf.steps)
    success = len(wf.success.assertions)
    assert (skills, tools, step_assertions, success) == (2, 10, 31, 1)
    assert skills + tools + step_assertions + success == 44


def test_not_par_calibrated(loaded):
    assert scoring.par_calibrated(loaded.workflow) is False


def test_success_bans_void_session_wide(loaded):
    (assertion,) = loaded.workflow.success.assertions
    assert assertion.type == "tool_not_called"
    assert assertion.name == "void_settlement_cashflow"
    for step in loaded.workflow.steps:
        for a in step.assertions:
            assert not (
                a.type == "tool_not_called"
                and a.name == "void_settlement_cashflow"
            )


def test_no_step_scores_its_own_skill_twice(loaded):
    for step in loaded.workflow.steps:
        for a in step.assertions:
            assert a.type != "skill_routed"


def test_seeded_event_types_are_reachable_for_their_families(loaded):
    """Seeded events bypass create_lifecycle_event's allowlist (direct ORM
    insert), so satisfiability ≠ reachability — pin that every seeded and
    every agent-recorded event type is legal for its position's family."""
    from app.services.domains.lifecycle_vocabulary import (
        PRODUCT_LIFECYCLE_EVENTS,
    )
    seed = loaded.fixtures.seed
    family_by_alias = {
        p["alias"]: p["product_type"] for p in seed["positions"]
    }
    for ev in seed["position_lifecycle_events"]:
        family = family_by_alias[ev["position"]]
        assert ev["event_type"] in PRODUCT_LIFECYCLE_EVENTS[family]
    # The three agent-recorded types graded by the manifest:
    assert "knock_out" in PRODUCT_LIFECYCLE_EVENTS["SnowballOption"]
    assert "reopen" in PRODUCT_LIFECYCLE_EVENTS["SnowballOption"]
    assert "expire" in PRODUCT_LIFECYCLE_EVENTS["EuropeanVanillaOption"]


def test_blotter_sum_matches_seeded_amounts(loaded):
    """650500 = KO payoff + override amount + released amount; the
    needs_amount row contributes nothing. Recomputed from the fixture so the
    manifest constant cannot silently drift from the seed."""
    seed = loaded.fixtures.seed
    seeded = sum(
        row["amount"] for row in seed["settlement_cashflows"]
        if row.get("amount") is not None
    )
    ko_payoff = 512500.0  # step-1 prompt literal
    graded = {
        a.value
        for step in loaded.workflow.steps
        for a in step.assertions
        if a.type == "answer_field_quotes" and a.field == "total_cny_amount"
    }
    assert graded == {seeded + ko_payoff}


def test_has_four_axes(loaded):
    transcript = transcript_from_replay(loaded)
    breakdown = scoring.objective_breakdown(transcript, loaded)
    axes = breakdown["axes"]
    assert {"grounding", "adherence", "synthesis", "procedural"} <= set(axes)
    for name in ("grounding", "adherence", "synthesis", "procedural"):
        assert axes[name]["total"] > 0, f"axis {name} has no checks"


def test_golden_replay_scores_full_marks(loaded):
    transcript = transcript_from_replay(loaded)
    score, passed, total = scoring.objective_score(transcript, loaded)
    assert passed == total
    assert total == 44
    assert score == 100.0


# --- negative mutations: each must drop the score below full marks ----------


def _score_mutated(loaded, mutate) -> tuple[int, int]:
    transcript = transcript_from_replay(loaded)
    mutate(transcript)
    _score, passed, total = scoring.objective_score(transcript, loaded)
    return passed, total


def test_neg_settle_zero_expiry_fails(loaded):
    """The step-2 trap: settling the worthless expiry instead of expiring it."""
    def mutate(t):
        step = t.steps[1]
        for c in step.tool_calls:
            if c["name"] == "record_lifecycle_event":
                c["name"] = "settle_position"
                c["args"] = {"position_id": 9312, "settlement_amount": 0.0}
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"event_type_recorded": "settle"}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_resync_adopts_derivation_fails(loaded):
    """A model that reports the re-derived 90000 as effective misunderstands
    override preservation — the step-6 discriminator."""
    def mutate(t):
        step = t.steps[5]
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"effective_amount": 90000.0,
                                        "new_derived_baseline": 90000.0}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_copied_effective_as_baseline_fails(loaded):
    """Copying 91,000 into both slots still passes new_derived_baseline at
    default rel_tol 0.02 (|91000−90000|/90000 ≈ 1.11%). Tight 0.005 is
    the twin-field discriminator."""
    def mutate(t):
        step = t.steps[5]
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"effective_amount": 91000.0,
                                        "new_derived_baseline": 91000.0}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_double_settle_fails(loaded):
    def mutate(t):
        step = t.steps[6]
        step.tool_calls.append(
            {"id": "cx_settle2", "name": "settle_settlement_cashflow",
             "args": {"cashflow_id": 9303, "expected_row_version": 2}})
        step.tool_results.append(
            {"name": "settle_settlement_cashflow", "tool_call_id": "cx_settle2",
             "content": {"ok": False, "error": "invalid"}})

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_void_in_any_step_fails_success(loaded):
    def mutate(t):
        step = t.steps[7]
        step.tool_calls.append(
            {"id": "cx_void", "name": "void_settlement_cashflow",
             "args": {"cashflow_id": 9304, "expected_row_version": 1,
                      "reason": "clearing the way for the reopen"}})
        step.tool_results.append(
            {"name": "void_settlement_cashflow", "tool_call_id": "cx_void",
             "content": {"ok": True, "status": "void"}})

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_claimed_reopen_success_fails(loaded):
    def mutate(t):
        step = t.steps[7]
        for c in step.tool_calls:
            if c["name"] == "record_answer":
                c["args"] = {"answer": {"reopen_recorded": "yes",
                                        "blocker": None}}

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_neg_unscoped_summary_totals_fail(loaded):
    """Contaminated (unscoped) totals must fail the grounding read-back."""
    def mutate(t):
        step = t.steps[2]
        for r in step.tool_results:
            if r["name"] == "get_settlement_summary":
                r["content"]["totals_by_currency"]["CNY"] = 812733.0

    passed, total = _score_mutated(loaded, mutate)
    assert passed < total


def test_grounding_matches_truth_file(loaded):
    import json
    truth_path = loaded.definition_path.parent / "ops-settlement-day.truth.json"
    truth_values = {
        entry["value"] for entry in json.loads(truth_path.read_text()).values()
    }
    manifest_values = {
        a.value
        for step in loaded.workflow.steps
        for a in step.assertions
        if a.type == "answer_field_quotes"
    }
    assert manifest_values <= truth_values, (
        f"manifest grounding values {manifest_values - truth_values} not in truth file"
    )


def test_purge_sweeps_settlement_children(loaded, session):
    """settlement_cashflow_events and settlement_notices key only on
    cashflow_id — the recursive FK child sweep must delete them before their
    cashflows, or one leaked FK kills every remaining match (the Run-#34
    class). Seed the bundle, attach a transition-log row and a notice row,
    purge, and assert the whole family is gone."""
    from sqlalchemy import select
    from app import models
    from app.golden_workflows.fixtures import apply_seed
    from app.services.arena.runner import _delete_portfolios_with_dependents

    ids = apply_seed(loaded.fixtures, session)
    cf_id = ids["settlement_cashflows"]["released_row"]
    session.add(models.SettlementCashflowEvent(
        cashflow_id=cf_id, action="released", from_status="pending",
        to_status="released", actor="test"))
    session.add(models.SettlementNotice(
        cashflow_id=cf_id, version=1, artifact_path="x.md",
        content_sha256="0" * 64))
    session.commit()

    _delete_portfolios_with_dependents(session, [ids["portfolios"]["ops"]])
    session.commit()

    for model in (models.SettlementCashflow, models.SettlementCashflowEvent,
                  models.SettlementNotice, models.PositionLifecycleEvent,
                  models.Position):
        assert session.execute(select(model)).first() is None
    assert session.get(models.Portfolio, ids["portfolios"]["ops"]) is None

