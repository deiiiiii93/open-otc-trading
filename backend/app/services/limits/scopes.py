"""The one definition of "this position is in that limit scope".

Two callers already matched scope membership independently — `monitoring._resolve_scopes`
over Position rows (it also mints `scope_key`) and `sources._risk_rows_for_scope` over
risk-run row dicts. The limit-review checkers need the same rule over Position rows at
check time, and a third copy would let a check disagree with the breach it is checking.
"""
from __future__ import annotations

from typing import Any

SCOPE_TYPES: tuple[str, ...] = ("portfolio", "underlying", "product_family", "position")


def _check_type(scope_type: str) -> None:
    if scope_type not in SCOPE_TYPES:
        raise ValueError(f"unsupported scope type {scope_type!r}")


def scope_key_for(scope_type: str, value: Any) -> str:
    """`<type>:<value>` — the key monitoring stores on evaluations and incidents."""
    _check_type(scope_type)
    return f"{scope_type}:{value}"


def scope_value(scope_key: str) -> str | None:
    """The value half of a scope key; None for a bare legacy key like `portfolio`."""
    _type, sep, value = str(scope_key).partition(":")
    return value if sep else None


def scope_matches(
    scope_type: str, value: Any, *, position_id: Any, underlying: Any, family: Any
) -> bool:
    """Does a row with these three attributes fall inside `scope_type:value`?

    Comparison is by `str` on both sides so an int id and its stored text agree.
    A missing attribute never matches (a row without an underlying is not "in"
    an underlying scope, whatever the value).
    """
    _check_type(scope_type)
    if scope_type == "portfolio":
        return True
    if scope_type == "position":
        return position_id is not None and value is not None and str(position_id) == str(value)
    if scope_type == "underlying":
        return underlying is not None and str(underlying) == str(value)
    return family is not None and str(family) == str(value)
