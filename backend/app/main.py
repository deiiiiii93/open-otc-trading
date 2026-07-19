from __future__ import annotations

from collections import deque
from collections.abc import Generator
from copy import deepcopy
from datetime import datetime, timedelta
import logging
from pathlib import Path
import shutil
from uuid import uuid4
from zipfile import BadZipFile

logger = logging.getLogger("agent.api")

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy.orm import Session, selectinload

from . import database
from .config import Settings, configure_settings, get_settings
from .models import (
    AgentSession,
    AgentTask,
    AgentMessage,
    AgentThread,
    ArtifactEvidenceRef,
    AssumptionSet,
    ContextPack,
    DomainEvent,
    GatewayBinding,
    Instrument,
    MarketDataProfile,
    MarketQuote,
    MarketSnapshot,
    EngineConfigVariant,
    BacktestRun,
    GreekLandscapeRun,
    Portfolio,
    PortfolioKind,
    Position,
    PositionLifecycleEvent,
    PositionValuationResult,
    PositionValuationRun,
    PricingParameterProfile,
    RFQ,
    ReportJob,
    RiskRun,
    ScenarioTestRun,
    SessionArtifact,
    TaskKind,
    TaskRun,
    Workflow,
)
from .schemas import (
    AgentMessageCreate,
    AgentMessageOut,
    AgentModelConfigOut,
    AgentThreadCreate,
    AgentThreadFork,
    AgentThreadOut,
    AgentThreadUpdate,
    AkshareBulkSnapshotRequest,
    AkshareSnapshotRequest,
    AssumptionSetOut,
    AuditEventOut,
    BacktestRunOut,
    BacktestRunRequest,
    GreekLandscapeRunOut,
    GreekLandscapeRunRequest,
    EngineConfigVariantIn,
    EngineConfigVariantOut,
    BuildAssumptionsRequest,
    FxRateCreate,
    FxRateOut,
    FxRateAkshareRequest,
    InstrumentCreate,
    InstrumentOut,
    InstrumentSyncResultOut,
    InstrumentTagsBody,
    InstrumentUpdate,
    MarketDataProfileOut,
    MarketDataSnapshot,
    MarketQuoteCreate,
    MarketQuoteOut,
    MarketSnapshotOut,
    PositionImportBatchOut,
    QuoteRefreshResultOut,
    PositionLifecycleEventCancelIn,
    PositionLifecycleEventIn,
    PositionLifecycleEventOut,
    PositionOut,
    PositionPriceRequest,
    PositionValuationResultOut,
    PositionValuationRunOut,
    PortfolioCreate,
    PortfolioIdsBody,
    PortfolioMembershipOut,
    PortfolioOut,
    PortfolioPositionSpec,
    PortfolioPreviewBody,
    PortfolioRuleBody,
    PortfolioSourcesBody,
    PortfolioTagsBody,
    PortfolioUpdate,
    BuildDefaultProfileRequest,
    LatestAkshareClose,
    PricingParameterProfileCreate,
    PricingParameterProfileOut,
    PricingEnvironmentSnapshot,
    PricingPreviewRequest,
    PricingPreviewOut,
    PricingGreeks,
    ResolvedPricingParamsOut,
    UnderlyingPricingDefaultOut,
    UnderlyingPricingDefaultUpdate,
    RFQApprovalDecision,
    RFQBookRequest,
    RFQChatRequest,
    RFQClientAcceptRequest,
    RFQDraftCreate,
    RFQDraftFromNLRequest,
    RFQDraftFromNLOut,
    RFQDraftUpdate,
    RFQOut,
    RFQQuoteRequest,
    RFQReleaseRequest,
    RFQRequestDraft,
    ReportJobCreate,
    ReportJobOut,
    BatchPricingRunRequest,
    RiskRunOut,
    ScenarioGridRequest,
    ScenarioGridSavedOut,
    ScenarioLibraryOut,
    ScenarioRunOut,
    ScenarioRunRequest,
    ScenarioSetsOut,
    ScenarioSetSavedOut,
    ScenarioSetDetailOut,
    ScenarioSetSummaryOut,
    ScenarioSpec,
    ScenarioTestRunOut,
    ScenarioTestRunRequest,
    TaskRunOut,
    TrySolveBatchOut,
    TrySolveBatchSolveRequest,
    TrySolveExportOut,
    TrySolveExportRequest,
    TrySolveSolveRequest,
    TrySolveValidateRequest,
    HedgeFamilyCount,
    HedgeUnderlyingOut,
    HedgeInstrumentOut,
    HedgeMapEntryOut,
    HedgeMapGroupOut,
    HedgeMarkRequest,
    HedgeMarkResultOut,
    HedgeRemovalResultOut,
    HedgeUnmarkRequest,
    HedgeLoadStartedOut,
    HedgeLoadStatusOut,
    HedgeBandsOut,
    HedgeBandsIn,
    HedgeSolveRequest,
    HedgeBookRequest,
)
from .services.agents import (
    AgentService,
    ResumeAgentError,
    ResumeConflictError,
    ResumeValidationError,
    WorkflowResumeConflict,
)
from .services.audit import record_audit
from .services.domains import positions as positions_svc
from .services.domains import pricing_profiles as pricing_profiles_domain
from .services.domains._errors import DomainWriteError
from .services.domains.pricing import price_product_preview
from .services.domains.booking import (
    BookingRequest,
    ProductBookingSpec,
    book_position,
    prepare_booking_product_spec,
    repair_invalid_snowball_booking_terms,
    repair_position_currencies,
    set_position_currency,
)
from .services.domains.position_terms import (
    refresh_position_barrier_state,
    reset_position_term_rows,
    upsert_position_term_rows,
)
from .services.domains.products import (
    create_or_get_product,
    hydrate_position_product_fields,
    product_spec_from_position_payload,
)
from .services.deep_agent import channel_registry as _channel_registry
from .services.deep_agent.channel_registry import (
    get_registry as _get_registry_for_models,
)
from .services.deep_agent.checkpointer import clear_thread_checkpoints
from .services.deep_agent.model_factory import agent_model_config
from .services.deep_agent.workflow_state import ensure_thread_workflow_state
from .services.market_data import effective_akshare_asset_class, fetch_akshare_snapshot
from .services.position_adapter import TRADE_SHEET, import_positions_from_xlsx
from .services.import_templates import (
    positions_template_bytes,
    pricing_parameters_template_bytes,
)
from .services.position_pricer import (
    MarketOverrides,
    price_portfolio_positions,
)
from .services.assumptions import build_assumptions_set
from .services.pricing_explain import resolve_position_pricing_params
from .services.pricing_profiles import (
    import_pricing_parameter_profile_from_xlsx,
    latest_manual_inputs_by_underlying,
    resolved_underlying_default_inputs,
)
from .services.underlying_defaults import (
    latest_akshare_close_by_underlying,
    list_underlying_defaults,
    refresh_underlying_defaults_from_open_positions,
    upsert_underlying_default,
)
from .services.underlyings import (
    active_market_data_underlyings,
    akshare_asset_class,
    akshare_symbol,
    ensure_underlying,
    link_market_data_profile_underlying,
    link_position_underlying,
    list_underlyings,
    open_position_underlying_symbols,
    sync_underlyings_from_positions,
    update_underlying,
)
from .services.instruments import (
    list_instruments,
    resolvable_market_data_instruments,
    set_instrument_tags,
    sync_hedge_tag,
    sync_instruments_from_positions,
    validate_instrument_terms,
)
from .services.quotes import record_quote, latest_quotes
from .services.quantark import (
    gross_notional_for_position,
    usable_model_value,
)
from .routers.audit import build_audit_router
from .routers.memory import build_memory_router
from .routers.skills import build_skills_router
from .routers.tracing import build_tracing_router
from .routers.arena import build_arena_router
from .routers.goal import build_goal_router
from .routers.workflows import build_desk_workflows_router
from .routers.agent_channels import build_agent_channels_router
from .routers.limits import build_limits_router
from .services.deep_agent.goal_mode import (
    GoalRunService,
    goal_grader_tool_allowlist,
)
from .services.deep_agent.goal_persistence import ThreadColumnBackend
from .services import rfq as rfq_service
from .services import fx as fx_service
from .services.fx import parse_fx_pair_symbol
from .services.reports import execute_report_job_task, queue_report_job
from .services.task_runner import (
    configure_task_executor,
    mark_stale_tasks_failed,
    submit_async_task,
)
from .services.try_solve import (
    export_try_solve_workbook,
    import_try_solve_workbook,
    solve_try_solve_row,
    validate_try_solve_row,
)
from .services.try_solve_registry import get_try_solve_catalog
from .services.gateway import identity as _gateway_identity

agent_service = AgentService()

# ---------------------------------------------------------------------------
# Gateway control-plane Pydantic models (module-level so FastAPI can resolve
# the type annotations under `from __future__ import annotations`).
# ---------------------------------------------------------------------------
from pydantic import BaseModel as _GWBaseModel


class GatewayLinkingCodeRequest(_GWBaseModel):
    persona: str


class GatewayLinkingCodeResponse(_GWBaseModel):
    code: str
    expires_at: str  # ISO 8601 UTC


class GatewayBindingOut(_GWBaseModel):
    id: int
    provider: str
    external_account_id: str
    workspace_id: str
    desk_user: str
    persona: str
    status: str
    bound_at: str | None
    last_seen_at: str | None
    revoked_at: str | None

    class Config:
        from_attributes = True


class GatewayBindingsResponse(_GWBaseModel):
    bindings: list[GatewayBindingOut]
    next_cursor: str | None


def _number_or_none(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None


def _to_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.min.time())


def _fx_rate_payload_from_profile(profile: MarketDataProfile) -> FxRateCreate | None:
    if profile.asset_class != "fx_rate":
        return None
    pair = parse_fx_pair_symbol(profile.symbol)
    if pair is None:
        return None
    spot = (profile.data or {}).get("spot")
    if spot is None:
        return None
    base, quote = pair
    return FxRateCreate(
        base_currency=base,
        quote_currency=quote,
        rate=float(spot),
        as_of_date=profile.valuation_date,
        source=profile.source or "akshare",
    )


def _booking_product_from_payload(payload: PortfolioPositionSpec) -> ProductBookingSpec:
    if payload.product is not None:
        product = ProductBookingSpec(**payload.product.model_dump(mode="json"))
        return prepare_booking_product_spec(product, engine_name=payload.engine_name)
    spec = product_spec_from_position_payload(payload.model_dump(mode="json"))
    product = ProductBookingSpec(**spec.__dict__)
    return prepare_booking_product_spec(product, engine_name=payload.engine_name)


def _valuation_result_gross_notional(
    result: PositionValuationResult, valuation_date: datetime
) -> float:
    payload = result.result_payload or {}
    payload_gross = _number_or_none(payload.get("gross_notional"))
    if payload_gross is not None:
        return payload_gross

    default_market = PricingEnvironmentSnapshot()
    market_inputs = result.market_inputs or {}
    product_kwargs = result.position.product_kwargs or {}
    fallback_spot = (
        _number_or_none(product_kwargs.get("initial_price"))
        or _number_or_none(product_kwargs.get("strike"))
        or default_market.spot
    )
    spot = _number_or_none(market_inputs.get("spot"))
    volatility = _number_or_none(market_inputs.get("volatility"))
    rate = _number_or_none(market_inputs.get("rate"))
    dividend_yield = _number_or_none(market_inputs.get("dividend_yield"))
    market = PricingEnvironmentSnapshot(
        valuation_date=valuation_date,
        spot=spot if spot is not None else fallback_spot,
        volatility=volatility if volatility is not None else default_market.volatility,
        rate=rate if rate is not None else default_market.rate,
        dividend_yield=(
            dividend_yield
            if dividend_yield is not None
            else default_market.dividend_yield
        ),
        asset_name=str(market_inputs.get("asset_name") or result.position.underlying),
        currency=default_market.currency,
    )
    return gross_notional_for_position(result.position, market)


def _valuation_result_out(
    result: PositionValuationResult, valuation_date: datetime
) -> PositionValuationResultOut:
    data = PositionValuationResultOut.model_validate(result).model_dump()
    market_value = _number_or_none(data.get("market_value"))
    if market_value is None:
        return PositionValuationResultOut(**data)

    gross_notional = _valuation_result_gross_notional(result, valuation_date)
    if usable_model_value(market_value, gross_notional):
        return PositionValuationResultOut(**data)

    payload = dict(data.get("result_payload") or {})
    payload.setdefault("raw_price", data.get("price"))
    payload.setdefault("raw_market_value", data.get("market_value"))
    payload.setdefault("raw_pnl", data.get("pnl"))
    payload["gross_notional"] = gross_notional
    data["ok"] = False
    data["price"] = None
    data["market_value"] = None
    data["pnl"] = None
    data["result_payload"] = payload
    data["error"] = data.get("error") or (
        f"Model returned implausible market value {market_value:.6g}; "
        f"gross notional is {gross_notional:.6g}"
    )
    return PositionValuationResultOut(**data)


def _valuation_run_out(run: PositionValuationRun) -> PositionValuationRunOut:
    results = [
        _valuation_result_out(result, run.valuation_date) for result in run.results
    ]
    summary = dict(run.summary or {})
    summary.update(
        {
            "positions": len(results),
            "priced": sum(1 for result in results if result.ok),
            "failed": sum(1 for result in results if not result.ok),
            "market_value": sum(
                float(result.market_value or 0.0) for result in results if result.ok
            ),
            "pnl": sum(float(result.pnl or 0.0) for result in results if result.ok),
        }
    )
    return PositionValuationRunOut(
        id=run.id,
        portfolio_id=run.portfolio_id,
        pricing_parameter_profile_id=run.pricing_parameter_profile_id,
        engine_config_id=run.engine_config_id,
        market_source_path=run.market_source_path,
        valuation_date=run.valuation_date,
        overrides=run.overrides or {},
        summary=summary,
        status=run.status,
        resolved_position_ids=run.resolved_position_ids,
        created_at=run.created_at,
        results=results,
    )


def _latest_task_id(task_runs: list[TaskRun]) -> int | None:
    if not task_runs:
        return None
    return max(task_runs, key=lambda task: task.id).id


def _risk_run_out(run: RiskRun) -> RiskRunOut:
    return RiskRunOut(
        id=run.id,
        portfolio_id=run.portfolio_id,
        pricing_parameter_profile_id=run.pricing_parameter_profile_id,
        engine_config_id=run.engine_config_id,
        market_snapshot_id=run.market_snapshot_id,
        method=run.method,
        status=run.status,
        metrics=run.metrics or {},
        scenario_cells=run.scenario_cells,
        resolved_position_ids=run.resolved_position_ids,
        task_id=_latest_task_id(list(run.task_runs or [])),
        created_at=run.created_at,
    )


def _greeks_landscape_run_out(run: GreekLandscapeRun) -> GreekLandscapeRunOut:
    return GreekLandscapeRunOut(
        id=run.id,
        portfolio_id=run.portfolio_id,
        pricing_parameter_profile_id=run.pricing_parameter_profile_id,
        engine_config_id=run.engine_config_id,
        status=run.status,
        config=run.config or {},
        results=run.results or {},
        excluded_positions=run.excluded_positions,
        resolved_position_ids=run.resolved_position_ids,
        task_id=_latest_task_id(list(run.task_runs or [])),
        created_at=run.created_at,
    )


def _report_job_out(job: ReportJob) -> ReportJobOut:
    return ReportJobOut(
        id=job.id,
        report_type=job.report_type,
        status=job.status,
        request_payload=job.request_payload or {},
        result_payload=job.result_payload or {},
        artifact_paths=job.artifact_paths or {},
        task_id=_latest_task_id(list(job.task_runs or [])),
        created_at=job.created_at,
    )


