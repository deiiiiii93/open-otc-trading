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
    assert c.extractor_model == "fast"  # registry tag for the flash tier
    assert "that's wrong" in DEFAULT_CORRECTION_PHRASES
    assert any("api" in d for d in DEFAULT_DENYLIST)


def test_get_memory_config_env_toggle(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "off")
    assert get_memory_config().enabled is False
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    assert get_memory_config().enabled is True
    monkeypatch.delenv("OPEN_OTC_MEMORY", raising=False)
    assert get_memory_config().enabled is True
