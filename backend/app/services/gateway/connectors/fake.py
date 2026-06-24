"""In-memory FakeConnector for testing."""

from __future__ import annotations

import uuid
from typing import Any, Callable, Coroutine

from app.services.gateway.types import (
    ChatRef,
    ConnectorCapabilities,
    ConnectorHealth,
    InboundMessage,
    MessageRef,
    OutboundCard,
    OutboundMessage,
)


class FakeConnector:
    """Fully in-memory connector. Use in unit/integration tests."""

    name: str = "fake"
    capabilities: ConnectorCapabilities = ConnectorCapabilities(
        supports_edit_in_place_message=True,
        supports_edit_in_place_card=True,
        supports_interactive_cards=True,
        max_message_chars=10000,
    )

    def __init__(self) -> None:
        # Visible to tests for assertion
        self.outbox: list[dict[str, Any]] = []

        # Idempotency store: key → MessageRef
        self._idem: dict[str, MessageRef] = {}

        # Registered inbound callback
        self._on_inbound: Callable[[InboundMessage], Coroutine[Any, Any, None]] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        on_inbound: Callable[[InboundMessage], Coroutine[Any, Any, None]],
    ) -> None:
        self._on_inbound = on_inbound

    async def stop(self) -> None:
        self._on_inbound = None

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    async def send_message(
        self,
        chat: ChatRef,
        msg: OutboundMessage,
        *,
        idempotency_key: str,
    ) -> MessageRef:
        if idempotency_key in self._idem:
            return self._idem[idempotency_key]

        ref = MessageRef(
            connector=self.name,
            workspace_id=chat.workspace_id,
            chat_id=chat.chat_id,
            message_id=str(uuid.uuid4()),
        )
        self._idem[idempotency_key] = ref
        self.outbox.append({"type": "message", "ref": ref, "msg": msg})
        return ref

    async def update_message(
        self,
        ref: MessageRef,
        msg: OutboundMessage,
    ) -> None:
        self.outbox.append({"type": "update_message", "ref": ref, "msg": msg})

    async def send_card(
        self,
        chat: ChatRef,
        card: OutboundCard,
        *,
        idempotency_key: str,
    ) -> MessageRef:
        if idempotency_key in self._idem:
            return self._idem[idempotency_key]

        ref = MessageRef(
            connector=self.name,
            workspace_id=chat.workspace_id,
            chat_id=chat.chat_id,
            message_id=str(uuid.uuid4()),
        )
        self._idem[idempotency_key] = ref
        self.outbox.append({"type": "card", "ref": ref, "card": card})
        return ref

    async def update_card(
        self,
        ref: MessageRef,
        card: OutboundCard,
    ) -> None:
        self.outbox.append({"type": "update_card", "ref": ref, "card": card})

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> ConnectorHealth:
        return ConnectorHealth(name=self.name, state="healthy", detail="in-memory fake")

    # ------------------------------------------------------------------
    # Test helper
    # ------------------------------------------------------------------

    async def feed_inbound(self, msg: InboundMessage) -> None:
        """Simulate an inbound event arriving from the platform."""
        if self._on_inbound is None:
            raise RuntimeError("FakeConnector.start() has not been called; no on_inbound callback registered")
        await self._on_inbound(msg)
