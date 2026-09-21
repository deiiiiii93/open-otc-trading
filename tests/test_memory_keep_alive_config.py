from __future__ import annotations

import pytest

from app.services.deep_agent.memory.config import MemoryConfig, get_memory_config


def test_defaults():
    c = MemoryConfig()
    assert (c.keep_alive_enabled, c.keep_alive_batch, c.keep_alive_sibling_limit,
            c.keep_alive_sibling_chars, c.keep_alive_refresh_days) == (True, 10, 50, 240, 30)


@pytest.mark.parametrize("raw, expected", [
    (None, True), ("on", True), ("ON", True), ("off", False), ("0", False), ("false", False),
])
def test_env_switch_uses_the_memory_parser(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("OPEN_OTC_MEMORY_KEEP_ALIVE", raising=False)
    else:
        monkeypatch.setenv("OPEN_OTC_MEMORY_KEEP_ALIVE", raw)
    assert get_memory_config().keep_alive_enabled is expected


def test_memory_switch_is_unchanged(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "off")
    assert get_memory_config().enabled is False
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    assert get_memory_config().enabled is True
