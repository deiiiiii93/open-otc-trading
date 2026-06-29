"""Registry load + deterministic regression replay for trader-rfq-booking-day."""
from app.golden_workflows.registry import get_workflow_bundle


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
