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
    # Derive-on-read adds an ability card (null here — no axes); stored content
    # round-trips unchanged (spec B8).
    got = match["score_breakdown"]
    assert {k: got[k] for k in breakdown} == breakdown
    assert got["card"] is None and got["card_reason"] == "legacy_no_axes"


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


def test_leaderboard_keys_are_rank_objective_subjective(session, settings):
    """Router ranks by objective (no blend) and exposes advisory subjective + rank."""
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    arena_store.set_run_status(session, run_id, "completed")
    arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=60.0, judged_score=70.0, total_score=60.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 70.0, "judged_stdev": 4.0}, "subjective_mode": "panel"},
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/leaderboard")
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 1
    row = rows[0]
    assert row["rank"] == 1
    assert row["avg_objective"] == 60.0
    assert row["subjective_mean"] == 70.0 and row["subjective_stdev"] == 4.0
    assert row["subjective_mode"] == "panel"
    assert "avg_total" not in row and "mean_total" not in row  # blend dropped
    assert row["matches"] == 1


def test_leaderboard_exposes_ovr(session, settings):
    """A carded row surfaces OVR + the full card_mean stat block on the API."""
    run_id = arena_store.create_run(
        session, workflow_ids=["risk-manager-control-day"], model_ids=["m1"])
    arena_store.set_run_status(session, run_id, "completed")
    axes = {"grounding": {"passed": 9, "total": 10}, "adherence": {"passed": 10, "total": 11},
            "synthesis": {"passed": 4, "total": 5}, "procedural": {"passed": 5, "total": 6}}
    arena_store.record_match(
        session, run_id, "risk-manager-control-day", "m1",
        objective_score=80.0, judged_score=None, total_score=80.0,
        judge_missing=False, config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": axes},
                         "diagnosis": {"counts_detail": {"tool_calls": 11}}},
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/leaderboard")
    assert resp.status_code == 200
    row = resp.json()["rows"][0]
    assert "ovr" in row and "card_mean" in row
    assert row["ovr"] == row["card_mean"]["ovr"]
    # card_mean carries the five stats + headline OVR, plus the CON block:
    # base_ovr (pre-blend) and con (None here — a single-match model has no
    # dispersion to measure, so OVR stays at the base).
    assert set(row["card_mean"]) == {"ovr", "base_ovr", "con",
                                     "GRD", "ADH", "SYN", "PRC", "EFF"}
    assert row["card_mean"]["con"] is None
    assert row["card_mean"]["ovr"] == row["card_mean"]["base_ovr"]


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
    def fake_queue(sess, *, workflow_ids, model_ids, weights=None, trials=1):
        raise ValueError(f"Unknown model id(s): {model_ids}")

    client = _make_arena_app(session, settings, queue_fn=fake_queue)
    resp = client.post(
        "/api/arena/runs",
        json={"workflow_ids": ["wf-a"], "model_ids": ["nonexistent-model"]},
    )
    assert resp.status_code == 422


def test_post_runs_unknown_workflow_returns_422(session, settings):
    def fake_queue(sess, *, workflow_ids, model_ids, weights=None, trials=1):
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

    def fake_queue(sess, *, workflow_ids, model_ids, weights=None, trials=1):
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


