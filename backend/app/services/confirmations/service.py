"""Trade-confirmation pipeline: store -> parse -> validate -> review -> book.

The single write path for every surface (REST router, agent tools). All
booking goes through domains.booking.book_position; extraction output is never
bookable without prepare_booking_product_spec passing and a human action.
"""
from __future__ import annotations

import hashlib
import logging
import mimetypes
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import (
    ConfirmationBatch, ConfirmationDocument, ExtractedTrade, Portfolio, Position,
    TaskRun, TaskStatus,
)
from ..domains.booking import (
    BookingRequest, ProductBookingSpec, book_position, prepare_booking_product_spec,
)
from ..domains.products import product_family_for_quantark_class
from ..engine_configs import DEFAULT_ENGINE_BY_PRODUCT_TYPE
from ..instruments import resolve_bookable_underlying
from ..task_runner import submit_async_task
from .. import system_one
from .extract import extract_document
from .family_check import FamilyCheck, check_family
from .llm import (
    ExtractionError, TradeDraft, build_extractor_client, extract_trade,
    segment_document,
)

logger = logging.getLogger(__name__)

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
        family_check=draft.family_check,
    )


def synthesize_booking_terms(family: str, terms: dict, *, underlying: str | None = None,
                             currency: str | None = None) -> tuple[dict | None, list[str]]:
    """Turn schema-vocabulary extraction output into a canonical QuantArk termsheet.

    Extraction fills ``get_product_term_schema(family)`` — the same surface the agent's
    own ``build_product`` tool consumes — so its output is RAW builder input, not a
    finished termsheet. It must go through ``build_product`` (the repo's single
    producer) exactly as an agent would before booking.

    Skipping this step silently breaks booking, and the live smoke proved it: a vanilla
    confirmation legitimately yields ``{initial_price, exercise_date, strike,
    option_type}`` (three of those are REQUIRED by the published schema), but
    ``booking._RAW_TERMSHEET_VOCAB`` — the sniffer that decides synthesize-vs-verbatim —
    lists only ``maturity_years|maturity_date|expiry_date|expiry``. With no match it
    routes the terms to the validate-and-wrap path, where QuantArk rejects
    ``initial_price`` as an unsupported kwarg for ``EuropeanVanillaOption``.

    The fix belongs here, not in that tuple: adding ``exercise_date`` to it would flip
    ALREADY-BUILT vanillas (whose persisted kwargs carry ``exercise_date`` but no
    ``initial_price``) from the working verbatim path onto a synthesize path that then
    fails for the missing field — the same trap the tuple's comment documents for
    ``initial_price``. Building here keeps shared booking semantics untouched and still
    leaves the booking gate to re-validate the result.
    """
    from ..domains.product_builders import build_product

    built = build_product(
        family,
        dict(terms or {}),
        underlying=underlying,
        currency=currency,
    )
    if not built.ok:
        problems = [*(built.warnings or [])]
        if built.missing:
            problems.append(f"missing required terms: {', '.join(built.missing)}")
        return None, problems or [f"could not build a valid {family} termsheet"]
    return dict(built.product_kwargs), []


def _underlying_evidence_quote(trade: ExtractedTrade) -> str | None:
    """The verbatim source text the extractor cited for `underlying`.

    Confirmations name the instrument twice — "Shares: Apple Inc. (Ticker:
    AAPL)" — and the extractor often keeps the legal name. Feeding the quote
    to the resolver as a candidate hint turns a dead-end "no such instrument"
    into "did you mean AAPL?". It is used for SUGGESTIONS ONLY; the stored
    value is never rewritten from it.
    """
    evidence = trade.evidence or {}
    entry = evidence.get("underlying") if isinstance(evidence, dict) else None
    quote = entry.get("quote") if isinstance(entry, dict) else None
    return str(quote) if quote else None


