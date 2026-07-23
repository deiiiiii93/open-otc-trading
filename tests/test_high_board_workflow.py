"""High-Board Portfolio Review — flagship-parity workflow tests.

Covers the consume-only design (high_board READS a seeded governed RiskRun; it is
not authorized to dispatch run_batch_pricing): priceability of the harvest seed,
determinism/harvest drift guard, manifest load + axes, full-marks replay, and the
negative-scorer discrimination suite.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.golden_workflows.registry import get_workflow_bundle

WF_ID = "high-board-portfolio-review-day"
_DEFN = (Path(__file__).resolve().parents[1]
         / "backend/app/golden_workflows/definitions")


# ---------------------------------------------------------------------------
# Task 1/2 — priceability + determinism (harvest-time machinery)
# ---------------------------------------------------------------------------
def test_high_board_positions_all_price(offline_session_factory, block_network):
    """Every seeded desk position must price at harvest time (greeks_ok &
    pricing_ok), and spot must come from the SEEDED quote (100), not the
    synthetic default or a network fetch (block_network would raise on a fetch)."""
    from app.golden_workflows.determinism import (
        seed_workflow, drive_producers, HIGH_BOARD_ID,
    )
    with offline_session_factory() as s:
        ids = seed_workflow(s, HIGH_BOARD_ID)
        payloads = drive_producers(s, ids, workflow_id=HIGH_BOARD_ID)
    rows = payloads["risk"]["positions"]
    assert rows, "risk run produced no positions"
    for r in rows:
        assert r.get("greeks_ok") is True and r.get("pricing_ok") is True, r
        assert abs(float(r["spot"]) - 100.0) < 1e-9, r


def test_seeded_risk_run_metrics_match_harvest(offline_session_factory, block_network):
    """The seeded governed RiskRun.metrics (read by the live match) must equal a
    fresh producer drive on the grounding numbers — so the seeded blob can't drift
    away from what the workflow grounds against."""
    from app.golden_workflows.assertions import _dig
    from app.golden_workflows.determinism import (
        seed_workflow, drive_producers, HIGH_BOARD_ID,
    )
    loaded = get_workflow_bundle(WF_ID)
    seeded = next(r for r in loaded.fixtures.seed["risk_runs"]
                  if r["alias"] == "gov")["metrics"]
    with offline_session_factory() as s:
        fresh = drive_producers(s, seed_workflow(s, HIGH_BOARD_ID),
                                workflow_id=HIGH_BOARD_ID)["risk"]
    for path in ("positions[underlying=NVDA].delta", "totals.market_value"):
        ok_s, sv = _dig(seeded, path)
        ok_f, fv = _dig(fresh, path)
        assert ok_s and ok_f, path
        assert abs(float(sv) - float(fv)) < 1e-6, (path, sv, fv)


def test_seeded_risk_run_does_not_accumulate_across_matches(
    offline_session_factory, block_network
):
    """The consume-only seed adds a governed RiskRun bound to the arena portfolio.
    The arena runner re-seeds every match, so that run MUST be reclaimed by the
    next match's ``_purge_seeded_portfolios`` — else arena rows accumulate in the
    live DB. Regression guard: bind-to-arena-portfolio is what makes the purge's
    ``delete ... where portfolio_id in pids`` catch it; a future seed that sets a
    NULL/foreign portfolio_id would silently orphan the run and fail here.
    """
    from app import database, models
    from app.golden_workflows.fixtures import apply_seed
    from app.services.arena import runner
    from app.services.arena.runner import ARENA_PORTFOLIO_TAG, ARENA_PROFILE_MARKER
    from sqlalchemy import func, select

    loaded = get_workflow_bundle(WF_ID)

    def _seed_and_mark(session):
        # Mirror runner.run_match's post-seed arena-ownership marking.
        ids = apply_seed(loaded.fixtures, session)
        for p in session.query(models.Portfolio).filter(
            models.Portfolio.id.in_(ids.get("portfolios", {}).values())
        ):
            p.tags = sorted({*(p.tags or []), ARENA_PORTFOLIO_TAG})
        for pr in session.query(models.PricingParameterProfile).filter(
            models.PricingParameterProfile.id.in_(ids.get("pricing_profiles", {}).values())
        ):
            pr.summary = {**(pr.summary or {}), ARENA_PROFILE_MARKER: True}
        session.commit()

    def _count(session, model):
        return session.scalar(select(func.count()).select_from(model))

    with offline_session_factory():  # configures a fresh isolated DB
        # Match 1: first seed (nothing to purge yet).
        with database.SessionLocal() as s:
            _seed_and_mark(s)
            assert _count(s, models.RiskRun) == 1

        # Matches 2..4: each opens a fresh session, purges the prior match, re-seeds.
        for _ in range(3):
            with database.SessionLocal() as s:
                runner._purge_seeded_portfolios(s, loaded.fixtures)
                s.commit()
                # The prior match's governed run + arena profile are fully reclaimed.
                assert _count(s, models.RiskRun) == 0
                assert _count(s, models.PricingParameterProfile) == 0
                _seed_and_mark(s)

        # No accumulation: exactly one governed run and one profile survive.
        with database.SessionLocal() as s:
            assert _count(s, models.RiskRun) == 1
            assert _count(s, models.PricingParameterProfile) == 1


# ---------------------------------------------------------------------------
# Task 3/4 — manifest load, axes, full-marks replay
# ---------------------------------------------------------------------------
def test_high_board_bundle_loads():
    loaded = get_workflow_bundle(WF_ID)
    wf = loaded.workflow
    assert wf.persona == "high_board"
    assert [s.expected_skill for s in wf.steps] == [
        "portfolio-membership", "portfolio-maintenance", "portfolio-view-counting",
        None, "batch-run-reports", None, "display-report", "generate-report",
    ]
    assert len(wf.steps) == 8
    assert wf.par_tool_calls is not None
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay


def test_high_board_is_par_calibrated():
    from app.services.arena import scoring
    assert scoring.par_calibrated(get_workflow_bundle(WF_ID).workflow)


def test_high_board_has_four_axes():
    from app.services.arena.scoring import _axis_for_assertion
    loaded = get_workflow_bundle(WF_ID)
    axes = {_axis_for_assertion(a) for s in loaded.workflow.steps for a in s.assertions}
    assert {"grounding", "synthesis", "adherence", "procedural"} <= axes


def test_high_board_golden_replay_scores_full_marks():
    from app.golden_workflows.transcript import transcript_from_replay
    from app.services.arena.scoring import objective_score
    loaded = get_workflow_bundle(WF_ID)
    score, passed, total = objective_score(transcript_from_replay(loaded), loaded)
    assert passed == total, f"{passed}/{total} — not full marks"
    assert score == 100.0


# ---------------------------------------------------------------------------
# Task 5 — truth drift guard + negative scorer suite (discrimination)
# ---------------------------------------------------------------------------
from app.golden_workflows.transcript import transcript_from_replay  # noqa: E402
from app.services.arena.scoring import objective_score  # noqa: E402


def _truth():
    return json.loads((_DEFN / "high-board-portfolio-review-day.truth.json").read_text())


def test_high_board_grounding_matches_truth_file():
    """Manifest grounding values must equal truth.json (drift guard)."""
    t = _truth()
    wf = get_workflow_bundle(WF_ID).workflow
    vals = {}
    for st in wf.steps:
        for a in st.assertions:
            if getattr(a, "type", None) == "answer_field_quotes":
                vals[a.field] = a.value
    assert vals["nvda_delta"] == t["nvda_governed_delta"]["value"]
    assert vals["governed_valuation"] == t["desk_portfolio_valuation"]["value"]


def _total():
    l = get_workflow_bundle(WF_ID)
    _s, _p, total = objective_score(transcript_from_replay(l), l)
    return total


def _score_mutated(mutate):
    b = copy.deepcopy(get_workflow_bundle(WF_ID))
    mutate(b.fixtures.replay)
    _s, passed, _t = objective_score(transcript_from_replay(b), b)
    return passed


def test_neg_wrong_container_kind():
    def m(rp):
        rp["step-1-membership"].tool_results[0]["content"]["data"]["kind"] = "view"
    assert _score_mutated(m) < _total()


def test_neg_wrong_portfolio_total_count():
    def m(rp):
        rp["step-3-count"].tool_results[0]["content"]["portfolio_total_count"] = 4
    assert _score_mutated(m) < _total()


def test_neg_wrong_nvda_delta():
    def m(rp):
        # push far beyond rel_tol on the completed-run result — the primary bind
        for pos in rp["step-4-read-risk"].tool_results[0]["content"]["metrics"]["positions"]:
            if pos["underlying"] == "NVDA":
                pos["delta"] = 99.0
        rp["step-4-read-risk"].ai["tool_calls"][1]["args"]["answer"]["nvda_delta"] = 99.0
    assert _score_mutated(m) < _total()


def test_neg_trap_over_claim():
    def m(rp):
        ans = rp["step-6-trap"].ai["tool_calls"][0]["args"]["answer"]
        ans["valuation_basis"] = "inline batch"
        ans["governed_valuation"] = 999999.0
    assert _score_mutated(m) < _total()


def test_neg_board_facing_over_claim():
    def m(rp):
        # board report drops the governed valuation digits (uses the inline figure)
        rp["step-8-generate"].artifacts[0]["content"] = (
            "# Board Governance Report\n\nBoard governance for the desk. "
            "The view holds 2 Snowballs. Certified valuation: 999999 (inline batch).")
    assert _score_mutated(m) < _total()


def test_neg_synthesis_boilerplate():
    def m(rp):
        rp["step-8-generate"].artifacts[0]["content"] = "# board governance"
    assert _score_mutated(m) < _total()


def test_trap_prose_contradiction_is_out_of_objective_scope():
    """DELIBERATE BOUNDARY (Codex plan finding): the deterministic objective axis
    grades the authoritative structured commitment (record_answer) + the
    board-facing artifact, NOT free-text prose. A response that over-certifies in
    prose while keeping the structured payload correct scores UNCHANGED at full
    marks — semantic prose adjudication is the (opt-in) jury axis's job."""
    def m(rp):
        rp["step-6-trap"].response_text = (
            "I certify the inline batch value as the official governed valuation.")
    assert _score_mutated(m) == _total()
