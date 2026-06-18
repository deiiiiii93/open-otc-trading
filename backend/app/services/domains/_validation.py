"""Shared value-sanity policy for pricing-parameter writes.

Sign/zero only by design (2026-06-06 spec): refuse outright nonsense, keep
no opinion on ranges — near-zero vol and negative rates are legitimate.
Bounds are policy, so they live in ONE module (unlike the deliberately
per-module ``_session_scope`` plumbing).
"""
from __future__ import annotations

import math
from typing import Any


def invalid_param_reason(field: str, value: Any) -> str | None:
    """Return a refusal reason for a provided r/q/vol value, or None if sane.

    ``json.loads`` accepts ``NaN``/``Infinity``, so non-finite floats can
    really arrive from LLM tool args.
    """
    if not math.isfinite(value):
        return "not_finite"
    if field == "volatility" and value <= 0:
        return "must_be_positive"
    return None


__all__ = ["invalid_param_reason"]
