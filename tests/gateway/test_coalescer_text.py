"""TDD tests for StreamRenderer — Task 11a: text path.

Tests cover:
(a) token buffering: multiple token events → single send_message, then update_message per flush
(b) no-edit fallback: when supports_edit_in_place_message=False, no update_message calls
(c) chunking: text > max_message_chars → multiple send_message calls
(d) send retry on transient failure: connector raises once → retried → sleep called
"""
from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import GatewayBinding
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import AgentEvent, ChatRef, ConnectorCapabilities, OutboundMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binding(session, *, desk_user: str = "trader_alice") -> GatewayBinding:
    b = GatewayBinding(
        provider="fake",
        external_account_id="ou_text_test",
        workspace_id="tk_text",
        desk_user=desk_user,
        persona="trader",
        status="active",
    )
    session.add(b)
    session.flush()
    return b


def _make_chat() -> ChatRef:
    return ChatRef(
        connector="fake",
        workspace_id="tk_text",
        chat_id="chat_text_001",
        chat_type="dm",
    )


async def _events(*items: AgentEvent) -> AsyncIterator[AgentEvent]:
    for item in items:
        yield item


# ---------------------------------------------------------------------------
# (a) Token buffering — multiple tokens → send_message once, update_message on flush
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_buffering_single_edit_message(db_session, db_settings):
    """Multiple token events accumulate and are flushed as a single message chain.

    Expected behaviour:
    - First flush: one send_message call
    - Subsequent flushes: update_message on the same ref
    - At done: final update_message
    """
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    binding = _make_binding(db_session)
    chat = _make_chat()

    # Use a very short flush interval so time-based flush triggers quickly,
    # and a very large flush_chars threshold so it does NOT trigger on chars.
    # We'll drive time manually by overriding monotonic.
    _time = [0.0]

    def fake_monotonic() -> float:
        return _time[0]

    async def fake_sleep(s: float) -> None:
        pass

    # flush_interval_ms = 100ms, flush_chars = 10000 (won't trigger on chars here)
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=100,
        gateway_flush_chars=10000,
    )

    renderer = StreamRenderer(
        connector=connector,
        settings=settings,
        sleep=fake_sleep,
        monotonic=fake_monotonic,
    )

    async def events():
        # Emit token1 at t=0, no flush yet (elapsed=0 < 0.1s)
        yield AgentEvent(type="token", data={"content": "Hello "})
        # Advance time past flush interval (100ms = 0.1s)
        _time[0] = 0.15
        # Emit token2 — triggers flush because interval elapsed
        # This flush: last_ref is None → send_message → gets a ref
        yield AgentEvent(type="token", data={"content": "world"})
        # Advance time past another interval
        _time[0] = 0.35
        # Emit token3 — triggers another flush; last_ref is now set → update_message
        yield AgentEvent(type="token", data={"content": "!"})
        yield AgentEvent(type="done", data={})

    await renderer.render_turn(db_session, binding, chat, events())

    # Filter outbox by type
    sends = [e for e in connector.outbox if e["type"] == "message"]
    updates = [e for e in connector.outbox if e["type"] == "update_message"]

    # There should be exactly one send_message (first flush when last_ref is None)
    assert len(sends) == 1, f"Expected 1 send_message, got {len(sends)}: {connector.outbox}"
    # After the first send, the second time-based flush should update_message
    assert len(updates) >= 1, f"Expected ≥1 update_message, got {len(updates)}: {connector.outbox}"

    # Verify the structure:
    # - First flush (token2 arrives at t=0.15): sends "Hello world" via send_message
    # - Second flush (token3 arrives at t=0.35): sends "!" via update_message on the same ref
    assert sends[0]["msg"].text == "Hello world", f"First send: {sends[0]['msg'].text!r}"
    # The update should be to the same ref as the initial send
    assert updates[-1]["ref"] == sends[0]["ref"], "update should be on the same message ref"


# ---------------------------------------------------------------------------
# (b) No-edit fallback — single send at done, no update_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_single_send_fallback_no_edit(db_session, db_settings):
    """When supports_edit_in_place_message=False, no update_message calls.

    All tokens are batched and sent as a single message at done.
    """
    from app.services.gateway.coalescer import StreamRenderer

    # Override capabilities: no edit-in-place
    connector = FakeConnector()
    connector.capabilities = ConnectorCapabilities(
        supports_edit_in_place_message=False,
        supports_edit_in_place_card=False,
        supports_interactive_cards=True,
        max_message_chars=10000,
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=100,
        gateway_flush_chars=10000,
    )

    renderer = StreamRenderer(
        connector=connector,
        settings=settings,
    )

    events = _events(
        AgentEvent(type="token", data={"content": "Part one. "}),
        AgentEvent(type="token", data={"content": "Part two."}),
        AgentEvent(type="done", data={}),
    )

    await renderer.render_turn(db_session, binding, chat, events)

    sends = [e for e in connector.outbox if e["type"] == "message"]
    updates = [e for e in connector.outbox if e["type"] == "update_message"]

    # Exactly one send at done, no updates
    assert len(sends) == 1, f"Expected 1 send, got {len(sends)}: {connector.outbox}"
    assert len(updates) == 0, f"Expected 0 updates, got {len(updates)}: {connector.outbox}"
    assert "Part one. " in sends[0]["msg"].text
    assert "Part two." in sends[0]["msg"].text


