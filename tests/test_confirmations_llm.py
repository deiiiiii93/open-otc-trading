import json

import pytest

from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.confirmations.llm import (
    ExtractionError, TradeSegment, extract_trade, segment_document,
)


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, content_parts):
        self.calls.append(content_parts)
        return self.responses.pop(0)


def _text_doc(text="BUY 100 vanilla call on AAPL strike 150"):
    return DocumentContent(
        pages=[PageContent(index=1, text=text)], page_count=1, extract_mode="text"
    )


def test_segment_document_parses_trade_list():
    client = FakeClient([json.dumps({"trades": [
        {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 100 vanilla"}
    ]})])
    segments = segment_document(_text_doc(), client)
    assert segments == [TradeSegment(
        family="EuropeanVanillaOption", pages=[1], anchor="BUY 100 vanilla")]
    # Prompt must constrain the family vocabulary.
    prompt_text = "".join(p.get("text", "") for p in client.calls[0])
    assert "EuropeanVanillaOption" in prompt_text
    assert "SnowballOption" in prompt_text


def test_segment_invalid_json_retries_once_then_raises():
    client = FakeClient(["not json", "still not json"])
    with pytest.raises(ExtractionError):
        segment_document(_text_doc(), client)
    assert len(client.calls) == 2


def test_extract_trade_targets_family_schema_and_parses_draft():
    payload = {
        "terms": {"strike": 150.0, "maturity_years": 1.0, "option_type": "call"},
        "underlying": "AAPL", "quantity": 100, "entry_price": 12.5,
        "currency": "USD", "counterparty": "Big Bank", "trade_date": "2026-08-01",
        "external_trade_id": "TC-1001", "confidence": 0.92,
        "evidence": {"strike": {"quote": "strike 150", "page": 1}},
    }
    client = FakeClient([json.dumps(payload)])
    seg = TradeSegment(family="EuropeanVanillaOption", pages=[1], anchor="BUY")
    draft = extract_trade(_text_doc(), seg, client)
    assert draft.family == "EuropeanVanillaOption"
    assert draft.terms["strike"] == 150.0
    assert draft.external_trade_id == "TC-1001"
    # Stage-2 prompt must embed the family's legal schema field names.
    prompt_text = "".join(p.get("text", "") for p in client.calls[0])
    assert "strike" in prompt_text


def test_image_pages_become_image_parts():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    doc = DocumentContent(
        pages=[PageContent(index=1, text="", image_png=png)],
        page_count=1, extract_mode="vision",
    )
    client = FakeClient([json.dumps({"trades": []})])
    segment_document(doc, client)
    kinds = {p["type"] for p in client.calls[0]}
    assert "image_url" in kinds


def test_resolver_prefers_dedicated_tag():
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    class Reg:
        def select_by_tag(self, tag):
            return {"channel": "zenmux", "provider": "openai", "model": "vision-x"} \
                if tag == "confirmation_extractor" else None

        def default_selection(self):
            return {"channel": "zenmux", "provider": "openai", "model": "default-y"}

    assert resolve_confirmation_extractor_selection(Reg())["model"] == "vision-x"
