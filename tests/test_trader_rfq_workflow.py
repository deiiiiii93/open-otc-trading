"""Registry load + deterministic regression replay for trader-rfq-booking-day.

Includes NEGATIVE scorer tests (Codex code-review): a canned replay earning full
marks proves the manifest is satisfiable, not that its checks DISCRIMINATE. Each
negative test mutates the replay into an adversarial-but-plausible wrong run and
asserts the score drops — otherwise a wrong trade/price/refusal would rank as
fully correct.
"""
import copy
import json
from pathlib import Path

import pytest

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score


def _score_with_mutation(mutate) -> tuple[float, int, int]:
    """Deep-copy the loaded bundle, mutate its replay, and re-score. The copy keeps
    the cached registry bundle uncorrupted for other tests."""
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
        None,  # step 9: write-free build-validation trap
    ]
    # Every replay key referenced by a step exists in the fixtures.
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay


def test_trader_rfq_is_par_calibrated():
    """par_tool_calls opts the workflow into golf-style EFF scoring."""
    from app.services.arena import scoring
    wf = get_workflow_bundle("trader-rfq-booking-day").workflow
    assert wf.par_tool_calls is not None
    assert scoring.par_calibrated(wf)


def test_trader_rfq_grounding_targets_match_truth_file():
    """Manifest answer_field_quotes values must equal the harvested truth (no drift)."""
    defn = Path("backend/app/golden_workflows/definitions")
    truth = json.loads((defn / "trader-rfq-booking-day.truth.json").read_text())
    premium = truth["msft_quote_premium"]["value"]
    wf = get_workflow_bundle("trader-rfq-booking-day").workflow
    quotes = [a for s in wf.steps for a in s.assertions
              if a.type == "answer_field_quotes" and a.field == "premium"]
    assert quotes, "no premium answer_field_quotes in manifest"
    assert quotes[0].value == premium


def test_trader_rfq_regression_replay_scores_full():
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    transcript = transcript_from_replay(loaded)
    score, passed, total = objective_score(transcript, loaded)
    # The clean replay path should satisfy every objective check.
    assert passed == total, f"{passed}/{total} objective checks passed"
    assert score == 100.0, f"score={score}"


# --- Negative scorer tests: the checks must DISCRIMINATE, not just be satisfiable.


def test_wrong_quote_price_loses_points():
    """A corrupted/​wrong persisted quote price must fail the truth-band bind."""
    def mutate(replay):
        replay["step-2-quote"].tool_results[0]["content"]["quote_payload"]["achieved_price"] = 999.0
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "achieved_price=999 still scored full marks"


def test_wrong_rfq_underlying_loses_points():
    """An RFQ captured for the wrong instrument must fail the intake term bind."""
    def mutate(replay):
        replay["step-1-intake"].tool_results[0]["content"]["request_payload"]["underlying"] = "AAPL"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "wrong underlying (AAPL) still scored full marks"


def test_wrong_booked_direction_loses_points():
    """A DOWN_OUT booking (wrong direction) must fail the booked-position bind."""
    def mutate(replay):
        replay["step-6-snapshot"].tool_results[0]["content"]["positions"][0]["barrier_type"] = "DOWN_OUT"
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "DOWN_OUT booking still scored full marks"


def test_trap_refusal_without_build_loses_points():
    """A hallucinated 'unsupported' refusal (no build_product call) must fail the trap."""
    def mutate(replay):
        replay["step-9-trap-unsupported-family"].ai["tool_calls"] = []
        replay["step-9-trap-unsupported-family"].tool_results.clear()
    _, passed, total = _score_with_mutation(mutate)
    assert passed < total, "no-tool refusal still scored full marks"


def test_validate_quote_rejects_failed_pricing():
    """The determinism harvester must not certify a pricing_failed / zero-price quote."""
    from app.golden_workflows.determinism import _validate_quote
    from app.models import RfqStatus

    class _RFQ:
        def __init__(self, status):
            self.status = status

    # pricing_failed status → rejected even with a numeric price
    with pytest.raises(AssertionError):
        _validate_quote(_RFQ(RfqStatus.PRICING_FAILED.value),
                        {"achieved_price": 12.3, "engine": "BarrierAnalyticalEngine"})
    # success status but zero price (QuantArk-raise sentinel) → rejected
    with pytest.raises(AssertionError):
        _validate_quote(_RFQ(RfqStatus.PENDING_APPROVAL.value),
                        {"achieved_price": 0.0, "engine": "BarrierAnalyticalEngine"})
    # success + positive price + expected engine → accepted
    ok = _validate_quote(_RFQ(RfqStatus.PENDING_APPROVAL.value),
                         {"achieved_price": 8.52, "engine": "BarrierAnalyticalEngine"})
    assert ok["achieved_price"] == 8.52
