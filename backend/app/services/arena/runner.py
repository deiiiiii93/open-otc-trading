"""Arena match runner.

Drives a single arena match against the REAL desk orchestrator:
  1. seed the workflow's fixtures into the main DB (fresh IDs per match),
  2. create an arena-tagged AgentThread,
  3. drive each workflow step through AgentService.stream_and_persist bound to
     the candidate Zenmux model,
  4. reconstruct the MatchTranscript from the persisted trace spans.

Matches run sequentially (the async checkpointer SQLite serialises writes).
The `drive` and `harvest` seams are injectable for unit tests.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path
from typing import Any, Callable

from app import database
from app.golden_workflows.fixtures import apply_seed
from app.models import AgentThread
from app.services.arena.models import arena_model_to_selection
from app.services.arena.trace_harvest import collect_rfq_ids_touched, transcript_from_trace

logger = logging.getLogger(__name__)

_PERSONA_TO_CHARACTER = {
    "trader": "trader",
    "risk_manager": "risk_manager",
    "high_board": "high_board",
    "sales": "trader",
    "quant": "trader",
}

_ARENA_SERVICE = None

# Between-steps task settle: poll until the match's queued background tasks
# finish so a later step reads completed results (e.g. the fresh risk run).
TASK_SETTLE_MAX_ATTEMPTS = 150
TASK_SETTLE_SLEEP_SECONDS = 2.0


def _persona_to_character(persona: str) -> str:
    return _PERSONA_TO_CHARACTER.get(persona, "trader")


def _wait_for_pending_tasks(
    baseline_task_id: int,
    *,
    max_attempts: int = TASK_SETTLE_MAX_ATTEMPTS,
    sleep_seconds: float = TASK_SETTLE_SLEEP_SECONDS,
) -> None:
    """Block until every TaskRun created after *baseline_task_id* is terminal.

    Workflow tools (run_batch_pricing, run_scenario_test, …) queue a TaskRun that
    a process-global thread pool executes asynchronously. Without waiting, the
    next workflow step would read stale results. The arena's own ARENA_RUN task
    is excluded so this never waits on itself. Bounded by *max_attempts* so a
    stuck task degrades to stale data rather than hanging the match forever.
    """
    import time

    from app import database
    from app.models import TaskKind, TaskRun, TaskStatus

    terminal = {
        TaskStatus.COMPLETED.value,
        TaskStatus.COMPLETED_WITH_ERRORS.value,
        TaskStatus.FAILED.value,
    }
    for _ in range(max_attempts):
        with database.SessionLocal() as session:
            pending = (
                session.query(TaskRun.id)
                .filter(
                    TaskRun.id > baseline_task_id,
                    TaskRun.kind != TaskKind.ARENA_RUN.value,
                    TaskRun.status.notin_(terminal),
                )
                .count()
            )
        if pending == 0:
            return
        time.sleep(sleep_seconds)


def _make_default_settle():
    """Snapshot the TaskRun high-water mark now, return a between-steps waiter."""
    from sqlalchemy import func

    from app import database
    from app.models import TaskRun

    with database.SessionLocal() as session:
        baseline = session.query(func.max(TaskRun.id)).scalar() or 0

    def settle() -> None:
        _wait_for_pending_tasks(baseline)

    return settle


def _get_arena_service():
    """Lazily build one AgentService for the process (model is rebound per turn)."""
    global _ARENA_SERVICE
    if _ARENA_SERVICE is None:
        from app.services.agents import AgentService
        _ARENA_SERVICE = AgentService()
    return _ARENA_SERVICE


def _persist_user_turn(thread_id: int, content: str, selection: dict) -> None:
    """Insert the user AgentMessage for this turn, mirroring the chat endpoint.

    ``AgentService.stream_and_persist`` assumes the caller has already inserted
    the user message (the HTTP endpoint does this before streaming). Without it
    the routed-stream turn cannot attach the route to the latest user message and
    later turns lose the real prompts from DB-backed thread history.
    """
    from app import database
    from app.models import AgentMessage

    with database.SessionLocal() as session:
        session.add(
            AgentMessage(
                thread_id=thread_id,
                role="user",
                character=None,
                content=content,
                meta={
                    "model_selection": selection,
                    "yolo_mode": True,
                    "confirmed_cost_preview": True,
                },
            )
        )
        session.commit()


# The arena measures whether a model can operate the desk as a human would
# WITHOUT human intervention. It drives every turn in YOLO (headless) mode: HITL
# interrupts are auto-cleared AND the propose_reply_options card tool is withheld,
# so the model cannot defer to a human and must execute on its own judgement.
# There is no faked-human answering of cards (which previously caused target
# drift), so each workflow step maps to exactly one driven turn.
ARENA_MODE = "yolo"


def _default_drive(thread_id: int, content: str, selection: dict) -> None:
    """Drive one desk turn to completion via stream_and_persist in YOLO mode.

    The transcript is harvested from the trace afterwards, so the streamed SSE
    events are consumed and discarded here.
    """
    _persist_user_turn(thread_id, content, selection)
    svc = _get_arena_service()

    async def _run() -> None:
        async for _chunk in svc.stream_and_persist(
            thread_id=thread_id,
            content=content,
            model_selection=selection,
            mode=ARENA_MODE,
            confirmed_cost_preview=True,
        ):
            pass

    asyncio.run(_run())


ARENA_PORTFOLIO_TAG = "arena"
ARENA_PROFILE_MARKER = "arena_owned"  # set in PricingParameterProfile.summary
ARENA_RFQ_CLIENT_PREFIX = "ARENA"  # the step-1 turn names the client with this prefix
# Reserved arena-private ReportJob.report_type for seeded governance evidence
# (high-board workflow). No production/user report path emits this value, so the
# recovery purge can delete every row of this type safely.
ARENA_REPORT_MARKER = "arena_high_board_governance"


def _purge_seeded_reports(session) -> None:
    """Recovery purge: delete EVERY ReportJob carrying the reserved arena-private
    marker report_type, so a freshly seeded match starts from a clean slate and the
    display-report step can only find the report this match just seeded.

    Safe because ARENA_REPORT_MARKER is a RESERVED report_type — an arena-private
    string no production/user/normal report path emits (same convention as
    ARENA_PORTFOLIO_TAG / ARENA_PROFILE_MARKER). Race-free under the documented
    sequential-matches invariant. Commits NOW (mirrors _purge_seeded_portfolios) so
    the delete is durable even if the subsequent apply_seed raises."""
    from sqlalchemy import delete

    from app import models
    session.execute(
        delete(models.ReportJob).where(models.ReportJob.report_type == ARENA_REPORT_MARKER)
    )
    session.commit()


def _purge_arena_rfqs(session, rfq_ids) -> None:
    """ORM-delete the given RFQ rows so quote_versions/approvals cascade
    (both relationships are cascade='all, delete-orphan')."""
    if not rfq_ids:
        return
    from app import models

    for rfq in session.query(models.RFQ).filter(models.RFQ.id.in_(list(rfq_ids))):
        session.delete(rfq)
    session.commit()


def _purge_match_rfqs(thread_id: int, rfq_id_baseline: int) -> None:
    """Best-effort cleanup of RFQs CREATED BY THIS MATCH: harvested (touched) ids,
    created after the pre-match baseline, AND carrying the arena client sentinel.

    Runs in a ``finally`` so an aborted match still cleans up. This matters because
    a leaked RFQ would otherwise be PERMANENT: the next match's baseline is taken
    after the leaked row already exists, so its ``id > baseline`` guard would never
    re-catch it. Fail-safe — a missing sentinel skips the row (cosmetic leak), never
    a real/seeded RFQ deleted. Never raises: cleanup is cosmetic hygiene and must not
    mask a match failure.
    """
    from app import models

    try:
        touched = collect_rfq_ids_touched(thread_id)
        candidates = {rid for rid in touched if rid > rfq_id_baseline}
        if not candidates:
            return
        with database.SessionLocal() as session:
            owned = [
                r.id
                for r in session.query(models.RFQ).filter(models.RFQ.id.in_(candidates))
                if (r.client_name or "").startswith(ARENA_RFQ_CLIENT_PREFIX)
            ]
            _purge_arena_rfqs(session, owned)
    except Exception:  # noqa: BLE001 — best-effort; never mask the match outcome
        logger.warning("arena RFQ cleanup failed for thread %s", thread_id, exc_info=True)


def _purge_seeded_portfolios(session, bundle) -> None:
    """Delete prior arena-seeded fixture rows sharing a fixture name (portfolios
    and pricing profiles), plus their dependents, so a re-seed for the next match
    starts from a clean slate and arena rows never accumulate in the real DB.

    The golden workflows are name-based (the agent resolves "the control
    portfolio" by name) and ``portfolios.name`` is UNIQUE, so a re-seed would
    collide unless the prior one is removed first. Pricing-profile names are NOT
    unique, so they never collide — but they ARE inserted on every match, so
    without cleanup repeated runs accumulate duplicate "Control Profile" rows in
    the user's profile list. Both are therefore purged.

    Deletion is scoped to rows THIS module created — portfolios carrying the
    ``ARENA_PORTFOLIO_TAG`` tag and profiles whose ``summary`` carries
    ``ARENA_PROFILE_MARKER`` — so a real desk portfolio/profile that happens to
    share the fixture name is NEVER touched. (A real same-named portfolio instead
    makes the seed fail cleanly on the unique-name constraint → failed match.)

    Portfolio dependents are removed by introspecting every mapped table for a
    ``portfolio_id`` or ``position_id`` column, which covers risk runs,
    valuations, scenario/backtest runs, hedge rows, and position children without
    hard-coding table names. Deletes run in reverse FK-dependency order (children
    before parents) because FK enforcement is ON (see database.py): ``task_runs``
    reference run rows (risk_run_id / scenario_test_run_id / backtest_run_id) as
    well as ``portfolio_id``, so they must be deleted before the run rows they
    point at. Profiles are handled last: an arena profile with no surviving
    run/fx referencer is deleted (owned rows first); one still referenced by a
    real-book run is RETIRED into the pricing subsystem's archived state
    (immutable, marker kept for later reclamation) rather than deleted, so that
    run's provenance is never destroyed — see the inline note.
    """
    import warnings

    from sqlalchemy import delete, select
    from sqlalchemy.exc import SAWarning

    from app import models
    from app.services.audit import record_audit
    from app.services.domains.pricing_profiles import ARCHIVED_SOURCE_TYPE

    # sorted_tables warns about an unrelated FK cycle among the agent_*/workflows
    # tables; none of those carry portfolio_id/position_id, so they fall outside
    # the purge scope and the ordering of the tables we touch stays correct.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SAWarning)
        fk_ordered_tables = list(reversed(models.Base.metadata.sorted_tables))

    # --- portfolios + dependents (arena-tagged only) ---
    names = [r["name"] for r in bundle.seed.get("portfolios", []) if r.get("name")]
    if names:
        candidates = session.scalars(
            select(models.Portfolio).where(models.Portfolio.name.in_(names))
        ).all()
        pids = [p.id for p in candidates if ARENA_PORTFOLIO_TAG in (p.tags or [])]
        if pids:
            posids = list(
                session.scalars(
                    select(models.Position.id).where(models.Position.portfolio_id.in_(pids))
                )
            )
            portfolio_table = models.Portfolio.__table__
            for table in fk_ordered_tables:
                if table is portfolio_table:
                    continue
                if posids and "position_id" in table.c:
                    session.execute(delete(table).where(table.c.position_id.in_(posids)))
                if "portfolio_id" in table.c:
                    session.execute(delete(table).where(table.c.portfolio_id.in_(pids)))
            session.execute(delete(portfolio_table).where(portfolio_table.c.id.in_(pids)))

    # --- pricing profiles (arena-marked only) ---
    prof_names = [r["name"] for r in bundle.seed.get("pricing_profiles", []) if r.get("name")]
    if prof_names:
        prof_candidates = session.scalars(
            select(models.PricingParameterProfile).where(
                models.PricingParameterProfile.name.in_(prof_names)
            )
        ).all()
        prof_ids = [
            p.id for p in prof_candidates if (p.summary or {}).get(ARENA_PROFILE_MARKER)
        ]
        if prof_ids:
            prof_table = models.PricingParameterProfile.__table__
            row_table = models.PricingParameterRow.__table__
            # An arena profile can be FK-referenced two ways, distinguished by
            # column nullability:
            #   * NOT NULL (pricing_parameter_rows.profile_id) — an owned
            #     composition child, cascaded on the profile's own delete.
            #   * NULLABLE (risk_runs / position_valuation_runs / greek_landscape_runs
            #     / scenario_test_runs / backtest_runs / fx_rates) — an INDEPENDENT
            #     run/snapshot that merely *used* the profile. A match's LLM may price
            #     a REAL, non-arena book (portfolio "Default") against the seeded
            #     profile; that run survives the portfolio-scoped purge above and is a
            #     genuine audit record.
            #
            # Mirror pricing_profiles.delete_profile: a profile a real run/fx snapshot
            # depends on is provenance we must NOT destroy — so REFUSE to delete it.
            # Nulling the FK (the earlier approach) silently erased which profile a
            # real-book run priced against and raced the scenario/backtest/greek
            # workers that branch on it. Instead RETIRE a referenced profile by moving
            # it into the SAME archived state the pricing subsystem already uses for
            # audit artifacts (source_type == ARCHIVED_SOURCE_TYPE): _mutable_profile
            # then rejects any edit/row-mutation, so it can't be mutated as a live
            # profile. We KEEP the arena marker (so a later purge reclaims it once its
            # last reference is gone) and DON'T rename it (the marker + stable name is
            # how the name-keyed lookup finds it again). This is safe because re-seeding
            # never needs the row gone — profile names are not unique and apply_seed
            # always mints a fresh profile row. Unreferenced profiles (the common case —
            # the LLM only priced the arena book, whose runs were purged with the
            # portfolio) are deleted cleanly, owned rows first; that same branch also
            # reclaims a previously-retired profile whose references have since cleared.
            usage_cols = [
                col
                for table in fk_ordered_tables
                if table not in (prof_table, row_table)
                for col in table.c
                if col.nullable
                and any(fk.column.table is prof_table for fk in col.foreign_keys)
            ]
            # NULL never matches an IN predicate, so every value returned here is a
            # real referencing profile id (no None to filter out).
            referenced_ids: set[int] = set()
            for col in usage_cols:
                referenced_ids.update(
                    session.scalars(
                        select(col).where(col.in_(prof_ids)).distinct()
                    ).all()
                )

            # Retire referenced profiles (refuse-to-delete; preserve the FK + rows).
            for prof in prof_candidates:
                if prof.id not in referenced_ids:
                    continue
                if prof.source_type == ARCHIVED_SOURCE_TYPE:
                    continue  # already retired on a prior purge — idempotent no-op
                prof.source_type = ARCHIVED_SOURCE_TYPE  # -> immutable via _mutable_profile
                prof.summary = {**(prof.summary or {}), "arena_retired": True}
                record_audit(
                    session,
                    event_type="pricing_parameter_profile.arena_retired",
                    actor="arena",
                    subject_type="pricing_parameter_profile",
                    subject_id=prof.id,
                    payload={
                        "name": prof.name,
                        "reason": "referenced_by_non_arena_run",
                    },
                )

            # Clean-delete unreferenced profiles (owned rows first — no cascade at the
            # Core layer). Also reclaims previously-retired profiles now unreferenced.
            unreferenced = [pid for pid in prof_ids if pid not in referenced_ids]
            if unreferenced:
                session.execute(
                    delete(row_table).where(row_table.c.profile_id.in_(unreferenced))
                )
                session.execute(
                    delete(prof_table).where(prof_table.c.id.in_(unreferenced))
                )

    session.commit()


def _copy_artifacts(artifacts: list[dict], artifact_root: Path, workflow_id: str) -> list[dict]:
    """Copy artifact files under artifact_root/workflow_id/ and rewrite paths.

    Silently passes through artifacts whose ``path`` is missing or non-existent.
    """
    dest_dir = artifact_root / workflow_id
    copied = []
    for art in artifacts:
        path_str = art.get("path")
        if not path_str:
            copied.append(art)
            continue
        src = Path(path_str)
        if not src.exists():
            copied.append(art)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        copied.append({**art, "path": str(dest)})
    return copied


def _purge_seeded_trap_sets(loaded, settings) -> None:
    """Delete any scenario-set files matching the workflow's reserved
    ``trap_absent_sets`` names, so a set a PRIOR match fabricated cannot leak
    forward and invert the trap for every later match.

    The flagship trap asks the model to run a 'does-not-exist' set; a model that
    falls for it creates that set via the scenario CRUD tool, leaving
    ``{name}.yaml`` / ``{name}.set.json`` on disk. ``run_match`` already purges
    seeded portfolios/RFQs/reports but not scenario-set files, so without this the
    leak trips ``_assert_trap_sets_absent`` at the START of every subsequent match
    — a self-pollution cascade that fails all later matches in a run.

    Safe by the same reserved-name contract as ``_purge_seeded_reports``: a
    ``trap_absent_sets`` name is benchmark-private (the workflow declares it must
    NOT exist), so any file under that name in the library is arena pollution,
    never a real desk set. Fail-soft: a removal error is logged, not raised — the
    subsequent ``_assert_trap_sets_absent`` is the hard backstop.
    """
    from pathlib import Path

    names = getattr(loaded.workflow, "trap_absent_sets", None) or []
    if not names:
        return
    d = Path(settings.scenario_sets_dir)
    for name in names:
        for suffix in (".yaml", ".set.json"):
            f = d / f"{name}{suffix}"
            try:
                if f.exists():
                    f.unlink()
                    logger.info("arena: purged leaked trap set %s", f)
            except OSError:
                logger.warning(
                    "arena: could not purge trap set %s", f, exc_info=True)


def _assert_trap_sets_absent(loaded, settings) -> None:
    """Fail-loud if a benchmark-reserved 'does-not-exist' scenario set actually
    exists in the active scenario library.

    A silently-present trap set inverts the trap check — competent models that
    check the library and run the (unexpectedly real) set then "fail" the trap,
    while only broken/blank runs "pass". Reads workflow-level ``trap_absent_sets``;
    no-op when unset. Runs AFTER ``_purge_seeded_trap_sets`` as a hard backstop: a
    surviving file here means the purge could not remove it (e.g. permissions),
    which is a genuine setup fault worth failing on.
    """
    from pathlib import Path
    names = getattr(loaded.workflow, "trap_absent_sets", None) or []
    if not names:
        return
    d = Path(settings.scenario_sets_dir)
    for name in names:
        if (d / f"{name}.yaml").exists() or (d / f"{name}.set.json").exists():
            raise RuntimeError(
                f"Trap-set precondition violated: reserved set '{name}' is present "
                f"in {d} — the trap check would silently invert. Remove it or pick "
                f"another reserved name in the workflow's trap_absent_sets."
            )


def run_match(
    loaded,
    model,
    *,
    artifact_root: Path,
    run_id: int | None = None,
    drive: Callable[[int, str, dict], int | None] | None = None,
    harvest: Callable[..., Any] | None = None,
    settle: Callable[[], None] | None = None,
) -> Any:
    """Run a single arena match and return a MatchTranscript.

    Args:
        loaded: LoadedWorkflow (registry.get_workflow_bundle).
        model: ArenaModel descriptor.
        artifact_root: Root directory for copied artifacts.
        run_id: ArenaRun id used to tag the created thread (None in unit tests).
        drive: Injectable turn driver ``(thread_id, content, selection) -> None``.
            Defaults to the stream_and_persist-based ``_default_drive``.
        harvest: Injectable transcript harvester ``(thread_id, workflow, model)``.
            Defaults to ``transcript_from_trace``.
        settle: Injectable no-arg waiter run after each step to let queued
            background tasks finish before the next step reads their results.
            Defaults to a DB-polling waiter; tests inject a no-op.
    """
    drive = drive or _default_drive
    harvest = harvest or transcript_from_trace

    workflow = loaded.workflow
    from app.config import get_settings
    _settings = get_settings()
    # Self-heal first: clear any reserved trap set a prior match fabricated, then
    # assert absence as a hard backstop (a survivor means the purge failed).
    _purge_seeded_trap_sets(loaded, _settings)
    _assert_trap_sets_absent(loaded, _settings)
    artifact_root = Path(artifact_root)
    selection = arena_model_to_selection(model)

    # Reset any prior same-named seed, then seed fresh (autoincrement IDs) and
    # create the arena-tagged thread.
    seeded_report_ids: list[int] = []
    with database.SessionLocal() as session:
        _purge_seeded_portfolios(session, loaded.fixtures)
        _purge_seeded_reports(session)   # recovery: reclaim prior crash orphans (commits)
        seed_ids = apply_seed(loaded.fixtures, session)
        seeded_report_ids = list(seed_ids.get("reports", {}).values())
        # Mark the seeded fixture rows as arena-owned so the next match's purge
        # only removes arena rows, never a real desk portfolio/profile of the
        # same name.
        seeded_pids = list(seed_ids.get("portfolios", {}).values())
        seeded_prof_ids = list(seed_ids.get("pricing_profiles", {}).values())
        if seeded_pids or seeded_prof_ids:
            from app.models import PricingParameterProfile, Portfolio

            for portfolio in session.query(Portfolio).filter(Portfolio.id.in_(seeded_pids)):
                portfolio.tags = sorted({*(portfolio.tags or []), ARENA_PORTFOLIO_TAG})
            for prof in session.query(PricingParameterProfile).filter(
                PricingParameterProfile.id.in_(seeded_prof_ids)
            ):
                prof.summary = {**(prof.summary or {}), ARENA_PROFILE_MARKER: True}
            session.commit()
        thread = AgentThread(
            title=f"[arena] {workflow.id} · {model.slug}",
            character=_persona_to_character(workflow.persona),
            source="arena",
            arena_run_id=run_id,
        )
        session.add(thread)
        session.commit()
        thread_id = thread.id

    # Snapshot the task high-water mark (after seeding, before driving) so the
    # settle step only waits on tasks this match queues.
    if settle is None:
        settle = _make_default_settle()

    # High-water mark for RFQs so post-match cleanup only deletes rows CREATED
    # during this match (a harvested id <= baseline was merely touched, e.g. a
    # pre-existing RFQ the agent quoted — never delete it).
    from sqlalchemy import func

    from app import models

    with database.SessionLocal() as session:
        rfq_id_baseline = session.query(func.max(models.RFQ.id)).scalar() or 0

    # Drive every workflow step as one YOLO turn on the same thread, waiting for
    # queued background tasks to finish before the next step reads their results.
    # The RFQ cleanup runs in a finally so an aborted match still purges the RFQs
    # it created (an un-purged leak would be permanent — see _purge_match_rfqs).
    try:
        for wf_step in workflow.steps:
            drive(thread_id, wf_step.user, selection)
            settle()

        transcript = harvest(thread_id, workflow, model)
    finally:
        _purge_match_rfqs(thread_id, rfq_id_baseline)
        if seeded_report_ids:
            # Ownership-precise: delete only THIS match's seeded ReportJob rows.
            from sqlalchemy import delete
            with database.SessionLocal() as session:
                session.execute(
                    delete(models.ReportJob).where(models.ReportJob.id.in_(seeded_report_ids))
                )
                session.commit()

    # Copy any harvested artifacts under the run's artifact root.
    copied_steps = []
    for step in transcript.steps:
        if step.artifacts:
            step.artifacts = _copy_artifacts(step.artifacts, artifact_root, workflow.id)
        copied_steps.append(step)
    transcript.steps = copied_steps
    return transcript
