"""Trade-confirmation pipeline: store -> parse -> validate -> review -> book.

The single write path for every surface (REST router, agent tools). All
booking goes through domains.booking.book_position; extraction output is never
bookable without prepare_booking_product_spec passing and a human action.
"""
from __future__ import annotations

import hashlib
import mimetypes
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ...models import (
    ConfirmationBatch, ConfirmationDocument, ExtractedTrade, Position, TaskRun,
    TaskStatus,
)
from ..domains.booking import (
    BookingRequest, ProductBookingSpec, book_position, prepare_booking_product_spec,
)
from ..domains.products import product_family_for_quantark_class
from ..engine_configs import DEFAULT_ENGINE_BY_PRODUCT_TYPE
from ..task_runner import submit_async_task
from .extract import extract_document
from .llm import (
    ExtractionError, TradeDraft, build_extractor_client, extract_trade,
    segment_document,
)

# NOTE: app.tools.product_term_schema is imported lazily at each use site
# below (validate_trade_terms / parse_document), not at module scope — same
# app.tools <-> services.confirmations edge class fixed in llm.py (see the
# comment there). A module-scope import here of anything under app.tools
# would make app.tools' package init (which imports app.tools.confirmations,
# which needs this whole module fully defined) re-enter this still-executing
# module. Leave NO module-scope app.tools import anywhere in
# app/services/confirmations/.


@dataclass(frozen=True)
class StoredFile:
    filename: str
    stored_path: str
    sha256: str
    byte_len: int
    mime: str


def sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def store_confirmation_files(settings, files: list[tuple[str, bytes]]) -> list[StoredFile]:
    target_dir = settings.artifact_dir / "uploads" / "confirmations"
    target_dir.mkdir(parents=True, exist_ok=True)
    stored: list[StoredFile] = []
    for filename, data in files:
        name = Path(filename or "confirmation.pdf").name
        digest = hashlib.sha256(data).hexdigest()
        target = target_dir / f"{digest[:12]}-{name}"
        target.write_bytes(data)
        mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
        stored.append(StoredFile(
            filename=name, stored_path=str(target), sha256=digest,
            byte_len=len(data), mime=mime,
        ))
    return stored


def create_batch(
    session: Session, *, files: list[StoredFile], source: str,
    portfolio_id: int | None,
) -> ConfirmationBatch:
    batch = ConfirmationBatch(source=source, default_portfolio_id=portfolio_id)
    session.add(batch)
    session.flush()
    for f in files:
        session.add(ConfirmationDocument(
            batch_id=batch.id, filename=f.filename, stored_path=f.stored_path,
            sha256=f.sha256, byte_len=f.byte_len, mime=f.mime,
        ))
    session.flush()
    return batch


def _draft_to_row(document_id: int, seq: int, draft: TradeDraft) -> ExtractedTrade:
    return ExtractedTrade(
        document_id=document_id, seq=seq, family=draft.family,
        extracted_terms=dict(draft.terms), terms=dict(draft.terms),
        underlying=draft.underlying, quantity=draft.quantity,
        entry_price=draft.entry_price, currency=draft.currency,
        counterparty=draft.counterparty, trade_date=draft.trade_date,
        external_trade_id=draft.external_trade_id, confidence=draft.confidence,
        evidence=dict(draft.evidence), validation_errors=[],
    )


def validate_trade_terms(trade: ExtractedTrade) -> tuple[str, list[str]]:
    from app.tools.product_term_schema import _SCHEMA_FAMILIES

    if trade.family not in _SCHEMA_FAMILIES:
        return "unsupported", [f"unsupported product family {trade.family!r}"]
    engine_name = DEFAULT_ENGINE_BY_PRODUCT_TYPE.get(trade.family)
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=product_family_for_quantark_class(trade.family),
        quantark_class=trade.family,
        underlying=trade.underlying or "UNKNOWN",
        currency=trade.currency,
        terms=dict(trade.terms or {}),
        components=[],
    )
    try:
        prepare_booking_product_spec(spec, engine_name=engine_name)
    except Exception as exc:  # noqa: BLE001 — validation failures are data here
        return "invalid", [str(exc)]
    if not trade.underlying:
        return "invalid", ["missing underlying"]
    if not trade.quantity:
        return "invalid", ["missing quantity"]
    return "valid", []


def parse_document(session: Session, document: ConfirmationDocument, *, client) -> None:
    from app.tools.product_term_schema import _SCHEMA_FAMILIES

    document.status = "parsing"
    session.flush()
    debug: dict = {}
    try:
        content = extract_document(Path(document.stored_path))
        document.page_count = content.page_count
        document.extract_mode = content.extract_mode
        segments = segment_document(content, client)
        drafts = []
        for seg in segments:
            if seg.family not in _SCHEMA_FAMILIES:
                drafts.append(TradeDraft(family=seg.family, terms={}))
                continue
            drafts.append(extract_trade(content, seg, client))
        for i, draft in enumerate(drafts, start=1):
            row = _draft_to_row(document.id, i, draft)
            session.add(row)
            session.flush()
            row.validation_status, row.validation_errors = validate_trade_terms(row)
        document.status = "parsed"
        document.parsed_at = datetime.utcnow()
        document.model_provenance = getattr(client, "selection", None)
    except ExtractionError as exc:
        debug["raw_response"] = exc.raw_response
        document.status = "failed"
        document.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — per-document isolation is the contract
        document.status = "failed"
        document.error = str(exc)
    finally:
        if debug:
            document.extraction_debug = debug
        session.flush()


