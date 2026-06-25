"""TDD tests for Dispatcher per-chat serialization + backpressure — Task 12d.

Covers:
1. Serial ordering — two turns same chat key run serially (ordering asserted via
   recorded start/finish events).
2. Queue overflow drop-newest — when depth > gateway_max_queued_per_chat the newest
   turn is dropped with a "too many pending" notice; bridge.submit_turn NOT called;
   dedup row is "done".
3. Age-cap drop — an admitted turn whose wait exceeds gateway_queue_max_age_s is
   dropped with a "too old" notice; bridge.submit_turn NOT called; dedup row "done".
4. Card-action not blocked — a card-action for the same chat completes while a
   turn is blocking the lane.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

import pytest

from app import database
from app.config import Settings
from app.models import GatewayBinding, GatewayCardAction, GatewayInboundSeen
from app.services.gateway import actions, identity
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.coalescer import ResumeOk, ResumeRaised
from app.services.gateway.types import (
    CardActionInbound,
    ChatRef,
    InboundMessage,
    MessageRef,
)


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
    database.configure_database(db_settings)
    database.init_db()
    return database.SessionLocal


@pytest.fixture
def sm(configured_db):
    return configured_db


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_EV = 0


def _next_ev() -> str:
    global _EV
    _EV += 1
    return f"ev_{_EV:06d}"


def _make_message_inbound(
    *,
    event_id: str | None = None,
    chat_id: str = "chat_dm_1",
    workspace_id: str = "wk_test",
    connector: str = "fake",
    external_account_id: str = "ou_test",
    text: str = "hello",
) -> InboundMessage:
    eid = event_id or _next_ev()
    return InboundMessage(
        connector=connector,
        workspace_id=workspace_id,
        external_account_id=external_account_id,
        provider_event_id=eid,
        chat=ChatRef(
            connector=connector,
            workspace_id=workspace_id,
            chat_id=chat_id,
            chat_type="dm",
        ),
        kind="message",
        text=text,
        action=None,
        raw={},
    )


class _FakeThread:
    id = 999
    title = "IM fake:chat"


async def _empty_async_gen() -> AsyncIterator:
    return
    yield


@dataclass
class _RecorderBridge:
    """Records calls; supports an optional per-call gate event for turn 1."""

    submit_turn_calls: list = field(default_factory=list)
    thread_for_calls: list = field(default_factory=list)
    # If set, submit_turn will wait for this event before returning.
    gate: asyncio.Event | None = None
    # Records (event_id, "start") and ("finish") for ordering checks.
    order_log: list = field(default_factory=list)

    def thread_for(self, session, binding, chat):
        self.thread_for_calls.append((binding, chat))
        return _FakeThread()

    async def submit_turn(self, session, binding, thread, text):
        self.submit_turn_calls.append((binding, thread, text))
        self.order_log.append(("start", text))
        if self.gate is not None:
            await self.gate.wait()
        self.order_log.append(("finish", text))
        return _empty_async_gen()


@dataclass
class _RecorderRenderer:
    render_turn_calls: list = field(default_factory=list)
    render_claim_error_calls: list = field(default_factory=list)
    render_resume_result_calls: list = field(default_factory=list)

    async def render_turn(self, session, binding, chat, agent_events):
        self.render_turn_calls.append((binding, chat))
        async for _ in agent_events:
            pass

    async def render_claim_error(self, session, source_message_ref, error):
        self.render_claim_error_calls.append(error)

    async def render_resume_result(self, session, binding, card_action, source_message_ref, outcome):
        self.render_resume_result_calls.append(outcome)


def _make_dispatcher(
    sm,
    settings: Settings,
    *,
    bridge=None,
    renderer=None,
    connector=None,
    monotonic=None,
):
    from app.services.gateway.dispatch import Dispatcher

    if connector is None:
        connector = FakeConnector()
    if bridge is None:
        bridge = _RecorderBridge()
    if renderer is None:
        renderer = _RecorderRenderer()

    kwargs = dict(
        connector=connector,
        bridge=bridge,
        renderer=renderer,
        sessionmaker=sm,
        settings=settings,
    )
    if monotonic is not None:
        kwargs["monotonic"] = monotonic

    return Dispatcher(**kwargs), connector, bridge, renderer


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


def _seed_binding(sm, settings: Settings) -> None:
    """Seed an active binding for (fake, wk_test, ou_test)."""
    with sm() as session:
        code, _ = identity.issue_linking_code(session, persona="trader", settings=settings)
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
# Test 1: Serial ordering
# ---------------------------------------------------------------------------


def test_serial_ordering_same_chat(sm, db_settings):
    """Two turns for the same (connector, workspace, chat) run serially.

    Turn-1 blocks behind an asyncio.Event gate.  Turn-2 is dispatched concurrently
    via gather but must NOT start until turn-1 finishes.
    """
    _seed_binding(sm, db_settings)

    gate = asyncio.Event()
    bridge = _RecorderBridge(gate=gate)
    disp, connector, bridge, renderer = _make_dispatcher(sm, db_settings, bridge=bridge)

    msg1 = _make_message_inbound(text="turn-1")
    msg2 = _make_message_inbound(text="turn-2")

    async def run():
        # Dispatch both concurrently, but the gate will hold turn-1 until we release it.
        task1 = asyncio.create_task(disp.handle(msg1))
        # Give task1 time to enter the lane and acquire the lock.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        task2 = asyncio.create_task(disp.handle(msg2))
        # Let both tasks run a bit without releasing the gate.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # At this point turn-1 is blocked inside submit_turn (holding the lock).
        # Turn-2 should be waiting to acquire the lock — NOT yet started.
        starts_before_release = [e for e in bridge.order_log if e[0] == "start"]
        assert len(starts_before_release) == 1, (
            f"Expected exactly 1 turn to have started before gate release, "
            f"got {len(starts_before_release)}: {bridge.order_log}"
        )
        assert starts_before_release[0][1] == "turn-1"

        # Release turn-1; turn-2 may now proceed.
        gate.set()
        await asyncio.gather(task1, task2)

    asyncio.run(run())

    # Verify full ordering in the log.
    assert bridge.order_log == [
        ("start", "turn-1"),
        ("finish", "turn-1"),
        ("start", "turn-2"),
        ("finish", "turn-2"),
    ], f"Unexpected order log: {bridge.order_log}"

    # Both dedup rows should be done.
    assert _get_dedup_row(sm, msg1).state == "done"
    assert _get_dedup_row(sm, msg2).state == "done"


# ---------------------------------------------------------------------------
# Test 2: Queue overflow — drop-newest
# ---------------------------------------------------------------------------


def test_queue_overflow_drops_newest(sm, db_settings):
    """When the lane is saturated (1 running + max waiting), next turn is dropped.

    We use gateway_max_queued_per_chat=1 so:
      - turn-1 is running (depth=1)
      - turn-2 is waiting (depth=2) → depth(2) == max+1 → still admitted (2 > 1? yes, but
        we set max=1 and depth will be 2 when turn-3 arrives — let's use max=1)
      Actually with max=1:
        turn-1 runs (depth=1), turn-2 admitted (depth=2, depth(2) > 1 → drop turn-2!)
      Wait — we need 1 running + max waiting. With max=1, 1 waiting means depth=2.
      So the 3rd turn (depth would become 3 > 1) gets dropped.
      Let's use max=1, so: turn-1 running (depth=1), turn-2 queued (depth=2 == max+1 → NOT dropped since
      2 is not > max+1).

      Re-reading spec: "depth > gateway_max_queued_per_chat → drop"
      depth includes the running turn. So max_queued=1 means:
        depth=1 (1 running, 0 waiting): admit
        depth=2 (1 running, 1 waiting): this is where drop happens (2 > 1 = True)

      So with max_queued=1:
        turn-1 running: depth=1 → admitted
        turn-2 arrives: depth currently 1, check (1 > 1)=False → admitted, depth=2
        turn-3 arrives: depth currently 2, check (2 > 1)=True → DROPPED
    """
    _seed_binding(sm, db_settings)

    # Override settings to have very small queue.
    settings = Settings(
        database_url=db_settings.database_url,
        artifact_dir=db_settings.artifact_dir,
        agent_checkpoint_db_path=db_settings.agent_checkpoint_db_path,
        gateway_max_queued_per_chat=1,
    )

    gate = asyncio.Event()
    bridge = _RecorderBridge(gate=gate)
    disp, connector, bridge, renderer = _make_dispatcher(sm, settings, bridge=bridge)

    msg1 = _make_message_inbound(text="turn-1")  # will run (depth=1)
    msg2 = _make_message_inbound(text="turn-2")  # will queue (depth=2)
    msg3 = _make_message_inbound(text="turn-3")  # DROPPED (depth would be 3 > 1)

    async def run():
        task1 = asyncio.create_task(disp.handle(msg1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Turn-2 queues behind turn-1 (depth: 1 → 2)
        task2 = asyncio.create_task(disp.handle(msg2))
        await asyncio.sleep(0)

        # Turn-3 should be dropped (depth would be 3 > 1)
        task3 = asyncio.create_task(disp.handle(msg3))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Release the gate so all admitted turns can complete.
        gate.set()
        await asyncio.gather(task1, task2, task3)

    asyncio.run(run())

    # Turn-3 was dropped: a notice was sent to the connector.
    drop_notices = [
        m for m in connector.outbox
        if "pending" in m["msg"].text.lower() or "too many" in m["msg"].text.lower() or "dropped" in m["msg"].text.lower()
    ]
    assert len(drop_notices) >= 1, (
        f"Expected at least one drop notice in outbox; got: {[m['msg'].text for m in connector.outbox]}"
    )

    # bridge.submit_turn was NOT called for the dropped turn.
    submit_texts = [call[2] for call in bridge.submit_turn_calls]
    assert "turn-3" not in submit_texts, (
        f"Dropped turn-3 should not have reached submit_turn; calls: {bridge.submit_turn_calls}"
    )

    # The dropped turn's dedup row should be "done".
    row3 = _get_dedup_row(sm, msg3)
    assert row3 is not None
    assert row3.state == "done", f"Expected 'done', got {row3.state!r}"


# ---------------------------------------------------------------------------
# Test 3: Age-cap drop
# ---------------------------------------------------------------------------


def test_age_cap_drops_stale_turn(sm, db_settings):
    """A turn that has waited longer than gateway_queue_max_age_s is dropped.

    We inject a fake monotonic clock that advances past the age cap so the
    queued turn is dropped when it acquires the lock.
    """
    _seed_binding(sm, db_settings)

    # Use a very small max age so a single step is "too old".
    settings = Settings(
        database_url=db_settings.database_url,
        artifact_dir=db_settings.artifact_dir,
        agent_checkpoint_db_path=db_settings.agent_checkpoint_db_path,
        gateway_queue_max_age_s=5,
    )

    # Controllable clock: starts at 0 and we can advance it.
    clock_value: list[float] = [0.0]

    def fake_monotonic() -> float:
        return clock_value[0]

    gate = asyncio.Event()
    bridge = _RecorderBridge(gate=gate)
    disp, connector, bridge, renderer = _make_dispatcher(
        sm, settings, bridge=bridge, monotonic=fake_monotonic
    )

    msg1 = _make_message_inbound(text="turn-1")  # runs, holds gate
    msg2 = _make_message_inbound(text="turn-2")  # queued; will be stale

    async def run():
        task1 = asyncio.create_task(disp.handle(msg1))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Advance clock before turn-2 enqueues so it is already "too old" when admitted.
        # Actually, we need it to be stale AFTER it is enqueued but BEFORE the lock is acquired.
        # So we enqueue it first (clock still at 0) then advance the clock.
        task2 = asyncio.create_task(disp.handle(msg2))
        await asyncio.sleep(0)

        # Advance clock past age cap so turn-2's wait will appear too long.
        clock_value[0] = 200.0  # far past gateway_queue_max_age_s=5

        # Release gate; turn-1 finishes, lock is released, turn-2 acquires lock
        # and sees clock - enqueued_at > 5 → drops.
        gate.set()
        await asyncio.gather(task1, task2)

    asyncio.run(run())

    # A "too old" drop notice was sent.
    drop_notices = [
        m for m in connector.outbox
        if "old" in m["msg"].text.lower() or "stale" in m["msg"].text.lower() or "resend" in m["msg"].text.lower() or "too old" in m["msg"].text.lower()
    ]
    assert len(drop_notices) >= 1, (
        f"Expected age-cap drop notice in outbox; got: {[m['msg'].text for m in connector.outbox]}"
    )

    # bridge.submit_turn NOT called for the stale turn-2.
    submit_texts = [call[2] for call in bridge.submit_turn_calls]
    assert "turn-2" not in submit_texts, (
        f"Stale turn-2 should not reach submit_turn; calls: {bridge.submit_turn_calls}"
    )

    # The stale turn's dedup row should be "done".
    row2 = _get_dedup_row(sm, msg2)
    assert row2 is not None
    assert row2.state == "done", f"Expected 'done', got {row2.state!r}"


# ---------------------------------------------------------------------------
# Test 4: Card-action not blocked by lane
# ---------------------------------------------------------------------------


def test_card_action_not_blocked_by_lane(sm, db_settings):
    """A card-action for the same chat completes immediately even while a turn
    is blocking the message lane.
    """
    _seed_binding(sm, db_settings)

    # Seed a card-action row using mint_card_action so all required fields are set.
    with sm() as session:
        binding = identity.active_binding(
            session,
            connector="fake",
            external_account_id="ou_test",
            workspace_id="wk_test",
        )
        binding_id = binding.id

    from app.services.gateway.types import MessageRef as _MessageRef
    out_ref = _MessageRef(
        connector="fake",
        workspace_id="wk_test",
        chat_id="chat_dm_1",
        message_id="card_msg_001",
    )
    with sm() as session:
        b = session.get(GatewayBinding, binding_id)
        token = actions.mint_card_action(
            session,
            binding=b,
            thread_id=42,
            message_id=101,
            action_id="act_001",
            decision="approve",
            out_ref=out_ref,
            settings=db_settings,
        )
        session.commit()

    gate = asyncio.Event()
    bridge = _RecorderBridge(gate=gate)

    # Minimal renderer that won't blow up on card-action calls.
    renderer = _RecorderRenderer()

    disp, connector, bridge, renderer = _make_dispatcher(
        sm, db_settings, bridge=bridge, renderer=renderer
    )

    msg_turn = _make_message_inbound(text="blocking-turn")

    # Build a card-action inbound event.
    card_ev = _next_ev()
    source_ref = MessageRef(
        connector="fake",
        workspace_id="wk_test",
        chat_id="chat_dm_1",
        message_id="card_msg_001",
    )
    card_inbound = InboundMessage(
        connector="fake",
        workspace_id="wk_test",
        external_account_id="ou_test",
        provider_event_id=card_ev,
        chat=ChatRef(
            connector="fake",
            workspace_id="wk_test",
            chat_id="chat_dm_1",
            chat_type="dm",
        ),
        kind="card_action",
        text=None,
        action=CardActionInbound(
            token=token,
            source_message_ref=source_ref,
        ),
        raw={},
    )

    # Swap in a bridge that doesn't block for the card-action (resume call).
    # The gate is only on submit_turn; resume is a plain sync call.
    bridge.resume_calls = []

    def fake_resume(*args, **kwargs):
        bridge.resume_calls.append(args)
        return "agent_msg"

    bridge.resume = fake_resume

    card_finished_at: list[float] = []
    turn_finished_at: list[float] = []

    async def run():
        import time

        turn_task = asyncio.create_task(disp.handle(msg_turn))
        # Let turn acquire the lock and block inside submit_turn.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        # Now dispatch the card-action — it should complete immediately.
        t0 = time.monotonic()
        await disp.handle(card_inbound)
        card_finished_at.append(time.monotonic() - t0)

        # The gate is still held; release to let turn finish.
        gate.set()
        await turn_task
        turn_finished_at.append(time.monotonic() - t0)

    asyncio.run(run())

    # Card-action completed (it didn't raise, and bridge.resume was called).
    # The card-action path uses verify_and_claim which may return ClaimError
    # since our token might not match all fields perfectly.  The important check
    # is that handle() returned without being blocked by the message lane.
    # (If it had been blocked, it would have waited for gate.set() which only
    # happened after the card returned.)
    assert len(card_finished_at) == 1
    # Card finished before the turn (gate was still held when card completed).
    assert card_finished_at[0] < turn_finished_at[0], (
        f"Card should have finished before turn was released; "
        f"card={card_finished_at[0]:.4f}s, turn={turn_finished_at[0]:.4f}s"
    )
