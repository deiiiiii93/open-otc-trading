"""@tool wrappers over the Settlement module.

Thin: no logic is reimplemented here. Every read returns ``row_version``,
because every mutation requires it — optimistic concurrency is never bypassed
server-side. Typed domain failures come back as structured results the model
can act on, not as exceptions.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from .. import database
from ..models import Position, PositionLifecycleEvent, SettlementCashflow
from ..services.deep_agent.capability_gate import capability_gated
from ..services.deep_agent.envelopes import ToolGroup
from ..services.settlement import drift, generate
from ..services.settlement import notice as notice_service
from ..services.settlement import store
from ..services.settlement.errors import (
    SettlementConflictError,
    SettlementNotFoundError,
    SettlementValidationError,
)


def _row_out(session, row: SettlementCashflow) -> dict[str, Any]:
    position = session.get(Position, row.position_id)
    event = session.get(PositionLifecycleEvent, row.lifecycle_event_id)
    return {
        "cashflow_id": row.id,
        "position_id": row.position_id,
        "portfolio_id": position.portfolio_id if position else None,
        "underlying": position.underlying if position else None,
        "product_type": position.product_type if position else None,
        "event_type": event.event_type if event else None,
        "lifecycle_event_id": row.lifecycle_event_id,
        "leg_key": row.leg_key,
        "direction": row.direction,
        "amount": row.amount,
        "currency": row.currency,
        "value_date": row.value_date.isoformat() if row.value_date else None,
        "counterparty": row.counterparty,
        "status": row.status,
        "stale": bool(row.stale),
        "stale_reason": row.stale_reason,
        "derived_amount": row.derived_amount,
        "derived_basis": row.derived_basis,
        "row_version": row.row_version,
    }


def _mutate(operation, **kwargs) -> dict[str, Any]:
    """Run one store/drift mutation in its own transaction, mapping typed
    domain failures onto structured tool results."""
    database.init_db()
    with database.SessionLocal() as session:
        try:
            row = operation(session, **kwargs)
            session.commit()
        except SettlementConflictError as error:
            session.rollback()
            return {
                "ok": False,
                "error": "conflict",
                "hint": str(error),
                "next": "re-read the cashflow and retry with its current row_version",
            }
        except SettlementValidationError as error:
            session.rollback()
            return {"ok": False, "error": "invalid", "hint": str(error)}
        except SettlementNotFoundError as error:
            session.rollback()
            return {"ok": False, "error": "not_found", "hint": str(error)}
        session.refresh(row)
        return {"ok": True, **_row_out(session, row)}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


class GetSettlementCashflowsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None
    position_id: int | None = None
    status: str | None = None
    stale_only: bool = False
    limit: int = 50


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_settlement_cashflows", args_schema=GetSettlementCashflowsInput)
def get_settlement_cashflows_tool(
    portfolio_id: int | None = None,
    position_id: int | None = None,
    status: str | None = None,
    stale_only: bool = False,
    limit: int = 50,
) -> dict[str, Any]:
    """List settlement cashflows implied by position lifecycle events, with
    their status (needs_amount/pending/blocked/released/settled/void), amount,
    currency, value date and counterparty. Each row carries row_version —
    every mutation requires it."""
    database.init_db()
    with database.SessionLocal() as session:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        if position_id is not None:
            query = query.where(SettlementCashflow.position_id == position_id)
        if status is not None:
            query = query.where(SettlementCashflow.status == status)
        if stale_only:
            query = query.where(SettlementCashflow.stale.is_(True))
        rows = (
            session.execute(
                query.order_by(SettlementCashflow.id.desc()).limit(
                    max(1, min(limit, 200))
                )
            )
            .scalars()
            .all()
        )
        return {"cashflows": [_row_out(session, r) for r in rows], "count": len(rows)}


class SettlementCashflowIdInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cashflow_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_settlement_cashflow", args_schema=SettlementCashflowIdInput)
def get_settlement_cashflow_tool(cashflow_id: int) -> dict[str, Any]:
    """Fetch one settlement cashflow with its full transition history, any
    notices issued, and the current row_version required by every mutation."""
    database.init_db()
    with database.SessionLocal() as session:
        row = session.get(SettlementCashflow, cashflow_id)
        if row is None:
            return {
                "ok": False,
                "error": "not_found",
                "hint": f"no settlement cashflow {cashflow_id}",
            }
        payload = _row_out(session, row)
        payload["events"] = [
            {
                "action": e.action,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "actor": e.actor,
                "reason": e.reason,
                "at": e.created_at.isoformat(),
            }
            for e in row.events
        ]
        payload["notices"] = [
            {
                "version": n.version,
                "artifact_path": n.artifact_path,
                "status": n.status,
                "rendered_at": n.rendered_at.isoformat(),
            }
            for n in row.notices
        ]
        return payload


class SettlementSummaryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_settlement_summary", args_schema=SettlementSummaryInput)
def get_settlement_summary_tool(portfolio_id: int | None = None) -> dict[str, Any]:
    """Settlement position at a glance: cashflow counts by status, total amount
    per currency, and how many rows have drifted from their source lifecycle
    event."""
    database.init_db()
    with database.SessionLocal() as session:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        rows = session.execute(query).scalars().all()
        by_status: dict[str, int] = {}
        totals: dict[str, float] = {}
        stale = 0
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            if row.amount is not None:
                totals[row.currency] = totals.get(row.currency, 0.0) + float(row.amount)
            if row.stale:
                stale += 1
        return {
            "by_status": by_status,
            "totals_by_currency": totals,
            "stale_count": stale,
            "total": len(rows),
        }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


class GenerateSettlementCashflowsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portfolio_id: int | None = None
    refresh_drift: bool = True


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_settlement_cashflows", args_schema=GenerateSettlementCashflowsInput)
def generate_settlement_cashflows_tool(
    portfolio_id: int | None = None, refresh_drift: bool = True
) -> dict[str, Any]:
    """Backfill settlement cashflows for lifecycle events that have none, and
    optionally re-check every existing cashflow against its source event.
    INSERT-only and idempotent: it never rewrites an existing amount, so
    running it twice is safe. HITL — requires confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        created = generate.generate_missing(
            session, portfolio_id=portfolio_id, actor="agent"
        )
        drifted = (
            drift.refresh_drift(session, portfolio_id=portfolio_id, actor="agent")
            if refresh_drift
            else None
        )
        session.commit()
        return {
            "ok": True,
            "created": created.created,
            "skipped": created.skipped,
            "filled": created.filled,
            "checked": drifted.checked if drifted else 0,
            "flagged_stale": drifted.flagged if drifted else 0,
        }


class UpdateSettlementCashflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cashflow_id: int
    expected_row_version: int
    amount: float | None = None
    value_date: date | None = None
    counterparty: str | None = None
    notes: str | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("update_settlement_cashflow", args_schema=UpdateSettlementCashflowInput)
def update_settlement_cashflow_tool(
    cashflow_id: int,
    expected_row_version: int,
    amount: float | None = None,
    value_date: date | None = None,
    counterparty: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Edit a settlement cashflow's amount, value date, counterparty or notes.
    Legal only while needs_amount/pending/blocked — a released or settled row
    cannot be edited. Supplying an amount on a needs_amount row promotes it to
    pending. Requires expected_row_version from a preceding read.
    HITL — requires confirmation."""
    kwargs: dict[str, Any] = {}
    if amount is not None:
        kwargs["amount"] = amount
    if value_date is not None:
        kwargs["value_date"] = value_date
    if counterparty is not None:
        kwargs["counterparty"] = counterparty
    if notes is not None:
        kwargs["notes"] = notes
    return _mutate(
        store.edit_cashflow,
        cashflow_id=cashflow_id,
        expected_row_version=expected_row_version,
        actor="agent",
        **kwargs,
    )


class SettlementTransitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cashflow_id: int
    expected_row_version: int
    reason: str | None = None


def _transition(
    action: str, cashflow_id: int, expected_row_version: int, reason: str | None
) -> dict[str, Any]:
    return _mutate(
        store.transition,
        cashflow_id=cashflow_id,
        action=action,
        expected_row_version=expected_row_version,
        actor="agent",
        reason=reason,
    )


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("release_settlement_cashflow", args_schema=SettlementTransitionInput)
def release_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Clear a pending settlement cashflow for payment. Reversible via
    unrelease_settlement_cashflow while it has not yet been marked settled.
    Requires expected_row_version. HITL — requires confirmation."""
    return _transition("release", cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("unrelease_settlement_cashflow", args_schema=SettlementTransitionInput)
def unrelease_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Pull a released settlement cashflow back to pending before it settles.
    Requires expected_row_version. HITL — requires confirmation."""
    return _transition("unrelease", cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("block_settlement_cashflow", args_schema=SettlementTransitionInput)
def block_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Stop a settlement cashflow from being paid, recording why. Reachable
    even from released — pulling a payment back is always allowed. Requires
    expected_row_version. HITL — requires confirmation."""
    return _transition("block", cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("unblock_settlement_cashflow", args_schema=SettlementTransitionInput)
def unblock_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Lift a block, returning the cashflow to pending. Requires
    expected_row_version. HITL — requires confirmation."""
    return _transition("unblock", cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("void_settlement_cashflow", args_schema=SettlementTransitionInput)
def void_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Terminate a settlement cashflow that should never be paid — typically
    because its source lifecycle event was cancelled. Terminal. Requires
    expected_row_version. HITL — requires confirmation."""
    return _transition("void", cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("settle_settlement_cashflow", args_schema=SettlementTransitionInput)
def settle_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Record that a released settlement cashflow has actually been paid.
    TERMINAL and unrecallable — this asserts money moved. Requires
    expected_row_version. HITL — requires confirmation in every mode."""
    return _transition("settle", cashflow_id, expected_row_version, reason)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("resync_settlement_cashflow", args_schema=SettlementTransitionInput)
def resync_settlement_cashflow_tool(
    cashflow_id: int, expected_row_version: int, reason: str | None = None
) -> dict[str, Any]:
    """Adopt the freshly derived amount and value date from the source
    lifecycle event, clearing the stale flag. An explicitly overridden amount
    is preserved. Illegal on settled or void rows. Requires
    expected_row_version. HITL — requires confirmation."""
    return _mutate(
        drift.resync_cashflow,
        cashflow_id=cashflow_id,
        expected_row_version=expected_row_version,
        actor="agent",
    )


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("generate_settlement_notice", args_schema=SettlementCashflowIdInput)
def generate_settlement_notice_tool(cashflow_id: int) -> dict[str, Any]:
    """Render a settlement notice document for one cashflow as a Markdown
    artifact and record its sha256. Requires a counterparty and an amount.
    Regenerating supersedes the previous version. HITL — requires
    confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        try:
            record = notice_service.generate_notice(
                session, cashflow_id=cashflow_id, actor="agent"
            )
            session.commit()
        except SettlementValidationError as error:
            session.rollback()
            return {"ok": False, "error": "invalid", "hint": str(error)}
        except SettlementNotFoundError as error:
            session.rollback()
            return {"ok": False, "error": "not_found", "hint": str(error)}
        return {
            "ok": True,
            "notice_version": record.version,
            "artifact_path": record.artifact_path,
            "content_sha256": record.content_sha256,
        }


__all__ = [
    "block_settlement_cashflow_tool",
    "generate_settlement_cashflows_tool",
    "generate_settlement_notice_tool",
    "get_settlement_cashflow_tool",
    "get_settlement_cashflows_tool",
    "get_settlement_summary_tool",
    "release_settlement_cashflow_tool",
    "resync_settlement_cashflow_tool",
    "settle_settlement_cashflow_tool",
    "unblock_settlement_cashflow_tool",
    "unrelease_settlement_cashflow_tool",
    "update_settlement_cashflow_tool",
    "void_settlement_cashflow_tool",
]
