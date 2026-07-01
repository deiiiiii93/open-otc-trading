"""Task 3: server-derived fan-out attribution (allowlisted seed workflows only)."""
from app.services.deep_agent.dynamic_subagents import (
    FANOUT_ATTRIBUTION_CASE3,
    FANOUT_ATTRIBUTION_KEY,
    FANOUT_WORKFLOW_ID_KEY,
    fanout_attribution_extra,
)


def test_stamps_for_allowlisted_seed_workflow():
    assert fanout_attribution_extra(slug="morning-risk-breach-commentary", source="seed") == {
        FANOUT_ATTRIBUTION_KEY: FANOUT_ATTRIBUTION_CASE3,
        FANOUT_WORKFLOW_ID_KEY: "morning-risk-breach-commentary",
    }


def test_no_stamp_for_user_source_even_if_allowlisted_slug():
    assert fanout_attribution_extra(slug="morning-risk-breach-commentary", source="user") == {}


def test_no_stamp_for_non_allowlisted():
    assert fanout_attribution_extra(slug="whatever", source="seed") == {}


def test_no_stamp_for_plain_chat():
    assert fanout_attribution_extra(slug=None, source=None) == {}
