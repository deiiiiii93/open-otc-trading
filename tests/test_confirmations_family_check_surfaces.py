"""A disagreement must reach the human at the moment they approve an
IRREVERSIBLE booking, and be served over REST (spec §3)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import database
from app.models import ConfirmationBatch, ConfirmationDocument, ExtractedTrade
from app.routers.confirmations import build_confirmations_router
from app.services.deep_agent.hitl import _summarize_book_extracted_trade

DISAGREE = {"status": "disagree", "reason": None, "jev_family": "PhoenixOption",
            "confidence": 0.82, "top": [["PhoenixOption", 0.82], ["SnowballOption", 0.18]],
            "model": "typesafe/jev-1.13"}


def _trade(session, family_check):
    batch = ConfirmationBatch(source="web")
    session.add(batch)
    session.flush()
    doc = ConfirmationDocument(batch_id=batch.id, filename="c.pdf", stored_path="/x",
                               sha256="f" * 64, byte_len=1, mime="application/pdf")
    session.add(doc)
    session.flush()
    trade = ExtractedTrade(document_id=doc.id, seq=1, family="SnowballOption",
                           underlying="600519.SH", quantity=100, validation_status="valid",
                           family_check=family_check)
    session.add(trade)
    session.commit()
    return batch, trade


def test_the_booking_card_names_a_disagreement(session):
    _batch, trade = _trade(session, DISAGREE)
    summary = _summarize_book_extracted_trade({"trade_id": trade.id})
    assert "FAMILY CHECK" in summary and "PhoenixOption" in summary and "0.82" in summary


@pytest.mark.parametrize("check", [None, {"status": "agree", "jev_family": "SnowballOption",
                                          "confidence": 0.9}])
def test_no_note_without_a_disagreement(session, check):
    _batch, trade = _trade(session, check)
    assert "FAMILY CHECK" not in _summarize_book_extracted_trade({"trade_id": trade.id})


def test_rest_serves_the_check(session):
    batch, _trade_row = _trade(session, DISAGREE)

    def get_db():
        with database.SessionLocal() as s:
            yield s

    app = FastAPI()
    app.include_router(build_confirmations_router(get_db=get_db))
    body = TestClient(app).get(f"/api/confirmations/{batch.id}").json()
    assert body["documents"][0]["trades"][0]["family_check"] == DISAGREE
