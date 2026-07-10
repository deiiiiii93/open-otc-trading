# Arena Runs Management — New Run (multi-trial), Delete, Merge

**Date:** 2026-07-09
**Status:** Approved design, pre-implementation
**Scope:** One feature spanning the arena backend (store + task + router + one migration)
and the Arena frontend (runs panel). Adds three capabilities to the Runs panel:
**New Run** (launch a run over selected workflows × models, with N trials folded into a
multi-trial aggregate), **Delete** (hard delete of selected runs), and **Merge**
(non-destructive fold of selected runs — already implemented in the store, newly exposed).

## Motivation

The Arena page can view runs and their ability-card drilldowns, but every operational
action is out-of-band: runs are launched by ad-hoc scripts, deleted by hand in SQLite, and
merged only via `merge_runs_cli.py`. Multi-trial boards (the consistency/CON scheme) are
currently produced by a throwaway script that runs N matches and folds them. This feature
brings launch / delete / merge into the UI, and makes multi-trial a first-class launch
option instead of a script.

## Current state (verified)

- **Store** (`backend/app/services/arena/store.py`)
  - `merge_runs(session, source_run_ids) -> int` exists — **non-destructive**: folds each
    `(workflow_id, model_id)`'s scored matches across the sources into ONE aggregate match
    (`n_trials`), creates a NEW completed run, leaves sources untouched. The aggregate-shape
    builder is **inlined** here (store.py ~174–184).
  - No `delete_runs`. `create_run(session, workflow_ids, model_ids, weights=None)`.
  - `list_runs`, `get_run`, `leaderboard`, `_run_to_dict`, `_derive_card`/`_match_card`.
- **Task** (`backend/app/services/arena/task.py`)
  - `queue_arena_run(session, *, workflow_ids, model_ids, weights=None)` → validates, calls
    `store.create_run`, creates a `TaskRun` with `progress_total = pairs`.
  - `_execute(...)` runs **one** match per `(workflow, model)` pair, applies the infra gates
    (`_is_infra_blank` / `_is_infra_contaminated`), judges (jury off by default →
    `subjective_mode="disabled"`), scores into a single breakdown (objective + diagnosis +
    `card = scoring.ability_card(...)`), and `record_match(status="scored")` — or records
    `status="invalid"` on infra, `status="failed"` on exception.
- **Scoring** (`backend/app/services/arena/scoring.py`)
  - `aggregate_card_from_trials(trial_cards) -> dict | None` already derives the CON card
    from per-trial cards on read. No `fold_trial_breakdowns` (aggregate builder) yet.
- **Router** (`backend/app/routers/arena.py`)
  - `POST /runs` (202, `CreateRunRequest{workflow_ids, model_ids, weights}`), `GET /runs`,
    `GET /runs/{id}`, `GET /matches/{id}/transcript`, `GET /leaderboard`, `GET /models`.
  - No delete, no merge, no workflows endpoint.
- **Models** (`backend/app/models.py`)
  - `ArenaRun` — `matches` relationship has `cascade="all, delete-orphan"`, so
    `session.delete(run)` cascades to `arena_match` rows.
  - `arena_match.run_id` is a real `ForeignKey("arena_run.id")`.
  - `agent_threads.arena_run_id` (models.py:138) is a **plain nullable Integer, NOT a FK** —
    nothing blocks a run delete, but dangling references are possible.
- **Artifacts**: written under `settings.artifact_dir / "arena" / str(run_id) / workflow_id`;
  each match `transcript_path` points inside there.
- **Frontend** (`Arena.live.tsx`, `arenaApi.ts`)
  - Runs render as a **single-select** `<button>` list keyed on `selectedRunId` (opens
    detail). No multi-select, no launch/delete/merge UI. `createArenaRun` wrapper exists but
    is unused by any component. No polling.

## Decisions (locked)

1. **Delete = hard**: remove `arena_run` + `arena_match` rows (cascade) **and** the on-disk
   transcript files + the run's `arena/<run_id>` artifact directory. Not recoverable.
2. **Merge = non-destructive**: reuse `store.merge_runs` unchanged; sources remain in the list.
3. **Multi-select UX = checkbox per row + action bar**; row-body click still opens detail.
4. **New Run = multi-trial + multi-model**: pick multiple workflows and models; each
   `(workflow × model)` pair runs **N trials** folded into a multi-trial aggregate (same shape
   and CON derivation as merged runs). **Trials default = 2**, range `1 ≤ trials ≤ 10`.
5. **Infra trials are skipped, not retried** inside a run: an infra-blank/contaminated trial
   does not count; the aggregate covers however many clean trials landed; **0 clean → invalid**.
   (The async task layer already reruns whole failed runs; no nested retry loop.)

## Architecture

Three well-bounded slices; each unit keeps one purpose and a clear interface.

### 1. Multi-trial aggregate builder (shared kernel)

**New** `scoring.fold_trial_breakdowns(trials: list[dict]) -> dict`. Pure function over a list
of single-match breakdowns (each carrying `objective`, `objective_score`, `subjective_mode`,
`card`, …). Returns the canonical aggregate:

