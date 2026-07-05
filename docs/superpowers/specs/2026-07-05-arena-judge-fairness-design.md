# Arena judge fairness & scoring-methodology reform

**Date:** 2026-07-05
**Status:** Design approved — open questions resolved via Codex consult (2026-07-05), user-approved. Pending explicit implementation go-ahead.
**Scope:** `backend/app/golden_workflows/` (flagship definition + fixtures, schema if needed),
`backend/app/services/arena/` (`judge.py`, `scoring.py`, `models.py`, `store.py`, `task.py`,
`runner.py`), Arena frontend (`Arena.live.tsx`, `arenaApi.ts`, `Arena.css`), tests, docs.

## 1. Problem

The LLM judge (`openai/gpt-5.5`, single call, `temperature=0`, `reasoning_effort=high`,
mean of six 0–100 rubric points) drives **50% of every model's total**, yet run #10
(8 models × 3 trials, 23 judge-complete trials in the live DB) shows it is the dominant
source of noise and unfairness — not a second, independent quality dimension.

**Rubric decomposition across all 23 trials** (score = mean / min / max / stdev):

| Rubric point | mean | range | stdev | reality |
|---|---|---|---|---|
| Trap handling | **0.0** | 0–0 | 0.0 | **Dead** — 0 for every model (the liquidity-crunch trap-set exists in the env, so every competent model "fails" by correctly running it) |
| Instruction adherence | 90.7 | 50–100 | 18.9 | Near-ceiling; already the deterministic `args_any_of` objective check |
| Process | 81.1 | 0–100 | 25.5 | Near-ceiling mean; already the deterministic `tools_routed_sequence` check |
| Staleness judgment | 59.3 | 0–100 | 26.3 | Already the deterministic `response_contains` stale-terms check |
| Numeric grounding | 67.6 | 50–100 | 18.6 | Already the deterministic `response_quotes_tool_value` check |
| Report synthesis | 55.0 | 0–100 | 32.0 | **Genuinely subjective** — the only point that needs an LLM |

**Five of six rubric points are dead or re-grade — noisily — a quality the deterministic
objective checker already scores correctly.** Each such point has an existing objective twin
in the manifest (`response_contains`, `response_quotes_tool_value`, `tool_not_called`,
`args_any_of`, `tools_routed_sequence`). The judge is not adding a dimension; it is adding a
biased, high-variance copy of the objective score and blending it in at 50%.

