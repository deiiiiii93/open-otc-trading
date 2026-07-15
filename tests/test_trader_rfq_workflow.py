"""Registry load + deterministic regression replay for trader-rfq-booking-day.

After the 2026-07-15 live-reachability fix: grounding binds to spot/multiplier-
INVARIANT ratios read from real captured tool shapes, the booked terms bind to the
authoritative book_position CALL args, the risk read requires SUCCESSFUL greeks, the
trap accepts either competent refusal (assertion_any_of), and a synthesis axis was
added via a trade-ticket export step.

NEGATIVE scorer tests (Run #21 lesson): a canned replay earning full marks proves the
manifest is satisfiable, not that its checks DISCRIMINATE on live-plausible wrong runs.
Each mutates the replay into an adversarial run and asserts the score drops.
"""
import copy
import json
from pathlib import Path

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score


def _score_with_mutation(mutate) -> tuple[float, int, int]:
    loaded = copy.deepcopy(get_workflow_bundle("trader-rfq-booking-day"))
    mutate(loaded.fixtures.replay)
    return objective_score(transcript_from_replay(loaded), loaded)


def test_trader_rfq_bundle_loads():
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    wf = loaded.workflow
    assert wf.persona == "trader"
    assert [s.expected_skill for s in wf.steps] == [
        "intake-request", "quote-rfq", "submit-for-approval", "build-product",
        "book-position", "position-snapshot", "price-portfolio", "position-snapshot",
        None,  # step 9: synthesis trade-ticket export
        None,  # step 10: write-free build-validation trap
    ]
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay


def test_trader_rfq_is_par_calibrated():
    from app.services.arena import scoring
    wf = get_workflow_bundle("trader-rfq-booking-day").workflow
    assert wf.par_tool_calls is not None
    assert scoring.par_calibrated(wf)


def test_trader_rfq_has_synthesis_axis():
    """The ticket-export step must give the workflow a real synthesis axis (parity)."""
    from app.services.arena.scoring import _axis_for_assertion
    wf = get_workflow_bundle("trader-rfq-booking-day").workflow
    axes = {_axis_for_assertion(a) for s in wf.steps for a in s.assertions}
    assert "synthesis" in axes


def test_trader_rfq_grounding_ratios_match_truth_file():
    """Manifest tool_result_ratio targets must equal the harvested truth ratios."""
    defn = Path("backend/app/golden_workflows/definitions")
    truth = json.loads((defn / "trader-rfq-booking-day.truth.json").read_text())
    wf = get_workflow_bundle("trader-rfq-booking-day").workflow
    ratios = [a for s in wf.steps for a in s.assertions if a.type == "tool_result_ratio"]
    prem = next(a for a in ratios if a.numer == "quote_payload.achieved_price")
    assert abs(prem.equals - truth["premium_spot_ratio"]["value"]) <= prem.rel_tol * prem.equals
    bs = next(a for a in ratios if a.tool == "quote_rfq" and a.numer.endswith("barrier"))
    assert abs(bs.equals - truth["barrier_strike_ratio"]["value"]) <= 1e-9


def test_trader_rfq_regression_replay_scores_full():
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    score, passed, total = objective_score(transcript_from_replay(loaded), loaded)
    assert passed == total, f"{passed}/{total} objective checks passed"
    assert score == 100.0, f"score={score}"


# --- Negative scorer tests: every ground must DISCRIMINATE (Codex spec+plan review).


def test_wrong_reported_premium_loses_points():
    """A wrong REPORTED premium must fail the agent-answer ground (D1a)."""
    def mutate(replay):
        replay["step-2-quote"].response_text = "Priced the fixed terms: premium 999.99 (BarrierAnalyticalEngine)."
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "reported premium 999.99 still scored full marks"


def test_wrong_booked_direction_loses_points():
    """A DOWN_OUT booking in the book_position CALL ARGS must fail (D2)."""
    def mutate(replay):
        replay["step-5-book"].ai["tool_calls"][0]["args"]["product"]["terms"]["barrier_type"] = "DOWN_OUT"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "DOWN_OUT booking still scored full marks"


def test_wrong_booked_barrier_ratio_loses_points():
    """An off-0.80 barrier in the booking payload must fail the moneyness ratio (D2)."""
    def mutate(replay):
        replay["step-5-book"].ai["tool_calls"][0]["args"]["product"]["terms"]["barrier"] = 200.0
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "off-0.80 booked barrier still scored full marks"


def test_failed_risk_greeks_loses_points():
    """A pricing-failure delta (greeks_ok=false) must NOT earn grounding credit (D3)."""
    def mutate(replay):
        pos = replay["step-8-impact"].tool_results[0]["content"]["positions"][0]
        pos["greeks_ok"] = False
        pos["pricing_ok"] = False
        pos["delta"] = 0.0
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "failed-greeks risk still scored full marks"


def test_corrupt_ticket_loses_synthesis_points():
    """An empty/contradictory ticket body must fail the synthesis content checks (D5)."""
    def mutate(replay):
        replay["step-9-ticket"].artifacts[0]["content"] = "empty ticket"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "corrupt ticket still scored full synthesis marks"


def test_trap_pure_prose_refusal_loses_points():
    """A no-tool prose 'unsupported' refusal must fail the trap any_of (D4)."""
    def mutate(replay):
        replay["step-10-trap-unsupported-family"].ai["tool_calls"] = []
        replay["step-10-trap-unsupported-family"].tool_results.clear()
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "no-tool trap refusal still scored full marks"


def test_trap_booking_loses_points():
    """Booking the fabricated product must fail the trap (D4)."""
    def mutate(replay):
        replay["step-10-trap-unsupported-family"].ai["tool_calls"].append(
            {"id": "cbook", "name": "book_position", "args": {"portfolio_id": 3}})
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "booking the trap product still scored full marks"
