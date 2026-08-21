"""`POST /api/arena/runs/{id}/cancel` — the trigger for the cancellation the
arena loop already honours.

Asserted at the HTTP layer on purpose. `_execute` honouring `cancel_requested`
is worth nothing if nothing can set it: before this endpoint the only way to stop
a board was a manual UPDATE on the database, which is a cancel in name only. The
repo has twice shipped a store that worked behind an API that served nothing
(`/api/agent/models` dropping a field via pydantic, `get_leaderboard`'s hand-built
key projection), so the assertion belongs here rather than only on the store.

Cancelling needs `task_runs.arena_run_id`: an arena run had NO link to the task
driving it — not a column, not even in the description — so given a run id
nothing could find the flag to flip.
"""
from __future__ import annotations

from app import database
from app.models import TaskKind, TaskRun
from app.services.arena import store as arena_store
from tests.test_arena_api import _make_arena_app


def _queued_run(session, settings, status: str = "running"):
    """An arena run with the task row that drives it, as queue_arena_run makes."""
    run_id = arena_store.create_run(
        session, workflow_ids=["wf-a"], model_ids=["gpt-5-5"],
    )
    arena_store.set_run_status(session, run_id, status)
    task = TaskRun(
        kind=TaskKind.ARENA_RUN.value,
        status="running",
        arena_run_id=run_id,
    )
    session.add(task)
    session.flush()
    session.commit()
    return run_id, task.id


def test_cancel_flips_the_flag_on_the_running_run(session, settings):
    """The endpoint must set cancel_requested on the task driving the run."""
    run_id, task_id = _queued_run(session, settings)
    client = _make_arena_app(session, settings)

    resp = client.post(f"/api/arena/runs/{run_id}/cancel")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["run_id"] == run_id
    assert body["cancel_requested"] is True
    assert session.get(TaskRun, task_id).cancel_requested is True


def test_cancel_is_idempotent(session, settings):
    """Asking twice is not an error — the desk may click Stop more than once."""
    run_id, task_id = _queued_run(session, settings)
    client = _make_arena_app(session, settings)

    assert client.post(f"/api/arena/runs/{run_id}/cancel").status_code == 200
    resp = client.post(f"/api/arena/runs/{run_id}/cancel")

    assert resp.status_code == 200, resp.text
    assert session.get(TaskRun, task_id).cancel_requested is True


def test_cancel_refuses_a_terminal_run(session, settings):
    """A finished run cannot be cancelled, and saying so beats a silent no-op.

    Returning 200 would let the UI show 'cancelling…' forever on a run that has
    already stopped.
    """
    run_id, _ = _queued_run(session, settings, status="completed")
    client = _make_arena_app(session, settings)

    resp = client.post(f"/api/arena/runs/{run_id}/cancel")

    assert resp.status_code == 409, resp.text
    assert "terminal" in resp.json()["detail"].lower()


def test_cancel_404s_on_an_unknown_run(session, settings):
    client = _make_arena_app(session, settings)
    assert client.post("/api/arena/runs/999999/cancel").status_code == 404
