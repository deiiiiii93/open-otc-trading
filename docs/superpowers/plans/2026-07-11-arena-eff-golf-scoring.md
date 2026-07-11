# Arena EFF Golf-Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flagship's hyperbolic EFF ratio (par=11 theoretical minimum) with golf-style linear scoring against a realistic designed par (24), gated so no other workflow regresses.

**Architecture:** `card_from_axes` (the single stat/OVR kernel used by both write-time and derive-on-read) gains a `par_calibrated` flag. When a workflow declares an explicit `par_tool_calls`, EFF decays linearly from par to 0 at `2×par`; otherwise it keeps today's hyperbolic formula byte-for-byte. Only the flagship declares a calibrated par (11→24), so it alone opts into golf scoring — and runs #10–#20 re-score on read with no migration.

**Tech Stack:** Python 3.11, pytest. Pure-function scoring (`backend/app/services/arena/scoring.py`), YAML workflow manifest, SQLite (read-only for verification).

## Global Constraints

- **The 39-point objective denominator is unchanged.** EFF is a derived card stat, orthogonal to the objective score; `test_golden_workflow_regression` must still earn 39/39.
- **No DB migration.** `card_from_axes` is the single kernel behind write-time (`task.py`) and derive-on-read (`store._derive_card`); the change re-scores stored runs on read.
- **Zero behavior change for uncalibrated workflows.** Any `card_from_axes` call without `par_calibrated=True` must produce the exact EFF it does today (hyperbolic `min(1, par/tool_calls)`).
- **par is counted-only.** par is compared against `_workflow_call_count` / `counts_detail.tool_calls`, which excludes `META_TOOLS = {task, read_file, write_todos}` (`trace_harvest.py`). Never include skill-file reads in a designed par.
- **Slope is scale-free:** `_EFF_ZERO_MULT = 2.0` → EFF reaches 0 at `2×par`. A workflow declares only its par.
- Run tests with `.venv/bin/python -m pytest`.

---

### Task 1: Calibration gate + linear EFF in `card_from_axes`

**Files:**
- Modify: `backend/app/services/arena/scoring.py` (add constant + helper ~line 117; rewrite EFF branch in `card_from_axes` ~lines 144–167)
- Test: `tests/test_arena_scoring.py` (add after `test_card_eff_penalizes_bloat_not_leanness`, ~line 452)

