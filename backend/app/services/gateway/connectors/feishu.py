"""Feishu (Lark) connector for the IM message gateway.

The ``lark_oapi`` package is optional — all imports are guarded so this
module can be imported in environments where the SDK is not installed.
Translation functions (feishu_event_to_inbound, feishu_card_action_to_inbound,
outbound_card_to_feishu) are pure and have no SDK dependency.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import threading
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
    value = evt["action"]["value"] or {}

    source_ref = MessageRef(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        chat_id=chat_id,
        message_id=message_id,
    )
    chat = ChatRef(
        connector=_CONNECTOR,
        workspace_id=workspace_id,
        chat_id=chat_id,
        chat_type="dm",  # card callbacks don't always reveal chat_type; default dm
    )

    # A reply-option pick is behaviorally a typed message: replay its text as a
    # normal turn, and carry the source card ref + label so the dispatcher can
    # lock the card ("You chose: …"). An approval pick carries a one-time token.
    if "reply" in value:
        return InboundMessage(
            connector=_CONNECTOR,
            workspace_id=workspace_id,
            external_account_id=external_account_id,
            provider_event_id=provider_event_id,
            chat=chat,
            kind="message",
            text=value.get("reply"),
            action=None,
            raw=callback_dict,
            card_lock_ref=source_ref,
            card_lock_label=value.get("label"),
        )

    action = CardActionInbound(
        source_message_ref=source_ref, token=value["token"]
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


def _text_to_markdown_card(text: str) -> dict[str, Any]:
    """Render plain agent text as a minimal schema-2.0 card with a single
    markdown element.

    Feishu text bubbles are literally plain — they cannot render markdown and
    there is no markdown message type. Wrapping the reply in a headerless,
    buttonless card with a ``markdown`` element makes Feishu render bold,
    lists, tables, headings and links, while still reading as a lightweight
    rich-text bubble.
    """
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "body": {"elements": [{"tag": "markdown", "content": text}]},
    }


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

    # Footer — schema 2.0 removed the "note" element, so render the footer text
    # as a small markdown element instead.
    if card.footer:
        elements.append(
            {
                "tag": "markdown",
                "content": card.footer,
            }
        )

    # Schema-2.0 interactive buttons are ELEMENTS inside body.elements — there
    # is no top-level "actions" property in 2.0 (that was the 1.x layout). A 2.0
    # button fires its callback via a "behaviors" array rather than a "value"
    # field; the callback value comes back as event.action.value (consumed by
    # feishu_card_action_to_inbound).
    for ca in card.actions:
        # Approval buttons carry {"token": …}; reply-option buttons carry
        # {"reply": …, "label": …} so the pick can be replayed as a message and
        # its originating card locked with the chosen label.
        if ca.reply is not None:
            callback_value: dict[str, Any] = {"reply": ca.reply, "label": ca.label}
        else:
            callback_value = {"token": ca.token}
        elements.append(
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": ca.label},
                "type": ca.style,
                "behaviors": [{"type": "callback", "value": callback_value}],
            }
        )

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": card.title},
        },
        "body": {"elements": elements},
    }


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
        # PKCS7 unpad — validate before slicing
        pad_len = padded[-1]
        if pad_len < 1 or pad_len > 16 or pad_len > len(padded):
            return False
        if padded[-pad_len:] != bytes([pad_len]) * pad_len:
            return False
        plaintext = padded[:-pad_len]
        event = json.loads(plaintext)
        # Extract token: check header.token (v2) then top-level token
        token = event.get("header", {}).get("token") or event.get("token")
        return hmac.compare_digest(token or "", config.feishu_verification_token or "")
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
        self._loop_task: "asyncio.Task[None] | None" = None
        self._loop: "asyncio.AbstractEventLoop | None" = None
        self._sdk_loop: "asyncio.AbstractEventLoop | None" = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _default_factory(self, on_event: Any) -> Any:
        """Build a real lark_oapi WebSocket client.  Only called when creds are set.

        ``on_event`` is :meth:`_handle_event` — an async callable taking a raw
        Feishu event dict. The lark SDK delivers *typed* events through a
        synchronous :class:`EventDispatcherHandler` running on a worker thread,
        so each typed event is marshalled back to its raw dict (matching the
        webhook JSON our pure translators expect) and scheduled onto the
        connector's event loop via ``run_coroutine_threadsafe``.
        """
        if not _LARK_AVAILABLE:
            raise RuntimeError(
                "lark_oapi is not installed. "
                "Add 'lark-oapi' to your dependencies or provide a ws_client_factory."
            )
        # Lazily import to avoid hard SDK dependency at module level.
        import lark_oapi as lark  # type: ignore[import-not-found]
        from lark_oapi.ws import Client  # type: ignore[import-not-found]
        from lark_oapi.event.callback.model.p2_card_action_trigger import (  # type: ignore[import-not-found]
            P2CardActionTriggerResponse,
        )

        def _dispatch(typed_event: Any) -> None:
            loop = self._loop
            if loop is None:
                return
            try:
                raw = json.loads(lark.JSON.marshal(typed_event))
            except Exception:
                _log.exception("Failed to marshal Feishu event %r", typed_event)
                return
            # Hop from the SDK's worker thread back onto our event loop.
            asyncio.run_coroutine_threadsafe(on_event(raw), loop)

        def _on_message(data: Any) -> None:
            _dispatch(data)

        def _on_card(data: Any) -> Any:
            _dispatch(data)
            # The card-action callback must return a response object to ack.
            return P2CardActionTriggerResponse()

        handler = (
            lark.EventDispatcherHandler.builder(
                self._config.feishu_encrypt_key or "",
                self._config.feishu_verification_token or "",
            )
            .register_p2_im_message_receive_v1(_on_message)
            .register_p2_card_action_trigger(_on_card)
            .build()
        )
        return Client(
            self._config.feishu_app_id or "",
            self._config.feishu_app_secret or "",
            event_handler=handler,
            domain=lark.FEISHU_DOMAIN,
            auto_reconnect=False,
        )

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
                inbound = feishu_card_action_to_inbound(event_dict)
            else:
                inbound = feishu_event_to_inbound(event_dict)
        except Exception:
            _log.exception("Failed to translate Feishu event: %r", event_dict)
            return
        try:
            await self._on_inbound(inbound)
        except Exception:
            _log.exception("on_inbound handler failed for Feishu event: %r", event_dict)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(
        self,
        on_inbound: Callable[[InboundMessage], Coroutine[Any, Any, None]],
    ) -> None:
        """Register the callback and launch the WS receive loop in the background.

        Per the MessageConnector contract, ``start()`` returns once the
        connector is running; the reconnect loop runs as a background task
        until ``stop()`` is called. Awaiting the loop inline here would block
        the caller (e.g. the app-startup hook) forever, since the loop only
        exits on ``stop()``.
        """
        self._on_inbound = on_inbound
        self._running = True
        self._loop_task = asyncio.create_task(self._run_forever())

    async def _run_forever(self) -> None:
        """Connect to Feishu WS, handling reconnection with exponential backoff."""
        self._loop = asyncio.get_running_loop()
        backoff = 1.0
        while self._running:
            try:
                self._client = self._ws_client_factory(self._handle_event)
                self._connected = True
                await self._run_client(self._client)
                # If start() returned normally, connection closed cleanly
                self._connected = False
                backoff = 1.0  # reset on clean disconnect
                if not self._running:
                    break
                # Settle before reconnecting: a rapid clean-close cycle
                # (server accepts then immediately closes) must not busy-spin.
                _log.debug("Feishu WS clean close; reconnecting in %.1fs", backoff)
                await self._sleep(backoff)
            except Exception as exc:
                self._connected = False
                if not self._running:
                    break
                _log.warning(
                    "Feishu WS disconnected; reconnecting in %.1fs: %s",
                    backoff,
                    exc,
                )
                _log.debug("Feishu WS disconnect detail", exc_info=True)
                await self._sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    async def _run_client(self, client: Any) -> None:
        """Run the client's blocking receive loop until it disconnects.

        Injected test fakes expose an ``async def start()`` and are awaited
        directly. The real lark ``ws.Client.start()`` is a *blocking* call that
        drives its own event loop via a module-global ``loop`` captured at
        import time (``lark_oapi.ws.client.loop``). Inside an already-running
        async server that global is the server's loop, so calling ``start()``
        directly raises "This event loop is already running". We therefore run
        it on a dedicated thread with a fresh event loop, rebinding the SDK's
        module global to that loop. The SDK's synchronous event callbacks hop
        back to the server loop via ``run_coroutine_threadsafe`` (see
        :meth:`_default_factory`).
        """
        start = client.start
        if inspect.iscoroutinefunction(start):
            await start()
            return

        assert self._loop is not None
        main_loop = self._loop
        done = asyncio.Event()
        error: list[BaseException] = []

        def _thread_main() -> None:
            try:
                import lark_oapi.ws.client as _wc  # type: ignore[import-not-found]

                sdk_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(sdk_loop)
                # The SDK drives this module-global loop with run_until_complete;
                # point it at this thread's fresh (non-running) loop.
                _wc.loop = sdk_loop
                self._sdk_loop = sdk_loop
                client.start()  # blocks until the WS disconnects or errors
            except BaseException as exc:  # noqa: BLE001 — surface to reconnect loop
                error.append(exc)
            finally:
                main_loop.call_soon_threadsafe(done.set)

        thread = threading.Thread(target=_thread_main, name="feishu-ws", daemon=True)
        thread.start()
        await done.wait()
        if error:
            raise error[0]

    async def stop(self) -> None:
        """Gracefully stop the connector and cancel the background receive loop."""
        self._running = False
        self._connected = False
        if self._client is not None:
            # The real lark ws.Client exposes no public stop(); injected fakes
            # provide an async stop() to unblock their start(). Call it only when
            # present, and await it only when it returns an awaitable.
            stop = getattr(self._client, "stop", None)
            if stop is not None:
                try:
                    result = stop()
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    _log.debug("Error during Feishu WS stop", exc_info=True)
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            except Exception:
                _log.debug("Error awaiting cancelled Feishu loop task", exc_info=True)
            self._loop_task = None

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

        # Render the reply as a headerless markdown card so Feishu renders
        # markdown (bold, lists, tables, links) rather than literal characters.
        body = (
            CreateMessageRequestBody.builder()
            .receive_id(chat.chat_id)
            .msg_type("interactive")
            .content(json.dumps(_text_to_markdown_card(msg.text)))
            .uuid(idempotency_key)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = await asyncio.get_running_loop().run_in_executor(
            None, lambda: client.im.v1.message.create(req)
        )
        if not resp.success():
            raise RuntimeError(
                f"Feishu message.create (text) failed: code={resp.code} "
                f"msg={resp.msg} log_id={resp.get_log_id()}"
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
            PatchMessageRequest,
            PatchMessageRequestBody,
        )

        client = lark.Client.builder().app_id(
            self._config.feishu_app_id or ""
        ).app_secret(
            self._config.feishu_app_secret or ""
        ).build()

        # Replies are sent as interactive markdown cards (see send_message), so
        # in-place edits patch the card content rather than a text body.
        body = (
            PatchMessageRequestBody.builder()
            .content(json.dumps(_text_to_markdown_card(msg.text)))
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(ref.message_id)
            .request_body(body)
            .build()
        )
        resp = await asyncio.get_running_loop().run_in_executor(
            None, lambda: client.im.v1.message.patch(req)
        )
        if not resp.success():
            raise RuntimeError(
                f"Feishu message.patch (text update) failed: code={resp.code} "
                f"msg={resp.msg} log_id={resp.get_log_id()}"
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
            .msg_type("interactive")
            .content(json.dumps(card_dict))
            .uuid(idempotency_key)
            .build()
        )
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(body)
            .build()
        )
        resp = await asyncio.get_running_loop().run_in_executor(
            None, lambda: client.im.v1.message.create(req)
        )
        if not resp.success():
            raise RuntimeError(
                f"Feishu message.create (card) failed: code={resp.code} "
                f"msg={resp.msg} log_id={resp.get_log_id()}"
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
            .content(json.dumps(card_dict))
            .build()
        )
        req = (
            PatchMessageRequest.builder()
            .message_id(ref.message_id)
            .request_body(body)
            .build()
        )
        resp = await asyncio.get_running_loop().run_in_executor(
            None, lambda: client.im.v1.message.patch(req)
        )
        if not resp.success():
            raise RuntimeError(
                f"Feishu message.patch (card update) failed: code={resp.code} "
                f"msg={resp.msg} log_id={resp.get_log_id()}"
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
