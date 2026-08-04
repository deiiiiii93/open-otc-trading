import json

import pytest

from app.models import (
    ConfirmationBatch, ConfirmationDocument, ExtractedTrade, Portfolio, Position,
)
from app.services.confirmations import service as svc
from app.services.confirmations.extract import DocumentContent, PageContent


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
    assert second == {
        "ok": False, "error": "already_booked", "position_id": first_position_id,
    }
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
