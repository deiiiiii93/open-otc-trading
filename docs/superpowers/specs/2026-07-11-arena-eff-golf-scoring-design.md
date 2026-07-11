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
elif tool_calls <= par:              # at or under par → full efficiency
    ratio = 1.0
else:                                # each call over par is a "bogey"
    over_par = tool_calls - par
    ratio = max(0.0, 1.0 - over_par / S)

EFF = round(c × 99 × ratio)
```

- **`c` (correctness gate) is retained** — a lean run with weak grounding/adherence
  still can't earn a high EFF. Full efficiency is `c × 99`, not a flat 99.
- **Leaner than par is not penalized** (`ratio = 1`), same as today.
- **The `tool_calls == 0` non-execution guard is retained** — the linear branch alone
  would hand a zero-call transcript `ratio = 1`, so the explicit guard stays on top.

### Par = 24 (designed manifest value)

`risk-manager-control-day.md` frontmatter: `par_tool_calls: 11 → 24`.

Derived from the **workflow structure**, not the contestant field (so it doesn't drift
run-to-run and isn't circular):

| Component | Count |
|---|---|
| Expected tool calls (7 signature + 4 retrieval) | 11 |
| Skill-file reads (6 steps declare a skill → one `read_file` each) | 6 |
| Legitimate result inspections / re-checks | ~7 |
| **Designed par** | **24** |

This matches the leanest *observed* competent runs (deepseek-pro 22/24, terra 26/27)
as a sanity check, without being computed from them.

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
   - `card_from_axes` (~line 161): replace the `ratio = min(1.0, par / tool_calls)`
     branch with the linear decay. Define the zero-point as `zero_at = _EFF_ZERO_MULT
     × par` and the span as `S = zero_at − par` (with `_EFF_ZERO_MULT = 2.0` this gives
     `S = par`, i.e. EFF reaches 0 at `2 × par`). Then
     `ratio = max(0.0, 1.0 − (tool_calls − par) / S)` for `tool_calls > par`.
     Keep the `tool_calls == 0 / par == 0 / tool_calls <= par` branches exactly as
     specified in the Formula section.
   - Add module constant `_EFF_ZERO_MULT = 2.0` with a comment (tunable later — e.g.
     `2.5` widens the fairway — without touching any per-workflow manifest).
   - `designed_par` is unchanged (explicit `par_tool_calls` wins; fallback stays
     `sum(expected_tools)`).

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
       `_do_nothing_scores_low_eff`: all exercise the retained guards → unchanged.
     - `test_designed_par_defaults_to_expected_tools_sum`: fallback unchanged → no change.

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

- **Other arena workflows' par.** Only the flagship has real arena runs and a realistic
  designed par. Workflows without an explicit `par_tool_calls` still fall back to
  `sum(expected_tools)` (theoretical minimum), which under the *linear* curve would
  over-penalize them (their EFF would zero out at `2 × a-too-low-par`). Each such
  workflow should declare a realistic `par_tool_calls` before its EFF is trustworthy —
  tracked as a follow-up, not done here.
- No change to how tool calls are *counted* (`counts_detail.tool_calls`), including the
  `record_answer` exemption.
