"""End-to-end vertical slice test for the IM message gateway (Task 16).

Exercises the full stack in order:
  a. Enroll:      issue linking code → feed DM → GatewayBinding exists, enrolled ack in outbox.
  b. Read query:  feed bound DM → scripted answer in outbox; fake bridge records correct actor.
  c. HITL book:   feed booking DM → approval card with 2 actions; GatewayCardAction minted.
  d. Approve:     extract confirm token → feed card-action → token claimed; resume ran;
                  AuditEvent row exists with correct actor.
  e. Unbound refuse: feed DM from unenrolled identity → refusal in outbox; submit_turn NOT
                  called for that identity.

Architecture
------------
Rather than hooking into AgentService (which requires an LLM), we implement a
FakeAgentBridge that satisfies exactly the interface the Dispatcher calls:

    bridge.thread_for(session, binding, chat) -> AgentThread
    bridge.submit_turn(session, binding, thread, text)  -> AsyncGenerator[AgentEvent]
    bridge.resume(session, binding, thread_id, message_id, action_id, decision) -> AgentMessage

FakeAgentBridge keeps its own AgentThread rows (created via AgentService) so FK
constraints on GatewayThreadMap + GatewayCardAction are satisfied.

For step (d), the dispatcher calls bridge.resume() which in the *real* bridge
delegates to AgentService.resume_pending_action(); our stub records the call and
calls record_audit() directly (matching the production audit path at the service
layer) so the AuditEvent row is verifiable.

SSE event shapes used
---------------------
FakeAgentBridge.submit_turn() yields AgentEvent objects directly (not raw SSE
strings).  The renderer expects an AsyncIterable[AgentEvent] from render_turn —
which is what submit_turn returns after the bridge's parse_sse_stream wrapper.
We bypass that wrapper by implementing the bridge interface at the AgentEvent
level rather than the SSE-string level.

For the HITL path (step c) we emit:
    AgentEvent(type="done", data={
        "thread_id": <int>,
        "message_id": <int>,       # real DB message id
        "pending_actions": [<action_dict>],
    })
so the coalescer's _handle_card_event fires.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import uuid
from typing import Any, AsyncIterator

import pytest

from app import database
from app.config import Settings
from app.models import (
    AgentMessage,
    AgentThread,
    AuditEvent,
    GatewayBinding,
    GatewayCardAction,
    GatewayThreadMap,
)
from app.services.audit import record_audit
from app.services.gateway import identity as identity_svc
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import (
    AgentEvent,
    CardActionInbound,
    ChatRef,
    InboundMessage,
    MessageRef,
)


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def e2e_settings(tmp_path):
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'e2e.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
        gateway_flush_interval_ms=1,    # flush immediately
        gateway_flush_chars=1,           # flush on first char
        gateway_card_action_ttl_s=3600,
        gateway_linking_code_ttl_s=600,
        gateway_dedupe_lease_s=120,
        gateway_dedupe_ttl_s=86400,
        gateway_lock_lease_s=30,
        gateway_enabled_connectors="fake",
        gateway_max_queued_per_chat=8,
        gateway_queue_max_age_s=120,
        gateway_max_inbound_chars=4000,
        gateway_default_desk_user="trader@desk",
    )


@pytest.fixture
def configured_sm(e2e_settings):
    """Configure DB and return the sessionmaker."""
    database.configure_database(e2e_settings)
    database.init_db()
    return database.SessionLocal


# ---------------------------------------------------------------------------
# FakeAgentBridge
# ---------------------------------------------------------------------------


class _FakeAgentBridge:
    """Minimal bridge stub that satisfies the Dispatcher interface.

    submit_turn behaviour:
    - If the text contains "book" → yield a done event with a pending book_position action.
    - Otherwise → yield a token event with a scripted answer + done event.

    resume behaviour:
    - Record the call and write an AuditEvent row so the test can verify.
    - Return a minimal AgentMessage stub.
    """

    def __init__(self, sm) -> None:
        self._sm = sm
        # Recorded arguments for assertions
        self.submit_turn_actors: list[str] = []
        self.resume_calls: list[dict] = []

    # ------------------------------------------------------------------
    # thread_for — reuse real AgentThread creation
    # ------------------------------------------------------------------

    def thread_for(self, session, binding: GatewayBinding, chat: ChatRef) -> AgentThread:
        """Create or return the AgentThread for this (binding, chat) pair."""
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        # Look up existing map row first.
        existing: GatewayThreadMap | None = (
            session.query(GatewayThreadMap)
            .filter_by(binding_id=binding.id, chat_id=chat.chat_id)
            .one_or_none()
        )
        if existing is not None:
            thread = session.get(AgentThread, existing.thread_id)
            if thread is not None:
                return thread

        # Create a new thread.
        thread = AgentThread(
            title=f"IM {chat.connector}:{chat.chat_id}",
            character=binding.persona,
        )
        session.add(thread)
        session.flush()

        # Insert map row (idempotent).
        stmt = sqlite_insert(GatewayThreadMap.__table__).values(
            binding_id=binding.id,
            chat_id=chat.chat_id,
            thread_id=thread.id,
        ).on_conflict_do_nothing(index_elements=["binding_id", "chat_id"])
        session.execute(stmt)
        session.flush()

        # Re-read canonical row.
        canonical = (
            session.query(GatewayThreadMap)
            .filter_by(binding_id=binding.id, chat_id=chat.chat_id)
            .one_or_none()
        )
        if canonical is not None and canonical.thread_id != thread.id:
            winner = session.get(AgentThread, canonical.thread_id)
            if winner is not None:
                return winner

        return thread

    # ------------------------------------------------------------------
    # submit_turn — return async generator of AgentEvent
    # ------------------------------------------------------------------

    async def submit_turn(
        self,
        session,
        binding: GatewayBinding,
        thread: AgentThread,
        text: str,
    ):
        """Return an async iterable of AgentEvent for render_turn to consume."""
        # Record the actor.
        self.submit_turn_actors.append(binding.desk_user)

        if "book" in text.lower():
            # Create a real AgentMessage so GatewayCardAction FK constraints are met.
            with self._sm() as msg_session:
                msg = AgentMessage(
                    thread_id=thread.id,
                    role="assistant",
                    content="I will book the position — please approve.",
                    meta={},
                )
                msg_session.add(msg)
                msg_session.commit()
                msg_session.refresh(msg)
                message_id = msg.id

            async def _booking_events():
                yield AgentEvent(type="token", data={"content": "I will book the position."})
                yield AgentEvent(
                    type="done",
                    data={
                        "thread_id": thread.id,
                        "message_id": message_id,
                        "pending_actions": [
                            {
                                "id": f"action-{uuid.uuid4()}",
                                "tool_name": "book_position",
                                "label": "Book vanilla call",
                                "summary": "Book a vanilla call option",
                                "payload": {
                                    "product": {
                                        "product_family": "vanilla",
                                        "quantark_class": "EuropeanOption",
                                        "underlying": "000300.SH",
                                        "currency": "CNY",
                                        "terms": {"strike": 4200.0, "expiry": "2026-12-31"},
                                    },
                                    "quantity": 100.0,
                                    "portfolio_id": 1,
                                },
                                "requires_confirmation": True,
                                "status": "pending",
                            }
                        ],
                    },
                )

            return _booking_events()
        else:
            async def _simple_events():
                yield AgentEvent(type="token", data={"content": "Hello from the desk agent."})
                yield AgentEvent(type="done", data={"thread_id": thread.id, "message_id": 0, "pending_actions": []})

            return _simple_events()

    # ------------------------------------------------------------------
    # resume — record call, write AuditEvent, return minimal message
    # ------------------------------------------------------------------

    def resume(
        self,
        session,
        binding: GatewayBinding,
        thread_id: int,
        message_id: int,
        action_id: str,
        decision: str,
    ) -> AgentMessage:
        """Record the resume call and write an AuditEvent for verifiability."""
        self.resume_calls.append({
            "actor": binding.desk_user,
            "binding_id": binding.id,
            "thread_id": thread_id,
            "message_id": message_id,
            "action_id": action_id,
            "decision": decision,
        })

        # Write a real AuditEvent so the test can query for it.
        record_audit(
            session,
            event_type="gateway.action_resumed",
            actor=binding.desk_user,
            subject_type="gateway_card_action",
            subject_id=str(action_id),
            payload={
                "decision": decision,
                "thread_id": thread_id,
                "message_id": message_id,
            },
        )

        # Return a minimal AgentMessage-like object (no content = no chained send).
        msg = AgentMessage(
            thread_id=thread_id,
            role="assistant",
            content=None,
            meta={},
        )
        return msg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_EV_COUNTER = {"n": 0}


def _new_event_id() -> str:
    _EV_COUNTER["n"] += 1
    return f"e2e_ev_{_EV_COUNTER['n']:04d}"


def _make_dm(
    *,
    text: str,
    external_account_id: str = "ou_trader_123",
    workspace_id: str = "wk_e2e",
    chat_id: str = "dm_trader_001",
    event_id: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        connector="fake",
        workspace_id=workspace_id,
        external_account_id=external_account_id,
        provider_event_id=event_id or _new_event_id(),
        chat=ChatRef(
            connector="fake",
            workspace_id=workspace_id,
            chat_id=chat_id,
            chat_type="dm",
        ),
        kind="message",
        text=text,
        action=None,
        raw={},
    )


def _make_card_action(
    *,
    token: str,
    source_ref: MessageRef,
    external_account_id: str = "ou_trader_123",
    workspace_id: str = "wk_e2e",
    chat_id: str = "dm_trader_001",
    event_id: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        connector="fake",
        workspace_id=workspace_id,
        external_account_id=external_account_id,
        provider_event_id=event_id or _new_event_id(),
        chat=ChatRef(
            connector="fake",
            workspace_id=workspace_id,
            chat_id=chat_id,
            chat_type="dm",
        ),
        kind="card_action",
        text=None,
        action=CardActionInbound(source_message_ref=source_ref, token=token),
        raw={},
    )


def _outbox_texts(outbox: list[dict]) -> list[str]:
    """Extract all text values from message-type outbox entries."""
    texts = []
    for entry in outbox:
        if entry["type"] in ("message", "update_message"):
            texts.append(entry["msg"].text)
    return texts


# ---------------------------------------------------------------------------
# Main vertical slice test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gateway_e2e_vertical_slice(configured_sm, e2e_settings):
    """
    Full gateway vertical slice: enroll → query → HITL approve → unbound refusal.

    Uses FakeAgentBridge (no LLM, no network) and FakeConnector (in-memory).
    """
    from app.services.gateway.coalescer import StreamRenderer
    from app.services.gateway.dispatch import Dispatcher

    sm = configured_sm

    # Build fake connector and bridge.
    connector = FakeConnector()
    bridge = _FakeAgentBridge(sm)

    renderer = StreamRenderer(
        connector=connector,
        settings=e2e_settings,
        sleep=asyncio.sleep,
    )
    dispatcher = Dispatcher(
        connector=connector,
        bridge=bridge,
        renderer=renderer,
        sessionmaker=sm,
        settings=e2e_settings,
    )

    # =====================================================================
    # (a) ENROLL: issue a linking code, feed it as a DM
    # =====================================================================
    with sm() as session:
        code, expires_at = identity_svc.issue_linking_code(
            session,
            persona="trader",
            settings=e2e_settings,
        )
        session.commit()

    enroll_msg = _make_dm(text=code)
    await dispatcher.handle(enroll_msg)

    # Assert: GatewayBinding exists and is active.
    with sm() as session:
        binding_row = identity_svc.active_binding(
            session,
            connector="fake",
            external_account_id="ou_trader_123",
            workspace_id="wk_e2e",
        )
    assert binding_row is not None, "Expected active GatewayBinding after enroll"
    assert binding_row.desk_user == "trader@desk", (
        f"Expected desk_user='trader@desk', got {binding_row.desk_user!r}"
    )
    assert binding_row.persona == "trader", (
        f"Expected persona='trader', got {binding_row.persona!r}"
    )

    # Assert: enrollment confirmation appears in outbox.
    enroll_texts = _outbox_texts(connector.outbox)
    assert any("Enrolled" in t for t in enroll_texts), (
        f"Expected 'Enrolled' confirmation in outbox, got: {enroll_texts}"
    )

    binding_id = binding_row.id

    # =====================================================================
    # (b) READ QUERY: feed a normal DM, assert scripted answer in outbox
    # =====================================================================
    connector.outbox.clear()
    bridge.submit_turn_actors.clear()

    query_msg = _make_dm(text="What is our risk exposure?", event_id=_new_event_id())
    await dispatcher.handle(query_msg)

    # Assert: some text answer appeared in outbox from the agent.
    query_outbox = connector.outbox[:]
    query_texts = _outbox_texts(query_outbox)
    assert len(query_texts) > 0, (
        f"Expected text in outbox after query, got: {query_outbox}"
    )
    assert any("desk agent" in t.lower() or "hello" in t.lower() for t in query_texts), (
        f"Expected scripted answer in outbox. Texts: {query_texts}"
    )

    # Assert: submit_turn was called with binding.desk_user as actor.
    assert len(bridge.submit_turn_actors) == 1, (
        f"Expected submit_turn called once, called {len(bridge.submit_turn_actors)} times"
    )
    assert bridge.submit_turn_actors[0] == "trader@desk", (
        f"Expected actor='trader@desk', got {bridge.submit_turn_actors[0]!r}"
    )

    # =====================================================================
    # (c) HITL BOOKING: feed a booking DM, assert approval card in outbox
    # =====================================================================
    connector.outbox.clear()
    bridge.submit_turn_actors.clear()

    book_msg = _make_dm(text="Please book a vanilla call position", event_id=_new_event_id())
    await dispatcher.handle(book_msg)

    # Assert: at least one card was sent (placeholder) and updated (actionable).
    card_sends = [e for e in connector.outbox if e["type"] == "card"]
    card_updates = [e for e in connector.outbox if e["type"] == "update_card"]

    assert len(card_sends) >= 1, (
        f"Expected at least 1 card send (placeholder), got {len(card_sends)}. Outbox: {connector.outbox}"
    )
    assert len(card_updates) >= 1, (
        f"Expected at least 1 card update (actionable), got {len(card_updates)}. Outbox: {connector.outbox}"
    )

    # The actionable card must have exactly 2 actions: Approve + Reject.
    actionable_card = card_updates[-1]["card"]
    assert len(actionable_card.actions) == 2, (
        f"Expected 2 card actions (Approve+Reject), got {actionable_card.actions}"
    )
    action_labels = {a.label for a in actionable_card.actions}
    assert "Approve" in action_labels, f"Expected 'Approve' action, got {action_labels}"
    assert "Reject" in action_labels, f"Expected 'Reject' action, got {action_labels}"

    # Assert: GatewayCardAction row(s) were minted for this binding.
    with sm() as session:
        minted_actions = (
            session.query(GatewayCardAction)
            .filter_by(binding_id=binding_id, decision="confirm")
            .all()
        )
    assert len(minted_actions) >= 1, (
        f"Expected at least 1 GatewayCardAction minted, got {len(minted_actions)}"
    )

    # Extract the confirm token and the placeholder card's MessageRef.
    confirm_token = actionable_card.actions[
        [a.label for a in actionable_card.actions].index("Approve")
    ].token
    assert confirm_token, "Confirm token must not be empty"

    # The source_message_ref for the card-action inbound must match the placeholder ref.
    placeholder_ref = card_sends[-1]["ref"]

    # =====================================================================
    # (d) APPROVE: feed card-action, assert token claimed + resume ran + audit
    # =====================================================================
    connector.outbox.clear()
    bridge.resume_calls.clear()

    approve_msg = _make_card_action(
        token=confirm_token,
        source_ref=placeholder_ref,
        event_id=_new_event_id(),
    )
    await dispatcher.handle(approve_msg)

    # Assert: resume was called.
    assert len(bridge.resume_calls) == 1, (
        f"Expected bridge.resume called once, got {len(bridge.resume_calls)} calls"
    )
    resume_record = bridge.resume_calls[0]

    # Assert: correct actor forwarded.
    assert resume_record["actor"] == "trader@desk", (
        f"Expected actor='trader@desk', got {resume_record['actor']!r}"
    )

    # Assert: token is now claimed (status no longer 'pending').
    with sm() as session:
        claimed_row = (
            session.query(GatewayCardAction)
            .filter_by(token=confirm_token)
            .first()
        )
    assert claimed_row is not None, "GatewayCardAction row must exist after claim"
    assert claimed_row.status != "pending", (
        f"Expected token status != 'pending' after claim, got {claimed_row.status!r}"
    )

    # Assert: AuditEvent row exists with the correct actor.
    with sm() as session:
        audit_row = (
            session.query(AuditEvent)
            .filter_by(event_type="gateway.action_resumed", actor="trader@desk")
            .first()
        )
    assert audit_row is not None, (
        "Expected AuditEvent(event_type='gateway.action_resumed', actor='trader@desk') to exist"
    )

    # =====================================================================
    # (e) UNBOUND REFUSAL: unenrolled identity gets refused
    # =====================================================================
    connector.outbox.clear()
    bridge.submit_turn_actors.clear()

    unbound_msg = _make_dm(
        text="Hello, I want to trade",
        external_account_id="ou_stranger_999",
        workspace_id="wk_e2e",
        chat_id="dm_stranger_999",
        event_id=_new_event_id(),
    )
    await dispatcher.handle(unbound_msg)

    # Assert: a refusal message appeared in the outbox.
    refusal_texts = _outbox_texts(connector.outbox)
    assert any(
        "not linked" in t.lower() or "linking code" in t.lower() or "enroll" in t.lower()
        for t in refusal_texts
    ), f"Expected a refusal/linking message for unbound identity, got: {refusal_texts}"

    # Assert: submit_turn was NOT called for this unenrolled identity.
    assert len(bridge.submit_turn_actors) == 0, (
        f"Expected submit_turn NOT called for unbound identity, "
        f"but it was called {len(bridge.submit_turn_actors)} time(s) "
        f"with actors: {bridge.submit_turn_actors}"
    )
