"""Detecting divergence between a cashflow and its source lifecycle event.

The rule is absolute: drift detection **flags**, it never applies. A cashflow
may have been edited by a human or already released, and silently rewriting
either is exactly the failure this module exists to prevent. Adopting new
values is a separate, explicit ``resync``.

Flagging deliberately does NOT bump ``row_version``: it is not a user mutation,
and a UI holding a version must stay able to act on the row it is looking at.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    Position,
    PositionLifecycleEvent,
    SettlementCashflow,
    utcnow,
)
from .contracts import TERMINAL_STATUSES
from .derive import derive_cashflows
from .errors import SettlementValidationError
from .store import _apply, get_cashflow, log_event


@dataclass(frozen=True, slots=True)
class DriftResult:
    checked: int
    flagged: int
    cleared: int


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, date) else value


def _recompute(
    session: Session, cashflow: SettlementCashflow
) -> tuple[float | None, date | None, str] | None:
    """Re-derive this cashflow's leg from the current event. None if gone."""
    event = session.get(PositionLifecycleEvent, cashflow.lifecycle_event_id)
    if event is None:
        return None
    position = session.get(Position, cashflow.position_id)
    if position is None:
        return None
    for draft in derive_cashflows(position, event):
        if draft.leg_key == cashflow.leg_key:
            return draft.amount, draft.value_date, draft.basis
    return None


def _evaluate(session: Session, cashflow: SettlementCashflow) -> dict[str, Any] | None:
    """Return a stale_reason dict, or None when the row is in step."""
    event = session.get(PositionLifecycleEvent, cashflow.lifecycle_event_id)
    if event is None:
        return {"kind": "source_event_missing", "detail": "lifecycle event deleted"}
    if event.cancelled_at is not None:
        return {
            "kind": "source_event_cancelled",
            "detail": event.cancellation_reason or "lifecycle event cancelled",
        }

    recomputed = _recompute(session, cashflow)
    if recomputed is None:
        return {
            "kind": "leg_no_longer_derived",
            "detail": f"event no longer produces leg '{cashflow.leg_key}'",
        }

    amount, value_date, basis = recomputed
    if amount == cashflow.derived_amount and value_date == cashflow.derived_value_date:
        return None
    return {
        "kind": "derived_values_changed",
        "detail": basis,
        "old": {
            "amount": cashflow.derived_amount,
            "value_date": _iso(cashflow.derived_value_date),
        },
        "new": {"amount": amount, "value_date": _iso(value_date)},
    }


def refresh_drift(
    session: Session,
    *,
    portfolio_id: int | None = None,
    cashflow_ids: list[int] | None = None,
    actor: str = "system",
) -> DriftResult:
    query = select(SettlementCashflow).where(
        SettlementCashflow.status.not_in(TERMINAL_STATUSES)
    )
    if portfolio_id is not None:
        query = query.join(
            Position, Position.id == SettlementCashflow.position_id
        ).where(Position.portfolio_id == portfolio_id)
    if cashflow_ids is not None:
        query = query.where(SettlementCashflow.id.in_(cashflow_ids))

    checked = flagged = cleared = 0
    now = utcnow()
    for cashflow in session.execute(query).scalars():
        checked += 1
        reason = _evaluate(session, cashflow)
        was_stale = bool(cashflow.stale)

        cashflow.last_checked_at = now
        if reason is None:
            if was_stale:
                cashflow.stale = False
                cashflow.stale_reason = None
                cleared += 1
                log_event(
                    session,
                    cashflow=cashflow,
                    action="stale_cleared",
                    from_status=cashflow.status,
                    to_status=None,
                    actor=actor,
                )
            continue

        if not was_stale or cashflow.stale_reason != reason:
            cashflow.stale = True
            cashflow.stale_reason = reason
            flagged += 1
            log_event(
                session,
                cashflow=cashflow,
                action="flagged_stale",
                from_status=cashflow.status,
                to_status=None,
                actor=actor,
                reason=reason.get("kind"),
                payload=reason,
            )
    session.flush()
    return DriftResult(checked=checked, flagged=flagged, cleared=cleared)


def resync_cashflow(
    session: Session,
    *,
    cashflow_id: int,
    expected_row_version: int,
    actor: str,
) -> SettlementCashflow:
    """Adopt the newly derived values as the baseline.

    The effective ``amount`` follows only when it was never overridden — an
    explicit human number outranks a re-derivation. Note a row filled by a
    later event also reads as overridden (``amount != derived_amount``), which
    is correct: that number came from a real event, not from this row's own.
    """
    cashflow = get_cashflow(session, cashflow_id)
    if cashflow.status in TERMINAL_STATUSES:
        raise SettlementValidationError(
            f"cannot resync a cashflow in status '{cashflow.status}'"
        )
    recomputed = _recompute(session, cashflow)
    if recomputed is None:
        raise SettlementValidationError(
            "source event no longer derives this leg; void the cashflow instead"
        )

    amount, value_date, basis = recomputed
    was_overridden = cashflow.amount != cashflow.derived_amount
    values: dict[str, Any] = {
        "derived_amount": amount,
        "derived_value_date": value_date,
        "derived_basis": basis,
        "stale": False,
        "stale_reason": None,
        "last_checked_at": utcnow(),
    }
    if not was_overridden:
        values["amount"] = amount
        values["value_date"] = value_date
        if cashflow.status == "needs_amount" and amount is not None:
            values["status"] = "pending"

    from_status = cashflow.status
    _apply(
        session,
        cashflow=cashflow,
        expected_row_version=expected_row_version,
        values=values,
    )
    log_event(
        session,
        cashflow=cashflow,
        action="resynced",
        from_status=from_status,
        to_status=cashflow.status,
        actor=actor,
        payload={"adopted_amount": amount, "kept_override": was_overridden},
    )
    return cashflow


__all__ = ["DriftResult", "refresh_drift", "resync_cashflow"]
