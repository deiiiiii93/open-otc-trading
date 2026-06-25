"""TDD tests for Dispatcher message path — Task 12b.

Covers (in order matching the brief):
- group chat → refusal, no binding created, bridge NOT called
- unbound DM → refusal, bridge NOT called
- empty text → help message, bridge NOT called
- non-text (text=None) → help message, bridge NOT called
- valid linking code in DM → binding created, "Enrolled" confirmation, bridge NOT called
- bound DM text → thread_for + submit_turn + render_turn each called once
- over-length text → "too long" refusal, bridge NOT called
- every refusal path → GatewayInboundSeen row state == "done"
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import AsyncIterator

import pytest

from app import database
from app.config import Settings
from app.models import GatewayInboundSeen
from app.services.gateway import identity
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import ChatRef, InboundMessage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
    )


@pytest.fixture
def configured_db(db_settings: Settings):
    """Configure the database and create all tables. Returns the sessionmaker."""
    database.configure_database(db_settings)
    database.init_db()
    return database.SessionLocal


@pytest.fixture
def sm(configured_db):
    return configured_db


# ---------------------------------------------------------------------------
# Recorder stubs for bridge and renderer
# ---------------------------------------------------------------------------


class _RecorderBridge:
    """Records calls to thread_for / submit_turn."""

    def __init__(self):
        self.thread_for_calls: list = []
        self.submit_turn_calls: list = []
        self._fake_thread = _FakeThread()

    def thread_for(self, session, binding, chat):
        self.thread_for_calls.append((binding, chat))
        return self._fake_thread

    async def submit_turn(self, session, binding, thread, text):
        self.submit_turn_calls.append((binding, thread, text))
        # Return empty async generator
        return _empty_async_gen()


class _FakeThread:
    id = 999
    title = "IM fake:chat"


async def _empty_async_gen() -> AsyncIterator:
    return
    yield  # make it an async generator


class _RecorderRenderer:
    """Records calls to render_turn."""

    def __init__(self):
        self.render_turn_calls: list = []

    async def render_turn(self, session, binding, chat, agent_events):
        self.render_turn_calls.append((binding, chat, agent_events))
        # Drain the async generator
        async for _ in agent_events:
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_COUNTER = 0


def _next_event_id() -> str:
    global _EVENT_COUNTER
    _EVENT_COUNTER += 1
    return f"ev_{_EVENT_COUNTER:04d}"


def _make_inbound(
    *,
    event_id: str | None = None,
    chat_type: str = "dm",
    text: str | None = "hello",
    kind: str = "message",
) -> InboundMessage:
    if event_id is None:
        event_id = _next_event_id()
    return InboundMessage(
        connector="fake",
        workspace_id="wk_test",
        external_account_id="ou_test",
        provider_event_id=event_id,
        chat=ChatRef(
            connector="fake",
            workspace_id="wk_test",
            chat_id="chat_dm_1",
            chat_type=chat_type,
        ),
        kind=kind,
        text=text,
        action=None,
        raw={},
    )


def _make_dispatcher(sm, settings, *, bridge=None, renderer=None, connector=None):
    from app.services.gateway.dispatch import Dispatcher

    if connector is None:
        connector = FakeConnector()
    if bridge is None:
        bridge = _RecorderBridge()
    if renderer is None:
        renderer = _RecorderRenderer()
    return Dispatcher(
        connector=connector,
        bridge=bridge,
        renderer=renderer,
        sessionmaker=sm,
        settings=settings,
    ), connector, bridge, renderer


def _get_dedup_row(sm, inbound: InboundMessage) -> GatewayInboundSeen | None:
    with sm() as session:
        return (
            session.query(GatewayInboundSeen)
            .filter_by(
                connector=inbound.connector,
                workspace_id=inbound.workspace_id,
                provider_event_id=inbound.provider_event_id,
            )
            .first()
        )


def _issue_and_redeem_code(sm, settings) -> None:
    """Seed an active binding for (fake, wk_test, ou_test) with persona=trader."""
    with sm() as session:
        code, _ = identity.issue_linking_code(
            session, persona="trader", settings=settings
        )
        session.commit()
    with sm() as session:
        identity.redeem_code(
            session,
            connector="fake",
            external_account_id="ou_test",
            workspace_id="wk_test",
            code=code,
            settings=settings,
        )
        session.commit()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_group_chat_refusal_no_bridge(sm, db_settings):
    """Group chat → refusal sent, no binding created, bridge NOT called."""
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    inbound = _make_inbound(chat_type="group", text="ABCDEFGH2345IJKLMNOP234567")
    asyncio.run(disp.handle(inbound))

    # Refusal was sent
    assert len(connector.outbox) == 1
    msg = connector.outbox[0]
    assert msg["type"] == "message"
    assert "direct message" in msg["msg"].text.lower() or "dm" in msg["msg"].text.lower() or "only available" in msg["msg"].text.lower()

    # Bridge NOT called
    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    # Dedup row is done
    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_unbound_dm_refusal_no_bridge(sm, db_settings):
    """Unbound DM → refusal, bridge NOT called."""
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    inbound = _make_inbound(text="hello world")
    asyncio.run(disp.handle(inbound))

    assert len(connector.outbox) == 1
    msg = connector.outbox[0]["msg"].text.lower()
    assert "link" in msg or "enroll" in msg or "code" in msg

    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_empty_text_help_no_bridge(sm, db_settings):
    """Empty text after stripping → help message, bridge NOT called."""
    # First create a binding so we get past the unbound gate
    _issue_and_redeem_code(sm, db_settings)

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    inbound = _make_inbound(text="   ")
    asyncio.run(disp.handle(inbound))

    assert len(connector.outbox) == 1
    msg_text = connector.outbox[0]["msg"].text.lower()
    assert "text" in msg_text or "message" in msg_text or "read" in msg_text

    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_none_text_help_no_bridge(sm, db_settings):
    """Non-text content (text=None) → help message, bridge NOT called."""
    # Create a binding first
    _issue_and_redeem_code(sm, db_settings)

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    inbound = _make_inbound(text=None)
    asyncio.run(disp.handle(inbound))

    assert len(connector.outbox) == 1
    msg_text = connector.outbox[0]["msg"].text.lower()
    assert "text" in msg_text or "message" in msg_text or "read" in msg_text

    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_valid_code_in_dm_enrolls_no_bridge(sm, db_settings):
    """Valid linking code in DM → binding created, Enrolled confirmation, bridge NOT called."""
    # Issue a code
    with sm() as session:
        code, _ = identity.issue_linking_code(
            session, persona="trader", settings=db_settings
        )
        session.commit()

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    inbound = _make_inbound(text=code)
    asyncio.run(disp.handle(inbound))

    # Enrolled confirmation was sent
    assert len(connector.outbox) == 1
    msg_text = connector.outbox[0]["msg"].text.lower()
    assert "enroll" in msg_text or "linked" in msg_text or "agent" in msg_text

    # Bridge NOT called
    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    # Binding was actually created in the DB
    with sm() as session:
        binding = identity.active_binding(
            session,
            connector="fake",
            external_account_id="ou_test",
            workspace_id="wk_test",
        )
        assert binding is not None
        assert binding.status == "active"

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_invalid_code_refusal_no_bridge(sm, db_settings):
    """Code-shaped but invalid/expired → refusal, bridge NOT called."""
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    # Craft a code-shaped string that doesn't exist in DB
    fake_code = "A" * 26  # 26 uppercase letters — passes is_code_shaped

    inbound = _make_inbound(text=fake_code)
    asyncio.run(disp.handle(inbound))

    assert len(connector.outbox) == 1
    msg_text = connector.outbox[0]["msg"].text.lower()
    assert "invalid" in msg_text or "expired" in msg_text

    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_bound_text_calls_bridge_and_renderer(sm, db_settings):
    """Bound DM text → thread_for + submit_turn + render_turn each called once."""
    _issue_and_redeem_code(sm, db_settings)

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    inbound = _make_inbound(text="What is my risk?")
    asyncio.run(disp.handle(inbound))

    assert len(bridge.thread_for_calls) == 1
    assert len(bridge.submit_turn_calls) == 1
    assert len(renderer.render_turn_calls) == 1

    # No direct refusal sent via connector
    assert len(connector.outbox) == 0

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_over_length_text_refusal_no_bridge(sm, db_settings):
    """Message exceeding gateway_max_inbound_chars → too-long refusal, bridge NOT called."""
    _issue_and_redeem_code(sm, db_settings)

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)

    long_text = "x" * (db_settings.gateway_max_inbound_chars + 1)
    inbound = _make_inbound(text=long_text)
    asyncio.run(disp.handle(inbound))

    assert len(connector.outbox) == 1
    msg_text = connector.outbox[0]["msg"].text.lower()
    assert "long" in msg_text or "limit" in msg_text

    assert len(bridge.thread_for_calls) == 0
    assert len(bridge.submit_turn_calls) == 0

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_group_refusal_dedup_row_done(sm, db_settings):
    """Group chat refusal → dedup row is terminal 'done'."""
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound(chat_type="group")
    asyncio.run(disp.handle(inbound))
    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_unbound_refusal_dedup_row_done(sm, db_settings):
    """Unbound refusal → dedup row is terminal 'done'."""
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound(text="some random text")
    asyncio.run(disp.handle(inbound))
    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_enrolled_confirmation_dedup_row_done(sm, db_settings):
    """Successful enrollment → dedup row is terminal 'done'."""
    with sm() as session:
        code, _ = identity.issue_linking_code(session, persona="trader", settings=db_settings)
        session.commit()

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound(text=code)
    asyncio.run(disp.handle(inbound))

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_turn_dedup_row_done(sm, db_settings):
    """Successful turn → dedup row is terminal 'done'."""
    _issue_and_redeem_code(sm, db_settings)

    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound(text="What's my P&L?")
    asyncio.run(disp.handle(inbound))

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_idempotency_key_stable_group_refusal(sm, db_settings):
    """Group refusal uses a stable, predictable idempotency key derived from the event id.

    After one send the FakeConnector's idempotency store must have exactly one
    entry whose key is ``<provider_event_id>:refuse-group``.  This proves the
    dispatcher uses a deterministic key (not a random UUID), which is the
    property that makes redelivery safe.
    """
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings)
    inbound = _make_inbound(chat_type="group")

    asyncio.run(disp.handle(inbound))

    # Exactly one key in the idempotency store
    assert len(connector._idem) == 1
    expected_key = f"{inbound.provider_event_id}:refuse-group"
    assert expected_key in connector._idem, (
        f"Expected idempotency key {expected_key!r} not found in store; "
        f"actual keys: {list(connector._idem.keys())}"
    )
