# backend/app/services/deep_agent/memory/runtime.py
"""Process-wide memory singletons + session-close enqueue seam."""
from __future__ import annotations

import threading

from .config import MemoryConfig, get_memory_config
from .middleware import MemoryMiddleware
from .queue import MemoryWriteQueue, QueueJob
from .runs import ExtractionRunStore, RunSpec, session_run_key
from .scope import book_scope_for_session
from .store import MemoryStore
from .window import load_extraction_window

# RLock (NOT Lock): the locked getters call each other on the same thread
# (get_memory_queue -> get_memory_store; get_memory_middleware -> both), so a
# non-reentrant lock would self-deadlock. RLock allows same-thread reentry.
_LOCK = threading.RLock()
_STORE: MemoryStore | None = None
_QUEUE: MemoryWriteQueue | None = None
_MIDDLEWARE: MemoryMiddleware | None = None


def resolve_extractor_selection(registry, config: MemoryConfig) -> dict:
    """Resolve the configured extractor tier to a concrete model selection.

    Prefer a healthy model tagged ``config.extractor_model`` (e.g. "fast"), so
    extraction routes to the cheap flash tier rather than the agent default.
    Falls back to ``registry.default_selection()`` when no healthy channel
    declares a model with that tag. Both the build path (_extractor_llm) and the
    provenance path (_extractor_model_id) go through this single resolver, so the
    model we record can never drift from the model we run.
    """
    selection = registry.select_by_tag(config.extractor_model)
    if selection is not None:
        return selection
    return registry.default_selection()


def _extractor_llm(prompt: str) -> str:
    from ..channel_registry import get_registry
    from ..model_factory import build_agent_model

    registry = get_registry()
    selection = resolve_extractor_selection(registry, get_memory_config())
    model = build_agent_model(registry, selection)
    if model is None:
        raise RuntimeError("extractor model unavailable")
    content = model.invoke(prompt).content
    if not isinstance(content, str):
        raise RuntimeError(
            f"extractor LLM returned non-str content (type={type(content).__name__!r}); "
            "expected a plain-text JSON response"
        )
    return content


def _extractor_model_id() -> str:
    """Return the ACTUAL model id resolved for extraction (flash-tier when
    available, else the registry default).

    Called at job-run time so meta.extractor_model records the real model,
    not the tier-concept string from MemoryConfig.extractor_model.
    """
    from ..channel_registry import get_registry
    return resolve_extractor_selection(get_registry(), get_memory_config())["model"]


def _window_loader(session_id, after_message_id, config: MemoryConfig):
    return load_extraction_window(session_id, after_message_id, config)


def get_memory_store() -> MemoryStore:
    global _STORE
    with _LOCK:
        if _STORE is None:
            _STORE = MemoryStore(get_memory_config())
        return _STORE


def get_memory_queue() -> MemoryWriteQueue:
    global _QUEUE
    with _LOCK:
        if _QUEUE is None:
            from app import database
            config = get_memory_config()
            _QUEUE = MemoryWriteQueue(
                config, get_memory_store(), ExtractionRunStore(config),
                session_factory=lambda: database.SessionLocal(),
                window_loader=lambda sid, after, cfg: _window_loader(sid, after, cfg),
                extractor_llm=lambda prompt: _extractor_llm(prompt),
                portfolio_resolver=book_scope_for_session,
                extractor_model_fn=lambda: _extractor_model_id())
        return _QUEUE


def get_memory_middleware() -> MemoryMiddleware:
    global _MIDDLEWARE
    with _LOCK:
        if _MIDDLEWARE is None:
            from app import database
            _MIDDLEWARE = MemoryMiddleware(
                config=get_memory_config(), store=get_memory_store(),
                queue=get_memory_queue(), session_factory=lambda: database.SessionLocal())
        return _MIDDLEWARE


def shutdown_memory_runtime(*, grace: float | None = None) -> None:
    """Drain + close the memory writer on app shutdown.

    No-op if the queue was never created (never spin one up at shutdown).
    flush() persists any in-memory pending run rows (run_job's first step is
    enqueue_run), so unprocessed jobs survive as durable `pending` runs.
    """
    if _QUEUE is None:
        return
    _QUEUE.flush(grace=grace)
    _QUEUE.close()


def reset_memory_runtime() -> None:
    global _STORE, _QUEUE, _MIDDLEWARE
    with _LOCK:
        if _QUEUE is not None:
            _QUEUE.close()
        _STORE = _QUEUE = _MIDDLEWARE = None


def enqueue_session_close(*, session_id, thread_id, persona, book_scope_id) -> None:
    if not get_memory_config().enabled:
        return
    spec = RunSpec(run_key=session_run_key(session_id), kind="session",
                   session_id=session_id, thread_id=thread_id, persona=persona,
                   book_scope_id=book_scope_id, trigger_message_id=None)
    get_memory_queue().enqueue(QueueJob(spec, "normal"))


def latest_user_message_id(session, thread_id) -> int | None:
    """Durable integer id of the most recent user AgentMessage on a thread."""
    from app.models import AgentMessage

    row = (session.query(AgentMessage.id)
           .filter(AgentMessage.thread_id == thread_id, AgentMessage.role == "user")
           .order_by(AgentMessage.id.desc()).first())
    return int(row[0]) if row else None


def memory_configurable(*, session_id, thread_id, persona, message_id=None) -> dict:
    """The configurable keys MemoryMiddleware reads (book read-scope + correction
    fast-path). Merge into the graph invocation's configurable_extra. All ids are
    DURABLE integers (AgentSession.id / AgentThread.id / AgentMessage.id)."""
    cfg = {"memory_session_id": session_id, "memory_thread_id": thread_id,
           "memory_persona": persona}
    if isinstance(message_id, int):
        cfg["memory_message_id"] = message_id
    return cfg
