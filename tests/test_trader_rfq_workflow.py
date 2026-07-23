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
    # Accounting date pinned to a concluded trading day (Run #26): the runner
    # passes it to stream_and_persist so live snapshot fetches always return rows.
    assert wf.accounting_date == "2026-07-16"
    assert [s.expected_skill for s in wf.steps] == [
        "intake-request", "quote-rfq", "submit-for-approval", "build-product",
        "book-position", "position-snapshot", "price-portfolio", "run-risk",
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


def test_duplicate_booking_loses_points():
    """A second (duplicate) booking must trip the max_calls/all_calls guard (Codex code-review)."""
    def mutate(replay):
        calls = replay["step-5-book"].ai["tool_calls"]
        calls.append(copy.deepcopy(calls[0]))
        calls[-1]["id"] = "c5b"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "duplicate booking still scored full marks"


def test_corrupt_ticket_loses_synthesis_points():
    """An empty/contradictory ticket body must fail the synthesis content checks (D5)."""
    def mutate(replay):
        replay["step-9-ticket"].artifacts[0]["content"] = "empty ticket"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "corrupt ticket still scored full synthesis marks"


def test_ticket_missing_underlying_loses_points():
    """A ticket omitting the underlying must fail synthesis (Codex code-review)."""
    def mutate(replay):
        a = replay["step-9-ticket"].artifacts[0]
        a["content"] = a["content"].replace("MSFT", "the stock")
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "ticket without MSFT still scored full marks"


def test_ticket_missing_barrier_level_loses_points():
    """A ticket with only the generic word 'knock-in' (no 80% level) must fail (Codex code-review)."""
    def mutate(replay):
        a = replay["step-9-ticket"].artifacts[0]
        a["content"] = a["content"].replace("80% of strike", "of strike")
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "ticket without the 80% level still scored full marks"


def test_stale_snapshot_date_loses_points():
    """A build priced off a non-accounting-date snapshot must fail the data-
    provenance ground (Run #26: luna fetched a 2025-01-02 close for the build)."""
    def mutate(replay):
        replay["step-4-build"].tool_results[0]["content"]["data"]["latest"]["date"] = "2025-01-02"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "stale-date snapshot still scored full marks"


def test_backdated_booking_loses_points():
    """A booking dated to the stale snapshot date instead of the accounting date
    must fail the anti-backdating adherence check (Run #26: luna's
    trade_effective_date=2025-01-02)."""
    def mutate(replay):
        replay["step-5-book"].ai["tool_calls"][0]["args"]["trade_effective_date"] = "2025-01-02"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "backdated booking still scored full marks"


def test_no_risk_read_loses_points():
    """Answering the delta question with NO risk read at all (no stored-run read,
    no in-memory calc) must fail both step-7 any_of checks (Run #26 redesign:
    the read is required, a prose-only delta is ungrounded)."""
    def mutate(replay):
        replay["step-8-impact"].ai["tool_calls"] = []
        replay["step-8-impact"].tool_results.clear()
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "prose-only delta answer still scored full marks"


def test_in_memory_calculate_risk_still_passes_step7():
    """The ad-hoc in-memory path stays an accepted alternative: swapping the
    canonical get_latest_risk_run read for a caller-supplied-snapshot
    calculate_risk must keep step 7 fully scored."""
    def mutate(replay):
        replay["step-8-impact"].ai["tool_calls"] = [
            {"id": "c8", "name": "calculate_risk",
             "args": {"positions": [{"product": {"quantark_class": "BarrierOption", "underlying": "MSFT",
                                                 "terms": {"strike": 502.06, "barrier": 401.648,
                                                           "barrier_type": "DOWN_IN", "option_type": "PUT",
                                                           "maturity": 1.0}}, "quantity": 1}],
                      "market": {"spot": 502.06}}}]
        replay["step-8-impact"].tool_results[:] = [
            {"tool_call_id": "c8", "name": "calculate_risk",
             "content": {"totals": {"delta": -0.41638929250939094},
                         "positions": [{"underlying": "MSFT", "product_type": "BarrierOption",
                                        "delta": -0.41638929250939094, "greeks_ok": True}]}}]
        replay["step-8-impact"].response_text = (
            "Net book impact from the new MSFT put — its delta -0.4164 lowers the desk's net delta.")
    score, passed, total = _score_with_mutation(mutate)
    # Exactly the two canonical-path checks drop — the step's expected_tool
    # (get_latest_risk_run) and the success-level tools_routed_sequence. Both
    # step-7 numeric/quote grounds must pass on the calculate_risk evidence.
    assert total - passed == 2, f"in-memory risk read lost {total - passed} checks"


def test_wrong_delta_number_loses_points():
    """A risk read whose MSFT delta is NOT the true engine constant must fail
    the numeric ground — and a response parroting the golden number then also
    fails the quote-binding (it no longer matches the tool truth)."""
    def mutate(replay):
        metrics = replay["step-8-impact"].tool_results[0]["content"]["metrics"]
        metrics["positions"][0]["delta"] = -0.10
    _, passed, total = _score_with_mutation(mutate)
    assert total - passed >= 2, "wrong delta number cost fewer than 2 checks"


def test_ungrounded_delta_prose_loses_points():
    """A response quoting a plausible-but-self-supplied delta (no matching tool
    value) must fail the quote-binding ground even though the word 'delta'
    appears."""
    def mutate(replay):
        replay["step-8-impact"].response_text = "The new trade's delta impact is -0.99."
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "self-supplied delta prose still scored full marks"


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
