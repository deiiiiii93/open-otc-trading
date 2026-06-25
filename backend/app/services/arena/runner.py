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
import shutil
from pathlib import Path
from typing import Any, Callable

from app import database
from app.golden_workflows.fixtures import apply_seed
from app.models import AgentThread
from app.services.arena.models import arena_model_to_selection
from app.services.arena.trace_harvest import transcript_from_trace

_PERSONA_TO_CHARACTER = {
    "trader": "trader",
    "risk_manager": "risk_manager",
    "sales": "trader",
    "quant": "trader",
}

_ARENA_SERVICE = None


def _persona_to_character(persona: str) -> str:
    return _PERSONA_TO_CHARACTER.get(persona, "trader")


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


def _default_drive(thread_id: int, content: str, selection: dict) -> None:
    """Drive one desk turn to completion via stream_and_persist (HITL auto-cleared).

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
            yolo_mode=True,
            confirmed_cost_preview=True,
        ):
            pass

    asyncio.run(_run())


ARENA_PORTFOLIO_TAG = "arena"
ARENA_PROFILE_MARKER = "arena_owned"  # set in PricingParameterProfile.summary


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
    point at. Profiles are purged last (after the run rows that reference
    ``pricing_parameter_profile_id`` are gone).
    """
    import warnings

    from sqlalchemy import delete, select
    from sqlalchemy.exc import SAWarning

    from app import models

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
            session.execute(delete(prof_table).where(prof_table.c.id.in_(prof_ids)))

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


def run_match(
    loaded,
    model,
    *,
    artifact_root: Path,
    run_id: int | None = None,
    drive: Callable[[int, str, dict], None] | None = None,
    harvest: Callable[..., Any] | None = None,
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
    """
    drive = drive or _default_drive
    harvest = harvest or transcript_from_trace

    workflow = loaded.workflow
    artifact_root = Path(artifact_root)
    selection = arena_model_to_selection(model)

    # Reset any prior same-named seed, then seed fresh (autoincrement IDs) and
    # create the arena-tagged thread.
    with database.SessionLocal() as session:
        _purge_seeded_portfolios(session, loaded.fixtures)
        seed_ids = apply_seed(loaded.fixtures, session)
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

    # Drive every workflow step as a turn on the same thread.
    for wf_step in workflow.steps:
        drive(thread_id, wf_step.user, selection)

    transcript = harvest(thread_id, workflow, model)

    # Copy any harvested artifacts under the run's artifact root.
    copied_steps = []
    for step in transcript.steps:
        if step.artifacts:
            step.artifacts = _copy_artifacts(step.artifacts, artifact_root, workflow.id)
        copied_steps.append(step)
    transcript.steps = copied_steps
    return transcript
