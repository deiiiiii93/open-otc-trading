"""Arena match runner.

Drives a single arena match: seeds an isolated DB, runs each workflow step
through an agent (real or injected fake), and returns a MatchTranscript.

## Concurrency constraint
Isolation is implemented via process-global DB reconfiguration (mutating
database.SessionLocal). Matches CANNOT run concurrently in the same process.
The effective pool size is 1. A future Task 13 carry-forward: replace with
per-call session injection so the pool can be widened.

## run-tool blocking
Tools whose names start with 'run_' are wrapped to poll until the background
task completes. The wrapper accepts an optional `status_checker` callable for
unit-test injection; in production a default status-checker that queries the
TaskRun table via database.SessionLocal is used.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_TURNS_PER_STEP = 12
POLL_MAX_ATTEMPTS = 120
POLL_SLEEP_SECONDS = 2.0


# ---------------------------------------------------------------------------
# DB isolation
# ---------------------------------------------------------------------------

@contextmanager
def isolated_match_db(bundle):
    """Reconfigure the global DB to a fresh temp SQLite, seed it, yield, restore.

    Because tools use ``database.SessionLocal`` directly, isolation requires
    mutating the module-level global. Matches must not overlap in the same
    process (see module docstring).

    Yields the (updated) ``database.SessionLocal`` factory for convenience.
    """
    from app import database
    from app.config import Settings
    from app.golden_workflows.fixtures import apply_seed

    prev_settings = database.settings
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    tmp_path = tmp.name
    tmp.close()
    try:
        new_settings = Settings(
            database_url=f"sqlite+pysqlite:///{tmp_path}",
            artifact_dir=prev_settings.artifact_dir,
            agent_checkpoint_db_path=":memory:",
        )
        database.configure_database(new_settings)
        database.init_db()
        with database.SessionLocal() as s:
            apply_seed(bundle, s)
        yield database.SessionLocal
    finally:
        database.configure_database(prev_settings)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# run-tool wrapping
# ---------------------------------------------------------------------------

def _default_status_checker(task_id: str) -> dict:
    """Poll TaskRun status via the module-global SessionLocal.

    Returns the TaskRun row as a dict with at minimum 'status'.
    """
    import time
    from app import database

    for _ in range(POLL_MAX_ATTEMPTS):
        with database.SessionLocal() as s:
            from sqlalchemy import text
            row = s.execute(
                text("SELECT status, result_payload FROM task_runs WHERE id = :id"),
                {"id": task_id},
            ).mappings().first()
            if row is None:
                break
            status = row["status"]
            if status in {"completed", "failed", "cancelled"}:
                result = {}
                if row["result_payload"]:
                    import json
                    try:
                        result = json.loads(row["result_payload"]) if isinstance(row["result_payload"], str) else row["result_payload"]
                    except Exception:
                        result = {}
                return {"task_id": task_id, "status": status, **result}
        time.sleep(POLL_SLEEP_SECONDS)
    # Timed out or not found — return last known state
    return {"task_id": task_id, "status": "unknown"}


def _wrap_run_tools(
    tools: list,
    status_checker: Callable[[str], dict] | None = None,
) -> list:
    """Wrap tools whose names start with 'run_' to block until completion.

    Non-run_ tools are returned unchanged.

    Args:
        tools: List of tool callables with a ``.name`` attribute.
        status_checker: Optional callable ``(task_id: str) -> dict`` that
            returns a status dict with at least a ``status`` key. Defaults
            to ``_default_status_checker`` (queries the real TaskRun table).

    Returns:
        A new list with run_ tools wrapped and others passed through.
    """
    if status_checker is None:
        status_checker = _default_status_checker

    result = []
    for tool in tools:
        name = getattr(tool, "name", "")
        if not name.startswith("run_"):
            result.append(tool)
            continue

        # Capture for closure
        original_tool = tool
        _checker = status_checker

        def _make_wrapper(orig, checker):
            def wrapper(*args, **kwargs):
                # Call original tool
                output = orig(*args, **kwargs)
                if not isinstance(output, dict):
                    return output
                status = output.get("status", "")
                task_id = output.get("task_id")
                # If already terminal or no task_id, return as-is
                if task_id is None or status in {"completed", "failed", "cancelled"}:
                    return output
                # Poll until terminal
                _terminal = {"completed", "failed", "cancelled"}
                for _ in range(POLL_MAX_ATTEMPTS):
                    result = checker(str(task_id))
                    if result.get("status") in _terminal:
                        return result
                # Timed out — return last result
                return result

            wrapper.name = orig.name
            return wrapper

        result.append(_make_wrapper(original_tool, _checker))

    return result


# ---------------------------------------------------------------------------
# arena agent builder
# ---------------------------------------------------------------------------

def build_arena_agent(chat) -> Any:
    """Build a full orchestrator agent from a ChatOpenAI client.

    Wraps ``build_orchestrator`` with arena-appropriate settings.
    Re-raises ImportError with a clear message if deepagents is not installed.
    """
    try:
        from app.services.deep_agent.orchestrator import build_orchestrator
    except ImportError as exc:
        raise ImportError(
            "build_arena_agent requires deepagents to be installed. "
            f"Original error: {exc}"
        ) from exc

    from app.tools import QUANT_AGENT_TOOLS
    from langgraph.checkpoint.memory import MemorySaver

    return build_orchestrator(
        model=chat,
        tools=list(QUANT_AGENT_TOOLS),
        checkpointer=MemorySaver(),
    )


# ---------------------------------------------------------------------------
# Artifact copy helper
# ---------------------------------------------------------------------------

def _copy_artifacts(
    artifacts: list[dict],
    artifact_root: Path,
    workflow_id: str,
) -> list[dict]:
    """Copy artifact files under artifact_root / workflow_id / and rewrite paths.

    Silently skips artifacts whose ``path`` field points to a non-existent file.
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
            # Skip silently
            copied.append(art)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        shutil.copy2(src, dest)
        new_art = {**art, "path": str(dest)}
        copied.append(new_art)
    return copied


