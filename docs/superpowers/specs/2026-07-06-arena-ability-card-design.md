# Arena Model Ability Card — 6-stat capability scoring + OVR

**Date:** 2026-07-06
**Status:** design
**Sub-project:** 2 of 2. Depends on
`2026-07-06-arena-fixture-determinism-design.md` (Spec A) for reproducible
fixture truth values.
**Builds on:** `2026-07-06-arena-jury-opt-in-design.md` (subjective demoted to
advisory) and `2026-07-05-arena-judge-fairness-design.md` (objective axes).

## Problem

The current objective score is a flat `+1` per manifest check summed to one
0–100 number, ranked by `mean_objective` (`scoring.py:245-270`,
`store.leaderboard`). It measures **procedural fidelity** — did the model read
the prescribed skill, call the prescribed tool, in the prescribed order — and
that mislabels smart capability as failure in two ways the user identified:

1. **Skill-reading is a false gate.** Each step with `expected_skill` earns a
   point only when the model *opens that SKILL.md* (`skills_routed`,
   `scoring.py:181-192`). Opus 4.8 / DeepSeek V4 Pro that call
   `get_latest_risk_run` directly and correctly report staleness lose the point
   for *not reading a file they didn't need*. The codebase already retreated from
   this signal — steps 3/5/8 are `expected_skill: null`, and the success block
   swapped `skills_routed_sequence` for `tools_routed_sequence` — but 6 live skill
   points remain.

2. **Context-utilization is penalized.** A model that already fetched the AAPL
   delta and answers the hotspot question **from context** — the efficient,
   token-saving move — fails the step's tool expectation and its self-grounded
   number check, even though the answer is correct (Spec A problem statement).

The deeper issue: a single blended number cannot express *"nailed the numbers,
skipped the ceremony."* The user's framing — a **FIFA-style ability card** —
resolves it: report several capability dimensions as 0–99 stats, derive an
overall (OVR), rank on OVR, and let procedure be a *visible low-weight stat*
rather than a pass/fail gate. This mirrors the jury reform: nothing is deleted;
signals are demoted and made transparent.

## Decisions

- **B1 — Six stats, each a 0–99 rating from its axis pass-rate.** Reuse the axis
  tallies `objective_breakdown` already emits (`axes: {axis:{passed,total}}`,
  `scoring.py:285-291`). `stat = round(99 * passed/total)` (0 when `total==0`).

  | Stat | Axis / check types | Role |
  |---|---|---|
  | **GRD** Grounding | `response_quotes_value` (fixture truth, Spec A) + `tool_result_path` | Right numbers |
  | **ADH** Adherence | `tool_called` (args/dates/scenario-set), `tool_not_called` (traps/no-substitute/no-recompute), `response_contains` | Instruction discipline |
  | **SYN** Synthesis | `artifact_exists`, `artifact_contains` | Report quality |
  | **PRC** Procedure | `skill_routed`, `tools_routed_sequence`, `task_returned_id` | Workflow fidelity (demoted) |
  | **EFF** Efficiency | `C × min(1, par/actual_calls) × 99` | Outcome-per-cost |
  | **JDG** Judgment | jury score (opt-in) | Advisory only |

- **B2 — OVR = numbers-first weighted mean of five stats.**
  `OVR = round(0.32·GRD + 0.26·ADH + 0.16·SYN + 0.16·EFF + 0.10·PRC)`.
  **JDG is never in OVR** (advisory, exactly like the opt-in jury). Weights sum to
  1.00 and encode the user's stated priority: numbers dominate, adherence close
  behind, procedure lowest, efficiency a meaningful 16%.

- **B3 — GRD credits correct-from-context via fixture truth.** A new assertion
  `response_quotes_value` carries a literal `value:` target (harvested per Spec A)
  and keeps the existing `match: signed|magnitude`, `near:`, and `rel_tol`
  semantics from `_quote_value_in_text` (`assertions.py:165-183`). It scans the
  response for the known-truth number **regardless of whether the tool fired that
  turn**. The retired `response_quotes_tool_value` self-grounding path is replaced
  for these grounding steps. The "did you call the tool *this turn*" signal
  survives only as **procedural** input — the per-step `expected_tools`
  `ToolExpectation` (axis `procedural`) and the session `tools_routed_sequence` —
  at PRC's 0.10 weight. **`tool_called` is NOT demoted:** it stays an **ADH**
  check (per the B1 table and `_AXIS_BY_TYPE`), because existing manifests use it
  for *argument/instruction* adherence (exact backtest dates, the market-crash
  scenario set, `exclusive_keys`/`all_calls`/`max_calls`), which is discipline,
  not ceremony. This is the point-2 fix: the smart shortcut posts a high GRD and a
  lower PRC — both true, neither a failure — while a *wrong-argument* call still
  loses its full-weight ADH point.

