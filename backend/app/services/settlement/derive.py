"""Pure, total derivation of cash legs from a position lifecycle event.

Contract, in order of importance:

1. **Pure.** No session, no I/O, no clock. Given the same position and event
   it returns the same drafts.
2. **Total.** It NEVER raises. Malformed ``event_data`` yields a draft with
   ``amount=None`` (persisted as ``needs_amount``) or no draft at all. This
   is what lets the inline generation hook be best-effort without risking
   the lifecycle event it is attached to.
3. **It does not compute payoffs.** It reads amounts the event already
   carries. Today only ``settle`` on a ``SnowballOption`` carries one
   (written by ``_enrich_snowball_ko_settlement``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ...models import Position, PositionLifecycleEvent
from .contracts import CashflowDraft, Direction


@dataclass(frozen=True, slots=True)
class LegRule:
    """One cash leg a lifecycle event type may produce.

    ``amount_keys`` are tried in order against ``event_data``; the first
    present, finite number wins. If none match and no ``position_resolver`` is
    declared, the leg is still emitted with ``amount=None`` so the desk sees
    that cash is owed but unquantified.

    ``direction`` may be ``None``, meaning the resolver decides it (a premium
    is paid on a long and received on a short).
    """

    leg_key: str
    direction: Direction | None
    amount_keys: tuple[str, ...] = ()
    date_keys: tuple[str, ...] = ("settlement_date", "value_date", "payment_date")
    position_resolver: str | None = None


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _first_amount(
    data: dict[str, Any], keys: tuple[str, ...]
) -> tuple[float | None, str]:
    for key in keys:
        amount = _finite_float(data.get(key))
        if amount is not None:
            return amount, f"event_data.{key}"
    return None, "none"


def _first_date(data: dict[str, Any], keys: tuple[str, ...]) -> date | None:
    for key in keys:
        parsed = _as_date(data.get(key))
        if parsed is not None:
            return parsed
    return None


def _flip(direction: Direction) -> Direction:
    return "receive" if direction == "pay" else "pay"


def _normalize(
    amount: float | None, direction: Direction
) -> tuple[float | None, Direction]:
    """Amounts are stored as non-negative magnitudes; ``direction`` carries the
    sign. A negative payable is a receivable — leaving it negative would let a
    loss-side settlement read as "pay 1,250" when the cash goes the other way.
    """
    if amount is None or amount >= 0:
        return amount, direction
    return abs(amount), _flip(direction)


def _premium_from_position(position: Position) -> tuple[float | None, Direction, str]:
    """Trade premium at inception: |entry_price x quantity|.

    This is a multiplication of two recorded trade fields, NOT a payoff model —
    the no-pricing rule is intact. Direction follows the sign of quantity: the
    desk pays premium on a long and receives it on a short.
    """
    quantity = _finite_float(position.quantity)
    price = _finite_float(position.entry_price)
    if quantity is None or price is None:
        return None, "pay", "none"
    amount = quantity * price
    if amount == 0:
        return None, "pay", "none"
    direction: Direction = "pay" if amount > 0 else "receive"
    return abs(amount), direction, "position.entry_price*quantity"


#: Named position-derived amount strategies, referenced by ``LegRule``.
_POSITION_RESOLVERS = {"premium": _premium_from_position}

#: Legs that can occur at most ONCE per position over its whole life.
#:
#: Consumed by ``generate.py`` for same-position dedup. Terminating events
#: (knock_out / autocall / maturity / close) and the later ``settle`` all emit
#: a ``settlement`` leg under DIFFERENT lifecycle event ids, so the
#: ``UNIQUE(lifecycle_event_id, leg_key)`` constraint cannot see the economic
#: duplicate — the generator must. ``coupon`` is deliberately ABSENT: coupons
#: recur, and deduping them would collapse a snowball's whole schedule into
#: one row.
SINGLETON_LEG_KEYS: frozenset[str] = frozenset({"settlement", "premium"})


def derive_cashflows(
    position: Position, event: PositionLifecycleEvent
) -> list[CashflowDraft]:
    """Return the cash legs implied by one lifecycle event. Never raises."""
    try:
        rules = CASH_LEG_RULES.get(event.event_type or "", ())
        if not rules:
            return []
        data = event.event_data if isinstance(event.event_data, dict) else None
        if data is None:
            return []
        drafts: list[CashflowDraft] = []
        for rule in rules:
            amount, basis = _first_amount(data, rule.amount_keys)
            direction: Direction = rule.direction or "pay"

            # event_data wins; a position resolver is the fallback, so an
            # explicitly recorded amount always overrides a computed one.
            if amount is None and rule.position_resolver is not None:
                resolver = _POSITION_RESOLVERS.get(rule.position_resolver)
                if resolver is not None:
                    amount, resolved_direction, basis = resolver(position)
                    if rule.direction is None:
                        direction = resolved_direction

            amount, direction = _normalize(amount, direction)
            drafts.append(
                CashflowDraft(
                    leg_key=rule.leg_key,
                    direction=direction,
                    amount=amount,
                    value_date=_first_date(data, rule.date_keys),
                    basis=basis,
                )
            )
        return drafts
    except Exception:  # noqa: BLE001 - totality is the contract
        return []


# ---------------------------------------------------------------------------
# The event -> cash leg mapping
# ---------------------------------------------------------------------------
#
# DOMAIN DECISION — owned by the desk, not by this module's author.
#
# The 17 legal event types live in ``LIFECYCLE_EVENT_TARGETS``
# (services/domains/positions.py). An event type absent from this map produces
# NO cashflow at all, which is the correct answer for observation-only events.
#
# Hazards this mapping must resolve:
#
#   * DOUBLE COUNT. A snowball fires ``knock_out`` (status -> closed) and THEN
#     ``settle`` (carrying the enriched ``settlement_amount``). If both emit a
#     leg with the same key the money is booked twice.
#     Pinned by test_knockout_and_settle_do_not_both_book_the_settlement.
#   * ``autocall`` and ``maturity`` both target status ``closed`` and may or
#     may not be followed by a separate ``settle``.
#   * ``open`` arguably implies a premium leg of entry_price x quantity — but
#     this deriver reads only ``event_data``, so such a leg comes back
#     ``needs_amount`` unless the amount is written into the event.
#   * ``coupon_observation`` is an observation; ``coupon_paid`` is cash.
#
# Keys the existing enrichment already writes into ``event_data``
# (``_enrich_snowball_ko_settlement``): settlement_amount, principal_amount,
# coupon_amount, settlement_date, ko_return_rate, ko_accrual_factor.
#
# Desk decisions (2026-08-10):
#
#   1. Every TERMINATING event emits a `settlement` leg immediately, usually
#      `needs_amount`, so a trade can never terminate silently with untracked
#      cash. The later `settle` — which is the only event the system enriches
#      with a number — FILLS that row rather than creating a second one.
#      Because those are different lifecycle events, the DB constraint cannot
#      dedupe them: `settlement` is in SINGLETON_LEG_KEYS and generate.py
#      enforces one-per-position.
#   2. `open` emits a `premium` leg computed from the position's own recorded
#      entry_price x quantity, so the cash lifecycle is covered from inception.
#
# Non-cash types (reopen, knock_in, coupon_observation, fixing, barrier_reset,
# expire, custom) are absent on purpose and therefore emit nothing. `expire`
# because nothing is owed — a settlement leg with no amount would sit
# `needs_amount` forever; `barrier_reset` because a stepping barrier is a
# change of terms, not cash.
#
# `autocall` and `coupon_lock` are RETIRED (see
# lifecycle_vocabulary.RETIRED_EVENT_TYPES): no family may record them any
# more, but `autocall` keeps its rule below so ``generate_missing`` can still
# sweep historical phoenix autocalls for cash.

_SETTLEMENT_LEG = LegRule(
    leg_key="settlement",
    direction="pay",
    amount_keys=("settlement_amount",),
)

_COUPON_LEG = LegRule(
    leg_key="coupon",
    direction="pay",
    amount_keys=("coupon_amount", "settlement_amount"),
)

CASH_LEG_RULES: dict[str, tuple[LegRule, ...]] = {
    # Inception.
    "open": (
        LegRule(leg_key="premium", direction=None, position_resolver="premium"),
    ),
    # Terminations — all emit the SAME singleton settlement leg.
    "settle": (_SETTLEMENT_LEG,),
    "knock_out": (_SETTLEMENT_LEG,),
    "autocall": (_SETTLEMENT_LEG,),
    "maturity": (_SETTLEMENT_LEG,),
    "close": (_SETTLEMENT_LEG,),
    "exercise": (_SETTLEMENT_LEG,),
    # Recurring coupons — deliberately NOT singleton.
    "coupon_paid": (_COUPON_LEG,),
    "memory_coupon": (_COUPON_LEG,),
}


__all__ = [
    "CASH_LEG_RULES",
    "SINGLETON_LEG_KEYS",
    "CashflowDraft",
    "LegRule",
    "derive_cashflows",
]