# ---------------------------------------------------------------------------
# Step driver
# ---------------------------------------------------------------------------

def _drive_step(
    agent,
    history: list[str],
    step_index: int,
    step,
) -> dict:
    """Drive a single workflow step through the agent with turn-budget enforcement.

    The agent is called as ``agent(history, step_index) -> turn_events dict``.
    If the agent keeps returning tool_calls beyond MAX_TURNS_PER_STEP, we
    stop and inject a budget_exceeded error.

    Returns a turn_events dict.
    """
    turn_count = 0
    last_events: dict | None = None

    while turn_count < MAX_TURNS_PER_STEP:
        turn_count += 1
        events = agent(history, step_index)
        last_events = events
        # If no tool calls remain, we're done
        if not events.get("tool_calls"):
            return events

    # Budget exceeded — inject error into the last events
    if last_events is None:
        last_events = {
            "index": step_index,
            "user": step.user,
            "messages": [],
            "tool_calls": [],
            "tool_results": [],
            "skills_routed": [],
            "artifacts": [],
            "response_text": "",
            "errors": [],
        }
    errors = list(last_events.get("errors") or [])
    errors.append({"type": "budget_exceeded", "max_turns": MAX_TURNS_PER_STEP})
    return {**last_events, "errors": errors}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_match(
    loaded,
    model,
    *,
    artifact_root: Path,
    agent=None,
    chat=None,
) -> Any:
    """Run a single arena match and return a MatchTranscript.

    Args:
        loaded: LoadedWorkflow (from registry.get_workflow_bundle).
        model: ArenaModel descriptor.
        artifact_root: Root directory for copied artifacts.
        agent: Optional injected fake agent callable ``(history, step_index) -> dict``.
            Used in tests. If None, a real agent is built from ``chat``.
        chat: ChatOpenAI instance. Used only when ``agent`` is None.

    Returns:
        MatchTranscript with schema_version=1, run_id=None.
    """
    from app.golden_workflows.transcript import (
        MatchTranscript,
        extract_step_from_events,
    )

    if agent is None:
        if chat is None:
            raise ValueError("Either agent or chat must be provided")
        agent = _make_langchain_agent_driver(build_arena_agent(chat))

    workflow = loaded.workflow
    bundle = loaded.fixtures
    artifact_root = Path(artifact_root)

    started_at = datetime.now(tz=timezone.utc).isoformat()

    steps = []
    history: list[str] = []

    with isolated_match_db(bundle):
        for step_index, wf_step in enumerate(workflow.steps):
            history.append(wf_step.user)
            try:
                events = _drive_step(agent, history, step_index, wf_step)
            except Exception as exc:
                events = {
                    "index": step_index,
                    "user": wf_step.user,
                    "messages": [],
                    "tool_calls": [],
                    "tool_results": [],
                    "skills_routed": [],
                    "artifacts": [],
                    "response_text": "",
                    "errors": [{"type": "error", "message": str(exc)}],
                }

            # Copy artifacts
            raw_artifacts = list(events.get("artifacts") or [])
            copied_artifacts = _copy_artifacts(raw_artifacts, artifact_root, workflow.id)
            events = {**events, "artifacts": copied_artifacts}

            match_step = extract_step_from_events(events)
            steps.append(match_step)

    finished_at = datetime.now(tz=timezone.utc).isoformat()

    return MatchTranscript(
        schema_version=1,
        run_id=None,
        workflow_id=workflow.id,
        model_id=model.slug,
        started_at=started_at,
        finished_at=finished_at,
        steps=steps,
    )


def _make_langchain_agent_driver(lc_agent) -> Callable:
    """Wrap a LangChain/LangGraph agent to match the (history, step_index) -> dict signature.

    NOTE: This is a stub for future use. Real agent driving requires async
    event streaming. For now, it raises NotImplementedError to surface
    clearly in tests/debug.
    """
    def driver(history: list[str], step_index: int) -> dict:
        raise NotImplementedError(
            "Real LangChain agent driving is not yet implemented. "
            "Inject a fake agent via the `agent=` parameter for testing."
        )
    return driver