```python
{
  "n_trials": len(trials),
  "aggregate": trials,                       # the per-trial breakdowns, in order
  "objective": trials[0].get("objective"),   # representative axes for the objective tie-break
  "objective_score": mean(objs),             # rounded 1dp; None if no scored objs
  "objective_stdev": pstdev(objs) if >1 else 0.0,
  "total_score": mean(objs),
  "subjective_mode": trials[0].get("subjective_mode", "disabled"),
}
```

- **Precondition**: `trials` non-empty (caller guarantees ≥1 clean trial).
- `store.merge_runs` is refactored to call this (deletes its inline duplication). The CON card
  continues to derive on read via `aggregate_card_from_trials` — **unchanged**.
- Placing it in `scoring.py` (not `store.py`) keeps it DB-free and unit-testable, and it sits
  beside `aggregate_card_from_trials`, its read-time counterpart.

### 2. Delete (store DB-pure + router filesystem)

**New** `store.delete_runs(session, run_ids: list[int]) -> dict`:

```python
{
  "deleted_run_ids": [int, ...],   # only runs that actually existed and were deleted
  "transcript_paths": [str, ...],  # collected from matches BEFORE deletion
  "match_count": int,              # matches removed across all deleted runs
}
```

- Loads each `ArenaRun` by id; **skips ids that don't exist** (a stale selection must not fail
  the whole batch). Collects match `transcript_path`s first, then `session.delete(run)`
  (cascade removes `arena_match`).
- Bulk-nulls `agent_threads.arena_run_id` where it references any deleted run id (raw UPDATE),
  so no thread dangles at a missing run.
- **No filesystem access** — returns paths for the router to unlink.

**New** `POST /api/arena/runs/delete` (`{run_ids: list[int]}`):
- 400 if `run_ids` empty.
- Calls `store.delete_runs`; then best-effort removes each `transcript_path` **and** each
  `Path(settings.artifact_dir)/"arena"/str(run_id)` directory (`shutil.rmtree(..., ignore_errors=True)`;
  missing files ignored). Counts files/dirs removed.
- `session.commit()`. Returns `{deleted_run_ids, match_count, files_removed}`.

### 3. Merge (router → existing store)

**New** `POST /api/arena/runs/merge` (`{source_run_ids: list[int]}`):
- Calls `store.merge_runs`; commit; return `{run_id}` (the new aggregate).
- `ValueError` (fewer than two distinct runs / no scored matches) → **400**.

### 4. New Run — multi-trial (data + queue + execute)

**Migration 0044** — `arena_run.trials INTEGER NOT NULL DEFAULT 1`
(migration-local Core table per repo convention — see `migrations_no_live_orm_services`).
- `ArenaRun.trials: Mapped[int]` (default 1). `store.create_run(..., trials: int = 1)`
  persists it. `_run_to_dict` surfaces `trials`.
- Back-compat: pre-existing runs read `trials = 1`.

**Queue** — `CreateRunRequest.trials: int = 2` (Pydantic `ge=1, le=10`);
`queue_arena_run(session, *, workflow_ids, model_ids, weights=None, trials=1)` passes `trials`
to `create_run`; `TaskRun.progress_total = pairs × trials`.

**Execute** — refactor `_execute`:
- Extract the current per-match body into `_run_and_score_once(session, run_id, loaded, model,
  workflow_id, model_id, weights, artifact_root, cfg, judge_fn, post) -> tuple[str, dict|None, str|None]`
  returning `("scored", breakdown, transcript_path)` or `("invalid", None, infra_error)`.
  This holds today's exact logic (infra gates, judge, objective score, breakdown incl. `card`,
  transcript save) — behavior-preserving.
- Trial loop per pair:
  ```
  clean = []
  last_infra = None
  for trial_no in range(trials):
      status, breakdown, info = _run_and_score_once(...)
      progress += 1; update_task_progress(current=progress, total=pairs*trials); commit
      if status == "scored": clean.append(breakdown)
      else: last_infra = info   # infra reason
  if clean:
      aggregate = scoring.fold_trial_breakdowns(clean)
      record_match(status="scored", objective_score=aggregate["objective_score"],
                   total_score=aggregate["objective_score"], score_breakdown=aggregate,
                   transcript_path=last_clean_transcript_path,   # last clean trial's saved path
                   config={"weights": weights, "trials": trials})
  else:
      record_match(status="invalid", error=last_infra or "infra_blank", ...)
  ```
- **`trials == 1` is behavior-preserving**: `fold_trial_breakdowns([bd])` yields an
  `n_trials=1` aggregate; `_derive_card`/`aggregate_card_from_trials` already render a
  single-trial card (CON greys) exactly as a lone match does today. Match-count and
  leaderboard semantics are unchanged.
- **Exceptions** inside a trial are caught per-trial (as today) → that trial is a failure;
  if all trials throw, record `status="failed"` with the traceback.