**Interfaces:**
- Produces: `scoring._EFF_ZERO_MULT: float = 2.0`; `scoring.par_calibrated(workflow) -> bool`; `scoring.card_from_axes(axes, tool_calls, par, judged=None, par_calibrated=False) -> dict` (new trailing kwarg, default `False`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_arena_scoring.py` (near the other card tests):

```python
def test_par_calibrated_helper():
    from app.golden_workflows.schema import GoldenWorkflow
    wf = _get_wf("risk-manager-control-day")  # declares par_tool_calls → calibrated
    assert scoring.par_calibrated(wf) is True
    data = wf.model_dump()
    data["par_tool_calls"] = None             # strip → falls back to sum(expected_tools)
    assert scoring.par_calibrated(GoldenWorkflow(**data)) is False


def test_card_eff_uncalibrated_keeps_hyperbolic():
    # A workflow without a calibrated par keeps TODAY's hyperbolic EFF exactly.
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    # default par_calibrated=False → ratio = min(1, par/tool_calls)
    assert scoring.card_from_axes(axes, 22, 11)["stats"]["EFF"] == 50   # 11/22
    assert scoring.card_from_axes(axes, 11, 11)["stats"]["EFF"] == 99
    assert scoring.card_from_axes(axes, 5, 11)["stats"]["EFF"] == 99    # leaner not penalized


def test_card_eff_linear_when_calibrated():
    # c = 1.0 (all correctness axes full) so EFF == round(99 * ratio).
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    f = lambda tc, par: scoring.card_from_axes(axes, tc, par, par_calibrated=True)["stats"]["EFF"]
    assert f(24, 24) == 99          # at par → full
    assert f(20, 24) == 99          # under par → full (leaner not penalized)
    assert f(36, 24) == 50          # +12 over par of 24, span 24 → 1-0.5 → round(49.5)=50
    assert f(48, 24) == 0           # 2×par → 0
    assert f(60, 24) == 0           # beyond 2×par → floored at 0


def test_card_eff_calibrated_guards_unchanged():
    # The non-execution / zero-par guards precede the calibration branch.
    axes = {"grounding": {"passed": 9, "total": 10}, "adherence": {"passed": 8, "total": 10},
            "synthesis": {"passed": 2, "total": 3}, "procedural": {"passed": 0, "total": 6}}
    assert scoring.card_from_axes(axes, 0, 24, par_calibrated=True)["stats"]["EFF"] == 0
    full = {"grounding": {"passed": 2, "total": 2}, "adherence": {"passed": 2, "total": 2},
            "synthesis": {"passed": 1, "total": 1}, "procedural": {"passed": 1, "total": 1}}
    assert scoring.card_from_axes(full, 0, 0, par_calibrated=True)["stats"]["EFF"] == 99
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py::test_card_eff_linear_when_calibrated tests/test_arena_scoring.py::test_par_calibrated_helper -v`
Expected: FAIL — `card_from_axes() got an unexpected keyword argument 'par_calibrated'` and `module has no attribute 'par_calibrated'`.

- [ ] **Step 3: Add the constant and helper**

In `backend/app/services/arena/scoring.py`, after the `_CARD_TIEBREAK_PRIORITY` line (~117), add:

```python
# Golf-style EFF (spec 2026-07-11): EFF reaches 0 at _EFF_ZERO_MULT × par. Scale-free —
# a workflow declares only its par and the slope follows. Tunable (2.5 widens the fairway)
# without touching any manifest.
_EFF_ZERO_MULT = 2.0


def par_calibrated(workflow) -> bool:
    """True when the workflow declares an explicit realistic par (opts into golf EFF).

    Uncalibrated workflows fall back to designed_par = sum(expected_tools) — a too-low
    theoretical minimum — and KEEP the legacy hyperbolic EFF, so changing the shared
    card_from_axes kernel can never regress a workflow that hasn't calibrated its par.
    """
    return getattr(workflow, "par_tool_calls", None) is not None
```

- [ ] **Step 4: Rewrite the EFF ratio branch**

In `card_from_axes`, change the signature and the ratio block. Replace lines ~144–167 so the signature gains `par_calibrated: bool = False` and the ratio computation becomes:

```python
def card_from_axes(axes: dict, tool_calls: int, par: int,
                   judged: float | None = None, par_calibrated: bool = False) -> dict:
    """Derive the ability card from objective axis tallies + tool-call count.

    Pure. Used at write time (task.py) and derive-on-read (store.py) — the single
    stat/OVR formula, so the drilldown card and the leaderboard OVR never disagree.
    """
    stats = {stat: 0 for stat in ("GRD", "ADH", "SYN", "PRC")}
    for axis, stat in _STAT_BY_AXIS.items():
        stats[stat] = _stat_from_tally(axes.get(axis, {}))
    c = _correctness(axes)
    # Efficiency ratio. Guards first (unchanged): ZERO calls with par>0 is non-execution
    # (ratio 0 — value-only grounding must not earn a free EFF pass); par==0 (no tools
    # designed) is legitimately full efficiency. Then, only when par is CALIBRATED, score
    # golf-style: full at/under par, linear decay to 0 at _EFF_ZERO_MULT × par. An
    # UNCALIBRATED par (theoretical-min fallback) keeps the legacy hyperbolic ratio, so
    # this shared kernel never regresses a workflow that hasn't set a realistic par.
    if tool_calls == 0 and par > 0:
        ratio = 0.0
    elif par == 0:
        ratio = 1.0
    elif not par_calibrated:
        ratio = min(1.0, par / tool_calls)
    elif tool_calls <= par:
        ratio = 1.0
    else:
        span = (_EFF_ZERO_MULT - 1.0) * par        # zero at _EFF_ZERO_MULT × par
        ratio = max(0.0, 1.0 - (tool_calls - par) / span)
    stats["EFF"] = round(c * ratio * 99)
    ovr = round(sum(_OVR_WEIGHTS[k] * stats[k] for k in _OVR_WEIGHTS))
    return {"ovr": ovr, "stats": stats, "jdg": judged,
            "position": _card_position(stats)}
```

- [ ] **Step 5: Run the new + existing card tests**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py -k "card or par" -v`
Expected: PASS — the four new tests pass; every pre-existing `test_card_*` still passes (they call `card_from_axes` without `par_calibrated`, hitting the unchanged hyperbolic/guard branches).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/arena/scoring.py tests/test_arena_scoring.py
git commit -m "feat(arena): golf-style linear EFF gated behind a calibrated par"
```

> **Task 1 is runtime-safe on its own.** `par_calibrated` defaults to `False` and no
> call site passes it yet, so `_derive_card` / `ability_card` still call `card_from_axes`
> without the flag → the flagship (even though it *declares* `par_tool_calls`) keeps the
> **hyperbolic** curve. The board is byte-for-byte unchanged after this commit.

---

### Task 2: Turn on golf scoring for the flagship — ATOMIC (par 11→24 + thread `par_calibrated` + coupled tests)

> **Why atomic (Codex plan review [high]):** the flagship *already* declares
> `par_tool_calls: 11`, so `par_calibrated(flagship)` is `True`. If the call-site
> threading were committed *before* the par bump, the flagship would derive-on-read with
> the **linear** curve at **par=11** (span 11 → EFF zeros at 22 calls), silently
> publishing distorted OVRs on the live leaderboard with no migration signal. The par
> change (11→24) and the `par_calibrated` threading MUST land in ONE commit, and every
> committed state must be green.

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.md` (frontmatter `par_tool_calls: 11 → 24`)
- Modify: `backend/app/services/arena/scoring.py` (`ability_card`, ~line 286)
- Modify: `backend/app/services/arena/store.py` (`_derive_card`, ~lines 30–36)
- Modify: `tests/test_flagship_loads.py` (~line 99–101: `test_flagship_declares_par_11`)
- Modify: `tests/test_arena_scoring.py` (`test_designed_par_defaults_to_expected_tools_sum`, ~line 416; add two tests)

**Interfaces:**
- Consumes: `scoring.par_calibrated`, `scoring.card_from_axes(..., par_calibrated=...)` from Task 1.
- Produces: `designed_par(flagship) == 24`; `par_calibrated(flagship) is True`; the flagship's card (write-time AND derive-on-read) uses the linear curve.

- [ ] **Step 1: Write/adjust the tests (they will only pass once ALL edits below land)**

In `tests/test_flagship_loads.py`, rename `test_flagship_declares_par_11` → `test_flagship_declares_par_24` and set line 101:

```python
    assert wf.par_tool_calls == 24
```

In `tests/test_arena_scoring.py`, rewrite the fallback test (the flagship now carries an explicit non-sum par) and add the calibration + derive-card-linear tests:

```python
def test_designed_par_defaults_to_expected_tools_sum():
    # The fallback (no explicit par_tool_calls) is sum(expected_tools). Strip the
    # flagship's explicit par to exercise it — the flagship itself now declares 24.
    from app.golden_workflows.schema import GoldenWorkflow
    wf = _get_wf("risk-manager-control-day")
    data = wf.model_dump()
    data["par_tool_calls"] = None
    stripped = GoldenWorkflow(**data)
    assert scoring.designed_par(stripped) == sum(len(s.expected_tools) for s in wf.steps)
    assert scoring.designed_par(stripped) == 11


def test_flagship_par_is_calibrated_24():
    wf = _get_wf("risk-manager-control-day")
    assert wf.par_tool_calls == 24
    assert scoring.designed_par(wf) == 24
    assert scoring.par_calibrated(wf) is True


def test_derive_card_uses_linear_for_calibrated_flagship():
    # A stored breakdown for the flagship (calibrated par=24) derives a LINEAR EFF on
    # read, not the hyperbolic one. c=1.0 → EFF == round(99 * linear_ratio).
    from app.services.arena.store import _derive_card
    axes = {"grounding": {"passed": 4, "total": 4}, "adherence": {"passed": 4, "total": 4},
            "synthesis": {"passed": 2, "total": 2}, "procedural": {"passed": 4, "total": 4}}
    bd = {"objective": {"axes": axes},
          "diagnosis": {"counts_detail": {"tool_calls": 36}}}
    card, reason = _derive_card(bd, "risk-manager-control-day")
    assert reason is None
    # 36 calls, par 24, span 24 → 1-(12/24)=0.5 → EFF 50 (linear).
    # (Hyperbolic would be round(99*24/36)=66 — proving the gate is on.)
    assert card["stats"]["EFF"] == 50
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_arena_scoring.py -k "par or derive_card_uses_linear" -v`
Expected: FAIL — flagship still `par_tool_calls: 11`; derive-card test sees par 11 (linear span 11 → 36 calls floored to EFF 0, not 50).

- [ ] **Step 3: Set the flagship par to 24**

In `backend/app/golden_workflows/definitions/risk-manager-control-day.md` frontmatter (line 18):

```yaml
par_tool_calls: 24
```

- [ ] **Step 4: Thread `par_calibrated` in `ability_card` (write-time)**

In `backend/app/services/arena/scoring.py`:

```python
def ability_card(transcript, loaded, judged: float | None = None) -> dict:
    """Convenience wrapper: evaluate the transcript once and build the card."""
    bd = objective_breakdown(transcript, loaded)
    heuristic = diagnose_heuristic(transcript, loaded)
    return card_from_axes(bd["axes"], heuristic["tool_calls"],
                          designed_par(loaded.workflow), judged=judged,
                          par_calibrated=par_calibrated(loaded.workflow))
```

- [ ] **Step 5: Thread `par_calibrated` in `store._derive_card` (derive-on-read)**

In `backend/app/services/arena/store.py`, resolve the workflow once and pass the flag:

```python
    try:
        from app.golden_workflows.registry import get_workflow
        wf = get_workflow(workflow_id)
        par = scoring.designed_par(wf)
    except Exception:
        return None, "workflow_unavailable"
    judged = (bd.get("judge") or {}).get("judged_score")
    return scoring.card_from_axes(axes, int(tc), par, judged=judged,
                                  par_calibrated=scoring.par_calibrated(wf)), None
```

- [ ] **Step 6: Run the full par/card/derive slice — everything green**

Run: `.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_arena_scoring.py -k "par or card or derive" -v`
Expected: PASS — all four new/adjusted tests green; every pre-existing `test_card_*` still passes.

- [ ] **Step 7: Commit (ONE atomic, green commit)**

```bash
git add backend/app/golden_workflows/definitions/risk-manager-control-day.md \
        backend/app/services/arena/scoring.py backend/app/services/arena/store.py \
        tests/test_flagship_loads.py tests/test_arena_scoring.py
git commit -m "feat(arena): enable golf EFF for flagship (par 11->24 + thread par_calibrated, atomic)"
```

---

### Task 3: Full regression, board re-score verification, and docs

**Files:**
- Modify: `CHANGELOG.md` (under `[Unreleased] ### Changed`)
- Modify: `CLAUDE.md` (Model Ability Card section — EFF note)
- Verify only (no edit): run #20 leaderboard re-derives

**Interfaces:**
- Consumes: everything from Tasks 1–2.

- [ ] **Step 1: Run the full arena + golden regression suite**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py tests/test_flagship_loads.py tests/test_golden_workflow_regression.py tests/test_arena_fixture_determinism.py -v`
Expected: PASS — critically `test_golden_workflow_regression` still earns 39/39 (EFF is orthogonal to the objective denominator).

- [ ] **Step 2: Run the broader suite to catch any other coupling**

Run: `.venv/bin/python -m pytest tests/ -k "arena or flagship or golden or card" -q`
Expected: PASS. If any test asserts a specific flagship EFF/OVR number, update it to the new linear value (there should be none beyond those handled in Tasks 1–2).

- [ ] **Step 3: Verify the run #20 board re-scores on read (no migration; never linear-at-par-11)**

Run:
```bash
.venv/bin/python - <<'PY'
import sys; sys.path.insert(0, "backend")
from app.database import SessionLocal
from app.services.arena import store
with SessionLocal() as s:
    lb = store.leaderboard(s, run_id=20)
rows = lb if isinstance(lb, list) else lb.get("rows") or []
for r in rows[:4]:
    c = r.get("card") or {}
    print(r.get("rank"), r["model_id"], "OVR", c.get("ovr"), "EFF", c.get("eff"))
PY
```
Expected: lean runs now carry a much higher EFF (~70–82 vs the old 34–41); terra rises toward #1 over luna. This confirms the board derived through the LINEAR curve at **par=24** (not the broken par=11 intermediate Codex flagged) — and it is derive-on-read, so no DB write occurred.

- [ ] **Step 4: Update CHANGELOG**

Add under `[Unreleased] ### Changed`:

```markdown
- **Arena EFF is now golf-scored against a realistic, calibrated par.** The efficiency
  stat previously divided by par=11 (the flagship's theoretical minimum — each expected
  tool called once), so every real run (22–108 calls) scored a hyperbolic 9–47 while
  other stats sat at 70–90; EFF was a uniform drag, not a discriminator. It now decays
  **linearly** from a designed par (flagship 11→24, a competent counted run) to 0 at
  `2×par`. Lean runs earn full EFF; only genuine over-execution is penalized. Gated
  behind an explicit `par_tool_calls`: workflows without a calibrated par keep the old
  hyperbolic formula unchanged (no regression). Derive-on-read re-scores runs #10–#20
  with no migration; the 39-point objective and golden replay are unaffected.
```

- [ ] **Step 5: Update CLAUDE.md**

In the **Model Ability Card** section, replace the `EFF = round(C × min(1, par/actual_calls) × 99)` description with:

```markdown
`EFF` is golf-scored against a **calibrated** par: full at/under par, then linear decay
to 0 at `2×par` (`_EFF_ZERO_MULT`), still gated by the correctness fraction `C`
(GRD+ADH+SYN). Only workflows that declare an explicit `par_tool_calls` opt into this
(`scoring.par_calibrated`); others keep the legacy hyperbolic `min(1, par/actual)` so the
shared `card_from_axes` kernel never regresses an uncalibrated workflow. The flagship par
is a **realistic counted run (24)**, not the theoretical minimum — and par is counted
against the same metric as `counts_detail.tool_calls`, which excludes skill-file reads
(`META_TOOLS`), so a designed par must never include them.
```

- [ ] **Step 6: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs: changelog + CLAUDE.md for golf-style EFF scoring"
```