def _task_run_out(task: TaskRun) -> TaskRunOut:
    return TaskRunOut.model_validate(task)


def _delete_thread_rows(session: Session, thread: AgentThread) -> set[str]:
    """Delete a chat thread plus its app-owned workflow/session state.

    SQLite deployments may have foreign-key enforcement disabled on existing
    connections or older local DBs, so the API boundary cleans these rows
    explicitly instead of relying only on ON DELETE behavior.
    """
    thread_id = thread.id
    workflow_ids = [
        workflow_id
        for (workflow_id,) in (
            session.query(Workflow.id)
            .filter(Workflow.thread_id == thread_id)
            .all()
        )
    ]
    checkpoint_keys = {str(thread_id), f"thread:{thread_id}:router"}
    if workflow_ids:
        checkpoint_keys.update(
            key
            for (key,) in (
                session.query(AgentSession.checkpointer_key)
                .filter(AgentSession.workflow_id.in_(workflow_ids))
                .all()
            )
            if key
        )

    thread.active_workflow_id = None
    session.flush()
    session.query(TaskRun).filter(TaskRun.parent_thread_id == thread_id).update(
        {TaskRun.parent_thread_id: None},
        synchronize_session=False,
    )
    session.query(AgentMessage).filter(AgentMessage.thread_id == thread_id).delete(
        synchronize_session=False,
    )

    if workflow_ids:
        artifact_ids = [
            artifact_id
            for (artifact_id,) in (
                session.query(SessionArtifact.id)
                .filter(SessionArtifact.workflow_id.in_(workflow_ids))
                .all()
            )
        ]
        session.query(DomainEvent).filter(
            DomainEvent.workflow_id.in_(workflow_ids)
        ).delete(synchronize_session=False)
        if artifact_ids:
            session.query(ArtifactEvidenceRef).filter(
                ArtifactEvidenceRef.artifact_id.in_(artifact_ids)
            ).delete(synchronize_session=False)
        session.query(AgentSession).filter(
            AgentSession.workflow_id.in_(workflow_ids)
        ).update({AgentSession.current_task_id: None}, synchronize_session=False)
        session.query(AgentTask).filter(
            AgentTask.workflow_id.in_(workflow_ids)
        ).update(
            {
                AgentTask.assigned_session_id: None,
                AgentTask.context_pack_id: None,
                AgentTask.output_artifact_id: None,
            },
            synchronize_session=False,
        )
        session.query(SessionArtifact).filter(
            SessionArtifact.workflow_id.in_(workflow_ids)
        ).update(
            {
                SessionArtifact.session_id: None,
                SessionArtifact.task_id: None,
                SessionArtifact.context_pack_id: None,
                SessionArtifact.superseded_by: None,
            },
            synchronize_session=False,
        )
        session.query(SessionArtifact).filter(
            SessionArtifact.workflow_id.in_(workflow_ids)
        ).delete(synchronize_session=False)
        session.query(ContextPack).filter(
            ContextPack.workflow_id.in_(workflow_ids)
        ).delete(synchronize_session=False)
        session.query(AgentTask).filter(
            AgentTask.workflow_id.in_(workflow_ids)
        ).delete(synchronize_session=False)
        session.query(AgentSession).filter(
            AgentSession.workflow_id.in_(workflow_ids)
        ).delete(synchronize_session=False)
        session.query(Workflow).filter(Workflow.id.in_(workflow_ids)).delete(
            synchronize_session=False,
        )

    session.query(AgentThread).filter(AgentThread.id == thread_id).delete(
        synchronize_session=False,
    )
    return checkpoint_keys


def _desk_workflow_drive_factory(
    agent_service, character: str = "auto", *, desk_workflow=None, launch_args=None
):
    """Return an injectable per-step driver that forwards SSE frames.

    Monkeypatched in tests; in production it persists the step prompt as a user
    turn then streams one ``stream_and_persist`` turn for that prompt, bound to
    the workflow's persona-derived ``character``. The workflow's ``slug`` and
    ``source`` are threaded into the run so the server (not the model) can stamp
    fan-out attribution for allowlisted seed workflows.
    """
    slug = getattr(desk_workflow, "slug", None)
    source = getattr(desk_workflow, "source", None)

    async def drive(thread_id: int, prompt: str, mode: str):
        with database.SessionLocal() as s:
            s.add(
                AgentMessage(
                    thread_id=thread_id, role="user", content=prompt, meta={"mode": mode}
                )
            )
            s.commit()
        async for frame in agent_service.stream_and_persist(
            thread_id=thread_id,
            content=prompt,
            requested_character=character,
            mode=mode,
            confirmed_cost_preview=True,
            desk_workflow_slug=slug,
            desk_workflow_source=source,
            desk_workflow_launch_args=launch_args,
        ):
            yield frame

    return drive


def _desk_workflow_settle_factory():
    """Return a settle() that waits on tasks queued after this baseline.

    Reuses the arena's between-steps waiter (snapshots the TaskRun high-water
    mark now, then blocks until tasks queued after it are terminal).
    """
    from .services.arena.runner import _make_default_settle

    return _make_default_settle()


