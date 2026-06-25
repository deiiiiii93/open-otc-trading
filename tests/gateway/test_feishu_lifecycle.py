"""Tests for FeishuConnector WebSocket lifecycle: start/stop and reconnect."""
from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from app.services.gateway.connectors.feishu import (
    FeishuConnector,
    feishu_card_action_to_inbound,
)
from app.services.gateway.config import GatewayConfig
from app.services.gateway.types import InboundMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config() -> GatewayConfig:
    return GatewayConfig(
        gateway_default_desk_user="desk_user",
        gateway_linking_code_ttl_s=300,
        gateway_card_action_ttl_s=300,
        gateway_max_inbound_chars=4096,
        gateway_max_queued_per_chat=20,
        gateway_queue_max_age_s=3600,
        gateway_dedupe_ttl_s=60,
        gateway_dedupe_lease_s=10,
        gateway_lock_lease_s=10,
        gateway_code_issue_per_min=10,
        gateway_flush_interval_ms=200,
        gateway_flush_chars=500,
        gateway_web_base_url=None,
        gateway_enabled_connectors="feishu",
        feishu_app_id="cli_abc",
        feishu_app_secret="secret_abc",
        feishu_verification_token="vtoken",
        feishu_encrypt_key="enckey",
    )


# ---------------------------------------------------------------------------
# Fake WS client
# ---------------------------------------------------------------------------


class FakeWsClient:
    """Controllable stub for the Feishu WS client.

    - ``start()`` records the call and blocks until ``stop()`` is called,
      unless ``_fail_on_start`` is True, in which case it raises ConnectionError.
    - ``stop()`` records the call and unblocks ``start()``.
    - ``feed(event_dict)`` invokes the registered event handler so tests can
      inject inbound events.
    """

    def __init__(self, event_handler: Callable) -> None:
        self._handler = event_handler
        self.started = False
        self.stopped = False
        self._fail_on_start = False
        self._done = asyncio.Event()

    async def start(self) -> None:
        self.started = True
        if self._fail_on_start:
            raise ConnectionError("simulated WS disconnect")
        # Block until stop() is called (simulate a long-running connection)
        await self._done.wait()

    async def stop(self) -> None:
        self.stopped = True
        self._done.set()  # unblock start()

    async def feed(self, event_dict: dict[str, Any]) -> None:
        """Inject an event dict as if it arrived from Feishu."""
        await self._handler(event_dict)


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


class FakeClientFactory:
    """Factory that produces FakeWsClient instances and keeps a log of them."""

    def __init__(self, *, fail_first: int = 0) -> None:
        self.clients: list[FakeWsClient] = []
        self._fail_first = fail_first  # how many start() calls should fail

    def __call__(self, event_handler: Callable) -> FakeWsClient:
        client = FakeWsClient(event_handler)
        idx = len(self.clients)
        if idx < self._fail_first:
            client._fail_on_start = True
        self.clients.append(client)
        return client

    @property
    def latest(self) -> FakeWsClient:
        return self.clients[-1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MESSAGE_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_lc_001",
        "event_type": "im.message.receive_v1",
        "tenant_key": "TENANT_LC",
        "app_id": "cli_abc",
        "token": "VTOKEN",
    },
    "event": {
        "sender": {
            "sender_id": {"open_id": "ou_lc_user"},
        },
        "message": {
            "message_id": "om_lc_001",
            "chat_id": "oc_lc_chat",
            "chat_type": "p2p",
            "message_type": "text",
            "content": '{"text": "lifecycle test"}',
        },
    },
}

CARD_EVENT = {
    "schema": "2.0",
    "header": {
        "event_id": "evt_card_lc_001",
        "event_type": "card.action.trigger",
        "tenant_key": "TENANT_LC",
        "app_id": "cli_abc",
        "token": "VTOKEN",
    },
    "event": {
        "operator": {"open_id": "ou_lc_user"},
        "token": "card_tok_lc",
        "action": {
            "value": {"token": "card_tok_lc"},
            "tag": "button",
        },
        "context": {
            "open_message_id": "om_lc_card_src",
            "open_chat_id": "oc_lc_chat",
            "app_id": "cli_abc",
        },
    },
}


