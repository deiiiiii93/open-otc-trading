"""Family cross-check (spec 2026-09-21 §3): every row of the status table."""
from __future__ import annotations

import pytest

from _system_one_fakes import ChoicePost, FakePost
from app.services.confirmations.extract import DocumentContent, PageContent
from app.services.confirmations.family_check import (
    FAMILY_DESCRIPTIONS, FamilyCheck, check_family, decide, family_options, segment_text,
)
from app.services.confirmations.llm import TradeSegment
from app.tools.product_term_schema import _SCHEMA_FAMILIES

FAMILIES = frozenset(_SCHEMA_FAMILIES)
TEXT = PageContent(index=1, text="Autocallable note, knock-out 103%, knock-in 70%")
SEG = TradeSegment(family="SnowballOption", pages=[1], anchor="Autocallable note")


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _doc(*pages):
    return DocumentContent(pages=list(pages), page_count=len(pages), extract_mode="text")


@pytest.mark.parametrize("llm, choice, confidence, expected", [
    ("SnowballOption", "SnowballOption", 0.2, ("agree", None)),
    ("unknown", "unknown", 0.9, ("agree", None)),
    ("MysteryOption", "unknown", 0.9, ("agree", None)),         # LLM also found no family
    ("SnowballOption", "unknown", 0.9, ("unscored", "jev_unknown")),
    ("SnowballOption", "PhoenixOption", 0.5, ("disagree", None)),
    ("SnowballOption", "PhoenixOption", 0.49, ("unscored", "low_confidence")),
])
def test_status_decision_table(llm, choice, confidence, expected):
    assert decide(llm, choice, confidence, FAMILIES) == expected


def test_unknown_is_a_reserved_option():
    assert "unknown" not in _SCHEMA_FAMILIES
    with pytest.raises(ValueError):
        family_options(FAMILIES | {"unknown"})


def test_descriptions_never_name_a_stale_family():
    assert set(FAMILY_DESCRIPTIONS) <= set(_SCHEMA_FAMILIES)


def test_a_new_family_falls_back_to_its_own_name():
    options = family_options(FAMILIES | {"BrandNewOption"})
    assert options["BrandNewOption"] == "BrandNewOption"
    assert list(options)[-1] == "unknown"


def test_text_layer_eligibility_uses_the_pipelines_own_classification():
    scan = PageContent(index=2, text="", image_png=b"png")
    assert segment_text(_doc(TEXT), [1]) == f"[page 1]\n{TEXT.text}"   # logo pages have image_png None
    assert segment_text(_doc(TEXT, scan), [1, 2]) is None
    assert segment_text(_doc(TEXT, scan), [1]) is not None             # the scan is outside the segment
    assert segment_text(_doc(TEXT, scan), []) is None                  # [] means every page


def test_pages_join_in_order_as_page_blocks():
    second = PageContent(index=2, text="second page")
    assert segment_text(_doc(TEXT, second), []) == f"[page 1]\n{TEXT.text}\n\n[page 2]\nsecond page"


def test_a_scan_page_is_unscored_without_a_call():
    post = ChoicePost("SnowballOption")
    scan = PageContent(index=1, text="", image_png=b"png")
    assert check_family(_doc(scan), SEG, schema_families=FAMILIES, post=post) == FamilyCheck(
        status="unscored", reason="no_text_layer")
    assert post.calls == []


def test_an_empty_text_layer_is_also_no_text_layer():
    blank = PageContent(index=1, text="")
    check = check_family(_doc(blank), SEG, schema_families=FAMILIES, post=ChoicePost("SnowballOption"))
    assert check.reason == "no_text_layer"


def test_agree_and_what_is_sent():
    post = ChoicePost("SnowballOption", confidence=0.9)
    check = check_family(_doc(TEXT), SEG, schema_families=FAMILIES, post=post)
    assert (check.status, check.jev_family, check.confidence, check.model) == (
        "agree", "SnowballOption", 0.9, "typesafe/jev-1.13")
    question = post.calls[0]["questions"]["family"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == sorted(FAMILIES) + ["unknown"]
    assert post.calls[0]["state"] == {"anchor": "Autocallable note",
                                      "document_text": f"[page 1]\n{TEXT.text}"}


def test_disagree_on_a_segment_the_llm_could_not_classify():
    seg = TradeSegment(family="unknown", pages=[1], anchor="x")
    check = check_family(_doc(TEXT), seg, schema_families=FAMILIES,
                         post=ChoicePost("SnowballOption", 0.9))
    assert check.status == "disagree"


def test_top_three_sorted_by_probability_then_name_rounded():
    probabilities = {option: 0.0 for option in sorted(FAMILIES) + ["unknown"]}
    probabilities.update({"SnowballOption": 0.6, "PhoenixOption": 0.2, "AsianOption": 0.2,
                          "BarrierOption": 0.004})
    body = {"model": "typesafe/jev-1.13", "answers": {"family": {
        "type": "choice", "choice": "SnowballOption", "confidence": 0.6,
        "probabilities": probabilities}}}
    check = check_family(_doc(TEXT), SEG, schema_families=FAMILIES, post=FakePost(body))
    assert check.top == [["SnowballOption", 0.6], ["AsianOption", 0.2], ["PhoenixOption", 0.2]]


def test_unavailable_records_the_reason_and_the_requested_model(monkeypatch):
    monkeypatch.delenv("ZENMUX_API_KEY")
    check = check_family(_doc(TEXT), SEG, schema_families=FAMILIES, post=ChoicePost("SnowballOption"))
    assert check.as_json() == {"status": "unscored", "reason": "no_key", "jev_family": None,
                               "confidence": None, "top": None, "model": "typesafe/jev-1.13"}
