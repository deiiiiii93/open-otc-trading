"""Shared block contract types for the report module.

These types are the seam between deterministic producers (which fill a
``BlockResult``) and the template/render layer (which selects a renderer from
the declared ``BlockShape``). They live here rather than in ``services/pnl``
because the reporting registry, the template validator, and the renderer all
consume them; the P&L producers depend on them one-way.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

BlockStatus = Literal["ok", "empty", "unavailable"]


class BlockShape(str, Enum):
    """Declared output shape of a block, used to pick a compatible renderer.

    A template save validates ``render`` against this, so an incompatible
    pairing fails at save time rather than at render time.
    """

    SCALARS = "scalars"
    SCALARS_WITH_PRIOR = "scalars_with_prior"
    ROWS = "rows"
    SERIES = "series"
    WATERFALL = "waterfall"
    ITEMS = "items"
    POSITION_GREEKS = "position_greeks"


class BlockContext(BaseModel):
    """Everything a block producer is allowed to read as input."""

    portfolio_id: int
    risk_run_id: int | None = None
    compare_to_run_id: int | None = None
    params: dict[str, Any] = Field(default_factory=dict)


class BlockResult(BaseModel):
    """The single return type of every block producer.

    The tri-state ``status`` is load-bearing: ``empty`` means the producer ran
    and there is genuinely nothing (no breaches today), while ``unavailable``
    means the producer could not run at all (no prior run to compare against).
    A risk report must never render those the same way, and the narrating agent
    is told which one it got.
    """

    status: BlockStatus
    reason: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _reason_required_when_not_ok(self) -> "BlockResult":
        if self.status != "ok" and not (self.reason or "").strip():
            raise ValueError(f"status={self.status!r} requires a non-empty reason")
        return self

    @classmethod
    def ok(
        cls, *, data: dict[str, Any], provenance: dict[str, Any] | None = None
    ) -> "BlockResult":
        return cls(status="ok", data=data, provenance=provenance or {})

    @classmethod
    def empty(
        cls, reason: str, *, provenance: dict[str, Any] | None = None
    ) -> "BlockResult":
        return cls(status="empty", reason=reason, provenance=provenance or {})

    @classmethod
    def unavailable(
        cls, reason: str, *, provenance: dict[str, Any] | None = None
    ) -> "BlockResult":
        return cls(status="unavailable", reason=reason, provenance=provenance or {})


__all__ = ["BlockStatus", "BlockShape", "BlockContext", "BlockResult"]
