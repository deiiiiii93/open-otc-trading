"""Dispatcher — orchestration glue for the IM message gateway.

Task 12a: dedup state machine + session ownership.
Task 12b: message path — group refuse, identity, enroll, validate, turn.

Transaction boundary contract:
  - After a "new" or "reclaim" claim, ``session.commit()`` is called IMMEDIATELY
    (before any long-running processing) so that a redelivery or a competing
    worker observes the committed lease and returns "skip".
  - A "skip" claim does NOT write; its session is closed/rolled back by the
    ``with sessionmaker() as session:`` context manager.
  - ``_finish_inbound`` sets the row's state to "done" and the caller commits in
    a SEPARATE terminal transaction after processing is complete.

Tasks 12c (card-action path) and 12d (backpressure) will extend ``handle()``
and add new methods to this class.
"""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta
from typing import Callable, Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import GatewayInboundSeen
from app.services.gateway import identity as identity_svc
from app.services.gateway.types import InboundMessage, OutboundMessage


class Dispatcher:
    """Orchestrates inbound IM events through dedup, identity, and agent bridge.

    The Dispatcher OWNS sessions: it opens one DB session per inbound event via
    the injected ``sessionmaker`` (a callable returning a context-manager Session).

    Usage::

        dispatcher = Dispatcher(
            connector=connector,
            bridge=bridge,
            renderer=renderer,
            sessionmaker=database.SessionLocal,
            settings=settings,
        )
        # Called by the connector's receive loop:
        dispatcher.handle(inbound_message)
    """

    def __init__(
        self,
        connector,
        bridge,
        renderer,
        sessionmaker: Callable[[], Session],
        settings,
    ) -> None:
        self._connector = connector
        self._bridge = bridge
        self._renderer = renderer
        self._sessionmaker = sessionmaker
        self._settings = settings

    # ------------------------------------------------------------------
    # Dedup state machine (Task 12a)
    # ------------------------------------------------------------------

    def _claim_inbound(
        self,
        session: Session,
        inbound: InboundMessage,
    ) -> Literal["new", "skip", "reclaim"]:
        """Attempt to claim the inbound event for processing.

        Returns:
            "new"     — first time we see this event; row inserted, ready to process.
            "skip"    — event is already claimed (fresh lease) or terminal (done/failed).
            "reclaim" — previous processing worker's lease expired; we took it over.

        After a "new" or "reclaim" return the CALLER must immediately call
        ``session.commit()`` before starting any long-running work.
        """
        owner_token = secrets.token_urlsafe(16)
        now = datetime.utcnow()
        lease_s = self._settings.gateway_dedupe_lease_s

        # --- Attempt optimistic INSERT ----------------------------------------
        try:
            row = GatewayInboundSeen(
                connector=inbound.connector,
                workspace_id=inbound.workspace_id,
                provider_event_id=inbound.provider_event_id,
                state="processing",
                owner_token=owner_token,
                claimed_at=now,
                attempts=1,
            )
            session.add(row)
            session.flush()  # surface the unique-constraint violation early
            return "new"
        except IntegrityError:
            # Concurrent insert won the race — roll back the failed sub-statement
            # and fall through to the existing-row branch.
            session.rollback()

        # --- Inspect the existing row ----------------------------------------
        stmt = select(GatewayInboundSeen).where(
            GatewayInboundSeen.connector == inbound.connector,
            GatewayInboundSeen.workspace_id == inbound.workspace_id,
            GatewayInboundSeen.provider_event_id == inbound.provider_event_id,
        )
        existing = session.scalars(stmt).first()

        if existing is None:
            # Should not normally happen; treat as skip to avoid a crash.
            return "skip"

        if existing.state != "processing":
            # Terminal state ("done" or "failed") — do not re-process.
            return "skip"

        # --- Still in "processing" — check lease freshness -------------------
        cutoff = now - timedelta(seconds=lease_s)
        if existing.claimed_at is not None and existing.claimed_at > cutoff:
            # Lease is still fresh; another worker owns it.
            return "skip"

        # --- Lease expired — take it over ------------------------------------
        existing.owner_token = owner_token
        existing.claimed_at = now
        existing.attempts = existing.attempts + 1
        # state stays "processing"
        session.flush()
        return "reclaim"

    def _finish_inbound(self, session: Session, inbound: InboundMessage) -> None:
        """Mark the dedup row as terminal ('done').

        The caller is responsible for calling ``session.commit()`` after this
        method returns.  This commit should happen in a SEPARATE transaction
        from the initial claim commit.
        """
        stmt = select(GatewayInboundSeen).where(
            GatewayInboundSeen.connector == inbound.connector,
            GatewayInboundSeen.workspace_id == inbound.workspace_id,
            GatewayInboundSeen.provider_event_id == inbound.provider_event_id,
        )
        row = session.scalars(stmt).first()
        if row is not None:
            row.state = "done"
            session.flush()

    # ------------------------------------------------------------------
    # Entry point (12a + 12b extension)
    # ------------------------------------------------------------------

    def handle(self, inbound: InboundMessage) -> None:
        """Process one inbound IM event end-to-end.

        Transaction ordering:
        1. Open a session, call _claim_inbound.
        2. If "skip", rollback/close and return immediately (skip rolls back).
        3. If "new" or "reclaim", COMMIT the lease immediately so that any
           concurrent redelivery observes the fresh claim and skips.
        4. Dispatch to kind-specific processing seam.
        5. The kind handler is responsible for calling _finish_inbound + commit
           in its own terminal transaction.
        """
        with self._sessionmaker() as session:
            result = self._claim_inbound(session, inbound)
            if result == "skip":
                session.rollback()
                return
            # Commit the lease before doing any processing.
            session.commit()

        if inbound.kind == "message":
            self._handle_message(inbound)
        else:
            # 12c: card-action path — leave the seam intact
            with self._sessionmaker() as session:
                self._finish_inbound(session, inbound)
                session.commit()

    # ------------------------------------------------------------------
    # Message path (Task 12b)
    # ------------------------------------------------------------------

    def _handle_message(self, inbound: InboundMessage) -> None:
        """Orchestrate the message path and run async steps synchronously."""
        asyncio.run(self._handle_message_async(inbound))

    async def _handle_message_async(self, inbound: InboundMessage) -> None:
        """Async implementation of the message path.

        Execution order (first matching outcome wins):
        1. Group refuse — only DMs are supported.
        2. Resolve identity — look up the active binding.
        3. Enroll — if unbound and text looks like a code, redeem it.
        4. Unbound refuse — binding still None after enroll attempt.
        5. Text validation — None, blank, or too-long.
        6. Turn — submit to agent bridge and render the result.

        Every path (including refusals) finishes with _finish_inbound + commit.
        """
        connector = self._connector
        settings = self._settings

        # ------------------------------------------------------------------
        # 1. Group refuse
        # ------------------------------------------------------------------
        if inbound.chat.chat_type == "group":
            await connector.send_message(
                inbound.chat,
                OutboundMessage(text="The agent is only available in direct messages."),
                idempotency_key=f"{inbound.provider_event_id}:refuse-group",
            )
            with self._sessionmaker() as session:
                self._finish_inbound(session, inbound)
                session.commit()
            return

        # ------------------------------------------------------------------
        # 2. Resolve identity
        # ------------------------------------------------------------------
        with self._sessionmaker() as session:
            binding = identity_svc.active_binding(
                session,
                connector=inbound.connector,
                external_account_id=inbound.external_account_id,
                workspace_id=inbound.workspace_id,
            )

        # ------------------------------------------------------------------
        # 3. Enroll (only when unbound and text looks like a code)
        # ------------------------------------------------------------------
        if binding is None and inbound.text is not None and identity_svc.is_code_shaped(inbound.text.strip()):
            with self._sessionmaker() as session:
                new_binding = identity_svc.redeem_code(
                    session,
                    connector=inbound.connector,
                    external_account_id=inbound.external_account_id,
                    workspace_id=inbound.workspace_id,
                    code=inbound.text.strip(),
                    settings=settings,
                )
                if new_binding is not None:
                    session.commit()
                    # Enrollment succeeded
                    await connector.send_message(
                        inbound.chat,
                        OutboundMessage(
                            text="Enrolled — you can now message the desk agent."
                        ),
                        idempotency_key=f"{inbound.provider_event_id}:enroll-ok",
                    )
                    with self._sessionmaker() as session2:
                        self._finish_inbound(session2, inbound)
                        session2.commit()
                    return
                else:
                    # Code was invalid or expired
                    await connector.send_message(
                        inbound.chat,
                        OutboundMessage(text="Invalid or expired linking code."),
                        idempotency_key=f"{inbound.provider_event_id}:enroll-fail",
                    )
                    with self._sessionmaker() as session2:
                        self._finish_inbound(session2, inbound)
                        session2.commit()
                    return

        # ------------------------------------------------------------------
        # 4. Unbound refuse
        # ------------------------------------------------------------------
        if binding is None:
            await connector.send_message(
                inbound.chat,
                OutboundMessage(
                    text="You're not linked yet. Send your linking code to enroll."
                ),
                idempotency_key=f"{inbound.provider_event_id}:refuse-unbound",
            )
            with self._sessionmaker() as session:
                self._finish_inbound(session, inbound)
                session.commit()
            return

        # ------------------------------------------------------------------
        # 5. Text validation
        # ------------------------------------------------------------------
        _HELP_TEXT = "I can only read text messages."

        if inbound.text is None or inbound.text.strip() == "":
            await connector.send_message(
                inbound.chat,
                OutboundMessage(text=_HELP_TEXT),
                idempotency_key=f"{inbound.provider_event_id}:help-text",
            )
            with self._sessionmaker() as session:
                self._finish_inbound(session, inbound)
                session.commit()
            return

        if len(inbound.text) > settings.gateway_max_inbound_chars:
            await connector.send_message(
                inbound.chat,
                OutboundMessage(text="Message too long."),
                idempotency_key=f"{inbound.provider_event_id}:refuse-toolong",
            )
            with self._sessionmaker() as session:
                self._finish_inbound(session, inbound)
                session.commit()
            return

        # ------------------------------------------------------------------
        # 6. Turn — bound + valid text
        # ------------------------------------------------------------------
        with self._sessionmaker() as session:
            thread = self._bridge.thread_for(session, binding, inbound.chat)
            session.commit()

        with self._sessionmaker() as session:
            events = await self._bridge.submit_turn(
                session, binding, thread, inbound.text.strip()
            )
            await self._renderer.render_turn(session, binding, inbound.chat, events)

        with self._sessionmaker() as session:
            self._finish_inbound(session, inbound)
            session.commit()
