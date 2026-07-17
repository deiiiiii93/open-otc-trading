from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, TypeAlias


LimitMode: TypeAlias = Literal["interactive", "auto", "yolo"]
LimitCategory: TypeAlias = Literal["greek", "var", "cvar", "stress"]
MetricKind: TypeAlias = Literal[
    "delta",
    "gamma",
    "vega",
    "theta",
    "rho",
    "rho_q",
    "var",
    "cvar",
    "stress_pnl",
]
SourceKind: TypeAlias = Literal["risk_run", "scenario_test", "backtest"]
ScopeType: TypeAlias = Literal[
    "portfolio",
    "underlying",
    "product_family",
    "position",
]
Aggregation: TypeAlias = Literal[
    "net",
    "gross_abs",
    "max_abs",
    "minimum",
    "maximum",
]
Transform: TypeAlias = Literal["signed", "absolute", "loss_magnitude"]
Comparator: TypeAlias = Literal["upper", "lower", "range"]


@dataclass(frozen=True, slots=True)
class LimitActionContext:
    """Trusted attribution assembled by REST, scheduler, or tool adapters."""

    actor: str
    persona: str | None
    mode: LimitMode
    thread_id: int | None = None
    audit_ref: str | None = None


@dataclass(frozen=True, slots=True)
class LimitVersionSpec:
    metric_kind: MetricKind
    source_kind: SourceKind
    methodology: dict = field(default_factory=dict)
    scope_type: ScopeType = "portfolio"
    scope_config: dict = field(default_factory=dict)
    aggregation: Aggregation = "net"
    transform: Transform = "signed"
    comparator: Comparator = "upper"
    warning_lower: float | None = None
    warning_upper: float | None = None
    hard_lower: float | None = None
    hard_upper: float | None = None
    unit: str = ""
    currency: str | None = None
    bump_convention: str | None = None
    freshness_policy: dict = field(default_factory=dict)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    rationale: str | None = None
