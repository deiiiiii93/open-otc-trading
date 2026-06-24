from typing import Callable, Coroutine, Any, Protocol

from app.services.gateway.types import (
    ChatRef,
    ConnectorCapabilities,
    ConnectorHealth,
    InboundMessage,
    MessageRef,
    OutboundCard,
    OutboundMessage,
)


class MessageConnector(Protocol):
    """Protocol that every IM connector must satisfy."""

    name: str
    capabilities: ConnectorCapabilities

    async def start(
        self,
        on_inbound: Callable[[InboundMessage], Coroutine[Any, Any, None]],
    ) -> None:
        """Connect to the platform and register the inbound-message callback."""
        ...

    async def stop(self) -> None:
        """Gracefully disconnect."""
        ...

    async def send_message(
        self,
        chat: ChatRef,
        msg: OutboundMessage,
        *,
        idempotency_key: str,
    ) -> MessageRef:
        """Send a plain text message; idempotent on key."""
        ...

    async def update_message(
        self,
        ref: MessageRef,
        msg: OutboundMessage,
    ) -> None:
        """Edit an existing message in place."""
        ...

    async def send_card(
        self,
        chat: ChatRef,
        card: OutboundCard,
        *,
        idempotency_key: str,
    ) -> MessageRef:
        """Send an interactive card; idempotent on key."""
        ...

    async def update_card(
        self,
        ref: MessageRef,
        card: OutboundCard,
    ) -> None:
        """Replace an existing card in place."""
        ...

    async def health(self) -> ConnectorHealth:
        """Return current health status."""
        ...
