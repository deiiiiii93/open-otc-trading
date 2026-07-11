# Arena EFF — golf-style scoring redesign

**Date:** 2026-07-11
**Status:** Design (awaiting review)
**Scope:** The EFF (efficiency) stat of the Model Ability Card, and the flagship
workflow's declared par. No change to the 39-point objective score, the other five
card stats, JDG, CON, or any DB schema.

## Problem

EFF is computed as a correctness-gated hyperbolic ratio against **par = 11**:

```
EFF = round(c × min(1, par / tool_calls) × 99)     # c = (GRD+ADH+SYN) pass fraction
```

`par = 11` is the workflow's *theoretical minimum* — each expected tool called exactly
once (7 signature tools + 4 retrieval/library calls). But no real run comes close: the
leanest competent run in Arena Run #20 was **22 calls**, the median **33**, the mean
**39**, the max **108**. Because every run exceeds par, `min(1, 11/x)` is always well
below 1 — the *best* EFF ratio anyone achieved was 0.5. Observed EFF lands at **9–47**
while every other stat sits at **70–90**.

Consequences:
- EFF is a **uniform drag** on OVR, not a discriminator. It compresses the board
  instead of separating models.
- The hyperbolic `1/x` shape is harsh and non-linear precisely in the realistic 22–40
  range, so small differences in competent runs produce large EFF swings while huge
  differences among bloated runs (68 vs 108) barely register.

## Goal

Adopt golf's core idea: **par is what a competent expert shoots, and you score relative
to par** — not against a physical minimum. A competent lean run should score *full*
efficiency; only genuine over-execution should be penalized, linearly.

## Design

### Formula (pure linear from par, correctness-gated, 0–99)

```
if tool_calls == 0 and par > 0:      # non-execution guard (unchanged): value-only
    ratio = 0.0                      # grounding must NOT earn a free efficiency pass
elif par == 0:                       # a workflow that designs no tools (unchanged)
    ratio = 1.0
elif not par_calibrated:             # no explicit realistic par → keep TODAY's curve
    ratio = min(1.0, par / tool_calls)   # existing hyperbolic — zero behavior change
elif tool_calls <= par:              # at or under par → full efficiency
    ratio = 1.0
else:                                # each call over par is a "bogey"
    over_par = tool_calls - par
    ratio = max(0.0, 1.0 - over_par / S)

EFF = round(c × 99 × ratio)
```

- **The golf (linear) curve applies only when par is explicitly calibrated** — i.e. the
  workflow declares `par_tool_calls`. A workflow that falls back to
  `sum(expected_tools)` (the too-low theoretical minimum) keeps **today's hyperbolic
  formula unchanged**, so changing this global kernel causes **zero regression** for any
  non-flagship workflow. The linear curve is only trusted with a par an author has
  calibrated to real counted runs; otherwise `2 × a-too-low-par` would wrongly zero out
  legitimate runs. `par_calibrated = (workflow.par_tool_calls is not None)`.
- **`c` (correctness gate) is retained** — a lean run with weak grounding/adherence
  still can't earn a high EFF. Full efficiency is `c × 99`, not a flat 99.
- **Leaner than par is not penalized** (`ratio = 1`), same as today.
- **The `tool_calls == 0` non-execution guard is retained** — the linear branch alone
  would hand a zero-call transcript `ratio = 1`, so the explicit guard stays on top.

### Par = 24 (designed manifest value)

`risk-manager-control-day.md` frontmatter: `par_tool_calls: 11 → 24`.

