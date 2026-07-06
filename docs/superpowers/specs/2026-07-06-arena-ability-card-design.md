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
  for these steps; the "did you call the tool here" bit survives only as a PRC
  input (`tool_called` / `tools_routed_sequence`), at 0.10 weight. This is the
  point-2 fix: the smart shortcut posts a high GRD and a lower PRC — both true,
  neither a failure.

- **B4 — EFF is correctness-gated.** `C` = fraction of (GRD+ADH+SYN) checks
  passed; `par` = designed signature-tool count (7 for the flagship, declared in
  the manifest so it is not a magic literal); `actual_calls` = total tool calls in
  the transcript (already summed by `diagnose_heuristic`, `scoring.py:315`).
  `min(1, par/actual)` caps efficiency at 1 so being lean is "not penalized for
  bloat," while redundant/duplicate dispatches scale it down. Because EFF
  multiplies by `C`, a do-nothing transcript (0 calls, low correctness) scores low
  EFF — gaming by omission is impossible.

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

- **B8 — Legacy rows are derived, never migrated.** Runs #1–#11 stored `axes`
  already, so GRD/ADH/SYN/PRC recompute on read; EFF uses the stored tool-call
  count; JDG from the judge block or `—`. A row missing `axes` (very old) shows an
  OVR from `objective` alone with a "legacy" marker. No DB migration.

## Architecture

**`services/arena/scoring.py`**
- Add `ability_card(transcript, loaded) -> dict` computing the five stats from the
  same `_evaluate_objective` axes, EFF from `diagnose_heuristic` counts + manifest
  `par`, OVR from B2 weights, and the B6 position. Reuse existing evaluation — one
  pass, no re-scoring.
- Add `card_tiebreak_key(stats)` mirroring `objective_tiebreak_key`.
- `objective_breakdown` gains the `card` block (B7).

**`golden_workflows/schema.py` + `assertions.py`**
- New assertion `response_quotes_value` (fields: `value`, `match`, `near`,
  `rel_tol`) reusing `_quote_value_in_text`. Register in `_AXIS_BY_TYPE` as
  `grounding`.
- Manifest gains an explicit `par_tool_calls` frontmatter field (e.g. `7` for the
  flagship) so EFF's `par` is self-documenting per-workflow data, not a literal and
  not coupled to the success-block internals.

**`services/arena/store.py`**
- `leaderboard` sorts by `card_mean.ovr`; shared-rank + stat-priority tie-break
  (B5); returns `card_mean` alongside existing means. Subjective aggregation
  (opt-in provenance) unchanged.

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
  GRD→…→PRC; JDG never affects rank; legacy row (axes present, no card) derives a
  card on read (B8).
- **`test_flagship_loads`:** `par_tool_calls` present; `response_quotes_value`
  targets are the harvested (Spec A) numbers.
- **Frontend `Arena.live.test.tsx`:** card renders OVR + six stats + position;
  jury-off row greys JDG and excludes it from OVR; leaderboard headline is OVR.

## Out of scope

- Spec A's determinism/fixtures/harvest (its prerequisite).
- Deleting procedural or jury signals — both retained, demoted (B1/B7).
- Pairwise/Elo (still deferred).
- Re-scoring historical runs into new DB columns (derived on read, B8).
- The `goal_*` RubricMiddleware subsystem (unrelated "rubric" concept).