def validate_trade_terms(session: Session, trade: ExtractedTrade) -> tuple[str, list[str]]:
    from app.tools.product_term_schema import _SCHEMA_FAMILIES

    if trade.family not in _SCHEMA_FAMILIES:
        return "unsupported", [f"unsupported product family {trade.family!r}"]
    booking_terms, problems = synthesize_booking_terms(
        trade.family, dict(trade.terms or {}),
        underlying=trade.underlying, currency=trade.currency,
    )
    if booking_terms is None:
        return "invalid", problems
    engine_name = DEFAULT_ENGINE_BY_PRODUCT_TYPE.get(trade.family)
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=product_family_for_quantark_class(trade.family),
        quantark_class=trade.family,
        underlying=trade.underlying or "UNKNOWN",
        currency=trade.currency,
        terms=booking_terms,
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
    # The instrument-master gate. A confirmation names the issuer in legal
    # form ("Apple Inc."), but positions/pricing/risk key off the instrument
    # SYMBOL, and book_position -> link_position_underlying -> ensure_underlying
    # would silently MINT a junk instrument for an unrecognised string rather
    # than refuse it. Booking must therefore be blocked here, at review time,
    # where a human or agent can still correct the value.
    resolution = resolve_bookable_underlying(
        session, trade.underlying, hint_text=_underlying_evidence_quote(trade))
    if not resolution.ok:
        return "invalid", [resolution.message or "underlying is not bookable"]
    return "valid", []


def _family_checker(requested: bool):
    """Per-segment System One cross-check (spec 2026-09-21 §3).

    Inert — every segment gets None, "never checked" — unless the caller allows
    it AND the master switch AND OPEN_OTC_CONFIRMATION_FAMILY_CHECK are on. A
    live check can never fail the document: any exception becomes
    unscored:internal_error with every Jev field null (logged, not stored).
    """
    settings = get_settings()
    if not (requested and system_one.is_enabled(settings)
            and settings.confirmation_family_check_enabled):
        return lambda content, segment, families: None

    def check(content, segment, families) -> dict:
        try:
            return check_family(content, segment, schema_families=families,
                                settings=settings).as_json()
        except Exception:  # noqa: BLE001 — a cross-check must never fail a document
            logger.warning("confirmation family cross-check failed", exc_info=True)
            return FamilyCheck.unscored("internal_error").as_json()

    return check


def parse_document(
    session: Session, document: ConfirmationDocument, *, client, family_check: bool = True
) -> None:
    from app.tools.product_term_schema import _SCHEMA_FAMILIES

    document.status = "parsing"
    session.flush()
    debug: dict = {}
    try:
        content = extract_document(Path(document.stored_path))
        document.page_count = content.page_count
        document.extract_mode = content.extract_mode
        segments = segment_document(content, client)
        # Segment -> draft -> row is 1:1, so the check rides on the draft; if
        # stage 2 raises, the document fails exactly as before and the checks
        # are dropped with its rows.
        checker = _family_checker(family_check)
        drafts = []
        for seg in segments:
            checked = checker(content, seg, _SCHEMA_FAMILIES)
            if seg.family not in _SCHEMA_FAMILIES:
                # Still checked: "LLM said unknown, Jev reads SnowballOption at
                # 0.9" is a visible disagree on an unsupported row.
                drafts.append(TradeDraft(family=seg.family, terms={}, family_check=checked))
                continue
            drafts.append(replace(extract_trade(content, seg, client), family_check=checked))
        for i, draft in enumerate(drafts, start=1):
            row = _draft_to_row(document.id, i, draft)
            session.add(row)
            session.flush()
            row.validation_status, row.validation_errors = validate_trade_terms(session, row)
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


def run_parse_batch(
    batch_id: int, task_id: int | None = None, *, client=None, family_check: bool = True
) -> None:
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
            parse_document(session, document, client=resolved_client,
                           family_check=family_check)
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
    trade.validation_status, trade.validation_errors = validate_trade_terms(session, trade)
    session.flush()
    return trade


def confirmation_source_trade_id(trade: ExtractedTrade, document: ConfirmationDocument) -> str:
    if trade.external_trade_id:
        return trade.external_trade_id
    return f"conf:{document.sha256[:12]}:{trade.seq}"


#: Terms worth showing on a booking card. Nested payloads (synthesized
#: observation schedules, coupon ladders) are dropped rather than truncated —
#: a card that half-renders a schedule is worse than one that omits it, and the
#: full termsheet is always on the position.
_CARD_TERM_LIMIT = 12


def _card_terms(terms: dict | None) -> dict:
    scalars = {
        key: value
        for key, value in (terms or {}).items()
        if isinstance(value, (str, int, float, bool)) and value is not None
    }
    return dict(list(scalars.items())[:_CARD_TERM_LIMIT])


