import json

import pytest

from app.models import (
    ConfirmationBatch, ConfirmationDocument, ExtractedTrade, Portfolio, Position,
)
from app.services.confirmations import service as svc
from app.services.confirmations.extract import DocumentContent, PageContent


@pytest.fixture(autouse=True)
def _aapl_is_bookable(registered_underlying):
    """Every fixture below trades AAPL, and validation now requires the
    underlying to be an ACTIVE instrument tagged "underlying" — otherwise
    booking would mint a junk instrument for it."""
    registered_underlying("AAPL")


@pytest.fixture
def container_portfolio(session):
    p = Portfolio(name="Conf Test Book", kind="container")
    session.add(p)
    session.flush()
    return p


class FakeClient:
    """Stage-1 then stage-2 responses, one document."""
    def __init__(self, segment_json, trade_jsons):
        self.responses = [segment_json, *trade_jsons]
        self.selection = {"channel": "test", "provider": "t", "model": "fake"}

    def complete(self, content_parts):
        return self.responses.pop(0)


# initial_price (S0) is required by every product_builders family — see
# tests/test_product_builders.py's canonical minimal vanilla term set.
VANILLA_TERMS = {
    "option_type": "call", "strike": 150.0, "maturity_years": 1.0,
    "initial_price": 148.0,
}


def _seed_doc(session, tmp_path, text="BUY 100 AAPL call strike 150"):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    stored = tmp_path / "c.pdf"
    stored.write_bytes(b"%PDF-1.4 minimal")
    doc = ConfirmationDocument(
        batch_id=batch.id, filename="c.pdf", stored_path=str(stored),
        sha256="c" * 64, byte_len=16, mime="application/pdf",
    )
    session.add(doc)
    session.flush()
    return batch, doc


def _fake_for_vanilla():
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 100"}]})
    trade = json.dumps({
        "terms": VANILLA_TERMS, "underlying": "AAPL", "quantity": 100,
        "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
        "trade_date": "2026-08-01", "external_trade_id": "TC-1001",
        "confidence": 0.9,
        "evidence": {"strike": {"quote": "strike 150", "page": 1}},
    })
    return FakeClient(seg, [trade])


def test_parse_document_produces_valid_trade(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="BUY 100 AAPL call")],
            page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    session.flush()
    assert doc.status == "parsed"
    trade = doc.trades[0]
    assert trade.family == "EuropeanVanillaOption"
    assert trade.validation_status == "valid"
    assert trade.extracted_terms == trade.terms
    assert trade.evidence["strike"]["page"] == 1


def test_parse_failure_isolated_to_document(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: (_ for _ in ()).throw(ValueError("boom")),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    assert doc.status == "failed"
    assert "boom" in (doc.error or "")


def test_invalid_terms_marked_invalid_not_booked(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "a"}]})
    bad = json.dumps({"terms": {"strike": -5}, "underlying": "AAPL",
                      "quantity": 1, "confidence": 0.5, "evidence": {}})
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=FakeClient(seg, [bad]))
    trade = doc.trades[0]
    assert trade.validation_status == "invalid"
    assert trade.validation_errors


def test_unknown_family_unsupported(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    seg = json.dumps({"trades": [{"family": "unknown", "pages": [1], "anchor": "a"}]})
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=FakeClient(seg, []))
    assert doc.trades[0].validation_status == "unsupported"


def test_update_trade_revalidates(session, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    trade = doc.trades[0]
    updated = svc.update_trade(session, trade.id, updates={"terms": {**VANILLA_TERMS, "strike": 155.0}})
    assert updated.terms["strike"] == 155.0
    assert updated.validation_status == "valid"
    assert updated.extracted_terms["strike"] == 150.0  # immutable original


def test_book_trade_books_and_is_idempotent(session, tmp_path, monkeypatch, container_portfolio):
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    trade = doc.trades[0]
    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)
    assert result["ok"] is True
    position = session.get(Position, result["position_id"])
    assert position.source_trade_id == "TC-1001"
    assert trade.status == "booked"
    again = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)
    assert again["ok"] is False and again["error"] == "already_booked"


def test_book_trade_returns_a_renderable_booking_summary(
    session, tmp_path, monkeypatch, container_portfolio,
):
    """The tool result is the ONLY structured record of the booking that reaches
    the chat UI. `{"ok": true, "position_id": 27}` is too thin to render, so the
    service returns the desk-facing facts alongside it."""
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_vanilla())
    trade = doc.trades[0]

    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)

    booking = result["booking"]
    assert booking["status"] == "booked"
    assert booking["position_id"] == result["position_id"]
    assert booking["trade_id"] == trade.id
    assert booking["portfolio"] == {
        "id": container_portfolio.id, "name": container_portfolio.name,
    }
    assert booking["underlying"] == "AAPL"
    assert booking["family"] == "EuropeanVanillaOption"
    assert booking["quantity"] == 100
    assert booking["entry_price"] == 12.5
    assert booking["currency"] == "USD"
    assert booking["counterparty"] == "Big Bank"
    assert booking["external_trade_id"] == "TC-1001"
    assert booking["source_document"] == "c.pdf"
    # Terms are the CANONICAL booked termsheet, not the raw extraction, so the
    # card shows what was actually persisted.
    assert booking["terms"]["strike"] == 150.0
    # Nested schedules would blow up a compact card; only scalars survive.
    assert all(
        isinstance(v, (str, int, float, bool)) for v in booking["terms"].values()
    )


