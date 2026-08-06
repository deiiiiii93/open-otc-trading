"""Desk-activity and governance block producers.

``audit.write_actions_summary`` is the governance block: the fail-closed audit
trail records every write-class action an agent took, and until now nothing in
the product reported on it. A board needs refused actions surfaced alongside
successful ones, so denials are counted separately rather than filtered out.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app import database
from app.models import RFQ, AgentActionAudit, Position, PositionBarrierState, RFQQuoteVersion
from app.services.pnl import diff_metrics, load_run_pair

from ..contracts import BlockContext, BlockResult, BlockShape
from ..registry import report_block

_LIVE_RFQ_STATUSES = ("draft", "requested", "priced", "quoted", "pending")
_DEFAULT_WINDOW_DAYS = 1
_NO_PAIR = "no prior completed risk run to compare membership against"


@contextmanager
def _session_scope(session: Session | None = None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def _load_open_rfqs(ctx: BlockContext) -> list[dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(RFQ)
            .filter(RFQ.status.in_(_LIVE_RFQ_STATUSES))
            .order_by(RFQ.created_at.desc())
            .all()
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            quote = row.quote_payload or {}
            out.append({
                "rfq_id": row.id,
                "client_name": row.client_name,
                "status": row.status,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "latest_price": quote.get("unit_price") or quote.get("achieved_price"),
            })
        return out


def _load_pending_approvals(ctx: BlockContext) -> list[dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(RFQQuoteVersion)
            .filter(RFQQuoteVersion.approved_at.is_(None))
            .filter(RFQQuoteVersion.status.in_(("priced", "pending_approval")))
            .order_by(RFQQuoteVersion.created_at.desc())
            .all()
        )
        return [
            {
                "rfq_id": row.rfq_id,
                "version": row.version,
                "created_by": row.created_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "valid_until": row.valid_until.isoformat() if row.valid_until else None,
            }
            for row in rows
        ]


def _load_barrier_states(ctx: BlockContext) -> list[dict[str, Any]]:
    with _session_scope() as session:
        rows = (
            session.query(PositionBarrierState, Position)
            .join(Position, Position.id == PositionBarrierState.position_id)
            .filter(Position.portfolio_id == ctx.portfolio_id)
            .all()
        )
        return [
            {
                "position_id": state.position_id,
                "underlying": position.underlying,
                "nearest_barrier_kind": state.nearest_barrier_kind,
                "nearest_barrier_level": state.nearest_barrier_level,
                "nearest_barrier_date": (
                    state.nearest_barrier_date.isoformat()
                    if state.nearest_barrier_date else None
                ),
                "days_to_nearest": state.days_to_nearest,
            }
            for state, position in rows
        ]


def _load_write_actions(
    ctx: BlockContext, window_days: int
) -> list[dict[str, Any]]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        days=window_days
    )
    with _session_scope() as session:
        rows = (
            session.query(AgentActionAudit)
            .filter(AgentActionAudit.occurred_at >= since)
            .order_by(AgentActionAudit.occurred_at.desc())
            .all()
        )
        return [
            {
                "tool_name": row.tool_name,
                "kind": row.kind,
                "status": row.status,
                "actor": row.actor,
                "persona": row.persona,
            }
            for row in rows
        ]


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
        return (
            before.metrics or {},
            after.metrics or {},
            {"risk_run_id": after.id, "compare_to_run_id": before.id},
        )


@report_block(
    key="rfq.open_pipeline", title="Open RFQ pipeline", shape=BlockShape.ROWS,
    domain="rfq", description="Client RFQs that are still live.",
)
def rfq_open_pipeline(ctx: BlockContext) -> BlockResult:
    rows = _load_open_rfqs(ctx)
    if not rows:
        return BlockResult.empty("no client RFQ is currently live")
    return BlockResult.ok(data={"rows": rows, "total_count": len(rows)})


@report_block(
    key="rfq.pending_approvals", title="Quotes awaiting approval",
    shape=BlockShape.ROWS, domain="rfq",
    description="Quote versions priced but not yet approved or released.",
)
def rfq_pending_approvals(ctx: BlockContext) -> BlockResult:
    rows = _load_pending_approvals(ctx)
    if not rows:
        return BlockResult.empty("no quote version is awaiting approval")
    return BlockResult.ok(data={"rows": rows, "total_count": len(rows)})


@report_block(
    key="positions.changes", title="Book changes", shape=BlockShape.ROWS,
    requires=("compare_to_run_id",), domain="positions",
    description="Positions added to or removed from the book between two governed runs.",
)
def positions_changes(ctx: BlockContext) -> BlockResult:
    before, after, provenance = _load_run_pair_metrics(ctx)
    if before is None or after is None:
        return BlockResult.unavailable(_NO_PAIR)

    diff = diff_metrics(before, after)
    before_rows = {
        int(row["position_id"]): row for row in before.get("positions") or []
        if row.get("position_id") is not None
    }
    after_rows = {
        int(row["position_id"]): row for row in after.get("positions") or []
        if row.get("position_id") is not None
    }

    added = [
        {"position_id": pid,
         "underlying": (after_rows.get(pid) or {}).get("underlying"),
         "market_value": (after_rows.get(pid) or {}).get("market_value")}
        for pid in diff["membership"]["added"]
    ]
    removed = [
        {"position_id": pid,
         "underlying": (before_rows.get(pid) or {}).get("underlying"),
         "market_value": (before_rows.get(pid) or {}).get("market_value")}
        for pid in diff["membership"]["removed"]
    ]

    if not added and not removed:
        return BlockResult.empty(
            "the book held the same positions across both runs",
            provenance=provenance,
        )

    return BlockResult.ok(
        data={"added": added, "removed": removed,
              "added_count": len(added), "removed_count": len(removed)},
        provenance=provenance,
    )


@report_block(
    key="positions.barrier_proximity", title="Barrier and KO watch",
    shape=BlockShape.ITEMS, domain="positions",
    description="Positions ranked by how close they sit to their nearest barrier.",
)
def positions_barrier_proximity(ctx: BlockContext) -> BlockResult:
    rows = _load_barrier_states(ctx)
    if not rows:
        return BlockResult.empty(
            "no position in this book carries a tracked barrier"
        )
    rows.sort(
        key=lambda row: (
            row["days_to_nearest"] if row.get("days_to_nearest") is not None else 10**9
        )
    )
    return BlockResult.ok(data={"items": rows, "total_count": len(rows)})


@report_block(
    key="audit.write_actions_summary", title="Agent actions taken",
    shape=BlockShape.ROWS, domain="audit",
    description="Write-class agent actions from the audit trail, aggregated by tool and outcome.",
)
def audit_write_actions_summary(ctx: BlockContext) -> BlockResult:
    window_days = int(ctx.params.get("window_days") or _DEFAULT_WINDOW_DAYS)
    actions = _load_write_actions(ctx, window_days)
    if not actions:
        return BlockResult.empty(
            f"no write-class agent action was recorded in the last {window_days} day(s)"
        )

    grouped: dict[tuple[Any, ...], dict[str, Any]] = {}
    denied = 0
    for action in actions:
        if action.get("status") == "denied":
            denied += 1
        key = (
            action.get("tool_name"), action.get("kind"), action.get("status"),
            action.get("actor"), action.get("persona"),
        )
        row = grouped.setdefault(
            key,
            {"tool_name": key[0], "kind": key[1], "status": key[2],
             "actor": key[3], "persona": key[4], "count": 0},
        )
        row["count"] += 1

    rows = sorted(grouped.values(), key=lambda row: row["count"], reverse=True)
    return BlockResult.ok(
        data={"rows": rows, "total_actions": len(actions),
              "denied_count": denied, "window_days": window_days}
    )


__all__ = [
    "rfq_open_pipeline",
    "rfq_pending_approvals",
    "positions_changes",
    "positions_barrier_proximity",
    "audit_write_actions_summary",
]
