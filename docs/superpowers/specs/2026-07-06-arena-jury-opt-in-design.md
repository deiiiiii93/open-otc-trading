# Arena jury opt-in — objective-only by default

**Date:** 2026-07-06
**Status:** design
**Supersedes (in part):** `2026-07-05-arena-judge-fairness-design.md` (the jury it
introduced becomes opt-in rather than always-on; the objective-only ranking it
established is unchanged and now the sole default axis).

## Problem

Arena run #11 (flagship v2, repaired manifest, 7 models) exposed that the LLM
subjective jury is too unstable to inform evaluation, even as an advisory axis:

- **Incoherent with the deterministic axis.** Claude Sonnet 5 scored 82.1 objective /
  28.7 jury; DeepSeek V4 Pro scored 74.4 objective / 81.2 jury — the jury ranked the
  pair in *reverse* of the objective axis. A model with a top objective score drew
  nearly the worst subjective score and vice-versa.
- **Infrastructure-fragile.** The jury's Opus/Qwen judges route through ZenMux. When
  ZenMux hit its 402 quota mid-run, panels lost quorum and degraded to
  `self_consistency` single-judge sampling (Opus's 8.3 was such an artifact). A score
  that swings on which judges are reachable is measuring reachability, not capability.
- **Quota cost.** The 3-model panel per trial roughly doubles the ZenMux calls of a
  run, and it was the jury calls (not the candidate calls) that pushed run #11 into
  the quota wall that capped it at n=1.

The reform already made the deterministic **objective** score the sole ranking axis
and demoted subjective to advisory. This spec finishes that direction: the jury
becomes **opt-in, default off**, so the default methodology is purely objective and
quota-light, while the jury machinery is preserved intact for anyone who deliberately
wants a subjective read.

## Decisions

- **D1 — Keep the jury code; gate it behind a flag.** `judge.py` (`judge_panel`,
  `_default_post_for`, `_canon_model`, `JudgeResult`, `_collect_rubric_points`) and all
  its config knobs (`arena_judge_models`, `arena_judge_substitutes`, `arena_min_judges`,
  `arena_self_consistency_k`) stay exactly as they are. No deletion, no behavior change
  to the jury *when it runs*.
- **D2 — New flag `arena_jury_enabled`, default `False`.** Added to **both** config
  representations in `config.py` (pydantic `Settings` with
  `validation_alias="OPEN_OTC_ARENA_JURY"`, and the dataclass mirror), matching the
  existing `agent_code_interpreter_enabled` pattern. Off by default.
- **D3 — Default scoring path computes no subjective, and says so explicitly.** In
  `task._execute`, the default (non-injected) judge runs **only** when
  `arena_jury_enabled` is true. Otherwise no judge runs: `judged_score=None`,
  `judge_missing=True`, and `score_breakdown` carries **no `judge` block** — but it
  **does** set `subjective_mode="disabled"` at the top level. This is the key
  observability distinction (D8): a deliberately jury-off row (`"disabled"`) must be
  distinguishable from a jury-on row whose judges all failed (`"missing"`), so a
  quota/dependency outage on an opt-in run is never silent. The objective axis is
  unchanged; `total_score` continues to mirror objective.
- **D8 — `subjective_mode` is the provenance channel.** Four values, ordered
  worst-visibility-wins in aggregation: `"disabled"` (jury intentionally off — no
  judge attempted), `"missing"` (jury on, **all** judges/substitutes failed —
  degraded, must stay visible), `"self_consistency"` (jury on, quorum lost → single
  judge sampled), `"panel"` (jury on, full panel). Only `"disabled"` means "no
  subjective was ever intended".
- **D4 — The `judge_fn` test seam is independent of the flag.** An explicitly injected
  `judge_fn` (test/caller intent) always runs, regardless of the flag. Precedence:
  `judge_fn` (if provided) → default jury (if `arena_jury_enabled`) → no judge. This
  keeps jury-path tests exercising the jury without turning the production default on.
- **D5 — Leaderboard is objective-only by default, subjective-tolerant and
  outage-visible.** `leaderboard` already ranks by `mean_objective` and reads
  subjective None-safely. It keeps returning `subjective_mean/stdev/mode`;
  `subjective_mean` is `None` when no jury score exists, and `subjective_mode` now
  propagates the D8 provenance via `_agg_mode` with precedence
  `missing > self_consistency > panel > disabled` — so a board with any degraded
  jury-on row aggregates to `"missing"` (visible), and only an all-`"disabled"` board
  aggregates to `"disabled"`. No ranking change.
- **D9 — Legacy rows (pre-`subjective_mode`) are inferred, never mislabeled.**
  Historical runs (#1–#10) predate `subjective_mode` — their rows have a
  `judge.judged_score` but **no** mode, and today's `_agg_mode([])` fallback of
  `"missing"` would falsely paint those successful juries as outages. `leaderboard`
  applies a **per-row mode inference** before aggregation: (1) a row with an explicit
  `subjective_mode` uses it; (2) else a row with a non-null `judged_score` infers
  `"panel"` (legacy successful jury); (3) else a row with `judge_missing` true and no
  score infers `"missing"`; (4) `"disabled"` is only ever what a new objective-only
  row wrote explicitly. Historical data is neither migrated nor deleted — it is
  interpreted correctly on read, and still surfaces on drilldown.
- **D6 — Manifest rubric stays.** The 2-point judge rubric in
  `risk-manager-control-day.md` is retained (the opt-in jury still consumes it via
  `_collect_rubric_points`). `test_flagship_loads` continues to assert 2 rubric points.
- **D7 — Frontend renders subjective conditionally, but objective detail is always
  shown.** Two rules:
  - **Drilldown (`ScoreBreakdownView`):** today it returns a compact fallback when
    `!objective || !judge`, so an objective-only row (no `judge` block) would lose its
    axes, step checks, success criteria, and diagnosis — the exact deterministic
    evidence for the sole ranking axis. The component must be decoupled: render the
    full **objective** detail whenever `objective` exists, and suppress **only** the
    subjective/jury sections when `judge` is absent. The compact fallback is reserved
    for rows with neither.
  - **Leaderboard Subjective column (board-level, not per-row):** the table column
    model is global, so visibility is a board predicate keyed on **jury intent**, not
    just presence of a mean: show the Subjective column when **any** displayed row had
    the jury intended — `subjective_mean != null || subjective_mode !== "disabled"`.
    Within a visible column: `panel`/`self_consistency` rows show the mean (± stdev);
    a `"missing"` row (jury on, all judges failed) shows an explicit degraded marker
    (e.g. `—` with a "jury failed" title) so a quota outage stays visible; a
    `"disabled"` row (in a mixed board) shows blank. Hide the column entirely only
    when **every** displayed row is `"disabled"`. This keeps mixed jury-on/jury-off
    boards (e.g. historical run #8 alongside a new objective-only run) rendering both
    kinds correctly, and never lets a failed opt-in jury silently vanish.
  Nothing about the objective column, rank, or tiebreak changes.

## Architecture

**`config.py`** — add `arena_jury_enabled: bool` (default `False`) to the pydantic
`Settings` (`validation_alias="OPEN_OTC_ARENA_JURY"`) and to the dataclass mirror.

**`services/arena/task.py::_execute`** — replace the judge-selection branch:
```
if judge_fn is not None:
    judge_result = judge_fn(transcript, loaded, post=post)
elif _cfg.arena_jury_enabled:
    judge_result = _default_judge(transcript, loaded, exclude_model=model_id)
else:
    judge_result = None
```
When `judge_result is None`: `judged_score=None`, `judge_missing=True`, the `breakdown`
dict omits the `judge` block but **sets `subjective_mode="disabled"`** (D3/D8).
`objective`, `diagnosis`, `objective_score`, `total_score` are unchanged.

**`services/arena/store.py::leaderboard` + `_agg_mode`** — extend `_agg_mode` to the
four-value precedence `missing > self_consistency > panel > disabled` (D8) so a
degraded jury-on board never collapses into a "disabled" board. Add regression tests:
(a) objective-only rows rank by objective and carry `subjective_mean=None`,
`subjective_mode="disabled"`; (b) a jury-on board where all judges failed aggregates
to `subjective_mode="missing"` (visible), distinct from (a).

**`routers/arena.py`** — response shape unchanged (subjective fields already optional;
`None` when jury off).

**Frontend (`Arena.live.tsx`, `arenaApi.ts`, `Arena.css`)** — two changes:
1. **`ScoreBreakdownView`:** change the early compact-fallback guard from
   `!objective || !judge` to `!objective` (i.e. objective drives the detailed view);
   wrap the subjective/jury (rubric + per-judge) sections in a `judge != null` guard
   so they simply don't render when absent. Objective axes/steps/success/diagnosis
   render for every row that has an `objective` block.
2. **Leaderboard table:** compute a board-level `juryIntended = rows.some(r =>
   r.subjective_mean != null || r.subjective_mode !== "disabled")`. Render the
   Subjective column header + cells only when `juryIntended`; within such a board,
   `panel`/`self_consistency` rows show the mean, `"missing"` rows show a degraded
   marker, and `"disabled"` rows show blank. Token-only styling; no new colors.

## Failure handling

- **Jury off + injected `judge_fn` (tests):** `judge_fn` still runs (D4) — existing
  jury/scoring tests pass unchanged.
- **Jury on + ZenMux 402:** unchanged from today — panel degrades to
  `self_consistency` / `judge_missing`; objective axis still scores the match. The flag
  only decides *whether* the jury is attempted, not how it degrades.
- **Mixed runs on one board:** a leaderboard spanning jury-on and jury-off rows shows
  subjective for the former and blank for the latter; ranking (objective) is identical
  either way.
- **Old rows:** run #1–#11 rows are untouched; their subjective still renders on
  drilldown. No migration.

## Testing

- `test_config`: `arena_jury_enabled` defaults `False`; `OPEN_OTC_ARENA_JURY=1`
  flips it (in a no-`.env` environment per the repo's config-test caveat).
- `test_arena_scoring` / `test_arena_api`: with the flag off and no `judge_fn`, a
  scored match has `judged_score=None`, `judge_missing=True`, **no** `judge` block, and
  `subjective_mode="disabled"` in `score_breakdown`; `total_score == objective_score`.
- With `judge_fn` injected (flag off), the judge path still runs (D4).
- New `test_arena_store` cases: (a) leaderboard over objective-only rows ranks by
  objective and returns `subjective_mean=None`, `subjective_mode="disabled"`; (b) a
  jury-on board whose judges all failed aggregates to `subjective_mode="missing"`
  (distinct from `"disabled"`) so the outage stays visible; (c) a **legacy** row
  (`judged_score` present, no `subjective_mode`) infers `"panel"` — not `"missing"` —
  and a mixed old-legacy + new-`disabled` board reports `"panel"` (D9).
- `test_arena_judge`: untouched — `judge_panel` unit behavior is unchanged.
- `test_flagship_loads`: rubric still 2 points (D6).
- Frontend `Arena.live.test.tsx`:
  - A leaderboard where **no** row has subjective data renders no Subjective column;
    one where **some** row has it renders the column (with blank cells for the
    objective-only rows) — the mixed-board case.
  - Drilldown of an **objective-only** match still renders objective axes + step
    checks + success + diagnosis (not the compact fallback), and renders **no**
    subjective/per-judge section.

## Out of scope

- Deleting the jury, its config knobs, or the manifest rubric (kept per D1/D6).
- Any DB migration or backfill of historical subjective data (kept per D5).
- Changing objective scoring, axes, the tiebreak, or infra-contamination gating.
- The `goal_*` RubricMiddleware subsystem (an unrelated "rubric" concept — untouched).
- Pairwise/Elo scoring (was already deferred).
