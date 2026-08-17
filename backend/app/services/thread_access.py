"""Public Agent Desk access rules for server-owned internal threads."""
from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Query, Session

from ..models import AgentMessage, AgentThread


def _like_pattern(term: str) -> str:
    """Wrap `term` for a substring LIKE, with wildcards escaped.

    `%` and `_` are LIKE metacharacters, so a desk user searching `100%` or
    `book_id` would otherwise get matches they never asked for.
    """
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


RESERVED_INTERNAL_THREAD_SOURCES = frozenset({"hedge_evidence"})


def is_reserved_internal_thread_source(source: str | None) -> bool:
    return source in RESERVED_INTERNAL_THREAD_SOURCES


def public_thread_query(
    session: Session, source: str | None = None, search: str | None = None
) -> Query:
    """Return the query base for threads a desk client may see or operate.

    `source` scopes the result to one client's own threads. It is optional
    because per-thread resolution (`get_public_thread`) must stay able to reach
    a thread of any public source; only the *list* is scoped. Scoping matters
    for more than tidiness: arena runs mint a thread per match, so an unscoped
    list grows without bound with board history and eagerly loads every one of
    their messages.

    `search` matches a thread's title OR any of its messages' content. It runs
    here — server-side, across every thread in scope — because the list itself
    is paged: filtering on the client would only ever search the page that
    happened to be loaded.
    """
    query = session.query(AgentThread).filter(
        AgentThread.source.notin_(RESERVED_INTERNAL_THREAD_SOURCES)
    )
    if source is not None:
        query = query.filter(AgentThread.source == source)
    if search:
        pattern = _like_pattern(search)
        query = query.filter(
            or_(
                AgentThread.title.ilike(pattern, escape="\\"),
                AgentThread.id.in_(
                    select(AgentMessage.thread_id).where(
                        AgentMessage.content.ilike(pattern, escape="\\")
                    )
                ),
            )
        )
    return query


def get_public_thread(session: Session, thread_id: Any) -> AgentThread | None:
    """Resolve a client-supplied id only when it names a public desk thread."""
    try:
        primary_key = int(thread_id)
    except (TypeError, ValueError):
        return None
    return public_thread_query(session).filter(AgentThread.id == primary_key).one_or_none()
