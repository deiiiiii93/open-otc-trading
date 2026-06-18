"""Queue + async execution for scenario test runs. Mirrors batch_pricing."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from .. import database
from ..config import get_settings
from ..models import (
    Portfolio,
    PricingParameterProfile,
    ScenarioTestRun,
    TaskKind,
    TaskRun,
    TaskStatus,
)
from .audit import record_audit
from .domains import positions as positions_svc
from .domains import scenario_test as scenario_test_svc
from .task_runner import submit_async_task


def queue_scenario_test(
    session: Session,
    *,
    portfolio_id: int,
    scenario_request: dict[str, Any],
    config: dict[str, Any],
    pricing_parameter_profile_id: int | None = None,
    engine_config_id: int | None = None,
    position_ids: list[int] | None = None,
) -> tuple[ScenarioTestRun, TaskRun]:
    """Create a queued ScenarioTestRun + TaskRun and dispatch the async worker."""
    if (
        not scenario_request.get("predefined")
        and not scenario_request.get("custom")
        and not scenario_request.get("scenario_set")
    ):
        raise ValueError(
            "At least one scenario is required (predefined, custom, or scenario_set)"
        )
    portfolio = session.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise ValueError(f"Portfolio not found: {portfolio_id}")
    if engine_config_id is not None:
        from app.services.engine_configs import get_engine_config

        get_engine_config(session, engine_config_id)
    if position_ids is not None:
        # Validate scoped ids belong to the portfolio (raises ValueError listing
        # missing ids -> REST maps to 400), mirroring batch pricing. Normalizing to
        # the resolved ids means a typo/foreign id errors out instead of silently
        # producing a smaller "successful" run.
        from app.services.risk_engine import _resolve_risk_positions

        resolved_scope = _resolve_risk_positions(
            portfolio, session, position_ids=position_ids
        )
        position_ids = [p.id for p in resolved_scope]

    # Validate that the scenario specs resolve BEFORE persisting anything, so bad
    # predefined names / missing saved sets / malformed custom specs return a
    # synchronous 400 (ValueError) rather than a queued run that fails in the worker.
    from .domains import scenario_catalog
    scenario_catalog.resolve_scenarios(scenario_request)  # raises ValueError on bad specs

    run = ScenarioTestRun(
        portfolio_id=portfolio_id,
        pricing_parameter_profile_id=pricing_parameter_profile_id,
        engine_config_id=engine_config_id,
        status=TaskStatus.QUEUED.value,
        scenario_spec=scenario_request,
        config=config,
        results={},
        excluded_positions=[],
        artifacts={},
        resolved_position_ids=position_ids,
    )
    session.add(run)
    session.flush()
    task = TaskRun(
        kind=TaskKind.SCENARIO_TEST.value,
        status=TaskStatus.QUEUED.value,
        portfolio_id=portfolio_id,
        scenario_test_run_id=run.id,
        message="Queued scenario test run",
    )
    session.add(task)
    session.flush()
    record_audit(
        session,
        event_type="scenario_test.queued",
        actor="desk_user",
        subject_type="portfolio",
        subject_id=portfolio_id,
        payload={"run_id": run.id, "scenarios": scenario_request},
    )
    session.commit()
    submit_async_task(execute_scenario_test_task, task.id, run.id)
    return run, task


def execute_scenario_test_task(
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

    run = session.get(ScenarioTestRun, run_id)
    task = session.get(TaskRun, task_id)
    if run is None or task is None:
        return
    try:
        mark_task_running(session, task_id, message="Running scenario test")
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

        status, results_dict, excluded, raw = scenario_test_svc.run_pipeline(
            session,
            positions=positions,
            pricing_parameter_profile_id=run.pricing_parameter_profile_id,
            engine_config_id=run.engine_config_id,
            scenario_request=run.scenario_spec,
            config=run.config,
            portfolio_name=(
                f"{portfolio.name if portfolio else 'portfolio'}-scenario"
            ),
            valuation_date=valuation_as_of,
        )
        run.results = results_dict
        run.excluded_positions = excluded
        if status == "completed" and raw is not None:
            settings = get_settings()
            run.artifacts = scenario_test_svc.write_artifacts(
                results=results_dict,
                excluded_positions=excluded,
                run_id=run.id,
                formats=run.config.get("export_formats", ["json"]),
                base_dir=settings.scenario_test_output_dir,
            )
        # NOTE: stale-task recovery (mark_stale_tasks_failed) resets the TaskRun but not a
        # linked ScenarioTestRun; a run interrupted by a server restart may remain
        # status="running". Acceptable for v1.
        run.status = status
        session.commit()
        mark_task_finished(
            session,
            task_id,
            status=TaskStatus.COMPLETED.value,
            message=f"Scenario test {status}",
            result_payload={"scenario_test_run_id": run_id},
        )
        session.commit()
    except Exception as exc:  # noqa: BLE001 — persist failure, never crash the worker
        session.rollback()
        try:
            run = session.get(ScenarioTestRun, run_id)
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
