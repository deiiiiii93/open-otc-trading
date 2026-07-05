# Arena Judge Fairness & Scoring-Methodology Reform — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Confine the LLM judge to genuinely subjective quality (2 rubric points), score it with a de-biased contestant-excluded jury reported on its own axis, and rank the leaderboard purely by the deterministic objective score — after repairing the broken benchmark checks the objective axis depends on.

**Architecture:** Four phases. **P0 (benchmark correctness)** repairs the contamination predicate, the dead trap check, and the invented grounding fixtures — it is a HARD prerequisite for P3. **P1** deletes the 5 redundant judge rubric points (their signal already lives in objective checks), leaving 2 subjective points. **P2** replaces the single GPT-5.5 judge with a `judge_panel` (3 diverse models, contestant-excluded, per-judge + stdev, self-consistency fallback). **P3** reports objective and subjective as separate axes, ranks by objective, and drops the 50/50 blended total (backend → API → frontend).

**Tech Stack:** Python (FastAPI, SQLAlchemy), pytest; React 19 / TypeScript / Vitest; golden-workflow markdown manifests + JSON replay fixtures; QuantArk scenario catalog (filesystem `data/scenario_sets/`).

## Global Constraints

- **P0 → (P1, P2 any order) → P3.** P3 (rank-by-objective, drop blend) MUST NOT be committed until P0 is complete and regression-tested. (spec §5, Phase 3 gate)
- **No blended total.** Rank by objective; subjective is advisory `mean ± stdev`; any one-number UI value is a labelled "advisory composite", never the sort key. (spec D5)
- **Objective ties resolve deterministically** by sub-axis priority `grounding → adherence → synthesis → procedural`, else shared rank; subjective never a silent tie-breaker. (spec D5a)
- **Default judge panel:** `deepseek-v4-pro` (direct channel) + `anthropic/claude-opus-4.8` + `qwen/qwen3.7-max`; contestant-excluded; substitution order on overlap `gemini-3.1-pro-preview → glm-5.2 → kimi-k2.7-code`; `min_judges=2`, `self_consistency_k=3`. (spec D2/D3)
- **2 subjective rubric points only:** synthesis coherence + analytical correctness; "reasoning depth" is forbidden (rewards verbosity). (spec D1/D3)
- **Self-consistency is a DEGRADED mode** (`subjective_mode="self_consistency"`), visibly distinct from a true panel; triggered when the initial pool `< min_judges` OR post-failure survivors `< min_judges`. (spec D4)
- **Judge stays network-isolated in tests** via injected `post`/panel-poster callables (existing `post=` seam). (spec D8)
- **Recovery signal = completed assistant response** (`response_text.strip()`), never a mere issued tool call. (spec §4.4)
- Backend tests: `.venv/bin/python -m pytest`. Frontend: `cd frontend && npm test`, `npx tsc --noEmit`. Token-only styling per `frontend/CLAUDE.md`.
- Commit after each task. Update `CHANGELOG.md` (`[Unreleased]`) before finishing (pre-push hook enforces).

---

## File Structure

- `backend/app/services/arena/task.py` — contamination predicate (P0.1), trap-absence setup hook (P0.2), judge_panel wiring + judge-missing handling (P3.2).
- `backend/app/services/arena/runner.py` — trap-set-absence enforcement at match setup (P0.2).
- `backend/app/golden_workflows/definitions/risk-manager-control-day.md` — grounding paths (P0.3), trap step name (P0.2), rubric 6→2 (P1).
- `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json` — re-harvested grounding + trap fixtures (P0.2/P0.3).
- `backend/app/services/arena/judge.py` — `JudgeResult` fields + `judge_panel` (P2).
- `backend/app/config.py` — arena judge settings (P2.3).
- `backend/app/services/arena/scoring.py` — drop blend, tie helper (P3.1).
- `backend/app/services/arena/store.py` — leaderboard subjective columns + objective sort + tie policy (P3.1).
- `backend/app/routers/arena.py` — expose subjective mean/stdev, per-judge, subjective_mode (P3.3).
- `frontend/src/lib/arenaApi.ts`, `frontend/src/routes/Arena.live.tsx`, `Arena.css`, `Arena.live.test.tsx` — separate-axes UI (P3.4).
- Tests throughout: `tests/test_arena_api.py`, `tests/test_arena_scoring.py`, `tests/test_arena_store.py`, `tests/test_arena_judge.py`, `tests/test_flagship_loads.py`, `tests/test_golden_workflow_regression.py`, `tests/test_golden_workflow_assertions.py`.

---

## Phase 0 — Benchmark correctness (HARD prerequisite for P3)

### Task 0.1: Harden the infra-contamination predicate

**Files:**
- Modify: `backend/app/services/arena/task.py` (`_is_infra_contaminated`, ~line 138–158)
- Test: `tests/test_arena_api.py`

**Interfaces:**
- Produces: `_is_infra_contaminated(transcript) -> bool` (signature unchanged; semantics: a step "recovers" only if `response_text.strip()` is non-empty — NOT if it merely has `tool_calls`).

- [ ] **Step 1: Write the failing test** in `tests/test_arena_api.py` (near `test_partial_provider_error_marks_invalid_and_skips_judge`):

