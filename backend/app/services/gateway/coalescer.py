"""Gateway Coalescer — stream renderer for IM message gateway (Tasks 11a–11d).

Renders a stream of AgentEvents to an IM connector, handling:
  11a: token buffering, chunking, jittered retry
  11b: approval cards for pending actions
  11c: resume result rendering (resolved, raised, claim errors)
  11d: mid-flight binding revocation detection
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.models import GatewayBinding, GatewayCardAction

from app.schemas import AgentActionProposal
from app.services.gateway.actions import ClaimError, mark_resolved, mark_unknown
from app.services.gateway.cards import build_approval_card
from app.services.gateway.config import GatewayConfig
from app.services.gateway.identity import active_binding
from app.services.audit import record_audit
from app.services.gateway.types import (
    AgentEvent,
    CardSection,
    ChatRef,
    MessageRef,
    OutboundCard,
    OutboundMessage,
)


# ---------------------------------------------------------------------------
# Resume result types (11c)
# ---------------------------------------------------------------------------


@dataclass
class ResumeOk:
    """The agent completed the action and produced a follow-up message."""
    agent_message: object  # AgentMessage


@dataclass
class ResumeRaised:
    """The agent raised an exception — outcome unknown."""
    pass


# ---------------------------------------------------------------------------
# StreamRenderer
# ---------------------------------------------------------------------------


class StreamRenderer:
    """Render a stream of AgentEvents to an IM connector.

    Parameters
    ----------
    connector:
        The MessageConnector (real or fake) to send messages through.
    settings:
        Application settings (gateway_flush_interval_ms, gateway_flush_chars, …).
    sleep:
        Injectable sleep coroutine (default: asyncio.sleep).  Used for retry backoff.
    monotonic:
        Injectable monotonic clock (default: time.monotonic).  Used for flush timing.
    jitter:
        Injectable jitter source (default: random.uniform).  Used for retry backoff.
    """

    # Retry parameters
    _RETRY_BASE = 0.5   # seconds
    _RETRY_CAP = 8.0    # seconds max backoff
    _MAX_RETRIES = 3

    def __init__(
        self,
        connector,
        settings,
        *,
        sleep: Callable = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._connector = connector
        self._settings = settings
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_with_retry(self, coro_factory: Callable) -> object:
        """Call ``coro_factory()`` up to _MAX_RETRIES times with jittered backoff."""
        for attempt in range(self._MAX_RETRIES + 1):
            try:
                return await coro_factory()
            except Exception:
                if attempt >= self._MAX_RETRIES:
                    raise
                base_delay = min(self._RETRY_BASE * (2 ** attempt), self._RETRY_CAP)
                jitter_amount = self._jitter(0, base_delay * 0.1)
                delay = base_delay + jitter_amount
                await self._sleep(delay)
        # Should not reach here
        raise RuntimeError("unreachable")

    def _flush_interval_s(self) -> float:
        return self._settings.gateway_flush_interval_ms / 1000.0

    def _flush_chars(self) -> int:
        return self._settings.gateway_flush_chars

    def _max_chars(self) -> int:
        return self._connector.capabilities.max_message_chars

    def _supports_edit(self) -> bool:
        return self._connector.capabilities.supports_edit_in_place_message

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into chunks that fit within max_message_chars."""
        max_c = self._max_chars()
        if max_c <= 0:
            return [text]
        if len(text) <= max_c:
            return [text]
        chunks = []
        while text:
            chunks.append(text[:max_c])
            text = text[max_c:]
        return chunks

    async def _send_or_update(
        self,
        chat: ChatRef,
        text: str,
        last_ref: MessageRef | None,
        idempotency_key: str,
    ) -> MessageRef:
        """Send or update a message, respecting edit-in-place capability.

        If last_ref is set AND the connector supports edit-in-place, update.
        Otherwise send fresh.
        Returns the (possibly new) MessageRef.
        """
        if last_ref is not None and self._supports_edit():
            await self._send_with_retry(
                lambda: self._connector.update_message(last_ref, OutboundMessage(text=text))
            )
            return last_ref
        else:
            ref = await self._send_with_retry(
                lambda: self._connector.send_message(
                    chat, OutboundMessage(text=text), idempotency_key=idempotency_key
                )
            )
            return ref

    async def _flush_buffer(
        self,
        buffer: list[str],
        chat: ChatRef,
        last_ref: MessageRef | None,
        idempotency_key: str,
    ) -> tuple[list[str], MessageRef | None, str]:
        """Flush buffered tokens to the connector.

        For no-edit connectors this is a no-op (returns unchanged state).
        For edit connectors this does send/update.

        Chunking behaviour: if total text > max_message_chars, the text is
        split into chunks.  The first chunk is sent as send/update (respecting
        last_ref).  Each subsequent chunk is ALWAYS a fresh send_message (chunks
        are independent messages, not edits of a prior chunk).

        Returns (new_buffer, new_ref, next_idempotency_key).
        """
        if not buffer:
            return buffer, last_ref, idempotency_key

        text = "".join(buffer)

        if not self._supports_edit():
            # No-edit connector: hold all tokens until done
            return buffer, last_ref, idempotency_key

        # Chunk if needed
        chunks = self._chunk_text(text)
        current_ref = last_ref
        for i, chunk in enumerate(chunks):
            chunk_key = idempotency_key if i == 0 else f"{idempotency_key}:c{i}"
            if i == 0:
                # First chunk: send or update depending on whether we have a ref
                current_ref = await self._send_or_update(chat, chunk, current_ref, chunk_key)
            else:
                # Subsequent chunks: always fresh send_message
                current_ref = await self._send_with_retry(
                    lambda _c=chunk, _k=chunk_key: self._connector.send_message(
                        chat, OutboundMessage(text=_c), idempotency_key=_k
                    )
                )

        # Generate a fresh idempotency key for the next flush
        next_key = str(uuid.uuid4())
        return [], current_ref, next_key

    # ------------------------------------------------------------------
    # Main rendering entrypoint (11a text)
    # ------------------------------------------------------------------

    async def render_turn(
        self,
        session,
        binding,
        chat: ChatRef,
        agent_events: AsyncIterable[AgentEvent],
    ) -> None:
        """Render a turn's AgentEvent stream to the chat.

        Processes token, done, action_required, and error events.
        """
        buffer: list[str] = []
        last_ref: MessageRef | None = None
        last_flush_time = self._monotonic()
        idempotency_key = str(uuid.uuid4())
        _cards_sent: set = set()  # message_ids already processed for cards (11b)

        async for event in agent_events:
            # ----------------------------------------------------------
            # 11d: Revocation check before each event
            # ----------------------------------------------------------
            current = active_binding(
                session,
                connector=binding.provider,
                external_account_id=binding.external_account_id,
                workspace_id=binding.workspace_id,
            )
            if current is None or current.id != binding.id:
                record_audit(
                    session,
                    event_type="gateway.revoked_midflight",
                    actor=binding.desk_user,
                    subject_type="gateway_binding",
                    subject_id=binding.id,
                    payload={"provider": binding.provider, "reason": "binding revoked during stream"},
                )
                if last_ref is not None:
                    await self._connector.update_message(
                        last_ref, OutboundMessage(text="⚠ session ended")
                    )
                return

            if event.type == "token":
                content = ""
                if isinstance(event.data, dict):
                    content = event.data.get("content", "")
                elif isinstance(event.data, str):
                    content = event.data
                buffer.append(content)

                # Check flush conditions (time or char threshold)
                now = self._monotonic()
                elapsed = now - last_flush_time
                should_flush = (
                    elapsed >= self._flush_interval_s()
                    or len("".join(buffer)) >= self._flush_chars()
                )

                if should_flush and self._supports_edit():
                    buffer, last_ref, idempotency_key = await self._flush_buffer(
                        buffer, chat, last_ref, idempotency_key
                    )
                    last_flush_time = self._monotonic()

            elif event.type == "action_required":
                # Flush any pending tokens first
                if buffer and self._supports_edit():
                    buffer, last_ref, idempotency_key = await self._flush_buffer(
                        buffer, chat, last_ref, idempotency_key
                    )
                    last_flush_time = self._monotonic()

                # 11b: send approval card
                await self._handle_action_required(
                    session, binding, chat, event, _cards_sent
                )

            elif event.type == "done":
                # Final flush
                if buffer:
                    text = "".join(buffer)
                    if not self._supports_edit():
                        # No-edit: send the whole thing now
                        chunks = self._chunk_text(text)
                        for i, chunk in enumerate(chunks):
                            chunk_key = idempotency_key if i == 0 else f"{idempotency_key}:c{i}"
                            await self._send_with_retry(
                                lambda _c=chunk, _k=chunk_key: self._connector.send_message(
                                    chat, OutboundMessage(text=_c), idempotency_key=_k
                                )
                            )
                    else:
                        # Edit-capable: update or send; multi-chunk = separate messages
                        chunks = self._chunk_text(text)
                        current_ref = last_ref
                        for i, chunk in enumerate(chunks):
                            chunk_key = idempotency_key if i == 0 else f"{idempotency_key}:c{i}"
                            if i == 0:
                                current_ref = await self._send_or_update(
                                    chat, chunk, current_ref, chunk_key
                                )
                            else:
                                # Subsequent chunks: always fresh send_message
                                current_ref = await self._send_with_retry(
                                    lambda _c=chunk, _k=chunk_key: self._connector.send_message(
                                        chat, OutboundMessage(text=_c), idempotency_key=_k
                                    )
                                )
                        last_ref = current_ref
                    buffer = []

                # 11b: handle any pending actions in the done event
                await self._handle_done_cards(session, binding, chat, event, _cards_sent)

            elif event.type == "error":
                # 11b: send an error notice
                await self._handle_error(session, binding, chat, event)

    # ------------------------------------------------------------------
    # 11b: Card sending helpers
    # ------------------------------------------------------------------

    async def _send_approval_card(
        self,
        session,
        binding,
        chat: ChatRef,
        thread_id: int,
        message_id: int,
        pending_action: AgentActionProposal,
    ) -> MessageRef:
        """Send a placeholder card then update it with the actionable approval card."""
        placeholder = OutboundCard(
            title=f"Action: {pending_action.tool_name}",
            body="Processing...",
            sections=[],
            actions=[],
            resolved=False,
            footer=None,
        )
        idempotency_key = f"{message_id}:{pending_action.id}"
        out_ref: MessageRef = await self._send_with_retry(
            lambda: self._connector.send_card(
                chat, placeholder, idempotency_key=idempotency_key
            )
        )

        # Build the full actionable card with minted tokens
        card = build_approval_card(
            session,
            binding=binding,
            thread_id=thread_id,
            message_id=message_id,
            pending_action=pending_action,
            out_ref=out_ref,
            settings=self._settings,
        )
        await self._send_with_retry(
            lambda: self._connector.update_card(out_ref, card)
        )
        return out_ref

    async def _handle_action_required(
        self,
        session,
        binding,
        chat: ChatRef,
        event: AgentEvent,
        _cards_sent: set,
    ) -> None:
        """Process an action_required event by sending an approval card."""
        if not isinstance(event.data, dict):
            return
        message_id = event.data.get("message_id")
        pending_actions = event.data.get("pending_actions", [])
        thread_id = event.data.get("thread_id")
        if message_id is None or thread_id is None:
            return
        if message_id in _cards_sent:
            return
        _cards_sent.add(message_id)
        for action_dict in pending_actions:
            try:
                pending_action = AgentActionProposal.model_validate(action_dict)
            except Exception:
                continue
            await self._send_approval_card(
                session, binding, chat, thread_id, message_id, pending_action
            )

    async def _handle_done_cards(
        self,
        session,
        binding,
        chat: ChatRef,
        event: AgentEvent,
        _cards_sent: set,
    ) -> None:
        """On done, check if there are pending actions to send cards for."""
        if not isinstance(event.data, dict):
            return
        message_id = event.data.get("message_id")
        pending_actions = event.data.get("pending_actions", [])
        thread_id = event.data.get("thread_id")
        if message_id is None or thread_id is None:
            return
        if message_id in _cards_sent:
            return
        if not pending_actions:
            return
        _cards_sent.add(message_id)
        for action_dict in pending_actions:
            try:
                pending_action = AgentActionProposal.model_validate(action_dict)
            except Exception:
                continue
            await self._send_approval_card(
                session, binding, chat, thread_id, message_id, pending_action
            )

    async def _handle_error(self, session, binding, chat: ChatRef, event: AgentEvent) -> None:
        """Send a text notice about an error event."""
        from app.services.gateway.config import GatewayConfig
        config = GatewayConfig.from_settings(self._settings)

        thread_id = None
        if isinstance(event.data, dict):
            thread_id = event.data.get("thread_id")

        web_link = config.web_thread_link(str(thread_id)) if thread_id else None
        notice = "An error occurred during processing."
        if web_link:
            notice += f"\nView details on web desk: {web_link}"

        idempotency_key = str(uuid.uuid4())
        await self._send_with_retry(
            lambda: self._connector.send_message(
                chat, OutboundMessage(text=notice), idempotency_key=idempotency_key
            )
        )

    # ------------------------------------------------------------------
    # 11c: Resume result rendering
    # ------------------------------------------------------------------

    async def render_resume_result(
        self,
        session,
        binding,
        claimed_action,
        clicked_card_ref: MessageRef,
        outcome,
    ) -> None:
        """Render the result of a resumed agent action.

        Parameters
        ----------
        claimed_action:
            The GatewayCardAction row (already in status='resolving').
        clicked_card_ref:
            The MessageRef of the card the user clicked.
        outcome:
            ResumeOk or ResumeRaised.
        """
        chat = ChatRef(
            connector=clicked_card_ref.connector,
            workspace_id=clicked_card_ref.workspace_id,
            chat_id=clicked_card_ref.chat_id,
            chat_type="dm",  # best guess; type doesn't affect sends
        )

        if isinstance(outcome, ResumeOk):
            mark_resolved(session, claimed_action, resolved_by_binding_id=binding.id)
            resolved_card = OutboundCard(
                title="Action resolved",
                body="Resolved",
                sections=[],
                actions=[],
                resolved=True,
                footer=None,
            )
            await self._connector.update_card(clicked_card_ref, resolved_card)

            # Post the follow-up content
            agent_message = outcome.agent_message
            if agent_message is not None and agent_message.content:
                idempotency_key = str(uuid.uuid4())
                await self._send_with_retry(
                    lambda: self._connector.send_message(
                        chat,
                        OutboundMessage(text=agent_message.content),
                        idempotency_key=idempotency_key,
                    )
                )

            # Check for chained pending actions
            pending_actions = []
            if agent_message is not None and isinstance(agent_message.meta, dict):
                pending_actions = agent_message.meta.get("pending_actions", [])

            thread_id = claimed_action.thread_id
            message_id = agent_message.id if agent_message is not None else None

            for action_dict in pending_actions:
                try:
                    pending_action = AgentActionProposal.model_validate(action_dict)
                except Exception:
                    continue
                if message_id is not None and thread_id is not None:
                    await self._send_approval_card(
                        session, binding, chat, thread_id, message_id, pending_action
                    )

        elif isinstance(outcome, ResumeRaised):
            mark_unknown(session, claimed_action)
            config = GatewayConfig.from_settings(self._settings)
            link = config.web_thread_link(str(claimed_action.thread_id))
            link_text = f"\nVerify on web desk: {link}" if link else ""
            unknown_card = OutboundCard(
                title="Outcome unknown",
                body=f"Outcome unknown — verify in web desk{link_text}",
                sections=[],
                actions=[],
                resolved=False,
                footer=None,
            )
            await self._connector.update_card(clicked_card_ref, unknown_card)

    async def render_claim_error(
        self,
        session,
        source_message_ref: MessageRef,
        error: ClaimError,
    ) -> None:
        """Render a ClaimError by updating the source card with an error state."""
        if error == ClaimError.already_resolved:
            body = "This action has already been handled."
            title = "Already handled"
        elif error == ClaimError.expired:
            body = "This approval request has expired. Please action it from the web desk."
            title = "Request expired"
        else:
            # bad_token or source_mismatch
            body = "Invalid action request. Please use the web desk."
            title = "Invalid request"

        error_card = OutboundCard(
            title=title,
            body=body,
            sections=[],
            actions=[],
            resolved=False,
            footer=None,
        )
        await self._connector.update_card(source_message_ref, error_card)
