"""TDD tests for Dispatcher card-action path — Task 12c.

Covers (matching the brief exactly):
1. valid token → bridge.resume called once → render_resume_result receives ResumeOk
2. bridge.resume raises → render_resume_result receives ResumeRaised (no exception escapes handle)
3. second click of same token → bridge.resume NOT called a second time; render_claim_error called with ClaimError.already_resolved
4. source-mismatch (inbound source_message_ref differs from minted out_* fields) → render_claim_error with ClaimError.source_mismatch; bridge.resume NOT called
5. after a card action (both win and claim-error), the GatewayInboundSeen dedup row is terminal "done"
6. (fail-closed) after a winning claim, GatewayCardAction.status is no longer "pending" even before resume completes — assert the resolving state is persisted (claim committed before resume)
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app import database
from app.config import Settings
from app.models import GatewayBinding, GatewayCardAction, GatewayInboundSeen
from app.services.gateway import actions
from app.services.gateway.actions import ClaimError
from app.services.gateway.coalescer import ResumeOk, ResumeRaised
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.types import CardActionInbound, ChatRef, InboundMessage, MessageRef


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
# Stub bridge and renderer
# ---------------------------------------------------------------------------


class _RecorderBridge:
    """Records calls to bridge.resume; configurable return value or exception."""

    def __init__(self, *, raises: Exception | None = None, return_value: Any = None):
        self.resume_calls: list = []
        self._raises = raises
        self._return_value = return_value

    def resume(self, session, binding, thread_id, message_id, action_id, decision):
        self.resume_calls.append((binding, thread_id, message_id, action_id, decision))
        if self._raises is not None:
            raise self._raises
        return self._return_value


class _RecorderRenderer:
    """Records calls to render_resume_result and render_claim_error."""

    def __init__(self):
        self.resume_result_calls: list = []   # [(outcome_type, outcome)]
        self.claim_error_calls: list = []     # [(source_message_ref, error)]

    async def render_resume_result(self, session, binding, claimed_action, clicked_card_ref, outcome):
        self.resume_result_calls.append((type(outcome), outcome))

    async def render_claim_error(self, session, source_message_ref, error):
        self.claim_error_calls.append((source_message_ref, error))


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_binding(sm) -> GatewayBinding:
    """Create and return an active GatewayBinding row."""
    # Create the binding directly — no linking code needed for these tests.
    with sm() as session:
        binding = GatewayBinding(
            provider="fake",
            external_account_id="ou_trader",
            workspace_id="wk_test",
            desk_user="trader@desk",
            persona="trader",
            status="active",
        )
        session.add(binding)
        session.commit()
        session.refresh(binding)
        binding_id = binding.id

    with sm() as session:
        return session.get(GatewayBinding, binding_id)


def _seed_card_action(sm, binding: GatewayBinding, settings) -> tuple[str, MessageRef]:
    """Mint a card action and return (token, out_ref)."""
    out_ref = MessageRef(
        connector="fake",
        workspace_id="wk_test",
        chat_id="chat_dm_1",
        message_id="msg_001",
    )
    with sm() as session:
        # Re-attach binding in this session
        b = session.get(GatewayBinding, binding.id)
        token = actions.mint_card_action(
            session,
            binding=b,
            thread_id=100,
            message_id=200,
            action_id="approve",
            decision="confirm",
            out_ref=out_ref,
            settings=settings,
        )
        session.commit()
    return token, out_ref


def _make_card_action_inbound(
    token: str,
    source_ref: MessageRef,
    *,
    event_id: str = "ev_card_001",
) -> InboundMessage:
    """Build an InboundMessage with kind='card_action'."""
    return InboundMessage(
        connector="fake",
        workspace_id="wk_test",
        external_account_id="ou_trader",
        provider_event_id=event_id,
        chat=ChatRef(
            connector="fake",
            workspace_id="wk_test",
            chat_id="chat_dm_1",
            chat_type="dm",
        ),
        kind="card_action",
        text=None,
        action=CardActionInbound(source_message_ref=source_ref, token=token),
        raw={},
    )


def _make_dispatcher(sm, settings, *, bridge=None, renderer=None):
    from app.services.gateway.dispatch import Dispatcher

    if bridge is None:
        bridge = _RecorderBridge()
    if renderer is None:
        renderer = _RecorderRenderer()
    connector = FakeConnector()
    return (
        Dispatcher(
            connector=connector,
            bridge=bridge,
            renderer=renderer,
            sessionmaker=sm,
            settings=settings,
        ),
        connector,
        bridge,
        renderer,
    )


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


def _get_card_action_by_token(sm, token: str) -> GatewayCardAction | None:
    from sqlalchemy import select
    with sm() as session:
        return session.execute(
            select(GatewayCardAction).where(GatewayCardAction.token == token)
        ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_valid_token_calls_bridge_resume_and_render_resume_ok(sm, db_settings):
    """Valid token → bridge.resume called once → render_resume_result receives ResumeOk."""
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    bridge = _RecorderBridge(return_value=None)
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    inbound = _make_card_action_inbound(token, out_ref)
    asyncio.run(disp.handle(inbound))

    # bridge.resume was called exactly once
    assert len(bridge.resume_calls) == 1

    # render_resume_result was called with ResumeOk
    assert len(renderer.resume_result_calls) == 1
    outcome_type, outcome = renderer.resume_result_calls[0]
    assert outcome_type is ResumeOk

    # render_claim_error was NOT called
    assert len(renderer.claim_error_calls) == 0


def test_bridge_resume_raises_renders_resume_raised_no_exception_escapes(sm, db_settings):
    """bridge.resume raises → render_resume_result receives ResumeRaised (no exception escapes handle)."""
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    bridge = _RecorderBridge(raises=RuntimeError("agent exploded"))
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    inbound = _make_card_action_inbound(token, out_ref, event_id="ev_card_002")

    # Should NOT raise — the exception must be caught and converted to ResumeRaised
    asyncio.run(disp.handle(inbound))

    # bridge.resume was called once
    assert len(bridge.resume_calls) == 1

    # render_resume_result was called with ResumeRaised
    assert len(renderer.resume_result_calls) == 1
    outcome_type, outcome = renderer.resume_result_calls[0]
    assert outcome_type is ResumeRaised

    # render_claim_error was NOT called
    assert len(renderer.claim_error_calls) == 0


def test_second_click_same_token_no_second_resume_already_resolved(sm, db_settings):
    """Second click of same token → bridge.resume NOT called again; render_claim_error with already_resolved."""
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    bridge = _RecorderBridge(return_value=None)
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    # First click (wins)
    inbound1 = _make_card_action_inbound(token, out_ref, event_id="ev_card_003a")
    asyncio.run(disp.handle(inbound1))

    assert len(bridge.resume_calls) == 1
    assert len(renderer.resume_result_calls) == 1

    # Second click (should lose the claim)
    inbound2 = _make_card_action_inbound(token, out_ref, event_id="ev_card_003b")
    asyncio.run(disp.handle(inbound2))

    # bridge.resume still called only once (no second resume)
    assert len(bridge.resume_calls) == 1

    # render_claim_error was called with already_resolved
    assert len(renderer.claim_error_calls) == 1
    _, error = renderer.claim_error_calls[0]
    assert error == ClaimError.already_resolved


def test_source_mismatch_refuses_no_resume(sm, db_settings):
    """source_message_ref differs from minted out_* → render_claim_error with source_mismatch; bridge.resume NOT called."""
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    bridge = _RecorderBridge(return_value=None)
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    # Mismatched source ref — different chat_id
    bad_ref = MessageRef(
        connector="fake",
        workspace_id="wk_test",
        chat_id="WRONG_CHAT",
        message_id="msg_001",
    )

    inbound = _make_card_action_inbound(token, bad_ref, event_id="ev_card_004")
    asyncio.run(disp.handle(inbound))

    # bridge.resume was NOT called
    assert len(bridge.resume_calls) == 0

    # render_claim_error was called with source_mismatch
    assert len(renderer.claim_error_calls) == 1
    _, error = renderer.claim_error_calls[0]
    assert error == ClaimError.source_mismatch


def test_dedup_row_done_after_winning_claim(sm, db_settings):
    """After a successful card action, the GatewayInboundSeen dedup row is terminal 'done'."""
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    bridge = _RecorderBridge(return_value=None)
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    inbound = _make_card_action_inbound(token, out_ref, event_id="ev_card_005a")
    asyncio.run(disp.handle(inbound))

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_dedup_row_done_after_claim_error(sm, db_settings):
    """After a claim error (source_mismatch), the GatewayInboundSeen dedup row is terminal 'done'."""
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    bridge = _RecorderBridge(return_value=None)
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    bad_ref = MessageRef(
        connector="fake",
        workspace_id="wk_test",
        chat_id="WRONG_CHAT",
        message_id="msg_001",
    )
    inbound = _make_card_action_inbound(token, bad_ref, event_id="ev_card_005b")
    asyncio.run(disp.handle(inbound))

    row = _get_dedup_row(sm, inbound)
    assert row is not None
    assert row.state == "done"


def test_fail_closed_resolving_state_committed_before_resume(sm, db_settings):
    """After a winning claim, GatewayCardAction.status transitions away from 'pending' before resume.

    This is the fail-closed ordering: the claim (status='resolving') is committed
    before bridge.resume is called, so a crash during resume leaves the DB in a
    non-pending state (no phantom second click possible).
    """
    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    # Verify initial state
    row_before = _get_card_action_by_token(sm, token)
    assert row_before is not None
    assert row_before.status == "pending"

    # Use a bridge that records the DB state AT THE MOMENT resume is called
    status_at_resume_time: list[str] = []

    class _StatusCaptureBridge:
        def resume(self, session, binding, thread_id, message_id, action_id, decision):
            # Read current status inside the same logical moment as resume
            from sqlalchemy import select
            with sm() as inner_session:
                row = inner_session.execute(
                    select(GatewayCardAction).where(GatewayCardAction.token == token)
                ).scalar_one_or_none()
                if row is not None:
                    status_at_resume_time.append(row.status)
            return None

    bridge = _StatusCaptureBridge()
    renderer = _RecorderRenderer()
    disp, connector, _, _ = _make_dispatcher(sm, db_settings, bridge=bridge, renderer=renderer)

    inbound = _make_card_action_inbound(token, out_ref, event_id="ev_card_006")
    asyncio.run(disp.handle(inbound))

    # The status at resume time must NOT be "pending" — claim was committed first
    assert len(status_at_resume_time) == 1
    assert status_at_resume_time[0] != "pending", (
        f"Expected status to be committed (not 'pending') before resume, "
        f"got: {status_at_resume_time[0]!r}"
    )


# ---------------------------------------------------------------------------
# Real-renderer regression test (Finding 2)
# Exercises the winning card-action path with the REAL StreamRenderer and a
# FakeConnector — not the stub _RecorderRenderer.  Proves that the
# GatewayCardAction row handed to render_resume_result is attached to the
# session that render_resume_result operates on.
# Without the dispatch.py fix (re-fetching via live_row = session2.get(...)),
# render_resume_result calls mark_resolved which flushes the detached result
# object and raises DetachedInstanceError.
# ---------------------------------------------------------------------------


def test_real_renderer_winning_claim_no_detached_instance_error(sm, db_settings):
    """Winning card-action path with the real StreamRenderer must not raise.

    Regression test for the DetachedInstanceError latent bug: the winning
    GatewayCardAction row (result) was attached to session1 which is closed
    before session2 opens.  render_resume_result calls mark_resolved(session2,
    result) which flush()es — raising DetachedInstanceError unless the row is
    re-fetched within session2.

    Assertions:
    (a) no exception escapes disp.handle(inbound)
    (b) GatewayCardAction.status == 'resolved' after the call (proving
        mark_resolved ran on an ATTACHED row — what the detachment bug broke)
    """
    import dataclasses
    from app.services.gateway.coalescer import StreamRenderer

    binding = _seed_binding(sm)
    token, out_ref = _seed_card_action(sm, binding, db_settings)

    # Simple stub agent-message-like object: has .content and .meta so
    # StreamRenderer.render_resume_result takes the ResumeOk branch and calls
    # mark_resolved on the card-action row.
    class _StubAgentMessage:
        content = "Action completed."
        meta: dict = {}

    class _StubBridge:
        def resume(self, session, binding, thread_id, message_id, action_id, decision):
            return _StubAgentMessage()

    connector = FakeConnector()
    settings = dataclasses.replace(
        db_settings,
        gateway_card_action_ttl_s=1800,
        gateway_web_base_url=None,
    )
    renderer = StreamRenderer(connector=connector, settings=settings)
    bridge = _StubBridge()

    disp, _, _, _ = _make_dispatcher(sm, settings, bridge=bridge, renderer=renderer)

    inbound = _make_card_action_inbound(token, out_ref, event_id="ev_card_007")

    # (a) Must not raise — DetachedInstanceError would surface here if unfixed.
    asyncio.run(disp.handle(inbound))

    # (b) Row must be 'resolved' — proves mark_resolved ran on an attached row.
    row = _get_card_action_by_token(sm, token)
    assert row is not None
    assert row.status == "resolved", (
        f"Expected 'resolved' after real-renderer path, got {row.status!r}"
    )
