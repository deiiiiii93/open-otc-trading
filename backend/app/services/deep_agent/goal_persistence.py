"""DB-backed persistence for goal runs (spec §H, slice 5 surface).

``GoalRunStore``/``GoalRunService`` take dict-shaped backends keyed by ``str(thread_id)``.
In tests those are plain dicts; in production they are ``ThreadColumnBackend`` instances
that read/write a JSON column on the owning ``AgentThread`` row (``goal_run`` for the run
state, ``goal_contract`` for the frozen contract).

Each operation opens its own short transaction via ``session_factory`` and commits
immediately, so the value persists across requests/processes. Cross-request atomicity of
the store's check-then-write critical sections rests on its in-process ``threading.Lock``
(this is a single-process app); the per-op transaction only guarantees a consistent row
write, not a multi-step compare-and-set.
"""
from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from ...models import AgentThread

_SENTINEL = object()


class ThreadColumnBackend:
    """A ``dict``-shaped view of one JSON column on ``AgentThread``, keyed by thread id.

    Implements only the mapping operations the goal store/service actually use —
    ``get``, ``__setitem__``, ``pop`` — over a per-call DB session. Keys are the
    string thread ids the HTTP layer carries; they are coerced to ``int`` for the
    primary-key lookup.
    """

    def __init__(self, session_factory: Callable[[], Session], column: str):
        if column not in {"goal_run", "goal_contract"}:
            raise ValueError(f"unsupported goal column: {column!r}")
        self._session_factory = session_factory
        self._column = column

    def get(self, thread_id: str, default: Any = None) -> Any:
        with self._session_factory() as session:
            value = self._read(session, thread_id, _SENTINEL)
        return default if value is _SENTINEL else value

    def __setitem__(self, thread_id: str, value: Any) -> None:
        with self._session_factory() as session:
            thread = session.get(AgentThread, int(thread_id))
            if thread is None:
                raise KeyError(thread_id)
            setattr(thread, self._column, value)
            session.commit()

    def pop(self, thread_id: str, default: Any = None) -> Any:
        with self._session_factory() as session:
            value = self._read(session, thread_id, _SENTINEL)
            if value is _SENTINEL:
                return default
            thread = session.get(AgentThread, int(thread_id))
            setattr(thread, self._column, None)
            session.commit()
            return default if value is None else value

    def _read(self, session: Session, thread_id: str, missing: Any) -> Any:
        thread = session.get(AgentThread, int(thread_id))
        if thread is None:
            return missing
        value = getattr(thread, self._column)
        return missing if value is None else value
