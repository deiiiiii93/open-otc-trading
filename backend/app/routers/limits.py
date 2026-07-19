"""HTTP boundary for the versioned Limits domain.

The deterministic services own validation and persistence.  This module only
translates trusted desk-user requests into typed service calls, applies the
portfolio visibility boundary, and serializes stable read models.
"""
from __future__ import annotations

from collections.abc import Callable, Generator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import database
from app.models import (
    LimitEvaluation,
    LimitIncident,
    LimitMonitoringRun,
    MarketSnapshot,
    Portfolio,
    RiskLimit,
    RiskLimitVersion,
    TaskRun,
)
from app.schemas import (
    LimitActionIn,
    LimitActivateIn,
    LimitCreateIn,
    LimitEvaluationOut,
    LimitIncidentAssignIn,
    LimitIncidentCommentIn,
    LimitIncidentOut,
    LimitIncidentWaiveIn,
    LimitMetadataPatchIn,
    LimitMonitoringRunCreateIn,
    LimitMonitoringRunOut,
    LimitSourceReferenceOut,
    LimitVersionCreateIn,
    LimitVersionOut,
    MarketSnapshotOut,
    RiskLimitOut,
)
from app.services.limits import definitions, incidents, monitoring
from app.services.limits.contracts import LimitActionContext, LimitVersionSpec
from app.services.limits.errors import (
    LimitConflictError,
    LimitNotFoundError,
    LimitValidationError,
)


GetDb = Callable[[], Generator[Session, None, None]]
DispatchLimitMonitoring = Callable[[int, int], None]


def _default_get_db() -> Generator[Session, None, None]:
    yield from database.get_session()


def _context() -> LimitActionContext:
    """HTTP calls cannot self-attribute an actor, persona, or operating mode."""
    return LimitActionContext(actor="desk_user", persona=None, mode="interactive")


