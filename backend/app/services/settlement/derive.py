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
    present, finite number wins. If none match, the leg is still emitted
    with ``amount=None`` so the desk sees that cash is owed but unquantified.
    """

    leg_key: str
    direction: Direction
    amount_keys: tuple[str, ...]
    date_keys: tuple[str, ...] = ("settlement_date", "value_date", "payment_date")


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
            drafts.append(
                CashflowDraft(
                    leg_key=rule.leg_key,
                    direction=rule.direction,
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
# The 14 legal event types live in ``LIFECYCLE_EVENT_TARGETS``
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
CASH_LEG_RULES: dict[str, tuple[LegRule, ...]] = {
    "settle": (
        LegRule(
            leg_key="settlement",
            direction="pay",
            amount_keys=("settlement_amount",),
        ),
    ),
    # TODO(desk): add the remaining cash-generating event types.
    #   Decide for each of: open, close, knock_out, coupon_paid, maturity,
    #   autocall, memory_coupon, custom.
    #   Leave non-cash types out entirely.
}


__all__ = ["CASH_LEG_RULES", "CashflowDraft", "LegRule", "derive_cashflows"]
