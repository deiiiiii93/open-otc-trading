"""Persistence and the cashflow state machine.

Every mutation is guarded by ``expected_row_version`` and writes an
append-only ``SettlementCashflowEvent``. Functions flush but never commit —
the caller owns the transaction boundary.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from ...models import SettlementCashflow, SettlementCashflowEvent, utcnow
from .contracts import EDITABLE_STATUSES
from .errors import (
    SettlementConflictError,
    SettlementNotFoundError,
    SettlementValidationError,
)

_UNSET: Any = object()

#: action -> the statuses it may be applied from.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "release": frozenset({"pending"}),
    "unrelease": frozenset({"released"}),
    # Reachable from `released` on purpose: pulling a payment back must never
    # be harder than releasing it.
    "block": frozenset({"needs_amount", "pending", "released"}),
    "unblock": frozenset({"blocked"}),
    "settle": frozenset({"released"}),
    "void": frozenset({"needs_amount", "pending", "blocked", "released"}),
}

_RESULT_STATUS: dict[str, str] = {
    "release": "released",
    "unrelease": "pending",
    "block": "blocked",
    "unblock": "pending",
    "settle": "settled",
    "void": "void",
}

_LOG_ACTION: dict[str, str] = {
    "release": "released",
    "unrelease": "unreleased",
    "block": "blocked",
    "unblock": "unblocked",
    "settle": "settled",
    "void": "voided",
}


def _jsonable(value: Any) -> Any:
    """Dates are not JSON-serializable; everything else passes through."""
    return value.isoformat() if isinstance(value, date) else value


def get_cashflow(session: Session, cashflow_id: int) -> SettlementCashflow:
    row = session.get(SettlementCashflow, cashflow_id)
    if row is None:
        raise SettlementNotFoundError(f"settlement cashflow {cashflow_id} not found")
    return row


def log_event(
    session: Session,
    *,
    cashflow: SettlementCashflow,
    action: str,
    from_status: str | None,
    to_status: str | None,
    actor: str,
    reason: str | None = None,
    payload: dict[str, Any] | None = None,
) -> SettlementCashflowEvent:
    row = SettlementCashflowEvent(
        cashflow_id=cashflow.id,
        action=action,
        from_status=from_status,
        to_status=to_status,
        actor=actor or "desk_user",
        reason=reason,
        payload=payload or {},
    )
    session.add(row)
    session.flush()
    return row


def _apply(
    session: Session,
    *,
    cashflow: SettlementCashflow,
    expected_row_version: int,
    values: dict[str, Any],
) -> SettlementCashflow:
    """Compare-and-swap on row_version. Raises on a stale expectation."""
    if (
        isinstance(expected_row_version, bool)
        or not isinstance(expected_row_version, int)
        or expected_row_version <= 0
    ):
        raise SettlementValidationError(
            "expected_row_version must be a positive integer"
        )
    result = session.execute(
        update(SettlementCashflow)
        .where(
            SettlementCashflow.id == cashflow.id,
            SettlementCashflow.row_version == expected_row_version,
        )
        .values(
            **values,
            row_version=SettlementCashflow.row_version + 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise SettlementConflictError(
            f"settlement cashflow {cashflow.id} row version is stale"
        )
    session.flush()
    session.refresh(cashflow)
    return cashflow


def transition(
    session: Session,
    *,
    cashflow_id: int,
    action: str,
    expected_row_version: int,
    actor: str,
    reason: str | None = None,
) -> SettlementCashflow:
    if action not in ALLOWED_TRANSITIONS:
        raise SettlementValidationError(f"unknown settlement action '{action}'")
    cashflow = get_cashflow(session, cashflow_id)
    from_status = cashflow.status
    if from_status not in ALLOWED_TRANSITIONS[action]:
        raise SettlementValidationError(
            f"cannot {action} a cashflow in status '{from_status}'"
        )
    to_status = _RESULT_STATUS[action]
    values: dict[str, Any] = {"status": to_status}
    if action == "block":
        values["block_reason"] = reason
    elif action == "unblock":
        values["block_reason"] = None
    _apply(
        session,
        cashflow=cashflow,
        expected_row_version=expected_row_version,
        values=values,
    )
    log_event(
        session,
        cashflow=cashflow,
        action=_LOG_ACTION[action],
        from_status=from_status,
        to_status=to_status,
        actor=actor,
        reason=reason,
    )
    return cashflow


def edit_cashflow(
    session: Session,
    *,
    cashflow_id: int,
    expected_row_version: int,
    actor: str,
    amount: float | None = _UNSET,
    value_date: date | None = _UNSET,
    counterparty: str | None = _UNSET,
    notes: str | None = _UNSET,
    reason: str | None = None,
) -> SettlementCashflow:
    cashflow = get_cashflow(session, cashflow_id)
    if cashflow.status not in EDITABLE_STATUSES:
        raise SettlementValidationError(
            f"cannot edit a cashflow in status '{cashflow.status}'"
        )

    values: dict[str, Any] = {}
    payload: dict[str, Any] = {}
    for field, supplied in (
        ("amount", amount),
        ("value_date", value_date),
        ("counterparty", counterparty),
        ("notes", notes),
    ):
        if supplied is _UNSET:
            continue
        current = getattr(cashflow, field)
        if current == supplied:
            continue
        values[field] = supplied
        payload[field] = {"from": _jsonable(current), "to": _jsonable(supplied)}

    if "amount" in values:
        if values["amount"] is None:
            raise SettlementValidationError(
                "amount cannot be cleared once supplied; void the cashflow instead"
            )
        if isinstance(values["amount"], bool) or not isinstance(
            values["amount"], (int, float)
        ):
            raise SettlementValidationError("amount must be a number")

    if not values:
        return cashflow

    from_status = cashflow.status
    to_status = from_status
    if from_status == "needs_amount" and values.get("amount") is not None:
        to_status = "pending"
        values["status"] = to_status

    _apply(
        session,
        cashflow=cashflow,
        expected_row_version=expected_row_version,
        values=values,
    )
    log_event(
        session,
        cashflow=cashflow,
        action="edited",
        from_status=from_status,
        to_status=to_status if to_status != from_status else None,
        actor=actor,
        reason=reason,
        payload=payload,
    )
    return cashflow