def test_execute_arena_run_task_jury_off_scores_objective_only(session, settings, monkeypatch):
    """Default (arena_jury_enabled False, no judge_fn): score objective-only, no jury
    call at all, and stamp subjective_mode='disabled' (distinct from a failed jury)."""
    database.configure_database(settings)
    database.init_db()

    # Any attempt to run the default jury when the flag is off is a bug.
    def _boom(*a, **k):
        raise AssertionError("jury must not run when arena_jury_enabled is False")
    monkeypatch.setattr("app.services.arena.judge.judge_panel", _boom)

    with database.SessionLocal() as s:
        run_id = arena_store.create_run(s, workflow_ids=["wf-a"], model_ids=["gpt-5-5"])
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued")
        s.add(task); s.flush(); task_id = task.id; s.commit()

    def fake_run_match(loaded, model, *, artifact_root, run_id=None):
        return _fake_transcript(workflow_id="wf-a", model_id=model.slug)

    from app.services.arena.task import execute_arena_run_task
    # NOTE: no judge_fn → exercises the production flag branch.
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal,
        settings=settings, run_match_fn=fake_run_match, get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        m = run_dict["matches"][0]
        assert m["status"] == "scored"
        assert m["judged_score"] is None
        # Top-level contract: a deliberate opt-out is NOT a jury outage — consumers
        # keying off judge_missing must not flag it. Provenance is subjective_mode.
        assert m["judge_missing"] is False
        assert m["total_score"] == m["objective_score"]
        bd = m["score_breakdown"]
        assert "judge" not in bd
        assert bd["subjective_mode"] == "disabled"
        # Ability card stamped at write time (spec B7); jury-off → jdg None.
        assert set(bd["card"]["stats"]) == {"GRD", "ADH", "SYN", "PRC", "EFF"}
        assert isinstance(bd["card"]["ovr"], int)
        assert bd["card"]["jdg"] is None


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


def test_provider_error_regex_classifies_transport_drops_not_line_numbers():
    """The provider-error signature must catch httpx transport drops and the
    ZenMux account-gate body, WITHOUT false-matching source line numbers.

    Run #33 surfaced two gaps: ``RemoteProtocolError('peer closed connection')``
    (a real mid-stream network death) slipped through as a scored low, while a
    greedy ``\\b402\\b`` would have matched ``factory.py, line 402`` in a
    traceback. Both directions are locked here so the classifier can't regress.
    """
    from app.services.arena.task import _PROVIDER_ERROR_RE as R

    # Transport / account-gate signatures that MUST be treated as infra.
    for s in (
        "RemoteProtocolError('peer closed connection without sending complete "
        "message body (incomplete chunked read)')",
        "StreamChunkTimeoutError('No streaming chunk received for 120.0s')",
        "APIStatusError(\"Error code: 402 - {'type': 'reject_no_credit'}\")",
        "insufficient balance",
        "httpx.APIConnectionError: Connection error",
        "Error code: 429 - rate_limit",
        "503 Service Unavailable",
    ):
        assert R.search(s), f"provider signature not detected: {s!r}"

    # Non-infra strings that must NOT match — chiefly bare status-code-shaped
    # line numbers in tracebacks (the false positive that mis-flagged kimi-2-7),
    # and genuine domain errors the agent is meant to handle.
    for s in (
        'File "…/agents/factory.py", line 402, in composed',
        'File "…/pricing.py", line 502, in run_batch_pricing',
        "ValueError('Unsupported kwargs for BarrierOption: initial_price')",
        "artifact section not found: product_spec",
        "GraphRecursionError('Recursion limit of 100 reached')",
    ):
        assert not R.search(s), f"false-positive infra match on: {s!r}"


def test_remote_protocol_drop_marks_invalid(session, settings):
    """A mid-run ``RemoteProtocolError`` (peer-closed connection) with no
    completed response is a transport death — recorded 'invalid', judge skipped.

    This is the Run #33 longcat gap: the drop left a step without a response yet
    the old regex missed the httpx wording, so the trial folded as a scored low
    instead of being gated. With the widened signature it must gate like a 402.
    """
    run_id, task_id = _queue_single_pair_run(settings)
    from app.golden_workflows.transcript import MatchTranscript, MatchStep

    def dropped(loaded, model, *, artifact_root, run_id=None):
        steps = [
            MatchStep(index=0, user="t0", messages=[],
                      tool_calls=[{"id": "c0", "name": "get_latest_risk_run", "args": {}}],
                      tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                      response_text="Latest risk is X.", errors=[]),
            MatchStep(index=1, user="t1", messages=[], tool_calls=[],
                      tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                      response_text="",
                      errors=[{"span": "llm", "name": "ChatOpenAI",
                               "error": "RemoteProtocolError('peer closed connection "
                                        "without sending complete message body "
                                        "(incomplete chunked read)')"}]),
        ]
        return MatchTranscript(
            schema_version=1, run_id=None, workflow_id="wf-a",
            model_id=model.slug, started_at=None, finished_at=None, steps=steps,
        )

    def exploding_judge(transcript, loaded, *, post=None):
        raise AssertionError("judge must not run for a transport-dropped match")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=dropped, judge_fn=exploding_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        (m,) = run_dict["matches"]
        assert m["status"] == "invalid"
        assert m["error"] == "infra_error"
        assert m["objective_score"] is None