def run_parse_batch(batch_id: int, task_id: int | None = None, *, client=None) -> None:
    """Async-task entrypoint: own session per phase, per-document isolation."""
    from ... import database

    database.init_db()
    resolved_client = client or build_extractor_client()
    with database.SessionLocal() as session:
        batch = session.get(ConfirmationBatch, batch_id)
        if batch is None:
            return
        doc_ids = [d.id for d in batch.documents]
        task = session.get(TaskRun, task_id) if task_id else None
        if task is not None:
            task.status = TaskStatus.RUNNING.value
            session.commit()
        for doc_id in doc_ids:
            document = session.get(ConfirmationDocument, doc_id)
            parse_document(session, document, client=resolved_client)
            session.commit()
        if task is not None:
            statuses = {d.status for d in session.get(ConfirmationBatch, batch_id).documents}
            task.status = (
                TaskStatus.FAILED.value if statuses == {"failed"}
                else TaskStatus.COMPLETED.value
            )
            task.finished_at = datetime.utcnow()
            session.commit()


def queue_parse_task(session: Session, batch: ConfirmationBatch) -> TaskRun:
    task = TaskRun(kind="confirmation_parse", status=TaskStatus.QUEUED.value)
    session.add(task)
    session.flush()
    batch.task_id = task.id
    session.flush()
    return task


def dispatch_parse(batch_id: int, task_id: int) -> None:
    submit_async_task(run_parse_batch, batch_id, task_id)


_EDITABLE_FIELDS = {
    "family", "underlying", "quantity", "entry_price", "currency", "terms",
}


def update_trade(session: Session, trade_id: int, *, updates: dict) -> ExtractedTrade:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        raise ValueError(f"Extracted trade {trade_id} not found")
    if trade.status != "extracted":
        raise ValueError(f"Trade {trade_id} is {trade.status}; only extracted trades are editable")
    for key, value in updates.items():
        if key not in _EDITABLE_FIELDS:
            raise ValueError(f"Field {key!r} is not editable")
        setattr(trade, key, value)
    trade.validation_status, trade.validation_errors = validate_trade_terms(trade)
    session.flush()
    return trade


def confirmation_source_trade_id(trade: ExtractedTrade, document: ConfirmationDocument) -> str:
    if trade.external_trade_id:
        return trade.external_trade_id
    return f"conf:{document.sha256[:12]}:{trade.seq}"


def book_trade(
    session: Session, trade_id: int, *, portfolio_id: int | None = None,
    actor: str = "desk_user",
) -> dict:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        return {"ok": False, "error": "not_found"}
    if trade.status == "booked":
        return {"ok": False, "error": "already_booked",
                "position_id": trade.booked_position_id}
    if trade.status != "extracted":
        return {"ok": False, "error": f"trade is {trade.status}"}
    trade.validation_status, trade.validation_errors = validate_trade_terms(trade)
    if trade.validation_status != "valid":
        return {"ok": False, "error": "validation_failed",
                "detail": trade.validation_errors}
    document = trade.document
    target_portfolio = portfolio_id or document.batch.default_portfolio_id
    if not target_portfolio:
        return {"ok": False, "error": "no_target_portfolio"}
    source_trade_id = confirmation_source_trade_id(trade, document)
    existing = (
        session.query(Position)
        .filter(Position.portfolio_id == target_portfolio,
                Position.source_trade_id == source_trade_id)
        .one_or_none()
    )
    if existing is not None:
        return {"ok": False, "error": "already_booked", "position_id": existing.id}
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=product_family_for_quantark_class(trade.family),
        quantark_class=trade.family,
        underlying=trade.underlying or "UNKNOWN",
        currency=trade.currency,
        terms=dict(trade.terms or {}),
        components=[],
        source_payload={
            "confirmation_document": document.filename,
            "confirmation_sha256": document.sha256,
            "counterparty": trade.counterparty,
            "trade_date": trade.trade_date,
            "extracted_terms": trade.extracted_terms,
        },
    )
    try:
        position = book_position(session, BookingRequest(
            portfolio_id=target_portfolio,
            product=spec,
            quantity=float(trade.quantity or 0.0),
            entry_price=float(trade.entry_price or 0.0),
            source_trade_id=source_trade_id,
            engine_name=DEFAULT_ENGINE_BY_PRODUCT_TYPE.get(trade.family),
            source="confirmation",
            actor=actor,
            source_payload={
                "confirmation_batch_id": document.batch_id,
                "confirmation_document_id": document.id,
                "extracted_trade_id": trade.id,
                "counterparty": trade.counterparty,
            },
        ))
    except ValueError as exc:
        return {"ok": False, "error": "booking_failed", "detail": str(exc)}
    trade.status = "booked"
    trade.booked_position_id = position.id
    session.flush()
    return {"ok": True, "position_id": position.id}


def reject_trade(session: Session, trade_id: int, *, reason: str | None = None) -> ExtractedTrade:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        raise ValueError(f"Extracted trade {trade_id} not found")
    if trade.status == "booked":
        raise ValueError("booked trades cannot be rejected")
    trade.status = "rejected"
    trade.reject_reason = reason
    session.flush()
    return trade
