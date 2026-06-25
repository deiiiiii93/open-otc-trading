"""AgentBridge — facade tying the IM gateway layer to the agent service.

The bridge is the single chokepoint through which all IM-originated turns
enter the agent service.  It owns the thread-map lookup/creation and routes
each inbound message to the correct agent thread.

Session ownership contract
--------------------------
The bridge never opens its own session.  Callers (the dispatcher, Tasks
12a–12d) own a ``sessionmaker`` and pass a live ``session`` per inbound
event; transaction boundaries are committed by the dispatcher at each
terminal step.

``stream_and_persist`` manages its own session for persistence (exactly as
the HTTP /chat endpoint does today) — the ``session`` argument is used only
for the bridge's own reads/writes (thread-map lookup, audit queries, etc.)
and is NOT forwarded into the async generator.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import AgentThread, GatewayBinding, GatewayThreadMap
from app.services.gateway.sse import parse_sse_stream
from app.services.gateway.types import AgentEvent, ChatRef

if TYPE_CHECKING:
    from app.models import AgentMessage
    from app.services.agents import AgentService


class AgentBridge:
    """Facade over AgentService for IM-originated requests.

    Parameters
    ----------
    agent_service:
        The ``AgentService`` instance to delegate to.  In production this is
        the ``active_agent_service`` created inside ``create_app``; in tests
        a fresh ``AgentService(settings=None)`` (disabled — no LLM) is fine
        for structural tests that spy on the service methods.
    """

    def __init__(self, agent_service: "AgentService") -> None:
        self._svc = agent_service

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def thread_for(
        self,
        session: Session,
        binding: GatewayBinding,
        chat: ChatRef,
    ) -> AgentThread:
        """Return the AgentThread mapped to ``(binding, chat)``, creating both
        the thread and the map row on the first call.

        The map-row insert uses ``INSERT OR IGNORE`` (SQLite) / ``INSERT … ON
        CONFLICT DO NOTHING`` so that two concurrent callers for the same chat
        cannot both insert — the winner's row survives; the loser silently
        skips.  Either way, the subsequent SELECT returns the authoritative row.

        Parameters
        ----------
        session:
            Caller-owned DB session.  The caller is responsible for committing
            (or flushing) before the next logical step.
        binding:
            The active ``GatewayBinding`` for this IM account.
        chat:
            The ``ChatRef`` identifying the specific IM chat.

        Returns
        -------
        AgentThread
            The thread associated with this (binding, chat) pair.
        """
        # 1. Look up an existing map row.
        existing: GatewayThreadMap | None = (
            session.query(GatewayThreadMap)
            .filter_by(binding_id=binding.id, chat_id=chat.chat_id)
            .one_or_none()
        )
        if existing is not None:
            thread = session.get(AgentThread, existing.thread_id)
            if thread is not None:
                return thread

        # 2. No map row yet — create the thread first.
        thread = self._svc.create_thread(
            session,
            title=f"IM {chat.connector}:{chat.chat_id}",
            character=binding.persona,
        )
        session.flush()  # obtain thread.id

        # 3. Insert the map row, ignoring a concurrent duplicate.
        #    SQLAlchemy Core INSERT … ON CONFLICT(binding_id, chat_id) DO NOTHING
        #    is dialect-aware via the prefix_with trick; simpler is raw SQL which
        #    works on both SQLite ("INSERT OR IGNORE") and PostgreSQL ("ON CONFLICT
        #    DO NOTHING").  We use the SQLAlchemy dialect-agnostic approach:
        #    use ``session.execute`` with a Core insert + ``on_conflict_do_nothing``.
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = sqlite_insert(GatewayThreadMap.__table__).values(
            binding_id=binding.id,
            chat_id=chat.chat_id,
            thread_id=thread.id,
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["binding_id", "chat_id"]
        )
        session.execute(stmt)
        session.flush()

        # 4. Re-read the authoritative row (in case a concurrent insert won).
        canonical: GatewayThreadMap | None = (
            session.query(GatewayThreadMap)
            .filter_by(binding_id=binding.id, chat_id=chat.chat_id)
            .one_or_none()
        )
        if canonical is not None and canonical.thread_id != thread.id:
            # A concurrent caller already inserted; use their thread.
            winner = session.get(AgentThread, canonical.thread_id)
            if winner is not None:
                return winner

        return thread

    async def submit_turn(
        self,
        session: Session,
        binding: GatewayBinding,
        thread: AgentThread,
        text: str,
    ) -> AsyncIterator[AgentEvent]:
        """Stream one agent turn, yielding ``AgentEvent`` objects.

        ``stream_and_persist`` manages its own DB session for persistence
        (identical to the HTTP /chat endpoint); the ``session`` argument here
        is the bridge's caller-owned session for gateway-layer reads and is
        NOT forwarded into the generator.

        Parameters
        ----------
        session:
            Caller-owned session (used for bridge-layer reads, not passed to
            ``stream_and_persist``).
        binding:
            The active binding; supplies ``desk_user`` (actor) and ``persona``.
        thread:
            The ``AgentThread`` to post the message to.
        text:
            The user message content.

        Yields
        ------
        AgentEvent
            Typed events parsed from the SSE stream produced by the agent.
        """
        sse_iter = self._svc.stream_and_persist(
            thread_id=thread.id,
            content=text,
            requested_character=binding.persona,
            page_context=None,
            context_usage=None,
            accounting_date=None,
            model_selection=self._svc.normalize_model_selection(None),
            yolo_mode=False,
            envelope="DESK_WORKFLOW",
            confirmed_cost_preview=False,
            actor=binding.desk_user,
        )
        async for event in parse_sse_stream(sse_iter):
            yield event

    def resume(
        self,
        session: Session,
        binding: GatewayBinding,
        thread_id: int,
        message_id: int,
        action_id: str,
        decision: str,
    ) -> "AgentMessage":
        """Resolve a pending HITL action, forwarding ``binding.desk_user`` as
        the auditable actor identity.

        Parameters
        ----------
        session:
            Caller-owned session passed through to ``resume_pending_action``.
        binding:
            The active binding; its ``desk_user`` field becomes the ``actor``
            recorded in the audit log — ensuring the IM-originated identity is
            propagated, not the web-layer default.
        thread_id, message_id, action_id, decision:
            Forwarded verbatim to ``resume_pending_action``.

        Returns
        -------
        AgentMessage
            The resulting message after the action is resolved.
        """
        return self._svc.resume_pending_action(
            thread_id=thread_id,
            message_id=message_id,
            action_id=action_id,
            decision=decision,
            actor=binding.desk_user,
            session=session,
        )