**Downstream symptoms (run #10):**

- **Judge is the dominant variance source.** Same model, same workflow: DeepSeek Flash judge
  scores across three trials were 70.8 / 43.3 / 59.2 (range **27.5**). "Report synthesis"
  stdev = 32. The deterministic objective score has none of this dispersion. With the judge
  at 50% weight and n=1, the top-4 total cluster (70.0 / 69.4 / 68.0 / 67.3) is inside judge
  noise — the ordering there is effectively a coin flip.
- **Judge and objective rank models near-oppositely at the extremes.** Step 3.7 Flash is last
  on objective but first on judge; Sonnet 5 is 2nd on objective but last on judge. The judge
  also compresses toward the middle and haircuts the objectively-best models hardest
  (GPT −24, Sonnet 5 −27) — a central-tendency pattern. We cannot currently tell whether the
  judge captures real synthesis quality the checks miss or rewards fluent-but-worse answers
  (verbosity/fluency bias).
- **Structural conflict of interest.** `openai/gpt-5.5` is simultaneously a ranked contestant
  and the sole judge. This data shows no self-preference (GPT-5.5-candidate landed mid-pack on
  judge), but a leaderboard where one competitor grades all others is indefensible regardless.
- **`temperature=0` gives false determinism.** `_build_payload` sets temp 0 expecting
  reproducibility; batch-invariance in inference kernels yields ~8% output variance regardless
  of temperature. Stability must come from aggregation, not the temperature knob.

Supporting literature (see §9): absolute pointwise 0–100 scoring is the least reliable judging
mode (verbosity bias, drift); a panel/jury of diverse models cuts self-preference bias >50% and
reduces variance at lower cost than one large judge; pairwise-with-position-swap is the
gold-standard for reliability but a larger architectural change.

## 2. Goals

Make the arena score **fair and reproducible** by confining the LLM judge to the one thing it is
actually suited for and de-biasing that residue:

1. **Deterministic questions are scored deterministically.** Remove the five redundant rubric
   points from the LLM judge; their signal already lives in the objective checks. The judge
   grades only genuinely subjective quality (report synthesis coherence, analytical correctness).
2. **The residual subjective judgment is robust and unbiased.** Replace the single GPT-5.5 call
   with a small **jury** of diverse judge models, averaged, with per-judge scores and a reported
   **stdev**; and **exclude any model that is a contestant in the same run** from judging it.
3. **Noise is visible, not hidden.** Report the **objective** axis (deterministic spine) and the
   **subjective** axis (jury mean ± stdev) **separately**; rank primarily by objective. Stop
   collapsing them into one 50/50 total that launders judge noise into the standings.
4. **Fix the dead trap point** so "trap handling" measures something (runner-enforced absence).
5. **Stop relying on `temperature=0`** for reproducibility; document that robustness comes from
   the jury/averaging.

**Non-goals:**

- Full pairwise-Elo migration — documented as an optional Phase 4, not core this round.
- Re-scoring historical runs — the leaderboard is already run-scoped (commit 6535726).
- Rewriting the objective assertion engine — it is the reliable spine and stays as-is (beyond
  the shared trap-absence fix).
- Human-calibration of the subjective rubric against annotator labels — future work.

## 3. Decisions

**D1 — Rubric reallocation (judge rubric: 6 → 2 subjective points).**
`risk-manager-control-day.md` rubric is reduced to genuinely-subjective points only. Deleted
points keep their signal via the existing objective checks (no new objective checks needed):

| Old rubric point | Disposition | Objective twin that already covers it |
|---|---|---|
| Trap handling | **Delete** (→ objective, after Phase 0 fix) | `tool_not_called: run_scenario_test` + not-found `response_contains` |
| Instruction adherence | **Delete** | `tool_called args_any_of` + `all_calls`/`max_calls` (backtest window, exact set) |
| Process | **Delete** | `tools_routed_sequence` success assertion |
| Staleness judgment | **Delete** | `response_contains` stale-terms (step 1) |
| Numeric grounding | **Delete** | `response_quotes_tool_value` (signed, near-anchored, magnitude for CVaR) |
| Report synthesis | **Keep + split into 2** | none — genuinely subjective |

New judge rubric (2 points, both subjective, both with 0/50/100 anchors):
- **Synthesis coherence** — does the governance report weave hotspot + landscape + scenario
  loss + backtest into one coherent narrative (not a bag of numbers)?
- **Analytical correctness** — are the risk interpretations sound (direction of risk, what the
  breach implies, whether the recommendation follows) — beyond whether the figures are merely
  present (that presence is the objective `response_quotes`/`artifact_contains` check)?

**D2 — Jury, not a single judge.** `judge_match` → a panel evaluator over a configured judge
pool (default 3 diverse models). Each judge scores the (now 2-point) rubric independently; the
subjective score is the **mean of per-judge means**, and the breakdown carries **per-judge
scores + stdev**. Aggregation is average pooling (not max-vote) since scores are continuous.

**Default panel (concrete, from `agent_channels.example.yml`):**
- `deepseek-v4-pro` — `deepseek` channel (**direct, non-ZenMux** — the required resilience judge)
- `anthropic/claude-opus-4.8` — `zenmux` channel (non-OpenAI frontier)
- `qwen/qwen3.7-max` — `zenmux` channel (independent, cheaper reasoning family)

Distinct provider lineages; ≥1 direct channel. **Contestant-overlap substitution order**
(skip any model that is the contestant being judged): `gemini-3.1-pro-preview` → `glm-5.2` →
`kimi-k2.7-code`. **Accepted known risk:** 2 of 3 defaults ride the shared ZenMux quota, so a
mid-run ZenMux outage degrades the panel to the single direct judge (`deepseek-v4-pro`) — the
self-consistency fallback (D4) then covers it, flagged as degraded. A second direct-channel
judge would harden this but the catalog offers none outside the DeepSeek family today; revisit
if a non-DeepSeek direct channel is added.

**D3 — Contestant exclusion.** The judge pool is declared separately from the candidate pool.
When judging model *M*, any pool member equal to *M* is dropped for that match. If exclusion
leaves fewer than a `min_judges` floor (default 2), fall back to **self-consistency** (D4).

**D4 — Self-consistency fallback (quota/availability).** When a full panel is unavailable
(exclusion or provider outage), take *k* independent samples (default 3) from the best available
non-contestant judge and average them, still reporting stdev. This preserves *sampling*-variance
reduction when the panel can't be assembled — directly relevant given ZenMux's rolling quota
(see the run-#10 quota post-mortem). **It must be surfaced as a DEGRADED mode, visibly distinct
from a true panel** (e.g. `subjective_mode: "self_consistency"` in the breakdown, rendered as
such): multiple samples from one model cut sampling noise but do NOT remove model-family/shared
bias, so it is not a substitute for jury diversity.

**D5 — Separate axes; objective is the ranking spine; no blended total.** `score_breakdown` and
the leaderboard report **objective** and **subjective** as distinct dimensions. Primary ranking =
objective (deterministic). Subjective is shown alongside as `mean ± stdev` and is **advisory** —
it does not move the rank. **The 50/50 `total_score` is dropped** (resolved §8.1): a blended
scalar lets a noisy, non-ground-truth axis move rank and falsely implies the two axes are
commensurable. If a one-number convenience value is ever needed for UI it is explicitly labelled
an **"advisory composite," never the sort key**.

**D5a — Explicit tie policy.** Because subjective is advisory, objective ties must resolve
deterministically, NOT by silently falling back to the subjective axis (which would smuggle the
blended-total problem back in). Tie-break order: (1) higher objective sub-axis by priority
`grounding → adherence → synthesis → procedural`; (2) if still tied, **shared rank** (both listed
at the same position). Subjective may be *displayed* next to a tie but never breaks it unless a
future policy explicitly and visibly opts in.

**D6 — Fix the dead trap (shared Phase 0).** The runner enforces the trap precondition: the
named "does-not-exist" scenario set is asserted-absent (or removed) at match setup, so a
competent model that checks the library and reports "not found" actually passes. This also
repairs the objective `tool_not_called`/`response_contains` twin, which is currently inverted
(only quota-dead trials "pass" it). Cross-references the run-#10 post-mortem benchmark bugs
(the dead `hotspot.delta` / `landscape` grounding paths are folded into this Phase 0 correctness
sweep since they share the "fixtures invented, not harvested" root cause).

**D7 — Determinism stance.** Remove the implicit reliance on `temperature=0` for reproducibility;
keep temp 0 as a mild stabilizer but document that real stability comes from the jury/self-
consistency averaging. No batch-invariant-kernel work (out of scope).

**D8 — Network isolation preserved.** The jury path stays injectable (`post`/panel-poster
callables) so tests never hit the network, matching the current `judge_match(post=…)` seam.

## 4. Architecture

### 4.1 `judge.py` — jury evaluator
- `JudgeResult` gains: `per_judge: list[{model, rubric_scores, judged_score}]`,
  `judged_stdev: float | None`, and keeps `judged_score` (now the panel mean).
- New `judge_panel(transcript, loaded, *, judge_models, exclude_model=None, min_judges=2,
  self_consistency_k=3, post_for=None) -> JudgeResult`:
  - resolve the effective pool = `judge_models − {exclude_model}`;
  - if `len(pool) >= min_judges`: one structured-output call per judge, average the per-judge
    means, compute stdev;
  - else (initial pool `< min_judges`): self-consistency — *k* samples from the best available
    non-contestant model, `subjective_mode="self_consistency"`;
  - a per-judge parse/HTTP failure drops that judge (not the whole panel). **After failures, if
    surviving judges `< min_judges`, do NOT silently proceed on the survivors** — that reinstates
    the single-judge failure mode this reform removes. Escalate to self-consistency (*k* samples
    on one surviving eligible non-contestant judge, `subjective_mode="self_consistency"`); only if
    **zero** eligible judges survive → `judge_missing=True` (advisory-unavailable), never a
    fabricated score.
- Keep the existing per-call retry/parse/validate logic (`_parse_response`) unchanged.
- `_build_prompt` updated for the 2-point rubric; `JUDGE_MODEL` const → `JUDGE_POOL` config.

### 4.2 Config
- Judge pool declared in settings / `agent_channels` (e.g. `ARENA_JUDGE_MODELS`), 3 diverse
  models that are **not** in the default candidate roster where possible. `min_judges`,
  `self_consistency_k` are settings with the defaults above.

### 4.3 `scoring.py` / `models.py` / `store.py`
- `score_breakdown.judge` carries `per_judge` + `judged_stdev`.
- `objective_breakdown` is unchanged (still the deterministic spine).
- `total_score`: per D5 — default stop emitting a 50/50 total; leaderboard sorts by objective,
  exposes `subjective_mean`, `subjective_stdev` as separate columns. If a blend is retained it
  is objective-dominant and labelled.
- `leaderboard()` rows gain `subjective_mean`, `subjective_stdev`; sort key leads with objective.

### 4.4 `runner.py` / `task.py`
- Phase 0: trap-set-absence enforcement at match setup (idempotent; restore/no-op after).
- **Harden `_is_infra_contaminated` (correctness fix, Phase 0).** The current predicate treats a
  step as "recovered" if it has `response_text` **or** `tool_calls`. That is wrong: a step can
  issue a tool call and then have its *final* assistant response die on a provider 402/429/5xx
  (empty `response_text` + provider error) — a partial death that the objective tool-call checks
  score as procedurally successful, corrupting means. Recovery must require a **completed
  assistant response** (`response_text.strip()`), NOT merely an issued tool call. Verified safe
  against the run-#10 recovery case (mimo#0 thread 215: its errored step carries `resp_len=2330`,
  so it stays exonerated). Regression: a later step with `tool_calls`, empty `response_text`, and
  a 402/429/5xx provider error must be recorded `invalid`.
- Judge-missing handling: a match whose *subjective* judgment is unavailable is still a valid
  **objective-scored** match (objective is the spine); subjective shows "n/a ± n/a". (This
  supersedes the run-#10 driver's judge-missing-as-incomplete workaround, which existed only
  because the total blended the two — once axes are separate, a missing jury no longer
  invalidates the trial.)

### 4.5 Frontend (`Arena.live.tsx`, `arenaApi.ts`, `Arena.css`)
- Leaderboard: **Objective** as the primary ranked column; **Subjective** shown as
  `mean ± stdev` (muted, advisory), not folded into one total.
- Match drilldown `ScoreBreakdownView`: render the 2-point subjective rubric with **per-judge**
  scores and the stdev; keep the axis strip for objective. Token-only styling per
  `frontend/CLAUDE.md`.

## 5. Phases (independently shippable)

- **Phase 0 — Benchmark correctness.** Runner-enforced trap-set absence; fix the dead
  `hotspot.delta` / `landscape` grounding paths (harvest fixtures from real tool output).
  Unblocks both the objective checks and the (about-to-shrink) judge rubric. Smallest, highest
  integrity win.
- **Phase 1 — Rubric reallocation.** Shrink the judge rubric to the 2 subjective points; delete
  the 5 redundant points (objective twins already cover them). Biggest fairness gain, minimal
  code. Update `test_flagship_loads` denominators and golden replay fixtures.
- **Phase 2 — Jury + exclusion + stdev + self-consistency.** `judge_panel`, contestant
  exclusion, per-judge + stdev in the breakdown, self-consistency fallback. Network-isolated
  tests via injected posters.
- **Phase 3 — Reporting reform.** Separate axes end-to-end (scoring → store → API → frontend);
  rank by objective; subjective advisory. Judge-missing no longer invalidates a match.
  **Hard sequencing gate: P3 must NOT ship until Phase 0 is complete and regression-tested.**
  Declaring the objective axis publicly authoritative while its trap/grounding fixtures are still
  inverted or invented would ship a "fairness fix" built on a broken spine. P0 → (P1, P2 in any
  order) → P3.
- **Phase 4 — (optional/future) Pairwise Elo** on the subjective dimension (position-swapped
  A/B, Bradley-Terry/Elo) for a principled subjective ranking. Deferred; documented only.

## 6. Failure handling

- **Partial jury failure (some judges 402/parse-fail):** average over survivors **only while
  survivors `≥ min_judges`**. If failures drop survivors below `min_judges`, do **not** proceed on
  the remaining one or two (that reinstates the single-judge variance this reform removes):
  escalate to self-consistency (*k* samples on one surviving eligible non-contestant judge,
  `subjective_mode="self_consistency"`, flagged degraded); if `0` eligible survive →
  `judge_missing=True` (advisory-only). This closes the D4 gap where a ZenMux outage could collapse
  a 3-judge panel to a single judge + single sample while appearing "handled".
- **Quota exhaustion:** self-consistency fallback (D4); a missing jury never blocks or corrupts
  the objective score (D5). Reuses the run-#10 lesson that infra failures must not masquerade as
  ability — but now scoped to the *subjective* axis only.
- **Contestant exclusion empties the pool:** fall back to a self-consistency run on a configured
  non-contestant judge; if none exists, subjective = n/a for that match (objective still stands).
- **Judge disagreement (high stdev):** surfaced, not hidden — a large stdev is a signal to the
  reader that the subjective call is unreliable for that match, exactly the transparency the
  current single-sample design lacks.

## 7. Testing

- **Rubric reallocation:** manifest loads with the 2-point rubric; `test_flagship_loads`
  denominator updated; golden replay still earns full **objective** marks.
- **`judge_panel`:** averaging + stdev math; per-judge failure drops one judge not the panel;
  zero-survivors → `judge_missing`; contestant exclusion removes the right model;
  self-consistency fallback triggers when pool `< min_judges`. **Post-failure escalation
  regression:** a 3-judge pool where 2 calls fail and 1 survives must trigger self-consistency
  (`subjective_mode="self_consistency"`), NOT proceed on the single survivor. All via injected
  fake posters (no network), matching the existing `post=` seam.
- **Separate axes:** `score_breakdown` shape; leaderboard sort by objective; subjective
  mean/stdev exposed; judge-missing match still objective-scored (not invalid).
- **Phase 0 — trap:** trap-absence enforcement makes a "checks library, reports not-found"
  transcript pass `tool_not_called` + `response_contains`; a "runs the set anyway" transcript fails.
- **Phase 0 — contamination predicate:** a later step with `tool_calls`, empty `response_text`,
  and a 402/429/5xx provider error is recorded `invalid` (tool-call-then-provider-death is a
  partial death, not recovery); a step whose provider error was retried and produced real
  `response_text` (mimo#0 shape) stays scored.
- **Frontend:** `Arena.live.test.tsx` — objective primary column, subjective `mean ± stdev`,
  per-judge drilldown, judge-missing renders "n/a"; `tsc --noEmit` clean; token-only styling.
- **Regression:** judge tests network-isolated; golden replay fixtures re-harvested (Phase 0)
  and kept full-marks on objective.

## 8. Resolved decisions (Codex-consulted 2026-07-05, user-approved)

1. **No blended total.** Rank purely by the deterministic objective axis; subjective is advisory
   `mean ± stdev`. Any one-number UI value is a labelled "advisory composite," never the sort key.
   → folded into D5 + D5a (explicit deterministic tie policy).
2. **Judge pool:** `deepseek-v4-pro` (direct) + `anthropic/claude-opus-4.8` + `qwen/qwen3.7-max`,
   contestant-excluded, ≥1 direct channel required. Substitution order on overlap:
   `gemini-3.1-pro-preview` → `glm-5.2` → `kimi-k2.7-code`. Accepted known risk: 2/3 on ZenMux →
   degrades to the single direct judge under a ZenMux outage (self-consistency, flagged degraded).
   → folded into D2 + D4.
3. **2 subjective rubric points:** synthesis coherence + analytical correctness. "Reasoning depth"
   explicitly rejected (rewards verbosity/visible chain-of-thought over correctness). → D1.
4. **Pairwise Elo (P4): deferred**, documented as a future option only — not justified for ~8
   models until the de-biased pointwise jury proves insufficient. → Phase 4 stays optional/future.

### Risk mitigations adopted from the Codex review
- **P0 gates P3** — objective axis may not be declared authoritative before the trap/grounding
  fixtures are repaired and regression-tested (folded into Phase 3).
- **Self-consistency is a visibly degraded mode**, never presented as a true panel (folded into D4).
- **Ties resolve deterministically**, subjective never a silent tie-breaker (folded into D5a).

### Remaining genuinely-open item (non-blocking)
- A **second direct-channel judge** would harden panel resilience, but the catalog offers none
  outside the DeepSeek family today. Revisit only if a non-DeepSeek direct channel is added; not a
  blocker for implementation.

## 9. References

- Panel/jury reduces bias & variance, cheaper than one big judge — *Replacing Judges with
  Juries* (arxiv 2404.18796); orq.ai "LLM juries in practice".
- Pointwise vs pairwise reliability, position/verbosity/self-preference bias — Adaline
  "LLM-as-a-judge reliability/bias"; *Position bias in pairwise LLM judges* (arxiv 2406.07791);
  *Justice or Prejudice?* (arxiv 2410.02736); Evidently AI LLM-as-judge guide.
- `temperature=0` ≠ deterministic (batch-invariance) — Thinking Machines "Defeating
  Nondeterminism in LLM Inference"; Singlr "The Deterministic LLM".
- Internal: run-#10 post-mortem + memory `arena_run10_flagship_v2` (judge variance table,
  trap-set-exists bug, quota post-mortem); flagship v2 spec `2026-07-04-arena-flagship-
  discrimination-design.md` (objective axes, assertion types).