```python
def test_toolcall_then_provider_death_marks_invalid(session, settings):
    """A step that issues a tool call but whose FINAL response dies on a 402
    (empty response_text + provider error) is a partial death, not recovery."""
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
                      response_text="",  # final response died
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
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py::test_toolcall_then_provider_death_marks_invalid -v`
Expected: FAIL — currently `status == "scored"` because the step has `tool_calls` and is treated as recovered.

- [ ] **Step 3: Implement the fix** in `task.py` `_is_infra_contaminated` — change the recovery guard:

```python
    for s in transcript.steps:
        if s.response_text.strip():
            continue  # recovered — a completed assistant response, not a mere issued tool call
        for entry in (s.errors or []):
            if _PROVIDER_ERROR_RE.search(_error_text(entry)):
                return True
    return False
```

(Remove the `or s.tool_calls` from the guard. Update the docstring line about recovery to say "completed assistant response".)

- [ ] **Step 4: Run the new test + the mimo-exoneration regression**

Run: `.venv/bin/python -m pytest tests/test_arena_api.py -k "contaminat or provider or infra or domain_tool" -v`
Expected: PASS (all), including `test_domain_tool_error_stays_scored` (domain errors still not flagged) and `test_partial_provider_error_marks_invalid_and_skips_judge`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/task.py tests/test_arena_api.py
git commit -m "fix(arena): infra-contamination recovery requires completed response, not a tool call"
```

### Task 0.2: Repair the dead trap check (runner-enforced absence)

**Context:** the manifest step-8 trap assumes the `liquidity-crunch` scenario set does NOT exist, but it exists in `data/scenario_sets/` — so every competent model runs it and "fails" the trap (0/23). Fix: use a benchmark-reserved set name verified absent, AND have the runner assert-absent at match setup (fail-loud if it ever exists, so the check can never silently invert again).

**Files:**
- Modify: `backend/app/services/arena/runner.py` (add `_assert_trap_sets_absent(loaded, settings)` called in `run_match` setup, before the drive loop ~line 435)
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md` (step-8 user text + `expected_tools`/`outcome`)
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json` (`step-trap-missing-scenario-set`)
- Test: `tests/test_arena_runner.py`, `tests/test_flagship_loads.py`

**Interfaces:**
- Produces: a per-workflow reserved trap-set name. Choose `"stagflation-shock-2011"` (verify absent: `ls data/scenario_sets/` shows no such file). The runner reads the trap-set name(s) from the workflow's step whose `expected_tools` is exactly `[list_scenario_library]` and whose user text names a quoted set — OR simpler, a new optional manifest key `trap_absent_sets: ["stagflation-shock-2011"]` at the workflow top level. Use the explicit key.

- [ ] **Step 1: Verify the chosen name is absent**

Run: `ls data/scenario_sets/ | grep -i stagflation || echo ABSENT`
Expected: `ABSENT`.

- [ ] **Step 2: Write the failing runner test** in `tests/test_arena_runner.py`:

```python
def test_assert_trap_sets_absent_raises_when_present(tmp_path, monkeypatch):
    from app.services.arena.runner import _assert_trap_sets_absent
    from app.config import Settings
    # seed a scenario_sets dir that WRONGLY contains the reserved trap name
    d = tmp_path / "scenario_sets"; d.mkdir()
    (d / "stagflation-shock-2011.yaml").write_text("version: '1.0'\nscenarios: []\n")
    settings = Settings(scenario_sets_dir=str(d))
    loaded = _load_flagship()  # helper that loads risk-manager-control-day bundle
    import pytest
    with pytest.raises(RuntimeError, match="trap.*present"):
        _assert_trap_sets_absent(loaded, settings)


def test_assert_trap_sets_absent_ok_when_missing(tmp_path):
    from app.services.arena.runner import _assert_trap_sets_absent
    from app.config import Settings
    d = tmp_path / "scenario_sets"; d.mkdir()
    settings = Settings(scenario_sets_dir=str(d))
    loaded = _load_flagship()
    _assert_trap_sets_absent(loaded, settings)  # no raise
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_arena_runner.py -k trap_sets_absent -v`
Expected: FAIL — `_assert_trap_sets_absent` undefined.

- [ ] **Step 4: Implement** — add to `runner.py`:

```python
def _assert_trap_sets_absent(loaded, settings) -> None:
    """Fail-loud if a benchmark-reserved 'does-not-exist' scenario set actually
    exists in the active scenario library. A silently-present trap set inverts
    the trap check (competent models run it and 'fail'). Reads workflow-level
    `trap_absent_sets`; no-op when unset."""
    names = getattr(loaded.workflow, "trap_absent_sets", None) or []
    if not names:
        return
    from pathlib import Path
    d = Path(settings.scenario_sets_dir)
    for name in names:
        if (d / f"{name}.yaml").exists() or (d / f"{name}.set.json").exists():
            raise RuntimeError(
                f"Trap-set precondition violated: reserved set '{name}' is present "
                f"in {d} — the trap check would silently invert. Remove it or pick "
                f"another reserved name.")