def test_toolcall_then_provider_death_marks_invalid(session, settings):
    """A step that issues a tool call but whose FINAL response dies on a 402
    (empty response_text + provider error) is a partial death, not recovery —
    a mere issued tool call must NOT count as a completed turn."""
    run_id, task_id = _queue_single_pair_run(settings)
    from app.golden_workflows.transcript import MatchTranscript, MatchStep

    def contaminated(loaded, model, *, artifact_root, run_id=None):
        steps = [
            MatchStep(index=0, user="t0", messages=[],
                      tool_calls=[{"id": "c0", "name": "get_latest_risk_run", "args": {}}],
                      tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                      response_text="Latest risk is X.", errors=[]),
            MatchStep(index=1, user="t1", messages=[],
                      tool_calls=[{"id": "c1", "name": "run_batch_pricing", "args": {}}],
                      tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                      response_text="",  # final response died after the tool call
                      errors=[{"span": "llm", "name": "ChatAnthropic",
                               "error": "APIStatusError(\"Error code: 402 ... quote_exceeded\")"}]),
        ]
        return MatchTranscript(schema_version=1, run_id=None, workflow_id="wf-a",
                               model_id=model.slug, started_at=None, finished_at=None, steps=steps)

    def exploding_judge(transcript, loaded, *, post=None):
        raise AssertionError("judge must not run for contaminated matches")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(task_id, run_id, database.SessionLocal, settings=settings,
                           run_match_fn=contaminated, judge_fn=exploding_judge,
                           get_bundle_fn=_fake_get_bundle)
    with database.SessionLocal() as s:
        (m,) = arena_store.get_run(s, run_id)["matches"]
        assert m["status"] == "invalid"
        assert m["error"] == "infra_error"


def test_judge_missing_stays_objective_scored(session, settings):
    """A match whose jury is unavailable (judge_missing) is still a valid
    objective-scored match — NOT invalid (subjective is advisory, objective is
    the spine)."""
    run_id, task_id = _queue_single_pair_run(settings)
    from app.golden_workflows.transcript import MatchTranscript, MatchStep

    def good_run_match(loaded, model, *, artifact_root, run_id=None):
        steps = [MatchStep(index=i, user=f"t{i}", messages=[],
                           tool_calls=[{"id": f"c{i}", "name": "get_latest_risk_run", "args": {}}],
                           tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
                           response_text="A complete answer.", errors=[]) for i in range(3)]
        return MatchTranscript(schema_version=1, run_id=None, workflow_id="wf-a",
                               model_id=model.slug, started_at=None, finished_at=None, steps=steps)

    from app.services.arena.judge import JudgeResult

    def dead_panel(transcript, loaded, *, post=None):
        return JudgeResult(judge_missing=True, judged_score=None, subjective_mode="missing")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(task_id, run_id, database.SessionLocal, settings=settings,
                           run_match_fn=good_run_match, judge_fn=dead_panel,
                           get_bundle_fn=_fake_get_bundle)
    with database.SessionLocal() as s:
        (m,) = arena_store.get_run(s, run_id)["matches"]
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