def create_app(
    settings: Settings | None = None,
    agent_service_override: AgentService | None = None,
) -> FastAPI:
    if settings is not None:
        configure_settings(settings)
        database.configure_database(settings)
    active_settings = settings or get_settings()
    database.init_db()
    configure_task_executor(active_settings)
    with database.SessionLocal() as startup_session:
        try:
            stale_count = mark_stale_tasks_failed(startup_session)
            repaired_rfq_positions = rfq_service.repair_legacy_rfq_booked_positions(startup_session)
            repaired_snowball_bookings = repair_invalid_snowball_booking_terms(startup_session)
            repaired_currencies = repair_position_currencies(startup_session)
            if stale_count:
                logger.info("Marked %s stale async task(s) as failed", stale_count)
            if repaired_rfq_positions:
                logger.info("Repaired %s RFQ-booked position(s)", repaired_rfq_positions)
            if repaired_snowball_bookings:
                logger.info("Repaired %s Snowball booking term set(s)", repaired_snowball_bookings)
            if repaired_currencies:
                logger.info("Repaired currency on %s position(s)", repaired_currencies)
            if stale_count or repaired_rfq_positions or repaired_snowball_bookings or repaired_currencies:
                startup_session.commit()
        except Exception:
            startup_session.rollback()
            logger.exception("Failed to run startup recovery")
    if settings is not None and agent_service_override is None:
        _channel_registry.configure_registry(None)
    active_agent_service = (
        agent_service_override
        if agent_service_override is not None
        else (
            AgentService(settings=active_settings)
            if settings is not None
            else agent_service
        )
    )

    app = FastAPI(title=active_settings.app_name)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_db() -> Generator[Session, None, None]:
        yield from database.get_session()

    def _store_upload(upload: UploadFile, subdir: str) -> Path:
        filename = Path(upload.filename or "upload.xlsx").name
        target_dir = active_settings.artifact_dir / "uploads" / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = (
            target_dir
            / f"{datetime.utcnow():%Y%m%d%H%M%S}-{uuid4().hex[:8]}-{filename}"
        )
        with target.open("wb") as handle:
            shutil.copyfileobj(upload.file, handle)
        return target

    def _parse_optional_datetime(value: str | None) -> datetime | None:
        if not value:
            return None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return datetime.fromisoformat(value)

    def _artifact_url(path_value: str | None) -> str | None:
        if not path_value:
            return None
        base = active_settings.artifact_dir.resolve()
        path = Path(path_value).resolve()
        if path != base and base not in path.parents:
            return None
        return f"/api/artifacts/{path.relative_to(base).as_posix()}"

    def _filter_try_solve_export_rows(payload: TrySolveExportRequest):
        rows = payload.rows
        if payload.scope == "selected":
            selected = set(payload.selected_row_ids)
            return [row for row in rows if row.row_id in selected]
        if payload.scope == "solved":
            return [row for row in rows if row.status == "solved"]
        if payload.scope == "errors":
            return [row for row in rows if row.status not in {"solved", "solver_ready"}]
        return rows

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "app": active_settings.app_name}

    @app.get("/api/artifacts/{relative_path:path}")
    def get_artifact(relative_path: str):
        base = active_settings.artifact_dir.resolve()
        target = (base / relative_path).resolve()
        if target != base and base not in target.parents:
            raise HTTPException(
                status_code=403,
                detail="Artifact path is outside the artifact directory",
            )
        if not target.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(target)

    @app.get("/api/agent/models", response_model=AgentModelConfigOut)
    def get_agent_models() -> dict:
        return agent_model_config(_get_registry_for_models())

    @app.post("/api/agent/channels/reload")
    def reload_channels() -> dict:
        # Note: the model object held by an in-flight stream stays alive via Python
        # references; that stream completes against the old model. New requests
        # after this call use the new registry.
        try:
            new_registry = _channel_registry.reload(force_reread_dotenv=True)
        except Exception as exc:
            logger.exception("channel reload failed")
            raise HTTPException(
                status_code=400, detail=f"reload failed: {exc}"
            ) from exc
        active_agent_service.rebuild_default_model()
        return {
            "ok": True,
            "active": new_registry.default_selection(),
            "healthy_channels": [ch.name for ch in new_registry.channels if ch.healthy],
            "errors": [],
        }

    @app.post("/api/chat/threads", response_model=AgentThreadOut)
    def create_thread(payload: AgentThreadCreate, session: Session = Depends(get_db)):
        thread = active_agent_service.create_thread(
            session, payload.title, payload.character, source=payload.source
        )
        session.commit()
        return thread

    @app.get("/api/chat/threads", response_model=list[AgentThreadOut])
    def list_threads(session: Session = Depends(get_db)):
        return (
            session.query(AgentThread)
            .options(selectinload(AgentThread.messages))
            .order_by(AgentThread.updated_at.desc())
            .all()
        )

    def _get_thread_or_404(thread_id: int, session: Session) -> AgentThread:
        thread = (
            session.query(AgentThread)
            .options(selectinload(AgentThread.messages))
            .filter(AgentThread.id == thread_id)
            .one_or_none()
        )
        if thread is None:
            raise HTTPException(status_code=404, detail="Thread not found")
        return thread

    def _clean_thread_title(title: str) -> str:
        cleaned = title.strip()
        if not cleaned:
            raise HTTPException(status_code=422, detail="Thread title cannot be blank")
        return cleaned[:200]

    @app.patch("/api/chat/threads/{thread_id}", response_model=AgentThreadOut)
    def rename_thread(
        thread_id: int,
        payload: AgentThreadUpdate,
        session: Session = Depends(get_db),
    ):
        thread = _get_thread_or_404(thread_id, session)
        if payload.title is not None:
            thread.title = _clean_thread_title(payload.title)
            record_audit(
                session,
                event_type="thread.renamed",
                actor="desk_user",
                subject_type="thread",
                subject_id=thread.id,
                payload={"title": thread.title},
            )
        if payload.report_currency is not None:
            thread.report_currency = payload.report_currency
        session.commit()
        session.refresh(thread)
        return thread

    @app.get("/api/chat/threads/{thread_id}/export")
    def export_thread(thread_id: int, session: Session = Depends(get_db)):
        thread = _get_thread_or_404(thread_id, session)
        data = AgentThreadOut.model_validate(thread).model_dump(mode="json")
        data["exported_at"] = datetime.utcnow().isoformat()
        filename = f"agent-thread-{thread.id}.json"
        return JSONResponse(
            data,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.delete("/api/chat/threads/{thread_id}")
    def delete_thread(thread_id: int, session: Session = Depends(get_db)):
        thread = _get_thread_or_404(thread_id, session)
        record_audit(
            session,
            event_type="thread.deleted",
            actor="desk_user",
            subject_type="thread",
            subject_id=thread.id,
            payload={"title": thread.title, "message_count": len(thread.messages)},
        )
        checkpoint_keys = _delete_thread_rows(session, thread)
        session.commit()
        for checkpoint_key in sorted(checkpoint_keys):
            try:
                clear_thread_checkpoints(active_settings, checkpoint_key)
            except Exception:
                logger.exception(
                    "Failed to clear checkpoints for deleted thread %s key %s",
                    thread_id,
                    checkpoint_key,
                )
        return {"ok": True, "deleted_id": thread_id}

    @app.post("/api/chat/threads/{thread_id}/fork", response_model=AgentThreadOut)
    def fork_thread(
        thread_id: int,
        payload: AgentThreadFork,
        session: Session = Depends(get_db),
    ):
        source = _get_thread_or_404(thread_id, session)
        title = _clean_thread_title(payload.title or f"Fork of {source.title}")
        forked = active_agent_service.create_thread(session, title, source.character)
        session.flush()
        for message in source.messages:
            session.add(
                AgentMessage(
                    thread_id=forked.id,
                    role=message.role,
                    character=message.character,
                    content=message.content,
                    meta=deepcopy(message.meta or {}),
                )
            )
        record_audit(
            session,
            event_type="thread.forked",
            actor="desk_user",
            subject_type="thread",
            subject_id=forked.id,
            payload={
                "source_thread_id": source.id,
                "message_count": len(source.messages),
            },
        )
        session.commit()
        session.refresh(forked)
        return forked

    @app.post("/api/chat/threads/{thread_id}/messages/stream")
    async def stream_chat_message(
        thread_id: int,
        payload: AgentMessageCreate,
        session: Session = Depends(get_db),
    ):
        thread = session.get(AgentThread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        resolved_selection = active_agent_service.normalize_model_selection(
            payload.model.model_dump() if payload.model else None
        )
        ensure_thread_workflow_state(session, thread.id)

        # Persist the user turn synchronously so it exists even if streaming
        # fails or the client disconnects mid-response.
        active_agent_service.auto_name_thread_from_first_message(
            session, thread, payload.content
        )
        # Normalize the execution mode so the persisted metadata matches what
        # actually runs: store the canonical mode and the derived clear-HITL
        # boolean (back-compat key ``yolo_mode``) instead of the raw request
        # fields, which disagree for AUTO/YOLO turns (mode set, yolo_mode False).
        from .services.agents import resolve_execution_mode

        norm_mode, clear_hitl, _allow_reply_options = resolve_execution_mode(
            payload.mode, payload.yolo_mode
        )
        user_msg = AgentMessage(
            thread_id=thread.id,
            role="user",
            character=None,
            content=payload.content,
            meta={
                "page_context": (
                    payload.page_context.model_dump(mode="json")
                    if payload.page_context
                    else None
                ),
                "context_usage": (
                    payload.context_usage.model_dump(mode="json")
                    if payload.context_usage
                    else None
                ),
                "accounting_date": (
                    payload.accounting_date.isoformat()
                    if payload.accounting_date
                    else None
                ),
                "model_selection": resolved_selection,
                "yolo_mode": clear_hitl,
                "mode": norm_mode,
                "envelope": payload.envelope,
                "confirmed_cost_preview": payload.confirmed_cost_preview,
            },
        )
        session.add(user_msg)
        session.commit()

        return StreamingResponse(
            active_agent_service.stream_and_persist(
                thread_id=thread.id,
                content=payload.content,
                requested_character=payload.character,
                page_context=payload.page_context,
                context_usage=payload.context_usage,
                accounting_date=payload.accounting_date,
                model_selection=resolved_selection,
                yolo_mode=payload.yolo_mode,
                mode=payload.mode,
                envelope=payload.envelope,
                confirmed_cost_preview=payload.confirmed_cost_preview,
            ),
            media_type="text/event-stream",
        )

    @app.post("/api/chat/threads/{thread_id}/workflows/{slug}/run")
    async def run_thread_workflow(
        thread_id: int,
        slug: str,
        payload: dict | None = None,
        session: Session = Depends(get_db),
    ):
        from .services.desk_workflow_runner import persona_to_character, run_desk_workflow
        from .services.desk_workflows import get_desk_workflow
        from .services.desk_workflows_script import (
            WorkflowScriptError,
            extract_meta,
            validate_workflow_args,
        )

        thread = session.get(AgentThread, thread_id)
        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")
        wf = get_desk_workflow(session, slug)
        if wf is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        raw_args = (payload or {}).get("args")
        if raw_args is None:
            raw_args = {}
        try:
            validated_args = validate_workflow_args(extract_meta(wf.script), raw_args)
        except WorkflowScriptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        ensure_thread_workflow_state(session, thread.id)
        session.commit()
        mode = (payload or {}).get("mode") or wf.default_mode
        drive = _desk_workflow_drive_factory(
            active_agent_service, persona_to_character(wf.persona),
            desk_workflow=wf, launch_args=validated_args,
        )
        settle = _desk_workflow_settle_factory()
        return StreamingResponse(
            run_desk_workflow(
                thread_id=thread.id, workflow=wf, mode=mode,
                drive=drive, settle=settle, args=validated_args,
            ),
            media_type="text/event-stream",
        )

    def _resume_action(
        *,
        thread_id: int,
        message_id: int,
        action_id: str,
        decision: str,
        session: Session,
    ) -> AgentMessage:
        """Thin wrapper: delegates to service layer with actor='desk_user'."""
        try:
            return active_agent_service.resume_pending_action(
                thread_id=thread_id,
                message_id=message_id,
                action_id=action_id,
                decision=decision,
                actor="desk_user",
                session=session,
            )
        except ResumeValidationError as exc:
            raise HTTPException(status_code=exc.status_hint, detail=str(exc)) from exc
        except ResumeConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except WorkflowResumeConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ResumeAgentError as exc:
            raise HTTPException(status_code=exc.status_hint, detail=str(exc)) from exc

    @app.post(
        "/api/chat/threads/{thread_id}/messages/{message_id}/actions/{action_id}/confirm",
        response_model=AgentMessageOut,
    )
    def confirm_agent_action(
        thread_id: int,
        message_id: int,
        action_id: str,
        session: Session = Depends(get_db),
    ):
        msg = _resume_action(
            thread_id=thread_id,
            message_id=message_id,
            action_id=action_id,
            decision="confirm",
            session=session,
        )
        session.commit()
        return msg

    @app.post(
        "/api/chat/threads/{thread_id}/messages/{message_id}/actions/{action_id}/dismiss",
        response_model=AgentMessageOut,
    )
    def dismiss_agent_action(
        thread_id: int,
        message_id: int,
        action_id: str,
        session: Session = Depends(get_db),
    ):
        msg = _resume_action(
            thread_id=thread_id,
            message_id=message_id,
            action_id=action_id,
            decision="dismiss",
            session=session,
        )
        session.commit()
        return msg

    @app.get("/api/chat/threads/{thread_id}/async_agents")
    def list_thread_async_agents(
        thread_id: int,
        include_terminal: bool = False,
        limit: int = 20,
        session: Session = Depends(get_db),
    ):
        from .models import TaskRun as _TaskRun
        from .models import TaskStatus as _TaskStatus

        active_statuses = (_TaskStatus.QUEUED.value, _TaskStatus.RUNNING.value)
        q = session.query(_TaskRun).filter(
            _TaskRun.kind == "async_agent",
            _TaskRun.parent_thread_id == thread_id,
        )
        if not include_terminal:
            q = q.filter(_TaskRun.status.in_(active_statuses))
        rows = q.order_by(_TaskRun.started_at.desc().nulls_last()).limit(limit).all()
        return [
            {
                "task_id": row.id,
                "description": row.description or "",
                "status": row.status,
                "awaiting_approval": (row.message or "") == "awaiting approval",
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                "last_message_preview": None,
            }
            for row in rows
        ]

    @app.post("/api/client/rfq/form", response_model=RFQOut)
    def client_rfq_form(payload: RFQRequestDraft, session: Session = Depends(get_db)):
        try:
            rfq = rfq_service.create_and_quote_rfq(
                session, payload, channel="form", actor=payload.client_name
            )
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/client/rfq/chat", response_model=RFQOut)
    def client_rfq_chat(payload: RFQChatRequest, session: Session = Depends(get_db)):
        extracted = rfq_service.draft_from_natural_language(
            payload.message, payload.client_name
        )
        try:
            rfq = rfq_service.create_rfq_draft(
                session, extracted.draft, channel="chat", actor=payload.client_name
            )
            if not extracted.missing_fields:
                rfq = rfq_service.quote_rfq(
                    session,
                    rfq.id,
                    RFQQuoteRequest(created_by=payload.client_name),
                )
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/client/rfq/{rfq_id}", response_model=RFQOut)
    def get_client_rfq(rfq_id: int, session: Session = Depends(get_db)):
        rfq = (
            session.query(RFQ)
            .options(selectinload(RFQ.quote_versions))
            .filter(RFQ.id == rfq_id)
            .first()
        )
        if not rfq:
            raise HTTPException(status_code=404, detail="RFQ not found")
        return rfq

    @app.get("/api/client/rfqs", response_model=list[RFQOut])
    def list_client_rfqs(
        client_name: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        session: Session = Depends(get_db),
    ):
        query = session.query(RFQ).options(selectinload(RFQ.quote_versions))
        if client_name:
            query = query.filter(RFQ.client_name == client_name)
        return (
            query.order_by(RFQ.created_at.desc(), RFQ.id.desc())
            .limit(limit)
            .all()
        )

    @app.get("/api/rfq/catalog")
    def get_rfq_catalog():
        return rfq_service.get_rfq_catalog()

    @app.get("/api/rfq/try-solve/catalog")
    def get_rfq_try_solve_catalog():
        return get_try_solve_catalog()

    @app.post("/api/rfq/try-solve/import", response_model=TrySolveBatchOut)
    def import_rfq_try_solve_workbook(file: UploadFile = File(...)):
        upload_path = _store_upload(file, "try-solve")
        try:
            return import_try_solve_workbook(upload_path)
        except (BadZipFile, InvalidFileException, KeyError, OSError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid or unreadable workbook upload: {exc}",
            ) from exc

    @app.post("/api/rfq/try-solve/validate")
    def validate_rfq_try_solve_row(
        payload: TrySolveValidateRequest, session: Session = Depends(get_db)
    ):
        return validate_try_solve_row(payload.row, session)

    @app.post("/api/rfq/try-solve/solve")
    def solve_rfq_try_solve_row(
        payload: TrySolveSolveRequest, session: Session = Depends(get_db)
    ):
        return solve_try_solve_row(payload.row, session)

    @app.post("/api/rfq/try-solve/solve-batch", response_model=TrySolveBatchOut)
    def solve_rfq_try_solve_batch(
        payload: TrySolveBatchSolveRequest, session: Session = Depends(get_db)
    ):
        rows = [solve_try_solve_row(row, session) for row in payload.rows]
        return TrySolveBatchOut(
            batch_id=f"try-solve-{uuid4().hex[:12]}",
            rows=rows,
            summary={
                "total_rows": len(rows),
                "solved": sum(1 for row in rows if row.status == "solved"),
            },
        )

    @app.post("/api/rfq/try-solve/export", response_model=TrySolveExportOut)
    def export_rfq_try_solve_workbook(payload: TrySolveExportRequest):
        rows = _filter_try_solve_export_rows(payload)
        filename = (
            f"try-solve-results-{datetime.utcnow():%Y%m%d%H%M%S}-"
            f"{uuid4().hex[:8]}.xlsx"
        )
        output_path = active_settings.artifact_dir / "try-solve" / filename
        export_try_solve_workbook(rows, output_path)
        url = _artifact_url(str(output_path))
        if url is None:
            raise HTTPException(status_code=500, detail="Export artifact path is invalid")
        return TrySolveExportOut(
            filename=filename,
            url=url,
            row_count=len(rows),
            scope=payload.scope,
        )

    @app.post("/api/rfq/draft/from-nl", response_model=RFQDraftFromNLOut)
    def draft_rfq_from_natural_language(payload: RFQDraftFromNLRequest):
        return rfq_service.draft_from_natural_language(
            payload.message, payload.client_name
        )

    @app.get("/api/internal/rfqs", response_model=list[RFQOut])
    def list_rfqs(session: Session = Depends(get_db)):
        return (
            session.query(RFQ)
            .options(selectinload(RFQ.quote_versions))
            .order_by(RFQ.created_at.desc())
            .all()
        )

    @app.post("/api/internal/rfq/draft", response_model=RFQOut)
    def create_internal_rfq_draft(
        payload: RFQDraftCreate, session: Session = Depends(get_db)
    ):
        try:
            rfq = rfq_service.create_rfq_draft(
                session, payload, channel=payload.channel, actor="desk_user"
            )
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/internal/rfq/{rfq_id}/draft", response_model=RFQOut)
    def update_internal_rfq_draft(
        rfq_id: int, payload: RFQDraftUpdate, session: Session = Depends(get_db)
    ):
        try:
            rfq = rfq_service.update_rfq_draft(
                session, rfq_id, payload, actor="desk_user"
            )
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/submit", response_model=RFQOut)
    def submit_internal_rfq(rfq_id: int, session: Session = Depends(get_db)):
        try:
            rfq = rfq_service.submit_rfq_for_approval(
                session, rfq_id, actor="desk_user"
            )
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/quote", response_model=RFQOut)
    def quote_internal_rfq(
        rfq_id: int,
        payload: RFQQuoteRequest = RFQQuoteRequest(),
        session: Session = Depends(get_db),
    ):
        try:
            rfq = rfq_service.quote_rfq(session, rfq_id, payload)
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/approve", response_model=RFQOut)
    def approve_rfq(
        rfq_id: int, payload: RFQApprovalDecision, session: Session = Depends(get_db)
    ):
        try:
            rfq = rfq_service.approve_rfq(session, rfq_id, payload)
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/reject", response_model=RFQOut)
    def reject_rfq(
        rfq_id: int, payload: RFQApprovalDecision, session: Session = Depends(get_db)
    ):
        try:
            rfq = rfq_service.reject_rfq(session, rfq_id, payload)
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/release", response_model=RFQOut)
    def release_internal_rfq(
        rfq_id: int, payload: RFQReleaseRequest, session: Session = Depends(get_db)
    ):
        try:
            rfq = rfq_service.release_rfq(session, rfq_id, payload)
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/client-accept", response_model=RFQOut)
    def client_accept_internal_rfq(
        rfq_id: int,
        payload: RFQClientAcceptRequest,
        session: Session = Depends(get_db),
    ):
        try:
            rfq = rfq_service.mark_client_accepted(session, rfq_id, payload)
            session.commit()
            return rfq
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/internal/rfq/{rfq_id}/book", response_model=PositionOut)
    def book_internal_rfq(
        rfq_id: int, payload: RFQBookRequest, session: Session = Depends(get_db)
    ):
        try:
            position = rfq_service.book_rfq_to_position(session, rfq_id, payload)
            session.commit()
            return position
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    from .services import portfolio_service
    from .models import (
        PortfolioCycleError,
        PortfolioDepthError,
        PortfolioKindError,
        PortfolioNameConflict,
        RuleCompilationError,
        RuleValidationError,
    )
    from .services.portfolio_membership import resolve_position_ids, resolve_positions

    def _portfolio_response(session, portfolio) -> PortfolioOut:
        if portfolio.kind == "container":
            positions = list(portfolio.positions)
        else:
            try:
                positions = resolve_positions(portfolio, session)
            except (PortfolioCycleError, PortfolioDepthError):
                positions = []
        try:
            count = len(resolve_position_ids(portfolio, session))
        except (PortfolioCycleError, PortfolioDepthError):
            count = 0
        return PortfolioOut.model_validate(
            {
                "id": portfolio.id,
                "name": portfolio.name,
                "base_currency": portfolio.base_currency,
                "kind": portfolio.kind,
                "description": portfolio.description,
                "tags": portfolio.tags or [],
                "filter_rule": portfolio.filter_rule,
                "manual_include_ids": portfolio.manual_include_ids or [],
                "manual_exclude_ids": portfolio.manual_exclude_ids or [],
                "source_portfolio_ids": portfolio.source_portfolio_ids or [],
                "resolved_position_count": count,
                "created_at": portfolio.created_at,
                "updated_at": portfolio.updated_at,
                "positions": [
                    PositionOut.model_validate(p, from_attributes=True)
                    for p in positions
                ],
            }
        )

    def _container_portfolio_or_404(portfolio_id: int, session: Session) -> Portfolio:
        portfolio = session.get(Portfolio, portfolio_id)
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if portfolio.kind != "container":
            raise HTTPException(
                status_code=400,
                detail="Positions can only be added to container portfolios",
            )
        return portfolio

    @app.get("/api/portfolios", response_model=list[PortfolioOut])
    def list_portfolios(
        kind: str | None = None,
        tag: list[str] | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        portfolios = portfolio_service.list_portfolios(session, kind=kind, tags=tag)
        return [_portfolio_response(session, p) for p in portfolios]

    @app.post("/api/portfolios", response_model=PortfolioOut)
    def create_portfolio(payload: PortfolioCreate, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.create_portfolio(
                session,
                name=payload.name,
                base_currency=payload.base_currency,
                kind=payload.kind,
                description=payload.description,
                tags=payload.tags,
                filter_rule=payload.filter_rule,
                manual_include_ids=payload.manual_include_ids,
                manual_exclude_ids=payload.manual_exclude_ids,
                source_portfolio_ids=payload.source_portfolio_ids,
            )
        except PortfolioNameConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (
            PortfolioKindError,
            RuleValidationError,
            PortfolioCycleError,
            RuleCompilationError,
        ) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.get("/api/portfolios/{portfolio_id}", response_model=PortfolioOut)
    def get_portfolio(portfolio_id: int, session: Session = Depends(get_db)):
        try:
            portfolio = portfolio_service.get_portfolio(session, portfolio_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _portfolio_response(session, portfolio)

    @app.patch("/api/portfolios/{portfolio_id}", response_model=PortfolioOut)
    def patch_portfolio(
        portfolio_id: int, payload: PortfolioUpdate, session: Session = Depends(get_db)
    ):
        try:
            portfolio = portfolio_service.update_portfolio(
                session,
                portfolio_id,
                name=payload.name,
                description=payload.description,
                base_currency=payload.base_currency,
                tags=payload.tags,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PortfolioNameConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (RuleValidationError,) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}", status_code=204)
    def delete_portfolio(portfolio_id: int, session: Session = Depends(get_db)):
        try:
            portfolio_service.delete_portfolio(session, portfolio_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        session.commit()
        return Response(status_code=204)

    @app.put("/api/portfolios/{portfolio_id}/rule", response_model=PortfolioOut)
    def put_portfolio_rule(
        portfolio_id: int,
        payload: PortfolioRuleBody,
        session: Session = Depends(get_db),
    ):
        try:
            portfolio = portfolio_service.set_filter_rule(
                session, portfolio_id, payload.filter_rule
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PortfolioKindError, RuleValidationError, RuleCompilationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    def _ids_action(portfolio_id, payload, action):
        try:
            portfolio = action(portfolio_id, payload)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PortfolioKindError, RuleValidationError, PortfolioCycleError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return portfolio

    @app.post("/api/portfolios/{portfolio_id}/includes", response_model=PortfolioOut)
    def add_includes(
        portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)
    ):
        portfolio = _ids_action(
            portfolio_id,
            payload,
            lambda pid, p: portfolio_service.add_manual_includes(
                session, pid, p.position_ids
            ),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}/includes", response_model=PortfolioOut)
    def remove_includes(
        portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)
    ):
        portfolio = _ids_action(
            portfolio_id,
            payload,
            lambda pid, p: portfolio_service.remove_manual_includes(
                session, pid, p.position_ids
            ),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.post("/api/portfolios/{portfolio_id}/excludes", response_model=PortfolioOut)
    def add_excludes(
        portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)
    ):
        portfolio = _ids_action(
            portfolio_id,
            payload,
            lambda pid, p: portfolio_service.add_manual_excludes(
                session, pid, p.position_ids
            ),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}/excludes", response_model=PortfolioOut)
    def remove_excludes(
        portfolio_id: int, payload: PortfolioIdsBody, session: Session = Depends(get_db)
    ):
        portfolio = _ids_action(
            portfolio_id,
            payload,
            lambda pid, p: portfolio_service.remove_manual_excludes(
                session, pid, p.position_ids
            ),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.post("/api/portfolios/{portfolio_id}/sources", response_model=PortfolioOut)
    def add_sources(
        portfolio_id: int,
        payload: PortfolioSourcesBody,
        session: Session = Depends(get_db),
    ):
        portfolio = _ids_action(
            portfolio_id,
            payload,
            lambda pid, p: portfolio_service.add_portfolio_sources(
                session, pid, p.portfolio_ids
            ),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.delete("/api/portfolios/{portfolio_id}/sources", response_model=PortfolioOut)
    def remove_sources(
        portfolio_id: int,
        payload: PortfolioSourcesBody,
        session: Session = Depends(get_db),
    ):
        portfolio = _ids_action(
            portfolio_id,
            payload,
            lambda pid, p: portfolio_service.remove_portfolio_sources(
                session, pid, p.portfolio_ids
            ),
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.put("/api/portfolios/{portfolio_id}/tags", response_model=PortfolioOut)
    def put_tags(
        portfolio_id: int,
        payload: PortfolioTagsBody,
        session: Session = Depends(get_db),
    ):
        try:
            portfolio = portfolio_service.set_portfolio_tags(
                session, portfolio_id, payload.tags
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except RuleValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.get(
        "/api/portfolios/{portfolio_id}/membership",
        response_model=PortfolioMembershipOut,
    )
    def get_membership(portfolio_id: int, session: Session = Depends(get_db)):
        try:
            ids = portfolio_service.preview_membership(session, portfolio_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (PortfolioCycleError, PortfolioDepthError, RuleCompilationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PortfolioMembershipOut(portfolio_id=portfolio_id, position_ids=ids)

    @app.post("/api/portfolios/preview", response_model=PortfolioMembershipOut)
    def post_preview(payload: PortfolioPreviewBody, session: Session = Depends(get_db)):
        try:
            ids = portfolio_service.preview_membership_dry_run(
                session,
                kind=payload.kind,
                filter_rule=payload.filter_rule,
                manual_include_ids=payload.manual_include_ids,
                manual_exclude_ids=payload.manual_exclude_ids,
                source_portfolio_ids=payload.source_portfolio_ids,
            )
        except (
            PortfolioKindError,
            RuleValidationError,
            RuleCompilationError,
            PortfolioCycleError,
            PortfolioDepthError,
        ) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return PortfolioMembershipOut(portfolio_id=0, position_ids=ids)

    @app.post("/api/portfolios/{portfolio_id}/positions", response_model=PortfolioOut)
    def add_position(
        portfolio_id: int,
        payload: PortfolioPositionSpec,
        session: Session = Depends(get_db),
    ):
        portfolio = _container_portfolio_or_404(portfolio_id, session)
        try:
            position = book_position(
                session,
                BookingRequest(
                    portfolio_id=portfolio.id,
                    product=_booking_product_from_payload(payload),
                    quantity=payload.quantity,
                    entry_price=payload.entry_price,
                    status=payload.status,
                    source_trade_id=payload.source_trade_id,
                    source_payload=payload.model_dump(mode="json"),
                    position_kind=payload.position_kind,
                    rfq_id=payload.rfq_id,
                    rfq_quote_version_id=payload.rfq_quote_version_id,
                    trade_effective_date=_to_datetime(payload.trade_effective_date),
                    engine_name=payload.engine_name,
                    engine_kwargs=payload.engine_kwargs,
                    actor="desk_user",
                    source="manual",
                ),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        portfolio.updated_at = datetime.utcnow()
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/import",
        response_model=PositionImportBatchOut,
    )
    def import_portfolio_positions(
        portfolio_id: int,
        file: UploadFile = File(...),
        sheet_name: str = Form(TRADE_SHEET),
        session: Session = Depends(get_db),
    ):
        portfolio = _container_portfolio_or_404(portfolio_id, session)
        upload_path = _store_upload(file, "positions")
        try:
            batch = import_positions_from_xlsx(
                session,
                portfolio_id=portfolio_id,
                xlsx_path=upload_path,
                sheet_name=sheet_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="positions.imported",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=portfolio_id,
            payload={
                "batch_id": batch.id,
                "source_path": str(upload_path),
                "status": batch.status,
            },
        )
        portfolio.updated_at = datetime.utcnow()
        session.commit()
        return batch

    _XLSX_MEDIA_TYPE = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    @app.get("/api/positions/import-template")
    def download_positions_import_template():
        return Response(
            content=positions_template_bytes(),
            media_type=_XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": 'attachment; filename="positions_import_template.xlsx"'
            },
        )

    @app.get("/api/pricing-parameter-profiles/import-template")
    def download_pricing_parameters_import_template():
        return Response(
            content=pricing_parameters_template_bytes(),
            media_type=_XLSX_MEDIA_TYPE,
            headers={
                "Content-Disposition": (
                    'attachment; filename="pricing_parameters_import_template.xlsx"'
                )
            },
        )

    @app.get(
        "/api/pricing-parameter-profiles",
        response_model=list[PricingParameterProfileOut],
    )
    def list_pricing_parameter_profiles(session: Session = Depends(get_db)):
        return (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .order_by(
                PricingParameterProfile.valuation_date.desc(),
                PricingParameterProfile.created_at.desc(),
            )
            .all()
        )

    @app.post(
        "/api/pricing-parameter-profiles",
        response_model=PricingParameterProfileOut,
        status_code=201,
    )
    def create_pricing_parameter_profile(
        payload: PricingParameterProfileCreate,
        session: Session = Depends(get_db),
    ):
        try:
            return pricing_profiles_domain.create_profile(
                name=payload.name,
                valuation_date=payload.valuation_date,
                rows=[row.model_dump() for row in payload.rows],
                actor="desk_user",
                session=session,
            )
        except DomainWriteError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": exc.error, "detail": exc.detail},
            ) from exc

    @app.get(
        "/api/pricing-parameter-profiles/{profile_id}",
        response_model=PricingParameterProfileOut,
    )
    def get_pricing_parameter_profile(
        profile_id: int, session: Session = Depends(get_db)
    ):
        profile = (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .filter(PricingParameterProfile.id == profile_id)
            .one_or_none()
        )
        if profile is None:
            raise HTTPException(
                status_code=404, detail="Pricing parameter profile not found"
            )
        return profile

    @app.get("/api/engine-configs", response_model=list[EngineConfigVariantOut])
    def list_engine_configs(session: Session = Depends(get_db)):
        return (
            session.query(EngineConfigVariant)
            .order_by(EngineConfigVariant.is_default.desc(), EngineConfigVariant.id.desc())
            .all()
        )

    @app.post("/api/engine-configs", response_model=EngineConfigVariantOut)
    def create_engine_config(payload: EngineConfigVariantIn, session: Session = Depends(get_db)):
        from .services.engine_configs import set_default_engine_config, validate_rules

        try:
            validate_rules(payload.rules)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = EngineConfigVariant(**payload.model_dump())
        session.add(config)
        session.flush()
        if config.is_default:
            set_default_engine_config(session, config)
        session.commit()
        session.refresh(config)
        return config

    @app.get("/api/engine-configs/{config_id}", response_model=EngineConfigVariantOut)
    def get_engine_config_endpoint(config_id: int, session: Session = Depends(get_db)):
        config = session.get(EngineConfigVariant, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Engine config not found")
        return config

    @app.put("/api/engine-configs/{config_id}", response_model=EngineConfigVariantOut)
    def update_engine_config(config_id: int, payload: EngineConfigVariantIn, session: Session = Depends(get_db)):
        from .services.engine_configs import set_default_engine_config, validate_rules

        try:
            validate_rules(payload.rules)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        config = session.get(EngineConfigVariant, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Engine config not found")
        for key, value in payload.model_dump().items():
            setattr(config, key, value)
        session.add(config)
        session.flush()
        if config.is_default:
            set_default_engine_config(session, config)
        session.commit()
        session.refresh(config)
        return config

    @app.post("/api/engine-configs/{config_id}/default", response_model=EngineConfigVariantOut)
    def set_engine_config_default(config_id: int, session: Session = Depends(get_db)):
        from .services.engine_configs import set_default_engine_config

        config = session.get(EngineConfigVariant, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Engine config not found")
        set_default_engine_config(session, config)
        session.commit()
        session.refresh(config)
        return config

    @app.delete("/api/engine-configs/{config_id}")
    def delete_engine_config(config_id: int, session: Session = Depends(get_db)):
        config = session.get(EngineConfigVariant, config_id)
        if config is None:
            raise HTTPException(status_code=404, detail="Engine config not found")
        if config.is_default:
            raise HTTPException(
                status_code=400,
                detail="Cannot delete the default engine config; set another default first",
            )
        session.delete(config)
        session.commit()
        return {"ok": True}

    # ------------------------------------------------------------------
    # Instruments endpoints (Task 10 — replaces /api/underlyings group)
    # ------------------------------------------------------------------

    @app.get("/api/instruments", response_model=list[InstrumentOut])
    def list_instruments_endpoint(
        kind: str | None = None,
        status: str | None = None,
        parent_id: int | None = None,
        series_root: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        session: Session = Depends(get_db),
    ):
        rows = list_instruments(
            session,
            kind=kind,
            status=status,
            parent_id=parent_id,
            series_root=series_root,
            search=search,
            tag=tag,
            limit=limit,
            offset=offset,
        )
        return rows

    @app.get("/api/instruments/{instrument_id}", response_model=InstrumentOut)
    def get_instrument_endpoint(
        instrument_id: int, session: Session = Depends(get_db)
    ):
        row = session.get(Instrument, instrument_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Instrument not found")
        return row

    @app.post("/api/instruments", response_model=InstrumentOut, status_code=201)
    def create_instrument_endpoint(
        payload: InstrumentCreate,
        session: Session = Depends(get_db),
    ):
        if session.query(Instrument).filter_by(symbol=payload.symbol).first() is not None:
            raise HTTPException(status_code=409, detail=f"Instrument {payload.symbol} already exists")
        if payload.parent_id is not None and session.get(Instrument, payload.parent_id) is None:
            raise HTTPException(
                status_code=400,
                detail=f"parent_id {payload.parent_id} does not reference an existing instrument",
            )
        try:
            validate_instrument_terms(
                kind=payload.kind,
                strike=payload.strike,
                option_type=payload.option_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        row = Instrument(
            symbol=payload.symbol,
            kind=payload.kind,
            display_name=payload.display_name,
            exchange=payload.exchange,
            currency=payload.currency,
            status=payload.status,
            source="manual",
            akshare_symbol=payload.akshare_symbol,
            akshare_asset_class=payload.akshare_asset_class,
            series_root=payload.series_root,
            expiry=payload.expiry,
            multiplier=payload.multiplier,
            strike=payload.strike,
            option_type=payload.option_type,
            parent_id=payload.parent_id,
            notes=payload.notes,
            rate=payload.rate,
            dividend_yield=payload.dividend_yield,
            volatility=payload.volatility,
        )
        session.add(row)
        session.flush()
        sync_hedge_tag(session, row.id)
        record_audit(
            session,
            event_type="instrument.created",
            actor="desk_user",
            subject_type="instrument",
            subject_id=row.id,
            payload=payload.model_dump(mode='json'),
        )
        session.commit()
        session.refresh(row)
        return row

    @app.patch("/api/instruments/{instrument_id}", response_model=InstrumentOut)
    def patch_instrument_endpoint(
        instrument_id: int,
        payload: InstrumentUpdate,
        session: Session = Depends(get_db),
    ):
        row = session.get(Instrument, instrument_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Instrument not found")
        fields = payload.model_dump(exclude_unset=True)
        # Resolve effective kind/strike/option_type for validation
        effective_kind = fields.get("kind", row.kind)
        effective_strike = fields.get("strike", row.strike)
        effective_option_type = fields.get("option_type", row.option_type)
        try:
            validate_instrument_terms(
                kind=effective_kind,
                strike=effective_strike,
                option_type=effective_option_type,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # Validate parent_id references an existing instrument
        if "parent_id" in fields and fields["parent_id"] is not None:
            parent = session.get(Instrument, fields["parent_id"])
            if parent is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"parent_id {fields['parent_id']} does not reference an existing instrument",
                )
        # Apply only provided fields
        for key, value in fields.items():
            setattr(row, key, value)
        session.flush()
        sync_hedge_tag(session, row.id)
        record_audit(
            session,
            event_type="instrument.updated",
            actor="desk_user",
            subject_type="instrument",
            subject_id=row.id,
            payload=payload.model_dump(exclude_unset=True, mode='json'),
        )
        session.commit()
        session.refresh(row)
        return row

    @app.put("/api/instruments/{instrument_id}/tags", response_model=InstrumentOut)
    def put_instrument_tags(
        instrument_id: int,
        payload: InstrumentTagsBody,
        session: Session = Depends(get_db),
    ):
        try:
            row = set_instrument_tags(session, instrument_id, payload.tags)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="instrument.tags_changed",
            actor="desk_user",
            subject_type="instrument",
            subject_id=row.id,
            payload={"tags": row.tags},
        )
        session.commit()
        session.refresh(row)
        return row

    @app.post("/api/instruments/sync-from-positions", response_model=InstrumentSyncResultOut)
    def sync_instruments_endpoint(session: Session = Depends(get_db)):
        result = sync_instruments_from_positions(session)
        session.commit()
        rows = list_instruments(session)
        return InstrumentSyncResultOut(
            created=result.created,
            existing=result.existing,
            instruments=[InstrumentOut.model_validate(r) for r in rows],
        )

    @app.post("/api/instruments/{instrument_id}/fetch-spot", response_model=MarketDataProfileOut)
    def fetch_instrument_spot(instrument_id: int, session: Session = Depends(get_db)):
        row = session.get(Instrument, instrument_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Instrument not found")
        request = AkshareSnapshotRequest(
            symbol=row.akshare_symbol or akshare_symbol(row.symbol),
            asset_class=effective_akshare_asset_class(
                row.symbol,
                row.akshare_asset_class or akshare_asset_class(row.symbol),
            ),  # type: ignore[arg-type]
            start_date=datetime.utcnow().strftime("%Y-%m-%d"),
            end_date=datetime.utcnow().strftime("%Y-%m-%d"),
            adjust="qfq",
            name=f"{row.symbol} AKShare snapshot",
        )
        normalized = fetch_akshare_snapshot(request)
        profile = _market_profile_from_snapshot(normalized, request, display_symbol=row.symbol)
        profile.underlying_id = row.id
        profile.source_metadata = {
            **(profile.source_metadata or {}),
            "underlying_symbol": row.symbol,
            "akshare_symbol": request.symbol,
        }
        session.add(profile)
        session.flush()
        _emit_profile_quote(session, profile, row.id)
        record_audit(
            session,
            event_type="instrument.fetch_spot",
            actor="desk_user",
            subject_type="instrument",
            subject_id=row.id,
            payload={"market_data_profile_id": profile.id, "symbol": row.symbol},
        )
        session.commit()
        return profile

    # ------------------------------------------------------------------
    # Quotes endpoints (Task 10)
    # ------------------------------------------------------------------

    @app.get("/api/market-data/quotes", response_model=list[MarketQuoteOut])
    def list_quotes_endpoint(
        latest: int | None = None,
        instrument_id: int | None = None,
        limit: int = 100,
        session: Session = Depends(get_db),
    ):
        now = datetime.utcnow()
        if latest:
            # One latest quote per instrument that has quotes
            from sqlalchemy import distinct
            from app.models import Instrument as _Inst

            inst_ids_with_quotes: list[int] = [
                row[0]
                for row in session.query(distinct(MarketQuote.instrument_id)).all()
            ]
            quotes_map = latest_quotes(session, inst_ids_with_quotes, as_of=now)
            result = []
            for iid, q in quotes_map.items():
                inst = session.get(_Inst, iid)
                if inst is None:
                    continue
                age_days = (now - q.as_of).total_seconds() / 86400.0
                result.append(
                    MarketQuoteOut(
                        id=q.id,
                        instrument_id=iid,
                        symbol=inst.symbol,
                        kind=inst.kind,
                        price=q.price,
                        price_type=q.price_type,
                        as_of=q.as_of,
                        source=q.source,
                        age_days=age_days,
                        market_data_profile_id=q.market_data_profile_id,
                    )
                )
            return result
        elif instrument_id is not None:
            from app.models import Instrument as _Inst

            inst = session.get(_Inst, instrument_id)
            quotes = (
                session.query(MarketQuote)
                .filter(MarketQuote.instrument_id == instrument_id)
                .order_by(MarketQuote.as_of.desc(), MarketQuote.id.desc())
                .limit(limit)
                .all()
            )
            result = []
            for q in quotes:
                age_days = (now - q.as_of).total_seconds() / 86400.0
                result.append(
                    MarketQuoteOut(
                        id=q.id,
                        instrument_id=q.instrument_id,
                        symbol=inst.symbol if inst else str(q.instrument_id),
                        kind=inst.kind if inst else "unknown",
                        price=q.price,
                        price_type=q.price_type,
                        as_of=q.as_of,
                        source=q.source,
                        age_days=age_days,
                        market_data_profile_id=q.market_data_profile_id,
                    )
                )
            return result
        return []

    @app.post("/api/market-data/quotes", response_model=MarketQuoteOut)
    def create_manual_quote(
        payload: MarketQuoteCreate, session: Session = Depends(get_db)
    ):
        from app.models import Instrument as _Inst

        inst = session.get(_Inst, payload.instrument_id)
        if inst is None:
            raise HTTPException(
                status_code=404,
                detail=f"Instrument {payload.instrument_id} not found",
            )
        q = record_quote(
            session,
            instrument_id=payload.instrument_id,
            price=payload.price,
            as_of=payload.as_of,
            source="manual",
            price_type=payload.price_type,
        )
        record_audit(
            session,
            event_type="market_data.quote.manual",
            actor="desk_user",
            subject_type="market_quote",
            subject_id=q.id,
            payload={
                "instrument_id": payload.instrument_id,
                "price": payload.price,
                "as_of": payload.as_of.isoformat(),
            },
        )
        session.commit()
        session.refresh(q)
        now = datetime.utcnow()
        age_days = (now - q.as_of).total_seconds() / 86400.0
        return MarketQuoteOut(
            id=q.id,
            instrument_id=q.instrument_id,
            symbol=inst.symbol,
            kind=inst.kind,
            price=q.price,
            price_type=q.price_type,
            as_of=q.as_of,
            source=q.source,
            age_days=age_days,
            market_data_profile_id=q.market_data_profile_id,
        )

    @app.post("/api/market-data/quotes/refresh", response_model=QuoteRefreshResultOut)
    def refresh_quotes_endpoint(
        session: Session = Depends(get_db),
    ):
        """Server-side refresh: sync instruments from positions, then fetch AKShare
        quotes for all resolvable instruments. Per-symbol failures are collected
        but never abort the loop."""
        # (1) Sync instruments from open positions
        sync_result = sync_instruments_from_positions(session)
        session.flush()

        # (2) Partition resolvable vs unresolvable
        resolvable = resolvable_market_data_instruments(session)

        # Instruments with positions but NOT resolvable → skipped list (by symbol)
        skipped: list[str] = []
        pos_symbols = set(open_position_underlying_symbols(session))
        resolvable_symbols = {r.symbol for r in resolvable}
        for sym in sorted(pos_symbols - resolvable_symbols):
            skipped.append(sym)

        # (3) Fetch per resolvable instrument
        fetched = 0
        failed: list[dict[str, str]] = []
        for inst in resolvable:
            req = AkshareSnapshotRequest(
                symbol=inst.akshare_symbol or akshare_symbol(inst.symbol),
                asset_class=effective_akshare_asset_class(
                    inst.symbol,
                    inst.akshare_asset_class or akshare_asset_class(inst.symbol),
                ),  # type: ignore[arg-type]
                start_date=(datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d"),
                end_date=datetime.utcnow().strftime("%Y-%m-%d"),
                adjust="qfq",
                name=f"{inst.symbol} AKShare snapshot",
            )
            try:
                normalized = fetch_akshare_snapshot(req)
                profile = _market_profile_from_snapshot(normalized, req, display_symbol=inst.symbol)
                profile.underlying_id = inst.id
                profile.source_metadata = {
                    **(profile.source_metadata or {}),
                    "underlying_symbol": inst.symbol,
                    "akshare_symbol": req.symbol,
                    "bulk_fetch": True,
                }
                session.add(profile)
                session.flush()
                _emit_profile_quote(session, profile, inst.id)
                fetched += 1
            except Exception as exc:  # noqa: BLE001
                failed.append({"symbol": inst.akshare_symbol or inst.symbol, "error": str(exc)})

        record_audit(
            session,
            event_type="market_data.quotes.refreshed",
            actor="desk_user",
            subject_type="market_quote",
            subject_id="refresh",
            payload={
                "synced_created": sync_result.created,
                "synced_existing": sync_result.existing,
                "fetched": fetched,
                "skipped": skipped,
                "failed_count": len(failed),
            },
        )
        session.commit()
        return QuoteRefreshResultOut(
            synced_created=sync_result.created,
            synced_existing=sync_result.existing,
            fetched=fetched,
            skipped=skipped,
            failed=failed,
        )

    def _serialize_default(
        row,
        latest_map: dict[str, dict | None],
        inherited_map: dict[str, dict] | None = None,
        open_symbols: set[str] | None = None,
    ) -> UnderlyingPricingDefaultOut:
        symbol = row.symbol
        latest = latest_map.get(symbol)
        inherited = (inherited_map or {}).get(symbol)
        resolved = resolved_underlying_default_inputs(row, inherited)
        is_complete = all(value is not None for value in resolved.values())
        return UnderlyingPricingDefaultOut(
            underlying=symbol,
            rate=resolved["rate"],
            dividend_yield=resolved["dividend_yield"],
            volatility=resolved["volatility"],
            notes=row.notes,
            is_complete=is_complete,
            has_open_position=symbol in (open_symbols or set()),
            latest_akshare_close=LatestAkshareClose(**latest) if latest else None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @app.get(
        "/api/underlying-pricing-defaults",
        response_model=list[UnderlyingPricingDefaultOut],
    )
    def list_underlying_pricing_defaults(session: Session = Depends(get_db)):
        rows = list_underlying_defaults(session)
        latest = latest_akshare_close_by_underlying(
            session, [row.underlying for row in rows]
        )
        inherited = latest_manual_inputs_by_underlying(
            session, [row.underlying for row in rows]
        )
        open_symbols = set(open_position_underlying_symbols(session))
        return [_serialize_default(row, latest, inherited, open_symbols) for row in rows]

    @app.put(
        "/api/underlying-pricing-defaults/{underlying:path}",
        response_model=UnderlyingPricingDefaultOut,
    )
    def put_underlying_pricing_default(
        underlying: str,
        payload: UnderlyingPricingDefaultUpdate,
        session: Session = Depends(get_db),
    ):
        body = payload.model_dump(exclude_unset=True)
        try:
            row = upsert_underlying_default(session, underlying=underlying, **body)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        record_audit(
            session,
            event_type="underlying_pricing_defaults.upserted",
            actor="desk_user",
            subject_type="underlying_pricing_default",
            subject_id=row.id,
            payload=body,
        )
        session.commit()
        latest = latest_akshare_close_by_underlying(session, [row.underlying])
        inherited = latest_manual_inputs_by_underlying(session, [row.underlying])
        open_symbols = set(open_position_underlying_symbols(session))
        return _serialize_default(row, latest, inherited, open_symbols)

    @app.post(
        "/api/underlying-pricing-defaults/refresh-from-positions",
        response_model=list[UnderlyingPricingDefaultOut],
    )
    def refresh_underlying_pricing_defaults(session: Session = Depends(get_db)):
        rows = refresh_underlying_defaults_from_open_positions(session)
        session.commit()
        latest = latest_akshare_close_by_underlying(
            session, [row.underlying for row in rows]
        )
        inherited = latest_manual_inputs_by_underlying(
            session, [row.underlying for row in rows]
        )
        open_symbols = set(open_position_underlying_symbols(session))
        return [_serialize_default(row, latest, inherited, open_symbols) for row in rows]

    # ------------------------------------------------------------------
    # Assumption sets  (instrument-level r/q/vol, no AKShare fetch)
    # ------------------------------------------------------------------

    @app.post("/api/assumptions/build", response_model=AssumptionSetOut)
    def build_assumptions_endpoint(
        payload: BuildAssumptionsRequest,
        session: Session = Depends(get_db),
    ):
        try:
            assumption_set = build_assumptions_set(
                session,
                name=payload.name,
                valuation_date=payload.valuation_date,
            )
        except ValueError as exc:
            arg = exc.args[0] if exc.args else "build failed"
            if isinstance(arg, dict) and "unfilled_underlyings" in arg:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "detail": "instrument assumptions missing inputs",
                        "unfilled_underlyings": arg["unfilled_underlyings"],
                    },
                )
            raise HTTPException(status_code=400, detail=str(arg))
        record_audit(
            session,
            event_type="assumptions.built",
            actor="desk_user",
            subject_type="assumption_set",
            subject_id=assumption_set.id,
            payload={
                "row_count": assumption_set.summary.get("row_count"),
                "instruments": assumption_set.summary.get("instruments", []),
            },
        )
        session.commit()
        return (
            session.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .filter(AssumptionSet.id == assumption_set.id)
            .one()
        )

    @app.get("/api/assumptions/sets", response_model=list[AssumptionSetOut])
    def list_assumption_sets_endpoint(session: Session = Depends(get_db)):
        from sqlalchemy import desc as sa_desc

        sets = (
            session.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .order_by(sa_desc(AssumptionSet.valuation_date), sa_desc(AssumptionSet.id))
            .all()
        )
        return sets

    @app.get("/api/assumptions/sets/{set_id}", response_model=AssumptionSetOut)
    def get_assumption_set_endpoint(
        set_id: int,
        session: Session = Depends(get_db),
    ):
        assumption_set = (
            session.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .filter(AssumptionSet.id == set_id)
            .one_or_none()
        )
        if assumption_set is None:
            raise HTTPException(status_code=404, detail=f"Assumption set {set_id} not found")
        return assumption_set

    @app.post(
        "/api/pricing-parameter-profiles/import",
        response_model=PricingParameterProfileOut,
    )
    def import_pricing_parameter_profile_endpoint(
        file: UploadFile = File(...),
        sheet_name: str | None = Form(None),
        valuation_date: str | None = Form(None),
        name: str | None = Form(None),
        session: Session = Depends(get_db),
    ):
        upload_path = _store_upload(file, "pricing-parameters")
        try:
            profile = import_pricing_parameter_profile_from_xlsx(
                session,
                xlsx_path=upload_path,
                name=name,
                valuation_date=_parse_optional_datetime(valuation_date),
                sheet_name=sheet_name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="pricing_parameters.imported",
            actor="desk_user",
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={
                "profile_id": profile.id,
                "source_path": str(upload_path),
                "summary": profile.summary,
            },
        )
        session.commit()
        return (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .filter(PricingParameterProfile.id == profile.id)
            .one()
        )

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/price",
        response_model=PositionValuationRunOut,
    )
    def price_portfolio_positions_endpoint(
        portfolio_id: int,
        payload: PositionPriceRequest,
        session: Session = Depends(get_db),
    ):
        if not session.get(Portfolio, portfolio_id):
            raise HTTPException(status_code=404, detail="Portfolio not found")
        has_engine_override = payload.engine_name is not None or bool(
            payload.engine_kwargs
        )
        if has_engine_override and len(payload.position_ids) != 1:
            raise HTTPException(
                status_code=400,
                detail="Engine overrides require exactly one position_id",
            )
        try:
            run = price_portfolio_positions(
                session,
                portfolio_id=portfolio_id,
                position_ids=payload.position_ids or None,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                engine_config_id=payload.engine_config_id,
                valuation_date=payload.valuation_date,
                overrides=MarketOverrides(
                    spot=payload.spot,
                    rate=payload.rate if payload.rate is not None else payload.r,
                    dividend_yield=(
                        payload.dividend_yield
                        if payload.dividend_yield is not None
                        else payload.q
                    ),
                    volatility=(
                        payload.volatility
                        if payload.volatility is not None
                        else payload.vol
                    ),
                ),
                engine_name=payload.engine_name,
                engine_kwargs=payload.engine_kwargs if has_engine_override else None,
                compute_greeks=payload.compute_greeks,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="positions.priced",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=portfolio_id,
            payload={
                "valuation_run_id": run.id,
                "status": run.status,
                "summary": run.summary,
            },
        )
        session.commit()
        persisted_run = (
            session.query(PositionValuationRun)
            .options(
                selectinload(PositionValuationRun.results).selectinload(
                    PositionValuationResult.position
                )
            )
            .filter(PositionValuationRun.id == run.id)
            .one()
        )
        return _valuation_run_out(persisted_run)

    @app.post("/api/pricing/preview", response_model=PricingPreviewOut)
    def pricing_preview_endpoint(payload: PricingPreviewRequest):
        result = price_product_preview(
            product_type=payload.product_type,
            product_kwargs=payload.product_kwargs,
            market=payload.market,
            engine_name=payload.engine_name,
            engine_kwargs=payload.engine_kwargs or None,
            compute_greeks=payload.compute_greeks,
        )
        data = result.data or {}
        raw_greeks = data.get("greeks")
        greeks = PricingGreeks(**raw_greeks) if raw_greeks else None
        return PricingPreviewOut(
            ok=bool(result.ok),
            price=float(data.get("price", 0.0)),
            engine=str(data.get("engine", payload.engine_name)),
            product_type=str(data.get("product_type", payload.product_type)),
            greeks=greeks,
            greeks_error=data.get("greeks_error"),
            error=result.error,
        )

    @app.get(
        "/api/portfolios/{portfolio_id}/runs",
        response_model=list[PositionValuationRunOut],
    )
    def list_portfolio_valuation_runs(
        portfolio_id: int, session: Session = Depends(get_db)
    ):
        runs = (
            session.query(PositionValuationRun)
            .options(
                selectinload(PositionValuationRun.results).selectinload(
                    PositionValuationResult.position
                )
            )
            .filter(PositionValuationRun.portfolio_id == portfolio_id)
            .order_by(PositionValuationRun.created_at.desc())
            .all()
        )
        return [_valuation_run_out(run) for run in runs]

    @app.get(
        "/api/position-valuation-runs/{run_id}/results",
        response_model=list[PositionValuationResultOut],
    )
    def list_position_valuation_results(
        run_id: int, session: Session = Depends(get_db)
    ):
        run = (
            session.query(PositionValuationRun)
            .options(
                selectinload(PositionValuationRun.results).selectinload(
                    PositionValuationResult.position
                )
            )
            .filter(PositionValuationRun.id == run_id)
            .one_or_none()
        )
        if not run:
            raise HTTPException(status_code=404, detail="Valuation run not found")
        return _valuation_run_out(run).results

    @app.patch(
        "/api/portfolios/{portfolio_id}/positions/{position_id}",
        response_model=PortfolioOut,
    )
    def patch_position(
        portfolio_id: int,
        position_id: int,
        payload: PortfolioPositionSpec,
        session: Session = Depends(get_db),
    ):
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        if portfolio.kind != PortfolioKind.CONTAINER.value:
            raise HTTPException(
                status_code=400,
                detail="Position management is only available for container portfolios",
            )
        position = session.get(Position, position_id)
        if not position or position.portfolio_id != portfolio_id:
            raise HTTPException(status_code=404, detail="Position not found")
        data = payload.model_dump(mode="json")
        try:
            product_spec = _booking_product_from_payload(payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if payload.product is not None:
            product = create_or_get_product(
                session,
                product_spec,
                reuse=True,
            )
            position.product = product
            position.product_id = product.id
            hydrate_position_product_fields(position)
            link_position_underlying(session, position, source="manual")
            for key in {
                "engine_name",
                "engine_kwargs",
                "quantity",
                "entry_price",
                "status",
                "position_kind",
                "source_trade_id",
                "trade_effective_date",
            }:
                setattr(position, key, data[key])
            reset_position_term_rows(session, position.id)
            upsert_position_term_rows(session, position)
            session.flush()
            refresh_position_barrier_state(session, position_id=position.id)
        else:
            editable_fields = {
                "underlying", "product_type", "product_kwargs", "engine_name",
                "engine_kwargs", "quantity", "entry_price", "status",
                "source_trade_id", "trade_effective_date", "position_kind",
            }
            for key, value in data.items():
                if key in editable_fields:
                    setattr(position, key, value)
            product = create_or_get_product(session, product_spec, reuse=True)
            position.product = product
            position.product_id = product.id
            hydrate_position_product_fields(position)
            link_position_underlying(session, position, source="manual")
            reset_position_term_rows(session, position.id)
            upsert_position_term_rows(session, position)
            session.flush()
            refresh_position_barrier_state(session, position_id=position.id)
        # Currency precedence: explicit payload wins; otherwise a product
        # replacement re-derives it from the booked product (same provenance
        # rule as booking). Fields-only patches without currency leave it alone.
        if payload.currency is not None:
            position.currency = payload.currency
        elif payload.product is not None:
            set_position_currency(position)
        portfolio.updated_at = datetime.utcnow()
        record_audit(
            session,
            event_type="position.updated",
            actor="desk_user",
            subject_type="position",
            subject_id=position.id,
            payload=data,
        )
        session.commit()
        return _portfolio_response(session, portfolio)

    @app.get(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/pricing-params",
        response_model=ResolvedPricingParamsOut,
    )
    def get_position_pricing_params(
        portfolio_id: int,
        position_id: int,
        pricing_parameter_profile_id: int | None = Query(default=None),
        session: Session = Depends(get_db),
    ):
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        position = session.get(Position, position_id)
        if not position or position.portfolio_id != portfolio_id:
            raise HTTPException(status_code=404, detail="Position not found")
        return resolve_position_pricing_params(
            session,
            position=position,
            pricing_parameter_profile_id=pricing_parameter_profile_id,
            as_of=datetime.utcnow(),
        )

    @app.get(
        "/api/portfolios/{portfolio_id}/lifecycle-events",
        response_model=list[PositionLifecycleEventOut],
    )
    def list_portfolio_lifecycle_events(
        portfolio_id: int,
        session: Session = Depends(get_db),
    ):
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        try:
            position_ids = resolve_position_ids(portfolio, session)
        except (PortfolioCycleError, PortfolioDepthError):
            position_ids = []
        if not position_ids:
            return []
        return (
            session.query(PositionLifecycleEvent)
            .filter(PositionLifecycleEvent.position_id.in_(position_ids))
            .order_by(
                PositionLifecycleEvent.position_id.asc(),
                PositionLifecycleEvent.created_at.desc(),
                PositionLifecycleEvent.id.desc(),
            )
            .all()
        )

    @app.get(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/lifecycle-events",
        response_model=list[PositionLifecycleEventOut],
    )
    def list_lifecycle_events(
        portfolio_id: int,
        position_id: int,
        session: Session = Depends(get_db),
    ):
        portfolio = session.get(Portfolio, portfolio_id)
        if portfolio is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        position = session.get(Position, position_id)
        if not position or position.portfolio_id != portfolio_id:
            raise HTTPException(status_code=404, detail="Position not found")
        return position.lifecycle_events

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/lifecycle-events",
        response_model=PositionLifecycleEventOut,
    )
    def create_lifecycle_event(
        portfolio_id: int,
        position_id: int,
        payload: PositionLifecycleEventIn,
        session: Session = Depends(get_db),
    ):
        try:
            update = positions_svc.create_lifecycle_event(
                portfolio_id=portfolio_id,
                position_id=position_id,
                event_type=payload.event_type,
                event_data=payload.event_data,
                actor="desk_user",
                session=session,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )
        return update.event

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/asian-fixing-schedule",
    )
    def generate_asian_fixing_schedule(
        portfolio_id: int,
        position_id: int,
        session: Session = Depends(get_db),
    ):
        """Generate `fixing` lifecycle events from the Asian averaging schedule."""
        try:
            count = positions_svc.generate_asian_fixing_schedule(
                portfolio_id=portfolio_id,
                position_id=position_id,
                actor="desk_user",
                session=session,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"position_id": position_id, "events_created": count}

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/{position_id}/asian-fixings/capture",
    )
    def capture_asian_fixings(
        portfolio_id: int,
        position_id: int,
        session: Session = Depends(get_db),
    ):
        """Capture observed prices for any due (past) Asian fixings."""
        try:
            captured = positions_svc.capture_due_asian_fixings(
                session, position_id, portfolio_id=portfolio_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"position_id": position_id, "captured": captured}

    @app.post(
        "/api/portfolios/{portfolio_id}/positions/{position_id}"
        "/lifecycle-events/{event_id}/cancel",
        response_model=PositionLifecycleEventOut,
    )
    def cancel_lifecycle_event(
        portfolio_id: int,
        position_id: int,
        event_id: int,
        payload: PositionLifecycleEventCancelIn,
        session: Session = Depends(get_db),
    ):
        try:
            update = positions_svc.cancel_lifecycle_event(
                portfolio_id=portfolio_id,
                position_id=position_id,
                lifecycle_event_id=event_id,
                reason=payload.reason,
                actor="desk_user",
                session=session,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail=str(exc),
            )
        return update.event

    @app.post("/api/batch-pricing/runs", response_model=RiskRunOut)
    def create_batch_pricing_run(
        payload: BatchPricingRunRequest, session: Session = Depends(get_db)
    ):
        from .services.batch_pricing import (
            execute_batch_pricing_task,
            queue_batch_pricing,
        )

        try:
            run, task = queue_batch_pricing(
                session,
                portfolio_id=payload.portfolio_id,
                position_ids=payload.position_ids,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                engine_config_id=payload.engine_config_id,
                market_snapshot_id=payload.market_snapshot_id,
            )
        except ValueError as exc:
            status_code = 404 if "Portfolio not found" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="batch_pricing.queued",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=run.portfolio_id,
            payload={
                "risk_run_id": run.id,
                "task_id": task.id,
                "position_ids": run.resolved_position_ids,
                "pricing_parameter_profile_id": run.pricing_parameter_profile_id,
                "engine_config_id": run.engine_config_id,
            },
        )
        session.commit()
        submit_async_task(
            execute_batch_pricing_task,
            task.id,
            run.id,
            database.SessionLocal,
            settings=active_settings,
        )
        return _risk_run_out(run)

    @app.get("/api/risk/runs/{run_id}", response_model=RiskRunOut)
    def get_risk_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(RiskRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Risk run not found")
        return _risk_run_out(run)

    @app.post("/api/greeks-landscape/runs", response_model=GreekLandscapeRunOut)
    def create_greeks_landscape_run(
        payload: GreekLandscapeRunRequest, session: Session = Depends(get_db)
    ):
        from .services.greeks_landscape import (
            execute_greeks_landscape_task,
            queue_greeks_landscape,
        )

        try:
            run, task = queue_greeks_landscape(
                session,
                portfolio_id=payload.portfolio_id,
                position_ids=payload.position_ids,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                engine_config_id=payload.engine_config_id,
                spot_min_pct=payload.spot_min_pct,
                spot_max_pct=payload.spot_max_pct,
                spot_nodes=payload.spot_nodes,
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        session.commit()
        submit_async_task(
            execute_greeks_landscape_task,
            task.id,
            run.id,
            database.SessionLocal,
            settings=active_settings,
        )
        return _greeks_landscape_run_out(run)

    @app.get(
        "/api/greeks-landscape/runs", response_model=list[GreekLandscapeRunOut]
    )
    def list_greeks_landscape_runs(
        portfolio_id: int = Query(...), session: Session = Depends(get_db)
    ):
        if session.get(Portfolio, portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        runs = (
            session.query(GreekLandscapeRun)
            .filter(GreekLandscapeRun.portfolio_id == portfolio_id)
            .order_by(GreekLandscapeRun.created_at.desc(), GreekLandscapeRun.id.desc())
            .all()
        )
        return [_greeks_landscape_run_out(r) for r in runs]

    @app.get(
        "/api/greeks-landscape/runs/{run_id}", response_model=GreekLandscapeRunOut
    )
    def get_greeks_landscape_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(GreekLandscapeRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Greeks landscape run not found")
        return _greeks_landscape_run_out(run)

    @app.get(
        "/api/portfolios/{portfolio_id}/greeks-landscape-runs/latest",
        response_model=GreekLandscapeRunOut | None,
    )
    def get_latest_greeks_landscape_run(
        portfolio_id: int, session: Session = Depends(get_db)
    ):
        if session.get(Portfolio, portfolio_id) is None:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        run = (
            session.query(GreekLandscapeRun)
            .filter(GreekLandscapeRun.portfolio_id == portfolio_id)
            .order_by(GreekLandscapeRun.created_at.desc(), GreekLandscapeRun.id.desc())
            .first()
        )
        return _greeks_landscape_run_out(run) if run is not None else None

    @app.get(
        "/api/portfolios/{portfolio_id}/risk-runs/latest",
        response_model=RiskRunOut | None,
    )
    def get_latest_risk_run(portfolio_id: int, session: Session = Depends(get_db)):
        if not session.get(Portfolio, portfolio_id):
            raise HTTPException(status_code=404, detail="Portfolio not found")
        run = (
            session.query(RiskRun)
            .filter(RiskRun.portfolio_id == portfolio_id)
            .order_by(RiskRun.created_at.desc(), RiskRun.id.desc())
            .first()
        )
        return _risk_run_out(run) if run is not None else None

    @app.get("/api/tasks", response_model=list[TaskRunOut])
    def list_tasks(
        kind: str | None = None,
        status: str | None = None,
        portfolio_id: int | None = None,
        limit: int = 100,
        session: Session = Depends(get_db),
    ):
        query = session.query(TaskRun)
        if kind:
            query = query.filter(TaskRun.kind == kind)
        if status:
            query = query.filter(TaskRun.status == status)
        if portfolio_id is not None:
            query = query.filter(TaskRun.portfolio_id == portfolio_id)
        tasks = (
            query.order_by(TaskRun.created_at.desc(), TaskRun.id.desc())
            .limit(min(max(1, limit), 500))
            .all()
        )
        return [_task_run_out(task) for task in tasks]

    @app.get("/api/tasks/{task_id}", response_model=TaskRunOut)
    def get_task(task_id: int, session: Session = Depends(get_db)):
        task = session.get(TaskRun, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return _task_run_out(task)

    # ------------------------------------------------------------------
    # Hedging endpoints
    # ------------------------------------------------------------------

    @app.get("/api/hedging/underlyings", response_model=list[HedgeUnderlyingOut])
    def hedging_underlyings(session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        return hedging_domain.underlyings_overview(session)

    @app.get("/api/hedging/instruments", response_model=list[HedgeInstrumentOut])
    def hedging_instruments(
        underlying_id: int,
        family: str | None = None,
        instrument_type: str | None = None,
        option_type: str | None = None,
        strike_min: float | None = None,
        strike_max: float | None = None,
        search: str | None = None,
        allowed_only: bool = False,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        session: Session = Depends(get_db),
    ):
        from .services.domains import hedging as hedging_domain
        return hedging_domain.list_instruments(
            session, underlying_id=underlying_id, family=family,
            instrument_type=instrument_type, option_type=option_type,
            strike_min=strike_min, strike_max=strike_max, search=search,
            allowed_only=allowed_only, status=status, limit=limit, offset=offset,
        )

    @app.post("/api/hedging/map/mark", response_model=HedgeMarkResultOut)
    def hedging_mark(payload: HedgeMarkRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        created = hedging_domain.mark(session, payload.instrument_ids, actor="desk_user")
        session.commit()
        return {"marked": len(created)}

    @app.post("/api/hedging/map/unmark", response_model=HedgeRemovalResultOut)
    def hedging_unmark(payload: HedgeUnmarkRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        removed = hedging_domain.unmark(
            session,
            instrument_ids=payload.instrument_ids,
            map_entry_ids=payload.map_entry_ids,
        )
        session.commit()
        return {"removed": removed}

    @app.get("/api/hedging/map", response_model=list[HedgeMapGroupOut])
    def hedging_map(underlying_id: int | None = None, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        return hedging_domain.get_map(session, underlying_id=underlying_id)

    @app.post("/api/hedging/map/purge-stale", response_model=HedgeRemovalResultOut)
    def hedging_purge_stale(underlying_id: int, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        removed = hedging_domain.purge_stale(session, underlying_id=underlying_id)
        session.commit()
        return {"removed": removed}

    @app.post("/api/hedging/instruments/load", response_model=HedgeLoadStartedOut)
    def hedging_load(session: Session = Depends(get_db)):
        from .services import hedging_loader
        # lazy import so tests can monkeypatch submit_async_task (keeps the load hermetic)
        from .services.task_runner import submit_async_task
        try:
            task = hedging_loader.queue_hedge_load(session)
        except hedging_loader.HedgeLoadInProgress as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "A hedge instrument load is already running.",
                    "in_flight_task_id": exc.task_id,
                },
            )
        session.commit()
        submit_async_task(
            hedging_loader.execute_hedge_load_task,
            task.id,
            database.SessionLocal,
        )
        return {"task_id": task.id}

    @app.get("/api/hedging/instruments/load/{task_id}", response_model=HedgeLoadStatusOut)
    def hedging_load_status(task_id: int, session: Session = Depends(get_db)):
        task = session.get(TaskRun, task_id)
        if task is None or task.kind != TaskKind.HEDGE_LOAD.value:
            raise HTTPException(status_code=404, detail="Hedge load not found")
        return {
            "task_id": task.id,
            "status": task.status,
            "progress_current": task.progress_current,
            "progress_total": task.progress_total,
            "message": task.message,
            "summary": task.result_payload,
        }

    @app.get("/api/hedging/hedgeable")
    def hedging_hedgeable(portfolio_id: int, session: Session = Depends(get_db)):
        from .services import hedging_greeks
        return hedging_greeks.aggregate_by_underlying(session, portfolio_id=portfolio_id)

    @app.get("/api/hedging/bands", response_model=HedgeBandsOut)
    def hedging_get_bands(underlying_id: int | None = None,
                          session: Session = Depends(get_db)):
        # underlying_id omitted addresses the portfolio-wide defaults row directly.
        from .services.domains import hedging_strategy as hs
        return hs.resolve_bands(session, underlying_id=underlying_id)

    @app.put("/api/hedging/bands", response_model=HedgeBandsOut)
    def hedging_put_default_bands(payload: HedgeBandsIn,
                                  session: Session = Depends(get_db)):
        # Set the portfolio-wide defaults row (underlying_id IS NULL).
        from .services.domains import hedging_strategy as hs
        hs.set_bands(session, underlying_id=None,
                     bands=payload.model_dump(), actor="desk_user")
        session.commit()
        return hs.resolve_bands(session, underlying_id=None)

    @app.put("/api/hedging/bands/{underlying_id}", response_model=HedgeBandsOut)
    def hedging_put_bands(underlying_id: int, payload: HedgeBandsIn,
                          session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        hs.set_bands(session, underlying_id=underlying_id,
                     bands=payload.model_dump(), actor="desk_user")
        session.commit()
        return hs.resolve_bands(session, underlying_id=underlying_id)

    @app.post("/api/hedging/solve")
    def hedging_solve(payload: HedgeSolveRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        return hs.solve_hedge(session, portfolio_id=payload.portfolio_id,
                              underlying=payload.underlying, strategy=payload.strategy,
                              legs=payload.legs, bands=payload.bands)

    @app.post("/api/hedging/book")
    def hedging_book(payload: HedgeBookRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        try:
            out = hs.book_hedge(session, portfolio_id=payload.portfolio_id,
                                underlying=payload.underlying, risk_run_id=payload.risk_run_id,
                                strategy=payload.strategy, legs=payload.legs, spot=payload.spot)
        except Exception:
            session.rollback()
            raise
        session.commit()
        return out

    @app.post("/api/risk/scenarios", response_model=ScenarioRunOut)
    def create_risk_scenario(
        payload: ScenarioRunRequest, session: Session = Depends(get_db)
    ):
        portfolio = (
            session.query(Portfolio)
            .options(selectinload(Portfolio.positions))
            .filter(Portfolio.id == payload.portfolio_id)
            .one_or_none()
        )
        if not portfolio:
            raise HTTPException(status_code=404, detail="Portfolio not found")
        market = PricingEnvironmentSnapshot()  # default snapshot for now
        from .services.risk_engine import run_portfolio_scenarios

        result = run_portfolio_scenarios(
            portfolio,
            market,
            payload.spot_shifts_pct,
            payload.vol_shifts_abs,
        )
        risk_run = None
        if payload.risk_run_id is not None:
            risk_run = session.get(RiskRun, payload.risk_run_id)
            if risk_run and risk_run.portfolio_id != portfolio.id:
                risk_run = None
        if risk_run is None:
            risk_run = (
                session.query(RiskRun)
                .filter(RiskRun.portfolio_id == portfolio.id)
                .order_by(RiskRun.created_at.desc(), RiskRun.id.desc())
                .first()
            )
        if risk_run is not None:
            risk_run.scenario_cells = result["cells"]
        record_audit(
            session,
            event_type="risk.scenario",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=portfolio.id,
            payload={
                "risk_run_id": risk_run.id if risk_run is not None else None,
                "shifts": {
                    "spot": payload.spot_shifts_pct,
                    "vol": payload.vol_shifts_abs,
                },
            },
        )
        session.commit()
        return result

    # ------------------------------------------------------------------
    # Scenario-test endpoints
    # ------------------------------------------------------------------

    @app.get("/api/scenario-test/library", response_model=ScenarioLibraryOut)
    def scenario_test_library():
        from .services.domains import scenario_catalog

        return ScenarioLibraryOut(
            predefined=scenario_catalog.list_predefined(),
            saved_sets=scenario_catalog.list_sets(),
        )

    @app.get("/api/scenario-test/sets", response_model=list[ScenarioSetDetailOut])
    def scenario_test_list_sets():
        from .services.domains import scenario_catalog

        return [ScenarioSetDetailOut(**d) for d in scenario_catalog.list_sets_detailed()]

    # NOTE: /sets/full and /sets/generate MUST precede /sets/{name} — FastAPI
    # matches in declaration order, so "full" would otherwise bind as {name}.
    @app.get("/api/scenario-test/sets/full", response_model=list[ScenarioSetSummaryOut])
    def scenario_test_list_sets_full():
        from .services.domains import scenario_catalog

        return [ScenarioSetSummaryOut(**d) for d in scenario_catalog.list_sets_full()]

    @app.post("/api/scenario-test/sets/generate", response_model=ScenarioGridSavedOut)
    def scenario_test_generate_set(payload: ScenarioGridRequest):
        from .services.domains import scenario_catalog

        spec = payload.model_dump()
        try:
            specs = scenario_catalog.generate_grid(spec)
            scenarios = [scenario_catalog.build_custom(s) for s in specs]
            path = scenario_catalog.save_set(payload.name, scenarios, grid_spec=spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ScenarioGridSavedOut(name=payload.name, num_scenarios=len(scenarios), path=path)

    @app.get("/api/scenario-test/sets/{name}", response_model=ScenarioSetDetailOut)
    def scenario_test_get_set(name: str):
        from .services.domains import scenario_catalog

        try:
            return ScenarioSetDetailOut(**scenario_catalog.get_set(name))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/api/scenario-test/sets/{name}/scenarios",
        response_model=list[ScenarioSpec],
    )
    def scenario_test_get_set_scenarios(name: str):
        from .services.domains import scenario_catalog

        try:
            return [ScenarioSpec(**s) for s in scenario_catalog.list_set_specs(name)]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/api/scenario-test/sets/{name}")
    def scenario_test_delete_set(name: str):
        from .services.domains import scenario_catalog

        try:
            scenario_catalog.delete_set(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"ok": True, "name": name}

    @app.post("/api/scenario-test/sets", response_model=ScenarioSetSavedOut)
    def scenario_test_save_set(payload: dict):
        from .services.domains import scenario_catalog

        try:
            name = payload["name"]
            scenarios = [scenario_catalog.build_custom(s) for s in payload.get("custom", [])]
            path = scenario_catalog.save_set(name, scenarios)
        except KeyError as exc:
            raise HTTPException(status_code=422, detail="Missing required field: 'name'") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return ScenarioSetSavedOut(name=name, path=path)

    @app.post("/api/scenario-test/runs", response_model=ScenarioTestRunOut)
    def scenario_test_create_run(
        payload: ScenarioTestRunRequest, session: Session = Depends(get_db)
    ):
        from .services import scenario_test_runner
        from .services.domains import scenario_catalog as _scenario_catalog

        expanded_custom = [c.model_dump() for c in payload.custom]
        try:
            for _set_name in payload.scenario_sets:
                expanded_custom.extend(_scenario_catalog.list_set_specs(_set_name))
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        scenario_request = {
            "predefined": payload.predefined,
            "custom": expanded_custom,
            "scenario_set": payload.scenario_set,
        }
        try:
            run, _task = scenario_test_runner.queue_scenario_test(
                session,
                portfolio_id=payload.portfolio_id,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                engine_config_id=payload.engine_config_id,
                scenario_request=scenario_request,
                config=payload.config.model_dump(),
                position_ids=payload.position_ids,
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        session.refresh(run)
        return ScenarioTestRunOut.model_validate(run)

    @app.get("/api/scenario-test/runs", response_model=list[ScenarioTestRunOut])
    def scenario_test_list_runs(
        portfolio_id: int = Query(...), session: Session = Depends(get_db)
    ):
        runs = (
            session.query(ScenarioTestRun)
            .filter(ScenarioTestRun.portfolio_id == portfolio_id)
            .order_by(ScenarioTestRun.created_at.desc(), ScenarioTestRun.id.desc())
            .all()
        )
        return [ScenarioTestRunOut.model_validate(r) for r in runs]

    @app.get("/api/scenario-test/runs/{run_id}", response_model=ScenarioTestRunOut)
    def scenario_test_get_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(ScenarioTestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="ScenarioTestRun not found")
        return ScenarioTestRunOut.model_validate(run)

    @app.get("/api/scenario-test/runs/{run_id}/artifacts/{name}")
    def scenario_test_get_artifact(
        run_id: int,
        name: str,
        download: bool = Query(False),
        session: Session = Depends(get_db),
    ):
        """Download or preview a single artifact recorded for a scenario test run.

        Only files explicitly recorded in the run's artifacts dict can be served
        (whitelist approach — prevents path traversal and arbitrary file reads).
        The `name` parameter is matched against os.path.basename of each recorded
        path; it is never joined onto a directory.
        """
        import os

        run = session.get(ScenarioTestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="ScenarioTestRun not found")
        artifacts = run.artifacts or {}
        recorded_paths: list[str] = []
        if artifacts.get("report_html_path"):
            recorded_paths.append(artifacts["report_html_path"])
        recorded_paths.extend(artifacts.get("export_paths", []))
        matched = next(
            (p for p in recorded_paths if os.path.basename(p) == name),
            None,
        )
        if matched is None:
            raise HTTPException(status_code=404, detail="Artifact not found in run")
        if not os.path.isfile(matched):
            raise HTTPException(status_code=404, detail="Artifact file not found on disk")
        return FileResponse(
            matched,
            filename=name,
            content_disposition_type="attachment" if download else "inline",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # ------------------------------------------------------------------
    # Backtest endpoints
    # ------------------------------------------------------------------

    @app.post("/api/backtest/runs", response_model=BacktestRunOut)
    def backtest_create_run(payload: BacktestRunRequest, session: Session = Depends(get_db)):
        from .services import backtest_runner

        try:
            run, _task = backtest_runner.queue_backtest(
                session,
                portfolio_id=payload.portfolio_id,
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
                engine_config_id=payload.engine_config_id,
                spec=payload.spec.model_dump(),
                config=payload.config.model_dump(),
                position_ids=payload.position_ids,
            )
        except ValueError as exc:
            status_code = 404 if "not found" in str(exc).lower() else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        session.refresh(run)
        return BacktestRunOut.model_validate(run)

    @app.get("/api/backtest/runs", response_model=list[BacktestRunOut])
    def backtest_list_runs(portfolio_id: int = Query(...), session: Session = Depends(get_db)):
        runs = (
            session.query(BacktestRun)
            .filter(BacktestRun.portfolio_id == portfolio_id)
            .order_by(BacktestRun.created_at.desc(), BacktestRun.id.desc())
            .all()
        )
        return [BacktestRunOut.model_validate(r) for r in runs]

    @app.get("/api/backtest/runs/{run_id}", response_model=BacktestRunOut)
    def backtest_get_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="BacktestRun not found")
        return BacktestRunOut.model_validate(run)

    @app.get("/api/backtest/runs/{run_id}/artifacts/{name}")
    def backtest_get_artifact(
        run_id: int,
        name: str,
        download: bool = Query(False),
        session: Session = Depends(get_db),
    ):
        """Download a single artifact recorded for a backtest run.

        Only files explicitly recorded in the run's artifacts dict can be served
        (whitelist approach — prevents path traversal and arbitrary file reads).
        The `name` parameter is matched against os.path.basename of each recorded
        path; it is never joined onto a directory.
        """
        import os

        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="BacktestRun not found")
        artifacts = run.artifacts or {}
        recorded: list[str] = list(artifacts.get("export_paths", []))
        for key in ("report_html_path", "dashboard_html"):
            if artifacts.get(key):
                recorded.append(artifacts[key])
        for v in (artifacts.get("dashboards", {}) or {}).values():
            recorded.append(v)
        matched = next((p for p in recorded if os.path.basename(p) == name), None)
        if matched is None:
            raise HTTPException(status_code=404, detail="Artifact not found in run")
        if not os.path.isfile(matched):
            raise HTTPException(status_code=404, detail="Artifact file not found on disk")
        return FileResponse(
            matched,
            filename=name,
            content_disposition_type="attachment" if download else "inline",
        )

    @app.delete("/api/backtest/runs/{run_id}")
    def backtest_delete_run(run_id: int, session: Session = Depends(get_db)):
        run = session.get(BacktestRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="BacktestRun not found")
        session.delete(run)
        session.commit()
        return {"deleted": run_id}

    @app.post("/api/reports/jobs", response_model=ReportJobOut)
    def create_report(payload: ReportJobCreate, session: Session = Depends(get_db)):
        job, task = queue_report_job(session, payload)
        record_audit(
            session,
            event_type="report.queued",
            actor="desk_user",
            subject_type="report",
            subject_id=job.id,
            payload=payload.model_dump(mode="json") | {"task_id": task.id},
        )
        session.commit()
        submit_async_task(
            execute_report_job_task,
            task.id,
            job.id,
            active_settings,
            database.SessionLocal,
            settings=active_settings,
        )
        return _report_job_out(job)

    @app.get("/api/reports/jobs", response_model=list[ReportJobOut])
    def list_reports(session: Session = Depends(get_db)):
        from .models import ReportJob

        jobs = (
            session.query(ReportJob)
            .order_by(ReportJob.created_at.desc(), ReportJob.id.desc())
            .all()
        )
        return [_report_job_out(job) for job in jobs]

    @app.get("/api/reports/jobs/{job_id}", response_model=ReportJobOut)
    def get_report(job_id: int, session: Session = Depends(get_db)):
        from .models import ReportJob

        job = session.get(ReportJob, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Report job not found")
        return _report_job_out(job)

    @app.get("/api/audit/events", response_model=list[AuditEventOut])
    def list_audit_events(
        event_type: str | None = None,
        subject_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 100,
        session: Session = Depends(get_db),
    ):
        from .models import AuditEvent

        query = session.query(AuditEvent)
        if event_type:
            query = query.filter(AuditEvent.event_type == event_type)
        if subject_type:
            query = query.filter(AuditEvent.subject_type == subject_type)
        if subject_id:
            query = query.filter(AuditEvent.subject_id == str(subject_id))
        return (
            query.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(min(limit, 500))
            .all()
        )

    @app.post("/api/market-data/snapshots/manual", response_model=MarketSnapshotOut)
    def create_manual_snapshot(
        payload: MarketDataSnapshot, session: Session = Depends(get_db)
    ):
        snapshot = MarketSnapshot(**payload.model_dump())
        session.add(snapshot)
        session.flush()
        record_audit(
            session,
            event_type="market.snapshot.manual",
            actor="desk_user",
            subject_type="market_snapshot",
            subject_id=snapshot.id,
            payload=payload.model_dump(mode="json"),
        )
        session.commit()
        return snapshot

    @app.post("/api/market-data/snapshots/akshare", response_model=MarketSnapshotOut)
    def create_akshare_snapshot(
        payload: AkshareSnapshotRequest, session: Session = Depends(get_db)
    ):
        normalized = fetch_akshare_snapshot(payload)
        snapshot = MarketSnapshot(**normalized.model_dump())
        session.add(snapshot)
        session.flush()
        record_audit(
            session,
            event_type="market.snapshot.akshare",
            actor="desk_user",
            subject_type="market_snapshot",
            subject_id=snapshot.id,
            payload=normalized.source_metadata,
        )
        session.commit()
        return snapshot

    @app.get("/api/market-data/profiles", response_model=list[MarketDataProfileOut])
    def list_market_data_profiles(session: Session = Depends(get_db)):
        return (
            session.query(MarketDataProfile)
            .order_by(
                MarketDataProfile.valuation_date.desc(),
                MarketDataProfile.created_at.desc(),
            )
            .all()
        )

    @app.get(
        "/api/market-data/profiles/{profile_id}", response_model=MarketDataProfileOut
    )
    def get_market_data_profile(profile_id: int, session: Session = Depends(get_db)):
        profile = session.get(MarketDataProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Market data profile not found")
        return profile

    @app.post("/api/market-data/profiles/akshare", response_model=MarketDataProfileOut)
    def create_akshare_market_data_profile(
        payload: AkshareSnapshotRequest, session: Session = Depends(get_db)
    ):
        normalized = fetch_akshare_snapshot(payload)
        underlying = ensure_underlying(session, normalized.symbol, source="market_data")
        profile = _market_profile_from_snapshot(normalized, payload)
        profile.underlying_id = underlying.id
        session.add(profile)
        session.flush()
        _emit_profile_quote(session, profile, underlying.id)
        fx_payload = _fx_rate_payload_from_profile(profile)
        fx_row_id = None
        if fx_payload is not None:
            fx_row = fx_service.create_fx_rate(session, fx_payload)
            fx_row_id = fx_row.id
        record_audit(
            session,
            event_type="market_data.profile.akshare",
            actor="desk_user",
            subject_type="market_data_profile",
            subject_id=profile.id,
            payload={
                "symbol": profile.symbol,
                "source_metadata": profile.source_metadata,
                "fx_rate_id": fx_row_id,
            },
        )
        session.commit()
        return profile

    @app.post(
        "/api/market-data/profiles/akshare/bulk",
        response_model=list[MarketDataProfileOut],
    )
    def create_akshare_market_data_profiles_for_positions(
        payload: AkshareBulkSnapshotRequest,
        session: Session = Depends(get_db),
    ):
        underlyings = active_market_data_underlyings(session)
        if not underlyings:
            raise HTTPException(
                status_code=400, detail="No managed underlyings available"
            )

        profiles: list[MarketDataProfile] = []
        for underlying in underlyings:
            request = AkshareSnapshotRequest(
                symbol=underlying.akshare_symbol or akshare_symbol(underlying.symbol),
                asset_class=effective_akshare_asset_class(
                    underlying.symbol,
                    underlying.akshare_asset_class or akshare_asset_class(underlying.symbol),
                ),  # type: ignore[arg-type]
                start_date=payload.start_date,
                end_date=payload.end_date,
                adjust=payload.adjust,
                name=(
                    f"{payload.name.strip()} {underlying.symbol}"
                    if payload.name and payload.name.strip()
                    else f"{underlying.symbol} AKShare snapshot"
                ),
            )
            normalized = fetch_akshare_snapshot(request)
            profile = _market_profile_from_snapshot(
                normalized, request, display_symbol=underlying.symbol
            )
            profile.underlying_id = underlying.id
            profile.source_metadata = {
                **(profile.source_metadata or {}),
                "position_underlying": underlying.symbol,
                "underlying_symbol": underlying.symbol,
                "akshare_symbol": request.symbol,
                "bulk_fetch": True,
            }
            session.add(profile)
            session.flush()
            _emit_profile_quote(session, profile, underlying.id)
            fx_payload = _fx_rate_payload_from_profile(profile)
            if fx_payload is not None:
                fx_row = fx_service.create_fx_rate(session, fx_payload)
                profile.source_metadata = {
                    **(profile.source_metadata or {}),
                    "fx_rate_id": fx_row.id,
                }
            profiles.append(profile)

        session.flush()
        record_audit(
            session,
            event_type="market_data.profile.akshare_bulk",
            actor="desk_user",
            subject_type="market_data_profile",
            subject_id="bulk",
            payload={
                "underlying_count": len(underlyings),
                "profile_ids": [profile.id for profile in profiles],
                "symbols": [underlying.symbol for underlying in underlyings],
            },
        )
        session.commit()
        return profiles

    @app.get("/api/market-data/fx-rates", response_model=list[FxRateOut])
    def list_fx_rates_endpoint(session: Session = Depends(get_db)):
        return fx_service.list_fx_rates(session)

    @app.post("/api/market-data/fx-rates", response_model=FxRateOut)
    def create_fx_rate_endpoint(payload: FxRateCreate, session: Session = Depends(get_db)):
        row = fx_service.create_fx_rate(session, payload)
        record_audit(session, event_type="market_data.fx_rate.manual", actor="desk_user",
                     subject_type="fx_rate", subject_id=row.id,
                     payload={"pair": f"{row.base_currency}{row.quote_currency}", "rate": row.rate})
        session.commit()
        session.refresh(row)
        return row

    @app.post("/api/market-data/fx-rates/akshare", response_model=FxRateOut)
    def create_fx_rate_akshare_endpoint(payload: FxRateAkshareRequest, session: Session = Depends(get_db)):
        from datetime import datetime as _dt
        rate = fx_service.fetch_akshare_fx_rate(payload.base_currency, payload.quote_currency)
        row = fx_service.create_fx_rate(session, FxRateCreate(
            base_currency=payload.base_currency, quote_currency=payload.quote_currency,
            rate=rate, as_of_date=payload.as_of_date or _dt.utcnow(), source="akshare"))
        record_audit(session, event_type="market_data.fx_rate.akshare", actor="desk_user",
                     subject_type="fx_rate", subject_id=row.id,
                     payload={"pair": f"{row.base_currency}{row.quote_currency}", "rate": rate})
        session.commit()
        session.refresh(row)
        return row

    @app.delete("/api/market-data/fx-rates/{fx_rate_id}")
    def delete_fx_rate_endpoint(fx_rate_id: int, session: Session = Depends(get_db)):
        fx_service.delete_fx_rate(session, fx_rate_id)
        record_audit(session, event_type="market_data.fx_rate.delete", actor="desk_user",
                     subject_type="fx_rate", subject_id=fx_rate_id, payload={})
        session.commit()
        return {"ok": True}

    def _market_profile_from_snapshot(
        normalized: MarketDataSnapshot,
        payload: AkshareSnapshotRequest,
        *,
        display_symbol: str | None = None,
    ) -> MarketDataProfile:
        source_metadata = dict(normalized.source_metadata or {})
        if display_symbol and display_symbol != normalized.symbol:
            source_metadata.setdefault("akshare_symbol", normalized.symbol)
        return MarketDataProfile(
            name=normalized.name,
            source="akshare",
            symbol=display_symbol or normalized.symbol,
            asset_class=normalized.asset_class,
            start_date=payload.start_date,
            end_date=payload.end_date,
            adjust=payload.adjust,
            valuation_date=normalized.valuation_date,
            data=normalized.data,
            source_metadata=source_metadata,
        )

    def _emit_profile_quote(session: Session, profile: MarketDataProfile, instrument_id: int):
        """Every fetch writes through the quote store (write-time unification)."""
        from .services.quotes import record_quote

        data = profile.data if isinstance(profile.data, dict) else {}
        spot = data.get("spot")
        if spot is None:
            return None
        return record_quote(
            session,
            instrument_id=instrument_id,
            price=float(spot),
            as_of=profile.valuation_date,
            source="akshare",
            market_data_profile_id=profile.id,
        )

    # ------------------------------------------------------------------
    # Gateway HTTP control plane — linking-code issuance (sub-task 15a)
    # ------------------------------------------------------------------
    # Rate-limiter state lives on app.state (NOT a module-global) so each
    # test client gets a fresh deque and tests don't bleed across each
    # other.  We key globally (no per-user identity in this auth-free app).
    import time as _time

    app.state.gateway_code_issue_times = deque()

    @app.post("/api/gateway/linking-codes")
    def issue_linking_code(
        payload: GatewayLinkingCodeRequest,
        db: Session = Depends(get_db),  # follows the app's standard no-auth get_db pattern
    ) -> GatewayLinkingCodeResponse:
        """Issue a one-time linking code for an IM enrollment."""
        # Rate limiting: rolling 60-second window using wall-clock time.
        # State is kept on app.state so each TestClient instance is isolated.
        now = _time.monotonic()
        window_start = now - 60.0
        issue_times: deque = app.state.gateway_code_issue_times
        # Evict records older than the window
        while issue_times and issue_times[0] < window_start:
            issue_times.popleft()
        if len(issue_times) >= active_settings.gateway_code_issue_per_min:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded: too many linking codes issued in the last 60 seconds.",
            )
        try:
            code, expires_at = _gateway_identity.issue_linking_code(
                db, persona=payload.persona, settings=active_settings
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        db.commit()
        issue_times.append(now)
        return GatewayLinkingCodeResponse(
            code=code,
            expires_at=expires_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # Gateway HTTP control plane — bindings list + revoke (sub-task 15b)
    # ------------------------------------------------------------------
    import base64 as _base64
    import json as _json
    import datetime as _dt

    @app.get("/api/gateway/bindings")
    def list_bindings(
        status: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1),
        cursor: str | None = Query(default=None),
        db: Session = Depends(get_db),  # follows the app's standard no-auth get_db pattern
    ) -> GatewayBindingsResponse:
        """List GatewayBinding rows, newest first, with cursor pagination."""
        # Clamp limit to [1, 200]
        effective_limit = min(limit, 200)

        query = db.query(GatewayBinding)
        if status is not None:
            query = query.filter(GatewayBinding.status == status)

        # Decode cursor: base64-encoded JSON {"bound_at": "<iso>", "id": <int>}
        if cursor is not None:
            try:
                payload = _json.loads(_base64.b64decode(cursor + "==").decode())
                cursor_bound_at = _dt.datetime.fromisoformat(payload["bound_at"])
                cursor_id = int(payload["id"])
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid cursor")
            # Fetch rows strictly before (bound_at, id) in DESC order:
            # i.e. (bound_at < cursor_bound_at) OR (bound_at == cursor_bound_at AND id < cursor_id)
            from sqlalchemy import or_, and_
            query = query.filter(
                or_(
                    GatewayBinding.bound_at < cursor_bound_at,
                    and_(
                        GatewayBinding.bound_at == cursor_bound_at,
                        GatewayBinding.id < cursor_id,
                    ),
                )
            )

        # Fetch limit+1 to detect whether there's a next page
        rows = (
            query
            .order_by(GatewayBinding.bound_at.desc(), GatewayBinding.id.desc())
            .limit(effective_limit + 1)
            .all()
        )

        has_next = len(rows) > effective_limit
        page_rows = rows[:effective_limit]

        next_cursor: str | None = None
        if has_next and page_rows:
            last = page_rows[-1]
            # Second-precision isoformat is sufficient: bound_at uses SQLite's
            # func.now() server default, which has no sub-second resolution.
            cursor_payload = {"bound_at": last.bound_at.isoformat(), "id": last.id}
            next_cursor = _base64.b64encode(
                _json.dumps(cursor_payload, separators=(",", ":")).encode()
            ).decode().rstrip("=")

        def _fmt(dt_val: object) -> str | None:
            if dt_val is None:
                return None
            return dt_val.isoformat()  # type: ignore[union-attr]

        bindings_out = [
            GatewayBindingOut(
                id=row.id,
                provider=row.provider,
                external_account_id=row.external_account_id,
                workspace_id=row.workspace_id,
                desk_user=row.desk_user,
                persona=row.persona,
                status=row.status,
                bound_at=_fmt(row.bound_at),
                last_seen_at=_fmt(row.last_seen_at),
                revoked_at=_fmt(row.revoked_at),
            )
            for row in page_rows
        ]

        return GatewayBindingsResponse(bindings=bindings_out, next_cursor=next_cursor)

    @app.delete("/api/gateway/bindings/{binding_id}")
    def revoke_binding(
        binding_id: int,
        db: Session = Depends(get_db),  # follows the app's standard no-auth get_db pattern
    ) -> dict:
        """Revoke a GatewayBinding; idempotent — revoking an already-revoked binding is still 200."""
        try:
            result = _gateway_identity.revoke_binding(db, binding_id=binding_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        db.commit()
        return {"status": result}

    # ------------------------------------------------------------------
    # Gateway HTTP control plane — health + reload (sub-task 15c)
    # ------------------------------------------------------------------

    def _get_gateway_runtime():
        runtime = getattr(app.state, "gateway_runtime", None)
        if runtime is None:
            raise HTTPException(status_code=503, detail="gateway runtime not initialized")
        return runtime

    @app.get("/api/gateway/health")
    async def gateway_health() -> dict:
        """Return gateway runtime health: lock ownership and connector states."""
        runtime = _get_gateway_runtime()
        h = await runtime.health()
        return {
            "worker_lock_owner": h["worker_lock_owner"],
            "connectors": [
                {"name": name, "state": v["state"], "detail": v["detail"]}
                for name, v in h["connectors"].items()
            ],
        }

    @app.post("/api/gateway/reload")
    async def gateway_reload() -> dict:
        """Owner-only: reload gateway connectors. Returns 409 if not the owner."""
        runtime = _get_gateway_runtime()
        h = await runtime.health()
        if not h["worker_lock_owner"]:
            raise HTTPException(status_code=409, detail="not the gateway worker owner")
        result = await runtime.reload()
        return {
            "worker_lock_owner": result["worker_lock_owner"],
            "connectors": [
                {"name": name, "state": v["state"], "detail": v["detail"]}
                for name, v in result.get("connectors", {}).items()
            ],
        }

    # ------------------------------------------------------------------
    # Gateway runtime lifecycle wiring (sub-task 15d)
    # ------------------------------------------------------------------
    from app.services.gateway.runtime import GatewayRuntime
    from app.services.gateway.bridge import AgentBridge

    app.state.gateway_runtime = GatewayRuntime(
        active_settings,
        database.SessionLocal,
        bridge=AgentBridge(active_agent_service),
    )

    @app.on_event("startup")
    async def _start_gateway_runtime() -> None:
        await app.state.gateway_runtime.start()

    @app.on_event("shutdown")
    async def _stop_gateway_runtime() -> None:
        await app.state.gateway_runtime.stop()

    @app.on_event("shutdown")
    async def _drain_memory_queue() -> None:
        from app.services.deep_agent.memory.runtime import shutdown_memory_runtime
        shutdown_memory_runtime()

    app.include_router(build_audit_router())
    app.include_router(build_memory_router())
    app.include_router(build_skills_router(active_agent_service))
    app.include_router(build_tracing_router())
    app.include_router(build_arena_router(settings=active_settings))
    app.include_router(build_desk_workflows_router())
    app.include_router(build_limits_router(get_db=get_db))
    app.include_router(
        build_agent_channels_router(active_agent_service, settings=active_settings)
    )

    # Goal mode (spec §G): the /goal lifecycle endpoints. The framer uses the desk
    # model; criteria are bounded to the DOMAIN_READ tools the grader may call; run
    # state and the frozen contract persist on the owning AgentThread row.
    goal_service = GoalRunService(
        model=active_agent_service.model,
        grader_tool_allowlist=goal_grader_tool_allowlist(active_agent_service.tools),
        run_backend=ThreadColumnBackend(database.SessionLocal, "goal_run"),
        contract_backend=ThreadColumnBackend(database.SessionLocal, "goal_contract"),
    )
    active_agent_service.goal_service = goal_service
    app.include_router(build_goal_router(goal_service))
    return app


app = create_app()