```

Call it in `run_match` immediately after resolving `settings`/before the drive loop. Add `trap_absent_sets: list[str] = []` to the workflow schema (`backend/app/golden_workflows/schema.py` `Workflow` model) and parse it from frontmatter (loader).

- [ ] **Step 5: Update the manifest** — step 8 user text → the reserved name, add the workflow-level key. In `risk-manager-control-day.md` frontmatter top level add `trap_absent_sets: ["stagflation-shock-2011"]`; change step 8 `user:` to `"Also stress the book with the 'stagflation-shock-2011' scenario set using the Control Profile."`; update `outcome`, the `## Step 8` prose, and the `response_contains` any_of stays as-is. Update the fixture `step-trap-missing-scenario-set` so its response mentions "stagflation-shock-2011" is "not found".

- [ ] **Step 6: Run runner + flagship-load + regression tests**

Run: `.venv/bin/python -m pytest tests/test_arena_runner.py tests/test_flagship_loads.py tests/test_golden_workflow_regression.py -v`
Expected: PASS; golden replay still full objective marks; the trap step now passes for a "reports not-found" transcript.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/arena/runner.py backend/app/golden_workflows/ tests/
git commit -m "fix(arena): trap uses reserved absent set name + runner asserts absence at setup"
```

### Task 0.3: Re-harvest the dead grounding fixtures/paths

**Context:** step-3 grounds on `hotspot.delta` but the real `get_latest_risk_run` payload has no `hotspot` key; step-5 grounds on `landscape[spot_shift=0.1].gamma` but the real `get_greeks_landscape_run` payload is `results.positions[].curves.raw[]` with `spot_shift_pct` in PERCENT (10.0, not 0.1). Both are 0/23 — unsatisfiable. Fix paths + fixture values against real payloads.

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md` (step-3 + step-5 `response_quotes_tool_value` paths)
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json` (matching fixture tool-result payloads + response text)
- Test: `tests/test_golden_workflow_regression.py` (golden replay), `tests/test_golden_workflow_assertions.py`

- [ ] **Step 1: Harvest the real payload shapes** (from run-10 traces, already in the trace DB):

```bash
.venv/bin/python - <<'PY'
import sqlite3, json
tdb = sqlite3.connect("data/agent_traces.sqlite3")
for name in ("get_latest_risk_run","get_greeks_landscape_run"):
    r = tdb.execute("SELECT outputs FROM trace_runs WHERE thread_id=217 AND run_type='tool' AND name=? AND status='success' ORDER BY start_time DESC LIMIT 1",(name,)).fetchone()
    d = json.loads(json.loads(r[0])["output"]["kwargs"]["content"])
    print(name, "TOPKEYS", list(d.keys()))
    print(json.dumps(d, indent=1)[:1500])
PY
```

Expected: `get_latest_risk_run` top keys `[portfolio_id, found, risk_run_id, status, created_at, valuation_as_of, metrics]` — locate a delta figure under `metrics` (e.g. `metrics.hotspots[0].delta` or `metrics.positions[...]`); `get_greeks_landscape_run` → `results.positions[position_id=8].curves.raw[spot_shift_pct=10.0].gamma` and `[spot_shift_pct=-20.0].delta`.

- [ ] **Step 2: Update the manifest paths** to the harvested real paths, e.g.:
  - step-3: `path: "metrics.hotspots[0].delta"` (use the actual harvested path), keep `near: ["delta"]`.
  - step-5: `path: "results.positions[position_id=8].curves.raw[spot_shift_pct=10.0].gamma"` and `path: "results.positions[position_id=8].curves.raw[spot_shift_pct=-20.0].delta"`, `scope: session`, `near: ["gamma"]` / `["delta"]`.

- [ ] **Step 3: Update the fixtures** so `step-3-read-fresh-risk` and `step-grid-comprehension` tool-result payloads match the real shape and the fixture response text quotes those exact numbers.

- [ ] **Step 4: Run the golden replay + assertions**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_regression.py::test_flagship_golden_replay_scores_full_marks tests/test_golden_workflow_assertions.py -v`
Expected: PASS — the replay earns full objective marks with the corrected paths (previously the grounding checks were dead and the fixtures matched the imagined shape).

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/ tests/
git commit -m "fix(golden): re-harvest grounding paths/fixtures from real tool payloads"
```

**Phase 0 done → objective axis is now trustworthy; P3 is unblocked.**

---

## Phase 1 — Rubric reallocation (judge rubric 6 → 2)

### Task 1.1: Shrink the judge rubric to 2 subjective points

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md` (`success.rubric`, lines 219–225)
- Test: `tests/test_flagship_loads.py`, `tests/test_arena_judge.py`, `tests/test_golden_workflow_regression.py`

**Interfaces:**
- Consumes: `_collect_rubric_points(loaded)` in `judge.py` (unchanged) now returns 2 points.
- Produces: the flagship rubric has exactly 2 points; the **objective** denominator (39) is UNCHANGED (rubric points feed only the judge, not the objective count).

- [ ] **Step 1: Write/adjust the failing load test** in `tests/test_flagship_loads.py`:

```python
def test_flagship_rubric_is_two_subjective_points():
    loaded = get_workflow_bundle("risk-manager-control-day")
    from app.services.arena.judge import _collect_rubric_points
    pts = _collect_rubric_points(loaded)
    assert len(pts) == 2
    joined = " ".join(pts).lower()
    assert "synthesis" in joined and "analytical" in joined
    assert "reasoning depth" not in joined  # forbidden: rewards verbosity
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py::test_flagship_rubric_is_two_subjective_points -v`
Expected: FAIL — currently 6 points.

