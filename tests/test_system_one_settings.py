"""System One settings (spec 2026-09-21 §0 Settings). Inert by default (D6)."""
from __future__ import annotations

import logging

import pytest

from app.config import TOOL_GUARD_MODES, Settings

_VARS = (
    "OPEN_OTC_SYSTEM_ONE", "OPEN_OTC_SYSTEM_ONE_MODEL", "OPEN_OTC_SYSTEM_ONE_BASE_URL",
    "OPEN_OTC_SYSTEM_ONE_TIMEOUT_S", "OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS",
    "OPEN_OTC_TOOL_GUARD", "OPEN_OTC_CONFIRMATION_FAMILY_CHECK", "OPEN_OTC_LIMIT_REVIEW",
    "OPEN_OTC_GUARD_SWEEP",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in _VARS:
        monkeypatch.delenv(name, raising=False)


def test_defaults_are_inert_and_match_the_spec():
    s = Settings()
    assert s.system_one_enabled is False
    assert s.system_one_model == "typesafe/jev-1.13"
    assert s.system_one_base_url == "https://zenmux.ai/api/v1"
    assert s.system_one_timeout_seconds == 5.0
    assert s.system_one_max_state_chars == 60000
    assert s.tool_guard_mode == "shadow"
    assert s.confirmation_family_check_enabled is True
    assert s.limit_review_enabled is True
    assert s.guard_sweep_enabled is True
    assert TOOL_GUARD_MODES == ("off", "shadow", "enforce")


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_MODEL", "typesafe/jev-latest")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_BASE_URL", "http://localhost:9/api/v1")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_TIMEOUT_S", "2.5")
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS", "1234")
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", "enforce")
    monkeypatch.setenv("OPEN_OTC_CONFIRMATION_FAMILY_CHECK", "false")
    monkeypatch.setenv("OPEN_OTC_LIMIT_REVIEW", "false")
    monkeypatch.setenv("OPEN_OTC_GUARD_SWEEP", "false")
    s = Settings()
    assert s.system_one_enabled is True
    assert s.system_one_model == "typesafe/jev-latest"
    assert s.system_one_base_url == "http://localhost:9/api/v1"
    assert s.system_one_timeout_seconds == 2.5
    assert s.system_one_max_state_chars == 1234
    assert s.tool_guard_mode == "enforce"
    assert s.confirmation_family_check_enabled is False
    assert s.limit_review_enabled is False
    assert s.guard_sweep_enabled is False


@pytest.mark.parametrize("raw, expected", [
    ("OFF", "off"), (" Shadow ", "shadow"), ("enforce", "enforce"),
])
def test_guard_mode_is_normalised(monkeypatch, raw, expected):
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", raw)
    assert Settings().tool_guard_mode == expected


@pytest.mark.parametrize("raw", ["enforced", "on", "", "block"])
def test_unknown_guard_mode_fails_closed_to_shadow(monkeypatch, caplog, raw):
    """Never to `off` (a typo must not stop measurement), never to `enforce`."""
    monkeypatch.setenv("OPEN_OTC_TOOL_GUARD", raw)
    with caplog.at_level(logging.WARNING, logger="app.config"):
        assert Settings().tool_guard_mode == "shadow"
    assert "OPEN_OTC_TOOL_GUARD" in caplog.text


def test_direct_construction_coerces_like_the_env_path():
    s = Settings(system_one_enabled="on", confirmation_family_check_enabled="0",
                 limit_review_enabled="0", guard_sweep_enabled="0",
                 system_one_timeout_seconds="3", system_one_max_state_chars="99",
                 tool_guard_mode="bogus")
    assert s.system_one_enabled is True
    assert s.confirmation_family_check_enabled is False
    assert s.limit_review_enabled is False
    assert s.guard_sweep_enabled is False
    assert s.system_one_timeout_seconds == 3.0
    assert s.system_one_max_state_chars == 99
    assert s.tool_guard_mode == "shadow"


def test_non_positive_timeout_is_rejected():
    with pytest.raises(ValueError, match="system_one_timeout_seconds"):
        Settings(system_one_timeout_seconds=0)


def test_limit_review_is_an_opt_out_under_the_master_switch():
    """Parent D15: default-enabled data class, opted out per feature."""
    assert Settings().limit_review_enabled is True
    assert Settings(limit_review_enabled="off").limit_review_enabled is False


def test_guard_sweep_is_an_opt_out_under_the_master_switch():
    """Spec 2026-09-22 D11 / parent D15: on by default, inert while the master is off."""
    assert Settings().guard_sweep_enabled is True
    assert Settings().system_one_enabled is False
    assert Settings(guard_sweep_enabled="off").guard_sweep_enabled is False
