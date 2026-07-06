# Arena Jury Opt-In — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the deterministic objective score the sole default scoring/ranking axis; the LLM jury becomes opt-in (default off) with explicit provenance so a failed opt-in jury never looks like a deliberate opt-out.

**Architecture:** A single config flag `arena_jury_enabled` (default `False`) gates the default jury call in `task._execute`. When off, matches score objective-only and stamp `subjective_mode="disabled"`; when on, today's contestant-excluded jury runs unchanged. `leaderboard` gains a four-value mode precedence plus a legacy-inference rule so old jury rows and new objective-only rows are both interpreted correctly on read (no migration). The frontend renders objective drilldown detail regardless of jury presence, and shows the Subjective column only for boards where the jury was intended.

**Tech Stack:** FastAPI + SQLAlchemy (backend), pytest, React 19 + TypeScript + Vitest (frontend). Spec: `docs/superpowers/specs/2026-07-06-arena-jury-opt-in-design.md`.

## Global Constraints

- Do NOT touch the `goal_*` RubricMiddleware subsystem (an unrelated "rubric").
- No DB migration or backfill; historical subjective data is read, not rewritten.
- Objective scoring, axes, the tiebreak, and infra-contamination gating are unchanged.
- The 2-point judge rubric in the `risk-manager-control-day` manifest STAYS (opt-in jury consumes it); `test_flagship_loads` must keep asserting 2 rubric points.
- Frontend: token-only styling, no new colors, no hardcoded hex.
- `subjective_mode` domain: `"disabled" | "missing" | "self_consistency" | "panel"`; aggregation precedence `missing > self_consistency > panel > disabled`.
- Config tests that assert defaults must run in a no-`.env` environment (repo caveat).

---

### Task 1: Config flag `arena_jury_enabled`

