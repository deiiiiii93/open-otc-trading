"""Two arms of one model must not share a transcript directory."""
from __future__ import annotations

from pathlib import Path

from app.services.arena.task import _save_transcript


class _FakeTranscript:
    def __init__(self, tag: str) -> None:
        self._tag = tag

    def model_dump(self) -> dict:
        return {"tag": self._tag}


def test_two_efforts_write_separate_transcripts(tmp_path: Path) -> None:
    """Without an effort segment the second arm clobbers the first — the same
    failure the per-trial copies fixed one level up, where the evidence you most
    need is exactly the one that got overwritten."""
    low = _save_transcript(_FakeTranscript("low"), tmp_path, "wf", "m",
                           reasoning_effort="low")
    high = _save_transcript(_FakeTranscript("high"), tmp_path, "wf", "m",
                            reasoning_effort="high")

    assert low != high
    assert Path(low).read_text(encoding="utf-8").count('"low"') == 1
    assert Path(high).read_text(encoding="utf-8").count('"high"') == 1


def test_unpinned_arm_lands_under_default(tmp_path: Path) -> None:
    path = _save_transcript(_FakeTranscript("x"), tmp_path, "wf", "m")
    assert Path(path) == tmp_path / "wf" / "m" / "default" / "transcript.json"


def test_per_trial_copies_stay_inside_the_arm(tmp_path: Path) -> None:
    _save_transcript(_FakeTranscript("t0"), tmp_path, "wf", "m", trial=0,
                     reasoning_effort="high")
    assert (tmp_path / "wf" / "m" / "high" / "transcript.trial0.json").exists()


def test_execute_runs_every_arm_of_one_model(session, settings):
    """The core of the feature: two efforts on ONE model produce TWO matches.

    Before effort entered the contestant key the second arm upserted over the
    first, so this run would have finished with one match and reported success.
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
                       reasoning_effort=None):
        seen.append(reasoning_effort)
        return _fake_transcript(workflow_id="wf-a", model_id=model.slug)

    execute_arena_run_task(
        task_id, run_id, database.SessionLocal,
        settings=settings, run_match_fn=fake_run_match,
        get_bundle_fn=_fake_get_bundle,
    )

    # Each arm drove its own match, at its own effort.
    assert seen == [None, "high"]
    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        efforts = sorted(
            (m["reasoning_effort"] or "") for m in run_dict["matches"]
        )
        assert efforts == ["", "high"]

    # Progress counted arms, not models: a two-arm run is two units of work.
    with database.SessionLocal() as s:
        assert s.get(TaskRun, task_id).progress_total == 2
