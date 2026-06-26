"""Envelope catalog — typed capability scopes for the shared runtime.

An envelope is a typed capability scope. The runtime grants a tool-group
set per envelope; tools wrapped with the capability gate consult this
module at invoke time. Escalation transitions are also defined here.
"""
from __future__ import annotations

from enum import Enum


class Envelope(str, Enum):
    PET_PAGE = "pet_page"
    PET_DIAGNOSTIC = "pet_diagnostic"
    DESK_WORKFLOW = "desk_workflow"
    DESK_ASYNC = "desk_async"
    # Goal-mode grader: observes the ledger to render an acceptance verdict.
    # Deliberately narrower than PET_DIAGNOSTIC — DOMAIN_READ only, fail closed.
    GOAL_GRADER_READ = "goal_grader_read"


class EscalationReason(str, Enum):
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    DIAGNOSTIC_FOLLOWUP = "diagnostic_followup"
    CROSS_PAGE_DEPENDENCY = "cross_page_dependency"
    WRITE_ACTION_REQUESTED = "write_action_requested"
    LONG_RUNNING_WORK = "long_running_work"
    LARGE_RESULT_SET = "large_result_set"
    TOOL_DENIED_BY_ENVELOPE = "tool_denied_by_envelope"


class ToolGroup(str, Enum):
    PAGE_READ = "page_read"
    PAGE_DETAIL = "page_detail"
    PAGE_ACTION = "page_action"
    TASK_POLL = "task_poll"
    DETERMINISTIC_PY = "deterministic_py"
    DOMAIN_READ = "domain_read"
    DOMAIN_WRITE = "domain_write"
    ASYNC_DISPATCH = "async_dispatch"


_ALLOWED: dict[Envelope, frozenset[ToolGroup]] = {
    Envelope.PET_PAGE: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
    }),
    Envelope.PET_DIAGNOSTIC: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
    }),
    Envelope.DESK_WORKFLOW: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
        ToolGroup.DOMAIN_WRITE,
    }),
    Envelope.DESK_ASYNC: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
        ToolGroup.DOMAIN_WRITE,
        ToolGroup.ASYNC_DISPATCH,
    }),
    # Grader reads ledger/domain state to verify criteria; nothing else.
    Envelope.GOAL_GRADER_READ: frozenset({
        ToolGroup.DOMAIN_READ,
    }),
}


def tool_allowed(envelope: Envelope, group: ToolGroup) -> bool:
    """Return True if the given tool group is permitted under this envelope."""
    return group in _ALLOWED[envelope]


_TRANSITIONS: dict[tuple[Envelope, EscalationReason], Envelope] = {
    (Envelope.PET_PAGE, EscalationReason.DIAGNOSTIC_FOLLOWUP): Envelope.PET_DIAGNOSTIC,
    (Envelope.PET_PAGE, EscalationReason.MISSING_REQUIRED_CONTEXT): Envelope.PET_DIAGNOSTIC,
    (Envelope.PET_PAGE, EscalationReason.WRITE_ACTION_REQUESTED): Envelope.DESK_WORKFLOW,
    (Envelope.PET_PAGE, EscalationReason.CROSS_PAGE_DEPENDENCY): Envelope.DESK_WORKFLOW,
    # Pet-tier async dispatch denials jump straight to DESK_ASYNC; going via
    # DESK_WORKFLOW would still deny ASYNC_DISPATCH and burn an escalation.
    (Envelope.PET_PAGE, EscalationReason.LONG_RUNNING_WORK): Envelope.DESK_ASYNC,
    (Envelope.PET_DIAGNOSTIC, EscalationReason.CROSS_PAGE_DEPENDENCY): Envelope.DESK_WORKFLOW,
    (Envelope.PET_DIAGNOSTIC, EscalationReason.WRITE_ACTION_REQUESTED): Envelope.DESK_WORKFLOW,
    (Envelope.PET_DIAGNOSTIC, EscalationReason.LONG_RUNNING_WORK): Envelope.DESK_ASYNC,
    (Envelope.DESK_WORKFLOW, EscalationReason.LONG_RUNNING_WORK): Envelope.DESK_ASYNC,
}


def transition(envelope: Envelope, reason: EscalationReason) -> Envelope | None:
    """Return the new envelope after escalation, or None if no transition exists."""
    return _TRANSITIONS.get((envelope, reason))


def reason_for_denied_group(
    envelope: Envelope, group: ToolGroup
) -> EscalationReason | None:
    """If `group` is denied under `envelope`, return the reason to escalate."""
    if tool_allowed(envelope, group):
        return None
    if group is ToolGroup.DOMAIN_WRITE:
        return EscalationReason.WRITE_ACTION_REQUESTED
    if group is ToolGroup.DOMAIN_READ:
        return EscalationReason.DIAGNOSTIC_FOLLOWUP
    if group is ToolGroup.ASYNC_DISPATCH:
        return EscalationReason.LONG_RUNNING_WORK
    return EscalationReason.TOOL_DENIED_BY_ENVELOPE


__all__ = [
    "Envelope",
    "EscalationReason",
    "ToolGroup",
    "tool_allowed",
    "transition",
    "reason_for_denied_group",
]
