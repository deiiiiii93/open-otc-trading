"""Scope identity + book resolution (spec §Decisions 1-2, §Book scope)."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.orm import Session

Scope = tuple[str, str]

_FIXED = {"user": "desk", "correction": "desk", "domain": "global"}


def scope_key(scope_type: str, principal: str = "desk") -> Scope:
    if scope_type == "book":
        return ("book", str(principal))
    return (scope_type, _FIXED.get(scope_type, "desk"))


def resolve_book_scope(
    source_portfolio_ids: Sequence[int], is_live: Callable[[int], bool]
) -> Scope | None:
    live = [pid for pid in source_portfolio_ids if is_live(pid)]
    if len(live) == 1:
        return ("book", str(live[0]))
    return None


def active_read_scopes(book: Scope | None) -> list[Scope]:
    scopes: list[Scope] = [("user", "desk"), ("correction", "desk"), ("domain", "global")]
    if book is not None:
        scopes.append(book)
    return scopes


def active_write_scopes(book: Scope | None) -> list[str]:
    scopes = ["user", "correction", "domain"]
    if book is not None:
        scopes.append("book")
    return scopes


def _portfolio_ids_from_pack(stable_payload) -> list[int]:
    payload = stable_payload or {}
    brief = payload.get("task_brief") if isinstance(payload, dict) else None
    ids = brief.get("portfolio_ids") if isinstance(brief, dict) else None
    if isinstance(ids, list):
        return [int(x) for x in ids if isinstance(x, (int, str)) and str(x).isdigit()]
    return []


def book_scope_for_session(session: Session, session_id: int) -> str | None:
    from app.models import AgentSession, ContextPack, ContextPackPayload, Portfolio

    agent_session = session.get(AgentSession, session_id)
    if agent_session is None:
        return None
    packs = (
        session.query(ContextPack)
        .filter(ContextPack.workflow_id == agent_session.workflow_id)
        .order_by(ContextPack.created_at.desc(), ContextPack.id.desc())
        .all()
    )
    # "Live" = a portfolio row that currently exists in the DB.
    # Portfolio has no soft-delete/status column; existence in the table IS
    # the liveness predicate. A deleted portfolio's id no longer appears here.
    live_ids = {pid for (pid,) in session.query(Portfolio.id).all()}
    for pack in packs:
        payload = session.get(ContextPackPayload, pack.payload_id)
        ids = _portfolio_ids_from_pack(payload.stable_payload if payload else None)
        if not ids:
            continue
        scope = resolve_book_scope(ids, live_ids.__contains__)
        return scope[1] if scope is not None else None
    return None
