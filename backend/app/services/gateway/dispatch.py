"""Dispatcher — orchestration glue for the IM message gateway.

Task 12a: dedup state machine + session ownership.
Task 12b: message path — group refuse, identity, enroll, validate, turn.
Task 12c: card-action path — priority lane, claim→resume→finalize.
Task 12d: per-chat serialization + backpressure.

Transaction boundary contract:
  - After a "new" or "reclaim" claim, ``session.commit()`` is called IMMEDIATELY
    (before any long-running processing) so that a redelivery or a competing
    worker observes the committed lease and returns "skip".
  - A "skip" claim does NOT write; its session is closed/rolled back by the
    ``with sessionmaker() as session:`` context manager.
  - ``_finish_inbound`` sets the row's state to "done" and the caller commits in
    a SEPARATE terminal transaction after processing is complete.

Backpressure (Task 12d):
  - Per-(connector, workspace_id, chat_id) asyncio serialization lock.
  - Only the MESSAGE path uses lanes; card-actions bypass entirely.
  - Bounded queue: when depth > gateway_max_queued_per_chat, drop newest turn
    with a "Too many pending messages" notice.
  - Age cap: when a queued turn has waited > gateway_queue_max_age_s after
    acquiring the lock, drop it with a "Message too old" notice.
"""
from __future__ import annotations

import asyncio
import secrets
import time as _time_module
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Literal, Tuple

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import GatewayBinding, GatewayCardAction, GatewayInboundSeen
from app.services.gateway import actions
from app.services.gateway import identity as identity_svc
from app.services.gateway.coalescer import ResumeOk, ResumeRaised
from app.services.gateway.types import InboundMessage, OutboundMessage

_HELP_TEXT = "I can only read text messages."

# Lane key type: (connector, workspace_id, chat_id)
_LaneKey = Tuple[str, str, str]


