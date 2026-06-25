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


def _default_drive(thread_id: int, content: str, selection: dict) -> None:
    """Drive one desk turn to completion via stream_and_persist (HITL auto-cleared).

    The transcript is harvested from the trace afterwards, so the streamed SSE
    events are consumed and discarded here.
    """
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

    # Seed fixtures (fresh IDs) + create the arena-tagged thread.
    with database.SessionLocal() as session:
        apply_seed(loaded.fixtures, session)
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
