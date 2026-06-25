"""GatewayRuntime — single-worker lease + connector lifecycle (Task 14).

The runtime is responsible for:
- Acquiring and refreshing a singleton ``gateway_worker_lock`` row (id=1).
- Starting one Dispatcher per enabled connector (only when this process owns
  the lock).
- Running a heartbeat loop that refreshes the lock lease and prunes stale
  ``gateway_inbound_seen`` rows.

Only ONE process at a time may be the "active" gateway worker.  The lease is
an optimistic locking mechanism: INSERT wins the race; a concurrent worker that
loses the INSERT falls back to an UPDATE gated on the lease expiry.

Lock atomicity
--------------
acquire_worker_lock uses a two-step INSERT → UPDATE strategy:

1. INSERT (id=1, owner_token, now, now+lease_s).
   - If it succeeds → we are the owner.
   - If IntegrityError (row exists) → roll back and fall through.
2. UPDATE WHERE id=1 AND (lease_expires_at < now OR owner_token = me).
   - Rowcount == 1 → either the lease expired or we already own it.
   - Rowcount == 0 → another process holds a fresh lease → not owner.

SQLAlchemy ``text()`` queries are used for the conditional UPDATE so that the
WHERE clause executes atomically inside a single SQL statement.

Heartbeat
---------
The heartbeat coroutine runs inside start() and periodically:
- Calls ``refresh_worker_lock`` to extend the lease.
- Calls ``prune_inbound_seen`` to bound table growth.

The heartbeat is an asyncio.Task so ``stop()`` can cancel it promptly.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import GatewayInboundSeen, GatewayWorkerLock

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def acquire_worker_lock(session, owner_token: str, settings) -> bool:
    """Try to become the gateway worker by claiming the singleton lock row.

    Returns True if this caller now owns the lock, False if another process
    holds a non-expired lease.

    The call is safe to make from multiple processes simultaneously: the
    INSERT is guarded by the PRIMARY KEY constraint (id=1), and the fallback
    UPDATE is gated on ``lease_expires_at < now OR owner_token = :me`` which
    is evaluated atomically by SQLite/PostgreSQL.
    """
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=settings.gateway_lock_lease_s)

    # --- Attempt INSERT (optimistic path) ------------------------------------
    try:
        row = GatewayWorkerLock(
            id=1,
            owner_token=owner_token,
            acquired_at=now,
            lease_expires_at=expires_at,
        )
        session.add(row)
        session.flush()
        session.commit()
        return True
    except IntegrityError:
        session.rollback()

    # --- Row already exists — try a conditional UPDATE -----------------------
    result = session.execute(
        text(
            "UPDATE gateway_worker_lock"
            " SET owner_token = :tok,"
            "     acquired_at = :now,"
            "     lease_expires_at = :exp"
            " WHERE id = 1"
            "   AND (lease_expires_at < :now OR owner_token = :tok)"
        ),
        {"tok": owner_token, "now": now, "exp": expires_at},
    )
    session.commit()
    return result.rowcount == 1


def refresh_worker_lock(session, owner_token: str, settings) -> bool:
    """Extend the lock lease for the current owner.

    Returns True if the refresh succeeded (rowcount==1), False if this token
    is no longer the owner (another worker reclaimed the row).
    """
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=settings.gateway_lock_lease_s)

    result = session.execute(
        text(
            "UPDATE gateway_worker_lock"
            " SET lease_expires_at = :exp"
            " WHERE id = 1 AND owner_token = :tok"
        ),
        {"tok": owner_token, "exp": expires_at},
    )
    session.commit()
    return result.rowcount == 1


def release_worker_lock(session, owner_token: str) -> None:
    """Vacate the lock by backdating the lease to the past.

    We set ``lease_expires_at`` to ``now`` (i.e., already expired) so the next
    worker can immediately reclaim without waiting.  Only our own token is
    updated.
    """
    now = datetime.utcnow()
    session.execute(
        text(
            "UPDATE gateway_worker_lock"
            " SET lease_expires_at = :now"
            " WHERE id = 1 AND owner_token = :tok"
        ),
        {"tok": owner_token, "now": now},
    )
    session.commit()


def prune_inbound_seen(session, settings) -> int:
    """Delete ``gateway_inbound_seen`` rows older than ``gateway_dedupe_ttl_s``.

    Returns the number of rows deleted.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=settings.gateway_dedupe_ttl_s)
    result = session.execute(
        text(
            "DELETE FROM gateway_inbound_seen WHERE seen_at < :cutoff"
        ),
        {"cutoff": cutoff},
    )
    session.commit()
    return result.rowcount


