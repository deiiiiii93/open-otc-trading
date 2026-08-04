"""REST surface over services/confirmations — upload, review, book."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..schemas import (
    BookTradeIn, ConfirmationBatchOut, ExtractedTradeOut, ExtractedTradeUpdateIn,
    RejectTradeIn,
)
from ..services.audit import record_audit
from ..services.confirmations import service as confirmations


def build_confirmations_router(*, get_db) -> APIRouter:
    router = APIRouter(prefix="/api/confirmations", tags=["confirmations"])

    @router.post("", response_model=ConfirmationBatchOut)
    def upload_confirmations(
        files: list[UploadFile] = File(...),
        portfolio_id: int | None = Form(None),
        session: Session = Depends(get_db),
    ):
        payload = [(f.filename or "confirmation.pdf", f.file.read()) for f in files]
        stored = confirmations.store_confirmation_files(get_settings(), payload)
        batch = confirmations.create_batch(
            session, files=stored, source="web", portfolio_id=portfolio_id)
        task = confirmations.queue_parse_task(session, batch)
        record_audit(
            session, event_type="confirmations.uploaded", actor="desk_user",
            subject_type="confirmation_batch", subject_id=batch.id,
            payload={"files": [s.filename for s in stored], "task_id": task.id},
        )
        session.commit()
        try:
            confirmations.dispatch_parse(batch.id, task.id)
        except Exception as exc:  # noqa: BLE001 — mirror limits router dispatch dance
            task.status = "failed"
            task.error = str(exc)
            session.commit()
            raise HTTPException(status_code=500, detail="parse dispatch failed") from exc
        session.refresh(batch)
        return batch

    @router.get("", response_model=list[ConfirmationBatchOut])
    def list_batches(session: Session = Depends(get_db)):
        from ..models import ConfirmationBatch

        return (
            session.query(ConfirmationBatch)
            .order_by(ConfirmationBatch.id.desc())
            .limit(100).all()
        )

    @router.get("/{batch_id}", response_model=ConfirmationBatchOut)
    def get_batch(batch_id: int, session: Session = Depends(get_db)):
        from ..models import ConfirmationBatch

        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail="batch not found")
        return batch

    @router.put("/trades/{trade_id}", response_model=ExtractedTradeOut)
    def update_trade(
        trade_id: int, payload: ExtractedTradeUpdateIn,
        session: Session = Depends(get_db),
    ):
        updates = {k: v for k, v in payload.model_dump().items() if v is not None}
        try:
            trade = confirmations.update_trade(session, trade_id, updates=updates)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        session.commit()
        return trade

    @router.post("/trades/{trade_id}/book")
    def book_trade(
        trade_id: int, payload: BookTradeIn, session: Session = Depends(get_db),
    ):
        result = confirmations.book_trade(
            session, trade_id, portfolio_id=payload.portfolio_id)
        if result.get("ok"):
            record_audit(
                session, event_type="confirmations.trade_booked", actor="desk_user",
                subject_type="extracted_trade", subject_id=trade_id,
                payload={"position_id": result["position_id"]},
            )
            session.commit()
        else:
            session.rollback()
        return result

    @router.post("/trades/{trade_id}/reject", response_model=ExtractedTradeOut)
    def reject_trade(
        trade_id: int, payload: RejectTradeIn, session: Session = Depends(get_db),
    ):
        try:
            trade = confirmations.reject_trade(session, trade_id, reason=payload.reason)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        record_audit(
            session, event_type="confirmations.trade_rejected", actor="desk_user",
            subject_type="extracted_trade", subject_id=trade_id,
            payload={"reason": payload.reason},
        )
        session.commit()
        return trade

    return router