@dataclass
class _Lane:
    """Per-chat serialization lane for the message path."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    depth: int = 0  # count of events in the lane (running + waiting)


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
        # Called by the connector's receive loop (awaited):
        await dispatcher.handle(inbound_message)
    """

    def __init__(
        self,
        connector,
        bridge,
        renderer,
        sessionmaker: Callable[[], Session],
        settings,
        *,
        monotonic: Callable[[], float] = _time_module.monotonic,
    ) -> None:
        self._connector = connector
        self._bridge = bridge
        self._renderer = renderer
        self._sessionmaker = sessionmaker
        self._settings = settings
        self._monotonic = monotonic
        # Per-chat serialization lanes (Task 12d).
        self._lanes: dict[_LaneKey, _Lane] = {}

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

    async def handle(self, inbound: InboundMessage) -> None:
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
            await self._run_in_lane(inbound)
        else:
            await self._handle_card_action_async(inbound)

    # ------------------------------------------------------------------
    # Per-chat lane: serialization + backpressure (Task 12d)
    # ------------------------------------------------------------------

    async def _run_in_lane(self, inbound: InboundMessage) -> None:
        """Serialise message turns per (connector, workspace_id, chat_id).

        Only the message path uses lanes.  Card-actions bypass entirely.

        Depth semantics:
          depth=0: lane idle (no entry in _lanes)
          depth=N: N turns are either running or waiting in the lane

        Drop-newest:
          Before admitting a new turn, check whether the current depth already
          exceeds ``gateway_max_queued_per_chat``.  If depth > max_queued the
          NEW turn is dropped immediately (before incrementing depth).

        Age-cap:
          After a turn acquires the lock, check how long it waited.  If the
          wait exceeds ``gateway_queue_max_age_s``, drop it without processing.
        """
        settings = self._settings
        max_queued: int = settings.gateway_max_queued_per_chat
        max_age_s: float = settings.gateway_queue_max_age_s

        key: _LaneKey = (
            inbound.chat.connector,
            inbound.chat.workspace_id,
            inbound.chat.chat_id,
        )

        # --- Get or create the lane ------------------------------------------
        lane = self._lanes.get(key)
        if lane is None:
            lane = _Lane()
            self._lanes[key] = lane

        # --- Bounded-queue check (drop-newest) --------------------------------
        if lane.depth > max_queued:
            # Too many turns already queued — drop this newest one.
            await self._connector.send_message(
                inbound.chat,
                OutboundMessage(
                    text="Too many pending messages — dropped the latest. Try again shortly."
                ),
                idempotency_key=f"{inbound.provider_event_id}:drop-overflow",
            )
            with self._sessionmaker() as session:
                self._finish_inbound(session, inbound)
                session.commit()
            # Clean up empty lane if nobody else is using it.
            if lane.depth == 0:
                self._lanes.pop(key, None)
            return

        # --- Admit the turn: increment depth and record enqueue time ---------
        lane.depth += 1
        enqueued_at: float = self._monotonic()

        try:
            async with lane.lock:
                # --- Age-cap check (post-lock) --------------------------------
                waited = self._monotonic() - enqueued_at
                if waited > max_age_s:
                    # Turn is too old — drop it.
                    await self._connector.send_message(
                        inbound.chat,
                        OutboundMessage(
                            text="Message too old to process — please resend."
                        ),
                        idempotency_key=f"{inbound.provider_event_id}:drop-stale",
                    )
                    with self._sessionmaker() as session:
                        self._finish_inbound(session, inbound)
                        session.commit()
                    return

                # --- Run the turn --------------------------------------------
                await self._handle_message_async(inbound)
        finally:
            lane.depth -= 1
            if lane.depth == 0:
                self._lanes.pop(key, None)

    # ------------------------------------------------------------------
    # Message path (Task 12b)
    # ------------------------------------------------------------------

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
            # Commit any card-action tokens minted during render_turn (e.g.
            # GatewayCardAction rows from build_approval_card / mint_card_action).
            # Without this commit the rows are rolled back when the with-block exits,
            # making the HITL approval path completely inoperable.
            session.commit()

        with self._sessionmaker() as session:
            self._finish_inbound(session, inbound)
            session.commit()

    # ------------------------------------------------------------------
    # Card-action path (Task 12c)
    # ------------------------------------------------------------------

    async def _handle_card_action_async(self, inbound: InboundMessage) -> None:
        """Async implementation of the card-action path.

        Priority lane — never queued behind turns.

        Execution order:
        1. verify_and_claim the token (atomic DB update).
        2a. Losing/invalid claim → render_claim_error; then finish dedup row.
        2b. Winning claim → commit the resolving state BEFORE resume (fail-closed);
            look up the binding; call bridge.resume wrapped broadly so a raise
            becomes ResumeRaised; render_resume_result; then finish dedup row.
        """
        with self._sessionmaker() as session:
            result = actions.verify_and_claim(
                session,
                token=inbound.action.token,
                source_message_ref=inbound.action.source_message_ref,
            )

            if isinstance(result, actions.ClaimError):
                # Losing or invalid claim — notify the caller and finish.
                await self._renderer.render_claim_error(
                    session, inbound.action.source_message_ref, result
                )
                self._finish_inbound(session, inbound)
                session.commit()
            else:
                # Winning claim: commit the "resolving" status BEFORE resume
                # so that a crash during resume leaves the row non-pending.
                session.commit()

                # Fetch the binding AND re-fetch the card-action row in a fresh
                # session.  The `result` object (GatewayCardAction) was attached
                # to the now-closed first session; passing it to render_resume_result
                # (which calls mark_resolved/mark_unknown → flush) would raise
                # DetachedInstanceError.  Re-fetching via session2 keeps the row
                # attached to the session that render_resume_result operates on.
                with self._sessionmaker() as session2:
                    binding = session2.get(GatewayBinding, result.binding_id)
                    live_row = session2.get(GatewayCardAction, result.id)

                    try:
                        agent_message = self._bridge.resume(
                            session2,
                            binding,
                            live_row.thread_id,
                            live_row.message_id,
                            live_row.action_id,
                            live_row.decision,
                        )
                        outcome = ResumeOk(agent_message=agent_message)
                    except Exception:
                        outcome = ResumeRaised()

                    await self._renderer.render_resume_result(
                        session2,
                        binding,
                        live_row,
                        inbound.action.source_message_ref,
                        outcome,
                    )
                    self._finish_inbound(session2, inbound)
                    session2.commit()
