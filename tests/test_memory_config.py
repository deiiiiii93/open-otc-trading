from datetime import datetime

from app.services.deep_agent.memory.config import (
    MemoryConfig, get_memory_config, DEFAULT_DENYLIST, DEFAULT_CORRECTION_PHRASES,
)


def test_defaults_match_spec():
    c = MemoryConfig()
    assert c.confidence_floor == 0.7
    assert c.max_facts_per_scope == 100
    assert c.max_correction_facts == 20
    assert c.injection_token_budget == 2000
    assert c.correction_token_budget == 1000
    assert c.max_queue_size == 1000
    assert c.max_high_queue_size == 256
    assert c.sweep_interval_seconds == 60
    assert c.max_extract_attempts == 3
    assert c.extract_window_messages == 40
    assert c.extract_window_tokens == 8000
    assert c.read_timeout_ms == 250
    assert c.writer_busy_timeout_ms == 2000
    assert c.content_max_chars == 2000
    assert c.category_max_chars == 64
    assert c.tiktoken_encoder == "cl100k_base"
    assert c.extractor_model == "extractor"        # dedicated tag (primary)
    assert c.extractor_fallback_tag == "fast"      # cheap-tier fallback
    assert "that's wrong" in DEFAULT_CORRECTION_PHRASES
    assert any("api" in d for d in DEFAULT_DENYLIST)


def test_get_memory_config_env_toggle(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "off")
    assert get_memory_config().enabled is False
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    assert get_memory_config().enabled is True
    monkeypatch.delenv("OPEN_OTC_MEMORY", raising=False)
    assert get_memory_config().enabled is True


def test_reconcile_since_defaults_none():
    assert MemoryConfig().reconcile_since is None


def test_reconcile_since_unset_is_none(monkeypatch):
    monkeypatch.delenv("OPEN_OTC_MEMORY_RECONCILE_SINCE", raising=False)
    assert get_memory_config().reconcile_since is None


def test_reconcile_since_parses_iso(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY_RECONCILE_SINCE", "2026-06-30T12:00:00")
    assert get_memory_config().reconcile_since == datetime(2026, 6, 30, 12, 0, 0)


def test_reconcile_since_accepts_z_suffix(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY_RECONCILE_SINCE", "2026-06-30T12:00:00Z")
    parsed = get_memory_config().reconcile_since
    assert parsed is not None and parsed.year == 2026 and parsed.month == 6


def test_reconcile_since_malformed_fails_open_to_none(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY_RECONCILE_SINCE", "not-a-date")
    assert get_memory_config().reconcile_since is None
