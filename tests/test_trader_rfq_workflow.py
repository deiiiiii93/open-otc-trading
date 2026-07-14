"""Registry load + deterministic regression replay for trader-rfq-booking-day."""
import json
from pathlib import Path

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score


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
