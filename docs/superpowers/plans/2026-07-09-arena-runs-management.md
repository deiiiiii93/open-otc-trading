# Arena Runs Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add New Run (multi-trial, multi-model launch), hard Delete, and non-destructive Merge to the Arena Runs panel, wired end-to-end backend→frontend.

**Architecture:** A shared `scoring.fold_trial_breakdowns` kernel folds N single-match breakdowns into the existing multi-trial aggregate shape; the arena task's `_execute` gains a per-pair trial loop over that kernel, and `merge_runs` is refactored onto it. New router endpoints expose delete (store-DB + router-filesystem split), merge, and a workflows list. The frontend runs panel gains per-row selection checkboxes + an action bar, a New Run modal (workflows × models × trials), and status polling.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend), pytest; React 19 + TypeScript + Radix + vitest (frontend); token-only "Warm Ledger" CSS.

## Global Constraints

- Tests: backend `.venv/bin/python -m pytest`; frontend `cd frontend && npm test` (vitest), type-check `npx tsc --noEmit`.
- Migrations are the upgrade path; **migration-local Core SQL / `sa.Table` on fresh MetaData only — never import app models/services** (they drift to the future schema).
- New migration is **0045**, `down_revision = "0044_hedge_tag"` (0044 is taken by hedge_tag).
- LLM channel config unchanged (no `config/agent_channels.*` edits).
- Frontend: **token-only styling** (`var(--token)` from `src/tokens/`), BEM `wl-` names, one co-located `.css`; reuse `Modal`/`Button`/`Input`/`NumberInput`; verify light + dark + compact density.
- Trials range: `1 ≤ trials ≤ 10`; New Run default trials = **2**.
- Delete = hard (rows via cascade + transcript files + `arena/<run_id>` dir); Merge = non-destructive (reuse `store.merge_runs`); infra trials are skipped, not retried.
- Before opening a PR/merging: update `CHANGELOG.md` (`[Unreleased]`) and `CLAUDE.md`.

---

### Task 1: Shared trial-fold kernel (`scoring.fold_trial_breakdowns`) + refactor `merge_runs`

**Files:**
- Modify: `backend/app/services/arena/scoring.py` (add function near `aggregate_card_from_trials`, ~line 228)
- Modify: `backend/app/services/arena/store.py:166-190` (`merge_runs` inner aggregate build)
- Test: `tests/test_arena_scoring.py` (fold unit), `tests/test_arena_store.py` (merge regression already present)

**Interfaces:**
- Produces: `scoring.fold_trial_breakdowns(trials: list[dict]) -> dict` returning
  `{n_trials, aggregate, objective, objective_score, objective_stdev, total_score, subjective_mode}`.
  Precondition: `trials` non-empty.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arena_scoring.py
def test_fold_trial_breakdowns_means_and_shape():
    from app.services.arena import scoring
    trials = [
        {"objective": {"axes": {"grounding": 1}}, "objective_score": 80.0,
         "subjective_mode": "disabled", "card": {"ovr": 70}},
        {"objective": {"axes": {"grounding": 0}}, "objective_score": 90.0,
         "subjective_mode": "disabled", "card": {"ovr": 72}},
    ]
    agg = scoring.fold_trial_breakdowns(trials)
    assert agg["n_trials"] == 2
    assert agg["aggregate"] == trials
    assert agg["objective"] == trials[0]["objective"]
    assert agg["objective_score"] == 85.0
    assert agg["objective_stdev"] == 5.0
    assert agg["total_score"] == 85.0
    assert agg["subjective_mode"] == "disabled"


def test_fold_trial_breakdowns_single_trial_zero_stdev():
    from app.services.arena import scoring
    agg = scoring.fold_trial_breakdowns([
        {"objective": {}, "objective_score": 88.5, "subjective_mode": "disabled"}])
    assert agg["n_trials"] == 1
    assert agg["objective_stdev"] == 0.0
    assert agg["objective_score"] == 88.5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py::test_fold_trial_breakdowns_means_and_shape -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'fold_trial_breakdowns'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/arena/scoring.py  (add near aggregate_card_from_trials)