def test_book_trade_reports_a_failed_booking_as_a_result(
    session, tmp_path, monkeypatch, container_portfolio,
):
    """A refusal is a result too — the card must be able to say WHY nothing was
    booked, rather than the turn going silent."""
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_with_legal_name())
    trade = doc.trades[0]

    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)

    assert result["ok"] is False
    booking = result["booking"]
    assert booking["status"] == "failed"
    assert booking["error"] == "validation_failed"
    assert booking["position_id"] is None
    assert booking["trade_id"] == trade.id
    assert booking["underlying"] == "Apple Inc."
    assert booking["detail"]


def _fake_with_legal_name():
    """The real-world failure: the extractor keeps the issuer's LEGAL name
    because the confirmation writes "Shares: Apple Inc. (Ticker: AAPL)" and
    the schema only asked for "underlying"."""
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 100"}]})
    trade = json.dumps({
        "terms": VANILLA_TERMS, "underlying": "Apple Inc.", "quantity": 100,
        "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
        "trade_date": "2026-08-01", "external_trade_id": "TC-LEGAL-1",
        "confidence": 0.9,
        "evidence": {
            "underlying": {"quote": "Shares: Apple Inc. (Ticker: AAPL)", "page": 1},
        },
    })
    return FakeClient(seg, [trade])


def test_legal_name_underlying_is_invalid_and_suggests_the_ticker(
    session, tmp_path, monkeypatch,
):
    """A legal name is not an instrument. Left unchecked this books "fine" and
    then silently cannot be priced: book_position -> link_position_underlying
    -> ensure_underlying MINTS an instrument for any unrecognised string."""
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    _, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_with_legal_name())

    trade = doc.trades[0]
    assert trade.validation_status == "invalid"
    assert "did you mean AAPL?" in " ".join(trade.validation_errors)
    # The extractor's own output is preserved verbatim for audit.
    assert trade.underlying == "Apple Inc."


def test_book_trade_refuses_an_underlying_that_is_not_a_bookable_instrument(
    session, tmp_path, monkeypatch, container_portfolio,
):
    """book_trade re-validates rather than trusting the stored status, so the
    gate holds even if a row was persisted 'valid' by an older parse."""
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    _, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_with_legal_name())
    trade = doc.trades[0]
    trade.validation_status = "valid"  # pretend an older parse blessed it
    session.flush()

    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)

    assert result["ok"] is False
    assert trade.status != "booked"
    assert session.query(Position).count() == 0


def test_underlying_must_be_tagged_underlying_not_merely_present(
    session, tmp_path, monkeypatch, registered_underlying,
):
    """An instrument that exists for hedging only is not a valid trade
    underlying — the error must say so instead of "no such instrument"."""
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    registered_underlying("IF2612.CFFEX", kind="futures", tags=["hedge"])
    _, doc = _seed_doc(session, tmp_path)

    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY"}]})
    trade_json = json.dumps({
        "terms": VANILLA_TERMS, "underlying": "IF2612.CFFEX", "quantity": 100,
        "entry_price": 12.5, "currency": "CNY", "confidence": 0.9, "evidence": {},
    })
    svc.parse_document(session, doc, client=FakeClient(seg, [trade_json]))

    trade = doc.trades[0]
    assert trade.validation_status == "invalid"
    assert "missing 'underlying'" in " ".join(trade.validation_errors)


def test_book_trade_dedup_guard_blocks_different_trade_same_source_id(
    session, tmp_path, monkeypatch, container_portfolio,
):
    """Two DIFFERENT ExtractedTrade rows (e.g. a re-uploaded duplicate
    confirmation) that resolve to the same source_trade_id must dedup via the
    Position(portfolio_id, source_trade_id) query, not the trade.status=="booked"
    fast path — trade 2 is never itself booked, so that fast path never fires.
    """
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    _, doc1 = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc1, client=_fake_for_vanilla())
    trade1 = doc1.trades[0]

    dup_dir = tmp_path / "dup"
    dup_dir.mkdir()
    _, doc2 = _seed_doc(session, dup_dir)
    svc.parse_document(session, doc2, client=_fake_for_vanilla())
    trade2 = doc2.trades[0]

    assert trade1.id != trade2.id
    assert trade1.external_trade_id == trade2.external_trade_id == "TC-1001"

    first = svc.book_trade(session, trade1.id, portfolio_id=container_portfolio.id)
    assert first["ok"] is True
    first_position_id = first["position_id"]

    second = svc.book_trade(session, trade2.id, portfolio_id=container_portfolio.id)
    assert second["ok"] is False
    assert second["error"] == "already_booked"
    assert second["position_id"] == first_position_id
    # The dedup refusal points at the ALREADY-booked position, so the card
    # tells the user where the trade actually lives.
    assert second["booking"]["status"] == "already_booked"
    assert second["booking"]["position_id"] == first_position_id
    assert second["booking"]["trade_id"] == trade2.id
    assert trade2.status == "extracted"
    assert trade2.booked_position_id is None
    count = (
        session.query(Position)
        .filter(Position.portfolio_id == container_portfolio.id,
                Position.source_trade_id == "TC-1001")
        .count()
    )
    assert count == 1