# ---------------------------------------------------------------------------
# Multi-trial _execute — fold N trials per pair into one aggregate match
# ---------------------------------------------------------------------------


def _scorable_transcript(workflow_id: str, model_id: str):
    """A non-blank, error-free transcript with tool calls — passes the infra
    gates and yields a numeric objective_score."""
    from app.golden_workflows.transcript import MatchTranscript, MatchStep
    steps = [
        MatchStep(
            index=i, user=f"turn {i}", messages=[],
            tool_calls=[{"id": f"c{i}", "name": "get_latest_risk_run", "args": {}}],
            tool_results=[], skills_routed=[], artifacts=[], task_ids=[],
            response_text="A complete answer.", errors=[],
        )
        for i in range(3)
    ]
    return MatchTranscript(
        schema_version=1, run_id=None, workflow_id=workflow_id,
        model_id=model_id, started_at=None, finished_at=None, steps=steps,
    )


def _queue_pair_run(settings, *, trials: int):
    database.configure_database(settings)
    database.init_db()
    with database.SessionLocal() as s:
        run_id = arena_store.create_run(
            s, workflow_ids=["wf-a"], model_ids=["gpt-5-5"], trials=trials)
        task = TaskRun(kind=TaskKind.ARENA_RUN.value, status="queued",
                       progress_current=0, progress_total=trials, message="")
        s.add(task)
        s.flush()
        task_id = task.id
        s.commit()
    return run_id, task_id


def test_execute_folds_multiple_trials_into_one_match(session, settings):
    """trials=3, all clean → exactly ONE scored match whose score_breakdown
    aggregates all 3 trials (n_trials==3, aggregate has 3 entries), and
    run_match_fn is called exactly 3 times."""
    run_id, task_id = _queue_pair_run(settings, trials=3)

    calls = {"n": 0}

    def fake_run_match(loaded, model, *, artifact_root, run_id=None):
        calls["n"] += 1
        return _scorable_transcript("wf-a", model.slug)

    from app.services.arena.judge import JudgeResult

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=None, judge_missing=True, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=fake_run_match, judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        assert calls["n"] == 3
        scored = [m for m in run_dict["matches"] if m["status"] == "scored"]
        assert len(scored) == 1
        bd = scored[0]["score_breakdown"]
        assert bd["n_trials"] == 3
        assert len(bd["aggregate"]) == 3
        assert scored[0]["config"]["trials"] == 3

        task_row = s.get(TaskRun, task_id)
        assert task_row.status == "completed"
        assert task_row.progress_total == 3


def test_execute_trials_one_is_behavior_preserving(session, settings):
    """trials=1 (the default/historical path) → one scored match, n_trials==1,
    a single aggregate entry, and run_match_fn is called exactly once."""
    run_id, task_id = _queue_pair_run(settings, trials=1)

    calls = {"n": 0}

    def fake_run_match(loaded, model, *, artifact_root, run_id=None):
        calls["n"] += 1
        return _scorable_transcript("wf-a", model.slug)

    from app.services.arena.judge import JudgeResult

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=None, judge_missing=True, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=fake_run_match, judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        assert calls["n"] == 1
        (m,) = run_dict["matches"]
        assert m["status"] == "scored"
        bd = m["score_breakdown"]
        assert bd["n_trials"] == 1
        assert len(bd["aggregate"]) == 1


def test_execute_all_trials_infra_marks_invalid(session, settings):
    """trials=3, every trial infra-gated (all-blank + errors) → ONE invalid
    match — infra trials are skipped, not retried into a scored aggregate."""
    run_id, task_id = _queue_pair_run(settings, trials=3)

    calls = {"n": 0}

    def blank_run_match(loaded, model, *, artifact_root, run_id=None):
        calls["n"] += 1
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
        assert calls["n"] == 3
        (m,) = run_dict["matches"]
        assert m["status"] == "invalid"
        assert m["error"] == "infra_blank"
        assert m["objective_score"] is None


