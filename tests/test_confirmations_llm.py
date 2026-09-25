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


def test_segment_non_dict_json_retries_once_then_succeeds():
    client = FakeClient([
        json.dumps([1, 2, 3]),
        json.dumps({"trades": [
            {"family": "EuropeanVanillaOption", "pages": [1], "anchor": "BUY 100 vanilla"}
        ]}),
    ])
    segments = segment_document(_text_doc(), client)
    assert segments == [TradeSegment(
        family="EuropeanVanillaOption", pages=[1], anchor="BUY 100 vanilla")]
    assert len(client.calls) == 2


def test_segment_non_dict_json_both_responses_raises_extraction_error():
    client = FakeClient([json.dumps([1, 2, 3]), json.dumps("just a string")])
    with pytest.raises(ExtractionError) as exc_info:
        segment_document(_text_doc(), client)
    assert len(client.calls) == 2
    assert exc_info.value.raw_response == json.dumps("just a string")


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


def _mixed_doc():
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 10
    return DocumentContent(
        pages=[PageContent(index=1, text="Ardsley Ref. No: ARD-EQO-2026-04781"),
               PageContent(index=2, text="", image_png=png)],
        page_count=2, extract_mode="mixed",
    )


def test_every_image_page_is_labelled_with_its_page_number():
    """Run #141: on conf-08 (text page 1, scanned page 2) the scan carried no page
    number, gpt-6-luna put the trade on page [1], and stage 2 never saw the scan.
    """
    client = FakeClient([json.dumps({"trades": []})])
    segment_document(_mixed_doc(), client)
    parts = client.calls[0]
    image_at = next(i for i, p in enumerate(parts) if p["type"] == "image_url")
    assert parts[image_at - 1] == {"type": "text", "text": "[page 2 — scanned image]"}


def test_stage2_retries_with_all_pages_when_its_subset_holds_no_terms():
    empty = {"terms": {}, "counterparty": "Larkspur Pension Trust", "confidence": 0.3}
    full = {"terms": {"strike": 185.0, "initial_price": 178.9}, "confidence": 0.95}
    client = FakeClient([json.dumps(empty), json.dumps(full)])
    seg = TradeSegment(family="EuropeanVanillaOption", pages=[1], anchor="Put")
    draft = extract_trade(_mixed_doc(), seg, client)
    assert draft.terms == {"strike": 185.0, "initial_price": 178.9}
    assert len(client.calls) == 2
    assert not any(p["type"] == "image_url" for p in client.calls[0])  # page 1 only
    assert any(p["type"] == "image_url" for p in client.calls[1])      # whole document


def test_stage2_does_not_retry_when_it_already_saw_every_page():
    client = FakeClient([json.dumps({"terms": {}, "confidence": 0.1})])
    seg = TradeSegment(family="EuropeanVanillaOption", pages=[1, 2], anchor="x")
    draft = extract_trade(_mixed_doc(), seg, client)
    assert draft.terms == {} and len(client.calls) == 1


def test_stage2_does_not_retry_a_subset_that_found_terms():
    client = FakeClient([json.dumps({"terms": {"strike": 1.0}})])
    seg = TradeSegment(family="EuropeanVanillaOption", pages=[1], anchor="x")
    extract_trade(_mixed_doc(), seg, client)
    assert len(client.calls) == 1


def test_resolver_prefers_dedicated_tag():
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    class Reg:
        def select_by_tag(self, tag):
            return {"channel": "zenmux", "provider": "openai", "model": "vision-x"} \
                if tag == "confirmation_extractor" else None

        def default_selection(self):
            return {"channel": "zenmux", "provider": "openai", "model": "default-y"}

    assert resolve_confirmation_extractor_selection(Reg())["model"] == "vision-x"


def test_content_to_text_passes_a_plain_string_through():
    from app.services.confirmations.llm import _content_to_text

    assert _content_to_text('{"strike": 205.0}') == '{"strike": 205.0}'


def test_content_to_text_flattens_anthropic_reasoning_blocks():
    """glm-5.3-flash returns [thinking, text]; only the text block is the answer.

    Measured live 2026-08-28 on z-ai/glm-5.3-flash:bigmodel. Before this the
    extractor raised ExtractionError on every such response, which on an arena
    board would read as "the model cannot see" rather than "the harness dropped
    the answer".
    """
    from app.services.confirmations.llm import _content_to_text

    content = [
        {"type": "thinking", "thinking": "The strike appears to be 205.",
         "signature": "abc"},
        {"type": "text", "text": '{"strike": 205.0}'},
    ]
    assert _content_to_text(content) == '{"strike": 205.0}'


def test_content_to_text_joins_multiple_text_blocks():
    from app.services.confirmations.llm import _content_to_text

    content = [{"type": "text", "text": '{"a": 1,'},
               {"type": "text", "text": ' "b": 2}'}]
    assert _content_to_text(content) == '{"a": 1, "b": 2}'


def test_content_to_text_raises_when_no_text_block_survives():
    from app.services.confirmations.llm import _content_to_text

    with pytest.raises(ExtractionError):
        _content_to_text([{"type": "thinking", "thinking": "hmm", "signature": "s"}])


def test_content_to_text_raises_on_an_unusable_type():
    from app.services.confirmations.llm import _content_to_text

    with pytest.raises(ExtractionError):
        _content_to_text(None)


class _TagRegistry:
    """A registry whose dedicated tag resolves, so the ladder is exercised."""

    def select_by_tag(self, tag):
        if tag == "confirmation_extractor":
            return {"channel": "zenmux", "provider": "google-vertex",
                    "model": "google/gemini-3.6-flash"}
        return None

    def default_selection(self):
        return {"channel": "zenmux", "provider": "openai", "model": "default-y"}


def test_resolver_override_wins_over_the_dedicated_tag():
    """The arena routes extraction to the CONTESTANT.

    Without this, every contestant on a vision board reads every document with
    whichever model happens to hold the confirmation_extractor tag, so every
    vision check lands N/N across the field and carries zero ability signal --
    the defect the Run #58 audit found in 15 of 50 checks.
    """
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    override = {"channel": "zenmux", "provider": "bigmodel",
                "model": "z-ai/glm-5.3-flash"}
    assert resolve_confirmation_extractor_selection(_TagRegistry(), override) == override


def test_resolver_returns_a_copy_so_a_caller_cannot_mutate_the_selection():
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    override = {"channel": "zenmux", "provider": "bigmodel",
                "model": "z-ai/glm-5.3-flash"}
    resolved = resolve_confirmation_extractor_selection(_TagRegistry(), override)
    resolved["model"] = "mutated"
    assert override["model"] == "z-ai/glm-5.3-flash"


def test_resolver_ignores_an_absent_or_empty_override():
    """Unset must be byte-identical to today: the production desk keeps its tag."""
    from app.services.confirmations.llm import resolve_confirmation_extractor_selection

    for override in (None, {}):
        resolved = resolve_confirmation_extractor_selection(_TagRegistry(), override)
        assert resolved["model"] == "google/gemini-3.6-flash"
