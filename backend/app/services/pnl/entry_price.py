"""Inception-P&L basis accounting.

``pnl = (price - entry_price) * quantity`` is already computed inside
QuantArk, but ``entry_price`` defaults to ``0.0``, which silently turns
inception P&L into market value. Rather than report that number, this module
excludes basis-less positions from the total and counts them, so a report can
say "inception P&L over 9 of 13 positions" instead of quoting a figure that
means nothing.
"""
from __future__ import annotations

from typing import Any, TypeGuard

# Price keys in preference order, kept deliberately identical to
# ``services/rfq.py::_quote_unit_price`` — that function is the BOOKING-time
# reader (it already fills ``entry_price`` on the RFQ path) and this is the
# REPORTING-time reader. If the two disagree, a position's basis and its
# reported basis diverge, so ``test_pnl_entry_price.py`` pins them equal.
_PRICE_KEYS: tuple[str, ...] = ("unit_price", "achieved_price", "price", "target_value")


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result else None


def _has_basis(value: float | None) -> TypeGuard[float]:
    """A basis of 0.0 is the schema default, not a real traded price.

    The RFQ booking path writes ``_quote_unit_price(quote) or 0.0``, so 0.0 is
    precisely the sentinel a quote without a price leaves behind.
    """
    return value is not None and value != 0.0


def inception_pnl(
    metrics: dict[str, Any], positions_entry: dict[int, float | None]
) -> dict[str, Any]:
    """Compute inception P&L over only those positions that have a real basis.

    ``positions_entry`` maps position_id -> entry_price (``None`` or ``0.0``
    meaning "no basis recorded").
    """
    per_position: dict[int, float] = {}
    basis_missing: list[int] = []

    for row in metrics.get("positions") or []:
        if not isinstance(row, dict) or row.get("position_id") is None:
            continue
        position_id = int(row["position_id"])
        basis = _float(positions_entry.get(position_id))
        if not _has_basis(basis):
            basis_missing.append(position_id)
            continue
        price = _float(row.get("price"))
        quantity = _float(row.get("quantity"))
        if price is None or quantity is None:
            basis_missing.append(position_id)
            continue
        per_position[position_id] = (price - basis) * quantity

    return {
        "total": sum(per_position.values()),
        "positions": per_position,
        "basis_missing": sorted(basis_missing),
        "basis_missing_count": len(basis_missing),
        "covered_count": len(per_position),
    }


def entry_price_from_quote(quote_payload: dict[str, Any]) -> float | None:
    """Read a traded unit price out of an ``rfq_quote_versions.quote_payload``.

    Mirrors the booking-time reader key-for-key; see ``_PRICE_KEYS``.
    """
    for key in _PRICE_KEYS:
        value = _float((quote_payload or {}).get(key))
        if value is not None:
            return value
    return None


__all__ = ["inception_pnl", "entry_price_from_quote", "_PRICE_KEYS"]
