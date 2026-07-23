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
