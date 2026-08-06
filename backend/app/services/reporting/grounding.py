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
import re
from typing import Any

from app.golden_workflows.assertions import _scan_numeric_tokens

DEFAULT_REL_TOL = 0.01

# Block data carries timestamps as ISO STRINGS ("2026-06-23T09:00:00"), so their
# components are invisible to a walk that only collects numeric values. A
# narrator that writes "first seen 23 June 2026" was therefore flagged for
# quoting 23 and 2026 — a false positive on every report that mentions a date,
# which is the fastest way to train a reader to ignore the flag entirely.
# Only strings that START with a full ISO date are mined, so a hash or an
# arbitrary label cannot launder junk numbers into the grounded set.
_ISO_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_INT_RUN = re.compile(r"\d+")

# Identifier-ish strings the narrator may quote VERBATIM out of the data — a
# position-set hash, an artifact id. Tokenizing those shreds the hex into
# fabricated "numbers" (one live run flagged 17 fragments of a single sha256 the
# model had copied correctly). Text quoted verbatim from the data is grounded by
# construction, so it is blanked out before scanning rather than tokenized.
_MIN_QUOTABLE_LEN = 8

# Prose rounds: "0.67" for 0.6711, "17,335" for 17334.67. Relative tolerance
# alone cannot express that — 0.06 written for 0.0634 is 5.4% off and would be
# flagged, while being exactly what a desk reader wants to see. A token is
# therefore also grounded when it equals a data value rounded to ANY precision.
_ROUNDING_PLACES = range(0, 7)


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
        elif isinstance(node, str):
            if _ISO_DATE_PREFIX.match(node):
                found.extend(float(part) for part in _INT_RUN.findall(node))
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(data)
    return found


def collect_quotable_strings(data: Any) -> list[str]:
    """Identifier-like string values a narrator may quote verbatim."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if len(node) >= _MIN_QUOTABLE_LEN and any(c.isdigit() for c in node):
                found.append(node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(data)
    return found


def _blank_quoted_strings(narrative: str, quotables: list[str]) -> str:
    """Blank out data strings the narrative reproduces verbatim.

    Replaced with spaces rather than removed so every remaining token keeps its
    original offset.
    """
    out = narrative
    # Longest first: a hash must be blanked before any substring of it.
    for quoted in sorted(set(quotables), key=len, reverse=True):
        if quoted in out:
            out = out.replace(quoted, " " * len(quoted))
    return out


def _matches(token: float, values: list[float], rel_tol: float) -> bool:
    for value in values:
        tolerance = rel_tol * abs(value) if value != 0 else rel_tol
        if abs(token - value) <= tolerance:
            return True
        if any(round(value, places) == token for places in _ROUNDING_PLACES):
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
    scanned = _blank_quoted_strings(narrative, collect_quotable_strings(block_data))
    tokens = _scan_numeric_tokens(scanned)

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


__all__ = [
    "DEFAULT_REL_TOL",
    "collect_numeric_values",
    "collect_quotable_strings",
    "check_grounding",
]
