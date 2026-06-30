"""Registry load + deterministic regression replay for trader-rfq-booking-day."""
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
    ]
    # Every replay key referenced by a step exists in the fixtures.
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay


def test_trader_rfq_regression_replay_scores_full():
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    transcript = transcript_from_replay(loaded)
    score, passed, total = objective_score(transcript, loaded)
    # The clean replay path should satisfy every objective check.
    assert passed == total, f"{passed}/{total} objective checks passed"