- **B4 — EFF is correctness-gated.** `C` = fraction of (GRD+ADH+SYN) checks
  passed; `actual_calls` = total tool calls in the transcript (already summed by
  `diagnose_heuristic`, `scoring.py:315`). `EFF = round(C × min(1, par/actual) × 99)`,
  and `min(1, par/actual)` caps efficiency at 1 so being lean is "not penalized for
  bloat," while redundant/duplicate dispatches scale it down. Because EFF
  multiplies by `C`, a do-nothing transcript (0 calls, low correctness) scores low
  EFF — gaming by omission is impossible.

  **`par` must count a *complete* compliant run, not just signature tools.** A
  fully-correct flagship run calls the four retrieval/library tools too
  (`get_greeks_landscape_run`, `get_scenario_test_run`, `get_backtest_run`,
  `list_scenario_library`) on top of the 7 signature tools — ~11 calls. A `par` of
  7 would cap even a perfect lean run's efficiency ratio at `7/11≈0.64`, which
  contradicts "lean is not penalized." So `par` = **the designed tool-call count**,
  computed as `sum(len(step.expected_tools) for step in workflow.steps)` (= 11 for
  the flagship). This derivation is the **default**; a workflow MAY override it
  with an explicit `par_tool_calls` frontmatter field when its designed count
  differs from the `expected_tools` sum.

- **B4a — `par_tool_calls` is optional; existing workflows are untouched.** Adding
  a *required* field would fail-load every current schema-v1 manifest
  (`trader-rfq-booking-day`, `high-board-portfolio-review-day`, …), none of which
  declare it. So the schema field is `int | None = None` (validated `≥ 1` when
  present). When absent, `par` uses the B4 `expected_tools`-sum derivation — always
  defined for any manifest — so EFF/OVR/`card_mean` are never implementation- or
  registry-order-dependent. The flagship sets `par_tool_calls: 11` explicitly so
  the number is self-documenting and decoupled from a future `expected_tools` edit.
  A registry-wide load test asserts every shipped workflow still parses.

- **B5 — Ranking by OVR; stat-priority tie-break.** `store.leaderboard` sorts by
  **OVR mean** across trials, shared ranks on exact ties, broken by stat pass-rate
  priority **GRD → ADH → SYN → EFF → PRC** (never JDG) — a direct reuse of
  `_AXIS_TIEBREAK_PRIORITY` (`scoring.py:75`). `mean_objective` is retained in the
  payload for continuity but is no longer the sort key.

- **B6 — Card position/archetype (presentation only).** Derive a label from the
  dominant-stat profile: *Sniper* (GRD-led), *Anchor* (ADH/PRC-led), *Playmaker*
  (EFF/SYN-led), *All-rounder* (flat). Pure display; zero ranking effect. Cheap
  discrimination signal that reads at a glance.

- **B7 — `score_breakdown` gains a `card` block; nothing removed.** The 39 checks
  stay the source of truth; the card is *derived*. New structure:
  `card: {ovr, stats:{GRD,ADH,SYN,PRC,EFF}, jdg, position}` on the per-match
  breakdown, plus `card_mean` aggregates on the leaderboard. `objective`, `axes`,
  `subjective_mode`, and the jury block are unchanged.

