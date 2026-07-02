"""Durable recorder for the dangerous-action audit trail (audit spec §5).

Phase-1 (`record_attempt`) is FAIL-CLOSED: bounded busy-retry, then
AuditUnavailableError — the caller must refuse the tool call. Phase-2
(`record_outcome`) and refusal rows are best-effort (log on failure): the
durable attempt row already exists, only the outcome may be unknown.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.exc import OperationalError, SQLAlchemyError

from .. import database
from ..models import AgentActionAudit
from .deep_agent.audit_redaction import redact_args, redact_text

logger = logging.getLogger(__name__)

AUDIT_CONTEXT_KEY = "__audit_context__"

# Bounded backoff before a fail-closed refusal (spec §5.2 lock handling).
_RETRY_DELAYS: tuple[float, ...] = (0.1, 0.3, 0.9)

_unpersisted_refusals = 0
_refusal_lock = threading.Lock()


class AuditUnavailableError(RuntimeError):
    """Raised when the phase-1 attempt record cannot be committed."""


_CTX_COLUMNS = (
    "mode", "envelope", "actor", "model", "persona", "thread_id",
    "workflow_id", "session_id", "task_id", "message_id", "desk_workflow_slug",
)


def _context_columns(context: dict[str, Any] | None) -> dict[str, Any]:
    ctx = context or {}
    out = {k: ctx.get(k) for k in _CTX_COLUMNS}
    if out.get("actor") is None:
        out["actor"] = "agent"
    return out


def _insert_with_retry(row: AgentActionAudit) -> int:
    last_exc: Exception | None = None
    for delay in (*_RETRY_DELAYS, None):
        try:
            with database.SessionLocal() as session:
                session.add(row)
                session.commit()
                return row.id
        except OperationalError as exc:
            last_exc = exc
            if delay is None:
                break
            time.sleep(delay)
    raise AuditUnavailableError(
        f"audit attempt row could not be committed after {len(_RETRY_DELAYS) + 1} tries"
    ) from last_exc


def record_attempt(
    *,
    tool_name: str,
    tool_class: str,
    tool_call_id: str | None,
    args: dict[str, Any] | None,
    context: dict[str, Any] | None,
) -> int:
    """FAIL-CLOSED phase 1: commit an 'attempted' row or raise."""
    payload, redacted = redact_args(tool_name, args)
    row = AgentActionAudit(
        kind="execution",
        status="attempted",
        tool_name=tool_name,
        tool_class=tool_class,
        tool_call_id=tool_call_id,
        audit_ref=(context or {}).get("audit_ref"),
        args_json=payload,
        redacted=redacted,
        **_context_columns(context),
    )
    return _insert_with_retry(row)


def record_outcome(
    row_id: int,
    *,
    status: str,
    deny_reason: str | None = None,
    result_preview: str | None = None,
    error: str | None = None,
) -> None:
    """Best-effort phase 2: outcome update by PK; on failure the row honestly
    stays 'attempted' and we log ERROR."""
    try:
        with database.SessionLocal() as session:
            row = session.get(AgentActionAudit, row_id)
            if row is None:  # pragma: no cover — PK came from phase 1
                return
            row.status = status
            row.deny_reason = deny_reason
            row.result_preview = redact_text(result_preview)
            row.error = redact_text(error)
            row.completed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            session.commit()
    except SQLAlchemyError:
        logger.exception(
            "audit outcome update failed for row %s (status=%s) — row stays 'attempted'",
            row_id,
            status,
        )


def record_refusal(
    *,
    tool_name: str,
    tool_class: str,
    tool_call_id: str | None,
    context: dict[str, Any] | None,
) -> None:
    """Best-effort durable record of a fail-closed refusal (spec §5.2).

    Contention may have cleared by now (we already waited out the retry
    window); if not, count in memory so /api/audit/summary still surfaces it.
    """
    global _unpersisted_refusals
    row = AgentActionAudit(
        kind="execution",
        status="refused",
        deny_reason="audit_unavailable",
        tool_name=tool_name,
        tool_class=tool_class,
        tool_call_id=tool_call_id,
        args_json={},
        **_context_columns(context),
    )
    try:
        _insert_with_retry(row)
    except (AuditUnavailableError, SQLAlchemyError):
        with _refusal_lock:
            _unpersisted_refusals += 1
        logger.exception("audit refusal row could not be persisted (counted in memory)")


def unpersisted_refusals() -> int:
    with _refusal_lock:
        return _unpersisted_refusals


def _audit_block(entry: dict[str, Any]) -> dict[str, Any]:
    meta = entry.get("source_meta") or {}
    audit = meta.get("audit") or {}
    return audit if isinstance(audit, dict) else {}


def record_hitl_proposal(
    session: Any,
    *,
    proposal: dict[str, Any],
    tool_class: str,
    context: dict[str, Any] | None,
) -> None:
    """Insert a proposal row into the CALLER's session (atomic with the
    pending-action card persistence — spec §5.4). No commit here."""
    audit = _audit_block(proposal)
    tool_name = str(proposal.get("tool_name") or "")
    payload, redacted = redact_args(tool_name, proposal.get("payload"))
    session.add(
        AgentActionAudit(
            kind="hitl_proposal",
            status="proposed",
            tool_name=tool_name,
            tool_class=tool_class,
            tool_call_id=audit.get("tool_call_id"),
            audit_ref=audit.get("audit_ref"),
            args_json=payload,
            redacted=redacted,
            **_context_columns(context),
        )
    )


def record_hitl_decision(
    session: Any,
    *,
    action: dict[str, Any],
    decision: str,
    actor: str,
    context: dict[str, Any] | None = None,
    tool_class: str = "domain_write",
) -> None:
    """Insert an approved/rejected decision row into the caller's session.

    Join keys come from `context` when the call site has them, falling back to
    the action's own source_meta — decision rows must be reachable from
    thread-scoped audit views.
    """
    audit = _audit_block(action)
    meta = action.get("source_meta") or {}
    merged = {
        "thread_id": meta.get("thread_id"),
        "workflow_id": meta.get("workflow_id"),
        "session_id": meta.get("session_id"),
        "task_id": meta.get("task_id"),
        **{k: v for k, v in (context or {}).items() if v is not None},
    }
    ctx = _context_columns(merged)
    ctx["actor"] = actor
    session.add(
        AgentActionAudit(
            kind="hitl_decision",
            status=decision,
            tool_name=str(action.get("tool_name") or ""),
            tool_class=tool_class,
            tool_call_id=audit.get("tool_call_id"),
            audit_ref=audit.get("audit_ref"),
            args_json={},
            **ctx,
        )
    )
