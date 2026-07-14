# Trader RFQ→Booking Day — Flagship Arena Parity

**Date:** 2026-07-14
**Status:** Approved (design)
**Sub-project:** 1 of 2 (this spec = `trader-rfq-booking-day`; sub-project 2 = `high-board-portfolio-review-day`, separate spec/plan/impl cycle)

## Goal

Bring the `trader-rfq-booking-day` golden workflow up to the same
*discrimination-benchmark* standard as the flagship `risk-manager-control-day`:
grounding-via-harvested-truth + structured answers + `par_tool_calls` golf-scoring
calibration + a trap step + a fixture-determinism gate, so it is Model-Ability-Card
gradable with a balanced grounding/adherence/synthesis/procedural axis spread instead
of the current fuzzy `response_contains`-heavy manifest.

The Ability Card scoring *kernel* is already workflow-agnostic
(`scoring.card_from_axes` derives axes from any assertion set via `_AXIS_BY_TYPE`;
`scoring.designed_par(wf)` reads `par_tool_calls` off any workflow). **No scoring-kernel
changes.** The work is (a) per-workflow manifest authoring and (b) a shared
generalization of the determinism/harvest harness, which is currently hardcoded to
`FLAGSHIP_ID`.

## Non-goals

- `high-board-portfolio-review-day` (sub-project 2).
- Any change to `scoring.py` ranking/card kernel, the jury, or the leaderboard.
- Harvesting the end-of-workflow net-delta as offline truth (see § Grounding, rejected
  alternative).

## Current gap

| Pattern element | flagship | trader-rfq (today) |
|---|---|---|
| Steps / assertions | 9 / 39 | 8 / ~14 |
| Grounding via harvested truth (`.truth.json`) | ✅ | ❌ |
| Structured answers (`record_answer` / `answer_field_*`) | ✅ | ❌ |
| `par_tool_calls` golf calibration | ✅ `24` | ❌ (uncalibrated → hyperbolic EFF) |
| Trap step (`trap_absent_*`) | ✅ | ❌ |
| Fixture determinism gate | ✅ | ❌ |
| Axis balance (grd/adh/syn/prc) | all four | mostly fuzzy `response_contains` |

## Grounding strategy (the central design decision — APPROVED)

A **three-tier hybrid**, mirroring how the flagship mixes harvested + self + categorical
grounding:

| Tier | Step(s) | Mechanism | Determinism source |
|---|---|---|---|
| **Harvested-truth** (anchor) | 2 (quote) | `solve_rfq` computes the barrier put's PV (+ delta) from static RFQ terms → `.truth.json` → `answer_field_quotes` on `record_answer({engine, premium})` | frozen spot (seeded `MarketDataProfile` + `valuation_date`); **one** producer driver |
| **Structured categoricals** | 4 (build), 6 (verify) | `record_answer({barrier_type, barrier, strike})` → `answer_field_equals` / `answer_field_quotes` | none needed — terms are inputs, not computed |
| **Self-grounded** | 8 (impact) | `response_quotes_tool_value` scope=session against the model's *own* valuation payload | robust to the mid-run booking; no harvested truth |

**Why self-ground step 8 rather than harvest it:** the "net delta impact of the new
trade" depends on a position the *model books mid-run*. Pre-harvesting it would require
seeding a twin MSFT barrier position into the fixtures purely to price it offline — but
then the live run's book carries two identical positions, and the harvested truth only
holds at position-level, adding fragility for one extra number. Self-grounding scores the
same number (sign-bound, `near`-anchored to "delta") against whatever valuation the model
itself fetched, exactly as the flagship self-grounds several checks with
`response_quotes_tool_value`. This also keeps the determinism harness **minimal (one
producer, `solve_rfq`)** vs the flagship's four.

**Rejected alternative:** full four-number harvest with a pre-seeded twin position.
Rejected for the double-book fragility above.

## Manifest rewrite (per step)

Target: keep the 8 booking steps, **add a 9th trap step**. Every fuzzy `response_contains`
that stands in for an assertion becomes a typed check; genuine presence checks (e.g.
"MSFT" appears) may stay as `response_contains`.

1. **Intake** — keep `response_contains: MSFT`; add `tool_called: create_or_update_rfq_draft`
   with `args`/`args_any_of` asserting client name = "ARENA Demo Client" and underlying MSFT
   (adherence).
2. **Quote** — replace `response_contains` with `record_answer({engine, premium})`:
   `answer_field_equals: engine = BarrierAnalyticalEngine` (adherence) +
   `answer_field_quotes: premium = <harvested>` (grounding). Keep `tools_routed`
   expectation on `solve_rfq` + `quote_rfq`.
3. **Approval** — tighten to `tool_called: submit_rfq_for_approval` (+ keep an absence/label
   presence check if useful).
4. **Build** — `record_answer({barrier_type})` → `answer_field_equals: DOWN_IN` (replaces the
   substring `DOWN_IN` match, which can hit prose). Keep the "validate only / do not book
   through RFQ" intent; add `tool_not_called: book_position` for this step if the runner
   supports per-step prohibitions here (confirm during planning).
5. **Book** — keep `tool_result_path: position.id is_not_null`.
6. **Verify** — `record_answer({barrier, strike})` → `answer_field_quotes: barrier = 80,
   strike = 100` (replaces `response_contains: "80"`, which matches "80%" noise anywhere).