**Files:**
- Modify: `backend/app/config.py` — the env-backed pydantic `_EnvironmentSettings` (~line 83, next to `agent_code_interpreter_enabled`) AND the frozen `Settings` dataclass that `get_settings()` returns (~line 232, next to `agent_code_interpreter_enabled`'s `_env_value` field)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Settings.arena_jury_enabled: bool` (default `False`), env var `OPEN_OTC_ARENA_JURY`. Consumed by Task 2 via `get_settings()`.

**Critical wiring note:** `get_settings()` returns the frozen `Settings` **dataclass**, whose fields read env values through `_env_value(name)` → `_EnvironmentSettings`. The sibling arena knobs (`arena_judge_models`, `arena_min_judges`) are hardcoded dataclass defaults and are NOT env-toggleable — do **not** copy that pattern. To make `OPEN_OTC_ARENA_JURY` actually honored in the production `_execute` path, the flag must be added to BOTH: the pydantic `_EnvironmentSettings` (which does the env read + bool coercion) and the `Settings` dataclass (via `_env_value`). A plain `= False` on the dataclass would leave the env override dead.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:
First add `"OPEN_OTC_ARENA_JURY"` to the module-level `ENV_KEYS` list (so
`_clear_config_env` isolates it and adjacent config tests don't leak it). Then use the
existing isolation helpers (`_clear_config_env`, `_config_with_env_file`) exactly as the
other `test_config.py` cases do — do NOT read the developer's real `.env`:
```python
def test_arena_jury_disabled_by_default(monkeypatch, tmp_path):
    # Hermetic: cleared env + empty env file → proves the CODE default is False.
    _clear_config_env(monkeypatch)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    config_module = _config_with_env_file(monkeypatch, env_file)
    assert config_module.Settings().arena_jury_enabled is False

def test_arena_jury_env_override(monkeypatch, tmp_path):
    _clear_config_env(monkeypatch)
    env_file = tmp_path / "empty.env"
    env_file.write_text("")
    monkeypatch.setenv("OPEN_OTC_ARENA_JURY", "1")
    config_module = _config_with_env_file(monkeypatch, env_file)
    assert config_module.Settings().arena_jury_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_arena_jury_disabled_by_default -v`
Expected: FAIL (`AttributeError: 'Settings' object has no attribute 'arena_jury_enabled'`)

- [ ] **Step 3: Add the flag to BOTH the env-backed pydantic model and the dataclass**

In `_EnvironmentSettings(BaseSettings)` (next to `agent_code_interpreter_enabled`, ~line 83) — this does the env read + bool coercion:
```python
    arena_jury_enabled: bool = Field(
        False,
        validation_alias="OPEN_OTC_ARENA_JURY",
    )
```
In the frozen `Settings` dataclass (next to `agent_code_interpreter_enabled`'s field, ~line 232) — this is what `get_settings()` returns and `_execute` reads:
```python
    arena_jury_enabled: bool = field(
        default_factory=lambda: _env_value("arena_jury_enabled")
    )
```
Do **not** add a plain `= False`. No `__post_init__` change is needed — the pydantic model already coerces `"1"`/`"true"` → `bool` before `_env_value` returns it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_config.py -v`
Expected: PASS (both new tests + existing config tests)

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py tests/test_config.py
git commit -m "feat(arena): add arena_jury_enabled flag (default off)"
```

---

### Task 2: Gate the default jury in `task._execute`; stamp `disabled`

**Files:**
- Modify: `backend/app/services/arena/task.py` (`_execute`, ~line 328-365)
- Test: `tests/test_arena_scoring.py` (or `tests/test_arena_api.py` — whichever drives `_execute` end-to-end today)

**Interfaces:**
- Consumes: `Settings.arena_jury_enabled` (Task 1).
- Produces: a scored match whose `score_breakdown` has **no `judge` block** and `subjective_mode="disabled"` when jury off + no `judge_fn`; `judged_score=None`, `judge_missing=True`, `total_score == objective_score`.

- [ ] **Step 1: Write the failing test**

Find the existing helper that runs `_execute` with a fake `run_match_fn` (grep `run_match_fn` in `tests/test_arena_scoring.py`/`test_arena_api.py`). **Guard against real LLM calls:** with no `judge_fn`, the pre-fix `else` branch calls the real `_default_judge`/`judge_panel` (quota-burning, flaky, tests reachability not behavior). Monkeypatch `judge_panel` to raise a sentinel so the pre-fix run fails *fast and locally*, and the post-fix run proves no judge is invoked at all:
```python
def test_execute_jury_off_scores_objective_only(session, monkeypatch, ...):
    # Any attempt to run the default jury is a bug when the flag is off.
    def _boom(*a, **k):
        raise AssertionError("jury must not run when arena_jury_enabled is False")
    monkeypatch.setattr("app.services.arena.judge.judge_panel", _boom)
    # arrange a run with one model + fake run_match_fn returning a good transcript;
    # settings with arena_jury_enabled=False, no judge_fn.
    _execute(session, task_id, run_id, settings=settings)  # no judge_fn
    m = session.query(ArenaMatch).filter_by(run_id=run_id).one()
    assert m.status == "scored"
    assert m.judged_score is None
    assert m.judge_missing is True
    assert m.total_score == m.objective_score
    assert "judge" not in (m.score_breakdown or {})
    assert m.score_breakdown["subjective_mode"] == "disabled"
```
Pre-fix expectation: FAILS via the `_boom` sentinel (proves the gate is missing) — no
network call. Post-fix: PASSES with no jury invocation.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py::test_execute_jury_off_scores_objective_only -v`
Expected: FAIL via the `_boom` `AssertionError` (the default jury still runs when the flag is off) — locally, no network call.

- [ ] **Step 3: Implement the gate + disabled breakdown**

Replace the judge-selection branch (currently `if judge_fn is not None: … else: _default_judge(…)`):
```python
                if judge_fn is not None:
                    judge_result = judge_fn(transcript, loaded, post=post)
                elif _cfg.arena_jury_enabled:
                    judge_result = _default_judge(
                        transcript, loaded, exclude_model=model_id)
                else:
                    judge_result = None
```
Then build the breakdown conditionally — objective/diagnosis/scores always, judge block only when a judge ran:
```python
                obj_score, _passed, _total = scoring.objective_score(transcript, loaded)
                t_score = round(obj_score, 1)
                heuristic = scoring.diagnose_heuristic(transcript, loaded)
                breakdown = {
                    "objective": scoring.objective_breakdown(transcript, loaded),
                    "diagnosis": {
                        "counts": heuristic["summary"],
                        "counts_detail": heuristic,
                        "analysis": (judge_result.diagnosis if judge_result else None),
                    },
                    "objective_score": round(obj_score, 1),
                    "total_score": t_score,
                }
                if judge_result is not None:
                    breakdown["judge"] = {
                        "rubric_scores": judge_result.rubric_scores,
                        "judged_score": judge_result.judged_score,
                        "judge_missing": judge_result.judge_missing,
                        "per_judge": judge_result.per_judge,
                        "judged_stdev": judge_result.judged_stdev,
                    }
                    breakdown["subjective_mode"] = judge_result.subjective_mode
                else:
                    breakdown["subjective_mode"] = "disabled"

                judged_score = judge_result.judged_score if judge_result else None
                judge_missing = judge_result.judge_missing if judge_result else True
```
Update the `store.record_match(...)` call to pass `judged_score=judged_score`, `judge_missing=judge_missing` (the locals above) instead of `judge_result.judged_score` / `judge_result.judge_missing`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py tests/test_arena_api.py -v`
Expected: PASS — the new jury-off test passes; existing tests that inject a `judge_fn` still exercise the judge path (D4) and pass unchanged.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/task.py tests/test_arena_scoring.py
git commit -m "feat(arena): gate default jury behind flag; stamp subjective_mode=disabled when off"
```

---

### Task 3: Leaderboard mode precedence + legacy inference

**Files:**
- Modify: `backend/app/services/arena/store.py` (`leaderboard`, `_agg_mode`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Consumes: match rows with `score_breakdown.subjective_mode` (new), legacy rows with `judge.judged_score` but no mode.
- Produces: leaderboard rows where `subjective_mode` reflects D8 precedence + D9 legacy inference; ranking by `mean_objective` unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_arena_store.py` (reuse the run/match seeding helpers already there):
```python
def test_leaderboard_objective_only_rows_report_disabled(session):
    # seed a run with 2 scored objective-only rows (score_breakdown has
    # subjective_mode="disabled", no judge block, judged_score None)
    ...
    rows = leaderboard(session, run_id=run_id)
    assert [r["model_id"] for r in rows] == [...]  # ranked by objective
    assert all(r["subjective_mean"] is None for r in rows)
    assert all(r["subjective_mode"] == "disabled" for r in rows)

def test_leaderboard_jury_all_failed_reports_missing_not_disabled(session):
    # seed a scored row with a judge block: judged_score None, judge_missing True,
    # subjective_mode="missing"
    ...
    rows = leaderboard(session, run_id=run_id)
    assert rows[0]["subjective_mode"] == "missing"

def test_leaderboard_legacy_row_infers_panel(session):
    # seed a legacy scored row: judge block with judged_score=72.0, NO subjective_mode
    ...
    rows = leaderboard(session, run_id=run_id)
    assert rows[0]["subjective_mean"] == 72.0
    assert rows[0]["subjective_mode"] == "panel"  # inferred, NOT "missing"

def test_leaderboard_legacy_toplevel_judged_score_no_breakdown(session):
    # oldest shape: top-level ArenaMatch.judged_score set, score_breakdown None.
    # Must still surface the mean and infer "panel" — NOT vanish as "disabled".
    _seed_match(session, run_id, "m1", objective_score=80.0,
                judged_score=72.0, judge_missing=False, score_breakdown=None)
    rows = leaderboard(session, run_id=run_id)
    assert rows[0]["subjective_mean"] == 72.0
    assert rows[0]["subjective_mode"] == "panel"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k "disabled or missing or legacy" -v`
Expected: FAIL (`disabled` never produced; legacy row yields `"missing"`).

- [ ] **Step 3: Implement per-row inference + precedence**

Two edits. First, make the subjective-**mean** collection fall back to the top-level
`ArenaMatch.judged_score` when the breakdown has no `judge` block (oldest rows persist
the score only on the column). Where it currently does
`if judge.get("judged_score") is not None: model_subjectives[...].append(judge["judged_score"])`,
use an effective score:
```python
        judge = bd.get("judge") or {}
        eff_judged = judge.get("judged_score")
        if eff_judged is None:
            eff_judged = m.judged_score          # legacy: score on the column only
        if eff_judged is not None:
            model_subjectives[m.model_id].append(eff_judged)
```
(Keep the existing `judged_stdev` collection guarded on the breakdown as-is.)

Second, replace the per-row mode collection
(`mode = bd.get("subjective_mode") or judge.get("subjective_mode")`) with a per-row
inference (D9) that uses the SAME `eff_judged` fallback so a top-level-only legacy row
infers `"panel"`, not `"disabled"`:
```python
        explicit_mode = bd.get("subjective_mode") or judge.get("subjective_mode")
        if explicit_mode:
            row_mode = explicit_mode
        elif eff_judged is not None:
            row_mode = "panel"          # legacy successful jury (breakdown or column)
        elif m.judge_missing:
            row_mode = "missing"        # legacy failed jury
        else:
            row_mode = "disabled"
        model_sub_modes[m.model_id].append(row_mode)
```
Extend `_agg_mode` to the four-value precedence:
```python
    def _agg_mode(modes: list[str]) -> str:
        if not modes:
            return "disabled"
        for level in ("missing", "self_consistency", "panel", "disabled"):
            if level in modes:
                return level
        return modes[0]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -v`
Expected: PASS (new cases + existing leaderboard tests, including run #11-shaped panel rows).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): leaderboard mode precedence + legacy panel inference (disabled vs missing)"
```

---

### Task 4: Frontend — objective drilldown always, Subjective column by intent

**Files:**
- Modify: `frontend/src/routes/Arena.live.tsx` (`ScoreBreakdownView` ~line 44; `leaderboardColumns` ~line 292-330; table render ~line 375)
- Modify: `frontend/src/lib/arenaApi.ts` (doc the `"disabled"` mode value in the `subjective_mode` comment)
- Modify: `frontend/src/routes/Arena.css` (degraded-marker class if a new one is needed; reuse existing tokens)
- Test: `frontend/src/routes/Arena.live.test.tsx`

**Interfaces:**
- Consumes: `ArenaScoreBreakdown` (may lack `judge`, may have `subjective_mode="disabled"`); `ArenaLeaderboardRow.subjective_mode`.

- [ ] **Step 1: Write the failing tests**

Add to `Arena.live.test.tsx`:
```typescript
it('renders objective drilldown for an objective-only match (no judge block)', () => {
  // breakdown with objective (axes+steps) present, judge undefined, subjective_mode "disabled"
  render(<ScoreBreakdownView breakdown={objectiveOnlyBreakdown} />)
  expect(screen.getByText(/Objective/)).toBeInTheDocument()
  // an axis/step label from the fixture is visible (NOT the compact fallback):
  expect(screen.getByText(/grounding|procedural/i)).toBeInTheDocument()
  expect(screen.queryByText(/Per-judge/i)).not.toBeInTheDocument()
})

it('hides the Subjective column when every row is disabled', () => {
  renderLeaderboard([{ ...row, subjective_mean: null, subjective_mode: 'disabled' }])
  expect(screen.queryByText(/Subjective/)).not.toBeInTheDocument()
})

it('shows the Subjective column when any row had the jury intended', () => {
  renderLeaderboard([
    { ...rowA, subjective_mean: 55, subjective_mode: 'panel' },
    { ...rowB, subjective_mean: null, subjective_mode: 'disabled' },
  ])
  expect(screen.getByText(/Subjective/)).toBeInTheDocument()  // mixed board
})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npm test -- Arena.live`
Expected: FAIL (compact fallback hides objective detail; Subjective column is static/always-present).

- [ ] **Step 3: Decouple the drilldown**

In `ScoreBreakdownView`, change the early guard from `if (!obj || !judge)` to `if (!obj)`. Wrap every subjective/jury section in a `judge` guard: the `· Subjective …` summary suffix, the `subjective_mode === 'self_consistency'` badge, and the `judge.per_judge` "Per-judge" block must each render only when `judge != null`. Objective axes/steps/success/diagnosis render whenever `obj` exists.

- [ ] **Step 4: Make the Subjective column board-conditional**

Convert `leaderboardColumns` from a module-level constant into a value computed from the rows (e.g. a `useMemo` or a `buildColumns(rows)` helper called at render, line ~375). Compute:
```typescript
const juryIntended = leaderboard.some(
  (r) => r.subjective_mean != null || r.subjective_mode !== 'disabled',
);
```
Include the `subjective` column only when `juryIntended`. In the subjective cell renderer, when `row.subjective_mode === 'missing'` render a degraded marker (`—` with a `title="jury failed"`); when `'disabled'` render blank; otherwise the mean (± stdev) as today.

- [ ] **Step 5: Run tests + type-check**

Run: `cd frontend && npm test -- Arena.live && npx tsc --noEmit`
Expected: PASS + no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Arena.live.tsx frontend/src/routes/Arena.live.test.tsx frontend/src/lib/arenaApi.ts frontend/src/routes/Arena.css
git commit -m "feat(arena-ui): objective drilldown independent of jury; Subjective column by jury intent"
```

---

### Task 5: Docs + full-suite verification

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`)
- Modify: `CLAUDE.md` (golden-workflows/arena section — note jury is now opt-in, default off, `OPEN_OTC_ARENA_JURY`, `subjective_mode` provenance incl. `disabled`)

- [ ] **Step 1: Update CHANGELOG.md** under `[Unreleased]`:
```markdown
### Changed
- Arena scoring is objective-only by default; the LLM jury is now opt-in via
  `OPEN_OTC_ARENA_JURY` (default off). Objective-only matches stamp
  `subjective_mode="disabled"`, distinct from a failed opt-in jury (`"missing"`),
  so quota/dependency outages stay visible. Leaderboard infers `"panel"` for legacy
  pre-mode jury rows. No migration; historical subjective data is read as-is.
```

- [ ] **Step 2: Update CLAUDE.md** — in the arena/judge-fairness subsection, add that the jury is opt-in (default off), the env flag, and the `disabled | missing | self_consistency | panel` provenance + legacy inference. Keep it to the existing terse style.

- [ ] **Step 3: Run the full affected suite**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py tests/test_arena_store.py tests/test_arena_api.py tests/test_arena_judge.py tests/test_config.py tests/test_flagship_loads.py tests/test_golden_workflow_assertions.py -v`
Expected: PASS (all). Confirm `test_flagship_loads` still asserts 2 rubric points (D6 — rubric unchanged).

- [ ] **Step 4: Frontend suite + type-check**

Run: `cd frontend && npm test && npx tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(arena): document opt-in jury (default off) + subjective_mode provenance"
```

---

## Self-Review

- **Spec coverage:** D1 (keep jury) → Tasks 2/3 leave `judge.py` untouched. D2 (flag) → Task 1. D3 (default no subjective + `disabled`) → Task 2. D4 (`judge_fn` seam) → Task 2 Step 4. D5/D8 (precedence) → Task 3. D6 (rubric stays) → Task 5 Step 3. D7 (frontend) → Task 4. D9 (legacy inference) → Task 3. Observability (disabled vs missing) → Tasks 2+3+4.
- **No placeholders:** every code step shows the actual edit. The two `...` markers in test bodies are seeding scaffolds that reuse existing per-file helpers (grep-located in Step 1), not logic gaps.
- **Type consistency:** `subjective_mode` string domain and `missing > self_consistency > panel > disabled` precedence are identical across Tasks 2/3/4 and the spec.
