"""Portfolio service — single authoritative module wrapping CRUD,
membership preview, and audit. Used by HTTP, CLI, and LangChain tool layers.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Iterable

from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import (
    Portfolio,
    PortfolioCycleError,
    PortfolioDepthError,
    PortfolioKind,
    PortfolioKindError,
    PortfolioNameConflict,
    Position,
    PositionImportBatch,
    PositionValuationResult,
    PositionValuationRun,
    RiskRun,
    RuleValidationError,
)
from .audit import record_audit
from .portfolio_membership import (
    MAX_AGGREGATION_DEPTH,
    find_descendants,
    resolve_position_ids,
    resolve_positions,
)
from .portfolio_rule import validate_rule


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def list_portfolios(
    session: Session,
    *,
    kind: str | None = None,
    tags: Iterable[str] | None = None,
) -> list[Portfolio]:
    q = session.query(Portfolio)
    if kind is not None:
        q = q.filter(Portfolio.kind == kind)
    out = q.order_by(Portfolio.created_at.desc()).all()
    if tags:
        wanted = {t.lower() for t in tags}
        out = [p for p in out if wanted.issubset(set(p.tags or []))]
    return out


def get_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    p = session.get(Portfolio, portfolio_id)
    if p is None:
        raise LookupError(f"Portfolio {portfolio_id} not found")
    return p


def preview_membership(session: Session, portfolio_id: int) -> list[int]:
    p = get_portfolio(session, portfolio_id)
    return resolve_position_ids(p, session)


def preview_membership_dry_run(
    session: Session,
    *,
    kind: str,
    filter_rule: dict | None = None,
    manual_include_ids: Iterable[int] = (),
    manual_exclude_ids: Iterable[int] = (),
    source_portfolio_ids: Iterable[int] = (),
) -> list[int]:
    if kind not in (PortfolioKind.CONTAINER.value, PortfolioKind.VIEW.value):
        raise PortfolioKindError(f"Unknown kind: {kind}")
    if kind == PortfolioKind.CONTAINER.value:
        return []
    fake = SimpleNamespace(
        id=0,
        kind=PortfolioKind.VIEW.value,
        filter_rule=filter_rule,
        manual_include_ids=list(manual_include_ids),
        manual_exclude_ids=list(manual_exclude_ids),
        source_portfolio_ids=list(source_portfolio_ids),
        positions=[],
    )
    return [p.id for p in resolve_positions(fake, session)]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers (used by writers in later tasks)
# ---------------------------------------------------------------------------

def _normalize_tags(tags: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for t in tags or []:
        if not isinstance(t, str):
            raise RuleValidationError([f"Tag must be a string, got {type(t).__name__}"])
        s = t.strip().lower()
        if not s:
            continue
        if len(s) > 40:
            raise RuleValidationError([f"Tag too long (>40 chars): {t!r}"])
        if s not in seen:
            seen.append(s)
    return seen


def _require_view(p: Portfolio) -> None:
    if p.kind != PortfolioKind.VIEW.value:
        raise PortfolioKindError(f"Portfolio {p.id} is a {p.kind}, not a view")


def _require_container(p: Portfolio) -> None:
    if p.kind != PortfolioKind.CONTAINER.value:
        raise PortfolioKindError(f"Portfolio {p.id} is a {p.kind}, not a container")


def _check_position_ids_exist(session: Session, ids: Iterable[int]) -> list[int]:
    ids_list = list(dict.fromkeys(int(i) for i in ids))
    if not ids_list:
        return []
    found = {pid for (pid,) in session.query(Position.id).filter(Position.id.in_(ids_list))}
    missing = [i for i in ids_list if i not in found]
    if missing:
        raise RuleValidationError([f"Unknown position ids: {missing}"])
    return ids_list


def _check_portfolio_ids_exist(session: Session, ids: Iterable[int]) -> list[int]:
    ids_list = list(dict.fromkeys(int(i) for i in ids))
    if not ids_list:
        return []
    found = {pid for (pid,) in session.query(Portfolio.id).filter(Portfolio.id.in_(ids_list))}
    missing = [i for i in ids_list if i not in found]
    if missing:
        raise RuleValidationError([f"Unknown source portfolio ids: {missing}"])
    return ids_list


def _check_no_cycle(session: Session, portfolio_id: int, candidate_sources: Iterable[int]) -> None:
    for src_id in candidate_sources:
        if src_id == portfolio_id:
            raise PortfolioCycleError(
                f"Self-reference: portfolio {portfolio_id}",
                cycle_path=[portfolio_id, portfolio_id],
            )
        descendants = find_descendants(session, src_id)
        if portfolio_id in descendants:
            raise PortfolioCycleError(
                f"Adding source {src_id} would create cycle through {portfolio_id}",
                cycle_path=[portfolio_id, src_id, portfolio_id],
            )


# ---------------------------------------------------------------------------
# Create / update / delete
# ---------------------------------------------------------------------------

def create_portfolio(
    session: Session,
    *,
    name: str,
    base_currency: str,
    kind: str,
    filter_rule: dict | None = None,
    manual_include_ids: Iterable[int] = (),
    manual_exclude_ids: Iterable[int] = (),
    source_portfolio_ids: Iterable[int] = (),
    tags: Iterable[str] = (),
    description: str | None = None,
    actor: str = "desk_user",
) -> Portfolio:
    if kind not in (PortfolioKind.CONTAINER.value, PortfolioKind.VIEW.value):
        raise PortfolioKindError(f"Unknown kind: {kind}")
    is_view = kind == PortfolioKind.VIEW.value
    if not is_view and (filter_rule or manual_include_ids or manual_exclude_ids or source_portfolio_ids):
        raise PortfolioKindError("Container portfolios cannot have filter_rule, manual_includes/excludes, or sources")

    if filter_rule is not None:
        errors = validate_rule(filter_rule)
        if errors:
            raise RuleValidationError(errors)

    includes = _check_position_ids_exist(session, manual_include_ids) if is_view else []
    excludes = _check_position_ids_exist(session, manual_exclude_ids) if is_view else []
    overlap = set(includes) & set(excludes)
    if overlap:
        raise RuleValidationError([f"Position id(s) in both includes and excludes: {sorted(overlap)}"])
    sources = _check_portfolio_ids_exist(session, source_portfolio_ids) if is_view else []
    normalized_tags = _normalize_tags(tags)

    portfolio = Portfolio(
        name=name,
        base_currency=base_currency,
        kind=kind,
        filter_rule=filter_rule if is_view else None,
        manual_include_ids=includes,
        manual_exclude_ids=excludes,
        source_portfolio_ids=sources,
        tags=normalized_tags,
        description=description,
    )
    session.add(portfolio)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise PortfolioNameConflict(f"Portfolio name already exists: {name!r}") from exc

    record_audit(
        session,
        event_type="portfolio.created",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={
            "name": name,
            "kind": kind,
            "tags": normalized_tags,
            "has_rule": filter_rule is not None,
            "source_count": len(sources),
            "include_count": len(includes),
            "exclude_count": len(excludes),
        },
    )
    return portfolio


def update_portfolio(
    session: Session,
    portfolio_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    base_currency: str | None = None,
    tags: Iterable[str] | None = None,
    actor: str = "desk_user",
) -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    changed: dict[str, object] = {}
    if name is not None and name != portfolio.name:
        portfolio.name = name
        changed["name"] = name
    if description is not None and description != portfolio.description:
        portfolio.description = description
        changed["description"] = description
    if base_currency is not None and base_currency != portfolio.base_currency:
        portfolio.base_currency = base_currency
        changed["base_currency"] = base_currency
    if tags is not None:
        normalized = _normalize_tags(tags)
        if normalized != list(portfolio.tags or []):
            portfolio.tags = normalized
            changed["tags"] = normalized
    if not changed:
        return portfolio
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise PortfolioNameConflict(f"Portfolio name already exists: {name!r}") from exc
    record_audit(
        session,
        event_type="portfolio.updated",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload=changed,
    )
    if "tags" in changed:
        record_audit(
            session,
            event_type="portfolio.tags_changed",
            actor=actor,
            subject_type="portfolio",
            subject_id=portfolio.id,
            payload={"tags": changed["tags"]},
        )
    return portfolio


def delete_portfolio(session: Session, portfolio_id: int, *, actor: str = "desk_user") -> None:
    portfolio = get_portfolio(session, portfolio_id)
    record_audit(
        session,
        event_type="portfolio.deleted",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"name": portfolio.name, "kind": portfolio.kind},
    )
    run_ids = [
        row.id
        for row in session.query(PositionValuationRun.id)
        .filter(PositionValuationRun.portfolio_id == portfolio.id)
        .all()
    ]
    if run_ids:
        session.query(PositionValuationResult).filter(
            PositionValuationResult.valuation_run_id.in_(run_ids)
        ).delete(synchronize_session=False)
    session.query(PositionValuationRun).filter(
        PositionValuationRun.portfolio_id == portfolio.id
    ).delete(synchronize_session=False)
    session.query(RiskRun).filter(RiskRun.portfolio_id == portfolio.id).delete(synchronize_session=False)
    session.query(PositionImportBatch).filter(
        PositionImportBatch.portfolio_id == portfolio.id
    ).delete(synchronize_session=False)
    session.query(Position).filter(Position.portfolio_id == portfolio.id).delete(synchronize_session=False)
    session.delete(portfolio)
    session.flush()


# ---------------------------------------------------------------------------
# Sub-resources
# ---------------------------------------------------------------------------

def set_filter_rule(
    session: Session,
    portfolio_id: int,
    rule: dict | None,
    *,
    actor: str = "desk_user",
) -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    _require_view(portfolio)
    if rule is not None:
        errors = validate_rule(rule)
        if errors:
            raise RuleValidationError(errors)
    portfolio.filter_rule = rule
    session.flush()
    record_audit(
        session,
        event_type="portfolio.rule_changed",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"rule": rule},
    )
    return portfolio


def _modify_id_list(
    session: Session,
    portfolio_id: int,
    attr: str,
    *,
    add: Iterable[int] | None = None,
    remove: Iterable[int] | None = None,
    audit_event: str,
    actor: str,
    overlap_attr: str | None = None,
    check_existence: str = "position",  # "position" | "portfolio" | "none"
    cycle_check_self: bool = False,
) -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    _require_view(portfolio)

    current: list[int] = list(getattr(portfolio, attr) or [])
    if add:
        if check_existence == "position":
            ids = _check_position_ids_exist(session, add)
        elif check_existence == "portfolio":
            ids = _check_portfolio_ids_exist(session, add)
            if cycle_check_self:
                _check_no_cycle(session, portfolio.id, ids)
        else:
            ids = list(dict.fromkeys(int(i) for i in add))
        if overlap_attr is not None:
            other = set(getattr(portfolio, overlap_attr) or [])
            overlap = set(ids) & other
            if overlap:
                raise RuleValidationError([f"Ids in conflict with {overlap_attr}: {sorted(overlap)}"])
        for i in ids:
            if i not in current:
                current.append(i)
    if remove:
        rm = {int(i) for i in remove}
        current = [i for i in current if i not in rm]

    setattr(portfolio, attr, current)
    session.flush()
    record_audit(
        session,
        event_type=audit_event,
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"attr": attr, "added": list(add or []), "removed": list(remove or []), "result": current},
    )
    return portfolio


def add_manual_includes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_include_ids", add=position_ids,
        audit_event="portfolio.positions_added", actor=actor,
        overlap_attr="manual_exclude_ids", check_existence="position",
    )


def remove_manual_includes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_include_ids", remove=position_ids,
        audit_event="portfolio.positions_removed", actor=actor, check_existence="none",
    )


def add_manual_excludes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_exclude_ids", add=position_ids,
        audit_event="portfolio.positions_added", actor=actor,
        overlap_attr="manual_include_ids", check_existence="position",
    )


def remove_manual_excludes(session, portfolio_id, position_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "manual_exclude_ids", remove=position_ids,
        audit_event="portfolio.positions_removed", actor=actor, check_existence="none",
    )


def add_portfolio_sources(session, portfolio_id, source_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "source_portfolio_ids", add=source_ids,
        audit_event="portfolio.sources_added", actor=actor, check_existence="portfolio",
        cycle_check_self=True,
    )


def remove_portfolio_sources(session, portfolio_id, source_ids, *, actor="desk_user"):
    return _modify_id_list(
        session, portfolio_id, "source_portfolio_ids", remove=source_ids,
        audit_event="portfolio.sources_removed", actor=actor, check_existence="none",
    )


def set_portfolio_tags(session, portfolio_id, tags, *, actor="desk_user") -> Portfolio:
    portfolio = get_portfolio(session, portfolio_id)
    normalized = _normalize_tags(tags)
    portfolio.tags = normalized
    session.flush()
    record_audit(
        session,
        event_type="portfolio.tags_changed",
        actor=actor,
        subject_type="portfolio",
        subject_id=portfolio.id,
        payload={"tags": normalized},
    )
    return portfolio
