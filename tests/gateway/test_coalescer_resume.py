"""TDD tests for StreamRenderer — Task 11c: resume result rendering.

Tests cover:
(a) ResumeOk → mark_resolved, update card to resolved, post content
(b) ResumeOk with chained pending action → fresh approval card
(c) ResumeRaised → mark_unknown, update card to unknown/buttonless
(d) render_claim_error(already_resolved) → "already handled" card update
(e) render_claim_error(expired) → "expired" card update
(f) render_claim_error(bad_token/source_mismatch) → generic error card update
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta

import pytest

from app.models import GatewayBinding, GatewayCardAction
from app.services.gateway.actions import ClaimError
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import MessageRef


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_binding(session, *, desk_user: str = "trader_alice") -> GatewayBinding:
    b = GatewayBinding(
        provider="fake",
        external_account_id="ou_resume_test",
        workspace_id="tk_resume",
        desk_user=desk_user,
        persona="trader",
        status="active",
    )
    session.add(b)
    session.flush()
    return b


def _make_card_action(session, binding, *, thread_id: int = 10, message_id: int = 20) -> GatewayCardAction:
    """Create a GatewayCardAction in 'resolving' status (as claimed by verify_and_claim)."""
    from datetime import datetime, timedelta
    row = GatewayCardAction(
        token="tok_resume_test",
        out_connector="fake",
        out_workspace_id="tk_resume",
        out_chat_id="chat_resume",
        out_message_id="msg_resume_card",
        binding_id=binding.id,
        thread_id=thread_id,
        message_id=message_id,
        action_id="intr_001:0",
        decision="confirm",
        expires_at=datetime.utcnow() + timedelta(hours=1),
        status="resolving",  # as if verify_and_claim already claimed it
    )
    session.add(row)
    session.flush()
    return row


def _make_clicked_ref() -> MessageRef:
    return MessageRef(
        connector="fake",
        workspace_id="tk_resume",
        chat_id="chat_resume",
        message_id="msg_resume_card",
    )


# ---------------------------------------------------------------------------
# (a) ResumeOk → mark_resolved, resolved card, post content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_ok_resolves_card_and_posts_content(db_session, db_settings):
    """ResumeOk → GatewayCardAction.status='resolved', card updated to resolved, content sent."""
    from app.models import AgentThread, AgentMessage
    from app.services.gateway.coalescer import ResumeOk, StreamRenderer

    thread = AgentThread(title="resume thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    follow_up = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        content="Position booked successfully.",
        meta={},
    )
    db_session.add(follow_up)
    db_session.flush()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_card_action_ttl_s=1800,
        gateway_web_base_url=None,
    )
    binding = _make_binding(db_session)
    claimed_action = _make_card_action(db_session, binding, thread_id=thread.id, message_id=follow_up.id)
    clicked_ref = _make_clicked_ref()

    renderer = StreamRenderer(connector=connector, settings=settings)
    await renderer.render_resume_result(
        db_session, binding, claimed_action, clicked_ref, ResumeOk(agent_message=follow_up)
    )

    # Check DB state
    db_session.refresh(claimed_action)
    assert claimed_action.status == "resolved", f"Expected resolved, got {claimed_action.status}"
    assert claimed_action.resolved_by_binding_id == binding.id

    # Check connector outbox
    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]
    message_sends = [e for e in connector.outbox if e["type"] == "message"]

    assert len(card_updates) >= 1, f"Expected card update, got: {connector.outbox}"
    resolved_card = card_updates[0]["card"]
    assert resolved_card.resolved is True, "Card should be marked resolved"

    assert len(message_sends) >= 1, f"Expected message send (follow-up content), got: {connector.outbox}"
    assert "Position booked" in message_sends[0]["msg"].text


# ---------------------------------------------------------------------------
# (b) ResumeOk with chained pending action → fresh card for chained action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_ok_chained_action_fresh_card(db_session, db_settings):
    """ResumeOk where follow-up message has pending_actions → fresh approval card."""
    from app.models import AgentThread, AgentMessage
    from app.services.gateway.coalescer import ResumeOk, StreamRenderer

    thread = AgentThread(title="chained thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    chained_action_dict = {
        "id": "intr_002:0",
        "tool_name": "book_position",
        "label": "Book chained",
        "summary": "Book chained position",
        "payload": {
            "product": {
                "product_family": "vanilla",
                "quantark_class": "EuropeanOption",
                "underlying": "000001.SZ",
                "currency": "CNY",
                "terms": {"strike": 100.0, "expiry": "2026-09-30"},
            },
            "quantity": 50.0,
            "portfolio_id": 7,
        },
        "requires_confirmation": True,
        "status": "pending",
    }

    follow_up = AgentMessage(
        thread_id=thread.id,
        role="assistant",
        content="Now book the next position.",
        meta={"pending_actions": [chained_action_dict]},
    )
    db_session.add(follow_up)
    db_session.flush()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_card_action_ttl_s=1800,
        gateway_web_base_url=None,
    )
    binding = _make_binding(db_session)
    claimed_action = _make_card_action(db_session, binding, thread_id=thread.id, message_id=follow_up.id)
    clicked_ref = _make_clicked_ref()

    renderer = StreamRenderer(connector=connector, settings=settings)
    await renderer.render_resume_result(
        db_session, binding, claimed_action, clicked_ref, ResumeOk(agent_message=follow_up)
    )

    # Should have a fresh approval card for the chained action
    card_sends = [e for e in connector.outbox if e["type"] == "card"]
    assert len(card_sends) >= 1, f"Expected chained card, got: {connector.outbox}"


# ---------------------------------------------------------------------------
# (c) ResumeRaised → mark_unknown, buttonless card
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resume_raised_unknown_card_no_token(db_session, db_settings):
    """ResumeRaised → status='unknown', card updated with no action buttons."""
    from app.models import AgentThread, AgentMessage
    from app.services.gateway.coalescer import ResumeRaised, StreamRenderer

    thread = AgentThread(title="raised thread", character="trader")
    db_session.add(thread)
    db_session.flush()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_card_action_ttl_s=1800,
        gateway_web_base_url="http://desk.example.com",
    )
    binding = _make_binding(db_session)
    msg_id = 99  # doesn't need a real AgentMessage row
    claimed_action = _make_card_action(db_session, binding, thread_id=thread.id, message_id=msg_id)
    clicked_ref = _make_clicked_ref()

    renderer = StreamRenderer(connector=connector, settings=settings)
    await renderer.render_resume_result(
        db_session, binding, claimed_action, clicked_ref, ResumeRaised()
    )

    # Check DB: status should be unknown
    db_session.refresh(claimed_action)
    assert claimed_action.status == "unknown", f"Expected unknown, got {claimed_action.status}"

    # Card should have no action buttons
    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]
    assert len(card_updates) >= 1, f"Expected card update, got: {connector.outbox}"
    unknown_card = card_updates[0]["card"]
    assert len(unknown_card.actions) == 0, f"Expected no buttons, got {unknown_card.actions}"


# ---------------------------------------------------------------------------
# (d) render_claim_error(already_resolved)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_claim_error_already_resolved(db_session, db_settings):
    """already_resolved claim error → card updated with 'already handled' message."""
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    settings = dataclasses.replace(db_settings, gateway_card_action_ttl_s=1800)
    source_ref = _make_clicked_ref()

    renderer = StreamRenderer(connector=connector, settings=settings)
    # No binding/session needed for render_claim_error
    await renderer.render_claim_error(db_session, source_ref, ClaimError.already_resolved)

    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]
    assert len(card_updates) == 1, f"Expected 1 card update, got: {connector.outbox}"
    card = card_updates[0]["card"]
    assert "already" in card.body.lower() or "handled" in card.body.lower(), f"Body: {card.body!r}"


# ---------------------------------------------------------------------------
# (e) render_claim_error(expired)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_claim_error_expired(db_session, db_settings):
    """expired claim error → card updated with 'expired' message."""
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    settings = dataclasses.replace(db_settings, gateway_card_action_ttl_s=1800)
    source_ref = _make_clicked_ref()

    renderer = StreamRenderer(connector=connector, settings=settings)
    await renderer.render_claim_error(db_session, source_ref, ClaimError.expired)

    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]
    assert len(card_updates) == 1
    card = card_updates[0]["card"]
    assert "expir" in card.body.lower(), f"Body: {card.body!r}"
    # Spec copy: must direct the user back to the agent (re-send), not the web desk.
    assert "re-send" in card.body.lower(), f"Expected 're-send' copy, got: {card.body!r}"


# ---------------------------------------------------------------------------
# (f) render_claim_error(bad_token / source_mismatch) → generic
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_render_claim_error_bad_token_source_mismatch(db_session, db_settings):
    """bad_token and source_mismatch errors → generic 'invalid' card update."""
    from app.services.gateway.coalescer import StreamRenderer

    connector = FakeConnector()
    settings = dataclasses.replace(db_settings, gateway_card_action_ttl_s=1800)
    source_ref = _make_clicked_ref()

    renderer = StreamRenderer(connector=connector, settings=settings)

    # bad_token
    await renderer.render_claim_error(db_session, source_ref, ClaimError.bad_token)
    # source_mismatch (second call)
    await renderer.render_claim_error(db_session, source_ref, ClaimError.source_mismatch)

    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]
    assert len(card_updates) == 2, f"Expected 2 card updates (one per error), got: {connector.outbox}"
    for update in card_updates:
        text = (update["card"].title + " " + update["card"].body).lower()
        assert "invalid" in text or "request" in text, f"Expected error message, got: {update['card']}"
