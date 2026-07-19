"""Persistent, optimistic-concurrency-safe risk-breach incident episodes.

The monitoring service owns evaluation persistence.  It calls
``reconcile_monitoring_incidents`` after flushing its final evaluation batch,
inside the same transaction, to project those immutable observations onto
incident episodes.  ``unknown`` observations are intentionally excluded: they
remain data-quality results and can never manufacture a risk-breach episode.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models import (
    LimitEvaluation,
    LimitIncident,
    LimitIncidentEvent,
    LimitMonitoringRun,
    RiskLimit,
    RiskLimitVersion,
    utcnow,
)
from ..audit import record_audit
from .contracts import LimitActionContext
from .errors import LimitConflictError, LimitNotFoundError, LimitValidationError


_ACTIVE_STATUSES = frozenset({"open", "acknowledged", "assigned", "waived"})
_TERMINAL_STATUSES = frozenset({"recovered", "resolved"})
_SEVERITY_RANK = {"warning": 1, "breach": 2}


@dataclass(frozen=True, slots=True)
class IncidentReconciliation:
    """The episode projections and immutable event rows touched by one batch."""

    incidents: tuple[LimitIncident, ...]
    events: tuple[LimitIncidentEvent, ...]


def _when(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    return _utc_naive(value, "occurred_at")


def _utc_naive(value: datetime, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise LimitValidationError(f"{field} must be a datetime")
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.replace(tzinfo=None)


def _context(context: LimitActionContext) -> None:
    if not isinstance(context, LimitActionContext):
        raise LimitValidationError("context must be LimitActionContext")
    if not isinstance(context.actor, str) or not context.actor.strip():
        raise LimitValidationError("context.actor must be a non-empty string")
    if context.persona is not None and (
        not isinstance(context.persona, str) or not context.persona.strip()
    ):
        raise LimitValidationError("context.persona must be non-empty when present")
    if context.mode not in {"interactive", "auto", "yolo"}:
        raise LimitValidationError("context.mode is invalid")
    if context.thread_id is not None and (
        isinstance(context.thread_id, bool)
        or not isinstance(context.thread_id, int)
        or context.thread_id <= 0
    ):
        raise LimitValidationError("context.thread_id must be a positive integer")


def _event(
    session: Session,
    *,
    incident: LimitIncident,
    event_type: str,
    context: LimitActionContext,
    occurred_at: datetime,
    evaluation: LimitEvaluation | None = None,
    payload: dict | None = None,
) -> LimitIncidentEvent:
    event = LimitIncidentEvent(
        incident_id=incident.id,
        event_type=event_type,
        evaluation_id=evaluation.id if evaluation is not None else None,
        actor=context.actor.strip(),
        persona=context.persona.strip() if context.persona else None,
        mode=context.mode,
        thread_id=context.thread_id,
        audit_ref=context.audit_ref,
        payload=deepcopy(payload or {}),
        created_at=occurred_at,
    )
    session.add(event)
    session.flush()
    record_audit(
        session,
        event_type=f"limit_incident.{event_type}",
        actor=context.actor.strip(),
        subject_type="limit_incident",
        subject_id=incident.id,
        payload={
            "event_id": event.id,
            "evaluation_id": event.evaluation_id,
            "persona": context.persona,
            "mode": context.mode,
            "thread_id": context.thread_id,
            "audit_ref": context.audit_ref,
            **deepcopy(payload or {}),
        },
    )
    return event


def _active_incident(
    session: Session,
    *,
    portfolio_id: int,
    risk_limit_id: int,
    scope_key: str,
) -> LimitIncident | None:
    return session.scalar(
        select(LimitIncident)
        .where(
            LimitIncident.portfolio_id == portfolio_id,
            LimitIncident.risk_limit_id == risk_limit_id,
            LimitIncident.scope_key == scope_key,
            LimitIncident.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(LimitIncident.id.desc())
    )


def _risk_limit(session: Session, evaluation: LimitEvaluation) -> RiskLimit:
    version = session.get(RiskLimitVersion, evaluation.limit_version_id)
    if version is None:
        raise LimitNotFoundError(
            f"risk limit version {evaluation.limit_version_id} was not found"
        )
    risk_limit = session.get(RiskLimit, version.risk_limit_id)
    if risk_limit is None:
        raise LimitNotFoundError(f"risk limit {version.risk_limit_id} was not found")
    return risk_limit


def _evaluation_order_key(
    session: Session,
    evaluation: LimitEvaluation,
) -> tuple[datetime, datetime, int]:
    monitoring_run = session.get(
        LimitMonitoringRun,
        evaluation.monitoring_run_id,
    )
    if monitoring_run is None:
        raise LimitNotFoundError(
            f"limit monitoring run {evaluation.monitoring_run_id} was not found"
        )
    if evaluation.id is None:
        raise LimitValidationError("evaluation must be persisted")
    return (
        _utc_naive(monitoring_run.valuation_as_of, "valuation_as_of"),
        _utc_naive(evaluation.evaluated_at, "evaluated_at"),
        evaluation.id,
    )


def _latest_incident_by_evidence(
    session: Session,
    *,
    portfolio_id: int,
    risk_limit_id: int,
    scope_key: str,
) -> LimitIncident | None:
    return session.scalar(
        select(LimitIncident)
        .join(
            LimitEvaluation,
            LimitEvaluation.id == LimitIncident.last_evaluation_id,
        )
        .join(
            LimitMonitoringRun,
            LimitMonitoringRun.id == LimitEvaluation.monitoring_run_id,
        )
        .where(
            LimitIncident.portfolio_id == portfolio_id,
            LimitIncident.risk_limit_id == risk_limit_id,
            LimitIncident.scope_key == scope_key,
        )
        .order_by(
            LimitMonitoringRun.valuation_as_of.desc(),
            LimitEvaluation.evaluated_at.desc(),
            LimitEvaluation.id.desc(),
        )
        .limit(1)
    )


def _is_newer_evaluation(
    session: Session,
    *,
    candidate: LimitEvaluation,
    prior: LimitEvaluation,
) -> bool:
    return _evaluation_order_key(
        session,
        candidate,
    ) > _evaluation_order_key(session, prior)


def _update(
    session: Session,
    *,
    incident: LimitIncident,
    expected_row_version: int | None,
    values: dict,
) -> LimitIncident:
    expected = (
        incident.row_version if expected_row_version is None else expected_row_version
    )
    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
        raise LimitValidationError("expected_row_version must be a positive integer")
    result = session.execute(
        update(LimitIncident)
        .where(
            LimitIncident.id == incident.id,
            LimitIncident.row_version == expected,
        )
        .values(
            **values,
            row_version=LimitIncident.row_version + 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise LimitConflictError(f"limit incident {incident.id} row version is stale")
    session.flush()
    session.refresh(incident)
    return incident


def _create_open_episode(
    session: Session,
    *,
    portfolio_id: int,
    risk_limit: RiskLimit,
    evaluation: LimitEvaluation,
    context: LimitActionContext,
    occurred_at: datetime,
) -> LimitIncident | None:
    """Create an episode, letting the partial unique index win concurrent races."""
    incident = LimitIncident(
        portfolio_id=portfolio_id,
        risk_limit_id=risk_limit.id,
        scope_type=evaluation.scope_type,
        scope_key=evaluation.scope_key,
        scope_label=evaluation.scope_label,
        severity=evaluation.status,
        status="open",
        first_evaluation_id=evaluation.id,
        last_evaluation_id=evaluation.id,
        first_seen_at=occurred_at,
        last_seen_at=occurred_at,
        owner=risk_limit.owner,
    )
    try:
        with session.begin_nested():
            session.add(incident)
            session.flush()
    except IntegrityError:
        return None
    _event(
        session,
        incident=incident,
        event_type="opened",
        context=context,
        occurred_at=occurred_at,
        evaluation=evaluation,
        payload={
            "severity": evaluation.status,
            "utilization": evaluation.utilization,
            "scope_type": evaluation.scope_type,
            "scope_key": evaluation.scope_key,
        },
    )
    return incident


def _expire_waiver_if_due(
    session: Session,
    *,
    incident: LimitIncident,
    context: LimitActionContext,
    occurred_at: datetime,
) -> LimitIncidentEvent | None:
    expires_at = incident.waiver_expires_at
    if incident.status != "waived" or expires_at is None or expires_at > occurred_at:
        return None
    _update(
        session,
        incident=incident,
        expected_row_version=None,
        values={"status": "open"},
    )
    return _event(
        session,
        incident=incident,
        event_type="waiver_expired",
        context=context,
        occurred_at=occurred_at,
        payload={"waiver_expires_at": expires_at.isoformat()},
    )


def _reconcile_active(
    session: Session,
    *,
    incident: LimitIncident,
    evaluation: LimitEvaluation,
    context: LimitActionContext,
    occurred_at: datetime,
) -> tuple[LimitIncident, tuple[LimitIncidentEvent, ...]]:
    if incident.last_evaluation_id == evaluation.id:
        return incident, ()
    previous_evaluation = session.get(
        LimitEvaluation,
        incident.last_evaluation_id,
    )
    if previous_evaluation is None:
        raise LimitNotFoundError(
            f"limit evaluation {incident.last_evaluation_id} was not found"
        )
    if not _is_newer_evaluation(
        session,
        candidate=evaluation,
        prior=previous_evaluation,
    ):
        return incident, ()
    events: list[LimitIncidentEvent] = []
    expired = _expire_waiver_if_due(
        session,
        incident=incident,
        context=context,
        occurred_at=occurred_at,
    )
    if expired is not None:
        events.append(expired)
    if evaluation.status == "ok":
        _update(
            session,
            incident=incident,
            expected_row_version=None,
            values={
                "status": "recovered",
                "last_evaluation_id": evaluation.id,
                "last_seen_at": occurred_at,
                "resolved_at": occurred_at,
            },
        )
        events.append(
            _event(
                session,
                incident=incident,
                event_type="recovered",
                context=context,
                occurred_at=occurred_at,
                evaluation=evaluation,
                payload={
                    "severity": incident.severity,
                    "utilization": evaluation.utilization,
                },
            )
        )
        return incident, tuple(events)

    previous_severity = incident.severity
    previous_utilization = (
        previous_evaluation.utilization if previous_evaluation is not None else None
    )
    event_type = (
        "escalated"
        if (
            _SEVERITY_RANK[evaluation.status] > _SEVERITY_RANK.get(previous_severity, 0)
            or (
                evaluation.utilization is not None
                and previous_utilization is not None
                and evaluation.utilization > previous_utilization
            )
        )
        else "repeated"
    )
    _update(
        session,
        incident=incident,
        expected_row_version=None,
        values={
            "severity": evaluation.status,
            "last_evaluation_id": evaluation.id,
            "last_seen_at": occurred_at,
        },
    )
    events.append(
        _event(
            session,
            incident=incident,
            event_type=event_type,
            context=context,
            occurred_at=occurred_at,
            evaluation=evaluation,
            payload={
                "previous_severity": previous_severity,
                "previous_utilization": previous_utilization,
                "severity": evaluation.status,
                "utilization": evaluation.utilization,
            },
        )
    )
    return incident, tuple(events)


def reconcile_monitoring_incidents(
    session: Session,
    *,
    monitoring_run: LimitMonitoringRun,
    evaluations: Iterable[LimitEvaluation],
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> IncidentReconciliation:
    """Project persisted monitoring evaluations into immutable incident episodes.

    This is the narrow final-transaction seam for ``monitoring.py``.  Callers
    must flush evaluations first; this function neither creates evaluations nor
    commits the caller's transaction.
    """
    _context(context)
    if not isinstance(monitoring_run, LimitMonitoringRun) or monitoring_run.id is None:
        raise LimitValidationError("monitoring_run must be persisted")
    when = _when(occurred_at)
    touched: list[LimitIncident] = []
    events: list[LimitIncidentEvent] = []
    seen_incidents: set[int] = set()

    for evaluation in evaluations:
        if not isinstance(evaluation, LimitEvaluation) or evaluation.id is None:
            raise LimitValidationError(
                "evaluations must be persisted LimitEvaluation rows"
            )
        if evaluation.monitoring_run_id != monitoring_run.id:
            raise LimitValidationError("evaluation does not belong to monitoring_run")
        if evaluation.status == "unknown":
            continue
        if evaluation.status not in {"ok", "warning", "breach"}:
            raise LimitValidationError("evaluation status is not reconcilable")
        risk_limit = _risk_limit(session, evaluation)
        incident = _active_incident(
            session,
            portfolio_id=monitoring_run.portfolio_id,
            risk_limit_id=risk_limit.id,
            scope_key=evaluation.scope_key,
        )
        emitted: tuple[LimitIncidentEvent, ...] = ()
        if incident is None:
            if evaluation.status == "ok":
                continue
            latest = _latest_incident_by_evidence(
                session,
                portfolio_id=monitoring_run.portfolio_id,
                risk_limit_id=risk_limit.id,
                scope_key=evaluation.scope_key,
            )
            if latest is not None:
                latest_evaluation = session.get(
                    LimitEvaluation,
                    latest.last_evaluation_id,
                )
                if latest_evaluation is None:
                    raise LimitNotFoundError(
                        f"limit evaluation {latest.last_evaluation_id} was not found"
                    )
                if not _is_newer_evaluation(
                    session,
                    candidate=evaluation,
                    prior=latest_evaluation,
                ):
                    continue
            incident = _create_open_episode(
                session,
                portfolio_id=monitoring_run.portfolio_id,
                risk_limit=risk_limit,
                evaluation=evaluation,
                context=context,
                occurred_at=when,
            )
            if incident is None:
                incident = _active_incident(
                    session,
                    portfolio_id=monitoring_run.portfolio_id,
                    risk_limit_id=risk_limit.id,
                    scope_key=evaluation.scope_key,
                )
                if incident is None:
                    raise LimitConflictError(
                        "active incident could not be recovered after insert race"
                    )
                incident, emitted = _reconcile_active(
                    session,
                    incident=incident,
                    evaluation=evaluation,
                    context=context,
                    occurred_at=when,
                )
            else:
                emitted = (incident.events[-1],)
        else:
            incident, emitted = _reconcile_active(
                session,
                incident=incident,
                evaluation=evaluation,
                context=context,
                occurred_at=when,
            )
        if incident.id not in seen_incidents:
            touched.append(incident)
            seen_incidents.add(incident.id)
        events.extend(emitted)
    return IncidentReconciliation(tuple(touched), tuple(events))


def _incident(session: Session, incident_id: int) -> LimitIncident:
    incident = session.get(LimitIncident, incident_id)
    if incident is None:
        raise LimitNotFoundError(f"limit incident {incident_id} was not found")
    return incident


def _active_action_incident(session: Session, incident_id: int) -> LimitIncident:
    incident = _incident(session, incident_id)
    if incident.status not in _ACTIVE_STATUSES:
        raise LimitConflictError(f"limit incident {incident.id} is not active")
    return incident


def acknowledge(
    session: Session,
    *,
    incident_id: int,
    expected_row_version: int,
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> LimitIncident:
    _context(context)
    when = _when(occurred_at)
    incident = _active_action_incident(session, incident_id)
    _update(
        session,
        incident=incident,
        expected_row_version=expected_row_version,
        values={"status": "acknowledged", "acknowledged_at": when},
    )
    _event(
        session,
        incident=incident,
        event_type="acknowledged",
        context=context,
        occurred_at=when,
    )
    return incident


def assign(
    session: Session,
    *,
    incident_id: int,
    assignee: str,
    expected_row_version: int,
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> LimitIncident:
    _context(context)
    if not isinstance(assignee, str) or not assignee.strip():
        raise LimitValidationError("assignee must be a non-empty string")
    when = _when(occurred_at)
    incident = _active_action_incident(session, incident_id)
    _update(
        session,
        incident=incident,
        expected_row_version=expected_row_version,
        values={
            "status": incident.status if incident.status == "waived" else "assigned",
            "assignee": assignee.strip(),
        },
    )
    _event(
        session,
        incident=incident,
        event_type="assigned",
        context=context,
        occurred_at=when,
        payload={"assignee": assignee.strip()},
    )
    return incident


def comment(
    session: Session,
    *,
    incident_id: int,
    comment: str,
    expected_row_version: int,
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> LimitIncident:
    _context(context)
    if not isinstance(comment, str) or not comment.strip():
        raise LimitValidationError("comment must be a non-empty string")
    when = _when(occurred_at)
    incident = _incident(session, incident_id)
    _update(
        session,
        incident=incident,
        expected_row_version=expected_row_version,
        values={},
    )
    _event(
        session,
        incident=incident,
        event_type="commented",
        context=context,
        occurred_at=when,
        payload={"comment": comment.strip()},
    )
    return incident


def waive(
    session: Session,
    *,
    incident_id: int,
    rationale: str,
    expires_at: datetime,
    expected_row_version: int,
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> LimitIncident:
    _context(context)
    if not isinstance(rationale, str) or not rationale.strip():
        raise LimitValidationError("rationale must be a non-empty string")
    when = _when(occurred_at)
    expiry = _when(expires_at)
    if expiry <= when:
        raise LimitValidationError("expires_at must be after occurred_at")
    incident = _active_action_incident(session, incident_id)
    _update(
        session,
        incident=incident,
        expected_row_version=expected_row_version,
        values={
            "status": "waived",
            "waived_at": when,
            "waiver_expires_at": expiry,
            "waiver_rationale": rationale.strip(),
        },
    )
    _event(
        session,
        incident=incident,
        event_type="waived",
        context=context,
        occurred_at=when,
        payload={
            "rationale": rationale.strip(),
            "waiver_expires_at": expiry.isoformat(),
        },
    )
    return incident


def resolve(
    session: Session,
    *,
    incident_id: int,
    expected_row_version: int,
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> LimitIncident:
    _context(context)
    when = _when(occurred_at)
    incident = _active_action_incident(session, incident_id)
    _update(
        session,
        incident=incident,
        expected_row_version=expected_row_version,
        values={"status": "resolved", "resolved_at": when},
    )
    _event(
        session,
        incident=incident,
        event_type="resolved",
        context=context,
        occurred_at=when,
    )
    return incident


def reopen(
    session: Session,
    *,
    incident_id: int,
    expected_row_version: int,
    context: LimitActionContext,
    occurred_at: datetime | None = None,
) -> LimitIncident:
    _context(context)
    when = _when(occurred_at)
    incident = _incident(session, incident_id)
    if incident.status not in _TERMINAL_STATUSES:
        raise LimitConflictError(
            f"limit incident {incident.id} is not resolved or recovered"
        )
    active = _active_incident(
        session,
        portfolio_id=incident.portfolio_id,
        risk_limit_id=incident.risk_limit_id,
        scope_key=incident.scope_key,
    )
    if active is not None and active.id != incident.id:
        raise LimitConflictError(
            "another active incident already exists for this limit and scope"
        )
    _update(
        session,
        incident=incident,
        expected_row_version=expected_row_version,
        values={"status": "open", "resolved_at": None},
    )
    _event(
        session,
        incident=incident,
        event_type="reopened",
        context=context,
        occurred_at=when,
    )
    return incident
