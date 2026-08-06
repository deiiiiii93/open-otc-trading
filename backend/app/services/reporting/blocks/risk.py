"""Risk and evidence-quality block producers.

All five read a persisted ``risk_runs.metrics`` payload. None of them price
anything: a report shows the governed run, it does not mint a new valuation.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.services.pnl import diff_metrics, load_run_pair

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_DIFF_KEYS = (
    "market_value", "delta_cash", "gamma_cash", "vega", "theta", "rho", "rho_q",
)

_NO_RUN = "no completed risk run exists for this portfolio"
_NO_PAIR = "no prior completed risk run to compare against"


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _provenance(metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    coverage = metrics.get("coverage") or {}
    return {
        "valuation_as_of": metrics.get("valuation_as_of"),
        "position_set_hash": metrics.get("position_set_hash"),
        "coverage": {
            "priced": coverage.get("coverage_count"),
            "total": coverage.get("total_count"),
        },
        **extra,
    }


def _load_metrics(ctx: BlockContext) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load the ``after`` run's metrics for this context.

    Patched wholesale in tests, which is why it is a module-level function
    rather than an inline query.
    """
    with _session_scope() as session:
        _before, after = load_run_pair(
            portfolio_id=ctx.portfolio_id,
            risk_run_id=ctx.risk_run_id,
            session=session,
        )
        if after is None:
            return None, {}
        metrics = after.metrics or {}
        return metrics, _provenance(metrics, risk_run_id=after.id)


def _load_run_pair_metrics(
    ctx: BlockContext,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any]]:
    with _session_scope() as session:
        before, after = load_run_pair(
            portfolio_id=ctx.portfolio_id,
            risk_run_id=ctx.risk_run_id,
            compare_to_run_id=ctx.compare_to_run_id,
            session=session,
        )
        if before is None or after is None:
            return None, None, {}
        metrics = after.metrics or {}
        return (
            before.metrics or {},
            metrics,
            _provenance(metrics, risk_run_id=after.id, compare_to_run_id=before.id),
        )


def _currency(metrics: dict[str, Any]) -> str:
    currencies = metrics.get("currencies") or []
    return currencies[0] if len(currencies) == 1 else "mixed"


@report_block(
    key="risk.totals", title="Portfolio totals", shape=BlockShape.SCALARS,
    domain="risk",
    description="Governed portfolio totals from the latest completed risk run.",
)
def risk_totals(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)
    totals = metrics.get("totals") or {}
    if not totals:
        return BlockResult.empty(
            "the risk run recorded no portfolio totals", provenance=provenance
        )
    return BlockResult.ok(
        data={"metrics": dict(totals), "currency": _currency(metrics)},
        provenance=provenance,
    )


@report_block(
    key="risk.exposure_by_underlying", title="Exposure by underlying",
    shape=BlockShape.SERIES, domain="risk",
    description="Delta cash, vega and market value aggregated per underlying.",
)
def risk_exposure_by_underlying(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)

    buckets: dict[str, dict[str, Any]] = {}
    for row in metrics.get("positions") or []:
        name = row.get("underlying") or "unknown"
        bucket = buckets.setdefault(
            name, {"underlying": name, "delta_cash": 0.0, "vega": 0.0,
                   "market_value": 0.0}
        )
        for key in ("delta_cash", "vega", "market_value"):
            try:
                bucket[key] += float(row.get(key) or 0.0)
            except (TypeError, ValueError):
                continue

    if not buckets:
        return BlockResult.empty(
            "the risk run priced no positions", provenance=provenance
        )

    series = sorted(
        buckets.values(), key=lambda row: abs(row["delta_cash"]), reverse=True
    )
    return BlockResult.ok(
        data={"chart_type": "bar", "x_key": "underlying", "y_key": "delta_cash",
              "series": series},
        provenance=provenance,
    )


@report_block(
    key="risk.greeks_by_bucket", title="Greeks by position",
    shape=BlockShape.POSITION_GREEKS, domain="risk",
    description="Per-position Greeks from the governed run.",
)
def risk_greeks_by_bucket(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)
    positions = list(metrics.get("positions") or [])
    if not positions:
        return BlockResult.empty(
            "the risk run priced no positions", provenance=provenance
        )
    return BlockResult.ok(data={"positions": positions}, provenance=provenance)


@report_block(
    key="risk.exposure_diff", title="Exposure change since last run",
    shape=BlockShape.SCALARS_WITH_PRIOR, requires=("compare_to_run_id",),
    domain="risk",
    description="Change in portfolio exposure between two governed risk runs.",
)
def risk_exposure_diff(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)
    diff = diff_metrics(before, after)
    metrics = {key: diff["totals"][key] for key in _DIFF_KEYS if key in diff["totals"]}
    return BlockResult.ok(
        data={
            "metrics": metrics,
            "composition_changed": diff["composition_changed"],
            "elapsed_days": diff["as_of"]["elapsed_days"],
        },
        provenance=provenance,
    )


@report_block(
    key="coverage.evidence", title="Evidence quality", shape=BlockShape.SCALARS,
    domain="risk",
    description="How much of the book actually priced, and how fresh its market evidence is.",
)
def coverage_evidence(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)

    coverage = metrics.get("coverage") or {}
    priced = coverage.get("coverage_count")
    total = coverage.get("total_count")
    ratio = None
    if isinstance(priced, int) and isinstance(total, int) and total > 0:
        ratio = priced / total

    manifest = (
        (metrics.get("source_metadata") or {}).get("market_evidence_manifest") or {}
    )
    ages = [
        int(row["quote_age_days"])
        for row in metrics.get("positions") or []
        if isinstance(row.get("quote_age_days"), (int, float))
    ]

    return BlockResult.ok(
        data={
            "priced": priced,
            "total": total,
            "ratio": ratio,
            "max_quote_age_days": max(ages) if ages else None,
            "evidence_complete": manifest.get("evidence_complete"),
            "missing_evidence": list(manifest.get("missing_evidence") or []),
            "position_set_hash": metrics.get("position_set_hash"),
        },
        provenance=provenance,
    )


__all__ = [
    "risk_totals",
    "risk_exposure_by_underlying",
    "risk_greeks_by_bucket",
    "risk_exposure_diff",
    "coverage_evidence",
]