7. **Price** — keep `task_returned_id: run_batch_pricing`.
8. **Impact** — `response_quotes_tool_value` (scope=session, `near: ["delta"]`, `match:
   signed`) replacing bare `response_contains: delta`.
9. **NEW — trap** — "Re-quote it against the 'Vintage 2019 Trader Profile'." No such profile
   exists. Agent must list available profiles, report the profile is unavailable, and **not**
   silently substitute the Arena Trader Profile: `tool_not_called: quote_rfq` (and/or
   `solve_rfq`) + `response_contains` absence-language (`not found` / `does not exist` /
   `not available` / …). Add the profile name to a `trap_absent_*` frontmatter list analogous
   to the flagship's `trap_absent_sets`, asserted absent at match setup by the runner.
   **Confirm during planning:** the exact profile-listing tool name (e.g.
   `list_pricing_profiles`) and whether the runner's `_assert_trap_sets_absent` generalizes to
   profiles or needs a sibling assertion.

`success` block: keep `tools_routed_sequence` (add the trap's list tool only if it has a
stable signature; the trap adds no booking tool so the existing 8-tool order still encodes
the designed sequence, like the flagship). Update the success-level `response_contains`
mirrors to the new typed checks where appropriate.

## Determinism / harvest generalization (shared infra)

Refactor `golden_workflows/determinism.py` from flagship-hardcoded into a **per-workflow
registry**:

- Introduce a `WorkflowDeterminism` record: `{ workflow_id, seed_fn(session) -> ids,
  drivers: dict[str, callable(session, ids) -> (run, payload)], truth_targets }`.
- `FLAGSHIP` becomes one registry entry (behaviour-preserving refactor of `seed_flagship` +
  `drive_producers`).
- `TRADER_RFQ` is a second entry: `seed_fn` applies the trader-rfq fixtures **plus** a frozen
  MSFT `MarketDataProfile` (+ pinned `valuation_date` on the Arena Trader Profile) so
  `solve_rfq` prices deterministically offline; `drivers = {"quote": _drive_solve_rfq}` driving
  the `solve_rfq` producer's private `_execute`/service seam under `_no_async_dispatch()` if it
  dispatches.
- `harvest_fixtures.py` and `tests/test_arena_fixture_determinism.py` iterate the registry
  instead of naming the flagship. `harvest_fixtures` writes each workflow's `*.truth.json`.
- Isolation posture unchanged: harvester + gate run in isolated clean DBs; the live arena path
  is untouched. Tag any seeded market rows `source=ARENA_MARKET_SOURCE`.

## par calibration

`par_tool_calls` for trader-rfq: **derived, not guessed.** During implementation, run the
golden replay and read `diagnosis.counts_detail.tool_calls` (which excludes `META_TOOLS` =
`task`/`read_file`/`write_todos`, so skill-file reads never inflate par), then set `par` to a
realistic *counted competent* run (expected calls + legitimate re-fetch/re-list/sanity
overhead), the same way the flagship's `24` was derived. Initial estimate ≈ **24** (≈12
expected tool calls + overhead); the plan finalizes the number from the measured replay.
Setting `par_tool_calls` opts the workflow into golf-scored EFF; leaving it unset would keep
the legacy hyperbolic curve, so it MUST be set for parity.

## Tests to update (coupling)

- `test_trader_rfq_loads` (or equivalent): the new point denominator — pin it, like the
  flagship's 39.
- Arena scoring / `test_golden_workflow_regression`: the golden replay for trader-rfq must
  still earn **full marks** (fixture-consistency gate) — regenerate replay fixtures if the new
  structured/grounding checks require captured `record_answer` payloads.
- New `test_trader_rfq_grounding_targets_match_truth_file` mirroring
  `test_flagship_grounding_targets_match_truth_file` — guards manifest grounding values against
  drift from `*.truth.json`.
- `test_arena_fixture_determinism.py`: parametrized over the registry so trader-rfq's producers
  are gated for byte-identical output.
- Any exact-set/count skill-catalog couplings only if a SKILL.md is added (none planned — the
  trap reuses existing skills).

## Risks / open items to resolve in planning

1. **Profile-listing tool + trap absence mechanism** — confirm the tool name and whether
   `_assert_trap_sets_absent` covers profiles or needs a sibling. If no clean "nonexistent
   profile" trap exists, fall back to a nonexistent-portfolio or nonexistent-instrument trap
   with the same "check → report absent → don't substitute" shape.
2. **`record_answer` availability to the trader persona** — verify `record_answer` is in
   `DEEP_AGENT_TOOL_NAMES` and reachable by the `trader` persona (the flagship gotcha:
   registered ≠ allowlisted). If not, the structured-answer checks can never pass.
3. **`solve_rfq` determinism** — confirm `solve_rfq` reads spot from the seeded
   `MarketDataProfile`/`valuation_date` and not a live fetch; if it fetches, seed coverage the
   way `seed_backtest_history` does for the flagship.
4. **Replay fixture regeneration** — the new `record_answer` steps need captured tool payloads
   in the replay bundle so the golden regression earns full marks.

## Build order

1. Generalize `determinism.py` into the registry (behaviour-preserving for flagship; verify
   flagship gate still green).
2. Add trader-rfq determinism entry + frozen market seed; harvest `trader-rfq-booking-day.truth.json`.
3. Rewrite the manifest steps 1–8 + add step 9 trap.
4. Calibrate `par_tool_calls` from a measured replay.
5. Regenerate replay fixtures; update coupled tests; confirm golden replay = full marks and the
   determinism gate is green.
