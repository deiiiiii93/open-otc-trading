"""Run-scoped capture of booking-tool results, for the chat booking card.

Why a middleware and not message scanning
-----------------------------------------
The obvious implementation — scan the agent result's ``messages`` for the
booking ToolMessage, like ``_term_form_from_result`` does — **cannot work
here**. The orchestrator delegates to a persona via the deepagents ``task()``
tool, and that persona runs in its OWN LangGraph checkpoint namespace, so its
tool messages never enter the orchestrator's top-level ``messages``. Measured
on a live gated booking (thread 684): ``book_extracted_trade`` appeared in 28
subagent checkpoints and **0** orchestrator checkpoints.

``propose_term_form`` escapes this only because the orchestrator calls it
itself. Anything a *persona* calls is invisible to result scanning.

The ``wrap_tool_call`` seam is the one place that sees a subagent's tool calls
— which is exactly why ``AuditTrailMiddleware`` lives there and is registered
in all three stacks. This module reuses that seam.

Why an explicit store and not a ContextVar
------------------------------------------
LangGraph may execute nodes on worker threads. A ContextVar set inside the
tool call is copied *into* the worker, so a write there would not propagate
back to the caller that persists the message. A keyed store does.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any, TypeAlias

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from ..audit_trail import AUDIT_CONTEXT_KEY as _AUDIT_CONTEXT_KEY

logger = logging.getLogger(__name__)

_ToolResult: TypeAlias = ToolMessage | Command

#: Tools whose result carries a renderable booking record (service key
#: "booking"). Keyed by tool NAME rather than sniffing every result for a
#: "booking" key: a model chooses arguments, never which tool is which, so a
#: name-keyed capture cannot be spoofed by an unrelated tool.
BOOKING_RESULT_TOOLS: frozenset[str] = frozenset({"book_extracted_trade"})

_LOCK = threading.Lock()
_BOOKINGS: dict[str, dict] = {}
#: Bounded so a persist path that never collects (an abandoned run) cannot
#: grow this without limit. Oldest entries are dropped first.
_MAX_PENDING = 64


def _decode_leading_json(text: str) -> dict | None:
    """Decode the FIRST JSON object in ``text``, ignoring anything after it.

    A booking tool's ToolMessage content is not plain JSON by the time this
    middleware sees it: ``GroundTruthArtifactMiddleware`` sits inside this one
    and appends an ``<artifact_ref>{...}</artifact_ref>`` block to the body, so
    the content is ``{...}\\n\\n<artifact_ref>…``. A plain ``json.loads`` raises
    "Extra data" and the whole payload is silently dropped — the live failure
    (``payload=False`` on a ``ToolMessage`` whose ``content`` was a ``str``
    that plainly contained the booking). ``raw_decode`` stops at the end of the
    first value, so the appended evidence reference is ignored rather than
    breaking the parse.
    """
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return None
    try:
        value, _end = json.JSONDecoder().raw_decode(stripped)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _iter_candidate_bodies(output: Any):
    """Yield every plausible carrier of the tool's JSON body.

    The `wrap_tool_call` seam does not hand back one stable shape:

    - a ``ToolMessage`` whose ``content`` is a plain JSON string;
    - a ``ToolMessage`` whose ``content`` is a LIST of content blocks
      (``[{"type": "text", "text": "{...}"}]``) — LangChain's structured
      content form, which a plain ``isinstance(content, str)`` check silently
      drops. This is what actually broke the live capture: the middleware fired
      with the right key and still parsed nothing (`payload=False`);
    - a ``Command`` (the HITL-resume shape), whose ToolMessages live under
      ``.update["messages"]``;
    - the raw dict, when a stack returns the tool value unwrapped.
    """
    if output is None:
        return
    update = getattr(output, "update", None)
    if isinstance(update, dict):
        for message in update.get("messages") or []:
            yield getattr(message, "content", message)
    content = getattr(output, "content", output)
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict):
                yield block.get("text") or block.get("content") or block
            else:
                yield block
        return
    yield content


def booking_payload_from_tool_output(output: Any) -> dict | None:
    """Decode a booking tool's result body into its ``booking`` summary."""
    for body in _iter_candidate_bodies(output):
        if isinstance(body, (bytes, bytearray)):
            body = body.decode("utf-8", "replace")
        if isinstance(body, str):
            body = _decode_leading_json(body)
            if body is None:
                continue
        if not isinstance(body, dict):
            continue
        booking = body.get("booking")
        if isinstance(booking, dict) and booking.get("status"):
            return booking
    return None