- **B8 — Legacy rows are derived where possible, otherwise flagged uncarded —
  never migrated.** Reality check (Codex, against `data/open_otc.sqlite3`): only
  `arena_match` rows for **runs #10–#11** persisted `score_breakdown.objective.axes`;
  runs #1–#9 predate the axis breakdown and have **no** axes to derive from. So:
  a row **with** stored `axes` recomputes GRD/ADH/SYN/PRC on read, EFF from the
  stored tool-call count + the workflow's `par`, JDG from the judge block or `—`.
  A row **without** `axes` cannot produce honest stats — it is marked
  `card: null` with a `"legacy_no_axes"` reason, shows its stored `objective`
  mean only, and is **excluded from `card_mean` and OVR ranking** (listed but
  uncarded) rather than silently zero-filled (which would fabricate a bottom-rank
  OVR for a row we simply can't score). No DB migration; no backfill in this spec.
  A `store` test asserts a scored axes-less row is excluded from `card_mean` and
  still appears in the board.

## Architecture

**`services/arena/scoring.py`**
- Add `ability_card(transcript, loaded) -> dict` computing the five stats from the
  same `_evaluate_objective` axes, EFF from `diagnose_heuristic` counts + manifest
  `par`, OVR from B2 weights, and the B6 position. Reuse existing evaluation — one
  pass, no re-scoring.
- Add `card_tiebreak_key(stats)` mirroring `objective_tiebreak_key`.
- `objective_breakdown` gains the `card` block (B7).

**`golden_workflows/schema.py` + `assertions.py`**
- New assertion `response_quotes_value` (fields: `value: float`, `match`, `near`,
  `rel_tol`, `scope`) reusing `_quote_value_in_text`. Register in `_AXIS_BY_TYPE`
  as `grounding`. `_AXIS_BY_TYPE` is the **single authoritative** axis map (B1
  table mirrors it exactly); no assertion type appears in two axes.
- `GoldenWorkflow` gains an **optional** `par_tool_calls: int | None = None`
  (validated `≥ 1` when present) — absent on every existing manifest, so they all
  still load. `scoring.py` exposes `designed_par(workflow)` returning
  `workflow.par_tool_calls` if set else `sum(len(s.expected_tools) for s in
  workflow.steps)` (= 11 for the flagship). The flagship manifest sets
  `par_tool_calls: 11` explicitly for self-documentation.

**`services/arena/store.py`**
- `leaderboard` sorts by `card_mean.ovr`; shared-rank + stat-priority tie-break
  (B5); returns `card_mean` alongside existing means. Rows whose matches all lack
  `axes` (`card: null`, B8) contribute **no** OVR — they are aggregated for
  `mean_objective`/counts only and sort after carded rows (uncarded rank last),
  so a missing-axes legacy row never fabricates or distorts an OVR ranking.
  Subjective aggregation (opt-in provenance) unchanged.

**`routers/arena.py`** — surface `card` per match and `card_mean` per board
(additive; existing fields retained).

**Frontend (`Arena.live.tsx`, `arenaApi.ts`, `Arena.css`)** — render the ability
card: large **OVR**, a six-spoke hexagon/radar of the stats, the position badge,
JDG greyed when jury off. Leaderboard headline column becomes OVR (objective mean
kept as a secondary/drilldown column). Token-only styling per `frontend/CLAUDE.md`
— no new hardcoded colors; stat tiers map to existing tokens. The
`ScoreBreakdownView` drilldown adds the per-stat check breakdown under each stat.

## Failure handling

- **Jury off:** JDG renders `—`, absent from OVR (B2) — no ranking impact.
- **`total==0` for an axis** (e.g. a workflow with no synthesis checks): that stat
  is 0 and its OVR weight still applies; document per-workflow so a
  synthesis-less workflow doesn't look "broken." (The flagship has all five.)
- **Blank / invalid transcript:** unchanged upstream — `invalid` (infra_blank) and
  infra-contamination gating still exclude the match before carding; a real scored
  0 produces an all-zero card honestly.
- **Scored row with no stored `axes` (runs #1–#9):** cannot derive stats, so
  `card: null` / reason `"legacy_no_axes"` — listed with its `objective` mean but
  excluded from `card_mean`/OVR ranking (B8). Distinct from an all-zero card, which
  is a row we *could* score and it genuinely earned zero.
- **`actual_calls == 0` with high C** (pure fixture-context answers, no tools):
  `par/0` guarded → EFF uses `min(1, ...)=1`, so a legitimately tool-free correct
  run gets full efficiency; but C caps it, and tool-requiring axes (task ids,
  computed CVaR) pull C down if nothing was actually computed.

## Testing

- **`test_arena_scoring`:** `ability_card` returns five stats in 0–99, OVR = the
  B2 weighted mean, JDG excluded from OVR; the 39-check denominator and axis
  tallies are unchanged (card is derived).
- **GRD context-credit:** a transcript that quotes the fixture truth number with
  **no** tool call that step passes `response_quotes_value` (the point-2
  regression) and posts high GRD / lower PRC.
- **EFF:** (a) correct + lean (`actual ≤ par`) ⇒ EFF≈`C·99`; (b) correct + double
  every call ⇒ EFF halves; (c) do-nothing ⇒ low C ⇒ low EFF (no gaming).
- **`test_arena_store`:** leaderboard ranks by OVR mean; exact-OVR tie broken by
  GRD→…→PRC; JDG never affects rank; a scored row **with** `axes` derives a card on
  read; a scored row **without** `axes` (runs #1–#9 shape) is `card: null`,
  excluded from `card_mean`/OVR ranking, still listed with its `mean_objective`
  (B8).
- **Registry load:** every shipped workflow (`get_all_workflows()`) still parses
  with the new optional `par_tool_calls` field — none is forced to declare it.
- **`test_flagship_loads`:** `par_tool_calls == 11` and equals `designed_par`;
  `response_quotes_value` targets equal the harvested (Spec A) `truth.json` numbers;
  the 39-check denominator and axis tallies are unchanged (card is derived).
- **Frontend `Arena.live.test.tsx`:** card renders OVR + six stats + position;
  jury-off row greys JDG and excludes it from OVR; leaderboard headline is OVR.

## Out of scope

- Spec A's determinism/fixtures/harvest (its prerequisite).
- Deleting procedural or jury signals — both retained, demoted (B1/B7).
- Pairwise/Elo (still deferred).
- Re-scoring historical runs into new DB columns (derived on read, B8).
- The `goal_*` RubricMiddleware subsystem (unrelated "rubric" concept).
