from __future__ import annotations

from app.schemas import AgentActionProposal


def test_agent_action_proposal_uses_tool_name():
    proposal = AgentActionProposal(
        id="intr-1:0",
        tool_name="run_batch_pricing",
        label="Run risk analysis",
        summary="Run summary risk for portfolio #7",
        payload={"portfolio_id": 7},
    )

    assert proposal.tool_name == "run_batch_pricing"
    assert proposal.requires_confirmation is True
    assert proposal.status == "pending"
    assert proposal.persona is None
    assert proposal.risk_level is None


def test_agent_action_proposal_legacy_type_field_is_accepted_and_normalized():
    """Old thread history rows have `type` and no `tool_name`. The schema must
    accept them and normalize so downstream code only needs to read tool_name."""
    proposal = AgentActionProposal.model_validate({
        "id": "act-old",
        "type": "approve_rfq",
        "label": "Approve RFQ",
        "summary": "...",
        "payload": {"rfq_id": 42},
    })

    assert proposal.tool_name == "approve_rfq"


def test_agent_action_proposal_optional_fields():
    proposal = AgentActionProposal(
        id="intr-2:0",
        tool_name="approve_rfq",
        label="Approve RFQ",
        summary="...",
        persona="high_board",
        risk_level="irreversible",
    )

    assert proposal.persona == "high_board"
    assert proposal.risk_level == "irreversible"


import pytest

from app.schemas import (
    AgentChannelOut,
    AgentMessageCreate,
    AgentModelConfigOut,
    AgentModelOption,
    AgentModelSelection,
)


def test_agent_model_selection_requires_channel_provider_model():
    s = AgentModelSelection(channel="zenmux", provider="anthropic", model="m")
    assert s.channel == "zenmux"
    assert s.provider == "anthropic"
    assert s.model == "m"


def test_agent_model_selection_provider_is_free_form():
    # Used to be Literal["anthropic", "openai"]; now any string.
    s = AgentModelSelection(channel="deepseek", provider="deepseek", model="deepseek-v4-flash")
    assert s.provider == "deepseek"


def test_agent_model_selection_backfills_channel_for_legacy_dict():
    # Legacy thread history rows have only {provider, model}.
    s = AgentModelSelection.model_validate({"provider": "anthropic", "model": "m"})
    assert s.channel == "zenmux"


def test_agent_model_option_has_optional_tags():
    o = AgentModelOption(channel="c", provider="x", model="m", label="M")
    assert o.tags == []
    o2 = AgentModelOption(
        channel="c", provider="x", model="m", label="M",
        tags=["fast", "tool-use"],
    )
    assert o2.tags == ["fast", "tool-use"]


def test_agent_channel_out_includes_healthy_flag():
    ch = AgentChannelOut(name="c", label="C", type="openai_compatible")
    assert ch.healthy is True
    assert ch.models == []


def test_agent_model_config_out_holds_nested_channels():
    cfg = AgentModelConfigOut(
        enabled=True,
        active=AgentModelSelection(channel="zenmux", provider="anthropic", model="m"),
        channels=[
            AgentChannelOut(
                name="zenmux", label="Zenmux", type="zenmux", healthy=True,
                models=[
                    AgentModelOption(channel="zenmux", provider="anthropic", model="m", label="M"),
                ],
            )
        ],
    )
    assert cfg.channels[0].models[0].label == "M"


def test_agent_message_create_accepts_optional_model():
    payload = AgentMessageCreate.model_validate({
        "content": "hi",
        "model": {"channel": "zenmux", "provider": "anthropic", "model": "m"},
    })
    assert payload.model is not None
    assert payload.model.channel == "zenmux"
