"""Tests for the envelope catalog + transition table."""
from __future__ import annotations

import pytest

from app.services.deep_agent.envelopes import (
    Envelope,
    EscalationReason,
    ToolGroup,
    reason_for_denied_group,
    tool_allowed,
    transition,
)


def test_envelope_enum_has_four_members():
    assert {e.value for e in Envelope} == {
        "pet_page", "pet_diagnostic", "desk_workflow", "desk_async",
    }


def test_escalation_reason_enum_complete():
    expected = {
        "missing_required_context",
        "diagnostic_followup",
        "cross_page_dependency",
        "write_action_requested",
        "long_running_work",
        "large_result_set",
        "tool_denied_by_envelope",
    }
    assert {r.value for r in EscalationReason} == expected


def test_pet_page_blocks_domain_write():
    assert tool_allowed(Envelope.PET_PAGE, ToolGroup.DOMAIN_WRITE) is False


def test_pet_page_blocks_domain_read():
    """Diagnostic-tier reads also blocked under pet_page (escalate to pet_diagnostic)."""
    assert tool_allowed(Envelope.PET_PAGE, ToolGroup.DOMAIN_READ) is False


def test_pet_diagnostic_allows_domain_read_but_blocks_writes():
    assert tool_allowed(Envelope.PET_DIAGNOSTIC, ToolGroup.DOMAIN_READ) is True
    assert tool_allowed(Envelope.PET_DIAGNOSTIC, ToolGroup.DOMAIN_WRITE) is False


def test_desk_workflow_allows_domain_read_and_write():
    assert tool_allowed(Envelope.DESK_WORKFLOW, ToolGroup.DOMAIN_READ) is True
    assert tool_allowed(Envelope.DESK_WORKFLOW, ToolGroup.DOMAIN_WRITE) is True


def test_only_desk_async_can_dispatch_async():
    for env in Envelope:
        expected = env is Envelope.DESK_ASYNC
        assert tool_allowed(env, ToolGroup.ASYNC_DISPATCH) is expected


def test_all_envelopes_allow_page_groups_and_deterministic_py():
    """page_read/detail/action, task_poll, deterministic_py are universal."""
    universal = {
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
    }
    for env in Envelope:
        for group in universal:
            assert tool_allowed(env, group), f"{env.value} should allow {group.value}"


@pytest.mark.parametrize(
    "current, reason, expected",
    [
        (Envelope.PET_PAGE, EscalationReason.DIAGNOSTIC_FOLLOWUP, Envelope.PET_DIAGNOSTIC),
        (Envelope.PET_PAGE, EscalationReason.MISSING_REQUIRED_CONTEXT, Envelope.PET_DIAGNOSTIC),
        (Envelope.PET_PAGE, EscalationReason.WRITE_ACTION_REQUESTED, Envelope.DESK_WORKFLOW),
        (Envelope.PET_PAGE, EscalationReason.CROSS_PAGE_DEPENDENCY, Envelope.DESK_WORKFLOW),
        # Pet-tier async dispatch jumps straight to DESK_ASYNC (skipping
        # DESK_WORKFLOW which also denies ASYNC_DISPATCH).
        (Envelope.PET_PAGE, EscalationReason.LONG_RUNNING_WORK, Envelope.DESK_ASYNC),
        (Envelope.PET_DIAGNOSTIC, EscalationReason.LONG_RUNNING_WORK, Envelope.DESK_ASYNC),
        (Envelope.PET_DIAGNOSTIC, EscalationReason.CROSS_PAGE_DEPENDENCY, Envelope.DESK_WORKFLOW),
        (Envelope.PET_DIAGNOSTIC, EscalationReason.WRITE_ACTION_REQUESTED, Envelope.DESK_WORKFLOW),
        (Envelope.DESK_WORKFLOW, EscalationReason.LONG_RUNNING_WORK, Envelope.DESK_ASYNC),
        # No transition out of DESK_ASYNC.
        (Envelope.DESK_ASYNC, EscalationReason.LONG_RUNNING_WORK, None),
        # No transition exists for this reason at this envelope.
        (Envelope.DESK_WORKFLOW, EscalationReason.DIAGNOSTIC_FOLLOWUP, None),
    ],
)
def test_transition_table(current, reason, expected):
    assert transition(current, reason) == expected


def test_pet_async_dispatch_denial_resolves_to_desk_async():
    """End-to-end: pet_page → ASYNC_DISPATCH denied → LONG_RUNNING_WORK
    reason → DESK_ASYNC. Without the pet/diagnostic LONG_RUNNING_WORK
    transitions, resolve_escalation would return None and the user would
    get a hard denial instead of widening. Regression for iter-4 P2."""
    reason = reason_for_denied_group(Envelope.PET_PAGE, ToolGroup.ASYNC_DISPATCH)
    assert reason is EscalationReason.LONG_RUNNING_WORK
    assert transition(Envelope.PET_PAGE, reason) is Envelope.DESK_ASYNC
    # Same for pet_diagnostic, the intermediate pet tier.
    assert transition(Envelope.PET_DIAGNOSTIC, reason) is Envelope.DESK_ASYNC


def test_reason_for_denied_group_domain_write_from_pet():
    assert (
        reason_for_denied_group(Envelope.PET_PAGE, ToolGroup.DOMAIN_WRITE)
        is EscalationReason.WRITE_ACTION_REQUESTED
    )


def test_reason_for_denied_group_domain_read_from_pet_page():
    assert (
        reason_for_denied_group(Envelope.PET_PAGE, ToolGroup.DOMAIN_READ)
        is EscalationReason.DIAGNOSTIC_FOLLOWUP
    )


def test_reason_for_denied_group_async_dispatch_from_desk_workflow():
    assert (
        reason_for_denied_group(Envelope.DESK_WORKFLOW, ToolGroup.ASYNC_DISPATCH)
        is EscalationReason.LONG_RUNNING_WORK
    )


def test_reason_for_denied_group_returns_none_when_allowed():
    assert reason_for_denied_group(Envelope.DESK_WORKFLOW, ToolGroup.DOMAIN_WRITE) is None
