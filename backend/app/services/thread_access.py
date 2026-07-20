"""Public Agent Desk access rules for server-owned internal threads."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Query, Session

from ..models import AgentThread


RESERVED_INTERNAL_THREAD_SOURCES = frozenset({"hedge_evidence"})


def is_reserved_internal_thread_source(source: str | None) -> bool:
    return source in RESERVED_INTERNAL_THREAD_SOURCES


def public_thread_query(session: Session) -> Query:
    """Return the query base for threads a desk client may see or operate."""
    return session.query(AgentThread).filter(
        AgentThread.source.notin_(RESERVED_INTERNAL_THREAD_SOURCES)
    )


def get_public_thread(session: Session, thread_id: Any) -> AgentThread | None:
    """Resolve a client-supplied id only when it names a public desk thread."""
    try:
        primary_key = int(thread_id)
    except (TypeError, ValueError):
        return None
    return public_thread_query(session).filter(AgentThread.id == primary_key).one_or_none()
