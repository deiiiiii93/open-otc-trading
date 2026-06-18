"""Queue + async execution for backtest runs. Mirrors scenario_test_runner."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import get_settings
from ..models import (
    BacktestRun,
    Portfolio,
    PricingParameterProfile,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from .audit import record_audit
from .domains import positions as positions_svc
from .domains import backtest as backtest_svc
from .task_runner import submit_async_task

_VALID_ENGINES = {"quad", "pde", "mc", "analytical"}
_VALID_FALLBACK_ENGINES = {"quad", "pde", "mc"}
_VALID_ENGINE_FAMILIES = {"autocallable", "other"}
_VALID_VOL = {"realized", "flat"}


def _validate_spec(spec: dict) -> None:
    import pandas as pd

    start, end = spec.get("start"), spec.get("end")
    if not start or not end or pd.Timestamp(start) >= pd.Timestamp(end):
        raise ValueError("spec.start must be a date strictly before spec.end")
    for key in ("engine", "autocallable_engine", "other_engine"):
        value = spec.get(key)
        if value is not None and value not in _VALID_ENGINES:
            raise ValueError(f"{key} must be one of {sorted(_VALID_ENGINES)}")
    fallback = spec.get("fallback_engine")
    if fallback is not None and fallback not in _VALID_FALLBACK_ENGINES:
        raise ValueError(f"fallback_engine must be one of {sorted(_VALID_FALLBACK_ENGINES)}")
    family = spec.get("engine_family")
    if family is not None and family not in _VALID_ENGINE_FAMILIES:
        raise ValueError(f"engine_family must be one of {sorted(_VALID_ENGINE_FAMILIES)}")
    if spec.get("vol_source", "realized") not in _VALID_VOL:
        raise ValueError(f"vol_source must be one of {sorted(_VALID_VOL)}")


def queue_backtest(
    session: Session,
    *,
    portfolio_id: int,
    spec: dict,
    config: dict,
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
) -> tuple[BacktestRun, TaskRun]:
    """Create a queued BacktestRun + TaskRun and dispatch the async worker."""
    _validate_spec(spec)
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    if engine_config_id is not None:
        from app.services.engine_configs import get_engine_config

        get_engine_config(session, engine_config_id)
    if position_ids is not None:
        from app.services.risk_engine import _resolve_risk_positions

        resolved_scope = _resolve_risk_positions(
            portfolio, session, position_ids=position_ids
        )
        position_ids = [p.id for p in resolved_scope]

    run = BacktestRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        status=TaskStatus.QUEUED.value,
        spec=spec,
        config=config,
        results={},
        excluded_positions=[],
        artifacts={},
        resolved_position_ids=position_ids,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.BACKTEST.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        backtest_run_id=run.id,
        message="Queued backtest run",
    )
    session.add(task)
    session.flush()
    record_audit(
        session,
        event_type="backtest.queued",
        actor="desk_user",
        subject_type="portfolio",
        subject_id=portfolio_id,
        payload={"run_id": run.id, "spec": spec},
    )
    session.commit()
    submit_async_task(execute_backtest_task, task.id, run.id)
    return run, task


def execute_backtest_task(
    task_id: int,
    run_id: int,
    session_factory: sessionmaker | None = None,
) -> None:
    """Entry point for the worker thread. Opens its own session (never reuse the request session)."""
    database.init_db()
    session = (session_factory or database.SessionLocal)()
    try:
        _execute(session, task_id, run_id)
    finally:
        session.close()


def _execute(session: Session, task_id: int, run_id: int) -> None:
    """Core execution: resolve positions, run pipeline, persist results, mark finished."""
    from .task_runner import mark_task_finished, mark_task_running

    run = session.get(BacktestRun, run_id)
    task = session.get(TaskRun, task_id)
    if run is None or task is None:
        return
    try:
        mark_task_running(session, task_id, message="Running backtest")
        run.status = TaskStatus.RUNNING.value
        session.commit()

        portfolio = session.get(Portfolio, run.portfolio_id)
        all_positions = positions_svc.list_filtered(
            portfolio_id=run.portfolio_id, session=session
        )
        # `is not None`: an explicitly-scoped run whose ids resolve to no open
        # positions (e.g. all closed) persists resolved_position_ids=[] and must
        # stay an EMPTY run — a truthy check would treat [] as unscoped and run
        # the whole portfolio.
        if run.resolved_position_ids is not None:
            wanted = set(run.resolved_position_ids)
            positions = [p for p in all_positions if p.id in wanted]
        else:
            positions = list(all_positions)
        run.resolved_position_ids = [p.id for p in positions]

        # Profile-bound runs price as-of the profile's valuation date (historical
        # repricing), mirroring batch pricing; unbound runs use queue time. Without
        # this, a historical profile would resolve quotes/maturities as-of utcnow.
        valuation_as_of = run.created_at
        if run.pricing_parameter_profile_id is not None:
            profile = session.get(
                PricingParameterProfile, run.pricing_parameter_profile_id
            )
            if profile is not None and profile.valuation_date is not None:
                valuation_as_of = profile.valuation_date

        def _progress(cur: int, total: int) -> None:
            task.progress_current, task.progress_total = cur, total
            session.commit()

        status, results_dict, excluded, raw = backtest_svc.run_pipeline(
            session,
            positions=positions,
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            engine_config_id=run.engine_config_id,
            spec=run.spec,
            config=run.config,
            portfolio_name=(portfolio.name if portfolio else "portfolio"),
            valuation_date=valuation_as_of,
            progress=_progress,
        )
        run.results = results_dict
        run.excluded_positions = excluded
        if status == "completed" and raw:
            settings = get_settings()
            run.artifacts = backtest_svc.write_artifacts(
                raw=raw,
                run_id=run.id,
                formats=run.config.get("export_formats", ["json", "xlsx", "html"]),
                base_dir=settings.backtest_output_dir,
            )
        run.status = status
        session.commit()
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.COMPLETED.value,
            message=f"Backtest {status}",
            result_payload={"backtest_run_id": run_id},
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 — persist failure, never crash the worker
        session.rollback()
        try:
            run = session.get(BacktestRun, run_id)
            if run is not None:
                run.status = TaskStatus.FAILED.value
                run.results = {"error": str(exc)}
            mark_task_finished(
                session,
                task_id,
                status=TaskStatus.FAILED.value,
                error=str(exc),
            )
            session.commit()
        except Exception:
            session.rollback()  # last resort: never crash the worker thread
