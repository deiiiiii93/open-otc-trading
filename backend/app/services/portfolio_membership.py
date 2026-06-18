"""Membership resolution for container vs view portfolios.

For containers: returns owned positions via FK.
For views: returns ``(rule_matches ∪ source_resolved ∪ manual_includes)
− manual_excludes`` with cycle detection and depth ≤ 3.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Portfolio,
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKind,
    Position,
)
from .portfolio_rule import compile_rule_to_sqla


MAX_AGGREGATION_DEPTH = 3
SLOW_RESOLVE_MS = 250

logger = logging.getLogger(__name__)


def resolve_positions(
    portfolio: Portfolio,
    session: Session,
    *,
    _visited: frozenset[int] | None = None,
    _depth: int = 0,
    _path: tuple[int, ...] = (),
) -> list[Position]:
    started = time.monotonic()
    visited = _visited or frozenset()
    if portfolio.id in visited:
        cycle = list(_path) + [portfolio.id]
        raise PortfolioCycleError(
            f"Cycle detected: {' -> '.join(str(i) for i in cycle)}",
            cycle_path=cycle,
        )
    if _depth > MAX_AGGREGATION_DEPTH:
        chain = list(_path) + [portfolio.id]
        raise PortfolioDepthError(
            f"Aggregation depth exceeded at portfolio {portfolio.id}",
            depth_path=chain,
        )
    visited = visited | {portfolio.id}
    path = _path + (portfolio.id,)

    out = _resolve_inner(portfolio, session, visited=visited, depth=_depth, path=path)

    if _depth == 0:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > SLOW_RESOLVE_MS:
            logger.warning(
                "resolve_positions slow: portfolio_id=%s kind=%s ms=%d count=%d",
                portfolio.id, portfolio.kind, elapsed_ms, len(out),
            )
    return out


def _resolve_inner(
    portfolio: Portfolio,
    session: Session,
    *,
    visited: frozenset[int],
    depth: int,
    path: tuple[int, ...],
) -> list[Position]:
    if portfolio.kind == PortfolioKind.CONTAINER.value:
        return list(portfolio.positions)

    matched: dict[int, Position] = {}

    if portfolio.filter_rule is not None:
        clause = compile_rule_to_sqla(portfolio.filter_rule)
        for p in session.execute(
            select(Position)
            .join(Portfolio, Position.portfolio_id == Portfolio.id)
            .where(Portfolio.kind == PortfolioKind.CONTAINER.value)
            .where(clause)
        ).scalars():
            matched[p.id] = p

    for src_id in portfolio.source_portfolio_ids or []:
        src = session.get(Portfolio, src_id)
        if src is None:
            continue
        for p in resolve_positions(src, session, _visited=visited, _depth=depth + 1, _path=path):
            matched[p.id] = p

    for inc in portfolio.manual_include_ids or []:
        p = session.get(Position, inc)
        if p is not None and p.portfolio.kind == PortfolioKind.CONTAINER.value:
            matched[p.id] = p

    for exc in portfolio.manual_exclude_ids or []:
        matched.pop(exc, None)

    return list(matched.values())


def resolve_position_ids(portfolio: Portfolio, session: Session) -> list[int]:
    return [p.id for p in resolve_positions(portfolio, session)]


def find_descendants(
    session: Session,
    portfolio_id: int,
    *,
    _visited: set[int] | None = None,
) -> set[int]:
    """All portfolio ids reachable through ``source_portfolio_ids``.

    Used by the picker to exclude descendants from the candidate list.
    Loops are guarded but not raised — this is a UI helper, not a
    validation point.
    """
    visited = _visited if _visited is not None else set()
    if portfolio_id in visited:
        return visited
    visited.add(portfolio_id)
    p = session.get(Portfolio, portfolio_id)
    if p is None:
        return visited
    for child in p.source_portfolio_ids or []:
        find_descendants(session, child, _visited=visited)
    return visited
