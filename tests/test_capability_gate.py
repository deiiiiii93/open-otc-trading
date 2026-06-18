"""Unit tests for the capability gate decorator."""
from __future__ import annotations

import pytest
from langchain_core.tools import tool

from app.services.deep_agent.capability_gate import (
    CapabilityDeniedError,
    capability_gated,
)
from app.services.deep_agent.envelopes import Envelope, ToolGroup


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("dangerous_write")
def dangerous_write_tool(x: int) -> dict:
    """Pretend to mutate state."""
    return {"ok": True, "x": x}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("careful_read")
def careful_read_tool(x: int) -> dict:
    """Read-only diagnostic."""
    return {"read": True, "x": x}


def _config_with_envelope(env: Envelope) -> dict:
    return {"configurable": {"envelope": env.value}}


def test_gate_blocks_domain_write_under_pet_page():
    with pytest.raises(CapabilityDeniedError) as exc:
        dangerous_write_tool.invoke(
            {"x": 1}, config=_config_with_envelope(Envelope.PET_PAGE)
        )
    assert exc.value.group is ToolGroup.DOMAIN_WRITE
    assert exc.value.envelope is Envelope.PET_PAGE
    assert exc.value.tool_name == "dangerous_write"


def test_gate_allows_domain_write_under_desk_workflow():
    result = dangerous_write_tool.invoke(
        {"x": 1}, config=_config_with_envelope(Envelope.DESK_WORKFLOW)
    )
    assert result["ok"] is True
    assert result["x"] == 1


def test_gate_defaults_to_pet_page_when_envelope_missing():
    """No envelope in config -> fail closed: assume the most restrictive."""
    with pytest.raises(CapabilityDeniedError):
        dangerous_write_tool.invoke({"x": 1})


def test_gate_defaults_to_pet_page_when_config_has_bogus_envelope():
    """Unknown envelope string also fails closed."""
    with pytest.raises(CapabilityDeniedError):
        dangerous_write_tool.invoke(
            {"x": 1}, config={"configurable": {"envelope": "made_up"}}
        )


def test_gate_blocks_domain_read_under_pet_page():
    with pytest.raises(CapabilityDeniedError):
        careful_read_tool.invoke(
            {"x": 5}, config=_config_with_envelope(Envelope.PET_PAGE)
        )


def test_gate_allows_domain_read_under_pet_diagnostic():
    result = careful_read_tool.invoke(
        {"x": 5}, config=_config_with_envelope(Envelope.PET_DIAGNOSTIC)
    )
    assert result == {"read": True, "x": 5}


def test_gate_stashes_capability_group_on_tool():
    """CI test in 4.5b reads this attribute to verify every tool was wrapped."""
    assert dangerous_write_tool.__capability_group__ is ToolGroup.DOMAIN_WRITE
    assert careful_read_tool.__capability_group__ is ToolGroup.DOMAIN_READ


@pytest.mark.asyncio
async def test_ainvoke_is_gated_too():
    with pytest.raises(CapabilityDeniedError):
        await dangerous_write_tool.ainvoke(
            {"x": 1}, config=_config_with_envelope(Envelope.PET_PAGE)
        )
    result = await dangerous_write_tool.ainvoke(
        {"x": 2}, config=_config_with_envelope(Envelope.DESK_WORKFLOW)
    )
    assert result["x"] == 2