# ---------------------------------------------------------------------------
# (c) Chunking at max_message_chars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chunking_at_max_chars(db_session, db_settings):
    """Text longer than max_message_chars is split into multiple send_message calls."""
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    connector.capabilities = ConnectorCapabilities(
        supports_edit_in_place_message=True,
        supports_edit_in_place_card=True,
        supports_interactive_cards=True,
        max_message_chars=20,  # very small limit
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,
        gateway_flush_chars=1,
    )

    renderer = StreamRenderer(connector=connector, settings=settings)

    # 50 chars total — should produce 3 chunks of ≤20 chars each
    long_text = "A" * 50

    events = _events(
        AgentEvent(type="token", data={"content": long_text}),
        AgentEvent(type="done", data={}),
    )

    await renderer.render_turn(db_session, binding, chat, events)

    sends = [e for e in connector.outbox if e["type"] == "message"]
    # With max_message_chars=20, 50 chars → ≥3 separate sends
    assert len(sends) >= 2, f"Expected multiple sends, got {len(sends)}: {connector.outbox}"
    # No single message should exceed the limit
    for entry in sends:
        assert len(entry["msg"].text) <= 20, f"Chunk too long: {entry['msg'].text!r}"


# ---------------------------------------------------------------------------
# (d) Send retry on transient failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_retry_on_failure(db_session, db_settings):
    """If send_message raises once, it is retried and sleep is called for backoff."""
    from app.services.gateway.coalescer import StreamRenderer

    call_count = [0]
    sleep_delays: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleep_delays.append(s)

    connector = FakeConnector()
    original_send = connector.send_message

    async def flaky_send(chat, msg, *, idempotency_key):
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("transient network error")
        return await original_send(chat, msg, idempotency_key=idempotency_key)

    connector.send_message = flaky_send

    binding = _make_binding(db_session)
    chat = _make_chat()

    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,
        gateway_flush_chars=1,
    )

    renderer = StreamRenderer(
        connector=connector,
        settings=settings,
        sleep=fake_sleep,
    )

    events = _events(
        AgentEvent(type="token", data={"content": "hello"}),
        AgentEvent(type="done", data={}),
    )

    await renderer.render_turn(db_session, binding, chat, events)

    # Should have retried: send was called at least twice
    assert call_count[0] >= 2, f"Expected retry, send called {call_count[0]} times"
    # Sleep was called for backoff
    assert len(sleep_delays) >= 1, "Expected at least one sleep call for backoff"
    # Final message made it through
    sends = [e for e in connector.outbox if e["type"] == "message"]
    assert len(sends) == 1


# ---------------------------------------------------------------------------
# (e) Token-bucket rate limiter — primary throttle (C1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_bucket_throttles_rapid_burst(db_session, db_settings):
    """More than `burst` rapid sends in zero elapsed time → bucket throttles.

    With burst=5 and the monotonic clock frozen at t=0 (no refill), a burst of
    8 separate sends consumes the 5 starter tokens immediately, then must SLEEP
    (via the injected sleep) to refill one token per send for the remaining 3.
    """
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    binding = _make_binding(db_session)
    chat = _make_chat()

    # Freeze monotonic at 0 → no passive refill ever happens.
    def frozen_monotonic() -> float:
        return 0.0

    sleep_calls: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleep_calls.append(s)

    # max_message_chars small so a single long token splits into many separate
    # send_message calls — each one consumes a bucket token.
    connector.capabilities = ConnectorCapabilities(
        supports_edit_in_place_message=True,
        supports_edit_in_place_card=True,
        supports_interactive_cards=True,
        max_message_chars=1,  # 1 char per chunk → 1 send per char
    )

    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,
        gateway_flush_chars=1,
    )

    renderer = StreamRenderer(
        connector=connector,
        settings=settings,
        sleep=fake_sleep,
        monotonic=frozen_monotonic,
    )

    # 8 chars → 8 chunked send_message calls (>5 burst).
    events = _events(
        AgentEvent(type="token", data={"content": "ABCDEFGH"}),
        AgentEvent(type="done", data={}),
    )

    await renderer.render_turn(db_session, binding, chat, events)

    sends = [e for e in connector.outbox if e["type"] == "message"]
    assert len(sends) == 8, f"Expected 8 chunked sends, got {len(sends)}: {connector.outbox}"

    # First 5 sends consume starter tokens (no sleep); the remaining 3 must each
    # wait for a refill → at least 3 throttle sleeps, total wait > 0.
    assert len(sleep_calls) >= 3, (
        f"Expected ≥3 bucket-throttle sleeps after a {len(sends)}-send burst, "
        f"got {len(sleep_calls)}: {sleep_calls}"
    )
    assert sum(sleep_calls) > 0, "Expected total throttle sleep time > 0"
    # Refill is 5 tokens/s → one token = 0.2s wait.
    assert any(abs(s - 0.2) < 1e-9 for s in sleep_calls), (
        f"Expected a 0.2s (one-token) refill wait, got: {sleep_calls}"
    )
