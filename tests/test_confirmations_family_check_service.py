"""The cross-check rides on every segment and can never fail a document or
touch validation (spec §3)."""
from __future__ import annotations

import json

import pytest

from _system_one_fakes import ChoicePost
from app import database
from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade
from app.services.confirmations import service as svc
from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.system_one import client as s1client

VANILLA_TERMS = {"option_type": "call", "strike": 150.0, "maturity_years": 1.0,
                 "initial_price": 148.0}


@pytest.fixture(autouse=True)
def _aapl_is_bookable(registered_underlying):
    registered_underlying("AAPL")


@pytest.fixture(autouse=True)
def _text_document(monkeypatch):
    monkeypatch.setattr(svc, "extract_document", lambda path: DocumentContent(
        pages=[PageContent(index=1, text="BUY 100 AAPL call strike 150")],
        page_count=1, extract_mode="text"))


@pytest.fixture
def s1_on(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def jev(monkeypatch, choice, confidence=0.9):
    post = ChoicePost(choice, confidence)
    monkeypatch.setattr(s1client, "_default_post", post)
    return post


class FakeClient:
    def __init__(self, family):
        seg = json.dumps({"trades": [{"family": family, "pages": [1], "anchor": "BUY 100"}]})
        trade = json.dumps({
            "terms": VANILLA_TERMS, "underlying": "AAPL", "quantity": 100,
            "entry_price": 12.5, "currency": "USD", "counterparty": "Big Bank",
            "trade_date": "2026-08-01", "external_trade_id": "TC-1", "confidence": 0.9,
            "evidence": {}})
        self.responses = [seg, trade]
        self.selection = {"channel": "test", "provider": "t", "model": "fake"}

    def complete(self, content_parts):
        return self.responses.pop(0)


def parse(session, tmp_path, family="EuropeanVanillaOption", **kw):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    stored = tmp_path / f"c-{batch.id}.pdf"
    stored.write_bytes(b"%PDF-1.4")
    doc = ConfirmationDocument(batch_id=batch.id, filename=stored.name,
                               stored_path=str(stored), sha256="c" * 64, byte_len=8,
                               mime="application/pdf")
    session.add(doc)
    session.flush()
    svc.parse_document(session, doc, client=FakeClient(family), **kw)
    return doc, session.query(ExtractedTrade).filter_by(document_id=doc.id).all()


def test_agreement_is_recorded(s1_on, session, tmp_path, monkeypatch):
    jev(monkeypatch, "EuropeanVanillaOption")
    doc, [trade] = parse(session, tmp_path)
    assert doc.status == "parsed"
    assert trade.family_check["status"] == "agree"
    assert trade.validation_status == "valid"


def test_a_disagreement_changes_nothing_about_validation(s1_on, session, tmp_path, monkeypatch):
    jev(monkeypatch, "SnowballOption", 0.9)
    _doc, [flagged] = parse(session, tmp_path)
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "false")
    _doc, [plain] = parse(session, tmp_path)
    assert flagged.family_check["status"] == "disagree"
    assert flagged.family_check["jev_family"] == "SnowballOption"
    assert plain.family_check is None
    assert (flagged.validation_status, flagged.validation_errors) == (
        plain.validation_status, plain.validation_errors)


def test_an_unsupported_segment_is_still_checked(s1_on, session, tmp_path, monkeypatch):
    jev(monkeypatch, "SnowballOption", 0.9)
    _doc, [trade] = parse(session, tmp_path, family="unknown")
    assert trade.validation_status == "unsupported"
    assert trade.family_check["status"] == "disagree"      # the most useful case