def current_run_keys() -> list[str]:
    """Candidate keys identifying the turn that is running.

    Two are recorded, because the persist path looks the booking up by
    **AgentThread id** while ``configurable["thread_id"]`` is the *checkpointer*
    key — documented in ``graph_run_config`` as "sometimes a composite string,
    NOT necessarily an AgentThread id", and in a persona subagent it is not the
    plain thread id at all. Relying on it alone silently missed every gated
    booking (live: position 28 booked, card blank).

    ``AUDIT_CONTEXT_KEY['thread_id']`` IS the AgentThread id and is stamped at
    every entry point (stream, resume, async runner) for the audit trail, which
    reads it from inside persona subagents already — so it survives the same
    nesting. Both are recorded; whichever the reader asks for hits.
    """
    try:
        from langgraph.config import get_config

        configurable = get_config().get("configurable") or {}
    except Exception:  # pragma: no cover — outside a runnable context
        return []
    keys: list[str] = []
    audit_ctx = configurable.get(_AUDIT_CONTEXT_KEY)
    if isinstance(audit_ctx, dict):
        thread_id = audit_ctx.get("thread_id")
        if thread_id not in (None, ""):
            keys.append(str(thread_id))
    checkpointer_key = configurable.get("thread_id")
    if checkpointer_key not in (None, "") and str(checkpointer_key) not in keys:
        keys.append(str(checkpointer_key))
    return keys


def record_booking(run_keys: str | list[str] | None, booking: dict) -> None:
    """Stash the last booking under every key that identifies this turn."""
    if not run_keys or not isinstance(booking, dict):
        return
    keys = [run_keys] if isinstance(run_keys, str) else list(run_keys)
    with _LOCK:
        for key in keys:
            if key:
                _BOOKINGS[key] = booking
        while len(_BOOKINGS) > _MAX_PENDING:
            _BOOKINGS.pop(next(iter(_BOOKINGS)))


def take_booking(run_key: str | None) -> dict | None:
    """Pop the booking recorded for ``run_key`` (None if there was none).

    Popping means a later turn on the same thread cannot re-render a stale
    card for a booking that already appeared.
    """
    if not run_key:
        return None
    with _LOCK:
        return _BOOKINGS.pop(run_key, None)


def clear_bookings() -> None:
    """Test seam."""
    with _LOCK:
        _BOOKINGS.clear()


class BookingResultMiddleware(AgentMiddleware):
    """Record a booking tool's structured result for the persist path.

    Deliberately passthrough-on-everything-else and never raises: this is a UI
    affordance, and failing a booking because its card could not be captured
    would be strictly worse than showing no card.
    """

    def _capture(self, request: ToolCallRequest, result: _ToolResult) -> None:
        name = (request.tool_call or {}).get("name", "")
        if name not in BOOKING_RESULT_TOOLS:
            return
        if getattr(result, "status", None) == "error":
            return
        try:
            booking = booking_payload_from_tool_output(result)
            if booking is not None:
                keys = current_run_keys()
                record_booking(keys, booking)
                logger.debug("captured booking card under keys %s", keys)
        except Exception:  # pragma: no cover — never break a real write
            logger.exception("booking card capture failed for %s", name)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], _ToolResult],
    ) -> _ToolResult:
        result = handler(request)
        self._capture(request, result)
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[_ToolResult]],
    ) -> _ToolResult:
        result = await handler(request)
        self._capture(request, result)
        return result
