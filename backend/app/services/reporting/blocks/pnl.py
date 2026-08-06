"""P&L block producers over Sub-project A's deterministic modules.

``pnl.daily`` reports the market-value move between two governed runs;
``pnl.explain`` decomposes it; ``pnl.inception`` reports since-trade P&L over
only the positions that carry a real cost basis.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import Position
from app.services.pnl import (
    UnsupportedMetricContract,
    diff_metrics,
    explain_diff,
    inception_pnl,
    load_run_pair,
)

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_NO_RUN = "no completed risk run exists for this portfolio"
_NO_PAIR = "no prior completed risk run to compare against"
_DEFAULT_TOP_N = 10


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _provenance(metrics: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "valuation_as_of": metrics.get("valuation_as_of"),
        "position_set_hash": metrics.get("position_set_hash"),
        **extra,
    }


def _load_metrics(ctx: BlockContext) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    with _session_scope() as session:
        _before, after = load_run_pair(
            portfolio_id=ctx.portfolio_id, risk_run_id=ctx.risk_run_id,
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


def _entry_prices(
    ctx: BlockContext, metrics: dict[str, Any]
) -> dict[int, float | None]:
    ids = [
        int(row["position_id"])
        for row in metrics.get("positions") or []
        if row.get("position_id") is not None
    ]
    if not ids:
        return {}
    with _session_scope() as session:
        rows = session.query(Position.id, Position.entry_price).filter(
            Position.id.in_(ids)
        ).all()
    return {int(pid): entry for pid, entry in rows}


@report_block(
    key="pnl.daily", title="Change since last run",
    shape=BlockShape.SCALARS_WITH_PRIOR, requires=("compare_to_run_id",),
    domain="pnl",
    description="Market-value move between the governed run and its predecessor.",
)
def pnl_daily(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)
    diff = diff_metrics(before, after)
    metrics = {
        key: diff["totals"][key]
        for key in ("market_value", "pnl", "gross_notional")
        if key in diff["totals"]
    }
    return BlockResult.ok(
        data={
            "metrics": metrics,
            "elapsed_days": diff["as_of"]["elapsed_days"],
            "composition_changed": diff["composition_changed"],
            "membership": diff["membership"],
            "coverage": diff["coverage"],
        },
        provenance=provenance,
    )


@report_block(
    key="pnl.explain", title="P&L attribution", shape=BlockShape.WATERFALL,
    requires=("compare_to_run_id",), domain="pnl",
    description="Decomposition of the market-value move into Greek buckets plus residual.",
)
def pnl_explain(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)
    try:
        explained = explain_diff(before, after, diff_metrics(before, after))
    except UnsupportedMetricContract as exc:
        return BlockResult.unavailable(
            f"attribution units cannot be resolved: {exc}", provenance=provenance
        )
    return BlockResult.ok(data=explained, provenance=provenance)


@report_block(
    key="pnl.by_position", title="Position movers", shape=BlockShape.ROWS,
    requires=("compare_to_run_id",), domain="pnl",
    description="Positions ranked by absolute market-value move since the prior run.",
)
def pnl_by_position(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)

    diff = diff_metrics(before, after)
    underlying_by_id = {
        int(row["position_id"]): row.get("underlying")
        for row in after.get("positions") or []
        if row.get("position_id") is not None
    }
    rows = [
        {
            "position_id": position_id,
            "underlying": underlying_by_id.get(position_id),
            "before": change["before"],
            "after": change["after"],
            "change": change["change"],
            "pct_change": change["pct_change"],
        }
        for position_id, change in diff["positions"].items()
    ]
    if not rows:
        return BlockResult.empty(
            "no positions were held across both runs", provenance=provenance
        )

    rows.sort(key=lambda row: abs(row["change"] or 0.0), reverse=True)
    top_n = int(ctx.params.get("top_n") or _DEFAULT_TOP_N)
    return BlockResult.ok(
        data={"rows": rows[:top_n], "truncated_to": top_n,
              "total_rows": len(rows)},
        provenance=provenance,
    )


@report_block(
    key="pnl.inception", title="P&L since trade", shape=BlockShape.SCALARS,
    domain="pnl",
    description="Since-inception P&L over only those positions that carry a real cost basis.",
)
def pnl_inception(ctx: BlockContext) -> BlockResult:
    metrics, provenance = _load_metrics(ctx)
    if metrics is None:
        return BlockResult.unavailable(_NO_RUN)
    result = inception_pnl(metrics, _entry_prices(ctx, metrics))
    if result["covered_count"] == 0:
        return BlockResult.empty(
            "no position in this book records a cost basis, so since-inception "
            "P&L cannot be computed",
            provenance=provenance,
        )
    return BlockResult.ok(
        data={
            "total": result["total"],
            "covered_count": result["covered_count"],
            "basis_missing_count": result["basis_missing_count"],
            "basis_missing": result["basis_missing"],
        },
        provenance=provenance,
    )


__all__ = ["pnl_daily", "pnl_explain", "pnl_by_position", "pnl_inception"]
