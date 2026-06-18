from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AuditEvent


def record_audit(
    session: Session,
    *,
    event_type: str,
    actor: str,
    subject_type: str,
    subject_id: str | int,
    payload: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        actor=actor,
        subject_type=subject_type,
        subject_id=str(subject_id),
        payload=payload or {},
    )
    session.add(event)
    session.flush()
    return event
