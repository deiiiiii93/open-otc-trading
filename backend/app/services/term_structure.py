"""Pure term-structure tenor labels + linear interpolation.

Dependency-free by design: no DB, no imports from app.services.*. Labels are
entry/display only — every calculation runs on the year-fraction axis.
"""
from __future__ import annotations

import math
from typing import Any

# Standard tenor labels -> year-fractions. Extend here (single source) if the
# desk needs 1D / 4M / 7Y / 10Y; keep values strictly increasing per label
# ordering used by the UI axis.
TENOR_YEARS: dict[str, float] = {
    "1W": 7 / 365,
    "2W": 14 / 365,
    "1M": 1 / 12,
    "2M": 2 / 12,
    "3M": 3 / 12,
    "6M": 6 / 12,
    "9M": 9 / 12,
    "1Y": 1.0,
    "18M": 18 / 12,
    "2Y": 2.0,
    "3Y": 3.0,
    "5Y": 5.0,
}


def tenor_to_years(label: str) -> float:
    """Year-fraction for a tenor label; ValueError on unknown label."""
    key = str(label).strip()
    if key not in TENOR_YEARS:
        raise ValueError(f"unknown tenor label: {label!r}")
    return TENOR_YEARS[key]


def validate_curve(
    points: Any, *, require_positive: bool = False
) -> list[dict] | None:
    """Return a cleaned [{"tenor","value"}] list, or None for None.

    Enforces: known labels, no duplicate labels, finite float values, and
    (when require_positive) value > 0. Raises ValueError on any violation.
    """
    if points is None:
        return None
    if not isinstance(points, list):
        raise ValueError("curve must be a list of {tenor, value} points")
    seen: set[str] = set()
    cleaned: list[dict] = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("curve point must be an object with tenor and value")
        label = str(point.get("tenor", "")).strip()
        if label not in TENOR_YEARS:
            raise ValueError(f"unknown tenor label: {label!r}")
        if label in seen:
            raise ValueError(f"duplicate tenor label: {label!r}")
        seen.add(label)
        try:
            value = float(point.get("value"))
        except (TypeError, ValueError):
            raise ValueError(f"non-numeric value for tenor {label!r}") from None
        if not math.isfinite(value):
            raise ValueError(f"non-finite value for tenor {label!r}")
        if require_positive and value <= 0:
            raise ValueError(f"value for tenor {label!r} must be > 0")
        cleaned.append({"tenor": label, "value": value})
    return cleaned


def interpolate_curve(points: list[dict] | None, target_years: float) -> float | None:
    """Linear interpolation on the year axis with flat extrapolation.

    None / [] -> None (no curve). Single point -> constant. Below the first
    knot -> first value; above the last -> last value; else linear between the
    bracketing knots. Points are assumed to carry valid labels (write path
    validates via validate_curve).
    """
    if not points:
        return None
    knots = sorted(
        (TENOR_YEARS[str(p["tenor"]).strip()], float(p["value"])) for p in points
    )
    if len(knots) == 1:
        return knots[0][1]
    if target_years <= knots[0][0]:
        return knots[0][1]
    if target_years >= knots[-1][0]:
        return knots[-1][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x0 <= target_years <= x1:
            if x1 == x0:
                return y0
            weight = (target_years - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return knots[-1][1]  # unreachable; defensive
