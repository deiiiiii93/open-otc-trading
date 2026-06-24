"""Typed gateway configuration view and web deep-link helpers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.config import Settings

_log = logging.getLogger(__name__)
_warned_no_base_url = False


@dataclass(frozen=True)
class GatewayConfig:
    gateway_default_desk_user: str
    gateway_linking_code_ttl_s: int
    gateway_card_action_ttl_s: int
    gateway_max_inbound_chars: int
    gateway_max_queued_per_chat: int
    gateway_queue_max_age_s: int
    gateway_dedupe_ttl_s: int
    gateway_dedupe_lease_s: int
    gateway_lock_lease_s: int
    gateway_code_issue_per_min: int
    gateway_flush_interval_ms: int
    gateway_flush_chars: int
    gateway_web_base_url: str | None
    gateway_enabled_connectors: str
    feishu_app_id: str | None
    feishu_app_secret: str | None
    feishu_verification_token: str | None
    feishu_encrypt_key: str | None

    @classmethod
    def from_settings(cls, settings: "Settings") -> "GatewayConfig":
        return cls(
            gateway_default_desk_user=settings.gateway_default_desk_user,
            gateway_linking_code_ttl_s=settings.gateway_linking_code_ttl_s,
            gateway_card_action_ttl_s=settings.gateway_card_action_ttl_s,
            gateway_max_inbound_chars=settings.gateway_max_inbound_chars,
            gateway_max_queued_per_chat=settings.gateway_max_queued_per_chat,
            gateway_queue_max_age_s=settings.gateway_queue_max_age_s,
            gateway_dedupe_ttl_s=settings.gateway_dedupe_ttl_s,
            gateway_dedupe_lease_s=settings.gateway_dedupe_lease_s,
            gateway_lock_lease_s=settings.gateway_lock_lease_s,
            gateway_code_issue_per_min=settings.gateway_code_issue_per_min,
            gateway_flush_interval_ms=settings.gateway_flush_interval_ms,
            gateway_flush_chars=settings.gateway_flush_chars,
            gateway_web_base_url=settings.gateway_web_base_url,
            gateway_enabled_connectors=settings.gateway_enabled_connectors,
            feishu_app_id=settings.feishu_app_id,
            feishu_app_secret=settings.feishu_app_secret,
            feishu_verification_token=settings.feishu_verification_token,
            feishu_encrypt_key=settings.feishu_encrypt_key,
        )

    def _base(self) -> str | None:
        """Return the base URL with any trailing slash stripped, or None."""
        if self.gateway_web_base_url is None:
            global _warned_no_base_url
            if not _warned_no_base_url:
                _log.warning(
                    "gateway_web_base_url is not set; web deep-links will be unavailable"
                )
                _warned_no_base_url = True
            return None
        return self.gateway_web_base_url.rstrip("/")

    def web_thread_link(self, thread_id: str) -> str | None:
        """Return a web deep-link to ``thread_id``, or ``None`` if base URL unset."""
        base = self._base()
        if base is None:
            return None
        return f"{base}/chat?thread={thread_id}"

    def web_action_link(
        self, thread_id: str, message_id: str, action_id: str
    ) -> str | None:
        """Return a web deep-link to a specific card action, or ``None`` if base URL unset."""
        base = self._base()
        if base is None:
            return None
        return f"{base}/chat?thread={thread_id}&message={message_id}&action={action_id}"
