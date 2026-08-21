"""An arena run must be stoppable without killing the process that holds it.

`cancel_requested` was honoured by `async_agents/runner.py` but NOT by the arena
loop, so the only way to stop a board was to find and kill its process — and the
task record outlives the process, so any worker could pick the run up again and
silently resume it under a different environment. That happened: run #121 was
killed at the launcher, its task row stayed `running`, and a `--reload` dev server
resumed it at `.env`'s recursion limit instead of the launcher's.

A match in flight cannot be interrupted (the same constraint `async_agents`
documents for the graph), so the checkpoint is the ARM boundary: finish the arm
that is running, record it, then stop before the next one.
"""
from __future__ import annotations


def test_cancel_requested_stops_the_run_before_the_next_arm(settings):
    """Flipping cancel_requested mid-run must stop the loop at the arm boundary.

    Fails before the fix by running BOTH arms and reporting `completed` — the
    flag being ignored is precisely the defect.
    """
    from app import database
    from app.models import TaskKind, TaskRun
    from app.services.arena import store as arena_store
    from app.services.arena.task import execute_arena_run_task
    from tests.test_arena_api import _fake_get_bundle, _fake_transcript

    database.configure_database(settings)
    database.init_db()
    seen: list[str | None] = []

    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s, workflow_ids=["wf-a"], model_ids=["gpt-5-5"],
            reasoning_efforts={"gpt-5-5": [None, "high"]},
        )
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued")
        s.add(task); s.flush(); task_id = task.id; s.commit()

    def fake_run_match(loaded, model, *, artifact_root, run_id=None,
                       reasoning_effort=None, **kwargs):
        seen.append(reasoning_effort)
        # Request cancellation while the FIRST arm is running, the way a human
        # or the API would: flip the flag on the row, commit, and leave the
        # in-flight match to finish.
        with database.SessionLocal() as s2:
            t = s2.get(TaskRun, task_id)
            t.cancel_requested = True
            s2.commit()
        return _fake_transcript(workflow_id="wf-a", model_id=model.slug)

    execute_arena_run_task(
        task_id, run_id, database.SessionLocal,
        settings=settings, run_match_fn=fake_run_match,
        get_bundle_fn=_fake_get_bundle,
    )

    # The second arm never ran.
    assert seen == [None], f"expected the run to stop after arm 1, drove {seen}"

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict is not None
        # The arm that DID run is preserved — cancelling discards nothing.
        assert len(run_dict["matches"]) == 1
        # A cancelled run is terminal, and honestly not "completed": reporting
        # success would let a partial board be read as a full one.
        assert run_dict["status"] == "failed"
        assert "cancel" in (run_dict.get("error") or "").lower()

        task = s.get(TaskRun, task_id)
        assert task.status == "failed"
        assert "cancel" in ((task.message or "") + (task.error or "")).lower()


def test_cancel_before_start_records_no_matches(settings):
    """A run cancelled before its first arm must stop having driven nothing.

    Distinct from the mid-run path: `_record_pair` is never reached, so the run
    ends with zero matches rather than a partial board. It must still be marked
    terminal — a cancelled run left `running` is exactly what makes another
    worker resume it later.
    """
    from app import database
    from app.models import TaskKind, TaskRun
    from app.services.arena import store as arena_store
    from app.services.arena.task import execute_arena_run_task
    from tests.test_arena_api import _fake_get_bundle, _fake_transcript

    database.configure_database(settings)
    database.init_db()
    drove: list[str] = []

    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s, workflow_ids=["wf-a"], model_ids=["gpt-5-5"],
        )
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued",
                       cancel_requested=True)
        s.add(task); s.flush(); task_id = task.id; s.commit()

    def fake_run_match(loaded, model, *, artifact_root, run_id=None,
                       reasoning_effort=None, **kwargs):
        drove.append(model.slug)
        return _fake_transcript(workflow_id="wf-a", model_id=model.slug)

    execute_arena_run_task(
        task_id, run_id, database.SessionLocal,
        settings=settings, run_match_fn=fake_run_match,
        get_bundle_fn=_fake_get_bundle,
    )

    assert drove == [], f"a cancelled run drove matches anyway: {drove}"
    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict is not None
        assert run_dict["matches"] == []
        assert run_dict["status"] == "failed"
        assert s.get(TaskRun, task_id).status == "failed"
