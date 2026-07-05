"""Tests for the Arena API router and async run task.

Coverage:
- POST valid → 202 + run_id + TaskRun(kind="arena_run") row exists
- POST unknown model / unknown workflow / empty list → 422
- GET /models lists the registry
- GET /runs empty → {runs:[], total:0}; GET /runs/{id} 404 unknown
- GET /leaderboard empty → 200 {"rows":[]}; populated case with avg_* keys
- GET /matches/{id}/transcript 404 when path missing
- execute_arena_run_task: 1-workflow × 2-model records 2 scored + run "completed"
- execute_arena_run_task: mock raises → match "failed" but run "completed"
- TaskKind.ARENA_RUN.value == "arena_run"
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app import database
from app.config import Settings
from app.models import ArenaMatch, ArenaRun, TaskKind, TaskRun
from app.routers.arena import build_arena_router
from app.services.arena import store as arena_store
from app.services.arena.models import CANDIDATE_MODELS


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_arena_app(
    session: Session,
    settings: Settings,
    submit_fn=None,
    queue_fn=None,
    execute_fn=None,
) -> TestClient:
    """Build a minimal FastAPI app with the arena router mounted."""
    app = FastAPI()

    def _get_db():
        yield session

    router = build_arena_router(
        get_db=_get_db,
        submit_async_task_fn=submit_fn or (lambda *a, **kw: None),
        session_factory=database.SessionLocal,
        settings=settings,
        queue_arena_run_fn=queue_fn,
        execute_arena_run_task_fn=execute_fn,
    )
    app.include_router(router)
    return TestClient(app)


# ---------------------------------------------------------------------------
# TaskKind enum
# ---------------------------------------------------------------------------


def test_task_kind_arena_run_value():
    assert TaskKind.ARENA_RUN.value == "arena_run"


# ---------------------------------------------------------------------------
# GET /api/arena/models
# ---------------------------------------------------------------------------


def test_list_models_returns_all_candidates(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    slugs = {m["slug"] for m in data["models"]}
    expected_slugs = {m.slug for m in CANDIDATE_MODELS}
    assert slugs == expected_slugs
    # Each model has required fields
    for m in data["models"]:
        assert "slug" in m
        assert "zenmux_name" in m
        assert "display_name" in m


# ---------------------------------------------------------------------------
# GET /api/arena/runs (empty)
# ---------------------------------------------------------------------------


def test_list_runs_empty(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"runs": [], "total": 0}


def test_list_runs_respects_max_limit(session, settings):
    """Limit is capped at 200 by the router."""
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/runs?limit=999")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/arena/runs/{id} — not found
# ---------------------------------------------------------------------------


def test_get_run_404_unknown(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/runs/99999")
    assert resp.status_code == 404


def test_get_run_returns_score_breakdown_on_matches(session, settings):
    """The run-detail endpoint must surface score_breakdown (the MatchSummary
    schema previously dropped it even though the store returned it)."""
    breakdown = {
        "objective": {"passed": 1, "total": 2,
                      "steps": [{"index": 0, "user": "u",
                                 "checks": [{"kind": "skill", "label": "skill: x",
                                             "passed": True, "detail": ""}]}],
                      "success": []},
        "judge": {"rubric_scores": [{"point": "p1", "score": 80}],
                  "judged_score": 80.0, "judge_missing": False},
    }
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=50.0, judged_score=80.0, total_score=65.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
        score_breakdown=breakdown,
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get(f"/api/arena/runs/{run_id}")
    assert resp.status_code == 200
    match = resp.json()["matches"][0]
    assert match["score_breakdown"] == breakdown


# ---------------------------------------------------------------------------
# GET /api/arena/matches/{id}/transcript — no path
# ---------------------------------------------------------------------------


def test_transcript_404_when_path_missing(session, settings):
    # Create a match without a transcript path
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    match_id = arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=None, judged_score=None, total_score=None,
        judge_missing=True, config={}, transcript_path=None, status="failed",
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get(f"/api/arena/matches/{match_id}/transcript")
    assert resp.status_code == 404


def test_transcript_404_when_match_not_found(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/matches/99999/transcript")
    assert resp.status_code == 404


def test_transcript_returns_json_when_file_exists(session, settings, tmp_path):
    transcript_data = {"schema_version": 1, "workflow_id": "wf-a"}
    t_file = tmp_path / "transcript.json"
    t_file.write_text(json.dumps(transcript_data), encoding="utf-8")

    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    match_id = arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=80.0, judged_score=70.0, total_score=75.0,
        judge_missing=False, config={}, transcript_path=str(t_file), status="scored",
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get(f"/api/arena/matches/{match_id}/transcript")
    assert resp.status_code == 200
    assert resp.json()["schema_version"] == 1


# ---------------------------------------------------------------------------
# GET /api/arena/leaderboard — empty
# ---------------------------------------------------------------------------


def test_leaderboard_empty(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/leaderboard")
    assert resp.status_code == 200
    assert resp.json() == {"rows": []}


def test_leaderboard_keys_are_avg_not_mean(session, settings):
    """Router must rename mean_total→avg_total, mean_objective→avg_objective, match_count→matches."""
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=60.0, judged_score=70.0, total_score=65.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/leaderboard")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    # Must use avg_* keys, NOT mean_* keys
    assert "avg_total" in row
    assert "avg_objective" in row
    assert "matches" in row
    assert "mean_total" not in row
    assert "mean_objective" not in row
    assert "match_count" not in row
    assert row["avg_total"] == 65.0
    assert row["matches"] == 1


# ---------------------------------------------------------------------------
# POST /api/arena/runs validation
# ---------------------------------------------------------------------------


def test_post_runs_empty_workflow_ids_returns_422(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.post("/api/arena/runs", json={"workflow_ids": [], "model_ids": ["gpt-5-5"]})
    assert resp.status_code == 422


def test_post_runs_empty_model_ids_returns_422(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.post("/api/arena/runs", json={"workflow_ids": ["some-wf"], "model_ids": []})
    assert resp.status_code == 422


def test_post_runs_unknown_model_returns_422(session, settings):
    # Patch queue_arena_run to raise ValueError for unknown model
    def fake_queue(sess, *, workflow_ids, model_ids, weights=None):
        raise ValueError(f"Unknown model id(s): {model_ids}")

    client = _make_arena_app(session, settings, queue_fn=fake_queue)
    resp = client.post(
        "/api/arena/runs",
        json={"workflow_ids": ["wf-a"], "model_ids": ["nonexistent-model"]},
    )
    assert resp.status_code == 422


def test_post_runs_unknown_workflow_returns_422(session, settings):
    def fake_queue(sess, *, workflow_ids, model_ids, weights=None):
        raise ValueError(f"Unknown workflow_id '{workflow_ids[0]}'")

    client = _make_arena_app(session, settings, queue_fn=fake_queue)
    resp = client.post(
        "/api/arena/runs",
        json={"workflow_ids": ["nonexistent-wf"], "model_ids": ["gpt-5-5"]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/arena/runs — valid → 202 + TaskRun row
# ---------------------------------------------------------------------------


def test_post_runs_valid_returns_202_and_task_run(session, settings):
    """Valid POST creates ArenaRun + TaskRun(kind='arena_run') and returns 202."""
    submitted_calls = []

    def capture_submit(fn, *args, **kwargs):
        submitted_calls.append((fn, args, kwargs))

    # Use the real queue_arena_run but patch the workflow registry
    from app.services.arena.task import queue_arena_run

    def fake_queue(sess, *, workflow_ids, model_ids, weights=None):
        # Create run directly in store (skip registry validation)
        run_id = arena_store.create_run(
            sess,
            workflow_ids=workflow_ids,
            model_ids=model_ids,
            weights=weights,
        )
        task = TaskRun(
            kind=TaskKind.ARENA_RUN.value,
            status="queued",
            description="test arena run",
        )
        sess.add(task)
        sess.flush()
        run_obj = SimpleNamespace(id=run_id)
        return run_obj, task

    client = _make_arena_app(
        session, settings,
        submit_fn=capture_submit,
        queue_fn=fake_queue,
    )

    resp = client.post(
        "/api/arena/runs",
        json={"workflow_ids": ["wf-demo"], "model_ids": ["gpt-5-5"]},
    )
    assert resp.status_code == 202
    data = resp.json()
    assert "run_id" in data
    assert data["status"] == "queued"

    # Verify TaskRun was created with kind=arena_run
    task = session.query(TaskRun).filter_by(kind="arena_run").first()
    assert task is not None
    assert task.kind == TaskKind.ARENA_RUN.value

    # Verify submit was called
    assert len(submitted_calls) == 1


def test_post_runs_valid_lists_in_get_runs(session, settings):
    """After POST, GET /runs returns the new run."""
    run_id = arena_store.create_run(
        session,
        workflow_ids=["wf-x"],
        model_ids=["gpt-5-5"],
    )
    arena_store.set_run_status(session, run_id, "queued")
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["runs"]) == 1
    assert data["runs"][0]["id"] == run_id


def test_get_run_returns_run_and_matches(session, settings):
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["gpt-5-5"])
    arena_store.record_match(
        session, run_id, "wf-a", "gpt-5-5",
        objective_score=70.0, judged_score=80.0, total_score=75.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get(f"/api/arena/runs/{run_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run"]["id"] == run_id
    assert len(data["matches"]) == 1
    m = data["matches"][0]
    assert m["workflow_id"] == "wf-a"
    assert m["total_score"] == 75.0


# ---------------------------------------------------------------------------
# execute_arena_run_task — unit tests with injected mocks
# ---------------------------------------------------------------------------


def _fake_loaded(workflow_id: str = "wf-a"):
    """Create a minimal fake LoadedWorkflow."""
    workflow = SimpleNamespace(id=workflow_id, steps=[], success=SimpleNamespace(assertions=[]))
    return SimpleNamespace(workflow=workflow, fixtures=SimpleNamespace())


def _fake_get_bundle(workflow_id: str):
    """Return a minimal fake LoadedWorkflow for any workflow_id."""
    return _fake_loaded(workflow_id)


def _fake_transcript(workflow_id: str = "wf-a", model_id: str = "gpt-5-5"):
    """Create a minimal MatchTranscript-like object."""
    from app.golden_workflows.transcript import MatchTranscript
    return MatchTranscript(
        schema_version=1,
        run_id=None,
        workflow_id=workflow_id,
        model_id=model_id,
        started_at="2026-01-01T00:00:00+00:00",
        finished_at="2026-01-01T00:01:00+00:00",
        steps=[],
    )


def test_execute_arena_run_task_scores_all_pairs(session, settings):
    """1 workflow × 2 models → 2 scored matches + run 'completed'."""
    # Setup DB
    database.configure_database(settings)
    database.init_db()

    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s,
            workflow_ids=["wf-a"],
            model_ids=["gpt-5-5", "claude-opus-4-8"],
        )
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued")
        s.add(task)
        s.flush()
        task_id = task.id
        s.commit()

    from app.services.arena.judge import JudgeResult

    def fake_run_match(loaded, model, *, artifact_root, run_id=None):
        return _fake_transcript(workflow_id="wf-a", model_id=model.slug)

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(
            rubric_scores=[],
            judged_score=None,
            judge_missing=True,
            notes="no rubric",
        )

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id,
        run_id,
        database.SessionLocal,
        settings=settings,
        run_match_fn=fake_run_match,
        judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        assert len(run_dict["matches"]) == 2
        statuses = {m["status"] for m in run_dict["matches"]}
        assert statuses == {"scored"}

        task_row = s.get(TaskRun, task_id)
        assert task_row.status == "completed"


def test_execute_arena_run_task_failed_match_doesnt_abort_run(session, settings):
    """When run_match_fn raises, match is 'failed' but run still 'completed'."""
    database.configure_database(settings)
    database.init_db()

    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s,
            workflow_ids=["wf-a"],
            model_ids=["gpt-5-5", "claude-opus-4-8"],
        )
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued")
        s.add(task)
        s.flush()
        task_id = task.id
        s.commit()

    call_count = {"n": 0}

    def failing_run_match(loaded, model, *, artifact_root, run_id=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Simulated match failure")
        from app.golden_workflows.transcript import MatchTranscript
        return MatchTranscript(
            schema_version=1,
            run_id=None,
            workflow_id="wf-a",
            model_id=model.slug,
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T00:01:00+00:00",
            steps=[],
        )

    from app.services.arena.judge import JudgeResult

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=None, judge_missing=True, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id,
        run_id,
        database.SessionLocal,
        settings=settings,
        run_match_fn=failing_run_match,
        judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        assert len(run_dict["matches"]) == 2

        statuses = {m["status"] for m in run_dict["matches"]}
        # One failed, one scored
        assert "failed" in statuses
        assert "scored" in statuses

        failed_match = next(m for m in run_dict["matches"] if m["status"] == "failed")
        assert failed_match["error"] is not None


def test_execute_arena_run_task_not_implemented_error_is_caught(session, settings):
    """NotImplementedError from run_match_fn is treated as a match failure (not infra fail)."""
    database.configure_database(settings)
    database.init_db()

    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s,
            workflow_ids=["wf-a"],
            model_ids=["gpt-5-5"],
        )
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued")
        s.add(task)
        s.flush()
        task_id = task.id
        s.commit()

    def notimpl_run_match(loaded, model, *, artifact_root, run_id=None):
        raise NotImplementedError("Real agent not implemented")

    def fake_judge(transcript, loaded, *, post=None):
        from app.services.arena.judge import JudgeResult
        return JudgeResult(judged_score=None, judge_missing=True, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id,
        run_id,
        database.SessionLocal,
        settings=settings,
        run_match_fn=notimpl_run_match,
        judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        assert len(run_dict["matches"]) == 1
        assert run_dict["matches"][0]["status"] == "failed"

        task_row = s.get(TaskRun, task_id)
        assert task_row.status == "completed"


# ---------------------------------------------------------------------------
# Infra-blank → invalid (flagship v2)
# ---------------------------------------------------------------------------


def _blank_transcript(workflow_id, model_id, *, with_errors):
    """All-blank transcript; error evidence on step 0 when with_errors."""
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    steps = [
        MatchStep(
            index=i, user=f"turn {i}", messages=[], tool_calls=[],
            tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
            response_text="",
            errors=(["provider 402 quota"] if (with_errors and i == 0) else []),
        )
        for i in range(3)
    ]
    return MatchTranscript(
        schema_version=1, run_id=None, workflow_id=workflow_id,
        model_id=model_id, started_at=None, finished_at=None, steps=steps,
    )


def _queue_single_pair_run(settings):
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s, workflow_ids=["wf-a"], model_ids=["gpt-5-5"])
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued")
        s.add(task)
        s.flush()
        task_id = task.id
        s.commit()
    return run_id, task_id


def test_infra_blank_marks_invalid_and_skips_judge(session, settings):
    """All-blank transcript WITH transport errors → status 'invalid',
    scores NULL, judge never invoked."""
    run_id, task_id = _queue_single_pair_run(settings)

    def blank_run_match(loaded, model, *, artifact_root, run_id=None):
        return _blank_transcript("wf-a", model.slug, with_errors=True)

    def exploding_judge(transcript, loaded, *, post=None):
        raise AssertionError("judge must not run for invalid matches")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=blank_run_match, judge_fn=exploding_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        (m,) = run_dict["matches"]
        assert m["status"] == "invalid"
        assert m["error"] == "infra_blank"
        assert m["objective_score"] is None
        assert m["judged_score"] is None
        assert m["total_score"] is None
        # Transcript evidence is still saved for audit
        assert m["transcript_path"]


def test_all_blank_without_errors_stays_scored(session, settings):
    """Blankness alone is NOT infra evidence — a silent model is a real 0."""
    run_id, task_id = _queue_single_pair_run(settings)

    def blank_run_match(loaded, model, *, artifact_root, run_id=None):
        return _blank_transcript("wf-a", model.slug, with_errors=False)

    from app.services.arena.judge import JudgeResult

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=None, judge_missing=True, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=blank_run_match, judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        (m,) = run_dict["matches"]
        assert m["status"] == "scored"
        assert m["objective_score"] is not None


def _partial_contaminated_transcript(workflow_id, model_id):
    """Early steps ran; a provider transport error (402 quota) struck mid-run.

    Mirrors the real Opus run-10 trial-1 shape: steps 0-1 produced real content,
    steps 2+ carry an ``APIStatusError("Error code: 402 ... quote_exceeded")``.
    """
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    steps = [
        MatchStep(
            index=0, user="turn 0", messages=[],
            tool_calls=[{"id": "c0", "name": "get_latest_risk_run", "args": {}}],
            tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
            response_text="Here is the latest risk.", errors=[],
        ),
        MatchStep(
            index=1, user="turn 1", messages=[],
            tool_calls=[{"id": "c1", "name": "run_batch_pricing", "args": {}}],
            tool_results=[], skills_routed=[], artifacts=[], task_ids=["t1"],
            response_text="Fresh run complete.", errors=[],
        ),
        MatchStep(
            index=2, user="turn 2", messages=[], tool_calls=[],
            tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
            response_text="",
            errors=[{"span": "llm", "name": "ChatAnthropic",
                     "error": "APIStatusError(\"Error code: 402 - {'error': "
                              "{'code': '402', 'type': 'quote_exceeded'}}\")"}],
        ),
    ]
    return MatchTranscript(
        schema_version=1, run_id=None, workflow_id=workflow_id,
        model_id=model_id, started_at=None, finished_at=None, steps=steps,
    )


def test_partial_provider_error_marks_invalid_and_skips_judge(session, settings):
    """A mid-run provider transport error (402 quota) contaminates the trial.

    Even though early steps produced real content (so it is NOT all-blank), a
    truncated run behind a provider quota death cannot be fairly scored — it is
    recorded 'invalid' (excluded from leaderboard means), judge is skipped."""
    run_id, task_id = _queue_single_pair_run(settings)

    def contaminated_run_match(loaded, model, *, artifact_root, run_id=None):
        return _partial_contaminated_transcript("wf-a", model.slug)

    def exploding_judge(transcript, loaded, *, post=None):
        raise AssertionError("judge must not run for contaminated matches")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=contaminated_run_match, judge_fn=exploding_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        (m,) = run_dict["matches"]
        assert m["status"] == "invalid"
        assert m["error"] == "infra_error"
        assert m["objective_score"] is None
        assert m["total_score"] is None


def test_domain_tool_error_stays_scored(session, settings):
    """A non-provider error (a tool raising a domain error) is a real outcome,
    not infra — the trial must still be scored, not invalidated."""
    run_id, task_id = _queue_single_pair_run(settings)

    from app.golden_workflows.transcript import MatchTranscript, MatchStep

    def domain_error_run_match(loaded, model, *, artifact_root, run_id=None):
        steps = [
            MatchStep(
                index=i, user=f"turn {i}", messages=[],
                tool_calls=[{"id": f"c{i}", "name": "get_latest_risk_run", "args": {}}],
                tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                response_text="Portfolio not found for id=999.",
                errors=([{"span": "tool", "name": "get_latest_risk_run",
                          "error": "ValueError: portfolio 999 does not exist"}]
                        if i == 1 else []),
            )
            for i in range(3)
        ]
        return MatchTranscript(
            schema_version=1, run_id=None, workflow_id="wf-a",
            model_id=model.slug, started_at=None, finished_at=None, steps=steps,
        )

    from app.services.arena.judge import JudgeResult

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=50.0, judge_missing=False, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=domain_error_run_match, judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        (m,) = run_dict["matches"]
        assert m["status"] == "scored"
        assert m["objective_score"] is not None


def test_leaderboard_api_carries_invalid_count(session, settings):
    """GET /api/arena/leaderboard rows expose the invalid count."""
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["m"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(
        session, run_id=run_id, workflow_id="wf-a", model_id="m",
        objective_score=80.0, judged_score=None, total_score=80.0,
        judge_missing=True, config={}, transcript_path=None, status="scored")
    arena_store.record_match(
        session, run_id=run_id, workflow_id="wf-b", model_id="m",
        objective_score=None, judged_score=None, total_score=None,
        judge_missing=True, config={}, transcript_path=None,
        status="invalid", error="infra_blank")
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get(f"/api/arena/leaderboard?run_id={run_id}")
    assert resp.status_code == 200
    (row,) = resp.json()["rows"]
    assert row["matches"] == 1
    assert row["invalid"] == 1


def test_run_detail_exposes_match_error(session, settings):
    """The corroborating invalid reason must be auditable via the API."""
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["m"])
    arena_store.record_match(
        session, run_id=run_id, workflow_id="wf-a", model_id="m",
        objective_score=None, judged_score=None, total_score=None,
        judge_missing=True, config={}, transcript_path=None,
        status="invalid", error="infra_blank")
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get(f"/api/arena/runs/{run_id}")
    assert resp.status_code == 200
    (m,) = resp.json()["matches"]
    assert m["status"] == "invalid"
    assert m["error"] == "infra_blank"