def test_book_trade_fallback_source_trade_id_from_sha256(
    session, tmp_path, monkeypatch, container_portfolio,
):
    """No external_trade_id in the extraction -> source_trade_id falls back to
    conf:{document.sha256[:12]}:{trade.seq} (confirmation_source_trade_id)."""
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="x")], page_count=1, extract_mode="text"),
    )
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "a"}]})
    trade_json = json.dumps({
        "terms": VANILLA_TERMS, "underlying": "AAPL", "quantity": 100,
        "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
        "trade_date": "2026-08-01", "confidence": 0.9, "evidence": {},
    })
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=FakeClient(seg, [trade_json]))
    trade = doc.trades[0]
    assert trade.external_trade_id is None

    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)
    assert result["ok"] is True
    position = session.get(Position, result["position_id"])
    assert position.source_trade_id == f"conf:{doc.sha256[:12]}:{trade.seq}"


# The vocabulary a REAL extraction produces: get_product_term_schema declares
# initial_price / exercise_date / strike as REQUIRED for EuropeanVanillaOption, and the
# live smoke confirmed the model fills exactly those. Critically it supplies NO key from
# booking._RAW_TERMSHEET_VOCAB (maturity_years|maturity_date|expiry_date|expiry), which
# is what made the booking gate treat these raw terms as a finished QuantArk termsheet
# and reject initial_price. Regression pin for synthesize_booking_terms.
SCHEMA_VOCAB_VANILLA_TERMS = {
    "initial_price": 148.25,
    "exercise_date": "2027-08-03",
    "strike": 150.0,
    "option_type": "CALL",
}


def _fake_for_schema_vocab_vanilla():
    seg = json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 500"}]})
    trade = json.dumps({
        "terms": SCHEMA_VOCAB_VANILLA_TERMS, "underlying": "AAPL", "quantity": 500,
        "entry_price": 12.5, "currency": "USD", "counterparty": "Northwind Securities",
        "trade_date": "2026-08-03", "external_trade_id": "TCS-2026-0042",
        "confidence": 0.95,
        "evidence": {"strike": {"quote": "Strike: 150.00", "page": 1}},
    })
    return FakeClient(seg, [trade])


def test_schema_vocabulary_terms_validate_and_book(
    session, tmp_path, monkeypatch, container_portfolio,
):
    """Terms in the published schema's vocabulary must be bookable end to end.

    Before synthesize_booking_terms they scored `invalid` with
    "Unsupported kwargs for EuropeanVanillaOption: initial_price" — schema-legal
    extraction the desk could never book.
    """
    monkeypatch.setattr(
        svc, "extract_document",
        lambda path: DocumentContent(
            pages=[PageContent(index=1, text="conf")], page_count=1, extract_mode="text"),
    )
    batch, doc = _seed_doc(session, tmp_path)
    svc.parse_document(session, doc, client=_fake_for_schema_vocab_vanilla())
    trade = doc.trades[0]

    assert trade.validation_status == "valid", trade.validation_errors
    # The raw extraction is preserved verbatim for audit...
    assert trade.extracted_terms["initial_price"] == 148.25
    assert trade.extracted_terms["exercise_date"] == "2027-08-03"

    result = svc.book_trade(session, trade.id, portfolio_id=container_portfolio.id)
    assert result["ok"] is True, result

    # ...while the PERSISTED product carries the canonical built termsheet, which is
    # what QuantArk actually prices (initial_price is consumed by the builder, not a
    # constructor kwarg).
    position = session.get(Position, result["position_id"])
    assert position.product_type == "EuropeanVanillaOption"
    assert position.product_kwargs["strike"] == 150.0
    assert position.product_kwargs["exercise_date"] == "2027-08-03"
    assert "initial_price" not in position.product_kwargs


def test_synthesize_booking_terms_reports_missing_required_fields():
    booking_terms, problems = svc.synthesize_booking_terms(
        "EuropeanVanillaOption", {"strike": 150.0},
    )
    assert booking_terms is None
    assert problems
