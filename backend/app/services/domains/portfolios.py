"""Portfolios domain service.

Pure-Python facade over the existing portfolio_service.py + portfolio_membership.py.
Returns ORM objects; never JSON. Session-aware.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app import database
from app.models import Portfolio
from app.services import portfolio_service as _ps
from app.services.audit import record_audit


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    """Yield a session; if we owned it, leave commit responsibility to the caller.

    Aligning with pricing/risk/reporting: write functions in this module
    commit explicitly before returning. The scope no longer auto-commits so
    read paths don't incur a redundant flush.
    """
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def list_all(
    *,
    kind: str | None = None,
    session: Session | None = None,
) -> list[Portfolio]:
    """Return all portfolios, optionally filtered by kind ('container' | 'view')."""
    with _session_scope(session) as sess:
        q = sess.query(Portfolio).order_by(Portfolio.id)
        if kind is not None:
            q = q.filter(Portfolio.kind == kind)
        return q.all()


def get(*, portfolio_id: int, session: Session | None = None) -> Portfolio | None:
    """Return one portfolio by id, or None if not found."""
    with _session_scope(session) as sess:
        return sess.get(Portfolio, portfolio_id)


def preview_membership(
    *,
    portfolio_id: int,
    session: Session | None = None,
) -> list[int]:
    """Return the resolved position ids for a portfolio (container or view)."""
    with _session_scope(session) as sess:
        return _ps.preview_membership(sess, portfolio_id)


def get_by_name(*, name: str, session: Session | None = None) -> Portfolio | None:
    """Return one portfolio by exact name, or None if not found."""
    with _session_scope(session) as sess:
        return sess.query(Portfolio).filter(Portfolio.name == name).first()


def resolve(
    *,
    identifier: int | str,
    session: Session | None = None,
) -> Portfolio | None:
    """Accept an int id, a numeric string, or a name, and resolve to a Portfolio."""
    if isinstance(identifier, int):
        return get(portfolio_id=identifier, session=session)
    text = str(identifier)
    if text.isdigit():
        return get(portfolio_id=int(text), session=session)
    return get_by_name(name=text, session=session)


def create(
    *,
    name: str,
    kind: str,
    base_currency: str = "USD",
    description: str | None = None,
    tags: list[str] | None = None,
    filter_rule: dict | None = None,
    source_portfolio_ids: list[int] | None = None,
    manual_include_ids: list[int] | None = None,
    manual_exclude_ids: list[int] | None = None,
    session: Session | None = None,
) -> Portfolio:
    """Create a portfolio. Delegates to portfolio_service.create_portfolio for validation."""
    with _session_scope(session) as sess:
        result = _ps.create_portfolio(
            sess,
            name=name,
            kind=kind,
            base_currency=base_currency,
            description=description,
            tags=tags or [],
            filter_rule=filter_rule,
            source_portfolio_ids=source_portfolio_ids or [],
            manual_include_ids=manual_include_ids or [],
            manual_exclude_ids=manual_exclude_ids or [],
        )
        sess.commit()
        return result


def update(
    *,
    portfolio_id: int,
    fields: dict,
    session: Session | None = None,
) -> Portfolio | None:
    """Update a portfolio's mutable fields (name/description/base_currency/tags).

    Returns the updated Portfolio, or None if not found.
    """
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.update_portfolio(
            sess,
            portfolio_id,
            name=fields.get("name"),
            description=fields.get("description"),
            base_currency=fields.get("base_currency"),
            tags=fields.get("tags"),
        )
        sess.commit()
        return result


def delete(*, portfolio_id: int, session: Session | None = None) -> bool:
    """Delete a portfolio. Returns True if deleted, False if not found."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return False
        _ps.delete_portfolio(sess, portfolio_id)
        sess.commit()
        return True


def set_rule(
    *,
    portfolio_id: int,
    filter_rule: dict | None,
    session: Session | None = None,
) -> Portfolio | None:
    """Replace a view portfolio's filter_rule. Pass None to clear."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.set_filter_rule(sess, portfolio_id, filter_rule)
        sess.commit()
        return result


def add_member_positions(
    *,
    portfolio_id: int,
    position_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Add manual_include_ids on a view portfolio."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.add_manual_includes(sess, portfolio_id, position_ids)
        sess.commit()
        return result


def remove_member_positions(
    *,
    portfolio_id: int,
    position_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Remove from manual_include_ids on a view portfolio."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.remove_manual_includes(sess, portfolio_id, position_ids)
        sess.commit()
        return result


def add_member_excludes(
    *,
    portfolio_id: int,
    position_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Add manual_exclude_ids on a view portfolio."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.add_manual_excludes(sess, portfolio_id, position_ids)
        sess.commit()
        return result


def remove_member_excludes(
    *,
    portfolio_id: int,
    position_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Remove from manual_exclude_ids on a view portfolio."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.remove_manual_excludes(sess, portfolio_id, position_ids)
        sess.commit()
        return result


def set_tags(
    *,
    portfolio_id: int,
    tags: list[str],
    session: Session | None = None,
) -> Portfolio | None:
    """Replace a portfolio's tags."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.set_portfolio_tags(sess, portfolio_id, tags)
        sess.commit()
        return result


def physically_delete_positions(
    *,
    portfolio_id: int,
    position_ids: list[int],
    actor: str = "agent",
    session: Session | None = None,
) -> tuple[Portfolio, list[int]] | None:
    """Delete physical Position rows owned by a container portfolio.

    Returns (portfolio, deleted_position_ids) on success, or None if not found.
    Mirrors the container-kind branch of the legacy
    ``remove_positions_from_portfolio`` tool.
    """
    with _session_scope(session) as sess:
        portfolio = sess.get(Portfolio, portfolio_id)
        if portfolio is None:
            return None
        wanted = {int(i) for i in position_ids}
        deleted: list[int] = []
        for pos in list(portfolio.positions):
            if pos.id in wanted:
                sess.delete(pos)
                deleted.append(pos.id)
        sess.flush()
        record_audit(
            sess,
            event_type="portfolio.positions_removed",
            actor=actor,
            subject_type="portfolio",
            subject_id=portfolio.id,
            payload={"deleted_position_ids": deleted},
        )
        sess.commit()
        return portfolio, deleted


def add_sources(
    *,
    portfolio_id: int,
    source_portfolio_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Append source portfolios to a view (cycle-checked)."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.add_portfolio_sources(sess, portfolio_id, source_portfolio_ids)
        sess.commit()
        return result


def remove_sources(
    *,
    portfolio_id: int,
    source_portfolio_ids: list[int],
    session: Session | None = None,
) -> Portfolio | None:
    """Remove source portfolios from a view."""
    with _session_scope(session) as sess:
        if sess.get(Portfolio, portfolio_id) is None:
            return None
        result = _ps.remove_portfolio_sources(sess, portfolio_id, source_portfolio_ids)
        sess.commit()
        return result
