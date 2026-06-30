"""Gateway Coalescer — stream renderer for IM message gateway (Tasks 11a–11d).

Renders a stream of AgentEvents to an IM connector, handling:
  11a: token buffering, chunking, per-(connector, chat) token-bucket rate limit,
       jittered retry backoff on transient failure
  11b: approval cards for pending actions
  11c: resume result rendering (resolved, raised, claim errors)
  11d: mid-flight binding revocation detection — re-checked before EVERY send

Send pipeline
-------------
Every outbound connector call in ``render_turn`` is routed through
``_guarded_send`` which, in order:
  1. re-checks ``active_binding`` (11d) — if the binding is no longer active it
     aborts the send, audits ``gateway.revoked_midflight`` once, edits the prior
     message to a "session ended" notice (best-effort), and signals the caller to
     stop the stream;
  2. acquires one token from the per-(connector_name, chat_id) bucket (11a) —
     burst 5, refill 5/s — sleeping (via the injected sleep) when throttled;
  3. performs the send with jittered exponential-backoff retry (≤3 retries).

Finalization sends (``render_resume_result`` / ``render_claim_error``) do not
re-check revocation (the user has already clicked) but are still routed through
the rate-limit + retry wrapper so transient connector errors retry.
"""
from __future__ import annotations

import asyncio
import random
import time
import uuid
from dataclasses import dataclass
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
    CardAction,
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
# Internal sentinel raised by _guarded_send when the binding is revoked.
# ---------------------------------------------------------------------------