def test_execute_one_of_three_trials_infra_folds_remaining_two(session, settings):
    """trials=3, 1 infra-gated + 2 clean → ONE scored match aggregating just
    the 2 clean trials (n_trials==2) — the infra trial is skipped, not
    counted, and not retried."""
    run_id, task_id = _queue_pair_run(settings, trials=3)

    calls = {"n": 0}

    def mixed_run_match(loaded, model, *, artifact_root, run_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _blank_transcript("wf-a", model.slug, with_errors=True)
        return _scorable_transcript("wf-a", model.slug)

    from app.services.arena.judge import JudgeResult

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=None, judge_missing=True, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=mixed_run_match, judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        assert calls["n"] == 3
        (m,) = run_dict["matches"]
        assert m["status"] == "scored"
        bd = m["score_breakdown"]
        assert bd["n_trials"] == 2
        assert len(bd["aggregate"]) == 2


def test_execute_multi_trial_rolls_up_judged_score_onto_aggregate(session, settings):
    """trials=2, judge_fn injected → the scored match's top-level judged_score
    column and score_breakdown["judge"] reflect the mean of the per-trial
    judged scores (not None/dropped), and store.leaderboard's subjective_mean
    picks it up too. Regression test for _record_pair hardcoding
    judged_score=None/judge_missing=False on the aggregate row."""
    run_id, task_id = _queue_pair_run(settings, trials=2)

    calls = {"n": 0}

    def fake_run_match(loaded, model, *, artifact_root, run_id=None):
        calls["n"] += 1
        return _scorable_transcript("wf-a", model.slug)

    from app.services.arena.judge import JudgeResult

    scores = iter([60.0, 80.0])

    def fake_judge(transcript, loaded, *, post=None):
        return JudgeResult(judged_score=next(scores), judge_missing=False, notes="")

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=fake_run_match, judge_fn=fake_judge,
        get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        assert run_dict["status"] == "completed"
        (m,) = run_dict["matches"]
        assert m["status"] == "scored"
        # Mean of the two per-trial judged scores (60.0, 80.0) == 70.0.
        assert m["judged_score"] == 70.0
        bd = m["score_breakdown"]
        assert bd["judge"]["judged_score"] == 70.0
        assert bd["judge"]["judge_missing"] is False

        rows = arena_store.leaderboard(s, run_id=run_id)
        row = next(r for r in rows if r["model_id"] == "gpt-5-5")
        assert row["subjective_mean"] == 70.0


def test_execute_jury_off_run_stays_behavior_preserving(session, settings):
    """No judge_fn injected, arena_jury_enabled False (default) → the scored
    match keeps judged_score=None and NO top-level judge key in the
    aggregate breakdown (subjective_mode=="disabled"), matching pre-fix
    behavior exactly."""
    run_id, task_id = _queue_pair_run(settings, trials=2)

    def fake_run_match(loaded, model, *, artifact_root, run_id=None):
        return _scorable_transcript("wf-a", model.slug)

    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(
        task_id, run_id, database.SessionLocal, settings=settings,
        run_match_fn=fake_run_match, get_bundle_fn=_fake_get_bundle,
    )

    with database.SessionLocal() as s:
        run_dict = arena_store.get_run(s, run_id)
        (m,) = run_dict["matches"]
        assert m["status"] == "scored"
        assert m["judged_score"] is None
        bd = m["score_breakdown"]
        assert "judge" not in bd
        assert bd["subjective_mode"] == "disabled"


def test_queue_arena_run_threads_trials(session):
    """queue_arena_run(trials=N) persists trials on the run and scales
    progress_total by pairs × trials."""
    from app.services.arena.task import queue_arena_run

    run_obj, task = queue_arena_run(
        session, workflow_ids=["risk-manager-control-day"],
        model_ids=["gpt-5-5", "claude-opus-4-8"],
        trials=4,
    )
    session.commit()

    run_dict = arena_store.get_run(session, run_obj.id)
    assert run_dict["trials"] == 4
    assert task.progress_total == 2 * 4


# ---------------------------------------------------------------------------
# POST /api/arena/runs/delete, POST /api/arena/runs/merge, GET /api/arena/workflows
# ---------------------------------------------------------------------------


def test_delete_runs_endpoint_removes_run_and_files(session, settings, tmp_path):
    """Happy path: seed a run + a match with a transcript file and an
    artifact_dir/arena/{run_id} directory; deleting removes the DB rows AND
    the filesystem artifacts."""
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    transcript_file = tmp_path / "transcript.json"
    transcript_file.write_text("{}", encoding="utf-8")
    arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=80.0, judged_score=None, total_score=80.0,
        judge_missing=True, config={}, transcript_path=str(transcript_file),
        status="scored",
    )
    session.commit()

    arena_dir = Path(settings.artifact_dir) / "arena" / str(run_id)
    arena_dir.mkdir(parents=True, exist_ok=True)
    (arena_dir / "artifact.txt").write_text("x", encoding="utf-8")

    client = _make_arena_app(session, settings)
    resp = client.post("/api/arena/runs/delete", json={"run_ids": [run_id]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["deleted_run_ids"] == [run_id]
    assert data["match_count"] == 1
    assert data["files_removed"] >= 2

    assert arena_store.get_run(session, run_id) is None
    assert not transcript_file.exists()
    assert not arena_dir.exists()


def test_delete_runs_empty_400(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.post("/api/arena/runs/delete", json={"run_ids": []})
    assert resp.status_code == 400


def test_merge_runs_needs_two_400(session, settings):
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    arena_store.record_match(
        session, run_id, "wf-a", "model-x",
        objective_score=80.0, judged_score=None, total_score=80.0,
        judge_missing=True, config={}, transcript_path=None, status="scored",
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.post("/api/arena/runs/merge", json={"source_run_ids": [run_id]})
    assert resp.status_code == 400


def test_merge_runs_folds_two_runs(session, settings):
    """Two distinct scored runs merge into a new aggregate run."""
    run_a = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    arena_store.record_match(
        session, run_a, "wf-a", "model-x",
        objective_score=70.0, judged_score=None, total_score=70.0,
        judge_missing=True, config={}, transcript_path=None, status="scored",
    )
    run_b = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["model-x"])
    arena_store.record_match(
        session, run_b, "wf-a", "model-x",
        objective_score=90.0, judged_score=None, total_score=90.0,
        judge_missing=True, config={}, transcript_path=None, status="scored",
    )
    session.commit()

    client = _make_arena_app(session, settings)
    resp = client.post(
        "/api/arena/runs/merge", json={"source_run_ids": [run_a, run_b]}
    )
    assert resp.status_code == 200
    new_run_id = resp.json()["run_id"]
    assert new_run_id not in (run_a, run_b)
    assert arena_store.get_run(session, new_run_id) is not None


def test_workflows_endpoint_shape(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.get("/api/arena/workflows")
    assert resp.status_code == 200
    body = resp.json()
    assert "workflows" in body
    assert len(body["workflows"]) > 0
    assert {"id", "title", "tags", "step_count"} <= set(body["workflows"][0])


def test_create_run_trials_bounds_422(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.post(
        "/api/arena/runs",
        json={
            "workflow_ids": ["risk-manager-control-day"],
            "model_ids": ["gpt-5-5"],
            "trials": 99,
        },
    )
    assert resp.status_code == 422


def test_create_run_trials_zero_422(session, settings):
    client = _make_arena_app(session, settings)
    resp = client.post(
        "/api/arena/runs",
        json={
            "workflow_ids": ["risk-manager-control-day"],
            "model_ids": ["gpt-5-5"],
            "trials": 0,
        },
    )
    assert resp.status_code == 422