- [ ] **Step 3: Edit the manifest** — replace the 6-line `rubric:` block with 2 points:

```yaml
  rubric:
    - "Synthesis coherence: 100 = the governance report weaves hotspot, landscape, scenario loss and backtest into ONE coherent narrative with the figures tied to their meaning; 50 = a correct but disjointed list of results; 0 = thin, fragmentary, or missing synthesis."
    - "Analytical correctness: 100 = the risk interpretations are sound — correct direction of risk, what the breach implies, and a recommendation that follows from the numbers; 50 = partially correct or hedged interpretation; 0 = wrong-signed or unsupported conclusions."
```

- [ ] **Step 4: Fix any hardcoded rubric-count assertions** — search and update:

Run: `grep -rn "rubric" tests/test_flagship_loads.py tests/test_arena_judge.py tests/test_golden_workflow_regression.py`
Update any count/point-text assertions to the 2-point rubric. The golden replay's judge is faked, so replay objective marks are unaffected.

- [ ] **Step 5: Run the affected suites**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_arena_judge.py tests/test_golden_workflow_regression.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/definitions/risk-manager-control-day.md tests/
git commit -m "feat(arena): shrink judge rubric 6->2 subjective points (delete deterministic-redundant)"
```

---

## Phase 2 — De-biased jury

### Task 2.1: `JudgeResult` gains per-judge + stdev + mode

**Files:**
- Modify: `backend/app/services/arena/judge.py` (`JudgeResult` dataclass)
- Test: `tests/test_arena_judge.py`

**Interfaces:**
- Produces: `JudgeResult` fields `per_judge: list[dict]` (`{model, rubric_scores, judged_score}`), `judged_stdev: float | None`, `subjective_mode: str` (`"panel"` | `"self_consistency"` | `"missing"`). Existing fields (`rubric_scores`, `judged_score`, `judge_missing`, `notes`, `diagnosis`) retained; for a panel, `rubric_scores` = the point-wise mean across judges, `judged_score` = panel mean.

- [ ] **Step 1: Write the failing test**:

```python
def test_judgeresult_has_panel_fields():
    from app.services.arena.judge import JudgeResult
    r = JudgeResult()
    assert r.per_judge == [] and r.judged_stdev is None and r.subjective_mode == "missing"
```

- [ ] **Step 2: Run → fail.** `.venv/bin/python -m pytest tests/test_arena_judge.py::test_judgeresult_has_panel_fields -v`

- [ ] **Step 3: Implement** — add to the dataclass:

```python
    per_judge: list[dict] = field(default_factory=list)
    judged_stdev: float | None = None
    subjective_mode: str = "missing"  # "panel" | "self_consistency" | "missing"
```

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit** `git commit -am "feat(arena): JudgeResult carries per-judge scores, stdev, subjective_mode"`

### Task 2.2: `judge_panel` — jury, exclusion, stdev, self-consistency

**Files:**
- Modify: `backend/app/services/arena/judge.py` (new `judge_panel`, refactor single-call into `_judge_one`)
- Test: `tests/test_arena_judge.py`

**Interfaces:**
- Consumes: existing `_build_prompt`, `_build_payload`, `_parse_response`, `_default_post`.
- Produces: `judge_panel(transcript, loaded, *, judge_models: list[str], exclude_model: str | None = None, min_judges: int = 2, self_consistency_k: int = 3, post_for: Callable[[str], Callable[[dict], str]] | None = None) -> JudgeResult`. `post_for(model_id)` returns a poster for that model (test seam; default builds a real poster bound to the model's channel/name). Each judge scored once; panel mean = mean of per-judge means; `judged_stdev` = population stdev of per-judge means (0.0 if one judge).

- [ ] **Step 1: Write failing tests** (all network-free via `post_for`):

```python
def _fake_post_for(scores_by_model):
    import json
    def factory(model_id):
        def post(payload):
            pts = payload["messages"][1]["content"]
            # return the fixed score for this model for every rubric point
            import re
            points = re.findall(r"^\d+\.\s+(.*)$", pts, re.M)
            return json.dumps({"rubric_scores": [{"point": p, "score": scores_by_model[model_id], "rationale": "x"} for p in points], "overall_notes": "", "diagnosis": ""})
        return post
    return factory

def test_panel_averages_and_stdev(loaded_flagship):
    from app.services.arena.judge import judge_panel
    r = judge_panel(_transcript(), loaded_flagship,
                    judge_models=["deepseek-v4-pro", "claude-opus-4-8", "qwen-3-7-max"],
                    post_for=_fake_post_for({"deepseek-v4-pro": 60, "claude-opus-4-8": 90, "qwen-3-7-max": 30}))
    assert r.subjective_mode == "panel"
    assert r.judged_score == 60.0            # mean of 60/90/30
    assert round(r.judged_stdev, 1) == 24.5  # pop stdev
    assert len(r.per_judge) == 3