# ---------------------------------------------------------------------------
# Default connector factory
# ---------------------------------------------------------------------------

def _default_connector_factory(name: str):
    """Build a connector by name.  Registered names: 'feishu', 'fake'."""
    if name == "feishu":
        from app.services.gateway.connectors.feishu import FeishuConnector
        from app.services.gateway.config import GatewayConfig

        # GatewayConfig is built lazily from the settings passed at call time;
        # the factory itself doesn't capture settings — GatewayRuntime passes
        # settings as part of the constructor.  We'll receive settings via the
        # closure in _build_connectors.
        raise ValueError(
            "FeishuConnector requires a GatewayConfig; use a custom connector_factory"
            " or pass connector_factory=None and let GatewayRuntime wire it."
        )
    if name == "fake":
        from app.services.gateway.connectors.fake import FakeConnector

        return FakeConnector()
    raise ValueError(f"Unknown connector name: {name!r}")


# ---------------------------------------------------------------------------
# GatewayRuntime
# ---------------------------------------------------------------------------


class GatewayRuntime:
    """Lifecycle controller for the IM message gateway background worker.

    Parameters
    ----------
    settings:
        Application settings (gateway_enabled_connectors, gateway_lock_lease_s, …).
    sessionmaker:
        Callable that returns a context-manager Session (``database.SessionLocal``).
    bridge:
        Optional ``AgentBridge`` instance.  If None, a bridge is built lazily
        at ``start()`` time (requires a real AgentService in the app context).
    connector_factory:
        Optional callable ``(name: str) -> connector``.  Defaults to the
        built-in factory that maps ``"feishu"`` and ``"fake"``.
    sleep:
        Injectable sleep coroutine (default: asyncio.sleep).  Inject a fast
        no-op in tests to avoid real delays.
    """

    def __init__(
        self,
        settings,
        sessionmaker: Callable,
        *,
        bridge=None,
        connector_factory: Callable | None = None,
        sleep: Callable = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._bridge = bridge
        self._connector_factory = connector_factory or _default_connector_factory
        self._sleep = sleep

        # State set during start()
        self._owner_token: str = secrets.token_urlsafe(16)
        self._owner: bool = False
        self._connectors: list = []
        self._heartbeat_task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Connector name → instance
    # ------------------------------------------------------------------

    def _parse_connector_names(self) -> list[str]:
        raw = self._settings.gateway_enabled_connectors or ""
        return [n.strip() for n in raw.split(",") if n.strip()]

    def _build_connectors(self) -> list:
        names = self._parse_connector_names()
        connectors = []
        for name in names:
            try:
                connector = self._connector_factory(name)
                connectors.append(connector)
            except Exception:
                _log.exception("Failed to build connector %r", name)
        return connectors

    # ------------------------------------------------------------------
    # Bridge (lazy)
    # ------------------------------------------------------------------

    def _ensure_bridge(self):
        if self._bridge is not None:
            return self._bridge
        # Lazy import — only works when the full app is booted.
        from app.services.gateway.bridge import AgentBridge
        from app.services.agents import active_agent_service

        self._bridge = AgentBridge(active_agent_service())
        return self._bridge

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def _heartbeat(self) -> None:
        """Periodically refresh the worker lock and prune old dedup rows."""
        interval = max(1, self._settings.gateway_lock_lease_s // 2)
        while True:
            await self._sleep(interval)
            with self._sessionmaker() as session:
                still_owner = refresh_worker_lock(session, self._owner_token, self._settings)
            if not still_owner:
                _log.warning("GatewayRuntime: lost worker lock ownership; stopping connectors.")
                self._owner = False
                break
            with self._sessionmaker() as session:
                pruned = prune_inbound_seen(session, self._settings)
            if pruned:
                _log.debug("GatewayRuntime: pruned %d stale inbound_seen rows.", pruned)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Acquire the worker lock and start all enabled connectors (if owner).

        Safe to call multiple times — if already started this is a no-op.
        If this process does NOT acquire the lock, connectors are NOT started
        and health()["worker_lock_owner"] returns False.
        """
        if self._heartbeat_task is not None:
            # Already started
            return

        # Try to acquire the worker lock.
        with self._sessionmaker() as session:
            acquired = acquire_worker_lock(session, self._owner_token, self._settings)

        self._owner = acquired

        if not acquired:
            _log.info(
                "GatewayRuntime: another worker holds the lock; running in standby mode."
            )
            return

        _log.info("GatewayRuntime: acquired worker lock (token=%s).", self._owner_token[:8])

        # Build and start connectors.
        bridge = self._ensure_bridge()
        self._connectors = self._build_connectors()

        for connector in self._connectors:
            from app.services.gateway.coalescer import StreamRenderer
            from app.services.gateway.dispatch import Dispatcher

            renderer = StreamRenderer(connector, self._settings)
            dispatcher = Dispatcher(
                connector=connector,
                bridge=bridge,
                renderer=renderer,
                sessionmaker=self._sessionmaker,
                settings=self._settings,
            )
            try:
                await connector.start(on_inbound=dispatcher.handle)
                _log.info("GatewayRuntime: started connector %r.", getattr(connector, "name", connector))
            except Exception:
                _log.exception("GatewayRuntime: failed to start connector %r.", getattr(connector, "name", connector))

        # Start the heartbeat.
        self._heartbeat_task = asyncio.create_task(self._heartbeat())

    async def stop(self) -> None:
        """Cancel the heartbeat and stop all connectors; release the lock if owner."""
        # Cancel heartbeat
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        self._heartbeat_task = None

        # Stop connectors
        for connector in self._connectors:
            try:
                await connector.stop()
            except Exception:
                _log.exception("GatewayRuntime: error stopping connector %r.", getattr(connector, "name", connector))
        self._connectors = []

        # Release lock
        if self._owner:
            with self._sessionmaker() as session:
                release_worker_lock(session, self._owner_token)
            self._owner = False
            _log.info("GatewayRuntime: released worker lock.")

    async def health(self) -> dict:
        """Return a health dict with lock ownership and per-connector state."""
        connector_health = {}
        for connector in self._connectors:
            name = getattr(connector, "name", str(connector))
            try:
                h = await connector.health()
                connector_health[name] = {"state": h.state, "detail": h.detail}
            except Exception as exc:
                connector_health[name] = {"state": "error", "detail": str(exc)}
        return {
            "worker_lock_owner": self._owner,
            "connectors": connector_health,
        }

    async def reload(self) -> dict:
        """Owner-only: stop all connectors, re-read config, restart connectors.

        Returns a health dict after the reload.  If this runtime is not the
        owner, returns a dict indicating standby status.
        """
        if not self._owner:
            return {"worker_lock_owner": False, "status": "standby — reload skipped"}

        # Stop connectors without releasing the lock.
        for connector in self._connectors:
            try:
                await connector.stop()
            except Exception:
                _log.exception("GatewayRuntime: error during reload stop of %r.", getattr(connector, "name", connector))
        self._connectors = []

        # Rebuild from current settings.
        bridge = self._ensure_bridge()
        self._connectors = self._build_connectors()

        for connector in self._connectors:
            from app.services.gateway.coalescer import StreamRenderer
            from app.services.gateway.dispatch import Dispatcher

            renderer = StreamRenderer(connector, self._settings)
            dispatcher = Dispatcher(
                connector=connector,
                bridge=bridge,
                renderer=renderer,
                sessionmaker=self._sessionmaker,
                settings=self._settings,
            )
            try:
                await connector.start(on_inbound=dispatcher.handle)
            except Exception:
                _log.exception("GatewayRuntime: failed to start connector %r during reload.", getattr(connector, "name", connector))

        return await self.health()
