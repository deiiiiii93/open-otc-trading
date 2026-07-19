from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from threading import Lock
from typing import Any

from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import (
    BacktestRun,
    GreekLandscapeRun,
    LimitMonitoringRun,
    ReportJob,
    ReportStatus,
    RiskRun,
    ScenarioTestRun,
    TaskRun,
    TaskStatus,
)

_EXECUTOR: ThreadPoolExecutor | None = None
_EXECUTOR_WORKERS: int | None = None
_EXECUTOR_LOCK = Lock()

ACTIVE_TASK_STATUSES = (TaskStatus.QUEUED.value, TaskStatus.RUNNING.value)
TERMINAL_TASK_STATUSES = (
    TaskStatus.COMPLETED.value,
    TaskStatus.COMPLETED_WITH_ERRORS.value,
    TaskStatus.FAILED.value,
)


def configure_task_executor(settings: Settings | None = None) -> None:
    """Prepare the process-level executor for async DB-backed tasks."""
    resolved = settings or get_settings()
    _executor_for_workers(resolved.async_task_workers)


def submit_async_task(
    fn: Callable[..., Any],
    *args: Any,
    settings: Settings | None = None,
) -> Future[Any]:
    resolved = settings or get_settings()
    executor = _executor_for_workers(resolved.async_task_workers)
    return executor.submit(fn, *args)


def _executor_for_workers(workers: int) -> ThreadPoolExecutor:
    global _EXECUTOR, _EXECUTOR_WORKERS
    normalized = max(1, int(workers))
    with _EXECUTOR_LOCK:
        if _EXECUTOR is not None and _EXECUTOR_WORKERS == normalized:
            return _EXECUTOR
        old = _EXECUTOR
        _EXECUTOR = ThreadPoolExecutor(
            max_workers=normalized,
            thread_name_prefix="open-otc-task",
        )
        _EXECUTOR_WORKERS = normalized
    if old is not None:
        old.shutdown(wait=False, cancel_futures=False)
    return _EXECUTOR


def mark_stale_tasks_failed(session: Session) -> int:
    tasks = (
        session.query(TaskRun).filter(TaskRun.status.in_(ACTIVE_TASK_STATUSES)).all()
    )
    stale_tasks: list[TaskRun] = []
    for task in tasks:
        if (
            task.kind == "async_agent"
            and task.status == TaskStatus.RUNNING.value
            and task.message == "awaiting approval"
            and not task.cancel_requested
        ):
            continue
        stale_tasks.append(task)

    now = datetime.utcnow()
    for task in stale_tasks:
        task.status = TaskStatus.FAILED.value
        task.error = "Task interrupted by server restart or reload."
        task.message = "Interrupted before completion"
        task.finished_at = now
        _sync_linked_status(session, task, TaskStatus.FAILED.value)

    # Post a recovery message for each stale async_agent task so the user
    # sees what happened in the chat. Imported locally to avoid a cycle.
    from ..models import AgentMessage, AgentThread

    for task in stale_tasks:
        if task.kind != "async_agent" or task.parent_thread_id is None:
            continue
        session.add(
            AgentMessage(
                thread_id=task.parent_thread_id,
                role="assistant",
                character="async_agent",
                content=(
                    f"Background task '{task.description or 'unnamed'}' was "
                    f"interrupted by server restart. Re-dispatch if still needed."
                ),
                meta={
                    "agent_graph": "async_agent",
                    "agent_phase": "error",
                    "async_task_id": task.id,
                },
            )
        )
        thread = session.get(AgentThread, task.parent_thread_id)
        if thread is not None:
            thread.updated_at = now

    if stale_tasks:
        session.flush()
    return len(stale_tasks)


def mark_task_running(
    session: Session,
    task_id: int,
    *,
    message: str | None = None,
    total: int | None = None,
) -> TaskRun:
    task = _require_task(session, task_id)
    task.status = TaskStatus.RUNNING.value
    task.started_at = task.started_at or datetime.utcnow()
    task.finished_at = None
    task.error = None
    if message is not None:
        task.message = message
    if total is not None:
        task.progress_total = max(0, int(total))
    _sync_linked_status(session, task, TaskStatus.RUNNING.value)
    session.flush()
    return task


def update_task_progress(
    session: Session,
    task_id: int,
    *,
    current: int | None = None,
    total: int | None = None,
    message: str | None = None,
) -> TaskRun:
    task = _require_task(session, task_id)
    if current is not None:
        task.progress_current = max(0, int(current))
    if total is not None:
        task.progress_total = max(0, int(total))
    if message is not None:
        task.message = message
    session.flush()
    return task


def mark_task_finished(
    session: Session,
    task_id: int,
    *,
    status: str,
    message: str | None = None,
    error: str | None = None,
    result_payload: dict | None = None,
) -> TaskRun:
    if status not in TERMINAL_TASK_STATUSES:
        raise ValueError(f"Task status is not terminal: {status}")
    task = _require_task(session, task_id)
    task.status = status
    task.finished_at = datetime.utcnow()
    if task.started_at is None:
        task.started_at = task.finished_at
    if task.progress_total and task.progress_current < task.progress_total:
        task.progress_current = task.progress_total
    if message is not None:
        task.message = message
    task.error = error
    if result_payload is not None:
        task.result_payload = result_payload
    _sync_linked_status(session, task, status)
    session.flush()
    return task


def _require_task(session: Session, task_id: int) -> TaskRun:
    task = session.get(TaskRun, task_id)
    if task is None:
        raise ValueError(f"Task not found: {task_id}")
    return task


def _sync_linked_status(session: Session, task: TaskRun, status: str) -> None:
    if task.risk_run_id is not None:
        run = session.get(RiskRun, task.risk_run_id)
        if run is not None:
            run.status = status
    if task.greeks_landscape_run_id is not None:
        run = session.get(GreekLandscapeRun, task.greeks_landscape_run_id)
        if run is not None:
            run.status = status
    if task.report_job_id is not None:
        job = session.get(ReportJob, task.report_job_id)
        if job is not None:
            if status == TaskStatus.COMPLETED_WITH_ERRORS.value:
                job.status = ReportStatus.COMPLETED_WITH_ERRORS.value
            else:
                job.status = status
    if task.backtest_run_id is not None:
        run = session.get(BacktestRun, task.backtest_run_id)
        if run is not None:
            run.status = status
    if task.scenario_test_run_id is not None:
        run = session.get(ScenarioTestRun, task.scenario_test_run_id)
        if run is not None:
            run.status = status
    if task.limit_monitoring_run_id is not None:
        run = session.get(LimitMonitoringRun, task.limit_monitoring_run_id)
        if run is not None:
            # Monitoring owns the business terminal split.  A completed task
            # must never erase a completed_with_unknowns domain outcome.
            if status == TaskStatus.RUNNING.value:
                run.status = status
                run.started_at = run.started_at or datetime.utcnow()
                run.finished_at = None
            elif status == TaskStatus.FAILED.value:
                run.status = status
                run.finished_at = datetime.utcnow()
            elif run.status not in {"completed", "completed_with_unknowns"}:
                run.status = "completed"
                run.finished_at = datetime.utcnow()
