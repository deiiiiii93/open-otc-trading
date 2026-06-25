"""TDD tests for StreamRenderer — Task 11d: mid-stream binding revocation.

Tests cover:
(a) revoke binding mid-stream → no further connector sends after revocation
(b) revoke mid-stream → 'gateway.revoked_midflight' audit row written
(c) revoke mid-stream with an existing message ref → update last message to "⚠ session ended"
"""
from __future__ import annotations

import dataclasses

import pytest

from app.models import GatewayBinding
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import AgentEvent, ChatRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binding(session, *, desk_user: str = "trader_dave") -> GatewayBinding:
    b = GatewayBinding(
        provider="fake",
        external_account_id="ou_revoke_test",
        workspace_id="tk_revoke",
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
        workspace_id="tk_revoke",
        chat_id="chat_revoke_001",
        chat_type="dm",
    )


def _revoke(session, binding: GatewayBinding) -> None:
    """Revoke a binding in the DB, simulating a mid-stream revocation."""
    import datetime
    binding.status = "revoked"
    binding.revoked_at = datetime.datetime.utcnow()
    session.flush()


# ---------------------------------------------------------------------------
# (a) Revocation stops further sends
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_mid_stream_stops_sends(db_session, db_settings):
    """Revoking a binding mid-stream stops all further connector sends.

    Sequence:
    1. token event → first flush → send_message (binding active)
    2. binding revoked (simulated in the generator)
    3. token event → revocation detected → NO further send
    """
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,  # flush immediately
        gateway_flush_chars=1,         # flush on every token
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    first_token_sent = []

    async def events():
        # First token — binding is active, should send
        yield AgentEvent(type="token", data={"content": "Before revocation"})
        # Record how many sends happened before revocation
        first_token_sent.append(len([e for e in connector.outbox if e["type"] == "message"]))
        # Simulate revocation mid-stream
        _revoke(db_session, binding)
        # Second token — binding is now revoked, should NOT send
        yield AgentEvent(type="token", data={"content": "After revocation"})
        yield AgentEvent(type="done", data={})

    await renderer.render_turn(db_session, binding, chat, events())

    sends = [e for e in connector.outbox if e["type"] == "message"]
    # Only the first token should have been sent
    assert first_token_sent, "Should have recorded sends after first token"
    # After revocation, no more sends with the revoked content
    after_revoke_content_sent = any(
        "After revocation" in e["msg"].text
        for e in sends
    )
    assert not after_revoke_content_sent, (
        f"'After revocation' content should NOT have been sent. outbox: {connector.outbox}"
    )


# ---------------------------------------------------------------------------
# (b) Revocation triggers audit event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_mid_stream_audit_event(db_session, db_settings):
    """Revoking mid-stream writes a 'gateway.revoked_midflight' audit event."""
    from app.models import AuditEvent
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,
        gateway_flush_chars=1,
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    async def events():
        yield AgentEvent(type="token", data={"content": "start"})
        _revoke(db_session, binding)
        yield AgentEvent(type="token", data={"content": "after"})
        yield AgentEvent(type="done", data={})

    await renderer.render_turn(db_session, binding, chat, events())

    audit_rows = db_session.query(AuditEvent).filter_by(
        event_type="gateway.revoked_midflight"
    ).all()
    assert len(audit_rows) == 1, (
        f"Expected 1 revoked_midflight audit event, got {len(audit_rows)}"
    )
    assert str(binding.id) == audit_rows[0].subject_id


# ---------------------------------------------------------------------------
# (c) Revocation updates last message to "⚠ session ended"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_mid_stream_edits_last_message(db_session, db_settings):
    """If a prior message ref exists, revocation updates it to '⚠ session ended'."""
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,
        gateway_flush_chars=1,
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    async def events():
        # First token creates a message ref
        yield AgentEvent(type="token", data={"content": "Hello"})
        # Revoke mid-stream
        _revoke(db_session, binding)
        # Next event — revocation detected, last_ref should be updated
        yield AgentEvent(type="token", data={"content": "should not appear"})
        yield AgentEvent(type="done", data={})

    await renderer.render_turn(db_session, binding, chat, events())

    updates = [e for e in connector.outbox if e["type"] == "update_message"]
    # The last message should be updated to the session-ended notice
    session_ended_updates = [
        e for e in updates
        if "session ended" in e["msg"].text.lower() or "⚠" in e["msg"].text
    ]
    assert len(session_ended_updates) >= 1, (
        f"Expected 'session ended' update_message, got updates: {updates}\noutbox: {connector.outbox}"
    )


# ---------------------------------------------------------------------------
# (d) Revocation between a text flush and a card send → NO card sent (per-send)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_revoked_between_flush_and_card_blocks_card(db_session, db_settings):
    """Revocation re-check runs before EVERY send, not just per-event.

    Sequence:
    1. token event flushes a text message (binding active) → send_message
    2. binding revoked
    3. done event carries a pending_action → the card send is attempted, but the
       per-send revocation re-check in _guarded_send must block it.

    Asserts NO send_card / update_card occur after revocation, and the audit row
    exists.
    """
    from app.models import AgentThread, AgentMessage, AuditEvent
    from app.services.gateway.coalescer import StreamRenderer

    thread = AgentThread(title="revoke-card thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    message = AgentMessage(thread_id=thread.id, role="assistant", content="booking", meta={})
    db_session.add(message)
    db_session.flush()

    action_data = {
        "id": "intr_001:0",
        "tool_name": "book_position",
        "label": "Book position",
        "summary": "Book a vanilla call",
        "payload": {
            "product": {
                "product_family": "vanilla",
                "quantark_class": "EuropeanOption",
                "underlying": "000300.SH",
                "currency": "CNY",
                "terms": {"strike": 4200.0, "expiry": "2026-12-31"},
            },
            "quantity": 100.0,
            "portfolio_id": 42,
        },
        "requires_confirmation": True,
        "status": "pending",
    }

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=0,
        gateway_flush_chars=1,
        gateway_card_action_ttl_s=1800,
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    async def events():
        # First a text flush while active.
        yield AgentEvent(type="token", data={"content": "Booking now"})
        # Revoke AFTER the text flush but BEFORE the card.
        _revoke(db_session, binding)
        # done carries a pending action → card send attempted but must be blocked.
        yield AgentEvent(type="done", data={
            "thread_id": thread.id,
            "message_id": message.id,
            "pending_actions": [action_data],
        })

    await renderer.render_turn(db_session, binding, chat, events())

    card_sends = [e for e in connector.outbox if e["type"] == "card"]
    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]
    assert len(card_sends) == 0, f"No card should be sent after revocation: {connector.outbox}"
    assert len(card_updates) == 0, f"No card update after revocation: {connector.outbox}"

    audit_rows = db_session.query(AuditEvent).filter_by(
        event_type="gateway.revoked_midflight"
    ).all()
    assert len(audit_rows) == 1, f"Expected 1 revoked_midflight audit, got {len(audit_rows)}"
