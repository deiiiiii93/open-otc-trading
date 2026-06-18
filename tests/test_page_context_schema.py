"""Tests for the extended AgentPageContext + new PageAction / LoadedContext schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AgentPageContext, LoadedContext, PageAction


def test_page_context_parses_with_legacy_shape():
    """Backward compat: the legacy payload (path + chips, no loaded_context) still parses."""
    ctx = AgentPageContext(
        route="positions",
        title="Positions",
        path="/positions",
        chips=["portfolio:1"],
    )
    assert ctx.loaded_context is None
    assert ctx.actions == []
    assert ctx.chips == ["portfolio:1"]
    assert ctx.path == "/positions"


def test_loaded_context_defaults_to_none_when_omitted():
    """Backward compatibility: a payload without loaded_context still parses."""
    ctx = AgentPageContext(route="positions", title="Positions")
    assert ctx.loaded_context is None


def test_loaded_context_accepts_paginated_with_counts():
    lc = LoadedContext(
        completeness="paginated",
        visible_count=20,
        total_count=120,
        query_ref="portfolio:7",
    )
    assert lc.completeness == "paginated"
    assert lc.visible_count == 20
    assert lc.total_count == 120
    assert lc.query_ref == "portfolio:7"


def test_loaded_context_rejects_invalid_completeness():
    with pytest.raises(ValidationError):
        LoadedContext(completeness="bogus")  # type: ignore[arg-type]


def test_page_action_required_fields():
    a = PageAction(
        name="run_batch_pricing",
        required_ids=["portfolio_id"],
        confirmation="implicit",
        backend_endpoint="POST /api/batch-pricing/runs",
    )
    assert a.name == "run_batch_pricing"
    assert a.required_ids == ["portfolio_id"]
    assert a.confirmation == "implicit"


def test_page_action_defaults():
    """name is the only truly required field; rest have sensible defaults."""
    a = PageAction(name="just_a_name")
    assert a.required_ids == []
    assert a.confirmation == "explicit"
    assert a.backend_endpoint == ""


def test_page_action_rejects_unknown_confirmation():
    with pytest.raises(ValidationError):
        PageAction(
            name="run_batch_pricing",
            required_ids=[],
            confirmation="maybe",  # type: ignore[arg-type]
            backend_endpoint="POST /x",
        )


def test_page_context_carries_actions_and_loaded_context():
    ctx = AgentPageContext(
        route="risk",
        title="Risk",
        entity_ids={"portfolio_id": 7, "pricing_profile_id": "default"},
        snapshot={"risk_totals": {}},
        loaded_context=LoadedContext(completeness="complete", visible_count=12, total_count=12),
        actions=[
            PageAction(name="run_batch_pricing", required_ids=["portfolio_id"], confirmation="explicit"),
        ],
    )
    assert ctx.loaded_context is not None
    assert ctx.loaded_context.completeness == "complete"
    assert len(ctx.actions) == 1
    assert ctx.actions[0].name == "run_batch_pricing"


def test_agent_message_create_accepts_envelope_literal():
    from app.schemas import AgentMessageCreate

    msg = AgentMessageCreate(content="hi", envelope="pet_page")
    assert msg.envelope == "pet_page"


def test_agent_message_create_rejects_bogus_envelope():
    from app.schemas import AgentMessageCreate

    with pytest.raises(ValidationError):
        AgentMessageCreate(content="hi", envelope="bogus")  # type: ignore[arg-type]


def test_agent_message_create_envelope_defaults_to_none():
    from app.schemas import AgentMessageCreate

    msg = AgentMessageCreate(content="hi")
    assert msg.envelope is None
