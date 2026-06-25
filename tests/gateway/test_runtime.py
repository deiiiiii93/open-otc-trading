"""Tests for GatewayRuntime — single-worker sentinel lease (Task 14).

Test coverage:
1. First runtime start() acquires lease → health()["worker_lock_owner"] is True
   and the fake connector is started. stop() releases cleanly.
2. A second runtime against the same DB, while the first holds a fresh lease,
   start() → does NOT acquire → worker_lock_owner is False, connectors NOT started.
3. Expired-lease reclaim: seed a lock row with lease_expires_at in the past;
   a new runtime start() → acquires (True).
4. prune_inbound_seen: seed one OLD and one FRESH GatewayInboundSeen row;
   run prune_inbound_seen; assert only the old row is deleted.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app import database
from app.config import Settings
from app.models import GatewayInboundSeen, GatewayWorkerLock
from app.services.gateway.connectors.fake import FakeConnector
from app.services.gateway.connectors.feishu import FeishuConnector
from app.services.gateway.runtime import GatewayRuntime, prune_inbound_seen


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def gateway_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'runtime_test.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
        agent_checkpoint_db_path=":memory:",
        # Fast lease for tests — no real sleeps needed
        gateway_lock_lease_s=10,
        gateway_dedupe_ttl_s=3600,
        gateway_enabled_connectors="fake",
    )


@pytest.fixture
def configured_db(gateway_settings: Settings):
    """Configure the database and return (sessionmaker, settings)."""
    database.configure_database(gateway_settings)
    database.init_db()
    return database.SessionLocal, gateway_settings


# ---------------------------------------------------------------------------
# Helper: build a GatewayRuntime with a stub bridge and fast sleep
# ---------------------------------------------------------------------------


class _StubBridge:
    """Minimal bridge stub — no AgentService needed."""

    pass


def _make_runtime(sessionmaker, settings, *, fake_connector=None):
    """Build a GatewayRuntime with a stub bridge and instant sleep."""

    def connector_factory(name: str):
        if name == "fake":
            return fake_connector or FakeConnector()
        raise ValueError(f"Unknown connector: {name}")

    async def _instant_sleep(_seconds):
        # Yield control once but don't actually wait
        await asyncio.sleep(0)

    return GatewayRuntime(
        settings=settings,
        sessionmaker=sessionmaker,
        bridge=_StubBridge(),
        connector_factory=connector_factory,
        sleep=_instant_sleep,
    )


# ---------------------------------------------------------------------------
# Test 1: First runtime acquires lease; fake connector is started; stop cleans up
# ---------------------------------------------------------------------------


def test_first_runtime_acquires_and_starts_connector(configured_db):
    """First runtime start() → acquires lease, fake connector is started; stop() releases."""
    sm, settings = configured_db
    connector = FakeConnector()
    runtime = _make_runtime(sm, settings, fake_connector=connector)

    async def run():
        await runtime.start()

        h = await runtime.health()
        assert h["worker_lock_owner"] is True, "First runtime should own the lock"
        # Fake connector should have been started (on_inbound callback registered)
        assert connector._on_inbound is not None, "Fake connector should have been started"

        await runtime.stop()
        # After stop, connector should be stopped
        assert connector._on_inbound is None, "Fake connector should be stopped"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 2: Second runtime does NOT acquire while first holds a fresh lease
# ---------------------------------------------------------------------------


def test_second_runtime_does_not_acquire_while_first_holds_lease(configured_db):
    """Second runtime start() while first still holds a fresh lease → not owner, connectors not started."""
    sm, settings = configured_db
    connector_a = FakeConnector()
    connector_b = FakeConnector()
    runtime_a = _make_runtime(sm, settings, fake_connector=connector_a)
    runtime_b = _make_runtime(sm, settings, fake_connector=connector_b)

    async def run():
        # First runtime acquires
        await runtime_a.start()
        h_a = await runtime_a.health()
        assert h_a["worker_lock_owner"] is True

        # Second runtime should fail to acquire
        await runtime_b.start()
        h_b = await runtime_b.health()
        assert h_b["worker_lock_owner"] is False, "Second runtime should NOT own the lock"
        # Second runtime's connector should NOT have been started
        assert connector_b._on_inbound is None, "Second runtime's connector should NOT be started"

        # Clean up
        await runtime_a.stop()
        await runtime_b.stop()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 3: Expired lease is reclaimable
# ---------------------------------------------------------------------------


def test_expired_lease_is_reclaimable(configured_db):
    """Seed an expired lock row; a new runtime start() should acquire (True)."""
    sm, settings = configured_db

    # Seed a lock row with a lease that has already expired
    with sm() as session:
        past = datetime.utcnow() - timedelta(seconds=settings.gateway_lock_lease_s + 60)
        row = GatewayWorkerLock(
            id=1,
            owner_token="old-dead-worker",
            acquired_at=past,
            lease_expires_at=past,
        )
        session.add(row)
        session.commit()

    connector = FakeConnector()
    runtime = _make_runtime(sm, settings, fake_connector=connector)

    async def run():
        await runtime.start()
        h = await runtime.health()
        assert h["worker_lock_owner"] is True, "Should reclaim an expired lease"
        assert connector._on_inbound is not None, "Connector should be started after reclaim"
        await runtime.stop()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 4: prune_inbound_seen deletes old rows and leaves fresh rows
# ---------------------------------------------------------------------------


def test_prune_inbound_seen_deletes_old_rows_only(configured_db):
    """prune_inbound_seen deletes rows older than gateway_dedupe_ttl_s; fresh rows survive."""
    sm, settings = configured_db

    # Seed one old row (older than dedupe_ttl) and one fresh row
    with sm() as session:
        old_time = datetime.utcnow() - timedelta(seconds=settings.gateway_dedupe_ttl_s + 100)
        fresh_time = datetime.utcnow() - timedelta(seconds=10)

        old_row = GatewayInboundSeen(
            connector="fake",
            workspace_id="ws_old",
            provider_event_id="ev_old_001",
            state="done",
            owner_token="tok_old",
            claimed_at=old_time,
            seen_at=old_time,
        )
        fresh_row = GatewayInboundSeen(
            connector="fake",
            workspace_id="ws_fresh",
            provider_event_id="ev_fresh_001",
            state="done",
            owner_token="tok_fresh",
            claimed_at=fresh_time,
            seen_at=fresh_time,
        )
        session.add(old_row)
        session.add(fresh_row)
        session.commit()

    # Run prune (prune_inbound_seen commits internally; no outer commit needed)
    with sm() as session:
        deleted = prune_inbound_seen(session, settings)

    assert deleted == 1, f"Expected 1 row deleted, got {deleted}"

    # Verify: fresh row still exists, old row gone
    with sm() as session:
        remaining = session.query(GatewayInboundSeen).all()
        assert len(remaining) == 1, f"Expected 1 row remaining, got {len(remaining)}"
        assert remaining[0].provider_event_id == "ev_fresh_001"


# ---------------------------------------------------------------------------
# Test 5: _ensure_bridge() builds AgentService on the default (no injected bridge) path
# ---------------------------------------------------------------------------


def test_ensure_bridge_default_path_builds_agent_service(
    configured_db, monkeypatch
):
    """_ensure_bridge() must use AgentService, NOT the nonexistent active_agent_service.

    This test would FAIL against the buggy import (ImportError: cannot import
    name 'active_agent_service' from 'app.services.agents') and PASS after the
    fix (AgentBridge wrapping a cheap FakeAgentService).
    """
    sm, settings = configured_db

    # Patch AgentService so no real model/LLM is constructed.
    class FakeAgentService:
        def __init__(self, settings=None, **kw):
            self.settings = settings

    import app.services.agents as agents_module

    monkeypatch.setattr(agents_module, "AgentService", FakeAgentService)

    from app.services.gateway.bridge import AgentBridge

    # Build a GatewayRuntime with NO injected bridge — this is the production path.
    runtime = GatewayRuntime(
        settings=settings,
        sessionmaker=sm,
    )

    bridge = runtime._ensure_bridge()

    assert bridge is not None, "_ensure_bridge() must return a bridge"
    assert isinstance(bridge, AgentBridge), "_ensure_bridge() must return an AgentBridge"
    assert isinstance(bridge._svc, FakeAgentService), "bridge._svc must be a FakeAgentService"
    assert bridge._svc.settings is settings, "AgentService must be constructed with the runtime's settings"


# ---------------------------------------------------------------------------
# Test 6: Heartbeat stand-down actually STOPS connectors when lease is lost
# ---------------------------------------------------------------------------


def test_heartbeat_standdown_stops_connectors(configured_db):
    """When the heartbeat detects a lost lease it must stop all connectors.

    Pre-fix behaviour: the heartbeat broke without stopping connectors, so
    connector._on_inbound remained set (the connector kept processing events
    while a second worker was now active — breaking the single-worker guarantee).

    Post-fix: connector._on_inbound is None after the stand-down tick.
    """
    sm, settings = configured_db
    connector_a = FakeConnector()
    runtime_a = _make_runtime(sm, settings, fake_connector=connector_a)

    async def run():
        # Start runtime_a — it acquires the lock and starts the connector.
        await runtime_a.start()
        assert runtime_a._owner is True
        assert connector_a._on_inbound is not None, "connector should be started"

        # Steal the lease: UPDATE the lock row to a different owner token.
        # runtime_a's next refresh_worker_lock call will return False.
        with sm() as session:
            from sqlalchemy import text as _text
            session.execute(
                _text(
                    "UPDATE gateway_worker_lock"
                    " SET owner_token = 'foreign-worker-token'"
                    " WHERE id = 1"
                )
            )
            session.commit()

        # Let the heartbeat task run one full iteration:
        # - _instant_sleep yields once (asyncio.sleep(0))
        # - refresh_worker_lock returns False
        # - stand-down: stops connectors, breaks
        # We yield several times to let the task progress through all awaits.
        for _ in range(10):
            await asyncio.sleep(0)

        # The heartbeat task should now be done (exited the loop).
        assert runtime_a._heartbeat_task is not None
        assert runtime_a._heartbeat_task.done(), (
            "heartbeat task should have exited after stand-down"
        )

        # Stand-down must have cleared ownership and stopped the connector.
        assert runtime_a._owner is False, "runtime_a should no longer be owner"
        assert connector_a._on_inbound is None, (
            "connector must be stopped during stand-down (on_inbound cleared)"
        )
        assert runtime_a._connectors == [], "connectors list must be cleared"

        # Clean up (stop() is safe to call even after stand-down)
        await runtime_a.stop()

    asyncio.run(run())


# ---------------------------------------------------------------------------
# Test 7: Default connector factory builds FeishuConnector for "feishu"
# ---------------------------------------------------------------------------


def test_default_factory_builds_feishu_connector(configured_db):
    """GatewayRuntime._default_connector_factory('feishu') must return a FeishuConnector.

    This test would FAIL against the old code that raised ValueError for 'feishu'
    (the factory had no access to settings so it refused to build the connector).
    After the fix, the factory is a bound method that constructs a GatewayConfig
    from self._settings and passes it to FeishuConnector — no lark_oapi needed.
    """
    sm, settings = configured_db

    # Build runtime without injecting a custom factory — uses the default.
    runtime = GatewayRuntime(
        settings=settings,
        sessionmaker=sm,
        bridge=_StubBridge(),
    )

    connector = runtime._default_connector_factory("feishu")

    assert isinstance(connector, FeishuConnector), (
        f"Expected FeishuConnector, got {type(connector)!r}"
    )
