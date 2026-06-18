"""Unit tests for the escalation engine."""
from __future__ import annotations

from typing import Any

import pytest

from app.services.deep_agent.capability_gate import CapabilityDeniedError
from app.services.deep_agent.envelopes import Envelope, ToolGroup
from app.services.deep_agent.escalation import run_with_escalation


class _FakeGraph:
    """Pretends to be a LangGraph agent.

    Captures the envelope (and any extras) each invoke received. ``denied_envelopes``
    lists envelopes under which the next invoke should raise CapabilityDeniedError.
    The fake denies DOMAIN_WRITE by default, but the test can override the group.
    """

    def __init__(
        self,
        *,
        denied_envelopes: list[Envelope],
        denied_group: ToolGroup = ToolGroup.DOMAIN_WRITE,
    ):
        self._denied = list(denied_envelopes)
        self._denied_group = denied_group
        self.invocations: list[dict[str, Any]] = []

    async def ainvoke(self, state: dict, config: dict) -> dict:
        env = Envelope(config["configurable"]["envelope"])
        self.invocations.append({"envelope": env, "config": config, "state": state})
        if env in self._denied:
            raise CapabilityDeniedError(
                envelope=env, group=self._denied_group, tool_name="fake_tool"
            )
        return {"messages": [{"role": "assistant", "content": "ok"}]}


@pytest.mark.asyncio
async def test_pet_page_escalates_to_desk_workflow_on_domain_write_denial():
    graph = _FakeGraph(denied_envelopes=[Envelope.PET_PAGE])
    audit_log: list[dict] = []

    async def audit(event: dict) -> None:
        audit_log.append(event)

    result = await run_with_escalation(
        graph,
        state={"messages": []},
        envelope=Envelope.PET_PAGE,
        record_audit=audit,
    )
    envs_called = [inv["envelope"] for inv in graph.invocations]
    assert envs_called == [Envelope.PET_PAGE, Envelope.DESK_WORKFLOW]
    assert audit_log[0]["event_type"] == "envelope.transitioned"
    assert audit_log[0]["previous_envelope"] == "pet_page"
    assert audit_log[0]["new_envelope"] == "desk_workflow"
    assert audit_log[0]["reason"] == "write_action_requested"
    assert audit_log[0]["denied_tool"] == "fake_tool"
    assert audit_log[0]["denied_group"] == "domain_write"
    assert result["messages"][0]["content"] == "ok"


@pytest.mark.asyncio
async def test_pet_page_escalates_to_pet_diagnostic_on_domain_read_denial():
    graph = _FakeGraph(
        denied_envelopes=[Envelope.PET_PAGE], denied_group=ToolGroup.DOMAIN_READ
    )
    audit_log: list[dict] = []

    async def audit(event: dict) -> None:
        audit_log.append(event)

    await run_with_escalation(
        graph,
        state={"messages": []},
        envelope=Envelope.PET_PAGE,
        record_audit=audit,
    )
    envs_called = [inv["envelope"] for inv in graph.invocations]
    assert envs_called == [Envelope.PET_PAGE, Envelope.PET_DIAGNOSTIC]
    assert audit_log[0]["new_envelope"] == "pet_diagnostic"
    assert audit_log[0]["reason"] == "diagnostic_followup"


@pytest.mark.asyncio
async def test_desk_workflow_escalates_to_desk_async_on_async_denial():
    graph = _FakeGraph(
        denied_envelopes=[Envelope.DESK_WORKFLOW],
        denied_group=ToolGroup.ASYNC_DISPATCH,
    )
    audit_log: list[dict] = []

    async def audit(event: dict) -> None:
        audit_log.append(event)

    await run_with_escalation(
        graph,
        state={"messages": []},
        envelope=Envelope.DESK_WORKFLOW,
        record_audit=audit,
    )
    envs_called = [inv["envelope"] for inv in graph.invocations]
    assert envs_called == [Envelope.DESK_WORKFLOW, Envelope.DESK_ASYNC]


@pytest.mark.asyncio
async def test_single_transition_per_turn():
    """A second denial after escalation must NOT trigger a second escalation."""
    graph = _FakeGraph(
        denied_envelopes=[Envelope.PET_PAGE, Envelope.DESK_WORKFLOW]
    )
    audit_log: list[dict] = []

    async def audit(event: dict) -> None:
        audit_log.append(event)

    with pytest.raises(CapabilityDeniedError):
        await run_with_escalation(
            graph,
            state={"messages": []},
            envelope=Envelope.PET_PAGE,
            record_audit=audit,
        )
    # Two invocations, one transition logged.
    assert len(graph.invocations) == 2
    assert len(audit_log) == 1


@pytest.mark.asyncio
async def test_no_transition_when_no_rule_matches_re_raises():
    """DESK_ASYNC denial of async_dispatch — no further transition defined."""
    graph = _FakeGraph(
        denied_envelopes=[Envelope.DESK_ASYNC],
        denied_group=ToolGroup.ASYNC_DISPATCH,
    )
    audit_log: list[dict] = []

    async def audit(event: dict) -> None:
        audit_log.append(event)

    with pytest.raises(CapabilityDeniedError):
        await run_with_escalation(
            graph,
            state={"messages": []},
            envelope=Envelope.DESK_ASYNC,
            record_audit=audit,
        )
    # Audit was NOT called because the transition lookup returned None.
    assert audit_log == []


@pytest.mark.asyncio
async def test_config_extras_threaded_into_invocations():
    """thread_id/run_id passed through both first attempt and retry."""
    graph = _FakeGraph(denied_envelopes=[Envelope.PET_PAGE])
    captured_audit: list[dict] = []

    async def audit(event: dict) -> None:
        captured_audit.append(event)

    await run_with_escalation(
        graph,
        state={"messages": []},
        envelope=Envelope.PET_PAGE,
        record_audit=audit,
        config_extras={"thread_id": 42, "run_id": "abc"},
    )
    for inv in graph.invocations:
        configurable = inv["config"]["configurable"]
        assert configurable["thread_id"] == 42
        assert configurable["run_id"] == "abc"


@pytest.mark.asyncio
async def test_no_denial_returns_first_attempt_directly():
    """The happy path: no escalation needed, no audit emitted."""
    graph = _FakeGraph(denied_envelopes=[])
    audit_log: list[dict] = []

    async def audit(event: dict) -> None:
        audit_log.append(event)

    result = await run_with_escalation(
        graph,
        state={"messages": []},
        envelope=Envelope.DESK_WORKFLOW,
        record_audit=audit,
    )
    assert len(graph.invocations) == 1
    assert audit_log == []
    assert result["messages"][0]["content"] == "ok"
