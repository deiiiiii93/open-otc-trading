"""Tests for gateway GatewayConfig typed view and deep-link helpers."""

from __future__ import annotations

from app.config import Settings
from app.services.gateway.config import GatewayConfig


def _settings(**overrides) -> Settings:
    """Build a Settings with sensible base defaults plus any overrides."""
    base = dict(
        # Required settings that have no env default in test env
        database_url="sqlite+pysqlite:///./data/open_otc.sqlite3",
        agent_channels_file="./config/agent_channels.yaml",
        agent_checkpoint_db_path="./agent_checkpoints.sqlite",
    )
    base.update(overrides)
    return Settings(**base)


class TestGatewayConfigDefaults:
    def test_default_desk_user(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_default_desk_user == "desk_user"

    def test_linking_code_ttl_s(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_linking_code_ttl_s == 600

    def test_card_action_ttl_s(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_card_action_ttl_s == 1800

    def test_max_inbound_chars(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_max_inbound_chars == 4000

    def test_max_queued_per_chat(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_max_queued_per_chat == 8

    def test_queue_max_age_s(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_queue_max_age_s == 120

    def test_dedupe_ttl_s(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_dedupe_ttl_s == 86400

    def test_dedupe_lease_s(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_dedupe_lease_s == 120

    def test_lock_lease_s(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_lock_lease_s == 30

    def test_code_issue_per_min(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_code_issue_per_min == 10

    def test_flush_interval_ms(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_flush_interval_ms == 700

    def test_flush_chars(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_flush_chars == 280

    def test_web_base_url_default_none(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_web_base_url is None

    def test_enabled_connectors_default_empty(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.gateway_enabled_connectors == ""

    def test_feishu_fields_default_none(self):
        cfg = GatewayConfig.from_settings(_settings())
        assert cfg.feishu_app_id is None
        assert cfg.feishu_app_secret is None
        assert cfg.feishu_verification_token is None
        assert cfg.feishu_encrypt_key is None


class TestWebThreadLink:
    def test_returns_none_when_base_url_unset(self):
        cfg = GatewayConfig.from_settings(_settings())
        result = cfg.web_thread_link("thread-123")
        assert result is None

    def test_returns_formatted_url_when_base_set(self):
        cfg = GatewayConfig.from_settings(
            _settings(gateway_web_base_url="https://example.com")
        )
        result = cfg.web_thread_link("thread-123")
        assert result == "https://example.com/chat?thread=thread-123"


class TestWebActionLink:
    def test_returns_none_when_base_url_unset(self):
        cfg = GatewayConfig.from_settings(_settings())
        result = cfg.web_action_link("t1", "m1", "a1")
        assert result is None

    def test_returns_formatted_url_when_base_set(self):
        cfg = GatewayConfig.from_settings(
            _settings(gateway_web_base_url="https://example.com")
        )
        result = cfg.web_action_link("t1", "m1", "a1")
        assert result == "https://example.com/chat?thread=t1&message=m1&action=a1"

    def test_url_with_trailing_slash_in_base(self):
        cfg = GatewayConfig.from_settings(
            _settings(gateway_web_base_url="https://example.com/")
        )
        # rstrip trailing slash to avoid double-slash
        result = cfg.web_action_link("t1", "m1", "a1")
        assert result == "https://example.com/chat?thread=t1&message=m1&action=a1"