# ---------------------------------------------------------------------------
# Tests: basic start / feed / stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_registers_callback():
    """start() runs without error and the client's start() is called."""
    factory = FakeClientFactory()
    connector = FeishuConnector(_make_config(), ws_client_factory=factory, sleep=asyncio.sleep)

    received: list[InboundMessage] = []

    async def on_inbound(msg: InboundMessage) -> None:
        received.append(msg)

    # Run start() in a task — it loops; FakeWsClient.start() blocks until stop().
    task = asyncio.create_task(connector.start(on_inbound))
    # Yield to the event loop so the task gets to run and build the client
    for _ in range(5):
        await asyncio.sleep(0)

    # Now stop — this unblocks the client's start() and halts the loop
    await connector.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert len(factory.clients) >= 1
    assert factory.clients[0].started is True


@pytest.mark.asyncio
async def test_feed_message_event_calls_on_inbound():
    """Feeding a message event via the client triggers on_inbound with kind='message'."""
    factory = FakeClientFactory()

    # We need to intercept the client after it's built so we can feed it an event.
    # Use a custom factory that pauses start() until we feed the event.
    inbound_event_fed = asyncio.Event()
    client_built = asyncio.Event()

    class PausingClient:
        def __init__(self, handler: Callable) -> None:
            self._handler = handler
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True
            client_built.set()
            # Wait until test has fed the event
            await inbound_event_fed.wait()

        async def stop(self) -> None:
            self.stopped = True
            inbound_event_fed.set()  # unblock start() if it's waiting

        async def feed(self, event_dict: dict) -> None:
            await self._handler(event_dict)

    the_client: list[PausingClient] = []

    def pausing_factory(handler: Callable) -> PausingClient:
        c = PausingClient(handler)
        the_client.append(c)
        return c

    connector = FeishuConnector(_make_config(), ws_client_factory=pausing_factory, sleep=asyncio.sleep)

    received: list[InboundMessage] = []

    async def on_inbound(msg: InboundMessage) -> None:
        received.append(msg)

    task = asyncio.create_task(connector.start(on_inbound))
    await client_built.wait()  # wait until the client is built and start() is called

    # Feed a message event
    await the_client[0].feed(MESSAGE_EVENT)

    await connector.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert len(received) == 1
    assert received[0].kind == "message"
    assert received[0].text == "lifecycle test"
    assert received[0].connector == "feishu"
    assert received[0].workspace_id == "TENANT_LC"


@pytest.mark.asyncio
async def test_feed_card_event_calls_on_inbound_as_card_action():
    """Feeding a card.action.trigger event produces kind='card_action'."""
    client_built = asyncio.Event()
    inbound_event_fed = asyncio.Event()

    class PausingClient:
        def __init__(self, handler: Callable) -> None:
            self._handler = handler
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True
            client_built.set()
            await inbound_event_fed.wait()

        async def stop(self) -> None:
            self.stopped = True
            inbound_event_fed.set()

        async def feed(self, event_dict: dict) -> None:
            await self._handler(event_dict)

    the_client: list[PausingClient] = []

    def pausing_factory(handler: Callable) -> PausingClient:
        c = PausingClient(handler)
        the_client.append(c)
        return c

    connector = FeishuConnector(_make_config(), ws_client_factory=pausing_factory, sleep=asyncio.sleep)

    received: list[InboundMessage] = []

    async def on_inbound(msg: InboundMessage) -> None:
        received.append(msg)

    task = asyncio.create_task(connector.start(on_inbound))
    await client_built.wait()

    await the_client[0].feed(CARD_EVENT)

    await connector.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert len(received) == 1
    assert received[0].kind == "card_action"
    assert received[0].action is not None
    assert received[0].action.token == "card_tok_lc"


@pytest.mark.asyncio
async def test_stop_halts_reconnect_loop():
    """stop() causes the reconnect loop to terminate without reconnecting."""
    factory = FakeClientFactory()

    connector = FeishuConnector(
        _make_config(),
        ws_client_factory=factory,
        sleep=asyncio.sleep,
    )

    received: list[InboundMessage] = []

    async def on_inbound(msg: InboundMessage) -> None:
        received.append(msg)

    task = asyncio.create_task(connector.start(on_inbound))
    # Yield to the event loop so the task builds the client and enters client.start()
    for _ in range(5):
        await asyncio.sleep(0)

    await connector.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # After stop(), the loop should not keep building new clients indefinitely
    # We just verify that the last client had stop() called on it or stop was requested
    assert not connector._running


