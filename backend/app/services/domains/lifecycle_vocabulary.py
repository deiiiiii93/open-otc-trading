"""The position lifecycle event vocabulary.

Deliberately DB-free: this module is pure data plus two lookups, so the REST
layer, the settlement deriver and the tests can all read the same tables
without importing the position service.

Two layers:

* ``LIFECYCLE_EVENT_TARGETS`` — every legal event type and the position status
  it drives. ``None`` means the event records something without moving status.
* ``PRODUCT_LIFECYCLE_EVENTS`` — which of those a given product family may
  actually record.

``create_lifecycle_event`` is the ONLY constructor of ``PositionLifecycleEvent``
and it rejects anything outside the family's allowlist, so the allowlist is
TOTAL: a family missing from this map does not degrade gracefully, it loses the
event outright, and the only symptom is a ValueError when a desk user tries to
record something real. ``tests/test_lifecycle_vocabulary.py`` guards that.
"""
from __future__ import annotations

LIFECYCLE_EVENT_TARGETS: dict[str, str | None] = {
    # Inception and correction. `open` RECORDS inception and deliberately moves
    # no status: a position may be booked already knocked-in (a historical
    # trade imported mid-life) or closed, and an inception record must not
    # overwrite the status it was booked with. `reopen` is the event that
    # actually transitions a position back to open.
    "open": None,
    "reopen": "open",
    # Terminations. `maturity`, `exercise` and `expire` are all FINAL states;
    # the desk records the one that describes what actually happened.
    "close": "closed",
    "settle": "closed",
    "maturity": "closed",
    "exercise": "closed",
    "expire": "closed",
    "knock_out": "closed",
    # Alive-but-changed.
    "knock_in": "knocked_in",
    # Observations and records — these move no status.
    "coupon_observation": None,
    "coupon_paid": None,
    "memory_coupon": None,
    "barrier_reset": None,
    "fixing": None,
    "custom": None,
    # Retired (see RETIRED_EVENT_TYPES). Kept so historical rows still resolve.
    "autocall": "closed",
    "coupon_lock": None,
}

RETIRED_EVENT_TYPES: frozenset[str] = frozenset({"autocall", "coupon_lock"})
"""Types no family may record any more, kept so history still resolves.

A phoenix autocall IS a knock-out and a coupon-lock IS a coupon observation, so
phoenix and snowball now share one vocabulary. Deleting the old spellings would
damage history twice over: `_project_status_from_lifecycle` resolves targets
with `.get()`, so an unknown type reads as *non-transitioning* rather than
*unrecognised* and a historical autocall would stop closing its position; and
dropping `CASH_LEG_RULES["autocall"]` would stop `generate_missing` ever
sweeping those events for cash.
"""

BASE_EVENTS: frozenset[str] = frozenset(
    {"open", "reopen", "close", "settle", "maturity", "custom"}
)
"""Every family can be opened, corrected, closed, settled and matured."""

_FAMILY_EXTRA_EVENTS: dict[str, frozenset[str]] = {
    "EuropeanVanillaOption": frozenset({"exercise", "expire"}),
    "AmericanOption": frozenset({"exercise", "expire"}),
    "CashOrNothingDigitalOption": frozenset({"exercise", "expire"}),
    "BarrierOption": frozenset({"knock_in", "knock_out", "exercise", "expire"}),
    "SingleSharkfinOption": frozenset({"knock_in", "knock_out", "exercise", "expire"}),
    "DoubleSharkfinOption": frozenset({"knock_in", "knock_out", "exercise", "expire"}),
    # The touch terminates the option AND creates the obligation, so it is a
    # knock_out (terminating, carries the settlement leg), never a knock_in
    # (leaves the position alive, has no cash rule). A no-touch is `expire`.
    "OneTouchOption": frozenset({"knock_out", "expire"}),
    "DoubleOneTouchOption": frozenset({"knock_out", "expire"}),
    "AsianOption": frozenset({"fixing", "exercise", "expire"}),
    "SnowballOption": frozenset(
        {"knock_in", "knock_out", "coupon_observation", "coupon_paid"}
    ),
    # A snowball plus the stepping barrier that distinguishes it. NOTE:
    # `barrier_reset` RECORDS the step; it does not move the barrier the
    # position is priced against (that lives in Position.product_kwargs).
    "KnockOutResetSnowballOption": frozenset(
        {"knock_in", "knock_out", "coupon_observation", "coupon_paid", "barrier_reset"}
    ),
    "PhoenixOption": frozenset(
        {"knock_in", "knock_out", "coupon_observation", "coupon_paid", "memory_coupon"}
    ),
    # Assumes range accruals pay once at maturity; `fixing` records the
    # in/out-of-range observations. A periodic payer would also need
    # `coupon_paid`.
    "RangeAccrualOption": frozenset({"fixing"}),
    # Futures always settle something, so no `expire`: maturity + settle.
    "Futures": frozenset(),
    "SpotInstrument": frozenset(),
}

PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]] = {
    family: set(BASE_EVENTS | extra) for family, extra in _FAMILY_EXTRA_EVENTS.items()
}


def valid_lifecycle_event_types(product_type: str) -> set[str]:
    """Return lifecycle events allowed for a product type.

    Every bookable family has an explicit entry (guarded by
    ``test_every_bookable_family_has_an_explicit_event_map``); the fallback
    only ever applies to an unrecognised product type.
    """
    return PRODUCT_LIFECYCLE_EVENTS.get(product_type, set(BASE_EVENTS))


EVENT_FIELD_SPECS: dict[str, tuple[dict[str, str], ...]] = {
    "open": (
        {"key": "trade_date", "label": "Trade Date", "type": "date"},
        {"key": "premium_amount", "label": "Premium Amount", "type": "number"},
    ),
    "reopen": ({"key": "reason", "label": "Reason", "type": "text"},),
    "close": ({"key": "reason", "label": "Reason", "type": "text"},),
    "settle": (
        {"key": "settlement_amount", "label": "Settlement Amount", "type": "number"},
        {"key": "settlement_date", "label": "Settlement Date", "type": "date"},
    ),
    "maturity": (
        {"key": "maturity_date", "label": "Maturity Date", "type": "date"},
        {"key": "final_payoff", "label": "Final Payoff", "type": "number"},
    ),
    "exercise": (
        {"key": "exercise_date", "label": "Exercise Date", "type": "date"},
        {"key": "early", "label": "Early Exercise", "type": "bool"},
        {"key": "settlement_amount", "label": "Settlement Amount", "type": "number"},
    ),
    "expire": (
        {"key": "expiry_date", "label": "Expiry Date", "type": "date"},
        {"key": "reason", "label": "Reason", "type": "text"},
    ),
    "knock_in": (
        {"key": "barrier_level", "label": "Barrier Level", "type": "number"},
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
    ),
    "knock_out": (
        {"key": "barrier_level", "label": "Barrier Level", "type": "number"},
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "payoff", "label": "Payoff", "type": "number"},
    ),
    "barrier_reset": (
        {"key": "reset_date", "label": "Reset Date", "type": "date"},
        {"key": "new_barrier_level", "label": "New Barrier Level", "type": "number"},
        {"key": "previous_barrier_level", "label": "Previous Barrier", "type": "number"},
    ),
    "coupon_observation": (
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "observed_price", "label": "Observed Price", "type": "number"},
    ),
    "coupon_paid": (
        {"key": "coupon_amount", "label": "Coupon Amount", "type": "number"},
        {"key": "coupon_date", "label": "Coupon Date", "type": "date"},
    ),
    "memory_coupon": (
        {"key": "coupon_amount", "label": "Coupon Amount", "type": "number"},
        {"key": "memory_periods", "label": "Memory Periods", "type": "number"},
    ),
    "fixing": (
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "observed_price", "label": "Observed Price", "type": "number"},
    ),
    "custom": ({"key": "reason", "label": "Reason", "type": "text"},),
    # Retained for RETIRED types so historical rows still render with labels.
    # `by_family` is what gates the picker; this map is what renders. Keeping
    # the two concerns separate is what lets a retired type stay readable.
    "autocall": (
        {"key": "autocall_level", "label": "Autocall Level", "type": "number"},
        {"key": "observation_date", "label": "Observation Date", "type": "date"},
        {"key": "payoff", "label": "Payoff", "type": "number"},
    ),
    "coupon_lock": (
        {"key": "lock_date", "label": "Lock Date", "type": "date"},
        {"key": "locked_coupon_rate", "label": "Locked Coupon Rate", "type": "number"},
    ),
}


__all__ = [
    "BASE_EVENTS",
    "EVENT_FIELD_SPECS",
    "LIFECYCLE_EVENT_TARGETS",
    "PRODUCT_LIFECYCLE_EVENTS",
    "RETIRED_EVENT_TYPES",
    "valid_lifecycle_event_types",
]
