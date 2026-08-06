"""Queued report-job execution.

The hardcoded HTML/XLSX writer that used to live here is deleted. A report is
now a ``ReportDocument`` produced by ``services/reporting`` from a declarative
template, so there is exactly one report writer in the codebase.
"""
from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import Settings
from ..models import (
    ReportJob,
    ReportStatus,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from ..schemas import ReportJobCreate
from .task_runner import mark_task_finished, mark_task_running, update_task_progress

# The compatibility template the retained `create_report` path generates.
DEFAULT_TEMPLATE_SLUG = "portfolio-snapshot"


def create_report_job(
    session: Session, settings: Settings, request: ReportJobCreate
) -> ReportJob:
    settings.artifact_dir.mkdir(parents=True, exist_ok=True)
    job = ReportJob(
        report_type=request.report_type,
        status=ReportStatus.RUNNING.value,
        request_payload=request.model_dump(mode="json"),
        result_payload={},
        artifact_paths={},
    )
    session.add(job)
    session.flush()
    _complete_report_job(session, settings, job, request)
    return job


def queue_report_job(
    session: Session,
    request: ReportJobCreate,
) -> tuple[ReportJob, TaskRun]:
    job = ReportJob(
        report_type=request.report_type,
        status=ReportStatus.QUEUED.value,
        request_payload=request.model_dump(mode="json"),
        result_payload={},
        artifact_paths={},
    )
    session.add(job)
    session.flush()
    task = TaskRun(
        kind=TaskKind.REPORT_JOB.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=request.portfolio_id,
        report_job_id=job.id,
        progress_current=0,
        progress_total=3,
        message="Queued report job",
    )
    session.add(task)
    session.flush()
    return job, task


def execute_report_job_task(
    task_id: int,
    report_job_id: int,
    settings: Settings,
    session_factory: sessionmaker | None = None,
) -> None:
    session = (session_factory or database.SessionLocal)()
    try:
        _execute_report_job_task(session, task_id, report_job_id, settings)
    finally:
        session.close()


def _execute_report_job_task(
    session: Session,
    task_id: int,
    report_job_id: int,
    settings: Settings,
) -> None:
    try:
        job = session.get(ReportJob, report_job_id)
        if job is None:
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                error=f"Report job not found: {report_job_id}",
            )
            session.commit()
            return
        request = ReportJobCreate.model_validate(job.request_payload)
        mark_task_running(session, task_id, message="Building report payload", total=3)
        session.commit()
        _complete_report_job(
            session,
            settings,
            job,
            request,
            task_id=task_id,
        )
        status = job.status
        mark_task_finished(
            session,
            task_id,
            status=status,
            message=(
                "Report generated"
                if status == TaskStatus.COMPLETED.value
                else "Report generated with issues"
            ),
        )
        session.commit()
    except Exception as exc:
        session.rollback()
        job = session.get(ReportJob, report_job_id)
        if job is not None:
            job.status = ReportStatus.FAILED.value
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.FAILED.value,
            message="Report generation failed",
            error=str(exc),
        )
        session.commit()


def _complete_report_job(
    session: Session,
    settings: Settings,
    job: ReportJob,
    request: ReportJobCreate,
    *,
    task_id: int | None = None,
) -> None:
    """Fill a queued ReportJob by generating its templated document.

    The legacy hardcoded HTML/XLSX writer is gone; there is ONE report writer.
    ``narrate`` is deliberately omitted: this queued path generates the
    deterministic ``portfolio-snapshot`` template, which declares no narrative
    sections, so it completes with zero LLM calls.
    """
    from .reporting.generate import _status_from_document, generate_document

    if task_id is not None:
        update_task_progress(
            session, task_id, current=0, total=2, message="Resolving report blocks"
        )

    job.status = ReportStatus.RUNNING.value
    # Commit the RUNNING marker BEFORE generating. Every block producer's
    # _session_scope calls database.init_db(), which issues create_all + schema
    # DDL; holding an open write transaction across that deadlocks SQLite in a
    # worker thread and the task hangs until its poll timeout. Committing here
    # also makes RUNNING visible to anyone polling the task.
    session.commit()

    template_slug = (
        job.template_slug
        or (job.request_payload or {}).get("template_slug")
        or DEFAULT_TEMPLATE_SLUG
    )
    document = generate_document(
        template_slug=template_slug,
        portfolio_id=request.portfolio_id,
        session=session,
    )

    if task_id is not None:
        update_task_progress(
            session, task_id, current=1, total=2, message="Assembling document"
        )

    job.template_slug = template_slug
    job.compare_to_run_id = document["params"]["compare_to_run_id"]
    job.result_payload = document
    # Artifacts are no longer written at generation time. Export on demand
    # re-renders from the document, so the file can never diverge from what
    # the screen shows — the divergence this redesign exists to remove.
    job.artifact_paths = {}
    job.status = _status_from_document(document)

    if task_id is not None:
        update_task_progress(
            session, task_id, current=2, total=2, message="Report generated"
        )
    session.flush()
