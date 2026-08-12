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
    "open": "open",
    "close": "closed",
    "settle": "closed",
    "reopen": "open",
    "knock_in": "knocked_in",
    "knock_out": "closed",
    "coupon_observation": None,
    "coupon_paid": None,
    "maturity": "closed",
    "autocall": "closed",
    "coupon_lock": None,
    "memory_coupon": None,
    "fixing": None,
    "custom": None,
}

PRODUCT_LIFECYCLE_EVENTS: dict[str, set[str]] = {
    "SnowballOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "coupon_observation",
        "coupon_paid",
        "maturity",
        "custom",
    },
    "PhoenixOption": {
        "close",
        "settle",
        "autocall",
        "coupon_lock",
        "coupon_paid",
        "memory_coupon",
        "maturity",
        "custom",
    },
    "BarrierOption": {"close", "settle", "knock_in", "knock_out", "maturity", "custom"},
    "SingleSharkfinOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "maturity",
        "custom",
    },
    "DoubleSharkfinOption": {
        "close",
        "settle",
        "knock_in",
        "knock_out",
        "maturity",
        "custom",
    },
    "AsianOption": {"close", "settle", "fixing", "custom"},
}


def valid_lifecycle_event_types(product_type: str) -> set[str]:
    """Return lifecycle events allowed for a product type."""
    return PRODUCT_LIFECYCLE_EVENTS.get(product_type, {"close", "settle", "custom"})


__all__ = [
    "LIFECYCLE_EVENT_TARGETS",
    "PRODUCT_LIFECYCLE_EVENTS",
    "valid_lifecycle_event_types",
]
