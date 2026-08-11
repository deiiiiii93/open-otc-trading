"""HTTP boundary for the Settlement module.

The services own validation, the state machine and persistence. This module
translates requests, maps typed domain errors onto status codes, and
serializes read models.

It **commits**. Do not model this on ``services/domains/risk.py``'s
``_session_scope``, which is read-only (it flushes and never commits): a write
path that copies it answers 200 while nothing persists.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Position, PositionLifecycleEvent, SettlementCashflow
from app.schemas import (
    SettlementActionIn,
    SettlementCashflowDetailOut,
    SettlementCashflowListOut,
    SettlementCashflowOut,
    SettlementCashflowPatchIn,
    SettlementGenerateOut,
    SettlementNoticeIn,
    SettlementNoticeOut,
    SettlementRefreshOut,
    SettlementSummaryOut,
    SettlementSweepIn,
)
from app.services.settlement import drift, generate
from app.services.settlement import notice as notice_service
from app.services.settlement import store
from app.services.settlement.errors import (
    SettlementConflictError,
    SettlementNotFoundError,
    SettlementValidationError,
)

#: Actions served by the generated transition routes.
TRANSITION_ACTIONS = ("release", "unrelease", "block", "unblock", "settle", "void")


def _raise(error: Exception) -> None:
    if isinstance(error, SettlementNotFoundError):
        raise HTTPException(status_code=404, detail=str(error))
    if isinstance(error, SettlementConflictError):
        raise HTTPException(status_code=409, detail=str(error))
    if isinstance(error, SettlementValidationError):
        raise HTTPException(status_code=422, detail=str(error))
    raise error


def _serialize(session: Session, row: SettlementCashflow) -> dict[str, Any]:
    position = session.get(Position, row.position_id)
    event = session.get(PositionLifecycleEvent, row.lifecycle_event_id)
    return {
        "id": row.id,
        "lifecycle_event_id": row.lifecycle_event_id,
        "leg_key": row.leg_key,
        "position_id": row.position_id,
        "portfolio_id": position.portfolio_id if position else None,
        "underlying": position.underlying if position else None,
        "product_type": position.product_type if position else None,
        "event_type": event.event_type if event else None,
        "currency": row.currency,
        "counterparty": row.counterparty,
        "direction": row.direction,
        "derived_amount": row.derived_amount,
        "derived_value_date": row.derived_value_date,
        "derived_basis": row.derived_basis,
        "amount": row.amount,
        "value_date": row.value_date,
        "status": row.status,
        "stale": bool(row.stale),
        "stale_reason": row.stale_reason,
        "last_checked_at": row.last_checked_at,
        "block_reason": row.block_reason,
        "notes": row.notes,
        "row_version": row.row_version,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def build_settlement_router(
    *, get_db: Callable[[], Generator[Session, None, None]]
) -> APIRouter:
    router = APIRouter(prefix="/api/settlement", tags=["settlement"])

    @router.get("/cashflows", response_model=SettlementCashflowListOut)
    def list_cashflows(
        portfolio_id: int | None = None,
        position_id: int | None = None,
        status: str | None = None,
        counterparty: str | None = None,
        stale: bool | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        db: Session = Depends(get_db),
    ) -> SettlementCashflowListOut:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        if position_id is not None:
            query = query.where(SettlementCashflow.position_id == position_id)
        if status is not None:
            query = query.where(SettlementCashflow.status == status)
        if counterparty is not None:
            query = query.where(SettlementCashflow.counterparty == counterparty)
        if stale is not None:
            query = query.where(SettlementCashflow.stale.is_(stale))

        total = db.execute(
            select(func.count()).select_from(query.subquery())
        ).scalar_one()
        rows = (
            db.execute(
                query.order_by(SettlementCashflow.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return SettlementCashflowListOut(
            items=[SettlementCashflowOut(**_serialize(db, r)) for r in rows],
            total=int(total),
            limit=limit,
            offset=offset,
        )

    @router.get("/summary", response_model=SettlementSummaryOut)
    def summary(
        portfolio_id: int | None = None, db: Session = Depends(get_db)
    ) -> SettlementSummaryOut:
        query = select(SettlementCashflow).join(
            Position, Position.id == SettlementCashflow.position_id
        )
        if portfolio_id is not None:
            query = query.where(Position.portfolio_id == portfolio_id)
        rows = db.execute(query).scalars().all()
        by_status: dict[str, int] = {}
        totals: dict[str, float] = {}
        stale_count = 0
        for row in rows:
            by_status[row.status] = by_status.get(row.status, 0) + 1
            if row.amount is not None:
                totals[row.currency] = totals.get(row.currency, 0.0) + float(row.amount)
            if row.stale:
                stale_count += 1
        return SettlementSummaryOut(
            by_status=by_status, totals_by_currency=totals, stale_count=stale_count
        )

    @router.post("/cashflows/generate", response_model=SettlementGenerateOut)
    def generate_cashflows(
        body: SettlementSweepIn, db: Session = Depends(get_db)
    ) -> SettlementGenerateOut:
        result = generate.generate_missing(
            db, portfolio_id=body.portfolio_id, actor="desk_user"
        )
        db.commit()
        return SettlementGenerateOut(
            created=result.created, skipped=result.skipped, filled=result.filled
        )

    @router.post("/cashflows/refresh", response_model=SettlementRefreshOut)
    def refresh_cashflows(
        body: SettlementSweepIn, db: Session = Depends(get_db)
    ) -> SettlementRefreshOut:
        result = drift.refresh_drift(
            db, portfolio_id=body.portfolio_id, actor="desk_user"
        )
        db.commit()
        return SettlementRefreshOut(
            checked=result.checked, flagged=result.flagged, cleared=result.cleared
        )

    @router.get("/cashflows/{cashflow_id}", response_model=SettlementCashflowDetailOut)
    def get_one(
        cashflow_id: int, db: Session = Depends(get_db)
    ) -> SettlementCashflowDetailOut:
        row = db.execute(
            select(SettlementCashflow)
            .where(SettlementCashflow.id == cashflow_id)
            .options(
                selectinload(SettlementCashflow.events),
                selectinload(SettlementCashflow.notices),
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=404, detail="cashflow not found")
        payload = _serialize(db, row)
        payload["events"] = [
            {
                "id": e.id,
                "action": e.action,
                "from_status": e.from_status,
                "to_status": e.to_status,
                "actor": e.actor,
                "reason": e.reason,
                "payload": e.payload or {},
                "created_at": e.created_at,
            }
            for e in row.events
        ]
        payload["notices"] = [
            {
                "id": n.id,
                "version": n.version,
                "artifact_path": n.artifact_path,
                "content_sha256": n.content_sha256,
                "status": n.status,
                "rendered_at": n.rendered_at,
                "rendered_by": n.rendered_by,
            }
            for n in row.notices
        ]
        return SettlementCashflowDetailOut(**payload)

    @router.patch("/cashflows/{cashflow_id}", response_model=SettlementCashflowOut)
    def patch_cashflow(
        cashflow_id: int,
        body: SettlementCashflowPatchIn,
        db: Session = Depends(get_db),
    ) -> SettlementCashflowOut:
        supplied = body.model_fields_set
        kwargs: dict[str, Any] = {
            field: getattr(body, field)
            for field in ("amount", "value_date", "counterparty", "notes")
            if field in supplied
        }
        try:
            row = store.edit_cashflow(
                db,
                cashflow_id=cashflow_id,
                expected_row_version=body.expected_row_version,
                actor="desk_user",
                **kwargs,
            )
            db.commit()
        except Exception as error:  # noqa: BLE001
            db.rollback()
            _raise(error)
        db.refresh(row)
        return SettlementCashflowOut(**_serialize(db, row))

    def _transition_route(action: str):
        def handler(
            cashflow_id: int,
            body: SettlementActionIn,
            db: Session = Depends(get_db),
        ) -> SettlementCashflowOut:
            try:
                row = store.transition(
                    db,
                    cashflow_id=cashflow_id,
                    action=action,
                    expected_row_version=body.expected_row_version,
                    actor="desk_user",
                    reason=body.reason,
                )
                db.commit()
            except Exception as error:  # noqa: BLE001
                db.rollback()
                _raise(error)
            db.refresh(row)
            return SettlementCashflowOut(**_serialize(db, row))

        return handler

    for action in TRANSITION_ACTIONS:
        router.add_api_route(
            f"/cashflows/{{cashflow_id}}/{action}",
            _transition_route(action),
            methods=["POST"],
            response_model=SettlementCashflowOut,
            name=f"settlement_{action}",
        )

    @router.post(
        "/cashflows/{cashflow_id}/resync", response_model=SettlementCashflowOut
    )
    def resync(
        cashflow_id: int,
        body: SettlementActionIn,
        db: Session = Depends(get_db),
    ) -> SettlementCashflowOut:
        try:
            row = drift.resync_cashflow(
                db,
                cashflow_id=cashflow_id,
                expected_row_version=body.expected_row_version,
                actor="desk_user",
            )
            db.commit()
        except Exception as error:  # noqa: BLE001
            db.rollback()
            _raise(error)
        db.refresh(row)
        return SettlementCashflowOut(**_serialize(db, row))

    @router.post("/cashflows/{cashflow_id}/notice", response_model=SettlementNoticeOut)
    def create_notice(
        cashflow_id: int,
        body: SettlementNoticeIn,
        db: Session = Depends(get_db),
    ) -> SettlementNoticeOut:
        try:
            record = notice_service.generate_notice(
                db, cashflow_id=cashflow_id, actor=body.actor
            )
            db.commit()
        except Exception as error:  # noqa: BLE001
            db.rollback()
            _raise(error)
        db.refresh(record)
        return SettlementNoticeOut(
            id=record.id,
            version=record.version,
            artifact_path=record.artifact_path,
            content_sha256=record.content_sha256,
            status=record.status,
            rendered_at=record.rendered_at,
            rendered_by=record.rendered_by,
        )

    return router


__all__ = ["TRANSITION_ACTIONS", "build_settlement_router"]
