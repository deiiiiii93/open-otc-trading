"""TDD tests for StreamRenderer — Task 11b: approval-card path.

Tests cover:
(a) done event with pending_action in event data → placeholder card sent, then actionable card updated
(b) both action_required and done for same message_id → only one card set sent (idempotent)
(c) error event → text notice message sent (with web deep-link if configured)
"""
from __future__ import annotations

import dataclasses

import pytest

from app.models import GatewayBinding
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import AgentEvent, ChatRef, ConnectorCapabilities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binding(session) -> GatewayBinding:
    b = GatewayBinding(
        provider="fake",
        external_account_id="ou_cards_test",
        workspace_id="tk_cards",
        desk_user="trader_bob",
        persona="trader",
        status="active",
    )
    session.add(b)
    session.flush()
    return b


def _make_chat() -> ChatRef:
    return ChatRef(
        connector="fake",
        workspace_id="tk_cards",
        chat_id="chat_cards_001",
        chat_type="dm",
    )


def _book_position_action(action_id: str = "intr_001:0") -> dict:
    """Return a dict that can be validated as AgentActionProposal for book_position."""
    return {
        "id": action_id,
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


# ---------------------------------------------------------------------------
# (a) done event with pending_action → placeholder → actionable card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_done_with_pending_action_sends_card(db_session, db_settings):
    """A done event that carries pending_actions triggers card sending.

    Expected sequence in connector.outbox:
    1. send_card (placeholder "Processing...")
    2. update_card (full actionable card with Approve/Reject buttons)
    """
    from app.services.gateway.coalescer import StreamRenderer

    # Need a db-seeded AgentThread + AgentMessage to satisfy foreign-key constraints
    from app.models import AgentThread, AgentMessage

    thread = AgentThread(title="test thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    message = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        content="I will book the position.",
        meta={},
    )
    db_session.add(message)
    db_session.flush()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=100,
        gateway_flush_chars=10000,
        gateway_card_action_ttl_s=1800,
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    action_data = _book_position_action()
    events_list = [
        AgentEvent(type="token", data={"content": "I will book the position."}),
        AgentEvent(type="done", data={
            "thread_id": thread.id,
            "message_id": message.id,
            "pending_actions": [action_data],
        }),
    ]

    async def events():
        for e in events_list:
            yield e

    await renderer.render_turn(db_session, binding, chat, events())

    card_sends = [e for e in connector.outbox if e["type"] == "card"]
    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]

    assert len(card_sends) == 1, f"Expected 1 send_card (placeholder), got {len(card_sends)}: {connector.outbox}"
    assert len(card_updates) == 1, f"Expected 1 update_card (actionable), got {len(card_updates)}: {connector.outbox}"

    # Placeholder card has "Processing..."
    assert "Processing" in card_sends[0]["card"].body

    # Updated actionable card has Approve/Reject actions
    actionable = card_updates[0]["card"]
    assert len(actionable.actions) == 2, f"Expected 2 actions, got {actionable.actions}"
    labels = {a.label for a in actionable.actions}
    assert "Approve" in labels
    assert "Reject" in labels


# ---------------------------------------------------------------------------
# (b) Both action_required and done for the same message_id → idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_both_action_required_and_done_idempotent(db_session, db_settings):
    """If both action_required and done arrive for the same message_id,
    only one set of cards is sent.
    """
    from app.services.gateway.coalescer import StreamRenderer
    from app.models import AgentThread, AgentMessage

    thread = AgentThread(title="idem thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    message = AgentMessage(thread_id=thread.id, role="assistant", content="ok", meta={})
    db_session.add(message)
    db_session.flush()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=100,
        gateway_flush_chars=10000,
        gateway_card_action_ttl_s=1800,
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    action_data = _book_position_action()
    card_event_data = {
        "thread_id": thread.id,
        "message_id": message.id,
        "pending_actions": [action_data],
    }

    async def events():
        # action_required first, then done — both carry the same message_id
        yield AgentEvent(type="action_required", data=card_event_data)
        yield AgentEvent(type="done", data=card_event_data)

    await renderer.render_turn(db_session, binding, chat, events())

    card_sends = [e for e in connector.outbox if e["type"] == "card"]
    # Only one card set should have been sent (idempotent on message_id)
    assert len(card_sends) == 1, f"Expected 1 send_card (idempotent), got {len(card_sends)}: {connector.outbox}"


# ---------------------------------------------------------------------------
# (c) error event → text notice (with deep-link when configured)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_event_sends_notice_with_deeplink(db_session, db_settings):
    """An error event sends a text notice that includes the web deep-link."""
    from app.services.gateway.coalescer import StreamRenderer
    from app.models import AgentThread

    thread = AgentThread(title="err thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_flush_interval_ms=100,
        gateway_flush_chars=10000,
        gateway_web_base_url="http://desk.example.com",
    )
    binding = _make_binding(db_session)
    chat = _make_chat()

    renderer = StreamRenderer(connector=connector, settings=settings)

    async def events():
        yield AgentEvent(type="error", data={"thread_id": thread.id, "message": "something went wrong"})

    await renderer.render_turn(db_session, binding, chat, events())

    sends = [e for e in connector.outbox if e["type"] == "message"]
    assert len(sends) >= 1, f"Expected error notice message, got: {connector.outbox}"
    notice_text = sends[0]["msg"].text
    # Should include the web deep-link
    assert "desk.example.com" in notice_text, f"Expected deep-link in notice: {notice_text!r}"
