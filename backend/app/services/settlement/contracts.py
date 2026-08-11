from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, TypeAlias

Direction: TypeAlias = Literal["pay", "receive"]

CashflowStatus: TypeAlias = Literal[
    "needs_amount", "pending", "blocked", "released", "settled", "void"
]

STATUSES: frozenset[str] = frozenset(
    {"needs_amount", "pending", "blocked", "released", "settled", "void"}
)

TERMINAL_STATUSES: frozenset[str] = frozenset({"settled", "void"})

#: Statuses in which an edit to amount / value_date is legal.
EDITABLE_STATUSES: frozenset[str] = frozenset({"needs_amount", "pending", "blocked"})


@dataclass(frozen=True, slots=True)
class CashflowDraft:
    """What the deriver produces. Not persisted directly."""

    leg_key: str
    direction: Direction
    amount: float | None
    value_date: date | None
    basis: str
