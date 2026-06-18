"""Escalation engine.

Wraps a LangGraph agent invocation. If the model picks a tool that the
current envelope blocks (the gate raises ``CapabilityDeniedError``), we
consult the transition table and re-invoke the same agent once under
the widened envelope. Only one transition per turn — a second denial
bubbles up to the caller, which surfaces a structured error to the user.

Audit events are persisted via the ``record_audit`` callable the caller
provides; we do not import the database layer here so this module stays
unit-testable.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .capability_gate import CapabilityDeniedError
from .envelopes import Envelope, reason_for_denied_group, transition


AuditCallback = Callable[[dict], Awaitable[None]]


def resolve_escalation(
    denial: CapabilityDeniedError,
) -> tuple[Envelope, dict] | None:
    """Pure policy: decide whether to escalate, and produce an audit payload.

    Returns ``(new_envelope, audit_payload)`` on a successful match, or
    ``None`` if the denial has no defined transition and must bubble up.
    Shared by both ``run_with_escalation`` (the ainvoke path) and the
    streaming caller in ``AgentService.stream_and_persist``.
    """
    reason = reason_for_denied_group(denial.envelope, denial.group)
    if reason is None:
        return None
    new_envelope = transition(denial.envelope, reason)
    if new_envelope is None:
        return None
    audit_payload = {
        "event_type": "envelope.transitioned",
        "previous_envelope": denial.envelope.value,
        "new_envelope": new_envelope.value,
        "reason": reason.value,
        "denied_tool": denial.tool_name,
        "denied_group": denial.group.value,
    }
    return new_envelope, audit_payload


async def run_with_escalation(
    graph: Any,
    *,
    state: dict,
    envelope: Envelope,
    record_audit: AuditCallback,
    config_extras: dict | None = None,
) -> dict:
    """Run ``graph.ainvoke(state, config)`` with one-shot escalation on denial.

    Parameters:
        graph: a LangGraph agent (must expose async ``ainvoke(state, config)``).
        state: the LangGraph state dict (messages + whatever else).
        envelope: the initial envelope for this turn.
        record_audit: async callback invoked once when a transition happens.
        config_extras: optional dict merged into ``configurable`` (e.g.,
            thread_id, run_id) so the gate has access to them.

    Returns the agent's final state dict. A second denial after escalation
    re-raises ``CapabilityDeniedError`` so the caller can surface a
    structured error.
    """
    extras = dict(config_extras or {})
    first_config = {
        "configurable": {"envelope": envelope.value, **extras},
    }
    try:
        return await graph.ainvoke(state, config=first_config)
    except CapabilityDeniedError as denial:
        resolution = resolve_escalation(denial)
        if resolution is None:
            raise
        new_envelope, audit_payload = resolution
        await record_audit(audit_payload)
        # One transition per turn — do NOT wrap this re-invoke in another
        # try/except. A second denial bubbles up.
        retry_config = {
            "configurable": {"envelope": new_envelope.value, **extras},
        }
        return await graph.ainvoke(state, config=retry_config)


__all__ = ["resolve_escalation", "run_with_escalation", "AuditCallback"]