import statistics

def fold_trial_breakdowns(trials: list[dict]) -> dict:
    """Fold N single-match breakdowns into the canonical multi-trial aggregate.

    Shared by store.merge_runs (folding cross-run trials) and the arena task's
    multi-trial New Run path. The CON ability card is derived on READ from the
    per-trial cards via aggregate_card_from_trials — not built here.
    """
    objs = [t["objective_score"] for t in trials
            if t.get("objective_score") is not None]
    obj_mean = round(sum(objs) / len(objs), 1) if objs else None
    obj_stdev = round(statistics.pstdev(objs), 1) if len(objs) > 1 else 0.0
    return {
        "n_trials": len(trials),
        "aggregate": trials,
        "objective": trials[0].get("objective"),
        "objective_score": obj_mean,
        "objective_stdev": obj_stdev,
        "total_score": obj_mean,
        "subjective_mode": trials[0].get("subjective_mode", "disabled"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -k fold_trial -v`
Expected: PASS (both)

- [ ] **Step 5: Refactor `merge_runs` onto the kernel**

Replace the inline aggregate build in `store.py` (currently ~166-190) so the per-group body reads:

```python
    from app.services.arena import scoring
    for (workflow_id, model_id), ms in groups.items():
        trials = [dict(m.score_breakdown) for m in ms if m.score_breakdown]
        if not trials:
            continue
        aggregate = scoring.fold_trial_breakdowns(trials)
        record_match(
            session, new_run_id, workflow_id, model_id,
            objective_score=aggregate["objective_score"], judged_score=None,
            total_score=aggregate["objective_score"], judge_missing=False,
            config={"merged_from": ordered}, transcript_path=None,
            status="scored", score_breakdown=aggregate,
        )
```

Delete the now-unused local `import statistics` in `merge_runs` if it is no longer referenced elsewhere in that function (Task 1's `fold_trial_breakdowns` owns the `statistics` import now).

- [ ] **Step 6: Run merge regression + full arena scoring/store suites**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py tests/test_arena_scoring.py -v`
Expected: PASS (merge_runs tests unchanged-green via the shared helper)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/arena/scoring.py backend/app/services/arena/store.py tests/test_arena_scoring.py
git commit -m "feat(arena): extract fold_trial_breakdowns; refactor merge_runs onto it"
```

---

### Task 2: `store.delete_runs` (DB-pure hard delete)

**Files:**
- Modify: `backend/app/services/arena/store.py` (add after `merge_runs`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Produces: `store.delete_runs(session, run_ids: list[int]) -> dict` →
  `{"deleted_run_ids": list[int], "transcript_paths": list[str], "match_count": int}`.
  Skips ids that don't exist. Nulls `agent_threads.arena_run_id` for deleted runs. No filesystem.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arena_store.py
def test_delete_runs_removes_runs_matches_and_returns_paths(db_session):
    from app.services.arena import store
    rid = store.create_run(db_session, ["wf"], ["m"])
    store.record_match(db_session, rid, "wf", "m", objective_score=80.0,
                       judged_score=None, total_score=80.0, judge_missing=False,
                       config={}, transcript_path="/x/t.json", status="scored",
                       score_breakdown={"objective_score": 80.0})
    db_session.commit()
    out = store.delete_runs(db_session, [rid, 999999])  # 999999 does not exist
    db_session.commit()
    assert out["deleted_run_ids"] == [rid]
    assert out["match_count"] == 1
    assert "/x/t.json" in out["transcript_paths"]
    assert store.get_run(db_session, rid) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py::test_delete_runs_removes_runs_matches_and_returns_paths -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'delete_runs'`

- [ ] **Step 3: Write minimal implementation**

```python
# backend/app/services/arena/store.py
from sqlalchemy import text as _sql_text

def delete_runs(session: Session, run_ids: list[int]) -> dict:
    """Hard-delete arena runs (+cascade matches). DB only — no filesystem.

    Returns deleted ids (only those that existed), the transcript_paths of their
    matches (for the caller to unlink), and the match_count removed. Nulls any
    agent_threads.arena_run_id pointing at a deleted run so no thread dangles.
    """
    ids = list(dict.fromkeys(run_ids))
    deleted: list[int] = []
    paths: list[str] = []
    match_count = 0
    for rid in ids:
        run = session.get(ArenaRun, rid)
        if run is None:
            continue
        for m in run.matches:
            match_count += 1
            if m.transcript_path:
                paths.append(m.transcript_path)
        session.delete(run)          # cascade="all, delete-orphan" drops matches
        deleted.append(rid)
    if deleted:
        session.execute(
            _sql_text("UPDATE agent_threads SET arena_run_id = NULL "
                      "WHERE arena_run_id IN :ids").bindparams(
                          sa.bindparam("ids", expanding=True)),
            {"ids": deleted},
        )
    return {"deleted_run_ids": deleted, "transcript_paths": paths, "match_count": match_count}
```

(If `sa` is not already imported in `store.py`, add `import sqlalchemy as sa` at top; if `Session`/`ArenaRun` imports exist, reuse them.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py::test_delete_runs_removes_runs_matches_and_returns_paths -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): store.delete_runs — DB-pure hard delete with dangling-ref nulling"
```

---

### Task 3: Migration 0045 + `ArenaRun.trials` + `create_run(trials=)` + surface in dict

**Files:**
- Create: `backend/alembic/versions/0045_arena_run_trials.py`
- Modify: `backend/app/models.py` (`ArenaRun`, ~line 1901 add `trials`)
- Modify: `backend/app/services/arena/store.py` (`create_run` signature+body; `_run_to_dict`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Produces: `ArenaRun.trials: int` (default 1); `store.create_run(session, workflow_ids, model_ids, weights=None, trials=1) -> int`; `_run_to_dict` includes `"trials"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arena_store.py
def test_create_run_persists_trials(db_session):
    from app.services.arena import store
    rid = store.create_run(db_session, ["wf"], ["m"], trials=3)
    db_session.commit()
    assert store.get_run(db_session, rid)["trials"] == 3

def test_create_run_defaults_trials_to_one(db_session):
    from app.services.arena import store
    rid = store.create_run(db_session, ["wf"], ["m"])
    db_session.commit()
    assert store.get_run(db_session, rid)["trials"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k trials -v`
Expected: FAIL (`create_run` has no `trials` kwarg / dict lacks `trials`)

- [ ] **Step 3: Add the model column**

In `backend/app/models.py` `ArenaRun` (after `weights`, ~line 1903):

```python
    trials: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
```

- [ ] **Step 4: Write the migration**

```python
# backend/alembic/versions/0045_arena_run_trials.py
"""arena_run.trials — number of trials folded per (workflow, model) match

HOUSE RULE: migration-local Core only — never import app models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0045_arena_run_trials"
down_revision = "0044_hedge_tag"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "arena_run",
        sa.Column("trials", sa.Integer(), nullable=False, server_default="1"),
    )


def downgrade() -> None:
    op.drop_column("arena_run", "trials")
```

- [ ] **Step 5: Wire create_run + _run_to_dict**

`create_run`:

```python
def create_run(session, workflow_ids, model_ids, weights=None, trials=1) -> int:
    run = ArenaRun(status="queued", workflow_ids=workflow_ids,
                   model_ids=model_ids, weights=weights, trials=trials)
    ...
```

`_run_to_dict` (add a line):

```python
        "trials": run.trials,
```

- [ ] **Step 6: Apply migration + run tests**

Run: `.venv/bin/python -m alembic upgrade head && .venv/bin/python -m pytest tests/test_arena_store.py -k trials -v`
Expected: migration applies; both tests PASS

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0045_arena_run_trials.py backend/app/models.py backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): arena_run.trials column + create_run(trials=) (mig 0045)"
```

---

### Task 4: `_execute` multi-trial loop + `queue_arena_run(trials=)`

**Files:**
- Modify: `backend/app/services/arena/task.py` (`queue_arena_run` ~24-84; `_execute` ~219-436)
- Test: `tests/test_arena_api.py` (or the existing arena task test module)

**Interfaces:**
- Consumes: `scoring.fold_trial_breakdowns` (Task 1), `store.create_run(trials=)` (Task 3).
- Produces: `queue_arena_run(session, *, workflow_ids, model_ids, weights=None, trials=1)`; `_execute` records one aggregate scored match per pair over `run_dict["trials"]` trials.

- [ ] **Step 1: Write the failing test** (inject a fake `run_match_fn`; assert aggregate)

```python
# tests/test_arena_api.py
def test_execute_folds_multiple_trials_into_one_match(db_session, monkeypatch):
    from app.services.arena import task, store
    rid = store.create_run(db_session, ["risk-manager-control-day"], ["deepseek-v4-flash"], trials=3)
    from app.models import TaskRun, TaskKind, TaskStatus
    tr = TaskRun(kind=TaskKind.ARENA_RUN.value, status=TaskStatus.QUEUED.value,
                 progress_current=0, progress_total=3, message="")
    db_session.add(tr); db_session.commit()

    calls = {"n": 0}
    def fake_run_match(loaded, model, *, artifact_root, run_id):
        calls["n"] += 1
        return _make_fake_scoring_transcript()  # helper: non-blank, some tool calls

    task._execute(db_session, tr.id, rid, settings=_test_settings(),
                  run_match_fn=fake_run_match, judge_fn=None)
    db_session.commit()
    run = store.get_run(db_session, rid)
    scored = [m for m in run["matches"] if m["status"] == "scored"]
    assert calls["n"] == 3
    assert len(scored) == 1
    assert scored[0]["score_breakdown"]["n_trials"] == 3
    assert len(scored[0]["score_breakdown"]["aggregate"]) == 3

def test_execute_trials_one_is_behavior_preserving(db_session):
    # trials=1 → n_trials==1 aggregate, single scored match (as today)
    ...  # mirror above with trials=1, assert n_trials==1
```

(Use the existing arena-task test's transcript-builder helper; if none, build a minimal `MatchTranscript` with one step that has `tool_calls` and non-empty `response_text` so the infra gates pass and `objective_score` returns a number.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -k folds_multiple_trials -v`
Expected: FAIL (only 1 call / no `n_trials`)

- [ ] **Step 3: Extract `_run_and_score_once`**

Move the current per-match body (infra gates → judge → objective_score → build `breakdown` incl. `card` → `_save_transcript`) into:

```python
def _run_and_score_once(session, *, run_id, loaded, model, workflow_id, model_id,
                        weights, artifact_root, cfg, run_match_fn, judge_fn, post):
    """Run ONE match; return ("scored", breakdown, transcript_path)
    or ("invalid", None, infra_error). Raises on transport/other exceptions."""
    from app.services.arena import scoring
    transcript = run_match_fn(loaded, model, artifact_root=artifact_root, run_id=run_id)
    if _is_infra_blank(transcript):
        return "invalid", None, "infra_blank"
    if _is_infra_contaminated(transcript):
        return "invalid", None, "infra_error"
    # ... existing judge + objective_score + breakdown (incl. card) construction ...
    transcript_path = _save_transcript(transcript, artifact_root, workflow_id, model_id)
    return "scored", breakdown, transcript_path
```

- [ ] **Step 4: Rewrite the pair loop in `_execute`**

`_run_and_score_once` returns `(status, breakdown, info)` where on `"scored"` the third
element `info` is the saved transcript path, and on `"invalid"` it is the infra reason string.
The loop keeps the last clean trial's path in a plain local `last_path`:

```python
    trials_n = int(run_dict.get("trials") or 1)
    total_units = len(workflow_ids) * len(model_ids) * trials_n
    completed = 0
    for workflow_id in workflow_ids:
        for model_id in model_ids:
            loaded = _get_bundle(workflow_id)
            model = get_model(model_id)
            clean: list[dict] = []
            last_infra: str | None = None
            last_path: str | None = None
            failed_exc: str | None = None
            for _ in range(trials_n):
                try:
                    status, breakdown, info = _run_and_score_once(
                        session, run_id=run_id, loaded=loaded, model=model,
                        workflow_id=workflow_id, model_id=model_id, weights=weights,
                        artifact_root=artifact_root, cfg=_cfg,
                        run_match_fn=_run_match_fn, judge_fn=judge_fn, post=post)
                    if status == "scored":
                        clean.append(breakdown)
                        last_path = info          # info is the saved transcript path
                    else:
                        last_infra = info         # info is the infra reason
                except Exception:
                    failed_exc = traceback.format_exc()
                completed += 1
                update_task_progress(session, task_id, current=completed, total=total_units)
                session.commit()
            _record_pair(session, run_id, workflow_id, model_id, weights, trials_n,
                         clean, last_path, last_infra, failed_exc)
            session.commit()
```

- [ ] **Step 5: Add `_record_pair` helper**

```python
def _record_pair(session, run_id, workflow_id, model_id, weights, trials_n,
                 clean, last_path, last_infra, failed_exc):
    """Persist exactly one match row for a (workflow, model) pair after its trials.

    ≥1 clean trial → one scored aggregate match; else a failed row if every trial
    raised, else an invalid row (all trials were infra-gated).
    """
    from app.services.arena import scoring
    cfg = {"weights": weights, "trials": trials_n}
    if clean:
        agg = scoring.fold_trial_breakdowns(clean)
        store.record_match(session, run_id=run_id, workflow_id=workflow_id,
                           model_id=model_id, objective_score=agg["objective_score"],
                           judged_score=None, total_score=agg["objective_score"],
                           judge_missing=False, config=cfg,
                           transcript_path=last_path, status="scored",
                           score_breakdown=agg)
    elif last_infra is None and failed_exc is not None:
        store.record_match(session, run_id=run_id, workflow_id=workflow_id,
                           model_id=model_id, objective_score=None, judged_score=None,
                           total_score=None, judge_missing=True, config=cfg,
                           transcript_path=None, status="failed", error=failed_exc)
    else:
        store.record_match(session, run_id=run_id, workflow_id=workflow_id,
                           model_id=model_id, objective_score=None, judged_score=None,
                           total_score=None, judge_missing=True, config=cfg,
                           transcript_path=None, status="invalid",
                           error=last_infra or "infra_blank")
```

- [ ] **Step 6: Wire `queue_arena_run(trials=)`**

```python
def queue_arena_run(session, *, workflow_ids, model_ids, weights=None, trials=1):
    ...
    run_id = store.create_run(session, workflow_ids=workflow_ids,
                              model_ids=canonical_model_ids, weights=weights, trials=trials)
    task = TaskRun(..., progress_total=len(workflow_ids) * len(canonical_model_ids) * trials, ...)
```

- [ ] **Step 7: Run tests**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -v`
Expected: PASS (folds-3, trials=1 behavior-preserving, all-infra→invalid if you added it)

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/arena/task.py tests/test_arena_api.py
git commit -m "feat(arena): multi-trial _execute — fold N trials per pair into one aggregate match"
```

---

### Task 5: Router endpoints — delete, merge, workflows, create-with-trials

**Files:**
- Modify: `backend/app/routers/arena.py` (`CreateRunRequest`; add 3 endpoints; pass `trials`)
- Test: `tests/test_arena_api.py` (or existing arena router test module)

**Interfaces:**
- Consumes: `store.delete_runs`, `store.merge_runs`, `queue_arena_run(trials=)`, `list_workflows`.
- Produces: `POST /api/arena/runs/delete`, `POST /api/arena/runs/merge`, `GET /api/arena/workflows`; `CreateRunRequest.trials`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_arena_api.py
def test_delete_runs_endpoint(client, seeded_run_id):
    r = client.post("/api/arena/runs/delete", json={"run_ids": [seeded_run_id]})
    assert r.status_code == 200
    assert r.json()["deleted_run_ids"] == [seeded_run_id]

def test_delete_runs_empty_400(client):
    assert client.post("/api/arena/runs/delete", json={"run_ids": []}).status_code == 400

def test_merge_runs_needs_two_400(client, seeded_run_id):
    assert client.post("/api/arena/runs/merge",
                       json={"source_run_ids": [seeded_run_id]}).status_code == 400

def test_workflows_endpoint_shape(client):
    body = client.get("/api/arena/workflows").json()
    assert "workflows" in body and {"id", "title", "tags", "step_count"} <= set(body["workflows"][0])

def test_create_run_trials_bounds_422(client):
    r = client.post("/api/arena/runs", json={"workflow_ids": ["risk-manager-control-day"],
                                             "model_ids": ["deepseek-v4-flash"], "trials": 99})
    assert r.status_code == 422
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -k "delete_runs or merge_runs or workflows or trials_bounds" -v`
Expected: FAIL (404 / no field)

- [ ] **Step 3: Implement**

```python
# CreateRunRequest — add:
    trials: int = Field(default=2, ge=1, le=10)

class DeleteRunsRequest(BaseModel):
    run_ids: list[int]

class MergeRunsRequest(BaseModel):
    source_run_ids: list[int]
```

Pass trials in `create_arena_run`: `... trials=payload.trials)`.

```python
    @router.post("/runs/delete")
    def delete_arena_runs(payload: DeleteRunsRequest, session=Depends(_get_db)):
        if not payload.run_ids:
            raise HTTPException(status_code=400, detail="run_ids must not be empty")
        out = store.delete_runs(session, payload.run_ids)
        files_removed = 0
        arena_root = Path(settings.artifact_dir) / "arena"
        for p in out["transcript_paths"]:
            try:
                Path(p).unlink(); files_removed += 1
            except OSError:
                pass
        import shutil
        for rid in out["deleted_run_ids"]:
            d = arena_root / str(rid)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True); files_removed += 1
        session.commit()
        return {"deleted_run_ids": out["deleted_run_ids"],
                "match_count": out["match_count"], "files_removed": files_removed}

    @router.post("/runs/merge")
    def merge_arena_runs(payload: MergeRunsRequest, session=Depends(_get_db)):
        try:
            new_id = store.merge_runs(session, payload.source_run_ids)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session.commit()
        return {"run_id": new_id}

    @router.get("/workflows")
    def list_arena_workflows():
        from app.golden_workflows.registry import list_workflows
        return {"workflows": [
            {"id": w.id, "title": w.title, "tags": w.tags, "step_count": len(w.steps)}
            for w in list_workflows()]}
```

(Ensure `Path` and `store` are imported at module top; `settings` is the router-factory arg already in scope.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/arena.py tests/test_arena_api.py
git commit -m "feat(arena): /runs/delete, /runs/merge, /workflows endpoints + trials on create"
```

---

### Task 6: Frontend API client wrappers

**Files:**
- Modify: `frontend/src/lib/arenaApi.ts`
- Test: covered via component tests in Tasks 7-8

**Interfaces:**
- Produces: `deleteArenaRuns`, `mergeArenaRuns`, `listArenaWorkflows`, `ArenaWorkflowSummary`, `trials` on `ArenaCreateRunRequest`.

- [ ] **Step 1: Add types + wrappers**

```typescript
export type ArenaWorkflowSummary = { id: string; title: string; tags: string[]; step_count: number };

export function listArenaWorkflows(): Promise<{ workflows: ArenaWorkflowSummary[] }> {
  return apiFetch('/api/arena/workflows');
}
export function deleteArenaRuns(runIds: number[]): Promise<{ deleted_run_ids: number[]; match_count: number; files_removed: number }> {
  return apiFetch('/api/arena/runs/delete', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ run_ids: runIds }),
  });
}
export function mergeArenaRuns(sourceRunIds: number[]): Promise<{ run_id: number }> {
  return apiFetch('/api/arena/runs/merge', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ source_run_ids: sourceRunIds }),
  });
}
```

Add `trials: number` to `ArenaCreateRunRequest` (and include it in `createArenaRun` body — already forwarded via `JSON.stringify(body)`).

- [ ] **Step 2: Type-check**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/arenaApi.ts
git commit -m "feat(arena-ui): api wrappers for delete/merge/workflows + trials"
```

---

### Task 7: Runs panel — selection checkboxes + action bar (delete/merge)

**Files:**
- Modify: `frontend/src/routes/Arena.live.tsx` (runs panel ~781-807; state ~577-588)
- Modify: `frontend/src/routes/Arena.css`
- Test: `frontend/src/routes/Arena.live.test.tsx`

**Interfaces:**
- Consumes: `deleteArenaRuns`, `mergeArenaRuns` (Task 6).

- [ ] **Step 1: Write the failing test**

```typescript
// Arena.live.test.tsx — mock arenaApi; render; check two run checkboxes → action bar shows "Merge (2)" and "Delete (2)"
it('shows merge/delete action bar when runs are checked', async () => {
  // ...render with two runs...
  fireEvent.click(screen.getAllByRole('checkbox')[0]);
  fireEvent.click(screen.getAllByRole('checkbox')[1]);
  expect(screen.getByText(/Merge \(2\)/)).toBeEnabled();
  expect(screen.getByText(/Delete \(2\)/)).toBeEnabled();
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- Arena.live.test`
Expected: FAIL (no checkboxes)

- [ ] **Step 3: Add state + selection + action bar**

- Add `const [selectedRunIds, setSelectedRunIds] = useState<Set<number>>(new Set());`
- In each run `<button>` row, prepend a checkbox whose `onClick`/`onChange` calls `stopPropagation()` and toggles membership; row `onClick` stays `selectRun(run.id)`.
- Above the run list, render an action bar when `selectedRunIds.size > 0`:
  - `Merge (N)` (`disabled={selectedRunIds.size < 2}`) → `mergeArenaRuns([...])` then refresh + `selectRun(new_id)` + clear.
  - `Delete (N)` → open confirm Modal; on confirm `deleteArenaRuns([...])` then refresh, clear selection, and if `selectedRunId` was deleted clear `runDetail`/`selectedMatchId`.
  - `Clear` → `setSelectedRunIds(new Set())`.
- Confirm Modal uses the `Modal` primitive listing ids + "removes matches and transcript files, cannot be undone".

- [ ] **Step 4: Token-only CSS**

Add `wl-arena__run-checkbox`, `wl-arena__run-actions` in `Arena.css` using existing tokens only.

- [ ] **Step 5: Run tests + type-check + verify both themes/compact**

Run: `cd frontend && npm test -- Arena.live.test && npx tsc --noEmit`
Expected: PASS; manually verify light/dark/compact.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Arena.live.tsx frontend/src/routes/Arena.css frontend/src/routes/Arena.live.test.tsx
git commit -m "feat(arena-ui): multi-select runs + delete/merge action bar"
```

---

### Task 8: New Run modal (workflows × models × trials) + polling

**Files:**
- Modify: `frontend/src/routes/Arena.live.tsx` (Runs panel header; add modal; add poll effect)
- Modify: `frontend/src/routes/Arena.css`
- Test: `frontend/src/routes/Arena.live.test.tsx`

**Interfaces:**
- Consumes: `listArenaWorkflows`, `listArenaModels`, `createArenaRun` (Task 6).

- [ ] **Step 1: Write the failing test**

```typescript
it('launches a run from the New Run modal with trials', async () => {
  // mock listArenaWorkflows/listArenaModels/createArenaRun
  fireEvent.click(screen.getByText('New Run'));
  fireEvent.click(await screen.findByLabelText(/risk-manager-control-day/i));
  fireEvent.click(screen.getByLabelText(/DeepSeek V4 Flash/i));
  fireEvent.change(screen.getByLabelText(/Trials/i), { target: { value: '3' } });
  fireEvent.click(screen.getByText(/^Launch$/));
  await waitFor(() => expect(createArenaRun).toHaveBeenCalledWith(
    expect.objectContaining({ workflow_ids: ['risk-manager-control-day'],
                              model_ids: ['deepseek-v4-flash'], trials: 3 })));
});
```

- [ ] **Step 2: Run to verify fail**

Run: `cd frontend && npm test -- Arena.live.test`
Expected: FAIL (no New Run button)

- [ ] **Step 3: Implement the modal**

- `New Run` `Button` in the Runs panel `wl-arena__section-head`.
- On open, fetch workflows once (state `workflows`) and reuse loaded `models`.
- Modal body: workflow checklist + model checklist (multi-select) + a `NumberInput` labelled `Trials` (default 2, min 1, max 10). `Launch` disabled unless ≥1 workflow and ≥1 model.
- `Launch` → `createArenaRun({ workflow_ids, model_ids, trials })` → close, refresh runs, `selectRun(run_id)`.

- [ ] **Step 4: Add polling effect**

```typescript
useEffect(() => {
  const anyRunning = runs.some((r) => r.status !== 'completed');
  if (!anyRunning) return;
  const t = setInterval(() => { listArenaRuns().then((resp) => setRuns(resp.runs)); }, 4000);
  return () => clearInterval(t);
}, [runs]);
```

- [ ] **Step 5: CSS (token-only) + run tests + type-check + theme/density check**

Run: `cd frontend && npm test -- Arena.live.test && npx tsc --noEmit`
Expected: PASS; verify light/dark/compact.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Arena.live.tsx frontend/src/routes/Arena.css frontend/src/routes/Arena.live.test.tsx
git commit -m "feat(arena-ui): New Run modal (workflows × models × trials) + status polling"
```

---

### Task 9: Docs — CHANGELOG + CLAUDE.md

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased] > Added`)
- Modify: `CLAUDE.md` (arena section)

- [ ] **Step 1: CHANGELOG `[Unreleased] > Added`**

```markdown
- **Arena Runs management** — the Runs panel can now launch runs (New Run: pick
  multiple workflows × models and N trials, folded into one multi-trial aggregate
  with the same CON scheme as merge), hard-delete selected runs (rows + transcript
  files), and non-destructively merge selected runs. New endpoints `POST
  /api/arena/runs/delete`, `POST /api/arena/runs/merge`, `GET /api/arena/workflows`;
  migration `0045` adds `arena_run.trials`.
```

- [ ] **Step 2: CLAUDE.md arena note**

Add a short bullet under the arena section: New Run folds N trials into the same CON aggregate as merge (shared `scoring.fold_trial_breakdowns`); delete is hard (rows via cascade + transcript files + `arena/<run_id>` dir, nulls dangling `agent_threads.arena_run_id`); merge stays non-destructive; migration `0045` adds `arena_run.trials` (default 1, back-compat).

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs: arena runs management (New Run/delete/merge, mig 0045)"
```

---

## Self-Review

**Spec coverage:** fold kernel (T1) ✓; delete store+router (T2,T5) ✓; migration+trials column (T3) ✓; multi-trial `_execute`+queue (T4) ✓; merge+workflows endpoints (T5) ✓; frontend api (T6), selection/action-bar (T7), New Run modal+polling (T8) ✓; docs (T9) ✓. Every spec section maps to a task.

**Placeholder scan:** Cleaned — Task 4's trial loop threads `last_path` as a plain local (`_run_and_score_once` returns the saved path in the `info` slot on `"scored"`, the infra reason on `"invalid"`), and `_record_pair` takes it as a parameter. Task 1's merge refactor no longer carries any illustrative no-op line. The only intentionally-open item is Task 4 Step 1's `_make_fake_scoring_transcript` helper: reuse the existing arena-task test transcript builder, or build a minimal `MatchTranscript` with one step carrying `tool_calls` + non-empty `response_text` (so the infra gates pass) — stated in-step.

**Type consistency:** `fold_trial_breakdowns(list[dict]) -> dict` (T1) consumed identically in T4/`merge_runs`. `delete_runs -> {deleted_run_ids, transcript_paths, match_count}` (T2) consumed verbatim in T5 router. `create_run(..., trials=1)` (T3) called by `queue_arena_run` (T4). Frontend `deleteArenaRuns/mergeArenaRuns/listArenaWorkflows` (T6) consumed in T7/T8. Consistent.