def test_a_check_failure_never_fails_the_document(s1_on, session, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(svc, "check_family", boom)
    doc, [trade] = parse(session, tmp_path)
    assert doc.status == "parsed"
    assert trade.family_check == {"status": "unscored", "reason": "internal_error",
                                  "jev_family": None, "confidence": None, "top": None,
                                  "model": None}


def test_no_key_is_visible_and_the_document_still_parses(s1_on, session, tmp_path, monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    doc, [trade] = parse(session, tmp_path)
    assert doc.status == "parsed"
    assert (trade.family_check["status"], trade.family_check["reason"]) == ("unscored", "no_key")


@pytest.mark.parametrize("setup, kwargs", [
    (lambda mp: mp.setenv("OPEN_OTC_SYSTEM_ONE", "false"), {}),
    (lambda mp: mp.setenv("OPEN_OTC_CONFIRMATION_FAMILY_CHECK", "false"), {}),
    (lambda mp: None, {"family_check": False}),
])
def test_inert_paths_make_no_call_and_leave_null(s1_on, session, tmp_path, monkeypatch, setup, kwargs):
    post = jev(monkeypatch, "EuropeanVanillaOption")
    setup(monkeypatch)
    _doc, [trade] = parse(session, tmp_path, **kwargs)
    assert trade.family_check is None and post.calls == []


def test_run_parse_batch_passes_the_flag_through(session, tmp_path, monkeypatch):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    session.add(ConfirmationDocument(batch_id=batch.id, filename="c.pdf",
                                     stored_path=str(tmp_path / "c.pdf"), sha256="d" * 64,
                                     byte_len=1, mime="application/pdf"))
    session.commit()
    seen = []
    monkeypatch.setattr(svc, "parse_document",
                        lambda session_, document, *, client, family_check=True: seen.append(family_check))
    svc.run_parse_batch(batch.id, client=object(), family_check=False)
    svc.run_parse_batch(batch.id, client=object())
    assert seen == [False, True]


def test_the_tool_disables_the_check_on_arena_turns(session, settings, monkeypatch):
    import app.tools.confirmations as tools_confirmations
    from app.config import configure_settings
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY

    seen = []
    monkeypatch.setattr(tools_confirmations.confirmations, "parse_document",
                        lambda session_, document, *, client, family_check=True: seen.append(family_check))
    monkeypatch.setattr(tools_confirmations.confirmations_llm, "build_extractor_client",
                        lambda selection=None: object())
    configure_settings(settings)
    try:
        uploads = settings.artifact_dir / "uploads" / "chat"
        uploads.mkdir(parents=True, exist_ok=True)
        stored = uploads / "c.pdf"
        stored.write_bytes(b"%PDF-1.4 x")
        stamp = {"channel": "zenmux", "provider": "p", "model": "m"}
        tools_confirmations.parse_trade_confirmation.func(
            paths=[str(stored)], config={"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: stamp}})
        tools_confirmations.parse_trade_confirmation.func(
            paths=[str(stored)], config={"configurable": {}})
    finally:
        configure_settings(None)
    assert seen == [False, True]


def test_family_check_allowed_reads_presence_of_the_arena_stamp():
    from app.services.confirmations.llm import CONFIRMATION_EXTRACTOR_SELECTION_KEY
    from app.tools.confirmations import _family_check_allowed

    assert _family_check_allowed(None) is True
    assert _family_check_allowed({"configurable": None}) is True
    assert _family_check_allowed({"configurable": {}}) is True
    # Presence is enough — even a malformed stamp marks an arena turn.
    assert _family_check_allowed({"configurable": {CONFIRMATION_EXTRACTOR_SELECTION_KEY: "x"}}) is False


def test_the_tool_payload_carries_the_check(session):
    from app.tools.confirmations import get_confirmation_batch

    batch = ConfirmationBatch(source="agent")
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(batch_id=batch.id, filename="c.pdf", stored_path="/x",
                               sha256="e" * 64, byte_len=1, mime="application/pdf")
    session.add(doc)
    session.flush()
    session.add(ExtractedTrade(document_id=doc.id, seq=1, family="SnowballOption",
                               validation_status="valid",
                               family_check={"status": "disagree", "jev_family": "PhoenixOption"}))
    session.commit()
    trade = get_confirmation_batch.func(batch_id=batch.id)["documents"][0]["trades"][0]
    assert trade["family_check"]["jev_family"] == "PhoenixOption"