class _Revoked(Exception):
    """Internal control-flow signal: binding revoked mid-flight; stop the stream."""
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
        Injectable sleep coroutine (default: asyncio.sleep).  Used for both retry
        backoff AND token-bucket throttling, so tests are deterministic.
    monotonic:
        Injectable monotonic clock (default: time.monotonic).  Used for flush
        timing AND token-bucket refill accounting.
    jitter:
        Injectable jitter source (default: random.uniform).  Used for retry backoff.
    """

    # Retry parameters
    _RETRY_BASE = 0.5   # seconds
    _RETRY_CAP = 8.0    # seconds max backoff
    _MAX_RETRIES = 3

    # Token-bucket parameters (per connector+chat)
    _BUCKET_BURST = 5.0      # maximum tokens
    _BUCKET_REFILL = 5.0     # tokens per second

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
        # Per-(connector_name, chat_id) token bucket state:
        #   key -> (tokens_available: float, last_refill_monotonic: float)
        self._buckets: dict[tuple[str, str], tuple[float, float]] = {}

    # ------------------------------------------------------------------
    # Token bucket (11a) — primary rate limiter
    # ------------------------------------------------------------------

    def _bucket_key(self, chat: ChatRef) -> tuple[str, str]:
        return (getattr(self._connector, "name", "connector"), chat.chat_id)

    async def _acquire_token(self, chat: ChatRef) -> None:
        """Acquire one token from the per-(connector, chat) bucket.

        Refills lazily based on elapsed monotonic time (rate ``_BUCKET_REFILL``,
        capped at ``_BUCKET_BURST``).  If no whole token is available, sleeps via
        the injected sleep for exactly the time needed to refill one token, then
        consumes it.
        """
        key = self._bucket_key(chat)
        now = self._monotonic()
        tokens, last = self._buckets.get(key, (self._BUCKET_BURST, now))

        # Refill based on elapsed time.
        elapsed = max(0.0, now - last)
        tokens = min(self._BUCKET_BURST, tokens + elapsed * self._BUCKET_REFILL)

        if tokens < 1.0:
            # Sleep until one whole token is available.
            deficit = 1.0 - tokens
            wait = deficit / self._BUCKET_REFILL
            await self._sleep(wait)
            # After waiting we have (at least) one token.
            tokens = tokens + wait * self._BUCKET_REFILL
            now = now + wait

        tokens -= 1.0
        self._buckets[key] = (tokens, now)

    # ------------------------------------------------------------------
    # Retry wrapper (11a / I2)
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

    async def _rate_limited_send(self, chat: ChatRef, coro_factory: Callable) -> object:
        """Acquire a rate-limit token then send with retry.

        Used by finalization paths (resume / claim error) that do NOT re-check
        revocation but still need throttling + retry.
        """
        await self._acquire_token(chat)
        return await self._send_with_retry(coro_factory)

    # ------------------------------------------------------------------
    # Guarded send (11d + 11a) — the single chokepoint for render_turn sends
    # ------------------------------------------------------------------

    async def _guarded_send(
        self,
        session,
        binding,
        chat: ChatRef,
        coro_factory: Callable,
        *,
        last_ref: MessageRef | None,
        revoked_state: dict,
    ) -> object:
        """Re-check revocation, acquire a rate-limit token, then send with retry.

        This is the SINGLE path through which every ``render_turn`` outbound send
        flows, so the revocation re-check (11d) and the token-bucket throttle
        (11a) apply before EVERY individual send.

        On revocation it audits ``gateway.revoked_midflight`` exactly once (guarded
        by ``revoked_state``), best-effort edits ``last_ref`` to a session-ended
        notice, and raises ``_Revoked`` so the caller aborts the stream.
        """
        current = active_binding(
            session,
            connector=binding.provider,
            external_account_id=binding.external_account_id,
            workspace_id=binding.workspace_id,
        )
        if current is None or current.id != binding.id:
            if not revoked_state.get("audited"):
                revoked_state["audited"] = True
                record_audit(
                    session,
                    event_type="gateway.revoked_midflight",
                    actor=binding.desk_user,
                    subject_type="gateway_binding",
                    subject_id=binding.id,
                    payload={
                        "provider": binding.provider,
                        "reason": "binding revoked during stream",
                    },
                )
                # Best-effort: edit the prior message to a session-ended notice.
                if last_ref is not None:
                    try:
                        await self._connector.update_message(
                            last_ref, OutboundMessage(text="⚠ session ended")
                        )
                    except Exception:
                        pass
            raise _Revoked()

        await self._acquire_token(chat)
        return await self._send_with_retry(coro_factory)

    # ------------------------------------------------------------------
    # Text helpers (11a)
    # ------------------------------------------------------------------

    def _flush_interval_s(self) -> float:
        return self._settings.gateway_flush_interval_ms / 1000.0

    def _flush_chars(self) -> int:
        return self._settings.gateway_flush_chars

    def _max_chars(self) -> int:
        return self._connector.capabilities.max_message_chars

    def _supports_edit(self) -> bool:
        return self._connector.capabilities.supports_edit_in_place_message

    def _chunk_text(self, text: str) -> list[str]:
        """Split text into chunks that fit within max_message_chars (code points)."""
        max_c = self._max_chars()
        if max_c <= 0 or len(text) <= max_c:
            return [text]
        chunks = []
        while text:
            chunks.append(text[:max_c])
            text = text[max_c:]
        return chunks

    async def _emit_text(
        self,
        session,
        binding,
        chat: ChatRef,
        text: str,
        last_ref: MessageRef | None,
        idempotency_key: str,
        revoked_state: dict,
    ) -> MessageRef | None:
        """Emit ``text`` (chunked) to the chat through guarded sends.

        First chunk: update_message if we already have a ref + edit support, else
        send_message.  Subsequent chunks: always a fresh send_message.  Every send
        flows through ``_guarded_send`` (revocation re-check + rate limit + retry).

        Returns the new "current" MessageRef (the last message touched).
        """
        chunks = self._chunk_text(text)
        current_ref = last_ref
        for i, chunk in enumerate(chunks):
            chunk_key = idempotency_key if i == 0 else f"{idempotency_key}:c{i}"
            if i == 0 and current_ref is not None and self._supports_edit():
                ref_for_send = current_ref
                await self._guarded_send(
                    session,
                    binding,
                    chat,
                    lambda _r=ref_for_send, _t=chunk: self._connector.update_message(
                        _r, OutboundMessage(text=_t)
                    ),
                    last_ref=current_ref,
                    revoked_state=revoked_state,
                )
                # ref unchanged for an in-place edit
            else:
                current_ref = await self._guarded_send(
                    session,
                    binding,
                    chat,
                    lambda _c=chunk, _k=chunk_key: self._connector.send_message(
                        chat, OutboundMessage(text=_c), idempotency_key=_k
                    ),
                    last_ref=current_ref,
                    revoked_state=revoked_state,
                )
        return current_ref

    # ------------------------------------------------------------------
    # Main rendering entrypoint
    # ------------------------------------------------------------------

    async def render_turn(
        self,
        session,
        binding,
        chat: ChatRef,
        agent_events: AsyncIterable[AgentEvent],
    ) -> None:
        """Render a turn's AgentEvent stream to the chat.

        Processes token, done, action_required, and error events.  Each individual
        outbound send re-checks revocation (11d) and is rate-limited (11a) via
        ``_guarded_send``.  On revocation the stream aborts immediately.
        """
        buffer: list[str] = []
        last_ref: MessageRef | None = None
        last_flush_time = self._monotonic()
        idempotency_key = str(uuid.uuid4())
        cards_sent: set = set()  # message_ids already processed for cards (11b)
        revoked_state: dict = {"audited": False}

        try:
            async for event in agent_events:
                if event.type == "token":
                    content = ""
                    if isinstance(event.data, dict):
                        # The live agent SSE emits token text under "text"
                        # (agents.py _sse("token", {"text": ...})); accept
                        # "content" as a fallback for synthetic event sources.
                        content = (
                            event.data.get("text")
                            or event.data.get("content")
                            or ""
                        )
                    elif isinstance(event.data, str):
                        content = event.data
                    buffer.append(content)

                    now = self._monotonic()
                    elapsed = now - last_flush_time
                    # Cumulative streaming: edit the SAME message in place with the
                    # FULL text so far (the buffer is never reset within a turn), so
                    # the displayed reply grows instead of being overwritten by each
                    # delta. Throttle by the flush interval to avoid an edit per token.
                    if (
                        elapsed >= self._flush_interval_s()
                        and self._supports_edit()
                        and "".join(buffer).strip()
                    ):
                        last_ref = await self._emit_text(
                            session, binding, chat, "".join(buffer), last_ref,
                            idempotency_key, revoked_state,
                        )
                        last_flush_time = self._monotonic()

                elif event.type == "action_required":
                    if buffer and self._supports_edit():
                        text = "".join(buffer)
                        last_ref = await self._emit_text(
                            session, binding, chat, text, last_ref,
                            idempotency_key, revoked_state,
                        )
                        buffer = []
                        idempotency_key = str(uuid.uuid4())
                        last_flush_time = self._monotonic()
                    await self._handle_card_event(
                        session, binding, chat, event, cards_sent,
                        last_ref, revoked_state,
                    )

                elif event.type == "done":
                    # Final flush: render the COMPLETE accumulated reply in place so
                    # the full answer remains visible after streaming ends.
                    text = "".join(buffer)
                    if text.strip():
                        last_ref = await self._emit_text(
                            session, binding, chat, text, last_ref,
                            idempotency_key, revoked_state,
                        )
                    await self._handle_card_event(
                        session, binding, chat, event, cards_sent,
                        last_ref, revoked_state, require_actions=True,
                    )
                    await self._handle_reply_options_event(
                        session, binding, chat, event, last_ref, revoked_state,
                    )

                elif event.type == "error":
                    await self._handle_error(
                        session, binding, chat, event, last_ref, revoked_state,
                    )
        except _Revoked:
            # Revocation detected and handled inside _guarded_send; stop cleanly.
            return

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
        *,
        last_ref: MessageRef | None = None,
        revoked_state: dict | None = None,
    ) -> MessageRef:
        """Two-phase send: buttonless placeholder → mint with out_ref → actionable card.

        When ``revoked_state`` is provided (the ``render_turn`` path) both the
        placeholder ``send_card`` and the ``update_card`` flow through
        ``_guarded_send`` (revocation re-check + rate limit + retry).  When it is
        None (the chained-action / resume path) they flow through the rate-limit +
        retry wrapper only.
        """
        placeholder = OutboundCard(
            title=f"Action: {pending_action.tool_name}",
            body="Processing...",
            sections=[],
            actions=[],
            resolved=False,
            footer=None,
        )
        idempotency_key = f"{message_id}:{pending_action.id}"

        send_placeholder = lambda: self._connector.send_card(
            chat, placeholder, idempotency_key=idempotency_key
        )
        if revoked_state is not None:
            out_ref: MessageRef = await self._guarded_send(
                session, binding, chat, send_placeholder,
                last_ref=last_ref, revoked_state=revoked_state,
            )
        else:
            out_ref = await self._rate_limited_send(chat, send_placeholder)

        # Build the actionable card with tokens stamped with the placeholder's ref.
        card = build_approval_card(
            session,
            binding=binding,
            thread_id=thread_id,
            message_id=message_id,
            pending_action=pending_action,
            out_ref=out_ref,
            settings=self._settings,
        )

        update_actionable = lambda: self._connector.update_card(out_ref, card)
        if revoked_state is not None:
            await self._guarded_send(
                session, binding, chat, update_actionable,
                last_ref=last_ref, revoked_state=revoked_state,
            )
        else:
            await self._rate_limited_send(chat, update_actionable)
        return out_ref

    async def _handle_card_event(
        self,
        session,
        binding,
        chat: ChatRef,
        event: AgentEvent,
        cards_sent: set,
        last_ref: MessageRef | None,
        revoked_state: dict,
        *,
        require_actions: bool = False,
    ) -> None:
        """Render approval cards for an action_required / done event (idempotent)."""
        if not isinstance(event.data, dict):
            return
        message_id = event.data.get("message_id")
        thread_id = event.data.get("thread_id")
        pending_actions = event.data.get("pending_actions", [])
        if message_id is None or thread_id is None:
            return
        if message_id in cards_sent:
            return
        if require_actions and not pending_actions:
            return
        cards_sent.add(message_id)
        for action_dict in pending_actions:
            try:
                pending_action = AgentActionProposal.model_validate(action_dict)
            except Exception:
                continue
            await self._send_approval_card(
                session, binding, chat, thread_id, message_id, pending_action,
                last_ref=last_ref, revoked_state=revoked_state,
            )

    async def _handle_reply_options_event(
        self,
        session,
        binding,
        chat: ChatRef,
        event: AgentEvent,
        last_ref: MessageRef | None,
        revoked_state: dict,
    ) -> None:
        """Render proposed reply options as a pickable card.

        Each option becomes a button whose callback carries the option's
        ``value`` (the message replayed on click) rather than a token — see
        ``feishu_card_action_to_inbound``. Descriptions are shown as card
        sections so the user has context before choosing.
        """
        if not isinstance(event.data, dict):
            return
        options = event.data.get("reply_options") or []
        if not options:
            return

        sections: list[CardSection] = []
        actions: list[CardAction] = []
        for opt in options:
            if not isinstance(opt, dict):
                continue
            label = opt.get("label")
            if not label:
                continue
            value = opt.get("value") or label
            description = opt.get("description")
            if description:
                sections.append(CardSection(title=label, body=description))
            actions.append(CardAction(label=label, style="default", reply=value))
        if not actions:
            return

        card = OutboundCard(
            title="Choose a reply",
            body="",
            sections=sections,
            actions=actions,
            resolved=False,
            footer=None,
        )
        message_id = event.data.get("message_id")
        idempotency_key = f"{message_id}:reply-options"
        send = lambda: self._connector.send_card(
            chat, card, idempotency_key=idempotency_key
        )
        await self._guarded_send(
            session, binding, chat, send,
            last_ref=last_ref, revoked_state=revoked_state,
        )

    async def _handle_error(
        self,
        session,
        binding,
        chat: ChatRef,
        event: AgentEvent,
        last_ref: MessageRef | None,
        revoked_state: dict,
    ) -> None:
        """Send a text notice about an error event, including a web deep-link."""
        config = GatewayConfig.from_settings(self._settings)
        thread_id = None
        if isinstance(event.data, dict):
            thread_id = event.data.get("thread_id")

        web_link = config.web_thread_link(str(thread_id)) if thread_id else None
        notice = "An error occurred during processing."
        if web_link:
            notice += f"\nView details on web desk: {web_link}"

        idempotency_key = str(uuid.uuid4())
        await self._guarded_send(
            session,
            binding,
            chat,
            lambda: self._connector.send_message(
                chat, OutboundMessage(text=notice), idempotency_key=idempotency_key
            ),
            last_ref=last_ref,
            revoked_state=revoked_state,
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

        Finalization sends are routed through the rate-limit + retry wrapper
        (no revocation re-check: the user has already clicked).
        """
        chat = ChatRef(
            connector=clicked_card_ref.connector,
            workspace_id=clicked_card_ref.workspace_id,
            chat_id=clicked_card_ref.chat_id,
            chat_type="dm",  # type doesn't affect sends
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
            await self._rate_limited_send(
                chat, lambda: self._connector.update_card(clicked_card_ref, resolved_card)
            )

            agent_message = outcome.agent_message
            if agent_message is not None and getattr(agent_message, "content", None):
                idempotency_key = str(uuid.uuid4())
                await self._rate_limited_send(
                    chat,
                    lambda: self._connector.send_message(
                        chat,
                        OutboundMessage(text=agent_message.content),
                        idempotency_key=idempotency_key,
                    ),
                )

            # Chained pending actions reuse the same two-phase helper (11b).
            pending_actions = []
            if agent_message is not None and isinstance(
                getattr(agent_message, "meta", None), dict
            ):
                pending_actions = agent_message.meta.get("pending_actions", [])

            thread_id = claimed_action.thread_id
            message_id = getattr(agent_message, "id", None)
            for action_dict in pending_actions:
                try:
                    pending_action = AgentActionProposal.model_validate(action_dict)
                except Exception:
                    continue
                if message_id is not None and thread_id is not None:
                    await self._send_approval_card(
                        session, binding, chat, thread_id, message_id, pending_action,
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
            await self._rate_limited_send(
                chat, lambda: self._connector.update_card(clicked_card_ref, unknown_card)
            )

    async def render_claim_error(
        self,
        session,
        source_message_ref: MessageRef,
        error: ClaimError,
    ) -> None:
        """Render a ClaimError by updating the source card idempotently.

        The update flows through the rate-limit + retry wrapper.
        """
        if error == ClaimError.already_resolved:
            title = "Already handled"
            body = "This action has already been handled."
        elif error == ClaimError.expired:
            title = "Request expired"
            body = "This approval request has expired — ask the agent to re-send."
        else:
            # bad_token or source_mismatch
            title = "Invalid request"
            body = "Couldn't process this click. Please use the web desk."

        error_card = OutboundCard(
            title=title,
            body=body,
            sections=[],
            actions=[],
            resolved=False,
            footer=None,
        )
        chat = ChatRef(
            connector=source_message_ref.connector,
            workspace_id=source_message_ref.workspace_id,
            chat_id=source_message_ref.chat_id,
            chat_type="dm",
        )
        await self._rate_limited_send(
            chat, lambda: self._connector.update_card(source_message_ref, error_card)
        )
