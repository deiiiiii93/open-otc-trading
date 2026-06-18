"""Reporting domain service.

Facade over ``services/reports.py`` and the ``ReportJob`` ORM model. Provides:

* ``list_reports`` — filtered listing of ReportJob rows
* ``get_report`` — single row by id
* ``create_report`` — queue + audit + dispatch to async runner
* ``run_batch`` — pure helper that builds an ad-hoc batch payload (no DB)

The ``portfolio_id`` filter is JSON-side because reports store it inside
``request_payload`` (not as a top-level column). This module preserves the
streaming/limit handling the legacy ``list_reports_tool`` used so the SQL
LIMIT does not silently hide older portfolio-matching rows.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from sqlalchemy.orm import Session

from app import database
from app.models import ReportJob
from app.schemas import ReportJobCreate
from app.services.audit import record_audit
from app.services.reports import execute_report_job_task, queue_report_job
from app.services.task_runner import submit_async_task


ReportType = Literal["portfolio", "risk", "rfq"]
ReportStatus = Literal[
    "queued", "running", "completed", "completed_with_errors", "failed"
]


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def list_reports(
    *,
    portfolio_id: int | None = None,
    report_type: ReportType | None = None,
    status: ReportStatus | None = None,
    limit: int = 20,
    session: Session | None = None,
) -> list[ReportJob]:
    """Return ReportJob rows matching the filter, newest-first.

    ``portfolio_id`` is stored inside ``request_payload`` (JSON), so this
    filter is applied in Python after streaming. To avoid hiding older
    matching rows behind a SQL-side LIMIT of recent unrelated rows, the
    SQL LIMIT is only applied when ``portfolio_id is None``. Otherwise we
    use ``yield_per`` and stop once enough matches accumulate.
    """
    capped_limit = max(1, min(limit, 100))
    with _session_scope(session) as sess:
        query = sess.query(ReportJob)
        if report_type is not None:
            query = query.filter(ReportJob.report_type == report_type)
        if status is not None:
            query = query.filter(ReportJob.status == status)
        query = query.order_by(ReportJob.created_at.desc(), ReportJob.id.desc())
        if portfolio_id is None:
            return list(query.limit(capped_limit).all())
        rows: list[ReportJob] = []
        for job in query.yield_per(100):
            payload = job.request_payload or {}
            if payload.get("portfolio_id") == portfolio_id:
                rows.append(job)
                if len(rows) >= capped_limit:
                    break
        return rows


def get_report(
    *,
    report_id: int,
    session: Session | None = None,
) -> ReportJob | None:
    """Return a single ReportJob by id, or None if missing."""
    with _session_scope(session) as sess:
        return sess.get(ReportJob, report_id)


def create_report(
    *,
    portfolio_id: int,
    report_type: ReportType = "portfolio",
    title: str = "Agent Generated Desk Report",
    pricing_profile_id: int | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Queue a persisted ReportJob and dispatch its async execution.

    The audit event is recorded and the session committed BEFORE the
    background task is submitted; this matches the legacy
    ``create_report_tool`` ordering and ensures the task can re-read the
    just-committed job rows.
    """
    if report_type not in ("portfolio", "risk", "rfq"):
        raise ValueError(f"unsupported report_type: {report_type}")
    with _session_scope(session) as sess:
        request = ReportJobCreate(
            portfolio_id=portfolio_id,
            report_type=report_type,
            pricing_parameter_profile_id=pricing_profile_id,
            title=title,
        )
        settings = database.settings
        job, task = queue_report_job(sess, request)
        record_audit(
            sess,
            event_type="report.queued",
            actor="desk_user",
            subject_type="report",
            subject_id=job.id,
            payload=request.model_dump(mode="json")
            | {"source": "agent_confirmed", "task_id": task.id},
        )
        sess.commit()
        submit_async_task(
            execute_report_job_task,
            task.id,
            job.id,
            settings,
            database.SessionLocal,
            settings=settings,
        )
        return {
            "report_job_id": job.id,
            "task_id": task.id,
            "pricing_parameter_profile_id": pricing_profile_id,
            "status": task.status,
            "message": "Report queued. Use the Tasks page or /api/tasks/{task_id} to monitor completion.",
        }


def run_batch(
    *,
    title: str,
    report_type: str,
    risk_summary: dict[str, Any],
) -> dict[str, Any]:
    """Build an ad-hoc report-batch envelope from a pre-computed risk summary.

    The legacy ``run_report_batch_tool`` invoked the risk calculator inline
    before shaping. The pure helper here takes the already-computed risk
    summary so the tool layer (and tests) can wire it however they like.
    No database access.
    """
    return {
        "title": title,
        "report_type": report_type,
        "status": "ready",
        "summary": risk_summary,
        "artifact_hint": str(Path("artifacts") / "{task_name}_{timestamp}.{html,xlsx}"),
    }


__all__ = [
    "ReportType",
    "ReportStatus",
    "list_reports",
    "get_report",
    "create_report",
    "run_batch",
]