def test_panel_rubric_points_are_per_point_mean(loaded_flagship):
    """When judges disagree PER POINT, the panel rubric must be the by-label
    mean — not the first judge's scores (which would depend on ordering)."""
    import json, re
    # judge A: [80, 20]; judge B: [20, 80] for the 2 rubric points → mean [50, 50]
    per_model = {"deepseek-v4-pro": [80, 20], "claude-opus-4-8": [20, 80]}
    def post_for(model_id):
        def post(payload):
            points = re.findall(r"^\d+\.\s+(.*)$", payload["messages"][1]["content"], re.M)
            scs = per_model[model_id]
            return json.dumps({"rubric_scores": [{"point": p, "score": scs[i], "rationale": ""}
                                                 for i, p in enumerate(points)],
                               "overall_notes": "", "diagnosis": ""})
        return post
    from app.services.arena.judge import judge_panel
    r = judge_panel(_transcript(), loaded_flagship,
                    judge_models=["deepseek-v4-pro", "claude-opus-4-8"], min_judges=2,
                    post_for=post_for)
    assert [round(s["score"], 1) for s in r.rubric_scores] == [50.0, 50.0]  # NOT [80, 20]

def test_panel_excludes_contestant(loaded_flagship):
    from app.services.arena.judge import judge_panel
    r = judge_panel(_transcript(), loaded_flagship,
                    judge_models=["deepseek-v4-pro", "claude-opus-4-8", "qwen-3-7-max"],
                    exclude_model="claude-opus-4-8",
                    post_for=_fake_post_for({"deepseek-v4-pro": 60, "qwen-3-7-max": 40}))
    assert {j["model"] for j in r.per_judge} == {"deepseek-v4-pro", "qwen-3-7-max"}

def test_panel_postfailure_below_min_escalates_to_self_consistency(loaded_flagship):
    """3-judge pool, 2 calls fail, 1 survives -> self-consistency, NOT proceed-on-one."""
    from app.services.arena.judge import judge_panel
    calls = {"n": 0}
    import json
    def post_for(model_id):
        def post(payload):
            if model_id in ("claude-opus-4-8", "qwen-3-7-max"):
                raise RuntimeError("402 quote_exceeded")
            calls["n"] += 1
            import re
            points = re.findall(r"^\d+\.\s+(.*)$", payload["messages"][1]["content"], re.M)
            return json.dumps({"rubric_scores": [{"point": p, "score": 55, "rationale": ""} for p in points], "overall_notes": "", "diagnosis": ""})
        return post
    r = judge_panel(_transcript(), loaded_flagship,
                    judge_models=["deepseek-v4-pro", "claude-opus-4-8", "qwen-3-7-max"],
                    min_judges=2, self_consistency_k=3, post_for=post_for)
    assert r.subjective_mode == "self_consistency"
    assert calls["n"] == 3   # k samples on the surviving deepseek judge

def test_panel_zero_survivors_is_missing(loaded_flagship):
    from app.services.arena.judge import judge_panel
    def post_for(model_id):
        def post(payload): raise RuntimeError("402 quote_exceeded")
        return post
    r = judge_panel(_transcript(), loaded_flagship,
                    judge_models=["deepseek-v4-pro", "claude-opus-4-8"], post_for=post_for)
    assert r.judge_missing and r.subjective_mode == "missing" and r.judged_score is None
```

- [ ] **Step 2: Run → fail** (`judge_panel` undefined).

- [ ] **Step 3: Implement** in `judge.py`:

```python
import statistics

def _judge_one(model_id, transcript, loaded, poster, retries=2) -> dict | None:
    """One judge's scored rubric via the existing single-call path. Returns
    {model, rubric_scores, judged_score} or None on failure/exhaustion."""
    res = _judge_single(transcript, loaded, post=poster, retries=retries)  # refactor of current judge_match body
    if res.judge_missing:
        return None
    return {"model": model_id, "rubric_scores": res.rubric_scores, "judged_score": res.judged_score}

def _mean_rubric(per_judge) -> list[dict]:
    """Average each rubric point BY LABEL across all judges, so the headline
    rubric breakdown explains the published subjective mean (never judge[0]'s,
    which would depend on ordering and mislead)."""
    from collections import defaultdict
    sums, counts, order = defaultdict(float), defaultdict(int), []
    for j in per_judge:
        for s in j["rubric_scores"]:
            if s["point"] not in counts:
                order.append(s["point"])
            sums[s["point"]] += s["score"]; counts[s["point"]] += 1
    return [{"point": p, "score": round(sums[p] / counts[p], 4), "rationale": "panel mean"}
            for p in order]

def _aggregate(per_judge, mode) -> JudgeResult:
    means = [j["judged_score"] for j in per_judge]
    panel_mean = sum(means) / len(means)
    stdev = statistics.pstdev(means) if len(means) > 1 else 0.0
    return JudgeResult(rubric_scores=_mean_rubric(per_judge), judged_score=round(panel_mean, 4),
                       judge_missing=False, per_judge=per_judge, judged_stdev=round(stdev, 4),
                       subjective_mode=mode)

