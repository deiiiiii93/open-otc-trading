# backend/app/tools/confirmations.py
"""Agent tools over the trade-confirmation pipeline (services/confirmations).

parse_trade_confirmation runs the parse synchronously inside the tool (agent
turns are long-running already); booking is HITL-gated and rides the same
service gate as the REST surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel

from .. import database
from ..config import get_settings
from ..models import ConfirmationBatch
from ..services.confirmations import service as confirmations
from ..services.confirmations.llm import build_extractor_client
from ..services.deep_agent.capability_gate import capability_gated
from ..services.deep_agent.envelopes import ToolGroup


def _batch_out(batch: ConfirmationBatch) -> dict[str, Any]:
    return {
        "batch_id": batch.id,
        "source": batch.source,
        "default_portfolio_id": batch.default_portfolio_id,
        "documents": [
            {
                "document_id": d.id, "filename": d.filename, "status": d.status,
                "extract_mode": d.extract_mode, "error": d.error,
                "trades": [
                    {
                        "trade_id": t.id, "seq": t.seq, "family": t.family,
                        "underlying": t.underlying, "quantity": t.quantity,
                        "entry_price": t.entry_price, "currency": t.currency,
                        "counterparty": t.counterparty,
                        "external_trade_id": t.external_trade_id,
                        "confidence": t.confidence, "terms": dict(t.terms or {}),
                        "validation_status": t.validation_status,
                        "validation_errors": list(t.validation_errors or []),
                        "status": t.status,
                        "booked_position_id": t.booked_position_id,
                    }
                    for t in d.trades
                ],
            }
            for d in batch.documents
        ],
    }


class ParseTradeConfirmationInput(BaseModel):
    paths: list[str]
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("parse_trade_confirmation", args_schema=ParseTradeConfirmationInput)
def parse_trade_confirmation(paths: list[str], portfolio_id: int | None = None) -> dict:
    """Parse uploaded trade confirmation files (PDF/DOCX, scans supported) into
    reviewable extracted trades. `paths` are the stored paths returned when the
    user attached files to this chat. Returns per-document status plus each
    extracted trade's terms, validation result, and trade_id. Booking is a
    separate confirmed step (book_extracted_trade)."""
    settings = get_settings()
    uploads_root = (settings.artifact_dir / "uploads").resolve()
    resolved: list[Path] = []
    for raw in paths:
        p = Path(raw).resolve()
        if not p.is_relative_to(uploads_root):
            return {"ok": False, "error": f"path outside uploads dir: {raw}"}
        if not p.exists():
            return {"ok": False, "error": f"file not found: {raw}"}
        resolved.append(p)
    if not resolved:
        return {"ok": False, "error": "no files given"}
    try:
        client = build_extractor_client()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    database.init_db()
    with database.SessionLocal() as session:
        stored = [
            confirmations.StoredFile(
                filename=p.name, stored_path=str(p),
                sha256=confirmations.sha256_of_file(p),
                byte_len=p.stat().st_size,
                mime="application/pdf" if p.suffix.lower() == ".pdf"
                else "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
            for p in resolved
        ]
        batch = confirmations.create_batch(
            session, files=stored, source="agent", portfolio_id=portfolio_id)
        session.commit()
        for document in batch.documents:
            confirmations.parse_document(session, document, client=client)
            session.commit()
        session.refresh(batch)
        return {"ok": True, **_batch_out(batch)}


class GetConfirmationBatchInput(BaseModel):
    batch_id: int


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_confirmation_batch", args_schema=GetConfirmationBatchInput)
def get_confirmation_batch(batch_id: int) -> dict:
    """Read a confirmation batch: per-document parse status and every extracted
    trade with its validation result and booking status."""
    database.init_db()
    with database.SessionLocal() as session:
        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            return {"ok": False, "error": f"batch {batch_id} not found"}
        return {"ok": True, **_batch_out(batch)}


class BookExtractedTradeInput(BaseModel):
    trade_id: int
    portfolio_id: int | None = None


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_extracted_trade", args_schema=BookExtractedTradeInput)
def book_extracted_trade(trade_id: int, portfolio_id: int | None = None) -> dict:
    """Book one reviewed extracted trade into a portfolio through the standard
    booking gate. Fails honestly on validation errors, missing target portfolio,
    or an already-booked source trade id. HITL — requires confirmation."""
    database.init_db()
    with database.SessionLocal() as session:
        result = confirmations.book_trade(
            session, trade_id, portfolio_id=portfolio_id, actor="desk_agent")
        if result.get("ok"):
            session.commit()
        else:
            session.rollback()
        return result
