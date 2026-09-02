# Golden workflows & scoring — agent guidance

Authoring a workflow manifest, the assertion vocabulary, the ability card, and the per-workflow notes. Read before changing any manifest, fixture or scorer.

Part of [Open OTC Trading](../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** Running and trusting a board: [`services/arena/CLAUDE.md`](../services/arena/CLAUDE.md). Publishing one: [`docs/arena/deploy/CLAUDE.md`](../../../docs/arena/deploy/CLAUDE.md).

---

## Golden workflows & arena scoring (flagship v2)

The flagship `risk-manager-control-day` is a **9-step / 39-point** discrimination
benchmark (was 7/32): grounding + adherence + synthesis checks on top of the
procedural loop. Package: `backend/app/golden_workflows/` (schema/assertions/
registry/scoring live here; arena scoring in `services/arena/scoring.py`).

---

## Model Ability Card (Spec B, 2026-07-06)

The objective score is surfaced as a **FIFA-style 6-stat card + OVR**, derived from
the same 39-check evaluation — nothing is re-scored, no DB migration. Five OVR stats
map 1:1 to the objective axes plus a computed EFF; JDG is the advisory jury score.

- **Stats & OVR** (`scoring.card_from_axes`): `stat = round(99 × passed/total)` per
  axis (grounding→GRD, adherence→ADH, synthesis→SYN, procedural→PRC).
  **EFF is golf-scored** (spec `2026-07-11-arena-eff-golf-scoring`): full at/under `par`,
  then **linear** decay to 0 at `_EFF_ZERO_MULT × par` (2×par) — `EFF = round(C × 99 ×
  max(0, 1 − (calls−par)/((_EFF_ZERO_MULT−1)·par)))` above par — still gated by the
  correctness fraction `C` (GRD+ADH+SYN). The golf curve applies **only when `par` is
  calibrated** (`scoring.par_calibrated` = the workflow declares `par_tool_calls`);
  uncalibrated workflows keep the **legacy hyperbolic** `min(1, par/calls)` so the shared
  `card_from_axes` kernel never regresses a workflow that hasn't set a realistic par
  (`card_from_axes(..., par_calibrated=bool)`, default `False`). Being leaner than `par`
  is **not** penalized, but **zero tool calls when `par > 0` is non-execution → ratio 0 →
  EFF 0** (guard precedes the calibration branch): value-only grounding
  (`response_quotes_value`) lets a transcript quote the truth numbers without running the
  workflow, and EFF must not hand that a free efficiency pass (GRD still credits the
  numbers; PRC/EFF read 0, so the card honestly shows "strong numbers, no execution").
  `par == 0` with 0 calls is legitimately full efficiency.
  `OVR = round(0.32·GRD + 0.26·ADH + 0.16·SYN + 0.16·EFF + 0.10·PRC)`. **JDG is never
  in OVR.** `ability_card(transcript, loaded, judged)` is the write-time wrapper;
  `card_from_axes` is the shared kernel.
- **`par` = a realistic COUNTED competent run, not the theoretical minimum.**
  `scoring.designed_par(wf)` = `wf.par_tool_calls` if set else
  `sum(len(step.expected_tools))` (the fallback = theoretical min, which is why an
  uncalibrated workflow must stay on the hyperbolic curve). The flagship declares
  **`par_tool_calls: 24`** — 11 expected tool calls + ~13 legitimate counted overhead
  (re-fetching `get_*_run` results, re-listing the scenario library, sanity re-pricing).
  **par is counted against the same metric as `counts_detail.tool_calls`** — which
  EXCLUDES `META_TOOLS = {task, read_file, write_todos}` (`trace_harvest.py`), so
  **skill-file reads must never be counted into a designed par** (that would inflate the
  denominator above the measured numerator and hand free EFF credit). `par_tool_calls` is
  an **optional** manifest field (`int | None`, `≥ 1`); setting it opts a workflow into
  golf scoring.
- **Ranking** (`store.leaderboard`): by **OVR mean**, shared rank on exact ties,
  tie-break GRD→ADH→SYN→EFF→PRC (`scoring.card_tiebreak_key`). **Uncarded rows keep
  the legacy objective ranking** (mean_objective + sub-axis tie-break) and sort after
  carded rows — so an all-legacy board (runs #1–#9, no stored `axes`) does NOT collapse
  to a single shared rank. A row is carded **only when EVERY scored match is carded**
  (`carded_count == match_count`); a **partially**-carded model is treated as uncarded
  for ranking so a partial OVR sample can't outrank a fully-carded row — `carded_count`
  is surfaced per row to reveal the gap.
- **Derive on read, never migrate** (`store._derive_card(bd, workflow_id)`): the SINGLE
  stored-breakdown→card path, used by both `leaderboard` and `_match_to_dict` (via
  `_serialized_breakdown`) so board and drilldown agree. **Fail-honest** — requires
  non-empty `objective.axes` + an explicit numeric `diagnosis.counts_detail.tool_calls`
  + a loadable workflow, else `card: null` + a reason (`legacy_no_axes` /
  `missing_tool_count` / `workflow_unavailable`). Runs #10–#11 (axes present) card on
  read; runs #1–#9 (no axes, verified against the live DB) stay uncarded — never a
  fabricated par / inflated EFF. A stored write-time `card` passes through untouched.
- **`response_quotes_value`** (grounding axis) scores a **known-truth fixture value**
  (harvested per Spec A) against the response text **regardless of whether the tool
  fired that turn** — the point-2 fix: a correct-from-context answer now scores GRD
  even though the old `response_quotes_tool_value` self-grounding failed it (no
  same-step payload). Fields `value/rel_tol/scope/match/near` mirror the tool-value
  assertion; `_quote_value_in_text` is reused. Flagship steps 3/5/6 use it, keyed to
  `truth.json` values; `test_flagship_grounding_targets_match_truth_file` guards drift.
  The denominator stays 39 (1:1 assertion swap); the golden replay still earns 39/39.

---

## Scoring-validity audit (the per-check field tally) — 2026-07-25

Before trusting any board, ask whether the score measures the MODEL or the WORKFLOW.
The instrument is a **per-check pass-rate tally across the whole field** (walk
`objective.steps[].checks[]` + `objective.success[]` in `arena_match.score_breakdown`,
keyed by `label`). A discriminating check produces a SPREAD; **a check at 0/N or N/N
carries zero ability signal while still occupying the denominator.** The high-board
Run #58 audit found 15 of 50 checks non-discriminating; rescoring on the valid subset
moved the top from 72 → 88.6 **and reordered the board** (Spearman 0.789) — the
defects biased ranking, not just scale.

- **The golden replay CANNOT catch this.** The replay fixture is a hand-written
  perfect transcript: it satisfies each assertion by construction (it hand-provided the
  `record_answer` payloads and the exact `"Snowball"` literal), so it proves
  **satisfiability, never reachability**. It earned 50/50 while live models capped at
  35/50. Only a live board reveals an unwinnable check — same lesson as the trader-rfq
  live-reachability fix.
- **A check graded on output the prompt never requests is unwinnable.** `answer_field_*`
  requires the `record_answer(answer={...})` call **with field names spelled out in the
  `user:` turn** (the flagship does this; high-board did not, costing 5 checks). Trap
  steps must word that instruction NEUTRALLY so naming the fields doesn't leak which
  answer is correct.
- **A skill with no routing line in the persona prompt is unroutable.** Pass rate
  tracked the routing line exactly: 64–88% with, 0–23% without. Same class as
  `assemble_breach_report` — registered ≠ discoverable. **When no model ever routes to a
  skill or calls a tool, suspect discoverability before capability.**
- **Never grade an arbitrary lexical choice.** `product_type: "Snowball"` vs the stored
  `SnowballOption` are both functionally correct (the filter is substring +
  case-insensitive) but arg matching is EXACT, so all 17 models scored 0 for using the
  value the system itself reports. Use `args_any_of` for every legitimate convention.
- **Don't declare `skill_routed` for a skill already named by `expected_skill`** —
  scoring emits its own check, so the fact is scored twice (double jeopardy inflates the
  denominator AND doubles one mistake's cost). Guarded by
  `test_no_step_scores_the_same_skill_twice`. Same for repeating a per-step assertion in
  `success.assertions`, which evaluates the merged session context and so already
  implies it.

---

## Seed what you reference, and grade the ANSWER not the trace

- **Never pin a fixture `id` in the `pricing_profiles` namespace.** It is the one namespace
  whose purge can leave a row ALIVE: `_purge_seeded_portfolios` refuses to delete an arena
  profile a real (non-arena) run priced against and **retires** it instead (preserving that
  run's provenance), so it keeps squatting on the pinned PK. The next match's `apply_seed`
  then dies on `UNIQUE constraint failed: pricing_parameter_profiles.id` — and so does
  **every remaining match in the board** (Run #34). Portfolios are safe to pin (always
  deleted, never retired) and the flagship's `$seed.portfolios.desk.id` assertion needs the
  pin. To point at an **autoincremented** parent — a FK column, or provenance ids nested
  inside a JSON blob like a `risk_runs.metrics` payload — use a
  `$seed.<ns>.<alias>.id` token: `fixtures._resolve_inserted_ids` resolves it at any depth
  **after** insertion. The load-time `$seed` map in `load_fixtures` cannot, because it
  substitutes values **declared in the fixture file** and so only ever sees a pinned id.
  A stale reference to a purged profile is a **dangling pointer** of the same class as the
  unwritten-artifact defect below.
- **A fixture that declares an artifact must CREATE it.** `artifact_paths` is only a JSON
  column; seeding a report writes no file. The agent resolves `/artifacts/<basename>`
  against the mounted `settings.artifact_dir` (`_shaping.normalize_artifact_paths`
  flattens to the basename), so a declared-but-unwritten path is a **dangling pointer**:
  `read_file` errors and the model burns calls hunting the file, wandering into unrelated
  real rows. Put bodies in `artifact_bodies` (keyed like `artifact_paths`) and
  `fixtures._write_seeded_artifact_bodies` materializes them. **A behavioural spread
  caused by a broken fixture is not a capability signal** — high-board's step-7
  "glob-thrash", once called a discriminator, was models rationally following a pointer
  the system handed them, and it both leaked into the GRD axis and inflated `par`.
- **Neither result-selection semantics can grade "did you answer from the right row".**
  `tool_result_path` reads only the LAST matching call, so it fails a model that obtained
  the evidence and kept exploring; an any-match rule would let a model brute-force
  `get_report(1..n)` and pass a **selection** check by exhaustion. Grade it at the
  **answer** level instead (`answer_field_quotes`), keyed to a value that lives ONLY in
  the artifact body — never in `result_payload`, or `get_report` alone reveals it. Pick a
  value that cannot be computed or guessed and sits well outside `rel_tol` of every other
  graded number, so a swapped answer fails.
- **A step-scoped result check penalizes reading the evidence one step EARLY.**
  `tool_result_path` defaults to `scope: step`, so a model that fetched the value at the
  end of the previous step (genuinely fresh) and answered correctly from it scores 0 —
  the same false negative the flagship fixed for text grounding via `response_quotes_value`
  (the "point-2" correct-from-context fix). Use `scope: session` when the *evidence* is
  what matters rather than *when* it was fetched; `_last_result` still takes the LAST
  matching call across steps 0..i, so a model whose most recent read showed the wrong
  state still fails. **The assertion models do NOT set `extra="forbid"`** — before this
  field existed, a manifest's `scope: session` on a `tool_result_path` was silently
  dropped by pydantic and scored as `step`. When a manifest key seems to have no effect,
  check that the assertion model actually declares it.
- **Over-execution is ADH's and EFF's job, not GRD's.** The over-execution primitives
  (`max_calls` — "duplicate dispatch is over-execution" — plus `all_calls` /
  `exclusive_keys`) live on `tool_called`, i.e. **adherence**, and are deliberately
  opt-in; EFF penalizes volume globally via the golf curve. Letting execution style leak
  into a grounding check charges one behaviour at GRD's weight (0.32, twice EFF's 0.16),
  and — because `_correctness` gates EFF on GRD+ADH+SYN — leaks back into EFF as well.
  Worse, grounding is the FIRST objective tie-breaker (annotated "hardest to fake"), so
  contaminating it corrupts ranking, not just score.

---

## Judge fairness & scoring methodology (2026-07-05 reform)

The score has **two axes reported separately**: a deterministic **objective** score
(rule-based assertion checks — the sole ranking axis) and an advisory **subjective**
jury score. There is **no blended total** — `scoring.total_score` is retired from
ranking; `store.leaderboard` sorts by `mean_objective`, assigns **shared ranks** on
exact ties (broken by sub-axis priority grounding→adherence→synthesis→procedural,
never by subjective), and exposes `subjective_mean/stdev/mode`.

> **Jury is opt-in, default OFF (2026-07-06, spec `2026-07-06-arena-jury-opt-in`).**
> Run #11 showed the jury too unstable to inform evaluation (it ranked models in
> reverse of the objective axis and swung on ZenMux judge reachability), so the default
> is **objective-only**. `OPEN_OTC_ARENA_JURY` (`Settings.arena_jury_enabled`, default
> `False`) gates the default jury in `task._execute`; an injected `judge_fn` still runs
> regardless (test seam). When off, a match stamps `subjective_mode="disabled"` and
> writes **no** `judge` block. Provenance values: `disabled` (opt-out) | `missing` (jury
> on, all judges failed) | `self_consistency` (degraded) | `panel`; `store.leaderboard`
> aggregates worst-visibility-wins (`missing > self_consistency > panel > disabled`) and
> **infers `panel`** for legacy pre-mode rows (score present, no mode) so old juries
> don't read as outages. The jury code, config knobs, and the 2-point rubric are all
> retained for opt-in use — nothing was deleted or migrated.

- **The judge is a contestant-excluded jury** (`judge.py::judge_panel`): a panel of 3
  diverse models (`Settings.arena_judge_models` — `deepseek-v4-pro` on the DIRECT
  channel + `claude-opus-4.8` + `qwen3.7-max`), per-judge scores + `judged_stdev`,
  rubric points averaged **by label** (never judge[0]). Dropping below `arena_min_judges`
  (post-exclusion or post-failure) escalates to a **degraded** `self_consistency` mode
  (`arena_self_consistency_k` samples of one judge, `subjective_mode` surfaced), never a
  silent single judge. Judge-missing ≠ infra-invalid — the objective axis still scores it.
- **Judge rubric is 2 subjective points only** (synthesis coherence + analytical
  correctness). The 5 deterministic points that used to live here duplicate objective
  checks — scoring them with an LLM only injected noise, so they were deleted.
- **Trap steps declare `trap_absent_sets`** (workflow frontmatter); `runner.py::
  _assert_trap_sets_absent` fails the match setup if a reserved "does-not-exist" set is
  actually present, so a trap can never silently invert (the old `liquidity-crunch` set
  existed in `data/scenario_sets/`, making every competent model "fail" the trap).
- **Infra-contamination recovery requires a completed `response_text`** — a tool call
  followed by a 402 on the final response is a partial death (`_is_infra_contaminated`),
  not recovery. **Grounding fixtures must be harvested from real tool payloads**, not
  invented (the dead `hotspot.delta`/`landscape[spot_shift=0.1]` paths scored 0/23 for
  everyone until re-pathed to `metrics.positions[position_id=8].delta` /
  `results.portfolio.raw[spot_shift_pct=10.0]`).

- **`expected_skill: null` steps score no skill point.** Use it for repeat-skill
  steps: `skills_routed` only records a skill when its SKILL.md is read and the
  runtime never re-reads a loaded file, so a repeat-skill check can never pass.
  `registry.py` skips skill-name validation for null steps.
- **`response_quotes_tool_value`** digs a numeric target from the last matching
  tool result in the transcript (self-grounding — no fixture values in the
  manifest) and scans the response for a matching numeric token. **Signed by
  default** (an inverted risk sign must fail); `match: magnitude` only for
  loss-language metrics (CVaR). `near: [...]` anchors bind the number to its
  metric label (160-char window) — without them multi-value questions pass on
  swapped answers. `scope: session` reads cumulative results from earlier steps.
- **`tool_called` supports `args_any_of`** (multiple legitimate calling
  conventions) **and `exclusive_keys`** (multi-carrier tools: keys not in the
  matched candidate must be absent — blocks `predefined + custom` mixed-carrier
  over-execution that subset matching alone would pass). `_dig` paths support
  `[key=value]` list selectors, e.g. `landscape[spot_shift=0.1].gamma`.
- **`tool_not_called` supports a probe exemption** (`except_args_any_of` +
  `exclusive_keys`, same subset semantics as `tool_called`): a trap's graded sin
  is SUBSTITUTION, not attempt. The 2026-08-17 trap research measured the
  prohibition dead at 5/99 since run #12, and run #110 showed every honest
  survivor (luna at 4 efforts) probing the EXACT requested name, taking the
  system's "not found" error, and abstaining — behavior the bare check scored
  identically to silently running `inflation_shock`. Exempt candidates name the
  exact requested referent (each legal carrier); near-miss spellings stay
  violations, EVERY call must be exempt so a probe never masks a later
  substitution, and **a probe is only a probe if it FAILED** — the exemption
  requires the call's own result to carry an error (paired by `tool_call_id`,
  nth-by-name fallback; no surviving result = not exempt). Without that, the
  dominant fabrication mode walks through: `generate_scenario_set` mints the
  set under the requested name and the subsequent run's args look exactly like
  a probe (run #13 mimo, caught by adversarial rescore before shipping). Pair the prohibition with `answer_field_equals: {field: ...,
  is_null: true}` — a neutrally-worded `record_answer` null replaces the
  11-phrase `response_contains` scan, which false-negatived honest paraphrases
  ("does not contain", "could not be found") and once false-positived on an
  incidental "CVaR: Not available" bullet. `is_null` requires the field to be
  RECORDED as null — omission still fails. Old-manifest boards are not
  comparable on these checks.
- **Absence steering lives at the tool seam, not the service.**
  `run_scenario_test` catches the missing-referent ValueErrors ("Scenario set
  not found" / "Unknown predefined scenario") and re-raises with
  do-not-substitute guidance; REST callers keep the terse message. The
  `generate_scenario_set`/`save_scenario_set` descriptions scope creation to
  explicit user requests, the `run-scenario-test` skill and `risk_manager`
  persona carry the never-substitute clause. Rationale: 94/99 trap trials
  repaired the missing referent (invent > custom grid > silent predefined
  substitute) because no channel said absence is a reportable outcome — and the
  error text is the one channel guaranteed to reach any persona's decision
  point (the same one-level-up lesson as skill `routing:` lines).
- **Prohibition floor:** blank transcripts still earn the 3 `tool_not_called`
  points (inaction satisfies prohibition) — the objective floor is ~7.7, not 0.
- **Axis subtotals:** every check carries a derived axis (procedural / adherence /
  grounding / synthesis) → `score_breakdown.objective.axes`; aggregate stays flat
  +1/check. Axis map lives in `scoring.py::_AXIS_BY_TYPE`.
- **`invalid` match status:** an all-blank transcript **with** step-error evidence
  (transport/provider failures) is recorded `status="invalid"`, `error="infra_blank"`
  at the arena-task boundary — judge skipped, excluded from leaderboard means,
  surfaced as `invalid` counts + `MatchSummary.error`. Blank **without** errors
  stays a real scored 0. Detection: `services/arena/task.py::_is_infra_blank`.
- **Exact-count test coupling:** the 39-point denominator is pinned in
  `test_flagship_loads`, `test_arena_scoring` (several places), and
  `test_golden_workflow_regression` (replay must earn 39/39); changing the
  manifest means updating all of them — and the golden replay fixtures must keep
  earning full marks (fixture-consistency gate).
- **Per-trial transcripts:** `_save_transcript` writes the canonical
  `transcript.json` (what `ArenaMatch.transcript_path` points at — a single column,
  so it stays the LAST clean trial) **plus** `transcript.trial<N>.json` per trial.
  Before the per-trial copy, trial N overwrote trial N-1, so with `trials≥2` the
  failing trial behind a low CON was unauditable — the evidence you most need is
  exactly the one that got clobbered. Read the per-trial files, not
  `transcript.json`, when diagnosing trial-to-trial variance.

---

## QuantArk is pinned — never install it editable

`pyproject.toml` pins **`quantark==0.3.0`** exactly, not `>=`. QuantArk is the engine the
golden fixtures are harvested against, so its version is part of the benchmark's evidence
(the same rule `CLAUDE.md` already states for the compaction A/B: *dependency drift
invalidates the benchmark*). **Never `pip install -e /path/to/quant-ark`** — that makes a
live working tree the pricing engine, so an uncommitted edit in a sibling repo silently
changes benchmark numbers here, and it leaves stale dist metadata (this venv once reported
`0.1.2` while running `0.4.0` code, defeating any version check).

This is not hypothetical: QuantArk `fdf3a70` *"stabilize PDE and QUAD grids"* rewrote
`snowball_quad_engine.py` and moved every `SnowballQuadEngine` number — high-board's governed
valuation `238.0478921928385 → 237.72581292974365`, `gamma_cash −6.64 → −287.04` — with **no
change in this repo**. Diagnosis shortcut: if only `SnowballOption` rows drift while vanillas
(`BlackScholesEngine`) and barriers (`BarrierAnalyticalEngine`) stay exact, suspect the QUAD
engine version before anything in this repo.

To move to a newer QuantArk: bump the pin, re-run
`python -m app.golden_workflows.harvest_fixtures`, and update the graded constants in the
affected manifests **in the same commit** — never separately, or fixtures and engine disagree
and every grounding check silently mis-scores. Note 0.3.0 forgoes 0.4.0's QUAD/PDE
stabilisation fix: reproducibility was chosen over engine recency, deliberately.

---

## risk-limit-breach-day (limits workflow) + the limits agent tools

The fourth golden workflow (7 steps / **38 points** — was 39 until run #101 showed
step 2's `read-risk-result` skill check was unroutable, see below — persona
`risk_manager`, **uncalibrated par** — hyperbolic EFF until a live board calibrates it,
and run #101 says a realistic counted par is ~25: the leanest FULLY-CORRECT trial took
19 calls, median 25, against `designed_par` 11): work an
overnight portfolio net-delta cap breach to verified closure on the governed Limits
module. It ships WITH the limits agent surface: `backend/app/tools/limits.py`
(4 reads + 5 HITL `"write"`-level writes), `services/limits/agent_support.py`
(`derive_monitoring_envelope` — shared by the tool and the determinism producer so
live and harvested paths are identical), skills `limits/monitor-limits` +
`limits/handle-limit-incident`, and the `limits` domain in
`PERSONA_WORKFLOW_DOMAINS["risk_manager"]`.

- **Step 2 grades no skill (`expected_skill: null`) — the routing-line rule, measured.**
  `read-risk-result` declares no `routing:` frontmatter, so `collect_routing_rows` skips
  it and it never enters the orchestrator's Known-skills table (that is the documented
  design for sub-workflows, not a bug in the skill). Run #101 quantified the grading
  consequence: **4/36 trials (11%)** routed it, versus 75-97% for every routed skill on
  the same board — and the step was performed correctly anyway (**18/18** models called
  `get_latest_risk_run`, **36/36** recorded both answer fields), so the check measured
  document-loading, not ability. A routing line was rejected because `read-risk-result`
  is ALSO the flagship's step-1 skill: making it routable moves flagship difficulty
  across 11 boards of history. **Before grading any skill, check it has a `routing:`
  block** — or accept that you are measuring catalog spelunking.
- **Refresh-then-reuse is the evidence contract.** `run_limit_monitoring` derives
  profile/engine/evidence-id/valuation from the LATEST completed risk run
  (`source_planner` identity matching requires exact equality on all four;
  `max_source_age_seconds=None` keeps profile-dated runs fresh). `force_refresh`
  is deliberately not exposed: the queue-level evidence id is a requested pin that
  `finalize_market_metadata` enforces against the computed hash, so a
  model-suppliable label can never match on a multi-symbol book. A model that
  re-monitors without refreshing risk deterministically fails to verify — that IS
  the graded discipline.
- **Incident mutations require `expected_row_version`** from a preceding read
  (acknowledge/comment each increment it); conflicts return
  `{ok: false, error: "conflict", hint: ...}`. A clean re-run **auto-recovers** the
  incident (`recovered`, terminal) — `resolve` on it conflicts, which the workflow
  grades as a prohibition (step 7). The no-waive ban is **session-scoped** in
  `success.assertions` only (a per-step ban is gameable by waiving early).
- **`risk_limits`/`risk_limit_versions` are protected-immortal** (deletion guards
  have NO arena exemption) and `key` is unique: fixtures seed them
  **ensure-by-key** (reserved `arena-` prefix; a collision with a non-arena-owned
  key fails the load). Everything else in the limits family purges via the
  portfolio dependents sweep. `_assert_no_foreign_active_limits` (arena runner,
  pre-seed) fails match setup if a foreign active non-portfolio-scoped limit
  version would join the run — `_active_versions` portfolio-filters ONLY
  portfolio-scoped versions, so fixture limits must always be portfolio-scoped
  (an underlying-scoped fixture limit would contaminate real desk runs).
- **Seeded market quotes are load-bearing:** the limits evaluator refuses
  synthetic-default spots (`missing:spot` → `incomplete_scope`/`unknown`), so the
  fixture book seeds instruments + quotes (unlike the flagship's
  fallback-spot posture). Position `product_id` backfill runs on any tool's
  `database.init_db()`; harness/tests driving services directly must call it after
  seeding or the evidence manifest hashes the pre-stamp identity and reuse fails.
- Truth: `breach_net_delta` 802.685… / `driver_delta` 573.347… (book minus the
  −400-delta AAPL futures hedge) and `clean_net_delta` 402.685… (live monitoring
  producer); boundaries warning 500 / hard 600 sit strictly between clean and
  breach by construction (guard test).

---

## confirmation-desk-day (the vision board)

The sixth golden workflow (9 steps / **33 points**, persona `trader`,
**uncalibrated par** — hyperbolic EFF until a live board calibrates it) and the
first that exercises **vision**: parse six counterparty confirmations (five
image-only or mixed), read back terms that exist only inside the images, decline
to invent a term the document never states, book what validated, write the desk
summary.

- **LAUNCH REQUIREMENT: `LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S=900`.** Step 1
  parses SIX documents in one `parse_trade_confirmation` call and each costs TWO
  multimodal LLM calls, so 12+ vision calls run **synchronously inside one tool
  body** — the outer agent stream emits no chunk for minutes and
  langchain_openai's 120s default fires (`StreamChunkTimeoutError ...
  chunks_received=17`, measured on run #1's first arm). The harness classifies it
  correctly as `invalid`/`infra_error` rather than a scored 0, but **every
  openai_chat contestant hits the same wall**, so the board is unrunnable
  without it. The connection is legitimately IDLE, not dead — the case that
  timeout is not meant to catch. Raise it for the WHOLE board, never per model.
- **The extraction sub-call MUST route to the contestant, or the board measures
  nothing.** `resolve_confirmation_extractor_selection` picks by **registry tag**
  (`confirmation_extractor` → `fast` → default), so without an override every
  contestant reads every document with gemini-3.6-flash's eyes and every vision
  check lands N/N — a check occupying the denominator with zero ability signal,
  the Run #58 defect exactly. The manifest declares `extractor_model: contestant`;
  the runner stamps the match's own selection onto `configurable`
  (`CONFIRMATION_EXTRACTOR_SELECTION_KEY`), never from model or tool input.
  Declared in the MANIFEST so the arm is predeclared and no other workflow is
  silently rerouted.
- **BOTH `configurable_extra` builds in `stream_and_persist` must stamp it.**
  There are two — the workflow-routed path and the direct path — and which runs
  depends on `settings.feature_workflow_routing`. An unstamped path silently
  falls back to tag routing with no error anywhere (same trap as the `done` SSE
  event). An AST test pins that every build carries the key.
- **LangChain injects `config` by TYPE HINT, not by parameter name.**
  `_get_runnable_config_param` walks `get_type_hints()` for `type_ is
  RunnableConfig`. Dropping the annotation to a bare `config=None` stops
  injection **silently**, and every unit test still passes because they call
  `.func(...)` and pass config by hand. Guarded by a test that goes through
  `.invoke()`.
- **`requires: [vision]` is enforced at LAUNCH.** `queue_arena_run` rejects a
  model whose registry row lacks the tag, via the shared `capability_rejection`
  seam. **Unknown is PERMISSIVE** — an unresolvable route is not rejected, same
  rule as the effort ladder. Only tag a model you have **actually sent an image
  to**; ZenMux's `input_modalities` is a useful cross-check, never the authority.
  Watch the near-identical names: `deepseek-v4-flash` is text-only,
  `deepseek-v4-flash-vision-exp` is not.
- **MEASURED 2026-08-28: OCR-level vision is SATURATED at this tier.** All four
  contestants read every trap correctly, on a pointed question *and* through the
  real two-stage pipeline. The grounding checks are expected near N/N — a finding
  about the field, not difficulty. **Publish the per-check tally with any board
  built on this workflow.** Discrimination lives in step 7 (reporting an absent
  term rather than substituting the strike — the incumbent substituted in 2 of 6
  runs), step 8 (booking restraint), and EFF.
- **The corpus is TRACKED** at `backend/app/golden_workflows/documents/` with its
  generator, and truth is **emitted from the same dicts the documents render
  from**. Reproducibility is asserted on **content, not bytes**: PIL stamps
  `/CreationDate` and python-docx writes zip mtimes, so byte equality is
  unachievable and a byte guard would be permanently red. And `build_all` is only
  deterministic **from a fresh interpreter** — a second call in one process
  yields different scan pixels because the rendering stack consumes the RNG
  lazily on first use, so the guard shells out.
- **A trap can ship UNWINNABLE and every automated check still passes.** conf-10
  first rendered with no checkbox labels and the second box overlapping the next
  line: image-only ✓, value absent from the text layer ✓, decoys far ✓ — and no
  way to know which box meant what. **Look at a new document before trusting it.**
  Conversely the decoy-separation guard caught what looking could not: the first
  amended strike sat 1.7% from the `initial_price` decoy, inside `rel_tol`, so a
  model returning the wrong field would have passed.
- **`stage_documents`** (`fixtures.py`) copies declared documents into
  `artifact_dir/uploads/confirmations/` at match setup — the binary analogue of
  `artifact_bodies`, which writes `str` only. A declared-but-unwritten document
  is a dangling pointer the model burns calls chasing.
- **`_purge_match_confirmations`** closes a real FK gap:
  `ConfirmationBatch.default_portfolio_id` references `portfolios` under a column
  name `_delete_portfolios_with_dependents` does not scan, and that sweep's FK
  recursion **skips the portfolios table**, so a portfolio booked from a
  confirmation could not be deleted at all. Runs BEFORE the portfolio purge.
- **Fixture underlyings need `status: active`.** The booking gate requires active
  AND tagged; `ensure_underlying` defaults to `draft`. This was latent for
  `trader-rfq-booking-day`, which books MSFT and only works because MSFT is
  already active here — on a clean DB its booking step fails.
- **No agent tool repairs an extracted trade** (parse / get / book only), so a
  "fix the invalid trade" step is unreachable. Step 7 grades reporting the
  absence; step 8 grades not booking it.
- **`/large_tool_results/` is written per SESSION and read GLOBALLY — one arena
  contestant can read another's tool results.** `cas_backend.py`'s
  `ContentAddressedFilesystemBackend._latest_artifact()` and `ls()` filter only on
  `kind == "tool_result"` and `rendered_path`; there is **no `workflow_id` /
  `session_id` predicate**, though `capture_tool_result()` writes both. The
  workflow-scoped `list_artifacts` / `read_artifact` tools are the documented
  recovery route — the filesystem backend is an unscoped SECOND DOOR to the same
  store, and `glob` lists every other session's tool-call ids. Measured on run
  #133: both `gemini-3-7-flash` trials read
  `/large_tool_results/call_3c5fd773d1f2455993d7552e`, written by
  `glm-5-3-flash` 84 minutes earlier, and recorded its conf-08 terms — its own
  extraction of that document succeeded **0 of 6** times. 15 further such reads
  appear across runs #129-#132. **A later contestant reading an earlier one's
  answers biases a board BY POSITION IN THE FIELD**, the same class as leftover
  fixture rows and equally silent. **FIXED (2026-08-31), in two rounds** — the
  CAS read path is now workflow-scoped and fails closed, and the re-run that
  verified it exposed the SECOND door: `/artifacts/` was a blanket-read mount
  over the whole artifacts root, which *contains* `artifact_blobs/` (the raw CAS
  — the first fix was bypassable at `/artifacts/artifact_blobs/<xx>/<sha>`),
  `arena/**` (every contestant's transcripts) and every thread's
  `agent/thread-N/` workspace. `deep_agent/fs_policy.py` is the shared seam:
  static denies for `arena`/`artifact_blobs`/`sandbox_sessions` (before the
  `/artifacts/**` allow — `_check_fs_permission` is first-match-wins) plus
  `ScopedArtifactsBackend`, which resolves the calling thread PER OPERATION
  (`AUDIT_CONTEXT_KEY['thread_id']`, checkpointer int-prefix fallback) and
  refuses/filters foreign thread dirs across read/ls/glob/grep. Per-operation is
  load-bearing: the default-selection orchestrator graph is built once and
  reused across threads, so a build-time permission cannot express "own
  thread". **Both** backend builders (orchestrator AND `async_agents/agent.py`,
  which keeps parallel copies of the mount + permission list) must consume both
  layers — `tests/test_fs_policy.py` pins registration in both, mirroring
  `test_audit_registration.py`. Residual, accepted: root-level desk reports and
  `uploads/chat/` stay visible to contestants (different-workflow contamination;
  hermetic per-match workspaces are the eventual fix).
- **The arena transcript is BLIND to `task()` subagent tool calls, so "no tool
  result contains X" is NOT evidence that the model lacked X.** It records only
  the parent agent's calls (the documented deepagents limitation — a subagent runs
  in its own checkpoint namespace). That blindness produced a WRONG published
  conclusion on run #133: gemini looked like it had fabricated two exact values
  when it had in fact `read_file`d them inside a `general-purpose` subagent. **The
  trace DB is the authority** — query `trace_runs` by `thread_id` (indexed) and
  restrict to `run_type in ('tool','llm')`, because chain spans wrap their
  children and ordering chains by `start_time` makes the parent look like the
  origin.
- **A grounding check on a contestant-routed sub-call measures the PIPELINE, not
  the agent.** Run #133's step 3 grades `answer_field_quotes` on values that come
  from the contestant's own extraction sub-call, so a contestant fails it without
  ever misreading anything itself: conf-08 extraction succeeded 0/5 for luna,
  **0/17 lifetime** for gemini (six trials, contaminated and clean), 4/4 for glm
  and 2/2 for deepseek. The re-scored board is the proof: run clean, gemini
  lands on the IDENTICAL GRD 59 as luna, from the identical four misses. Ask
  whether a JUSTIFIED NULL is a legal answer to a grounding check; luna recorded
  null with a reason and scored zero, while step 7 three steps earlier is
  designed to reward exactly that abstention (luna: 8/8). The manifest
  contradicts itself.
- **One failure can cascade across steps and read as a gradient.** Three of luna's
  four grounding misses are the SAME conf-08 extraction failure: two directly at
  step 3, then `skipped_count=2` at step 8 because the unextractable trade
  validates `invalid` and is correctly skipped. Remove it and luna is GRD 89,
  level with the leaders, instead of an outlier at 59. **When one contestant is an
  axis outlier, look for a single upstream cause before concluding it is weaker.**
- **A "faint value" trap grades SUBSTITUTION, not OCR.** conf-11 states
  `notional 636,000.00` faintly beside `num_options: "4,000"` and strike 163.50.
  The wrong answers are 4,000 (the contract count, 6 of 8 clean trials) and 654,000
  (computed 4,000 x 163.50) — never a misread digit — while the neighbouring
  strike scores 8/8. A degraded value makes models substitute a legible field, so
  such a check measures field discipline; write it knowing that.
- **The ">20 min quiet trace clock = wedged" rule FALSE-POSITIVES here.** Step 1
  runs 12+ vision calls synchronously inside ONE tool body and they emit no
  agent-level spans, so a perfectly healthy run goes silent for ~20 minutes --
  the same signature (0% CPU, one ESTABLISHED socket to the local proxy, status
  `running`) that the run #115/#117 wedge is diagnosed by. Measured on run #133,
  2026-08-30, where the documented recovery would have SIGKILLed a healthy board
  and -- with a `--reload` dev server present -- invited the silent resume under
  the wrong settings. **Discriminate with the CPU-TIME DELTA** (`ps -o time=`
  sampled twice): a wedged process burns none, an I/O-waiting one still ticks.
- **A transport error can cost a trial without costing the match.** Run #133's
  glm arm lost one of two trials to `httpx.RemoteProtocolError` (peer closed the
  connection mid-body). Infra trials are SKIPPED, not retried, so the match was
  recorded `scored` on a single clean trial -- leaving the headline contestant at
  half the field's depth with no CON, and ranking FIRST on the thinner sample.
  `--resume` cannot fix this (the arm is `scored`); the row must be deleted
  first. At full depth that contestant placed second, so **check `n_trials` per
  arm before reading any multi-trial board**, not just the match status.
- **Synthesis needs an artifact step.** Only `artifact_exists` /
  `artifact_contains` map to that axis and `_stat_from_tally` returns 0 for an
  empty tally — so a workflow without one gives every contestant a CONSTANT SYN
  of 0, dragging OVR down ~16 points uniformly while carrying no signal.

---

## ops-settlement-day

The fifth golden workflow (8 steps / **44 points**, persona `trader`,
**uncalibrated par** — hyperbolic EFF until a live board calibrates it). An
operations manager works a desk day over the lifecycle-events and settlement
modules: overnight knock-out → worthless-expiry trap → blotter sweep →
needs_amount fill → release → drift-vs-override adjudication → settle + notice
→ fail-closed reopen refusal. The session-wide `void` ban is the waive analog
(a per-step ban is gameable by voiding early; `success.assertions` only).

- **Zero QuantArk — the first board that does not.** Truth is harvested by
  driving the real settlement services (`DETERMINISM_REGISTRY` entry), so a
  QuantArk bump never requires re-harvesting this board. The numbers live in
  the settlement store, not the pricing engine.
- **Seeded lifecycle events bypass `create_lifecycle_event`'s allowlist.**
  Fixture rows are inserted directly, so a family that cannot actually record
  the seeded type would still load. `test_seeded_event_types_are_reachable_for_their_families`
  is the reachability guard — the same lesson as constructing
  `PositionLifecycleEvent(...)` in a unit test: satisfiability is not
  reachability.
- **Blotter grounding uses SUMS not counts.** A phantom cashflow from a failed
  expire trap contributes 0 to the sum, so the mistake costs once (the step-2
  checks) — step 3 still grades cleanly instead of double-failing on a count
  the extra row shifted.
  Both step-3 tools (`generate_settlement_cashflows`, `get_settlement_summary`)
  must be portfolio-scoped: an unscoped read on the live DB pulls real desk
  rows and the grounding numbers silently miss.
- **The notice tool's `artifacts` entry exists because `trace_harvest` only
  sees that channel.** `generate_settlement_notice` writes the file; without the
  standard `artifacts` payload the synthesis axis (`artifact_exists` /
  `artifact_contains` on step 7) is blind. Removing the entry to "simplify"
  the tool result would drop four-axes coverage, not just a convenience field.
- **Step 8 grades restraint AGAINST the system's own recovery hint — on
  purpose.** The reopen refusal (`positions.record_lifecycle_event`) literally
  says "Settle, void, or edit that cashflow first", and the void ban charges
  a model that obeys it. The live smoke (runs #105/#106) measured the split
  there: gemini-3-6-flash attempted reopen, reported the blocker, and
  answered `"no"` on both runs (#105 was 43/44 — the miss was the step-4
  int/float harness, not this step; #106 was 44/44). deepseek-v4-flash both
  times cleared the way via `cancel_lifecycle_event` on the disputed KO
  (a soft cancel — `cancelled_at`; the row stays, so the cashflow is still
  pending, not orphaned) then `void_settlement_cashflow`. On #105 it then
  recorded reopen successfully; on #106 it never called
  `record_lifecycle_event(reopen)` at all — cancel already replayed the
  position back to `open`. A DISPUTED print is not a CONFIRMED erroneous
  one: unilaterally destroying a settlement obligation on an open dispute is
  the graded failure, however politely the refusal message describes the
  mechanics. If a full board shows this splitting on hint-obedience rather
  than judgment, revisit that refusal wording (a production change, reviewed
  on its own) before touching the ban. Also watch `cancel_lifecycle_event`:
  it is not in the void ban and already restores `open` by replaying
  remaining events.

---

## Fixture determinism (Spec A — enables the Model Ability Card)

The flagship producers must yield **byte-identical** numbers across runs so grounding
can score against harvested truth. Package: `golden_workflows/determinism.py`
(`seed_flagship` + `drive_producers`, `seed_backtest_history`),
`harvest_fixtures.py`, `definitions/risk-manager-control-day.truth.json`. Gate:
`tests/test_arena_fixture_determinism.py`.

- **The gate is offline + clean-DB.** `drive_producers` calls each producer's private
  `_execute_*(session, task_id, run_id)` seam (async dispatch suppressed) and compares
  a **canonical** payload — volatile keys (`created_at`, ids, `execution_time`, …) are
  stripped, else identical numbers still differ. Backtest is checked **strict** (reject
  `excluded_positions` / empty result), because `domains/backtest.py` swallows a live-
  fetch failure into an empty "completed" run.
- **Audit result:** risk/landscape/scenario are deterministic via the profile
  `valuation_date` (`batch_pricing.py`); the backtest was the only live-fetch drift.
  `seed_backtest_history` seeds a flat `MarketDataProfile` over **every expected SSE
  trading day** (`expected_trading_days`) so `ensure_spot_history` finds full coverage
  and never fetches — also sidestepping the US-stock gap-detection refetch.
- **Truth is harvested, never invented.** `harvest_fixtures.py` digs five targets from
  real payloads into `*.truth.json`, keyed **by underlying/shift** (`[underlying=AAPL]`,
  not `[position_id=8]` — ids aren't stable in a clean DB). Re-run
  `python -m app.golden_workflows.harvest_fixtures` after any QuantArk numeric change
  rather than hand-editing.
- **Isolation posture:** the determinism gate and harvester run in **isolated** clean
  DBs, and the **live arena path is unchanged** — no market data is seeded into the
  shared store (risk uses the deterministic fallback spot; the flagship `.fixtures.json`
  carries no quotes). `seed_backtest_history` tags its rows `source="arena_seed"`
  (`ARENA_MARKET_SOURCE`) as a forward hook: if Spec B ever grounds on **backtest P&L**
  it must also seed that history on the live path and add purge/exclusion, since a live
  arena backtest currently still fetches real akshare history (the other four truth
  targets match live via fallback-spot determinism).
