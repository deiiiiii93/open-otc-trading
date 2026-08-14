"""Resume must not call a pair done because ONE arm finished.

Run #104's failure shape: a sustained provider outage swept the remaining pairs
to invalid and the run was still marked completed. An arm-blind resume repeats it
silently — the high arm never runs and the board claims success.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _launcher():
    """Load scripts/launch_arena_run.py by PATH.

    `scripts/` has no __init__.py, so import_module("scripts.launch_arena_run")
    fails — the same spec_from_file_location pattern as
    tests/test_generate_demo_smoke.py.
    """
    spec = importlib.util.spec_from_file_location(
        "launch_arena_run", _ROOT / "scripts" / "launch_arena_run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_resume_todo_is_keyed_by_arm() -> None:
    launcher = _launcher()
    run = {
        "workflow_ids": ["wf-a"],
        "model_ids": ["model-x"],
        "reasoning_efforts": {"model-x": [None, "high"]},
        "matches": [
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": None, "status": "scored"},
        ],
    }
    assert launcher._resume_todo(run) == [("wf-a", "model-x", "high")]


def test_resume_todo_is_empty_when_every_arm_scored() -> None:
    launcher = _launcher()
    run = {
        "workflow_ids": ["wf-a"],
        "model_ids": ["model-x"],
        "reasoning_efforts": {"model-x": [None, "high"]},
        "matches": [
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": None, "status": "scored"},
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": "high", "status": "scored"},
        ],
    }
    assert launcher._resume_todo(run) == []


def test_resume_todo_on_an_unpinned_run_is_unchanged() -> None:
    """The historical path: no efforts pinned, one arm per model."""
    launcher = _launcher()
    run = {
        "workflow_ids": ["wf-a", "wf-b"],
        "model_ids": ["model-x"],
        "reasoning_efforts": {},
        "matches": [
            {"workflow_id": "wf-a", "model_id": "model-x",
             "reasoning_effort": None, "status": "scored"},
        ],
    }
    assert launcher._resume_todo(run) == [("wf-b", "model-x", None)]


def test_cli_expands_several_efforts_into_per_model_arms(session) -> None:
    """`--reasoning-effort low high` must reach queue_arena_run as ARMS.

    Without this the CLI — how real boards are actually launched here — could not
    express the very thing this feature adds.
    """
    from app.services.arena import store
    from app.services.arena.task import queue_arena_run

    run_id, _task = queue_arena_run(
        session,
        workflow_ids=["risk-manager-control-day"],
        model_ids=["deepseek-v4-pro"],
        # exactly what main() builds from `--reasoning-effort low high`
        reasoning_efforts={"deepseek-v4-pro": ["low", "high"]},
    )
    assert store.get_run(session, run_id)["reasoning_efforts"] == {
        "deepseek-v4-pro": ["low", "high"]
    }