- **Transcript path for the aggregate**: keep the last clean trial's saved transcript path on
  the match row (each trial still saves its own transcript to disk under the run dir). The
  per-trial transcripts also live inside `aggregate["aggregate"][i]` breakdowns' own data.

**Workflows endpoint** — **New** `GET /api/arena/workflows` →
`{workflows: [{id, title, tags, step_count}]}` from `list_workflows()` (for the launch form).

### 5. Frontend — Runs panel

**`arenaApi.ts`**
- `deleteArenaRuns(runIds): Promise<{deleted_run_ids; match_count; files_removed}>`
- `mergeArenaRuns(sourceRunIds): Promise<{run_id}>`
- `listArenaWorkflows(): Promise<{workflows: {id; title; tags; step_count}[]}>`
- `ArenaCreateRunRequest` gains `trials: number`.

**`Arena.live.tsx`**
- New state `selectedRunIds: Set<number>` (multi-select) alongside `selectedRunId` (detail).
- Each run row: leading **checkbox** toggling membership; `onClick` `stopPropagation` so it does
  not open detail. Row body click → `selectRun` (unchanged).
- **Action bar** shown when `selectedRunIds.size > 0`: `Merge (N)` (enabled N≥2), `Delete (N)`
  (enabled N≥1), `Clear`.
  - Delete → **confirm Modal** listing the run ids and "removes matches and transcript files,
    cannot be undone" → `deleteArenaRuns` → refresh list, clear selection, and clear
    `selectedRunId`/`runDetail`/`selectedMatchId` if the open run was deleted.
  - Merge → `mergeArenaRuns` → refresh list, select the new aggregate run, clear selection.
- **New Run** button in the Runs panel header → **Modal form**: multi-select **workflow**
  checklist (`listArenaWorkflows`), multi-select **model** checklist (`listArenaModels`), and a
  **Trials** number input (default 2, min 1, max 10). Launch → `createArenaRun({workflow_ids,
  model_ids, trials})` → on 202 close, refresh runs, select the new run. Disabled unless ≥1
  workflow and ≥1 model.
- **Polling**: run status advances `queued → running → completed` (a run always finishes
  `completed`; per-pair failures live at the match level). While any listed run is non-terminal
  (status `!= "completed"`), refresh the runs list on an interval (e.g. 4s); stop when all are
  `completed`. Clear the interval on unmount.
- Reuse `Modal`, `Button`, and the `wl-field`/`wl-input` primitives (or theme raw controls).
  **Token-only CSS** in `Arena.css`; verify light + dark + compact density.

## Error handling

- Empty `run_ids` (delete) / fewer than two runs (merge) → 400 (server) and disabled buttons
  (client).
- `trials` out of `[1,10]` → 422 (Pydantic).
- Delete of a currently-`running` run is allowed (user's call); no special guard.
- Missing transcript files / artifact dirs on delete → ignored (best-effort).
- Non-existent run ids in a delete batch → silently skipped; response reports only actually
  deleted ids so the client reconciles its selection.
- Per-trial infra/exception isolation as described; a run always finishes `completed` even if
  some pairs end `invalid`/`failed`.

## Testing

- **Store/scoring**: `fold_trial_breakdowns` (mean/stdev/n_trials/subjective_mode; single-trial
  case); `merge_runs` regression still green via the shared helper; `delete_runs` (removes
  run+matches, returns transcript paths + match_count, nulls `agent_threads.arena_run_id`,
  skips missing ids).
- **Task**: `_execute` with injected `run_match_fn` — `trials=3` → one `scored` match with
  `n_trials=3`, mean objective, `len(aggregate)==3`; `trials=1` → unchanged single-trial
  breakdown (guards back-compat); all-infra trials → `invalid`; 1-of-3 infra → `n_trials=2`;
  `progress_total == pairs × trials`.
- **Migration**: 0044 applies on a clean DB; `trials` defaults to 1 on existing rows.
- **Router**: `POST /runs` with `trials` reaches `queue_arena_run`; `trials` bounds 422;
  `POST /runs/delete` (happy + empty-list 400 + files removed); `POST /runs/merge` (happy +
  `<2` 400); `GET /workflows` shape.
- **Frontend**: New Run modal — select workflow+model+trials → `createArenaRun` called with the
  trials value, modal closes, list refreshes; checkbox selection enables the action bar with the
  right counts; delete confirm calls `deleteArenaRuns` and clears open detail on match.

## Docs

- `CHANGELOG.md` `[Unreleased] > Added`: Arena Runs management (New Run with N-trial aggregates,
  hard delete, non-destructive merge) + migration 0044.
- `CLAUDE.md` arena section: New Run folds N trials into the same CON aggregate as merge; delete
  is hard (rows + transcript files); merge is non-destructive; migration 0044.
- Both `config/agent_channels.*` — **N/A** (no channel changes).

## Out of scope (YAGNI)

- Per-trial retry inside a run (async layer reruns whole runs).
- Soft-delete / archive / undo.
- Editing an existing run's workflows/models/trials.
- Weights UI (the create path still accepts `weights`, but no picker is added).