def judge_panel(transcript, loaded, *, judge_models, exclude_model=None, min_judges=2,
                self_consistency_k=3, post_for=None):
    post_for = post_for or _default_post_for
    pool = [m for m in judge_models if m != exclude_model]
    def missing(): return JudgeResult(judge_missing=True, judged_score=None, subjective_mode="missing",
                                      notes="No eligible judges.")
    if not pool:
        return missing()

    if len(pool) >= min_judges:
        survivors = [j for m in pool if (j := _judge_one(m, transcript, loaded, post_for(m)))]
        if len(survivors) >= min_judges:
            return _aggregate(survivors, "panel")
        # post-failure below floor -> self-consistency on one surviving eligible judge
        eligible = survivors[0]["model"] if survivors else (pool[0] if pool else None)
    else:
        eligible = pool[0]

    # self-consistency: k samples on `eligible`
    if eligible is None:
        return missing()
    samples = [j for _ in range(self_consistency_k)
               if (j := _judge_one(eligible, transcript, loaded, post_for(eligible)))]
    if not samples:
        return missing()
    return _aggregate(samples, "self_consistency")
```

Refactor the current `judge_match` body into `_judge_single(...)` (identical logic, returns `JudgeResult`), and keep `judge_match` as a thin wrapper for back-compat (single model = `JUDGE_MODEL`). Add `_default_post_for(model_id)` returning a poster bound to that model's channel (reuse `_default_post` but parameterize model + base_url; DeepSeek-direct vs ZenMux per the model's channel).

- [ ] **Step 4: Run → pass.** `.venv/bin/python -m pytest tests/test_arena_judge.py -v`

- [ ] **Step 5: Commit** `git commit -am "feat(arena): judge_panel jury — exclusion, stdev, post-failure self-consistency"`

### Task 2.3: Judge-pool config + defaults + substitution

**Files:**
- Modify: `backend/app/config.py` (Settings: `arena_judge_models`, `arena_min_judges`, `arena_self_consistency_k`, `arena_judge_substitutes`)
- Modify: `backend/app/services/arena/judge.py` (`_default_post_for` channel resolution via `agent_channels`)
- Modify: `config/agent_channels.example.yml` + (per-env) `config/agent_channels.yaml` — ensure `qwen/qwen3.7-max` and the substitution models are declared.
- Test: `tests/test_config.py`, `tests/test_arena_judge.py`

- [ ] **Step 1: Failing config test** (`tests/test_config.py`):

```python
def test_arena_judge_pool_defaults():
    from app.config import Settings
    s = Settings()
    assert s.arena_judge_models == ["deepseek-v4-pro", "anthropic/claude-opus-4.8", "qwen/qwen3.7-max"]
    assert s.arena_min_judges == 2 and s.arena_self_consistency_k == 3
    assert s.arena_judge_substitutes == ["gemini-3.1-pro-preview", "glm-5.2", "kimi-k2.7-code"]
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** the Settings fields (defaults above; loadable from env as comma-lists), and `_default_post_for` that resolves each model's channel/base_url/api-key from `agent_channels` (DeepSeek → `api.deepseek.com` + `DEEPSEEK_API_KEY`; others → ZenMux). Confirm the models are declared in `config/agent_channels.example.yml` (add `qwen/qwen3.7-max` + substitutes if absent — CLAUDE.md: edit BOTH the example and the gitignored live yaml).

- [ ] **Step 4: Run config + judge tests → pass.**

- [ ] **Step 5: Commit** `git commit -am "feat(arena): judge-pool settings + channel-aware poster + panel model decls"`

---

## Phase 3 — Reporting reform (ONLY after Phase 0 is green)

### Task 3.1: Drop the blend; objective ranking + tie policy + subjective columns

**Files:**
- Modify: `backend/app/services/arena/store.py` (`leaderboard`, ~195–229)
- Modify: `backend/app/services/arena/scoring.py` (add `objective_tiebreak_key(breakdown)` helper for D5a)
- Test: `tests/test_arena_store.py`, `tests/test_arena_scoring.py`

**Interfaces:**
- Produces: leaderboard rows gain `subjective_mean: float | None`, `subjective_stdev: float | None`, `subjective_mode: str`, and an explicit `rank: int`; `mean_total` REMOVED as a sort key (kept as an optional labelled `advisory_composite` field or dropped). Sort key: `(-mean_objective, tiebreak by sub-axis priority, model_id)` for a stable display order, but **`rank` is SHARED** across rows whose `(mean_objective, tiebreak_key)` are exactly equal (standard competition ranking: 1,1,3…). `model_id` only stabilizes display order — it NEVER breaks the rank. Subjective never affects rank. Rows aggregate `subjective_mean` = mean of per-match `judged_score` (jury means); `subjective_stdev` = mean of per-match `judged_stdev` (dispersion within matches) — document the choice.

- [ ] **Step 1: Failing store test** (`tests/test_arena_store.py`):

