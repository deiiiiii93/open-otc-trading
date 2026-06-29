import pytest
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.extractor import (
    MemoryDiff, validate_diff, parse_diff, extract_facts, MalformedDiffError, _clamp,
)


def test_validate_drops_empty_and_clamps_confidence():
    raw = {"add": [
        {"content": "", "scope_type": "user"},
        {"content": "books in USD", "scope_type": "user", "confidence": 5},
        {"content": "books in USD", "scope_type": "user"},
        {"content": "x", "scope_type": "book"},
    ]}
    diff = validate_diff(raw, ["user", "correction", "domain"], set(), MemoryConfig())
    assert len(diff.add) == 1
    assert diff.add[0]["confidence"] == 1.0


def test_validate_category_cleanup():
    raw = {"add": [
        {"content": "books in USD", "scope_type": "user", "category": "Trade Style!!"},
        {"content": "hedges net delta", "scope_type": "user", "category": "hedging"},
        {"content": "long fact x", "scope_type": "user", "category": "x" * 80},
    ]}
    diff = validate_diff(raw, ["user"], set(), MemoryConfig())
    cats = [a["category"] for a in diff.add]
    assert cats == [None, "hedging", None]


def test_validate_drops_below_floor_and_overlong():
    cfg = MemoryConfig()
    raw = {"add": [
        {"content": "low conf fact", "scope_type": "user", "confidence": 0.5},   # < floor 0.7 -> drop
        {"content": "kept fact ok", "scope_type": "user", "confidence": 0.8},     # kept
        {"content": "y" * (cfg.content_max_chars + 1), "scope_type": "user"},     # too long -> drop
    ]}
    diff = validate_diff(raw, ["user"], set(), cfg)
    assert [a["content"] for a in diff.add] == ["kept fact ok"]


def test_validate_update_overlong_content_dropped():
    cfg = MemoryConfig()
    raw = {"update": [
        {"id": 1, "content": "z" * (cfg.content_max_chars + 1)},   # too long -> drop item
        {"id": 2, "content": "fine"},                              # kept
    ]}
    diff = validate_diff(raw, ["user"], {1, 2}, cfg)
    assert [u["id"] for u in diff.update] == [2]


def test_validate_update_remove_in_scope():
    raw = {"remove": [1, 99], "update": [{"id": 1, "content": "new"}, {"id": 7, "content": "x"}]}
    diff = validate_diff(raw, ["user"], {1}, MemoryConfig())
    assert diff.remove == [1]
    assert [u["id"] for u in diff.update] == [1]


def test_parse_diff_malformed():
    with pytest.raises(MalformedDiffError):
        parse_diff("not json{{")


def test_extract_facts_with_stub_llm():
    llm = lambda prompt: '{"add": [{"content": "hedges net delta", "scope_type": "user", "confidence": 0.9}]}'
    diff = extract_facts([{"role": "user", "content": "I always hedge net delta"}],
                         [], ["user", "correction", "domain"], llm=llm, config=MemoryConfig())
    assert diff.add[0]["content"] == "hedges net delta"


def test_extract_facts_malformed_raises():
    with pytest.raises(MalformedDiffError):
        extract_facts([], [], ["user"], llm=lambda p: "garbage", config=MemoryConfig())


# ---------------------------------------------------------------------------
# Fix 2: _clamp non-numeric confidence → floor-drop (0.0), not max-trust (1.0)
# ---------------------------------------------------------------------------

def test_clamp_non_numeric_returns_zero():
    """Non-numeric confidence values must return 0.0 (floor-drop), not 1.0 (max-trust)."""
    assert _clamp("high") == 0.0
    assert _clamp("medium") == 0.0
    assert _clamp(None) == 0.0
    assert _clamp([0.9]) == 0.0
    # numeric values still work
    assert _clamp(0.8) == 0.8
    assert _clamp(5.0) == 1.0   # clamped to max
    assert _clamp(-0.1) == 0.0  # clamped to min


def test_non_numeric_confidence_add_is_floor_dropped():
    """An add item whose confidence is a non-numeric string is dropped by the floor check
    because _clamp returns 0.0 (< confidence_floor=0.7), not 1.0."""
    raw = {"add": [
        {"content": "fact with string confidence", "scope_type": "user", "confidence": "high"},
        {"content": "fact with numeric confidence", "scope_type": "user", "confidence": 0.9},
    ]}
    diff = validate_diff(raw, ["user"], set(), MemoryConfig())
    # "high" → _clamp returns 0.0 → 0.0 < 0.7 → dropped
    assert len(diff.add) == 1
    assert diff.add[0]["content"] == "fact with numeric confidence"