def _booking_summary(
    trade: ExtractedTrade,
    *,
    status: str,
    position_id: int | None = None,
    portfolio_id: int | None = None,
    portfolio_name: str | None = None,
    terms: dict | None = None,
    error: str | None = None,
    detail: object = None,
) -> dict:
    """Desk-facing record of a booking attempt.

    Returned on BOTH the success and refusal paths: a refusal is a result the
    user needs to see, and without it a failed agent booking is invisible.
    Purely descriptive — it never re-derives anything, it reports what the
    write actually did.
    """
    document = trade.document
    summary: dict = {
        "status": status,
        "position_id": position_id,
        "trade_id": trade.id,
        "family": trade.family,
        "underlying": trade.underlying,
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "currency": trade.currency,
        "counterparty": trade.counterparty,
        "trade_date": trade.trade_date,
        "external_trade_id": trade.external_trade_id,
        "source_document": document.filename if document is not None else None,
        "terms": _card_terms(terms if terms is not None else trade.terms),
    }
    if portfolio_id is not None:
        summary["portfolio"] = {"id": portfolio_id, "name": portfolio_name}
    if error:
        summary["error"] = error
    if detail:
        summary["detail"] = detail
    return summary


def book_trade(
    session: Session, trade_id: int, *, portfolio_id: int | None = None,
    actor: str = "desk_user",
) -> dict:
    trade = session.get(ExtractedTrade, trade_id)
    if trade is None:
        return {"ok": False, "error": "not_found"}
    if trade.status == "booked":
        return {"ok": False, "error": "already_booked",
                "position_id": trade.booked_position_id,
                "booking": _booking_summary(
                    trade, status="already_booked",
                    position_id=trade.booked_position_id,
                    error="already_booked")}
    if trade.status != "extracted":
        return {"ok": False, "error": f"trade is {trade.status}",
                "booking": _booking_summary(
                    trade, status="failed", error=f"trade is {trade.status}")}
    trade.validation_status, trade.validation_errors = validate_trade_terms(session, trade)
    if trade.validation_status != "valid":
        return {"ok": False, "error": "validation_failed",
                "detail": trade.validation_errors,
                "booking": _booking_summary(
                    trade, status="failed", error="validation_failed",
                    detail=trade.validation_errors)}
    document = trade.document
    target_portfolio = portfolio_id or document.batch.default_portfolio_id
    if not target_portfolio:
        return {"ok": False, "error": "no_target_portfolio",
                "booking": _booking_summary(
                    trade, status="failed", error="no_target_portfolio")}
    source_trade_id = confirmation_source_trade_id(trade, document)
    existing = (
        session.query(Position)
        .filter(Position.portfolio_id == target_portfolio,
                Position.source_trade_id == source_trade_id)
        .one_or_none()
    )
    portfolio = session.get(Portfolio, target_portfolio)
    portfolio_name = portfolio.name if portfolio is not None else None
    if existing is not None:
        return {"ok": False, "error": "already_booked", "position_id": existing.id,
                "booking": _booking_summary(
                    trade, status="already_booked", position_id=existing.id,
                    portfolio_id=target_portfolio, portfolio_name=portfolio_name,
                    error="already_booked")}
    # Same synthesis validate_trade_terms just ran — booking must persist the CANONICAL
    # termsheet, not the raw schema-vocabulary extraction, or the two would disagree
    # about what "valid" meant.
    booking_terms, problems = synthesize_booking_terms(
        trade.family, dict(trade.terms or {}),
        underlying=trade.underlying, currency=trade.currency,
    )
    if booking_terms is None:
        return {"ok": False, "error": "validation_failed", "detail": problems,
                "booking": _booking_summary(
                    trade, status="failed", error="validation_failed",
                    detail=problems, portfolio_id=target_portfolio,
                    portfolio_name=portfolio_name)}
    spec = ProductBookingSpec(
        asset_class="equity",
        product_family=product_family_for_quantark_class(trade.family),
        quantark_class=trade.family,
        underlying=trade.underlying or "UNKNOWN",
        currency=trade.currency,
        terms=booking_terms,
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
        return {"ok": False, "error": "booking_failed", "detail": str(exc),
                "booking": _booking_summary(
                    trade, status="failed", error="booking_failed",
                    detail=str(exc), terms=booking_terms,
                    portfolio_id=target_portfolio, portfolio_name=portfolio_name)}
    trade.status = "booked"
    trade.booked_position_id = position.id
    session.flush()
    return {
        "ok": True,
        "position_id": position.id,
        "booking": _booking_summary(
            trade, status="booked", position_id=position.id, terms=booking_terms,
            portfolio_id=target_portfolio, portfolio_name=portfolio_name),
    }


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