```python
def test_leaderboard_ranks_by_objective_not_blend(session):
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["hi", "lo"])
    arena_store.set_run_status(session, run_id, "completed")
    # 'hi' has higher OBJECTIVE but lower subjective; must still rank first
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="hi",
        objective_score=80.0, judged_score=40.0, total_score=None, judge_missing=False,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 40.0, "judged_stdev": 5.0}, "subjective_mode": "panel"})
    arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id="lo",
        objective_score=70.0, judged_score=95.0, total_score=None, judge_missing=False,
        config={}, transcript_path=None, status="scored",
        score_breakdown={"objective": {"axes": {}}, "judge": {"judged_score": 95.0, "judged_stdev": 5.0}, "subjective_mode": "panel"})
    rows = arena_store.leaderboard(session, run_id)
    assert [r["model_id"] for r in rows] == ["hi", "lo"]
    assert rows[0]["subjective_mean"] == 40.0 and rows[0]["subjective_stdev"] == 5.0
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — in `leaderboard`, aggregate `subjective_*` from `score_breakdown.judge`, drop `mean_total` from the sort key, sort by `(-mean_objective, _tiebreak(axes), model_id)`. Add `scoring.objective_tiebreak_key(axes)` returning a tuple ordered `grounding, adherence, synthesis, procedural` (each `-passed/total`). Then assign SHARED ranks — competition ranking on the `(mean_objective, tiebreak_key)` pair only:

```python
    rows.sort(key=lambda r: (-(r["mean_objective"] or 0.0), r["_tiebreak"], r["model_id"]))
    rank = 0
    prev_key = object()
    for i, r in enumerate(rows):
        key = (r["mean_objective"], r["_tiebreak"])  # model_id excluded — display-only
        if key != prev_key:
            rank = i + 1          # standard competition ranking (1,1,3)
            prev_key = key
        r["rank"] = rank
        del r["_tiebreak"]
```

Read the per-match `judged_stdev`/`subjective_mode` from `m.score_breakdown`.

- [ ] **Step 3b: Shared-rank test** (`tests/test_arena_store.py`) — two models with identical objective + identical sub-axis tallies share rank 1:

```python
def test_leaderboard_shares_rank_on_exact_objective_tie(session):
    run_id = arena_store.create_run(session, workflow_ids=["wf-a"], model_ids=["a", "b", "c"])
    arena_store.set_run_status(session, run_id, "completed")
    axes = {"axes": {"grounding": {"passed": 5, "total": 5}}}
    for mid, obj, sub in [("a", 80.0, 40.0), ("b", 80.0, 90.0), ("c", 70.0, 99.0)]:
        arena_store.record_match(session, run_id=run_id, workflow_id="wf-a", model_id=mid,
            objective_score=obj, judged_score=sub, total_score=None, judge_missing=False,
            config={}, transcript_path=None, status="scored",
            score_breakdown={"objective": axes, "judge": {"judged_score": sub, "judged_stdev": 0.0}, "subjective_mode": "panel"})
    ranks = {r["model_id"]: r["rank"] for r in arena_store.leaderboard(session, run_id)}
    assert ranks["a"] == 1 and ranks["b"] == 1  # tied objective -> shared rank, subjective ignored
    assert ranks["c"] == 3                        # competition ranking skips 2
```

- [ ] **Step 4: Failing scoring test** for the tie helper + run both → pass:

```python
def test_objective_tiebreak_prefers_grounding():
    from app.services.arena.scoring import objective_tiebreak_key
    a = {"grounding": {"passed": 5, "total": 5}, "adherence": {"passed": 1, "total": 8}}
    b = {"grounding": {"passed": 1, "total": 5}, "adherence": {"passed": 8, "total": 8}}
    assert objective_tiebreak_key(a) < objective_tiebreak_key(b)  # a wins on grounding
```

Run: `.venv/bin/python -m pytest tests/test_arena_store.py tests/test_arena_scoring.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(arena): rank leaderboard by objective + deterministic tie policy; expose subjective mean/stdev"`

### Task 3.2: Wire the jury into the run loop (contestant exclusion + judge-missing = objective-scored)

**Files:**
- Modify: `backend/app/services/arena/task.py` (`_execute`: call `judge_panel(..., exclude_model=model_id)`; judge-missing no longer invalidates)
- Test: `tests/test_arena_api.py`

**Interfaces:**
- Consumes: `judge_panel`, settings (`arena_judge_models`, etc.), the candidate `model_id` for exclusion.
- Produces: `score_breakdown.judge` now carries `per_judge`, `judged_stdev`, `subjective_mode`; a judge-missing match is `status="scored"` with objective score (NOT `invalid`) — infra-invalid stays reserved for candidate-side blank/contamination.

- [ ] **Step 1: Failing test** — a match whose jury is fully unavailable is still objective-scored:

```python
def test_judge_missing_stays_objective_scored(session, settings):
    run_id, task_id = _queue_single_pair_run(settings)
    def good_run_match(loaded, model, *, artifact_root, run_id=None):
        return _real_ish_transcript("wf-a", model.slug)  # non-blank, valid
    def dead_panel(*a, **k):
        from app.services.arena.judge import JudgeResult
        return JudgeResult(judge_missing=True, judged_score=None, subjective_mode="missing")
    # inject via judge_fn seam
    from app.services.arena.task import execute_arena_run_task
    execute_arena_run_task(task_id, run_id, database.SessionLocal, settings=settings,
                           run_match_fn=good_run_match, judge_fn=dead_panel, get_bundle_fn=_fake_get_bundle)
    with database.SessionLocal() as s:
        (m,) = arena_store.get_run(s, run_id)["matches"]
        assert m["status"] == "scored"
        assert m["objective_score"] is not None
```

- [ ] **Step 2: Run → fail** (if current code invalidates or blends).

