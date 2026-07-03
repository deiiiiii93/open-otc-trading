"""TDD tests for AgentBridge — Task 8.

(a) thread_for is idempotent: two calls for the same (binding, chat) return the
    same thread id and the second insert does not raise.
(b) resume passes binding.desk_user (not the literal "desk_user") as actor to
    resume_pending_action — proving the gateway propagates bound identity.
"""
from __future__ import annotations

import pytest

from app.models import GatewayBinding
from app.services.gateway.types import ChatRef


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_binding(session, *, desk_user: str = "trader_alice", persona: str = "trader") -> GatewayBinding:
    binding = GatewayBinding(
        provider="feishu",
        external_account_id="ou_bridge_test",
        workspace_id="tk_bridge",
        desk_user=desk_user,
        persona=persona,
        status="active",
    )
    session.add(binding)
    session.flush()
    return binding


def _make_chat_ref(chat_id: str = "chat_xyz") -> ChatRef:
    return ChatRef(
        connector="feishu",
        workspace_id="tk_bridge",
        chat_id=chat_id,
        chat_type="group",
    )


# ---------------------------------------------------------------------------
# (a) thread_for idempotency
# ---------------------------------------------------------------------------


def test_thread_for_creates_and_returns_thread(db_session):
    """thread_for creates a new AgentThread on first call."""
    from app.services.gateway.bridge import AgentBridge
    from app.services.agents import AgentService

    svc = AgentService(settings=None)
    bridge = AgentBridge(svc)

    binding = _make_binding(db_session)
    chat = _make_chat_ref()

    thread = bridge.thread_for(db_session, binding, chat)

    assert thread is not None
    assert thread.id is not None
    assert thread.character == binding.persona
    assert f"feishu" in thread.title
    assert chat.chat_id in thread.title


def test_thread_for_is_idempotent(db_session):
    """Two calls with the same (binding, chat) return the exact same thread id
    and do NOT raise (the second INSERT ON CONFLICT is silently ignored).
    """
    from app.services.gateway.bridge import AgentBridge
    from app.services.agents import AgentService

    svc = AgentService(settings=None)
    bridge = AgentBridge(svc)

    binding = _make_binding(db_session)
    chat = _make_chat_ref()

    t1 = bridge.thread_for(db_session, binding, chat)
    t2 = bridge.thread_for(db_session, binding, chat)

    assert t1.id == t2.id


def test_thread_for_different_chats_different_threads(db_session):
    """Different chat_ids for the same binding get separate threads."""
    from app.services.gateway.bridge import AgentBridge
    from app.services.agents import AgentService

    svc = AgentService(settings=None)
    bridge = AgentBridge(svc)

    binding = _make_binding(db_session)
    chat_a = _make_chat_ref("chat_A")
    chat_b = _make_chat_ref("chat_B")

    t_a = bridge.thread_for(db_session, binding, chat_a)
    t_b = bridge.thread_for(db_session, binding, chat_b)

    assert t_a.id != t_b.id


# ---------------------------------------------------------------------------
# (b) resume propagates binding.desk_user as actor
# ---------------------------------------------------------------------------


def test_resume_passes_binding_desk_user_as_actor(db_session, monkeypatch):
    """resume() must forward binding.desk_user — not the literal 'desk_user' —
    as the actor argument to resume_pending_action.
    """
    from app.services.gateway.bridge import AgentBridge
    from app.services.agents import AgentService

    svc = AgentService(settings=None)
    bridge = AgentBridge(svc)

    binding = _make_binding(db_session, desk_user="trader_alice")

    captured: dict = {}

    def spy_resume(*, thread_id, message_id, action_id, decision, actor, session):
        captured["actor"] = actor
        # Return a minimal stub — we just need to confirm the actor was forwarded.
        from app.models import AgentMessage
        msg = AgentMessage(
            thread_id=thread_id,
            role="assistant",
            content="ok",
            meta={},
        )
        return msg

    monkeypatch.setattr(svc, "resume_pending_action", spy_resume)

    bridge.resume(
        session=db_session,
        binding=binding,
        thread_id=99,
        message_id=1,
        action_id="act_1",
        decision="confirm",
    )

    assert captured.get("actor") == "trader_alice", (
        f"Expected actor='trader_alice', got {captured.get('actor')!r}"
    )


# ---------------------------------------------------------------------------
# (c) submit_turn persists the inbound user message BEFORE streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_turn_persists_user_message(db_session, monkeypatch):
    """submit_turn must insert a role='user' AgentMessage for the inbound text
    before it streams the assistant turn.

    ``AgentService.stream_and_persist`` only persists the *assistant* reply and
    assumes the caller already inserted the user turn (the HTTP /chat endpoint
    and the arena runner both do). The gateway is the third caller and must
    honor the same contract — otherwise IM-originated user turns never land in
    ``agent_messages`` and the chat panel shows only assistant replies.
    """
    from app.services.gateway.bridge import AgentBridge
    from app.services.agents import AgentService
    from app.models import AgentMessage

    svc = AgentService(settings=None)
    bridge = AgentBridge(svc)

    binding = _make_binding(db_session)
    chat = _make_chat_ref()
    thread = bridge.thread_for(db_session, binding, chat)
    db_session.commit()

    captured: dict = {}

    async def fake_stream_and_persist(**kwargs):
        # Record that streaming was invoked and prove the user row already
        # exists at stream time (mirrors the real ordering guarantee).
        captured["content"] = kwargs.get("content")
        from app import database

        with database.SessionLocal() as s:
            captured["user_rows_at_stream"] = (
                s.query(AgentMessage)
                .filter(
                    AgentMessage.thread_id == thread.id,
                    AgentMessage.role == "user",
                )
                .count()
            )
        return
        yield  # pragma: no cover — makes this an async generator

    monkeypatch.setattr(svc, "stream_and_persist", fake_stream_and_persist)

    async for _event in bridge.submit_turn(
        db_session, binding, thread, "list my portfolios"
    ):
        pass

    rows = (
        db_session.query(AgentMessage)
        .filter(
            AgentMessage.thread_id == thread.id,
            AgentMessage.role == "user",
        )
        .all()
    )
    assert len(rows) == 1, (
        f"Expected exactly one persisted user message, found {len(rows)}"
    )
    assert rows[0].content == "list my portfolios"
    # The user row must be committed BEFORE stream_and_persist runs (the routed
    # stream turn reads it in its own session to attach the route).
    assert captured.get("user_rows_at_stream") == 1, (
        "User message must be committed before stream_and_persist is called; "
        f"saw {captured.get('user_rows_at_stream')} rows at stream time"
    )