def _raise_domain_error(exc: Exception) -> None:
    if isinstance(exc, LimitNotFoundError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, LimitConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(exc, ValueError) and "not found:" in str(exc).lower():
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if isinstance(exc, (LimitValidationError, ValueError, ValidationError)):
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise exc


def _spec(payload: Any) -> LimitVersionSpec:
    return LimitVersionSpec(**payload.model_dump())


def _limit_out(row: RiskLimit) -> dict[str, Any]:
    versions = [
        LimitVersionOut.model_validate(version).model_dump() for version in row.versions
    ]
    active = next(
        (version for version in versions if version["id"] == row.active_version_id),
        None,
    )
    return RiskLimitOut(
        id=row.id,
        key=row.key,
        name=row.name,
        description=row.description,
        category=row.category,
        owner=row.owner,
        tags=list(row.tags or []),
        active_version_id=row.active_version_id,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        versions=versions,
        active_version=active,
    ).model_dump()


def _run_out(row: LimitMonitoringRun) -> dict[str, Any]:
    inputs = dict((row.definition_snapshot or {}).get("inputs") or {})
    task_id = max((task.id for task in row.task_runs), default=None)
    return LimitMonitoringRunOut(
        id=row.id,
        trigger=row.trigger,
        mode=row.mode,
        portfolio_id=row.portfolio_id,
        pricing_parameter_profile_id=row.pricing_parameter_profile_id,
        engine_config_id=row.engine_config_id,
        market_snapshot_id=row.market_snapshot_id,
        effective_market_evidence_id=inputs.get("effective_market_evidence_id"),
        valuation_as_of=row.valuation_as_of,
        source_policy=row.source_policy,
        max_source_age_seconds=row.max_source_age_seconds,
        status=row.status,
        summary=dict(row.summary or {}),
        definition_snapshot_hash=row.definition_snapshot_hash,
        limit_version_ids=sorted(link.limit_version_id for link in row.version_links),
        task_id=task_id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        source_references=[
            LimitSourceReferenceOut.model_validate(reference).model_dump()
            for reference in sorted(
                row.source_references,
                key=lambda reference: reference.id,
            )
        ],
    ).model_dump()


def _incident_out(row: LimitIncident, *, portfolio_id: int) -> dict[str, Any]:
    if row.portfolio_id != portfolio_id:
        raise ValueError("incident portfolio scope mismatch")
    return LimitIncidentOut(
        id=row.id,
        risk_limit_id=row.risk_limit_id,
        portfolio_id=row.portfolio_id,
        scope_type=row.scope_type,
        scope_key=row.scope_key,
        scope_label=row.scope_label,
        severity=row.severity,
        status=row.status,
        first_evaluation_id=row.first_evaluation_id,
        last_evaluation_id=row.last_evaluation_id,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        acknowledged_at=row.acknowledged_at,
        waived_at=row.waived_at,
        resolved_at=row.resolved_at,
        owner=row.owner,
        assignee=row.assignee,
        waiver_expires_at=row.waiver_expires_at,
        waiver_rationale=row.waiver_rationale,
        row_version=row.row_version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        risk_limit=_limit_out(row.risk_limit) if row.risk_limit is not None else None,
        # Event ids are the monotonic ledger cursor exposed by summary polling;
        # use the same deterministic order rather than trusting client clocks.
        events=sorted(row.events, key=lambda event: event.id),
    ).model_dump()


def _incident_with_portfolio(
    session: Session, incident_id: int, portfolio_id: int
) -> LimitIncident:
    row = session.scalar(
        select(LimitIncident)
        .where(
            LimitIncident.id == incident_id,
            LimitIncident.portfolio_id == portfolio_id,
        )
        .options(
            selectinload(LimitIncident.events),
            selectinload(LimitIncident.risk_limit).selectinload(RiskLimit.versions),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="limit incident not found")
    return row


def _run_with_portfolio(
    session: Session, run_id: int, portfolio_id: int
) -> LimitMonitoringRun:
    row = session.scalar(
        select(LimitMonitoringRun)
        .where(
            LimitMonitoringRun.id == run_id,
            LimitMonitoringRun.portfolio_id == portfolio_id,
        )
        .options(
            selectinload(LimitMonitoringRun.task_runs),
            selectinload(LimitMonitoringRun.version_links),
            selectinload(LimitMonitoringRun.source_references),
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="limit monitoring run not found")
    return row


def _require_portfolio(session: Session, portfolio_id: int) -> Portfolio:
    row = session.get(Portfolio, portfolio_id)
    if row is None:
        raise HTTPException(status_code=404, detail="portfolio not found")
    return row


def build_limits_router(
    get_db: GetDb | None = None,
    dispatch_limit_monitoring_fn: DispatchLimitMonitoring | None = None,
) -> APIRouter:
    """Build the independently testable Limits router.

    ``get_db`` and dispatch are injectable so API tests never enqueue a real
    worker.  Production defaults preserve the exact committed queue/dispatch
    boundary required by the monitoring service.
    """
    db_dependency = get_db or _default_get_db
    dispatch = dispatch_limit_monitoring_fn or monitoring.dispatch_limit_monitoring
    router = APIRouter(prefix="/api", tags=["limits"])

    @router.get("/limits")
    def list_limits(
        category: str | None = None,
        owner: str | None = None,
        state: str | None = None,
        scope_type: str | None = None,
        tag: str | None = None,
        portfolio_id: int | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(db_dependency),
    ):
        rows = list(
            session.scalars(
                select(RiskLimit)
                .options(selectinload(RiskLimit.versions))
                .order_by(RiskLimit.id.desc())
            )
        )

        def matches(row: RiskLimit) -> bool:
            versions = list(row.versions)
            if category is not None and row.category != category:
                return False
            if owner is not None and row.owner != owner:
                return False
            if tag is not None and tag not in set(row.tags or []):
                return False
            if state is not None and not any(
                version.state == state for version in versions
            ):
                return False
            if scope_type is not None and not any(
                version.scope_type == scope_type for version in versions
            ):
                return False
            if portfolio_id is not None and not any(
                portfolio_id
                in set((version.scope_config or {}).get("portfolio_ids") or [])
                for version in versions
                if version.scope_type == "portfolio"
            ):
                return False
            return True

        filtered = [row for row in rows if matches(row)]
        return {
            "items": [_limit_out(row) for row in filtered[offset : offset + limit]],
            "total": len(filtered),
        }

    @router.post("/limits", status_code=201)
    def create_limit(payload: LimitCreateIn, session: Session = Depends(db_dependency)):
        try:
            identity, _version = definitions.create_limit(
                session,
                key=payload.key,
                name=payload.name,
                description=payload.description,
                category=payload.category,
                owner=payload.owner,
                tags=payload.tags,
                initial_version=_spec(payload.initial_version),
                context=_context(),
            )
            session.commit()
            session.refresh(identity)
            return _limit_out(identity)
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)

    @router.get("/limits/{limit_id}")
    def get_limit(limit_id: int, session: Session = Depends(db_dependency)):
        row = session.scalar(
            select(RiskLimit)
            .where(RiskLimit.id == limit_id)
            .options(selectinload(RiskLimit.versions))
        )
        if row is None:
            raise HTTPException(status_code=404, detail="risk limit not found")
        return _limit_out(row)

    @router.patch("/limits/{limit_id}")
    def patch_limit(
        limit_id: int,
        payload: LimitMetadataPatchIn,
        session: Session = Depends(db_dependency),
    ):
        try:
            patch = payload.model_dump(
                exclude={"expected_row_version"}, exclude_none=True
            )
            row = definitions.update_metadata(
                session,
                limit_id=limit_id,
                expected_row_version=payload.expected_row_version,
                patch=patch,
                context=_context(),
            )
            session.commit()
            session.refresh(row)
            return _limit_out(row)
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)

    @router.post("/limits/{limit_id}/versions", status_code=201)
    def create_version(
        limit_id: int,
        payload: LimitVersionCreateIn,
        session: Session = Depends(db_dependency),
    ):
        try:
            definitions.add_version(
                session,
                limit_id=limit_id,
                expected_row_version=payload.expected_row_version,
                spec=_spec(payload.version),
                context=_context(),
            )
            session.commit()
            row = session.scalar(
                select(RiskLimit)
                .where(RiskLimit.id == limit_id)
                .options(selectinload(RiskLimit.versions))
            )
            if row is None:
                raise HTTPException(status_code=404, detail="risk limit not found")
            return _limit_out(row)
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)

    @router.get("/limits/{limit_id}/versions")
    def list_versions(limit_id: int, session: Session = Depends(db_dependency)):
        if session.get(RiskLimit, limit_id) is None:
            raise HTTPException(status_code=404, detail="risk limit not found")
        return [
            LimitVersionOut.model_validate(row).model_dump()
            for row in session.scalars(
                select(RiskLimitVersion)
                .where(RiskLimitVersion.risk_limit_id == limit_id)
                .order_by(RiskLimitVersion.version)
            )
        ]

    @router.post("/limits/{limit_id}/versions/{version_id}/activate")
    def activate_limit_version(
        limit_id: int,
        version_id: int,
        payload: LimitActivateIn,
        session: Session = Depends(db_dependency),
    ):
        try:
            definitions.activate_version(
                session,
                limit_id=limit_id,
                version_id=version_id,
                expected_row_version=payload.expected_row_version,
                activated_at=None,
                context=_context(),
            )
            session.commit()
            row = session.scalar(
                select(RiskLimit)
                .where(RiskLimit.id == limit_id)
                .options(selectinload(RiskLimit.versions))
            )
            if row is None:
                raise HTTPException(status_code=404, detail="risk limit not found")
            return _limit_out(row)
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)

    def _limit_action(action: Callable[..., RiskLimit]):
        def endpoint(
            limit_id: int,
            payload: LimitActionIn,
            session: Session = Depends(db_dependency),
        ):
            try:
                row = action(
                    session,
                    limit_id=limit_id,
                    expected_row_version=payload.expected_row_version,
                    context=_context(),
                )
                session.commit()
                session.refresh(row)
                return _limit_out(row)
            except Exception as exc:
                session.rollback()
                _raise_domain_error(exc)

        return endpoint

    router.post("/limits/{limit_id}/deactivate")(_limit_action(definitions.deactivate))
    router.post("/limits/{limit_id}/retire")(_limit_action(definitions.retire))

    @router.post("/limit-monitoring/runs", status_code=202)
    def queue_run(
        payload: LimitMonitoringRunCreateIn, session: Session = Depends(db_dependency)
    ):
        try:
            run, task = monitoring.queue_limit_monitoring(
                session,
                portfolio_id=payload.portfolio_id,
                trigger="manual",
                context=_context(),
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                engine_config_id=payload.engine_config_id,
                market_snapshot_id=payload.market_snapshot_id,
                effective_market_evidence_id=payload.effective_market_evidence_id,
                valuation_as_of=payload.valuation_as_of,
                source_policy=payload.source_policy,
                max_source_age_seconds=payload.max_source_age_seconds,
                source_inputs=payload.source_inputs,
            )
            # The worker is only permitted to see a durable immutable envelope.
            session.commit()
            dispatch(task.id, run.id)
            return _run_out(run)
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)

    @router.get("/limit-monitoring/runs")
    def list_runs(
        portfolio_id: int,
        status: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(db_dependency),
    ):
        _require_portfolio(session, portfolio_id)
        statement = select(LimitMonitoringRun).where(
            LimitMonitoringRun.portfolio_id == portfolio_id
        )
        if status is not None:
            statement = statement.where(LimitMonitoringRun.status == status)
        rows = list(
            session.scalars(
                statement.options(
                    selectinload(LimitMonitoringRun.task_runs),
                    selectinload(LimitMonitoringRun.version_links),
                    selectinload(LimitMonitoringRun.source_references),
                ).order_by(
                    LimitMonitoringRun.created_at.desc(), LimitMonitoringRun.id.desc()
                )
            )
        )
        return {
            "items": [_run_out(row) for row in rows[offset : offset + limit]],
            "total": len(rows),
        }

    @router.get("/limit-monitoring/runs/{run_id}")
    def get_run(
        run_id: int, portfolio_id: int, session: Session = Depends(db_dependency)
    ):
        return _run_out(_run_with_portfolio(session, run_id, portfolio_id))

    @router.get("/limit-monitoring/runs/{run_id}/evaluations")
    def list_evaluations(
        run_id: int,
        portfolio_id: int,
        status: str | None = None,
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(db_dependency),
    ):
        _run_with_portfolio(session, run_id, portfolio_id)
        statement = select(LimitEvaluation).where(
            LimitEvaluation.monitoring_run_id == run_id
        )
        if status is not None:
            statement = statement.where(LimitEvaluation.status == status)
        rows = list(session.scalars(statement.order_by(LimitEvaluation.id)))
        return {
            "items": [
                LimitEvaluationOut.model_validate(row).model_dump()
                for row in rows[offset : offset + limit]
            ],
            "total": len(rows),
        }

    @router.get("/limit-incidents")
    def list_incidents(
        portfolio_id: int,
        status: str | None = None,
        severity: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(db_dependency),
    ):
        _require_portfolio(session, portfolio_id)
        statement = (
            select(LimitIncident)
            .where(LimitIncident.portfolio_id == portfolio_id)
            .options(
                selectinload(LimitIncident.events),
                selectinload(LimitIncident.risk_limit).selectinload(RiskLimit.versions),
            )
        )
        if status is not None:
            statement = statement.where(LimitIncident.status == status)
        if severity is not None:
            statement = statement.where(LimitIncident.severity == severity)
        rows = list(
            session.scalars(
                statement.order_by(
                    LimitIncident.last_seen_at.desc(), LimitIncident.id.desc()
                )
            )
        )
        return {
            "items": [
                _incident_out(row, portfolio_id=portfolio_id)
                for row in rows[offset : offset + limit]
            ],
            "total": len(rows),
        }

    @router.get("/limit-incidents/{incident_id}")
    def get_incident(
        incident_id: int, portfolio_id: int, session: Session = Depends(db_dependency)
    ):
        return _incident_out(
            _incident_with_portfolio(session, incident_id, portfolio_id),
            portfolio_id=portfolio_id,
        )

    def _apply_incident_action(
        session: Session,
        *,
        incident_id: int,
        portfolio_id: int,
        payload: LimitActionIn,
        action: Callable[..., LimitIncident],
        **extra: Any,
    ) -> dict[str, Any]:
        _incident_with_portfolio(session, incident_id, portfolio_id)
        try:
            row = action(
                session,
                incident_id=incident_id,
                expected_row_version=payload.expected_row_version,
                context=_context(),
                **extra,
            )
            session.commit()
            # The incident was loaded for the portfolio guard before the
            # service appended its immutable event; expire eager collections so
            # the response reflects the just-committed ledger row.
            session.expire_all()
            return _incident_out(
                _incident_with_portfolio(session, row.id, portfolio_id),
                portfolio_id=portfolio_id,
            )
        except Exception as exc:
            session.rollback()
            _raise_domain_error(exc)

    @router.post("/limit-incidents/{incident_id}/acknowledge")
    def acknowledge_incident(
        incident_id: int,
        portfolio_id: int,
        payload: LimitActionIn,
        session: Session = Depends(db_dependency),
    ):
        return _apply_incident_action(
            session,
            incident_id=incident_id,
            portfolio_id=portfolio_id,
            payload=payload,
            action=incidents.acknowledge,
        )

    @router.post("/limit-incidents/{incident_id}/assign")
    def assign_incident(
        incident_id: int,
        portfolio_id: int,
        payload: LimitIncidentAssignIn,
        session: Session = Depends(db_dependency),
    ):
        return _apply_incident_action(
            session,
            incident_id=incident_id,
            portfolio_id=portfolio_id,
            payload=payload,
            action=incidents.assign,
            assignee=payload.assignee,
        )

    @router.post("/limit-incidents/{incident_id}/comments")
    def comment_incident(
        incident_id: int,
        portfolio_id: int,
        payload: LimitIncidentCommentIn,
        session: Session = Depends(db_dependency),
    ):
        return _apply_incident_action(
            session,
            incident_id=incident_id,
            portfolio_id=portfolio_id,
            payload=payload,
            action=incidents.comment,
            comment=payload.comment,
        )

    @router.post("/limit-incidents/{incident_id}/waive")
    def waive_incident(
        incident_id: int,
        portfolio_id: int,
        payload: LimitIncidentWaiveIn,
        session: Session = Depends(db_dependency),
    ):
        return _apply_incident_action(
            session,
            incident_id=incident_id,
            portfolio_id=portfolio_id,
            payload=payload,
            action=incidents.waive,
            rationale=payload.rationale,
            expires_at=payload.expires_at,
        )

    @router.post("/limit-incidents/{incident_id}/resolve")
    def resolve_incident(
        incident_id: int,
        portfolio_id: int,
        payload: LimitActionIn,
        session: Session = Depends(db_dependency),
    ):
        return _apply_incident_action(
            session,
            incident_id=incident_id,
            portfolio_id=portfolio_id,
            payload=payload,
            action=incidents.resolve,
        )

    @router.post("/limit-incidents/{incident_id}/reopen")
    def reopen_incident(
        incident_id: int,
        portfolio_id: int,
        payload: LimitActionIn,
        session: Session = Depends(db_dependency),
    ):
        return _apply_incident_action(
            session,
            incident_id=incident_id,
            portfolio_id=portfolio_id,
            payload=payload,
            action=incidents.reopen,
        )

    @router.get("/limit-monitoring/dashboard")
    def dashboard(
        portfolio_id: int,
        trend_limit: int = Query(20, ge=1, le=100),
        session: Session = Depends(db_dependency),
    ):
        _require_portfolio(session, portfolio_id)
        runs = list(
            session.scalars(
                select(LimitMonitoringRun)
                .where(LimitMonitoringRun.portfolio_id == portfolio_id)
                .options(
                    selectinload(LimitMonitoringRun.task_runs),
                    selectinload(LimitMonitoringRun.version_links),
                    selectinload(LimitMonitoringRun.source_references),
                )
                .order_by(
                    LimitMonitoringRun.created_at.desc(),
                    LimitMonitoringRun.id.desc(),
                )
            )
        )
        latest_terminal = max(
            (
                run
                for run in runs
                if run.status in {"completed", "completed_with_unknowns"}
            ),
            key=lambda run: (run.valuation_as_of, run.id),
            default=None,
        )
        current = (
            list(
                session.scalars(
                    select(LimitEvaluation)
                    .where(LimitEvaluation.monitoring_run_id == latest_terminal.id)
                    .options(
                        selectinload(LimitEvaluation.limit_version).selectinload(
                            RiskLimitVersion.risk_limit
                        )
                    )
                    .order_by(LimitEvaluation.id)
                )
            )
            if latest_terminal is not None
            else []
        )
        active_incidents = list(
            session.scalars(
                select(LimitIncident)
                .where(
                    LimitIncident.portfolio_id == portfolio_id,
                    LimitIncident.status.in_(
                        ("open", "acknowledged", "assigned", "waived")
                    ),
                )
                .options(
                    selectinload(LimitIncident.events),
                    selectinload(LimitIncident.risk_limit).selectinload(
                        RiskLimit.versions
                    ),
                )
                .order_by(LimitIncident.last_seen_at.desc(), LimitIncident.id.desc())
            )
        )
        breaches = sum(row.status == "breach" for row in current)
        warnings = sum(row.status == "warning" for row in current)
        unknowns = sum(row.status == "unknown" for row in current)
        utilizations = [
            row.utilization for row in current if row.utilization is not None
        ]
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in current:
            category = row.limit_version.risk_limit.category
            groups.setdefault(category, []).append(
                LimitEvaluationOut.model_validate(row).model_dump()
            )
        return {
            "summary": {
                "breaches": breaches,
                "warnings": warnings,
                "unknowns": unknowns,
                "ok": sum(row.status == "ok" for row in current),
                "highest_utilization": max(utilizations) if utilizations else None,
                "active_incidents": len(active_incidents),
            },
            "current_evaluations": [
                LimitEvaluationOut.model_validate(row).model_dump() for row in current
            ],
            "evaluation_groups": [
                {"category": category, "evaluations": rows}
                for category, rows in sorted(groups.items())
            ],
            "latest_run": _run_out(runs[0]) if runs else None,
            "active_incidents": [
                _incident_out(row, portfolio_id=portfolio_id)
                for row in active_incidents
            ],
            "trends": [
                {
                    "run_id": row.id,
                    "created_at": row.created_at,
                    "status": row.status,
                    "summary": row.summary or {},
                }
                for row in reversed(runs[:trend_limit])
            ],
        }

    @router.get("/limit-monitoring/summary")
    def summary(portfolio_id: int, session: Session = Depends(db_dependency)):
        result = dashboard(portfolio_id=portfolio_id, trend_limit=1, session=session)
        from app.models import LimitIncidentEvent

        latest_event_id = session.scalar(
            select(LimitIncidentEvent.id)
            .join(LimitIncident, LimitIncidentEvent.incident_id == LimitIncident.id)
            .where(LimitIncident.portfolio_id == portfolio_id)
            .order_by(LimitIncidentEvent.id.desc())
            .limit(1)
        )
        return {
            **result["summary"],
            "latest_run": result["latest_run"],
            "latest_incident_event_id": latest_event_id or 0,
        }

    @router.get("/market-data/snapshots", response_model=list[MarketSnapshotOut])
    def list_market_snapshots(
        source: str | None = None,
        as_of: datetime | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
        session: Session = Depends(db_dependency),
    ):
        statement = select(MarketSnapshot)
        if source is not None:
            statement = statement.where(MarketSnapshot.source == source)
        if as_of is not None:
            statement = statement.where(MarketSnapshot.valuation_date <= as_of)
        return list(
            session.scalars(
                statement.order_by(
                    MarketSnapshot.valuation_date.desc(),
                    MarketSnapshot.created_at.desc(),
                    MarketSnapshot.id.desc(),
                )
                .offset(offset)
                .limit(limit)
            )
        )

    return router
