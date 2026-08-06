"""Runtime numeric grounding guard over agent-written report narrative.

Every numeric token in a section's prose must correspond to a number that
appears somewhere in that section's resolved block data. This reuses the arena
scorer's tokenizer (``_scan_numeric_tokens``), which was built to catch a model
quoting a value it never fetched — the same failure mode as an LLM writing a
risk report, so the same check applies.

Flags are informational by default: generation records them on the section
rather than failing, so a false positive degrades the report's confidence
signal instead of destroying the report.
"""
from __future__ import annotations

import math
from typing import Any

from app.golden_workflows.assertions import _scan_numeric_tokens

DEFAULT_REL_TOL = 0.01


def _is_real_number(value: Any) -> bool:
    # bool is a subclass of int; a True in a payload is not a reported figure.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value)


def collect_numeric_values(data: Any) -> list[float]:
    """Every finite number anywhere in a nested structure, in traversal order."""
    found: list[float] = []

    def walk(node: Any) -> None:
        if _is_real_number(node):
            found.append(float(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(data)
    return found


def _matches(token: float, values: list[float], rel_tol: float) -> bool:
    for value in values:
        tolerance = rel_tol * abs(value) if value != 0 else rel_tol
        if abs(token - value) <= tolerance:
            return True
    return False


def check_grounding(
    narrative: str,
    block_data: list[dict[str, Any]],
    *,
    rel_tol: float = DEFAULT_REL_TOL,
) -> dict[str, Any]:
    """Flag numeric tokens in ``narrative`` absent from ``block_data``.

    ``block_data`` is the list of resolved ``BlockResult.data`` payloads for the
    section. A token matching ANY value in ANY of them is grounded.
    """
    if not (narrative or "").strip():
        return {"checked": False, "flags": [], "grounded_count": 0}

    values = collect_numeric_values(block_data)
    tokens = _scan_numeric_tokens(narrative)

    # A `%` token yields both its face value and value/100, so a 0.34 ratio
    # written as "34%" matches. Group by offset and ground the token if EITHER
    # reading matches.
    by_offset: dict[int, list[float]] = {}
    for offset, token in tokens:
        by_offset.setdefault(offset, []).append(token)

    flags: list[dict[str, Any]] = []
    grounded = 0
    for offset in sorted(by_offset):
        readings = by_offset[offset]
        if any(_matches(reading, values, rel_tol) for reading in readings):
            grounded += 1
        else:
            flags.append({"token": readings[0], "offset": offset})

    return {"checked": True, "flags": flags, "grounded_count": grounded}


__all__ = ["DEFAULT_REL_TOL", "collect_numeric_values", "check_grounding"]
