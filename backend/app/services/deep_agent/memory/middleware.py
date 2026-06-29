"""MemoryMiddleware — inject at turn start, prompt seam, correction fast-path."""
from __future__ import annotations

import contextlib
import logging
import re
from collections.abc import Callable, Sequence
from typing import NotRequired, TypedDict

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import SystemMessage
from sqlalchemy import text

from .config import MemoryConfig
from .inject import format_for_injection, inject_memory_block
from .runs import RunSpec, correction_run_key
from .scope import active_read_scopes, book_scope_for_session
from .store import MemoryStore

logger = logging.getLogger(__name__)


def matches_correction(text_in: str, phrases: Sequence[str]) -> bool:
    """Return True iff text_in contains any phrase as a whole word (case-insensitive)."""
    low = (text_in or "").lower()
    return any(re.search(r"\b" + re.escape(p.lower()) + r"\b", low) for p in phrases)


@contextlib.contextmanager
def memory_read_session(session_factory, busy_timeout_ms: int):
    """Context manager yielding a SQLAlchemy session with PRAGMA busy_timeout set.

    Sets PRAGMA busy_timeout = busy_timeout_ms (250 ms by default) so reads
    fail fast rather than blocking the agent turn indefinitely.
    Lives in this module because Task 11 created the write-session equivalent
    (memory_write_session in queue.py) and left the read-session owed to Task 14.
    """
    session = session_factory()
    try:
        session.execute(text(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}"))
        yield session
    finally:
        session.close()


class MemoryState(TypedDict):
    memory_block: NotRequired[str]


class MemoryMiddleware(AgentMiddleware):
    """Inject remembered context before each agent turn and trigger correction extraction.

    Three hooks:
    - ``before_agent``: resolve active read scopes, load injectable facts, render the
      ``<memory>`` block and write it to ``state["memory_block"]``. Fail-open.
    - ``wrap_model_call`` / ``awrap_model_call``: append the rendered block to the
      system prompt just before the LLM call. Fail-open.
    - ``after_model``: on a correction phrase in the latest user message, enqueue a
      high-priority correction extraction job. Deduplicated by ``run_key``.

    ``enabled=False`` → hard no-op in all three hooks (no inject, no enqueue).
    """

    state_schema = MemoryState

    def __init__(
        self,
        *,
        config: MemoryConfig,
        store: MemoryStore,
        queue,
        session_factory,
        book_resolver: Callable = book_scope_for_session,
    ) -> None:
        super().__init__()
        self.config = config
        self.store = store
        self.queue = queue
        self._session_factory = session_factory
        self._book_resolver = book_resolver

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _configurable(self, config) -> dict:
        if isinstance(config, dict):
            return config.get("configurable", {}) or {}
        return {}

    def _resolve_book(self, config):
        """Return a (scope_type, scope_id) tuple for the book scope, or None.

        Reads ``memory_session_id`` from config configurable; resolves via
        book_resolver on a read session. Returns None on absence or any error.
        """
        session_id = self._configurable(config).get("memory_session_id")
        if session_id is None or self._session_factory is None:
            return None
        try:
            with memory_read_session(self._session_factory, self.config.read_timeout_ms) as s:
                pid = self._book_resolver(s, session_id)
            return ("book", str(pid)) if pid is not None else None
        except Exception:  # noqa: BLE001 — fail-open
            logger.warning("memory _resolve_book failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # before_agent — load + inject memory block onto state
    # ------------------------------------------------------------------

    def before_agent(self, state, runtime, config):  # type: ignore[override]
        """Resolve read scopes, load injectable facts, render <memory> block.

        Returns ``{"memory_block": block}`` or ``None``.
        Fail-open: any exception injects nothing.
        """
        if not self.config.enabled:
            return None
        try:
            scopes = active_read_scopes(self._resolve_book(config))
            with memory_read_session(self._session_factory, self.config.read_timeout_ms) as s:
                facts = self.store.load_injectable(s, scopes)
            block = format_for_injection(facts, self.config)
            return {"memory_block": block} if block else None
        except Exception:  # noqa: BLE001 — fail-open: never break the turn
            logger.warning("memory before_agent failed; injecting nothing", exc_info=True)
            return None

    async def abefore_agent(self, state, runtime, config):  # type: ignore[override]
        """Async variant of before_agent (delegates to sync; I/O is SQLite)."""
        return self.before_agent(state, runtime, config)

    # ------------------------------------------------------------------
    # wrap_model_call — append block to system prompt
    # ------------------------------------------------------------------

    def _inject_request(self, request):
        """Return a request with the memory block appended to the system prompt,
        or None if there is nothing to inject / config disabled."""
        if not self.config.enabled:
            return None
        block = (getattr(request, "state", {}) or {}).get("memory_block")
        if not block:
            return None
        base = request.system_message.content if request.system_message is not None else ""
        new_sys = SystemMessage(content=inject_memory_block(base, request.state))
        return request.override(system_message=new_sys)

    def wrap_model_call(self, request, handler):
        """Append <memory> block to system prompt before each LLM call. Fail-open."""
        try:
            injected = self._inject_request(request)
        except Exception:  # noqa: BLE001 — fail-open: never break the turn
            logger.warning("memory wrap_model_call failed; no injection", exc_info=True)
            injected = None
        return handler(injected if injected is not None else request)

    async def awrap_model_call(self, request, handler):
        """Async variant of wrap_model_call. Fail-open."""
        try:
            injected = self._inject_request(request)
        except Exception:  # noqa: BLE001 — fail-open: never break the turn
            logger.warning("memory awrap_model_call failed; no injection", exc_info=True)
            injected = None
        return await handler(injected if injected is not None else request)

    # ------------------------------------------------------------------
    # after_model — correction fast-path
    # ------------------------------------------------------------------

    def after_model(self, state, runtime, config):  # type: ignore[override]
        """Enqueue a high-priority correction job when a correction phrase is detected.

        Uses the DURABLE integer ``memory_message_id`` from config configurable
        (NOT the LangChain string message id) so the run_key is stable across
        restarts. A missing or non-integer id is a no-op (dedupe guard).
        """
        if not self.config.enabled or self.queue is None:
            return None
        try:
            from langchain_core.messages import HumanMessage
            from .queue import QueueJob

            messages = (state or {}).get("messages", [])
            user_msgs = [m for m in messages if isinstance(m, HumanMessage)]
            if not user_msgs:
                return None
            latest = user_msgs[-1]
            content = (latest.content if isinstance(latest.content, str)
                       else str(latest.content))
            if not matches_correction(content, self.config.correction_phrases):
                return None
            cfgable = self._configurable(config)
            session_id = cfgable.get("memory_session_id")
            # Use the DURABLE integer AgentMessage id from configurable, NOT
            # latest.id (a LangChain string like "m42"): the runs table stores
            # trigger_message_id as Integer and the cursor compares ints.
            trigger_message_id = cfgable.get("memory_message_id")
            if not isinstance(session_id, int) or not isinstance(trigger_message_id, int):
                return None
            spec = RunSpec(
                run_key=correction_run_key(session_id, trigger_message_id),
                kind="correction",
                session_id=session_id,
                thread_id=cfgable.get("memory_thread_id"),
                persona=cfgable.get("memory_persona"),
                book_scope_id=None,
                trigger_message_id=trigger_message_id,
            )
            self.queue.enqueue(QueueJob(spec, "high"))
        except Exception:  # noqa: BLE001 — fail-open: never break the turn
            logger.warning("memory after_model correction path failed", exc_info=True)
        return None

    async def aafter_model(self, state, runtime, config):  # type: ignore[override]
        """Async variant of after_model (delegates to sync)."""
        return self.after_model(state, runtime, config)