Derived from the **workflow structure**, not the contestant field (so it doesn't drift
run-to-run and isn't circular). **Critically, par is counted against the same metric it
is compared to** — `_workflow_call_count` / `counts_detail.tool_calls`, which
**excludes** the `META_TOOLS = {task, read_file, write_todos}` set (`trace_harvest.py`)
and therefore does **not** count skill-file `read_file` loads (those are harvested
separately into `skills_routed`). So par must be built from *counted domain tool calls
only* — including skill reads in par would inflate the denominator above the measured
numerator and hand every model free efficiency credit.

| Component (counted domain tool calls only) | Count |
|---|---|
| Expected tool calls (7 signature + 4 retrieval) | 11 |
| Legitimate counted overhead — re-fetching `get_*_run` results, re-listing the scenario library, sanity re-pricing before reporting | ~13 |
| **Designed par** | **24** |

Skill-file reads are **excluded** (they aren't counted by the EFF metric). This
designed par matches the leanest *observed* competent runs — which are likewise
counted, excluding their skill reads (deepseek-pro 22/24, terra 26/27) — as a sanity
check, without being computed from them.

### Slope S = par (EFF reaches 0 at 2×par)

Encoded as a **scale-free rule**, not a standalone magic number: a module constant
`_EFF_ZERO_MULT = 2.0` sets the zero-point at `2 × par`, so `S = par`. A workflow
declares **one** number (its par) and the slope follows.

Rule in words: *"take more than twice a competent expert's calls and you get no
efficiency credit."* Per-call penalty = `99 / par` points.

This is the most golf-faithful and most discriminating setting: it makes leanness a
real competitive axis (a lean, slightly-lower-objective run can overtake a heavier,
higher-objective one) rather than a mere tie-break.

## Code changes

1. **`backend/app/services/arena/scoring.py`**
   - `card_from_axes` (~line 161): add a `par_calibrated: bool = False` parameter.
     When `par_calibrated` is `False`, keep the **existing** `ratio = min(1.0, par /
     tool_calls)` hyperbolic branch verbatim (back-compat for uncalibrated workflows).
     When `True`, use the linear decay: zero-point `zero_at = _EFF_ZERO_MULT × par`,
     span `S = zero_at − par` (with `_EFF_ZERO_MULT = 2.0`, `S = par`, EFF reaches 0 at
     `2 × par`), then `ratio = max(0.0, 1.0 − (tool_calls − par) / S)` for
     `tool_calls > par` and `1.0` for `tool_calls ≤ par`. Keep the `tool_calls == 0 /
     par == 0` guards exactly as specified in the Formula section (they precede the
     calibration branch, so a `par_calibrated=False` workflow with 0 calls still gets
     `ratio=0`).
   - Add module constant `_EFF_ZERO_MULT = 2.0` with a comment (tunable later — e.g.
     `2.5` widens the fairway — without touching any per-workflow manifest).
   - Add a `par_calibrated(workflow) -> bool` helper returning
     `getattr(workflow, "par_tool_calls", None) is not None` (single source of truth for
     the gate). `designed_par` is unchanged (explicit `par_tool_calls` wins; fallback
     stays `sum(expected_tools)`).
   - Thread `par_calibrated` through the two call sites: `ability_card` (write-time
     wrapper, ~line 286) passes `par_calibrated(loaded.workflow)`; `store._derive_card`
     passes it from the loaded workflow the same way. Both already resolve the workflow,
     so no new plumbing.

2. **`backend/app/golden_workflows/definitions/risk-manager-control-day.md`**
   - `par_tool_calls: 11 → 24`.

3. **Tests**
   - `tests/test_flagship_loads.py:101` — `assert wf.par_tool_calls == 11 → 24`.
   - `tests/test_arena_scoring.py`:
     - `test_card_eff_penalizes_bloat_not_leanness` (~line 447–452): the `(22, 11)`
       case currently asserts `EFF == 50` (old hyperbolic ratio 0.5). Under the linear
       curve with `par = 11, S = 11`, `22` calls is `par + S` → `ratio 0` → `EFF 0`.
       Rewrite this test to assert the **linear** shape explicitly, e.g. with
       `par = 20, S = 20`: `(20 → full)`, `(30 → round(c·99·0.5))`, `(40 → 0)`,
       `(10 → full, leaner)`.
     - `test_card_from_axes_stats_and_ovr` (line 438/442): uses `tool_calls == par == 11`
       → `ratio 1` → EFF unchanged (77). No change.
     - `test_card_zero_tools_par_positive_gets_zero_eff` / `_zero_par_is_full_eff` /
       `_do_nothing_scores_low_eff`: all exercise the retained guards → unchanged. Note
       these call `card_from_axes` without `par_calibrated`, so they run the default
       (hyperbolic) branch — the guards precede it, so the assertions hold. Add explicit
       `par_calibrated=True` variants only where they assert the linear shape.
     - `test_designed_par_defaults_to_expected_tools_sum`: fallback unchanged → no change.
     - **New `test_card_eff_uncalibrated_par_keeps_hyperbolic`**: with `par_calibrated=
       False`, `card_from_axes(axes, 22, 11)` still yields the old hyperbolic `EFF` (ratio
       0.5), proving a non-flagship/uncalibrated workflow is byte-for-byte unchanged.
     - **New `test_flagship_par_is_calibrated`**: `par_calibrated(flagship) is True` and a
       non-flagship workflow without `par_tool_calls` is `False`.

## Consequences

- **No migration; all carded runs re-score on read.** `card_from_axes` is the single
  kernel behind both write-time (`task.py`) and derive-on-read (`store._derive_card`),
  so runs #10–#20 re-derive their EFF/OVR on the next page load under the new formula.
  This is the existing "derive on read, never migrate" invariant working as designed.
  Expected board shift on the flagship (base OVR, run #20): lean runs' EFF jumps from
  34–41 to full (~82); **terra rises toward #1** over luna (lean beats heavier at equal
  correctness); over-executors **grok/longcat fall** below their old OVR.
- **The 39-point objective and the golden replay are unaffected.** EFF is a *derived
  card stat*, orthogonal to the objective denominator; `test_golden_workflow_regression`
  still earns 39/39.
- **CON, JDG, tie-break order, and the other four stats are untouched.**

## Out of scope

- **Calibrating other arena workflows' par.** Only the flagship gets a realistic
  designed par here. Other workflows without an explicit `par_tool_calls` are **safe**
  under this change — the calibration gate keeps them on today's hyperbolic formula, so
  their cards are byte-for-byte unchanged (no regression). Giving trader-rfq /
  high-board their own realistic `par_tool_calls` (to opt them into golf scoring) is a
  follow-up, not done here. **Golf scoring is opt-in per workflow via a calibrated par.**
- No change to how tool calls are *counted* (`counts_detail.tool_calls`), including the
  `record_answer` exemption.
