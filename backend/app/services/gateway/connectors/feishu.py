"""Feishu (Lark) connector for the IM message gateway.

The ``lark_oapi`` package is optional — all imports are guarded so this
module can be imported in environments where the SDK is not installed.
Translation functions (feishu_event_to_inbound, feishu_card_action_to_inbound,
outbound_card_to_feishu) are pure and have no SDK dependency.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine

if TYPE_CHECKING:
    from app.services.gateway.config import GatewayConfig

from app.services.gateway.types import (
    CardAction,
    CardActionInbound,
    CardSection,
    ChatRef,
    ConnectorCapabilities,
    ConnectorHealth,
    InboundMessage,
    MessageRef,
    OutboundCard,
    OutboundMessage,
)

try:
    import lark_oapi  # type: ignore[import-not-found]

    _LARK_AVAILABLE = True
except ImportError:
    _LARK_AVAILABLE = False
    lark_oapi = None  # type: ignore[assignment]

_log = logging.getLogger(__name__)

_CONNECTOR = "feishu"

# ---------------------------------------------------------------------------
# Pure translation helpers
# ---------------------------------------------------------------------------


def _feishu_chat_type(raw: str) -> str:
    """Map Feishu chat_type strings to our canonical 'dm' | 'group'."""
    return "dm" if raw == "p2p" else "group"


def feishu_event_to_inbound(event_dict: dict[str, Any]) -> InboundMessage:
    """Translate a Feishu v2 ``im.message.receive_v1`` event dict to InboundMessage.

    Key paths used:
    - workspace_id  : header.tenant_key
    - external_account_id : event.sender.sender_id.open_id
    - provider_event_id   : header.event_id
    - chat_id       : event.message.chat_id
    - chat_type     : event.message.chat_type  (p2p → "dm", group → "group")
    - text          : json.loads(event.message.content)["text"]
    """
    header = event_dict["header"]
    evt = event_dict["event"]
    msg = evt["message"]

    workspace_id = header["tenant_key"]
    external_account_id = evt["sender"]["sender_id"]["open_id"]
    provider_event_id = header["event_id"]
    chat_id = msg["chat_id"]
    chat_type = _feishu_chat_type(msg["chat_type"])

    # Extract text from JSON-encoded content
    content = msg.get("content", "{}")
    try:
        text: str | None = json.loads(content).get("text")
    except (json.JSONDecodeError, AttributeError):
        text = None

    chat = ChatRef(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        chat_id=chat_id,
        chat_type=chat_type,  # type: ignore[arg-type]
    )

    return InboundMessage(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        external_account_id=external_account_id,
        provider_event_id=provider_event_id,
        chat=chat,
        kind="message",
        text=text,
        action=None,
        raw=event_dict,
    )


def feishu_card_action_to_inbound(callback_dict: dict[str, Any]) -> InboundMessage:
    """Translate a Feishu v2 card-action callback dict to InboundMessage.

    Key paths used:
    - workspace_id         : header.tenant_key
    - external_account_id  : event.operator.open_id
    - provider_event_id    : header.event_id
    - chat_id              : event.context.open_chat_id
    - source message_id    : event.context.open_message_id
    - token                : event.action.value.token
    """
    header = callback_dict["header"]
    evt = callback_dict["event"]
    context = evt["context"]

    workspace_id = header["tenant_key"]
    external_account_id = evt["operator"]["open_id"]
    provider_event_id = header["event_id"]
    chat_id = context["open_chat_id"]
    message_id = context["open_message_id"]
    token = evt["action"]["value"]["token"]

    source_ref = MessageRef(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        chat_id=chat_id,
        message_id=message_id,
    )
    action = CardActionInbound(source_message_ref=source_ref, token=token)

    chat = ChatRef(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        chat_id=chat_id,
        chat_type="dm",  # card callbacks don't always reveal chat_type; default dm
    )

    return InboundMessage(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        external_account_id=external_account_id,
        provider_event_id=provider_event_id,
        chat=chat,
        kind="card_action",
        text=None,
        action=action,
        raw=callback_dict,
    )


def outbound_card_to_feishu(card: OutboundCard) -> dict[str, Any]:
    """Convert an OutboundCard to a Feishu interactive card v2 dict.

    Output structure:
    {
      "schema": "2.0",
      "config": {"wide_screen_mode": true},
      "header": {"title": {"tag": "plain_text", "content": "<title>"}},
      "body": {"elements": [...]},
      "actions": [{"tag": "action", "actions": [...buttons...]}]
    }

    Each button value dict contains ONLY {"token": "<token>"} as required by
    the Feishu card action protocol.
    """
    # Build body elements
    elements: list[dict[str, Any]] = []

    # Main body text
    if card.body:
        elements.append(
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": card.body},
            }
        )

    # Additional sections
    for section in card.sections:
        if section.title:
            elements.append(
                {
                    "tag": "markdown",
                    "content": f"**{section.title}**",
                }
            )
        if section.body:
            elements.append(
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": section.body},
                }
            )

    # Footer
    if card.footer:
        elements.append(
            {
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": card.footer}],
            }
        )

    # Build action buttons
    buttons: list[dict[str, Any]] = []
    for ca in card.actions:
        buttons.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": ca.label},
                "type": ca.style,
                "value": {"token": ca.token},
            }
        )

    result: dict[str, Any] = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": card.title},
        },
        "body": {"elements": elements},
    }

    if buttons:
        result["actions"] = [{"tag": "action", "actions": buttons}]
    else:
        result["actions"] = []

    return result


# ---------------------------------------------------------------------------
# AES-256-CBC verification helper
# ---------------------------------------------------------------------------


def verify_event(raw_body: bytes, config: "GatewayConfig") -> bool:
    """Return True iff the body is correctly encrypted and carries the right verification token.

    Feishu AES-256-CBC scheme:
    - key = sha256(feishu_encrypt_key.encode()).digest()  (32 bytes)
    - body JSON: {"encrypt": "<base64(iv + ciphertext)>"}
    - iv = first 16 bytes of decoded data; ciphertext = remainder
    - PKCS7 unpad the decrypted plaintext
    - Parse inner JSON; check header.token (v2) or top-level token
    """
    try:
        import base64

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        if not config.feishu_encrypt_key or not config.feishu_verification_token:
            return False

        body = json.loads(raw_body)
        encrypted = base64.b64decode(body["encrypt"])
        key = hashlib.sha256(config.feishu_encrypt_key.encode()).digest()
        iv = encrypted[:16]
        ciphertext = encrypted[16:]
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        # PKCS7 unpad
        pad_len = padded[-1]
        plaintext = padded[:-pad_len]
        event = json.loads(plaintext)
        # Extract token: check header.token (v2) then top-level token
        token = event.get("header", {}).get("token") or event.get("token")
        return token == config.feishu_verification_token
    except Exception:
        return False


# ---------------------------------------------------------------------------
# FeishuConnector — WebSocket lifecycle
# ---------------------------------------------------------------------------


class FeishuConnector:
    """Feishu/Lark connector implementing the MessageConnector protocol.

    WebSocket connections are managed with automatic exponential-backoff
    reconnection.  The ``ws_client_factory`` and ``sleep`` parameters are
    injectable for testing without requiring the ``lark_oapi`` SDK.
    """

    name: str = _CONNECTOR
    capabilities: ConnectorCapabilities = ConnectorCapabilities(
        supports_edit_in_place_message=True,
        supports_edit_in_place_card=True,
        supports_interactive_cards=True,
        max_message_chars=10000,
    )

    def __init__(
        self,
        config: "GatewayConfig",
        *,
        ws_client_factory: Callable[[Any], Any] | None = None,
        sleep: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
    ) -> None:
        self._config = config
        self._sleep = sleep
        self._ws_client_factory = ws_client_factory or self._default_factory
        self._on_inbound: Callable[[InboundMessage], Coroutine[Any, Any, None]] | None = None
        self._client: Any = None
        self._running = False
        self._connected = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_factory(self, event_handler: Any) -> Any:
        """Build a real lark_oapi WebSocket client.  Only called when creds are set."""
        if not _LARK_AVAILABLE:
            raise RuntimeError(
                "lark_oapi is not installed. "
                "Add 'lark-oapi' to your dependencies or provide a ws_client_factory."
            )
        # Lazily import to avoid hard SDK dependency at module level.
        from lark_oapi.ws import Client  # type: ignore[import-not-found]

        client = (
            Client.builder()
            .app_id(self._config.feishu_app_id or "")
            .app_secret(self._config.feishu_app_secret or "")
            .event_handler(event_handler)
            .build()
        )
        return client

    async def _handle_event(self, event_dict: dict[str, Any]) -> None:
        """Dispatch a raw Feishu event dict to the on_inbound callback."""
        if self._on_inbound is None:
            return
        try:
            event_type = event_dict.get("header", {}).get("event_type", "")
            # card.action.trigger is the Feishu v2 card-interaction event type.
            # Also check if event.action is present (alternative detection).
            if event_type == "card.action.trigger" or (
                event_dict.get("event", {}).get("action") is not None
                and event_dict.get("event", {}).get("message") is None
            ):
                msg = feishu_card_action_to_inbound(event_dict)
            else:
                msg = feishu_event_to_inbound(event_dict)
            await self._on_inbound(msg)
        except Exception:
            _log.exception("Error handling Feishu event: %r", event_dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        on_inbound: Callable[[InboundMessage], Coroutine[Any, Any, None]],
    ) -> None:
        """Connect to Feishu WS, handling reconnection with exponential backoff."""
        self._on_inbound = on_inbound
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                self._client = self._ws_client_factory(self._handle_event)
                self._connected = True
                await self._client.start()
                # If start() returned normally, connection closed cleanly
                self._connected = False
                backoff = 1.0  # reset on clean disconnect
            except Exception:
                self._connected = False
                if not self._running:
                    break
                _log.warning(
                    "Feishu WS disconnected; reconnecting in %.1fs", backoff
                )
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def stop(self) -> None:
        """Gracefully stop the connector."""
        self._running = False
        self._connected = False
        if self._client is not None:
            try:
                await self._client.stop()
            except Exception:
                _log.debug("Error during Feishu WS stop", exc_info=True)

    # ------------------------------------------------------------------
    # Sending — thin HTTP adapters (lark_oapi IM API)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        chat: ChatRef,
        msg: OutboundMessage,
        *,
        idempotency_key: str,
    ) -> MessageRef:
        if not _LARK_AVAILABLE:
            raise RuntimeError("lark_oapi not installed")
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import (  # type: ignore[import-not-found]
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(
            self._config.feishu_app_id or ""
        ).app_secret(
            self._config.feishu_app_secret or ""
        ).build()

        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat.chat_id)
            .receive_id_type("chat_id")
            .msg_type("text")
            .content(json.dumps({"text": msg.text}))
            .uuid(idempotency_key)
            .build()
        )
        req = CreateMessageRequest.builder().request_body(body).build()
        resp = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.im.v1.message.create(req)
        )
        message_id: str = resp.data.message_id if resp.data else idempotency_key
        return MessageRef(
            connector=self.name,
            workspace_id=chat.workspace_id,
            chat_id=chat.chat_id,
            message_id=message_id,
        )

    async def update_message(self, ref: MessageRef, msg: OutboundMessage) -> None:
        if not _LARK_AVAILABLE:
            raise RuntimeError("lark_oapi not installed")
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import (  # type: ignore[import-not-found]
            UpdateMessageRequest,
            UpdateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(
            self._config.feishu_app_id or ""
        ).app_secret(
            self._config.feishu_app_secret or ""
        ).build()

        body = (
            UpdateMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": msg.text}))
            .build()
        )
        req = (
            UpdateMessageRequest.builder()
            .message_id(ref.message_id)
            .request_body(body)
            .build()
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.im.v1.message.update(req)
        )

    async def send_card(
        self,
        chat: ChatRef,
        card: OutboundCard,
        *,
        idempotency_key: str,
    ) -> MessageRef:
        if not _LARK_AVAILABLE:
            raise RuntimeError("lark_oapi not installed")
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import (  # type: ignore[import-not-found]
            CreateMessageRequest,
            CreateMessageRequestBody,
        )

        client = lark.Client.builder().app_id(
            self._config.feishu_app_id or ""
        ).app_secret(
            self._config.feishu_app_secret or ""
        ).build()

        card_dict = outbound_card_to_feishu(card)
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat.chat_id)
            .receive_id_type("chat_id")
            .msg_type("interactive")
            .content(json.dumps(card_dict))
            .uuid(idempotency_key)
            .build()
        )
        req = CreateMessageRequest.builder().request_body(body).build()
        resp = await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.im.v1.message.create(req)
        )
        message_id = resp.data.message_id if resp.data else idempotency_key
        return MessageRef(
            connector=self.name,
            workspace_id=chat.workspace_id,
            chat_id=chat.chat_id,
            message_id=message_id,
        )

    async def update_card(self, ref: MessageRef, card: OutboundCard) -> None:
        if not _LARK_AVAILABLE:
            raise RuntimeError("lark_oapi not installed")
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.api.im.v1 import (  # type: ignore[import-not-found]
            PatchMessageRequest,
            PatchMessageRequestBody,
        )

        client = lark.Client.builder().app_id(
            self._config.feishu_app_id or ""
        ).app_secret(
            self._config.feishu_app_secret or ""
        ).build()

        card_dict = outbound_card_to_feishu(card)
        body = (
            PatchMessageRequestBody.builder()
            .msg_type("interactive")
            .content(json.dumps(card_dict))
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(ref.message_id)
            .request_body(body)
            .build()
        )
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: client.im.v1.message.patch(req)
        )

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def health(self) -> ConnectorHealth:
        if not self._running:
            return ConnectorHealth(
                name=self.name,
                state="unhealthy",
                detail="connector not started",
            )
        if self._connected:
            return ConnectorHealth(
                name=self.name,
                state="healthy",
                detail="WS connected",
            )
        return ConnectorHealth(
            name=self.name,
            state="degraded",
            detail="WS reconnecting",
        )