- [ ] **Step 3: Implement** — in `_execute`, replace the `_judge_fn(...)` call so the default judge fn is `lambda t, l, **k: judge_panel(t, l, judge_models=settings.arena_judge_models, exclude_model=model_id, min_judges=settings.arena_min_judges, self_consistency_k=settings.arena_self_consistency_k)`. Build `breakdown["judge"]` from the panel result (`per_judge`, `judged_stdev`, `subjective_mode`). Ensure judge-missing → objective-only scoring (existing `total_score(..., judge_missing=True)` path already returns objective; keep it, but since the blend is dropped the leaderboard uses objective anyway).

- [ ] **Step 4: Run arena api suite → pass.** `.venv/bin/python -m pytest tests/test_arena_api.py -v`

- [ ] **Step 5: Commit** `git commit -am "feat(arena): run loop uses contestant-excluded jury; judge-missing stays objective-scored"`

### Task 3.3: API surface — subjective mean/stdev/mode + per-judge

**Files:**
- Modify: `backend/app/routers/arena.py` (`MatchSummary`, leaderboard row model)
- Test: `tests/test_arena_api.py`

- [ ] **Step 1: Failing API test** — GET leaderboard row carries `rank`, `subjective_mean`, `subjective_stdev`, `subjective_mode` (and two exactly-tied models share `rank`); match detail carries `per_judge`.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — add `rank`, `subjective_mean`, `subjective_stdev`, `subjective_mode` to the leaderboard row model and `per_judge` to the match-detail model; populate from store/breakdown.

- [ ] **Step 4: Run → pass.**

- [ ] **Step 5: Commit** `git commit -am "feat(arena): API exposes subjective mean/stdev/mode + per-judge scores"`

### Task 3.4: Frontend — separate axes, per-judge drilldown, degraded label

**Files:**
- Modify: `frontend/src/lib/arenaApi.ts` (types: row `subjective_mean?`, `subjective_stdev?`, `subjective_mode?`; breakdown `judge.per_judge?`, `judge.judged_stdev?`; drop reliance on blended total)
- Modify: `frontend/src/routes/Arena.live.tsx` (leaderboard: Objective primary column, Subjective `mean ± stdev` muted advisory + degraded badge when `subjective_mode==="self_consistency"`; drilldown: per-judge rows)
- Modify: `frontend/src/routes/Arena.css` (token-only additions)
- Test: `frontend/src/routes/Arena.live.test.tsx`

- [ ] **Step 1: Failing vitest** — render a leaderboard where model A has higher objective but lower subjective and assert A is listed first; assert two exactly-objective-tied models display the **same `rank`** (not 1 and 2); assert `mean ± stdev` text renders; assert a `subjective_mode==="self_consistency"` row shows a "degraded" badge; assert per-judge scores render in the drilldown.

- [ ] **Step 2: Run → fail.** `cd frontend && npx vitest run src/routes/Arena.live.test.tsx`

- [ ] **Step 3: Implement** the types + components (token-only; reuse existing `wl-arena__axes`/badge patterns; new muted `wl-arena__subjective` cell + `wl-arena__degraded-chip`). Rank display follows API order (already objective-sorted).

- [ ] **Step 4: Run vitest + tsc → pass.** `cd frontend && npx vitest run src/routes/Arena.live.test.tsx && npx tsc --noEmit`

- [ ] **Step 5: Commit** `git commit -am "feat(arena): frontend separate axes — objective ranked, subjective advisory mean±stdev + per-judge"`

### Task 3.5: Docs + full-suite gate

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]`), `CLAUDE.md` (arena scoring section — jury + separate axes + P0 fixes), `README.md` if user-facing.

- [ ] **Step 1: Update CHANGELOG/CLAUDE/README** describing: contamination-predicate fix, trap-absence enforcement, re-harvested grounding, rubric 6→2, jury with contestant-exclusion + stdev + self-consistency, separate-axes leaderboard (no blend), tie policy.
- [ ] **Step 2: Full backend suite** `.venv/bin/python -m pytest -q` → green.
- [ ] **Step 3: Frontend** `cd frontend && npm test && npx tsc --noEmit` → green (pre-existing unrelated failures noted, not arena).
- [ ] **Step 4: Commit** `git commit -am "docs(arena): changelog/CLAUDE for judge-fairness reform"`

---

## Self-Review notes (spec coverage)

- **§3 D1** → Task 1.1 (2 points, no reasoning-depth). **D2/D3** → 2.2/2.3 (panel, exclusion, substitution, config). **D4** → 2.2 (initial + post-failure self-consistency, degraded mode). **D5/D5a** → 3.1 (no blend, objective sort, tie policy). **D6** → 0.2 (trap). **D7** → no code (temp 0 kept; robustness via jury — documented in 3.5). **D8** → 2.2 tests (injected posters).
- **§4.4 contamination fix** → Task 0.1. **grounding paths** → 0.3. **P0-gates-P3** → phases ordered; 3.x steps assume 0.x committed.
- **§7 testing** → each task carries its regression; golden replay full objective marks preserved (0.2/0.3/1.1); post-failure escalation regression in 2.2; toolcall-then-402 in 0.1.
- Gap check: judge model channel resolution (DeepSeek-direct vs ZenMux) is the riskiest new surface → covered in 2.3 `_default_post_for`; verify against `config/agent_channels` at implementation.
