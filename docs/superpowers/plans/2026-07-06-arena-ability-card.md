# Arena Model Ability Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat single-number objective ranking with a FIFA-style 6-stat ability card (GRD/ADH/SYN/PRC/EFF + advisory JDG) and a numbers-first OVR, derived from the existing assertion evaluation — no re-scoring, no DB migration.

**Architecture:** The 39-check objective evaluation stays the source of truth. `scoring.py` gains pure helpers that derive five 0–99 stats from the axis tallies already emitted (`objective_breakdown().axes`), an EFF stat from correctness × par/actual tool calls, and an OVR weighted mean. `task.py` stamps a `card` block onto `score_breakdown` at write time; `store.py` re-derives OVR on read (so runs #10–#11 with stored axes get cards without migration; runs #1–#9 without axes are listed uncarded). A new `response_quotes_value` assertion scores grounding against harvested fixture truth (Spec A) regardless of whether the tool fired that turn. Frontend renders the card.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / Pydantic (backend); React 19 / TypeScript / Vitest (frontend). Tests: `.venv/bin/python -m pytest` (backend), `cd frontend && npm test` / `npx tsc --noEmit` (frontend).

## Global Constraints

- **No DB migration, no backfill.** Cards are derived on read (spec B8). Legacy rows without stored `axes` (runs #1–#9) are `card: null` / reason `"legacy_no_axes"`, excluded from `card_mean`/OVR ranking, still listed with `mean_objective`.
- **The 39-check denominator is unchanged.** Swapping `response_quotes_tool_value` → `response_quotes_value` is one-assertion-for-one; the golden replay must still earn **39/39**. Exact-count coupling lives in `test_flagship_loads`, `test_arena_scoring`, `test_golden_workflow_regression` — keep all green.
- **`_AXIS_BY_TYPE` (`scoring.py`) is the single authoritative axis map.** No assertion type appears in two axes. `tool_called` stays `adherence`; `tools_routed_sequence`/`skill_routed`/`task_returned_id` stay `procedural`.
- **OVR weights (spec B2):** `OVR = round(0.32·GRD + 0.26·ADH + 0.16·SYN + 0.16·EFF + 0.10·PRC)`. JDG is **never** in OVR.
- **`par` = complete compliant tool-call count**, default `sum(len(step.expected_tools))` (= 11 for the flagship), overridable by optional `par_tool_calls`. `par_tool_calls` is `int | None = None` (validated `≥ 1`) so existing manifests still load.
- **Frontend: token-only styling.** No hardcoded colors/spacing in `.css`; only `var(--token)` from `src/tokens/`. Never branch on `data-theme`. Verify light + dark + compact. Read `frontend/UI_STYLE_GUIDE.md` before Task 8.
- End every commit message with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 1: `response_quotes_value` assertion (fixture-truth grounding)

**Files:**
- Modify: `backend/app/golden_workflows/schema.py` (add model + union member)
- Modify: `backend/app/golden_workflows/assertions.py` (evaluate branch)
- Modify: `backend/app/services/arena/scoring.py` (`_AXIS_BY_TYPE`, `_assertion_label`)
- Test: `tests/test_golden_assertions.py` (or the existing assertions test module)

**Interfaces:**
- Consumes: `_quote_value_in_text(text, target, *, rel_tol, mode, near)` (existing, `assertions.py:165`).
- Produces: assertion type `response_quotes_value` with fields `value: float`, `rel_tol: float=0.02`, `scope: "step"|"session"="step"`, `match: "signed"|"magnitude"="signed"`, `near: list[str]|None=None`. Axis: `grounding`.

- [ ] **Step 1: Write the failing test**

Add to the assertions test module (find it: `grep -rl "evaluate_assertion" tests`):

```python
from app.golden_workflows.schema import parse_workflow  # noqa
from app.golden_workflows.assertions import AssertionContext, evaluate_assertion
from app.golden_workflows.schema import _ResponseQuotesValue

def _ctx(text):
    return AssertionContext(response_text=text, tool_calls=[], tool_results=[],
                            skills_routed=[], artifacts=[], task_ids=[])

def test_response_quotes_value_signed_hit():
    a = _ResponseQuotesValue(type="response_quotes_value", value=573.3467, near=["delta"])
    ok, _ = evaluate_assertion(a, _ctx("AAPL delta is 573.35"))
    assert ok

def test_response_quotes_value_signed_miss_on_wrong_sign():
    a = _ResponseQuotesValue(type="response_quotes_value", value=573.3467, near=["delta"])
    ok, _ = evaluate_assertion(a, _ctx("AAPL delta is -573.35"))
    assert not ok

def test_response_quotes_value_magnitude_ignores_sign():
    a = _ResponseQuotesValue(type="response_quotes_value", value=-7758.99,
                             match="magnitude", near=["cvar"])
    ok, _ = evaluate_assertion(a, _ctx("CVaR loss of 7,759"))
    assert ok

def test_response_quotes_value_no_tool_needed():
    # The point-2 fix: correct-from-context, ZERO tool_results present.
    a = _ResponseQuotesValue(type="response_quotes_value", value=16.403, near=["gamma"])
    ok, _ = evaluate_assertion(a, _ctx("gamma at +10% is 16.40"))
    assert ok
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_golden_assertions.py -k response_quotes_value -q`
Expected: FAIL — `ImportError: cannot import name '_ResponseQuotesValue'` (or unknown assertion).

- [ ] **Step 3: Add the schema model**

In `backend/app/golden_workflows/schema.py`, after `_ResponseQuotesToolValue` (line ~131), add:

```python
class _ResponseQuotesValue(BaseModel):
    type: Literal["response_quotes_value"]
    value: float
    rel_tol: float = 0.02
    scope: Literal["step", "session"] = "step"
    match: Literal["signed", "magnitude"] = "signed"
    near: list[str] | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "_ResponseQuotesValue":
        if not (0 < self.rel_tol < 1):
            raise ValueError("rel_tol must be in (0, 1)")
        if self.near is not None and not self.near:
            raise ValueError("near must be non-empty when present")
        return self
```

Add `_ResponseQuotesValue` to the `Assertion` union (line ~133):

```python
Assertion = Annotated[
    Union[_SkillRouted, _SkillsRoutedSequence, _ToolsRoutedSequence, _ToolCalled,
          _TaskReturnedId, _ArtifactExists, _ResponseContains, _ToolResultPath,
          _ToolNotCalled, _ArtifactContains, _ResponseQuotesToolValue,
          _ResponseQuotesValue],
    Field(discriminator="type"),
]
```

- [ ] **Step 4: Add the evaluate branch**

In `backend/app/golden_workflows/assertions.py`, inside `evaluate_assertion`, before the final `return False, f"unknown assertion {t}"`:

```python
    if t == "response_quotes_value":
        ok = _quote_value_in_text(ctx.response_text, float(a.value),
                                  rel_tol=a.rel_tol, mode=a.match, near=a.near)
        return ok, "" if ok else (
            f"response does not quote value {a.value} "
            f"(match={a.match}, rel_tol={a.rel_tol}, near={a.near})")
```

- [ ] **Step 5: Register axis + label**

In `backend/app/services/arena/scoring.py`, add to `_AXIS_BY_TYPE` (after the `response_quotes_tool_value` entry):

```python
    "response_quotes_value": "grounding",
```

In `_assertion_label`, before the final `return t`:

```python
    if t == "response_quotes_value":
        return f"response quotes value {a.value}"
```

- [ ] **Step 6: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/test_golden_assertions.py -k response_quotes_value -q`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/schema.py backend/app/golden_workflows/assertions.py backend/app/services/arena/scoring.py tests/test_golden_assertions.py
git commit -m "feat(arena): response_quotes_value fixture-truth grounding assertion"
```

---

### Task 2: `par_tool_calls` field + `designed_par` helper

**Files:**
- Modify: `backend/app/golden_workflows/schema.py` (`GoldenWorkflow.par_tool_calls`)
- Modify: `backend/app/services/arena/scoring.py` (`designed_par`)
- Test: `tests/test_arena_scoring.py`

**Interfaces:**
- Produces: `GoldenWorkflow.par_tool_calls: int | None = None` (validated `≥ 1`); `scoring.designed_par(workflow) -> int` returning `par_tool_calls` if set else `sum(len(s.expected_tools) for s in workflow.steps)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_arena_scoring.py`:

```python
from app.services.arena import scoring
from app.golden_workflows.registry import get_workflow

def test_designed_par_defaults_to_expected_tools_sum():
    wf = get_workflow("risk-manager-control-day")
    # flagship declares par_tool_calls: 11 (Task 7); equals the expected_tools sum
    assert scoring.designed_par(wf) == 11
    assert scoring.designed_par(wf) == sum(len(s.expected_tools) for s in wf.steps)

def test_designed_par_override_wins():
    from app.golden_workflows.schema import GoldenWorkflow
    wf = get_workflow("risk-manager-control-day")
    data = wf.model_dump()
    data["par_tool_calls"] = 99
    overridden = GoldenWorkflow(**data)
    assert scoring.designed_par(overridden) == 99
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -k designed_par -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'designed_par'`.

- [ ] **Step 3: Add the schema field**

In `backend/app/golden_workflows/schema.py`, in `GoldenWorkflow`, after `trap_absent_sets` (line ~179):

```python
    # Designed complete-run tool-call count for the EFF ability stat. Optional so
    # existing manifests still load; when absent, scoring.designed_par derives it
    # from sum(len(step.expected_tools)). Overridable per-workflow.
    par_tool_calls: int | None = Field(default=None, ge=1)
```

- [ ] **Step 4: Add the helper**

In `backend/app/services/arena/scoring.py`, after `objective_tiebreak_key` (line ~86):

```python
def designed_par(workflow) -> int:
    """Designed complete-run tool-call count for the EFF stat. Explicit
    ``par_tool_calls`` wins; else sum of per-step expected tools."""
    explicit = getattr(workflow, "par_tool_calls", None)
    if explicit is not None:
        return explicit
    return sum(len(s.expected_tools) for s in workflow.steps)
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -k designed_par -q`
Expected: PASS. (The `== 11` assertion depends on Task 7 adding `par_tool_calls: 11`; if run before Task 7, it still passes because the expected_tools sum is 11 — verify: 1+1+1+2+0+2+2+1+1 = 11.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/schema.py backend/app/services/arena/scoring.py tests/test_arena_scoring.py
git commit -m "feat(arena): optional par_tool_calls field + designed_par derivation"
```

---

### Task 3: `ability_card` + `card_from_axes` + `card_tiebreak_key`

**Files:**
- Modify: `backend/app/services/arena/scoring.py`
- Test: `tests/test_arena_scoring.py`

**Interfaces:**
- Produces:
  - `card_from_axes(axes: dict, tool_calls: int, par: int, judged: float | None = None) -> dict` → `{"ovr": int, "stats": {"GRD","ADH","SYN","PRC","EFF": int}, "jdg": float | None, "position": str}`.
  - `ability_card(transcript, loaded, judged: float | None = None) -> dict` → convenience wrapper computing axes via `objective_breakdown` + tool count via `diagnose_heuristic` + `designed_par(loaded.workflow)`.
  - `card_tiebreak_key(stats: dict) -> tuple` → descending sort key over GRD→ADH→SYN→EFF→PRC.
- Consumes: `_STAT_BY_AXIS` map (grounding→GRD, adherence→ADH, synthesis→SYN, procedural→PRC).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_arena_scoring.py`:

```python
def test_card_from_axes_stats_and_ovr():
    axes = {
        "grounding": {"passed": 10, "total": 10},   # GRD 99
        "adherence": {"passed": 5, "total": 10},    # ADH 50 → round(49.5)=50
        "synthesis": {"passed": 3, "total": 3},     # SYN 99
        "procedural": {"passed": 0, "total": 5},    # PRC 0
    }
    # correct-heavy, lean: par 11, actual 11 → EFF ratio 1 → EFF = round(C*99)
    card = scoring.card_from_axes(axes, tool_calls=11, par=11, judged=42.0)
    s = card["stats"]
    assert s["GRD"] == 99 and s["SYN"] == 99 and s["PRC"] == 0
    assert s["ADH"] == 50
    # C = (10+5+3)/(10+10+3) = 18/23 = 0.7826 → EFF = round(0.7826*1*99) = 77
    assert s["EFF"] == 77
    # OVR = round(.32*99+.26*50+.16*99+.16*77+.10*0) = round(31.68+13+15.84+12.32+0)=round(72.84)=73
    assert card["ovr"] == 73
    assert card["jdg"] == 42.0

def test_card_eff_penalizes_bloat_not_leanness():
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    lean = scoring.card_from_axes(axes, tool_calls=11, par=11)   # ratio 1.0
    bloat = scoring.card_from_axes(axes, tool_calls=22, par=11)  # ratio 0.5
    assert lean["stats"]["EFF"] == 99          # C=1, ratio capped at 1
    assert bloat["stats"]["EFF"] == 50         # round(1*0.5*99)=50 (49.5→50)
    fewer = scoring.card_from_axes(axes, tool_calls=5, par=11)   # ratio capped at 1
    assert fewer["stats"]["EFF"] == 99          # leaner than par is NOT penalized

def test_card_do_nothing_scores_low_eff():
    axes = {"grounding": {"passed": 0, "total": 10}, "adherence": {"passed": 0, "total": 10},
            "synthesis": {"passed": 0, "total": 3}, "procedural": {"passed": 3, "total": 3}}
    card = scoring.card_from_axes(axes, tool_calls=0, par=11)  # actual 0 → ratio 1 guard
    assert card["stats"]["EFF"] == 0   # C=0 → EFF 0 despite ratio guard (no gaming)

def test_card_jdg_excluded_from_ovr():
    axes = {"grounding": {"passed": 5, "total": 10}, "adherence": {"passed": 5, "total": 10},
            "synthesis": {"passed": 1, "total": 2}, "procedural": {"passed": 2, "total": 4}}
    a = scoring.card_from_axes(axes, 11, 11, judged=0.0)
    b = scoring.card_from_axes(axes, 11, 11, judged=99.0)
    assert a["ovr"] == b["ovr"]   # JDG never moves OVR

def test_card_tiebreak_priority():
    hi_grd = {"GRD": 90, "ADH": 10, "SYN": 10, "EFF": 10, "PRC": 10}
    hi_adh = {"GRD": 10, "ADH": 90, "SYN": 10, "EFF": 10, "PRC": 10}
    assert scoring.card_tiebreak_key(hi_grd) < scoring.card_tiebreak_key(hi_adh)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -k "card_" -q`
Expected: FAIL — `AttributeError: ... 'card_from_axes'`.

- [ ] **Step 3: Implement the helpers**

In `backend/app/services/arena/scoring.py`, after `designed_par` (Task 2), add:

```python
# Ability-card stat ← axis map (spec B1). One axis → one stat; EFF is computed.
_STAT_BY_AXIS = {
    "grounding": "GRD",
    "adherence": "ADH",
    "synthesis": "SYN",
    "procedural": "PRC",
}
# OVR weights (spec B2). Sum to 1.00. JDG is NOT here (advisory).
_OVR_WEIGHTS = {"GRD": 0.32, "ADH": 0.26, "SYN": 0.16, "EFF": 0.16, "PRC": 0.10}
# Correctness axes for EFF's C multiplier (procedural excluded on purpose).
_CORRECTNESS_AXES = ("grounding", "adherence", "synthesis")
# Card tie-break priority (spec B5): GRD → ADH → SYN → EFF → PRC.
_CARD_TIEBREAK_PRIORITY = ("GRD", "ADH", "SYN", "EFF", "PRC")


def _stat_from_tally(tally: dict) -> int:
    total = tally.get("total", 0)
    return round(99 * tally.get("passed", 0) / total) if total else 0


def _correctness(axes: dict) -> float:
    passed = sum(axes.get(ax, {}).get("passed", 0) for ax in _CORRECTNESS_AXES)
    total = sum(axes.get(ax, {}).get("total", 0) for ax in _CORRECTNESS_AXES)
    return (passed / total) if total else 0.0


def _card_position(stats: dict) -> str:
    """Presentation-only archetype from the dominant stat (spec B6)."""
    vals = [stats[k] for k in ("GRD", "ADH", "SYN", "EFF", "PRC")]
    if max(vals) - min(vals) <= 8:
        return "All-rounder"
    dominant = max(("GRD", "ADH", "SYN", "EFF", "PRC"), key=lambda k: stats[k])
    return {
        "GRD": "Sniper", "ADH": "Anchor", "PRC": "Anchor",
        "SYN": "Playmaker", "EFF": "Playmaker",
    }[dominant]


def card_from_axes(axes: dict, tool_calls: int, par: int,
                   judged: float | None = None) -> dict:
    """Derive the ability card from objective axis tallies + tool-call count.

    Pure. Used both at write time (task.py) and derive-on-read (store.py)."""
    stats = {stat: 0 for stat in ("GRD", "ADH", "SYN", "PRC")}
    for axis, stat in _STAT_BY_AXIS.items():
        stats[stat] = _stat_from_tally(axes.get(axis, {}))
    c = _correctness(axes)
    ratio = min(1.0, par / tool_calls) if tool_calls > 0 else 1.0
    stats["EFF"] = round(c * ratio * 99)
    ovr = round(sum(_OVR_WEIGHTS[k] * stats[k] for k in _OVR_WEIGHTS))
    return {"ovr": ovr, "stats": stats, "jdg": judged,
            "position": _card_position(stats)}


def card_tiebreak_key(stats: dict) -> tuple:
    """Descending sort key (better first) over GRD→ADH→SYN→EFF→PRC."""
    return tuple(-stats.get(k, 0) for k in _CARD_TIEBREAK_PRIORITY)


def ability_card(transcript, loaded, judged: float | None = None) -> dict:
    """Convenience wrapper: evaluate the transcript once and build the card."""
    bd = objective_breakdown(transcript, loaded)
    heuristic = diagnose_heuristic(transcript, loaded)
    return card_from_axes(bd["axes"], heuristic["tool_calls"],
                          designed_par(loaded.workflow), judged=judged)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -k "card_" -q`
Expected: PASS (5 tests). If an OVR/EFF assertion is off by 1, re-derive the rounding by hand and correct the *test's* expected literal (the implementation rounding is canonical) — do not fudge the formula.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/scoring.py tests/test_arena_scoring.py
git commit -m "feat(arena): ability_card / card_from_axes / card_tiebreak_key derivation"
```

---

### Task 4: Stamp `card` onto `score_breakdown` at write time

**Files:**
- Modify: `backend/app/services/arena/task.py` (breakdown assembly, ~line 356)
- Test: `tests/test_arena_scoring.py` (or `test_arena_task*.py` if one exists — reuse the existing task-level test module; find via `grep -rl "score_breakdown" tests`)

**Interfaces:**
- Consumes: `scoring.ability_card(transcript, loaded, judged)`.
- Produces: `breakdown["card"] = {ovr, stats, jdg, position}` present on every scored match.

- [ ] **Step 1: Write the failing test**

Add a focused test that a scored breakdown carries a card. If there is an existing end-to-end arena-task test that builds a breakdown, extend it; otherwise add a unit test that calls the same helper the task uses:

```python
def test_scored_breakdown_carries_card(monkeypatch):
    # Minimal: assert the wiring — ability_card output is attached under "card".
    # (Full task integration is covered by the golden regression in Task 7.)
    from app.golden_workflows.registry import load_workflow_for_replay  # or the loader used by task.py
    # If a lighter fixture exists in the test module, prefer it. The essential
    # assertion is that task.py's breakdown dict includes a "card" block with an
    # int ovr and five stats. Implement against the module's existing harness.
```

Note to implementer: locate how existing arena-task tests construct `breakdown` (search `objective_breakdown(` in `tests`). Mirror that harness; the new assertion is `assert "card" in breakdown and set(breakdown["card"]["stats"]) == {"GRD","ADH","SYN","PRC","EFF"}`.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests -k "card and (task or breakdown)" -q`
Expected: FAIL (no `card` key yet).

- [ ] **Step 3: Wire the card into the breakdown**

In `backend/app/services/arena/task.py`, in the breakdown assembly (after `judged_score = judge_result.judged_score if judge_result else None`, ~line 380, so JDG is available), add:

```python
                breakdown["card"] = scoring.ability_card(
                    transcript, loaded, judged=judged_score)
```

Place it after the `judge`/`subjective_mode` block and after `judged_score` is computed, but before `store.record_match(...)`.

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests -k "card and (task or breakdown)" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/arena/task.py tests
git commit -m "feat(arena): attach ability card to score_breakdown at write time"
```

---

### Task 5: Leaderboard ranks by OVR mean; uncarded rows excluded

**Files:**
- Modify: `backend/app/services/arena/store.py` (`leaderboard`)
- Test: `tests/test_arena_store.py`

**Interfaces:**
- Consumes: `scoring.card_from_axes`, `scoring.designed_par`, `scoring.card_tiebreak_key`, `get_workflow(workflow_id)`; introduces the module-level `_derive_card(bd, workflow_id) -> (card|None, reason)` shared with Task 6.
- Produces: each leaderboard row gains `card_mean: {ovr, GRD, ADH, SYN, EFF, PRC} | None`; `rank` (shared on exact ties) now reflects OVR order. Ranking key is `(card_mean is None, -ovr, card_tiebreak, model_id)`. `mean_objective` retained. A row whose matches are all uncarded (no axes / no tool count / unloadable workflow) has `card_mean=None` and sorts after carded rows.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_arena_store.py`:

The store test fixture is `session` (NOT `db_session`, which is gateway-scoped — `tests/conftest.py:63`). Add to `tests/test_arena_store.py`:

```python
def test_leaderboard_ranks_by_ovr_and_excludes_uncarded(session):
    # Carded (hi/lo) + legacy-no-axes + axes-but-no-tool-count. Build via
    # record_match with score_breakdown carrying objective.axes + tool count.
    run_id = store.create_run(
        session, ["risk-manager-control-day"],
        ["m-hi", "m-lo", "m-legacy", "m-notool"])

    def bd(axes, tool_calls):
        return {"objective": {"axes": axes},
                "diagnosis": {"counts_detail": {"tool_calls": tool_calls}}}

    hi_axes = {"grounding": {"passed": 10, "total": 10}, "adherence": {"passed": 11, "total": 11},
               "synthesis": {"passed": 5, "total": 5}, "procedural": {"passed": 6, "total": 6}}
    lo_axes = {"grounding": {"passed": 2, "total": 10}, "adherence": {"passed": 2, "total": 11},
               "synthesis": {"passed": 1, "total": 5}, "procedural": {"passed": 6, "total": 6}}
    common = dict(judged_score=None, judge_missing=False, config={}, transcript_path=None,
                  status="scored")
    store.record_match(session, run_id, "risk-manager-control-day", "m-hi",
                       objective_score=90.0, total_score=90.0,
                       score_breakdown=bd(hi_axes, 11), **common)
    store.record_match(session, run_id, "risk-manager-control-day", "m-lo",
                       objective_score=20.0, total_score=20.0,
                       score_breakdown=bd(lo_axes, 11), **common)
    # legacy: NO objective.axes at all (runs #1-#9 shape)
    store.record_match(session, run_id, "risk-manager-control-day", "m-legacy",
                       objective_score=50.0, total_score=50.0,
                       score_breakdown={"objective_score": 50.0}, **common)
    # axes present but NO tool count → cannot compute EFF honestly → uncarded
    store.record_match(session, run_id, "risk-manager-control-day", "m-notool",
                       objective_score=60.0, total_score=60.0,
                       score_breakdown={"objective": {"axes": hi_axes}}, **common)
    store.set_run_status(session, run_id, "completed")

    rows = store.leaderboard(session, run_id=run_id)
    by_model = {r["model_id"]: r for r in rows}
    assert by_model["m-hi"]["card_mean"]["ovr"] > by_model["m-lo"]["card_mean"]["ovr"]
    assert by_model["m-hi"]["rank"] == 1
    # uncarded rows: listed, card_mean None, keep mean_objective, sort after carded
    assert by_model["m-legacy"]["card_mean"] is None
    assert by_model["m-legacy"]["mean_objective"] == 50.0
    assert by_model["m-notool"]["card_mean"] is None   # axes but no tool count
    assert by_model["m-hi"]["rank"] < by_model["m-legacy"]["rank"]
    assert by_model["m-hi"]["rank"] < by_model["m-notool"]["rank"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k ovr -q`
Expected: FAIL — `KeyError: 'card_mean'`.

- [ ] **Step 3: Add the shared `_derive_card` guard (fail-honest, not fail-inflated)**

This single helper is the ONLY place a stored breakdown becomes a card — used by both `leaderboard` (Task 5) and `_match_to_dict` (Task 6), so the drilldown and the board never disagree. It refuses to card unless it has BOTH a loadable workflow (for `par`) AND an explicit numeric tool count — otherwise a schema-drifted or count-less row would be carded with a fabricated perfect efficiency ratio (Codex finding). Add near the top of `backend/app/services/arena/store.py` (module level, after imports):

```python
def _derive_card(bd: dict, workflow_id: str) -> tuple[dict | None, str | None]:
    """Derive an ability card from a stored score_breakdown, or (None, reason).

    Fail-honest: requires non-empty objective.axes, an explicit numeric
    diagnosis.counts_detail.tool_calls, and a loadable workflow (for par). Any
    missing → uncarded with a machine reason, never a fabricated card."""
    from app.services.arena import scoring
    axes = (bd.get("objective") or {}).get("axes") or {}
    if not axes:
        return None, "legacy_no_axes"
    tc = ((bd.get("diagnosis") or {}).get("counts_detail") or {}).get("tool_calls")
    if not isinstance(tc, (int, float)) or isinstance(tc, bool):
        return None, "missing_tool_count"
    try:
        from app.golden_workflows.registry import get_workflow
        par = scoring.designed_par(get_workflow(workflow_id))
    except Exception:
        return None, "workflow_unavailable"
    judged = (bd.get("judge") or {}).get("judged_score")
    return scoring.card_from_axes(axes, int(tc), par, judged=judged), None
```

- [ ] **Step 4: Aggregate per-match OVR in `leaderboard` via `_derive_card`**

Near the other accumulators in `leaderboard` (~line 202) add:

```python
    model_ovrs: dict[str, list[int]] = defaultdict(list)
    model_stat_lists: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(list))
```

Inside the per-match loop, after the existing `axes` accumulation block (~line 244), add:

```python
        # Ability card, via the shared fail-honest guard. Uncarded rows (no axes /
        # no tool count / unloadable workflow) contribute NOTHING to card_mean.
        card, _reason = _derive_card(bd, m.workflow_id)
        if card is not None:
            model_ovrs[m.model_id].append(card["ovr"])
            for stat, val in card["stats"].items():
                model_stat_lists[m.model_id][stat].append(val)
```

- [ ] **Step 6: Build `card_mean` per row + re-rank**

`leaderboard` already has `from app.services.arena import scoring` in scope (~line 196). Replace the row-building + sort block (~lines 256-291). In the `rows.append({...})` dict add:

```python
            "card_mean": (
                {"ovr": round(sum(model_ovrs[model_id]) / len(model_ovrs[model_id])),
                 **{stat: round(sum(vals) / len(vals))
                    for stat, vals in model_stat_lists[model_id].items()}}
                if model_ovrs.get(model_id) else None),
```

And compute a card tie-break from the mean stats for the sort. After building `rows`, replace the sort + rank loop with:

```python
    def _card_tb(r):
        cm = r["card_mean"]
        return scoring.card_tiebreak_key(cm) if cm else tuple()

    # Rank by OVR mean (carded first, None last), then card stat-priority
    # tie-break, then model_id (display stabilizer). Shared rank on exact ties.
    rows.sort(key=lambda r: (
        r["card_mean"] is None,
        -((r["card_mean"] or {}).get("ovr") or 0),
        _card_tb(r),
        r["model_id"],
    ))
    rank = 0
    prev_key: object = object()
    for i, r in enumerate(rows):
        cm = r["card_mean"]
        key = (cm is None, (cm or {}).get("ovr"), _card_tb(r))
        if key != prev_key:
            rank = i + 1
            prev_key = key
        r["rank"] = rank
        r.pop("_tiebreak", None)
    return rows
```

Drop the old objective `_tiebreak` key from the row dict (it was only a sort input) — the objective mean and axes still feed `mean_objective`; only the *ranking key* changes to OVR. Remove the now-dead `objective_tiebreak_key` accumulation if nothing else uses it (keep the function; other callers/tests may reference it). Update the `leaderboard` docstring to say ranking is by `card_mean.ovr` with objective mean retained.

- [ ] **Step 7: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -q`
Expected: PASS (new test + all existing store tests; fix any existing test that asserted objective-mean ordering if OVR now reorders — verify each such change is legitimate, not masking a regression).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/arena/store.py tests/test_arena_store.py
git commit -m "feat(arena): leaderboard ranks by OVR mean; uncarded rows excluded (fail-honest _derive_card)"
```

---

### Task 6: Derive-on-read match card + surface OVR/`card_mean` on the leaderboard API

Two read-path surfaces must be consistent with the leaderboard: (a) an **existing** persisted row with `objective.axes` but no stored `card` (runs #10–#11) must show a card in the match drilldown — the no-migration promise — not just in the aggregate; (b) the leaderboard API must expose OVR. Both reuse `_derive_card` (Task 5) so the drilldown and the board can never disagree.

**Files:**
- Modify: `backend/app/services/arena/store.py` (`_match_to_dict` — inject derived card)
- Modify: `backend/app/routers/arena.py` (`get_leaderboard` row shape)
- Test: `tests/test_arena_store.py` (match serialization), `tests/test_arena_api.py` (leaderboard OVR)

**Interfaces:**
- Produces: `_match_to_dict` returns `score_breakdown` with a `card` block synthesized on read when absent — a full card when derivable, else `{"card": None, "card_reason": <reason>}` semantics via a `card` key set to `None` plus a sibling `card_reason`. Leaderboard rows gain `ovr: number | null` and `card_mean: {...} | null`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_arena_store.py` — an existing axes-only row gets a card on read:

```python
def test_match_serialization_derives_card_on_read(session):
    run_id = store.create_run(session, ["risk-manager-control-day"], ["m1"])
    axes = {"grounding": {"passed": 8, "total": 10}, "adherence": {"passed": 9, "total": 11},
            "synthesis": {"passed": 4, "total": 5}, "procedural": {"passed": 5, "total": 6}}
    bd = {"objective": {"axes": axes}, "diagnosis": {"counts_detail": {"tool_calls": 11}}}
    # NOTE: no "card" key stored — mimics runs #10-#11 persisted before this feature
    store.record_match(session, run_id, "risk-manager-control-day", "m1",
                       objective_score=80.0, judged_score=None, total_score=80.0,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown=bd)
    detail = store.get_run(session, run_id)
    card = detail["matches"][0]["score_breakdown"]["card"]
    assert card is not None and set(card["stats"]) == {"GRD","ADH","SYN","PRC","EFF"}

def test_match_serialization_uncarded_row_carries_reason(session):
    run_id = store.create_run(session, ["risk-manager-control-day"], ["m2"])
    store.record_match(session, run_id, "risk-manager-control-day", "m2",
                       objective_score=50.0, judged_score=None, total_score=50.0,
                       judge_missing=False, config={}, transcript_path=None,
                       status="scored", score_breakdown={"objective_score": 50.0})
    detail = store.get_run(session, run_id)
    sb = detail["matches"][0]["score_breakdown"]
    assert sb["card"] is None and sb["card_reason"] == "legacy_no_axes"
```

Add to `tests/test_arena_api.py` (build a carded run mirroring Task 5's `record_match` setup, then):

```python
def test_leaderboard_exposes_ovr(client):
    # ... seed a completed run with a carded match (axes + tool_calls) ...
    resp = client.get("/api/arena/leaderboard")
    row = resp.json()["rows"][0]
    assert "ovr" in row and "card_mean" in row
    assert row["ovr"] == row["card_mean"]["ovr"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py -k "derives_card or carries_reason" tests/test_arena_api.py -k "leaderboard_exposes_ovr" -q`
Expected: FAIL — no `card` on read; `KeyError: 'ovr'`.

- [ ] **Step 3: Inject the derived card in `_match_to_dict`**

In `backend/app/services/arena/store.py`, change `_match_to_dict` (~line 299) to synthesize the card on read when a stored breakdown lacks one:

```python
def _serialized_breakdown(m: ArenaMatch) -> dict | None:
    bd = m.score_breakdown
    if bd is None:
        return None
    if "card" in bd and bd["card"] is not None:
        return bd  # already carded at write time (new rows)
    out = dict(bd)
    card, reason = _derive_card(bd, m.workflow_id)
    out["card"] = card
    if card is None:
        out["card_reason"] = reason
    return out
```

Then in `_match_to_dict` replace `"score_breakdown": m.score_breakdown,` with `"score_breakdown": _serialized_breakdown(m),`.

- [ ] **Step 4: Add OVR/`card_mean` to the router leaderboard row**

In `backend/app/routers/arena.py`, in `get_leaderboard`'s `renamed` comprehension (~line 240), add:

```python
                "ovr": (r.get("card_mean") or {}).get("ovr"),
                "card_mean": r.get("card_mean"),
```

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_arena_store.py tests/test_arena_api.py -q`
Expected: PASS (new + existing).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/arena/store.py backend/app/routers/arena.py tests/test_arena_store.py tests/test_arena_api.py
git commit -m "feat(arena): derive-on-read match card + expose OVR/card_mean on the API"
```

---

### Task 7: Flagship manifest — `par_tool_calls: 11` + fixture-truth grounding

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md` (frontmatter + 4 grounding assertions)
- Test: `tests/test_flagship_loads.py`, `tests/test_golden_workflow_regression.py` (must stay 39/39)

**Interfaces:**
- Consumes: `risk-manager-control-day.truth.json` values (573.3467058766552, 16.403033928381223, 391.1919745962153, -7758.989817924667).
- Produces: manifest with `par_tool_calls: 11` and steps 3/5/6 grounding via `response_quotes_value`.

- [ ] **Step 1: Write/extend the failing test**

Add to `tests/test_flagship_loads.py`:

```python
import json, pathlib
from app.golden_workflows.registry import get_workflow

def test_flagship_declares_par_11():
    wf = get_workflow("risk-manager-control-day")
    assert wf.par_tool_calls == 11

def test_flagship_grounding_targets_match_truth_file():
    wf = get_workflow("risk-manager-control-day")
    truth = json.loads(pathlib.Path(
        "backend/app/golden_workflows/definitions/risk-manager-control-day.truth.json"
    ).read_text())
    want = {t["value"] for t in truth.values()}
    got = set()
    for step in wf.steps:
        for a in step.assertions:
            if a.type == "response_quotes_value":
                got.add(a.value)
    # every quoted grounding value is a harvested truth number
    assert got and got <= want
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py -k "par_11 or truth_file" -q`
Expected: FAIL — `par_tool_calls` is None; no `response_quotes_value` assertions yet.

- [ ] **Step 3: Add `par_tool_calls` to frontmatter**

In `risk-manager-control-day.md`, after `trap_absent_sets:` (line ~15):

```yaml
# Designed complete-run tool-call count (EFF ability stat). Equals the
# expected_tools sum across steps; declared explicitly for self-documentation.
par_tool_calls: 11
```

- [ ] **Step 4: Convert step 3 grounding to fixture truth**

Replace step 3's `response_quotes_tool_value` block (lines ~54-57):

```yaml
      - type: response_quotes_value
        value: 573.3467058766552
        near: ["delta"]
```

- [ ] **Step 5: Convert step 5 grounding (two values)**

Replace the two `response_quotes_tool_value` blocks (lines ~81-90):

```yaml
      - type: response_quotes_value
        value: 16.403033928381223
        near: ["gamma"]
      - type: response_quotes_value
        value: 391.1919745962153
        near: ["delta"]
```

(The `tool_not_called: run_greeks_landscape` assertion below them stays unchanged.)

- [ ] **Step 6: Convert step 6 CVaR grounding (magnitude)**

Replace step 6's `response_quotes_tool_value` block (lines ~125-129):

```yaml
      - type: response_quotes_value
        value: -7758.989817924667
        match: magnitude
        near: ["cvar", "expected shortfall", "loss"]
```

(The `tool_result_path` CVaR `lte: 0` check above it stays — it still verifies the computed payload sign.)

- [ ] **Step 7: Run the full flagship + regression suite**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_golden_workflow_regression.py tests/test_arena_scoring.py -q`
Expected: PASS. The replay transcript (reconciled to truth in Spec A) must earn **39/39**. If a grounding check now fails on replay, confirm the replay prose quotes the truth number (it should from Spec A); do NOT weaken the assertion.

- [ ] **Step 8: Commit**

```bash
git add backend/app/golden_workflows/definitions/risk-manager-control-day.md tests/test_flagship_loads.py
git commit -m "feat(arena): flagship par_tool_calls=11 + fixture-truth grounding assertions"
```

---

### Task 8: Frontend ability-card render

**Files:**
- Modify: `frontend/src/lib/arenaApi.ts` (types: `card` on breakdown, `ovr`/`card_mean` on row)
- Modify: `frontend/src/routes/Arena.live.tsx` (leaderboard OVR column + card in drilldown)
- Modify: `frontend/src/routes/Arena.css` (card styles — token-only)
- Test: `frontend/src/routes/Arena.live.test.tsx`

**Interfaces:**
- Consumes: `ArenaLeaderboardRow.ovr`, `ArenaLeaderboardRow.card_mean`, `ArenaScoreBreakdown.card`.

**REQUIRED READING before editing CSS:** `frontend/UI_STYLE_GUIDE.md` and the existing `Arena.css` token usage. Reuse `wl-`/BEM primitives; never hardcode a color/spacing.

- [ ] **Step 1: Write the failing test**

In `frontend/src/routes/Arena.live.test.tsx`, add a case that renders a leaderboard row with `ovr` and a match breakdown with a `card`, asserting the OVR headline and six stat labels appear, and that JDG is greyed/`—` when `jdg` is null. Mirror the module's existing render harness (mock `fetch`/api). Assertions:

```ts
expect(screen.getByText(/OVR/i)).toBeInTheDocument();
['GRD','ADH','SYN','PRC','EFF'].forEach((s) => expect(screen.getByText(s)).toBeInTheDocument());
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- Arena.live`
Expected: FAIL (no OVR/stat rendering yet).

- [ ] **Step 3: Extend the API types**

In `frontend/src/lib/arenaApi.ts`:

Add to `ArenaScoreBreakdown` (after `subjective_mode`):

```ts
  card?: {
    ovr: number;
    stats: { GRD: number; ADH: number; SYN: number; PRC: number; EFF: number };
    jdg: number | null;
    position: string;
  } | null;
```

Add to `ArenaLeaderboardRow` (after `rank`):

```ts
  ovr?: number | null;
  card_mean?: { ovr: number; GRD: number; ADH: number; SYN: number; EFF: number; PRC: number } | null;
```

- [ ] **Step 4: Add the OVR leaderboard column**

In `Arena.live.tsx`, add an `ovr` column to `leaderboardColumns` as the **headline** (before `avg_objective`, which stays as a secondary column). Render `row.ovr != null ? row.ovr : '—'`. Keep `avg_objective` labelled "Objective" as a secondary/drilldown column.

- [ ] **Step 5: Render the card in the drilldown**

In `ScoreBreakdownView`, when `breakdown.card` is present, render a compact card block above the axes: a large `OVR` number, the position badge, and a six-item stat strip (GRD/ADH/SYN/PRC/EFF + JDG). JDG shows `—` and a greyed class when `card.jdg == null`. Use existing `wl-arena__` class conventions; add new BEM elements (`wl-arena__card`, `wl-arena__card-ovr`, `wl-arena__stat`, `wl-arena__stat--jdg`) styled with tokens only. A radar/hexagon is optional polish — a labelled stat strip satisfies the spec; only add SVG if time permits and it stays token-styled.

- [ ] **Step 6: Style with tokens**

In `Arena.css`, add the new BEM rules using only `var(--...)` tokens (colors, spacing, radius, font). Grey JDG via an existing muted-text token. No `[data-theme]` branches.

- [ ] **Step 7: Verify tests + type-check + both themes**

Run: `cd frontend && npm test -- Arena.live && npx tsc --noEmit`
Expected: PASS + no type errors. Manually confirm the card reads correctly in light, dark, and compact density (per `frontend/CLAUDE.md`).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/lib/arenaApi.ts frontend/src/routes/Arena.live.tsx frontend/src/routes/Arena.css frontend/src/routes/Arena.live.test.tsx
git commit -m "feat(arena-ui): render ability card + OVR leaderboard headline"
```

---

### Task 9: Docs + full-suite gate

**Files:**
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Added`)
- Modify: `CLAUDE.md` (golden-workflows section — card subsection)
- Modify: `README.md` (only if the arena section describes scoring user-facingly)

- [ ] **Step 1: Update CHANGELOG**

Under `[Unreleased] / ### Added`, add a bullet describing the ability card (6 stats + OVR, numbers-first weights, `response_quotes_value` fixture grounding, `par_tool_calls`, OVR ranking with uncarded legacy rows).

- [ ] **Step 2: Update CLAUDE.md**

Add a `### Model Ability Card (Spec B)` subsection under "Golden workflows & arena scoring" documenting: OVR weights, the stat←axis map + EFF formula, `par_tool_calls`/`designed_par`, `response_quotes_value`, derive-on-read + uncarded legacy rows, and that JDG is advisory/out-of-OVR.

- [ ] **Step 3: Run the full backend + frontend suites**

Run: `.venv/bin/python -m pytest tests -q` and `cd frontend && npm test && npx tsc --noEmit`
Expected: all green. Pay special attention to the exact-count coupling (`test_flagship_loads`, `test_arena_scoring`, `test_golden_workflow_regression`) and the fixture-determinism gate (`test_arena_fixture_determinism.py`) — none should regress.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md CLAUDE.md README.md
git commit -m "docs(arena): document the Model Ability Card (Spec B)"
```

---

## Self-Review Notes (for the implementer)

- **Exact-count coupling:** Task 7 swaps assertion *types* 1:1, so the 39 denominator holds. If any count test hardcodes assertion-type names, update it.
- **Derive-on-read vs write-time card must agree:** the write path (`task.py`, Task 4) uses `scoring.ability_card` (fresh from transcript); the read path (`_derive_card`, Tasks 5–6) uses `scoring.card_from_axes` on the stored breakdown. Both bottom out in `card_from_axes`. The read path reads `tool_calls` from `diagnosis.counts_detail.tool_calls` — confirm task.py's stored `diagnosis.counts_detail` carries `tool_calls` (it does — `heuristic["tool_calls"]`, task.py:362), else new rows would re-derive `missing_tool_count` on read.
- **`_derive_card` is fail-honest, single-source:** it is the ONLY stored-breakdown→card path (leaderboard AND `_match_to_dict`). Reasons: `legacy_no_axes` (no axes), `missing_tool_count` (axes but no numeric tool count), `workflow_unavailable` (renamed/deleted workflow). No broad `except` that fabricates a par.
- **A stored `card` (new write-time rows) is passed through untouched** by `_serialized_breakdown`; only pre-feature rows get a card synthesized on read.
- **Rounding:** all stats/OVR use Python `round` (banker's rounding). Test literals must match `round`, not `math.floor`/`ceil`.