@pytest.mark.asyncio
async def test_reconnect_on_disconnect_calls_sleep():
    """On client.start() failure, connector sleeps before reconnecting."""
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)
        # Don't actually sleep — just record and return immediately

    # Let first client fail, second succeed
    fail_then_succeed = asyncio.Event()

    call_count = 0

    class CountingClient:
        def __init__(self, handler: Callable, *, fail: bool) -> None:
            self._handler = handler
            self._fail = fail
            self.started = False
            self.stopped = False

        async def start(self) -> None:
            self.started = True
            if self._fail:
                raise ConnectionError("simulated disconnect")
            # Succeed: block briefly then let stop() cancel
            fail_then_succeed.set()
            await asyncio.sleep(10)  # will be cancelled by task.cancel()

        async def stop(self) -> None:
            self.stopped = True

    clients_built: list[CountingClient] = []

    def counting_factory(handler: Callable) -> CountingClient:
        nonlocal call_count
        fail = call_count == 0  # first client fails
        call_count += 1
        c = CountingClient(handler, fail=fail)
        clients_built.append(c)
        return c

    connector = FeishuConnector(
        _make_config(),
        ws_client_factory=counting_factory,
        sleep=fake_sleep,
    )

    async def on_inbound(msg: InboundMessage) -> None:
        pass

    task = asyncio.create_task(connector.start(on_inbound))

    # Wait until second client's start() is running (fail_then_succeed is set)
    await asyncio.wait_for(fail_then_succeed.wait(), timeout=2.0)

    await connector.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    # Sleep should have been called once (after first failure, before reconnect)
    assert len(sleep_calls) >= 1
    assert sleep_calls[0] == 1.0  # initial backoff


@pytest.mark.asyncio
async def test_backoff_doubles_on_repeated_failure():
    """Backoff delay doubles on each consecutive failure (1.0, 2.0, 4.0…)."""
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    n_failures = 3
    call_count = 0

    class FailingThenDoneClient:
        def __init__(self, handler: Callable, *, fail: bool) -> None:
            self._handler = handler
            self._fail = fail
            self.started = False
            self.stopped = False
            self._done = asyncio.Event()

        async def start(self) -> None:
            self.started = True
            if self._fail:
                raise ConnectionError("fail")
            # Block until stop() is called
            await self._done.wait()

        async def stop(self) -> None:
            self.stopped = True
            self._done.set()

    clients_built: list[FailingThenDoneClient] = []

    def factory(handler: Callable) -> FailingThenDoneClient:
        nonlocal call_count
        fail = call_count < n_failures
        call_count += 1
        c = FailingThenDoneClient(handler, fail=fail)
        clients_built.append(c)
        return c

    connector = FeishuConnector(
        _make_config(),
        ws_client_factory=factory,
        sleep=fake_sleep,
    )

    async def on_inbound(msg: InboundMessage) -> None:
        pass

    task = asyncio.create_task(connector.start(on_inbound))

    # Wait until we have at least n_failures sleeps recorded
    for _ in range(50):
        await asyncio.sleep(0)
        if len(sleep_calls) >= n_failures:
            break

    await connector.stop()
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass

    assert len(sleep_calls) >= n_failures
    # Verify exponential growth: each sleep doubles
    for i in range(1, len(sleep_calls)):
        assert sleep_calls[i] == sleep_calls[i - 1] * 2, (
            f"Expected backoff to double: {sleep_calls}"
        )


@pytest.mark.asyncio
async def test_health_not_running():
    """health() returns unhealthy when connector has not been started."""
    connector = FeishuConnector(_make_config())
    h = await connector.health()
    assert h.state == "unhealthy"
    assert h.name == "feishu"


@pytest.mark.asyncio
async def test_connector_name():
    connector = FeishuConnector(_make_config())
    assert connector.name == "feishu"


@pytest.mark.asyncio
async def test_connector_capabilities_interactive_cards():
    connector = FeishuConnector(_make_config())
    assert connector.capabilities.supports_interactive_cards is True


@pytest.mark.asyncio
async def test_connector_capabilities_edit_message():
    connector = FeishuConnector(_make_config())
    assert connector.capabilities.supports_edit_in_place_message is True


@pytest.mark.asyncio
async def test_connector_capabilities_edit_card():
    connector = FeishuConnector(_make_config())
    assert connector.capabilities.supports_edit_in_place_card is True


@pytest.mark.asyncio
async def test_connector_capabilities_max_chars():
    connector = FeishuConnector(_make_config())
    assert connector.capabilities.max_message_chars == 10000
