"""Turning lifecycle events into cashflow rows.

Two callers, one deriver:

* ``generate_for_event`` runs inline inside ``create_lifecycle_event``.
* ``generate_missing`` is the backfill sweep over events that have no rows.

Both are **INSERT-only**, with exactly one narrow and audited exception (see
``_fill_if_empty``). Neither ever rewrites a number that is already there, which
is what makes re-running safe and guarantees a human edit or a released row is
never silently changed.

Two dedup layers, because they catch different duplicates:

* ``UNIQUE(lifecycle_event_id, leg_key)`` stops the SAME event producing the
  same leg twice.
* ``SINGLETON_LEG_KEYS`` + ``_open_singleton`` stop DIFFERENT events producing
  the same once-per-position leg twice. The database cannot see that one: a
  ``knock_out`` and its follow-up ``settle`` are different rows in
  ``position_lifecycle_events``, so only the generator knows they describe one
  economic settlement.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import (
    ExtractedTrade,
    Position,
    PositionLifecycleEvent,
    RFQ,
    SettlementCashflow,
)
from .contracts import CashflowDraft, TERMINAL_STATUSES
from .derive import SINGLETON_LEG_KEYS, derive_cashflows
from .store import log_event


@dataclass(frozen=True, slots=True)
class GenerationResult:
    created: int
    skipped: int
    filled: int
    cashflow_ids: tuple[int, ...]


def resolve_counterparty(session: Session, position: Position) -> str | None:
    """Best-effort counterparty for a position.

    Positions carry no counterparty of their own, so this walks back to
    whichever origin recorded one: a booked confirmation first, then the RFQ
    the position was booked from.
    """
    trade_counterparty = session.execute(
        select(ExtractedTrade.counterparty)
        .where(ExtractedTrade.booked_position_id == position.id)
        .where(ExtractedTrade.counterparty.is_not(None))
        .limit(1)
    ).scalar_one_or_none()
    if trade_counterparty:
        return trade_counterparty

    if position.rfq_id is not None:
        client = session.execute(
            select(RFQ.client_name).where(RFQ.id == position.rfq_id)
        ).scalar_one_or_none()
        if client:
            return client
    return None


def _existing_leg_keys(session: Session, event_id: int) -> set[str]:
    return set(
        session.execute(
            select(SettlementCashflow.leg_key).where(
                SettlementCashflow.lifecycle_event_id == event_id
            )
        ).scalars()
    )


def _open_singleton(
    session: Session, position_id: int, leg_key: str
) -> SettlementCashflow | None:
    """The live once-per-position row for this leg, if any.

    Terminal rows (``settled`` / ``void``) are excluded on purpose: a position
    that settles, reopens and settles again legitimately earns a second
    cashflow, and a voided one must not block its replacement.
    """
    return session.execute(
        select(SettlementCashflow)
        .where(
            SettlementCashflow.position_id == position_id,
            SettlementCashflow.leg_key == leg_key,
            SettlementCashflow.status.not_in(TERMINAL_STATUSES),
        )
        .order_by(SettlementCashflow.id)
        .limit(1)
    ).scalar_one_or_none()


def live_settlement_row(
    session: Session, position_id: int
) -> SettlementCashflow | None:
    """The non-terminal ``settlement`` row that makes a ``reopen`` unsafe.

    A public wrapper over the singleton lookup so the lifecycle layer can ask
    the question without reaching into this module's internals. Callers use it
    to REFUSE, never to mutate: if a position reopens while its settlement is
    still live, the next ``settle`` is absorbed by ``_open_singleton`` and its
    amount is silently dropped — the lifecycle log would say one number and the
    blotter another.
    """
    return _open_singleton(session, position_id, "settlement")


def _fill_if_empty(
    session: Session,
    *,
    cashflow: SettlementCashflow,
    draft: CashflowDraft,
    source_event: PositionLifecycleEvent,
    actor: str,
) -> bool:
    """Supply an amount a terminating event could not provide.

    The single sanctioned exception to INSERT-only, deliberately narrow:

    * fires only when the row is still ``needs_amount`` with a NULL amount, so
      it can never overwrite a number, a human edit, or a released row;
    * leaves ``derived_*`` untouched. That snapshot describes the row's OWN
      event, which really did carry no amount — rewriting it would make drift
      detection compare the filled value against a re-derived ``None`` forever;
    * is idempotent: a second pass finds a non-null amount and does nothing.
    """
    if draft.amount is None:
        return False
    if cashflow.status != "needs_amount" or cashflow.amount is not None:
        return False

    cashflow.amount = draft.amount
    if cashflow.value_date is None:
        cashflow.value_date = draft.value_date
    cashflow.status = "pending"
    cashflow.row_version = int(cashflow.row_version or 1) + 1
    session.flush()

    log_event(
        session,
        cashflow=cashflow,
        action="filled_from_event",
        from_status="needs_amount",
        to_status="pending",
        actor=actor,
        reason=f"amount supplied by lifecycle event {source_event.event_type}",
        payload={
            "source_lifecycle_event_id": source_event.id,
            "source_event_type": source_event.event_type,
            "amount": draft.amount,
            "basis": draft.basis,
        },
    )
    return True


def generate_for_event(
    session: Session,
    *,
    event: PositionLifecycleEvent,
    actor: str = "system",
) -> GenerationResult:
    """Insert any cash legs this event implies that do not exist yet."""
    position = session.get(Position, event.position_id)
    if position is None:
        return GenerationResult(created=0, skipped=0, filled=0, cashflow_ids=())

    drafts = derive_cashflows(position, event)
    if not drafts:
        return GenerationResult(created=0, skipped=0, filled=0, cashflow_ids=())

    already = _existing_leg_keys(session, event.id)
    counterparty = resolve_counterparty(session, position)
    created_ids: list[int] = []
    skipped = 0
    filled = 0

    for draft in drafts:
        if draft.leg_key in already:
            skipped += 1
            continue

        if draft.leg_key in SINGLETON_LEG_KEYS:
            existing = _open_singleton(session, position.id, draft.leg_key)
            if existing is not None:
                if _fill_if_empty(
                    session,
                    cashflow=existing,
                    draft=draft,
                    source_event=event,
                    actor=actor,
                ):
                    filled += 1
                else:
                    skipped += 1
                continue

        status = "needs_amount" if draft.amount is None else "pending"
        row = SettlementCashflow(
            lifecycle_event_id=event.id,
            leg_key=draft.leg_key,
            position_id=position.id,
            currency=position.currency or "CNY",
            counterparty=counterparty,
            direction=draft.direction,
            derived_amount=draft.amount,
            derived_value_date=draft.value_date,
            derived_basis=draft.basis,
            amount=draft.amount,
            value_date=draft.value_date,
            status=status,
        )
        session.add(row)
        session.flush()
        log_event(
            session,
            cashflow=row,
            action="generated",
            from_status=None,
            to_status=status,
            actor=actor,
            payload={"basis": draft.basis, "leg_key": draft.leg_key},
        )
        created_ids.append(row.id)

    return GenerationResult(
        created=len(created_ids),
        skipped=skipped,
        filled=filled,
        cashflow_ids=tuple(created_ids),
    )


def generate_missing(
    session: Session,
    *,
    portfolio_id: int | None = None,
    since: datetime | None = None,
    actor: str = "system",
) -> GenerationResult:
    """Backfill sweep. Idempotent: re-running inserts nothing new.

    Events are walked in id order so a ``knock_out`` is always processed before
    the ``settle`` that fills it.
    """
    query = select(PositionLifecycleEvent).join(
        Position, Position.id == PositionLifecycleEvent.position_id
    )
    if portfolio_id is not None:
        query = query.where(Position.portfolio_id == portfolio_id)
    if since is not None:
        query = query.where(PositionLifecycleEvent.created_at >= since)
    query = query.order_by(PositionLifecycleEvent.id)

    created = 0
    skipped = 0
    filled = 0
    ids: list[int] = []
    for event in session.execute(query).scalars():
        result = generate_for_event(session, event=event, actor=actor)
        created += result.created
        skipped += result.skipped
        filled += result.filled
        ids.extend(result.cashflow_ids)
    return GenerationResult(
        created=created, skipped=skipped, filled=filled, cashflow_ids=tuple(ids)
    )


__all__ = [
    "GenerationResult",
    "generate_for_event",
    "generate_missing",
    "live_settlement_row",
    "resolve_counterparty",
]
