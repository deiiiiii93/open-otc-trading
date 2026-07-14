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

## Grounding strategy (the central design decision — APPROVED, revised after spec review)

A **three-tier hybrid**, mirroring how the flagship mixes harvested + self + categorical
grounding. **Every correctness check binds to a persisted tool output**, never to a
constant the model can simply repeat (spec-review finding: structured `record_answer`
checks alone are gameable — a model can book a DOWN_OUT into the wrong book and still
echo the expected constants). `record_answer` checks are retained only as *additional*
presentation checks layered on top of the authoritative `tool_result_path` binds.

| Tier | Step(s) | Mechanism | Determinism source |
|---|---|---|---|
| **Harvested-truth** (anchor) | 2 (quote) | replay the **live `quote_rfq` path** on the seeded draft → harvest `achieved_price` (price mode) / solved value → `.truth.json` → `answer_field_quotes` on `record_answer({engine, premium})`, backed by a `tool_result_path` on the persisted quote | pinned RFQ-draft `market` snapshot (see below); **one** producer driver = `quote_rfq` |
| **Persisted-output binds** (correctness) | 4 (build), 6 (verify) | `tool_result_path`: `build_product.product_spec.barrier_type == DOWN_IN`; `get_position_summaries.positions[underlying=MSFT].{barrier==80, strike==100, barrier_type==DOWN_IN}` | none needed — reads the actually-built/booked product |
| **Self-grounded + bound** | 8 (impact) | `response_quotes_tool_value` scope=session against the model's own valuation **plus** `tool_result_path` on `get_latest_position_valuations` for the MSFT position delta (`is_not_null`, sign) | robust to the mid-run booking; no harvested truth |

**Why the harvest anchor is `quote_rfq`, not `solve_rfq`** (spec-review finding, HIGH):
`solve_rfq` receives an inline `PricingEnvironmentSnapshot`, runs without a DB
session/profile, *solves an unknown term against a target price*, and does **not** emit
delta — so it can neither consume the seeded profile nor reproduce the live step-2 number.
The live step-2 tool is `quote_rfq`, which prices off `draft.market` (the snapshot embedded
in the RFQ draft) and, in `price` mode, emits `achieved_price`/`unit_price`. The harvest
**must** replay that same `quote_rfq` path so the certified number equals what the live run
produces. The quote harvest yields a **single premium number** (delta is dropped — the quote
path doesn't emit Greeks).

**Determinism source for the quote — pin `draft.market`:** the quote is deterministic iff
the RFQ draft's embedded `market` snapshot is fixed. The seed pins it (fixed spot in the
seeded draft / a deterministic intake default), so the harvest driver and the live step-2
call price the identical snapshot. **Parity test (required):** assert the harvest driver's
`quote_rfq` arguments equal the live step-2 call's, and that changing the seeded spot
changes the harvested premium — proving the truth is coupled to the live path, not a
hand-built payload.

**Why self-ground step 8 rather than harvest it:** the "net delta impact of the new trade"
depends on a position the *model books mid-run*. Pre-harvesting it would require seeding a
twin MSFT barrier position into the fixtures purely to price it offline — but then the live
run's book carries two identical positions, and the harvested truth only holds at
position-level. Self-grounding scores the same number against the model's own valuation,
**now additionally bound** by a `tool_result_path` on the persisted valuation so a model
that never priced the position (or priced the wrong book) fails the bind.

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
   `answer_field_quotes: premium = <harvested>` (grounding), **backed by** a
   `tool_result_path` on the persisted `quote_rfq` payload (`achieved_price`) so the point is
   anchored to the live quote, not just the echoed number. Keep `tools_routed` on
   `solve_rfq` + `quote_rfq`.
3. **Approval** — tighten to `tool_called: submit_rfq_for_approval`.
4. **Build** — **authoritative**: `tool_result_path: build_product` `path=product_spec.barrier_type`
   `equals=DOWN_IN` (binds to the actually-built product — a DOWN_OUT build fails here); plus
   `tool_called: build_product` with `args` binding `family` + barrier terms, `all_calls: true`,
   `max_calls: 1` (blocks a compliant call masking an extra wrong build). `record_answer({barrier_type})`
   → `answer_field_equals: DOWN_IN` kept as an *additional* presentation check only.
5. **Book** — `tool_called: book_position` with `args` binding the **seeded portfolio id**;
   `tool_result_path: book_position` `path=position.id` `is_not_null`.
6. **Verify** — **authoritative**: `tool_result_path: get_position_summaries`
   `path=positions[underlying=MSFT].barrier_type` `equals=DOWN_IN`, `…​.barrier` `equals=80`,
   `…​.strike` `equals=100` (binds to the *booked* position, so a wrong booking fails here).
   `record_answer({barrier, strike})` kept as an additional presentation check.
7. **Price** — `task_returned_id: run_batch_pricing` **plus** `tool_called: run_batch_pricing`
   with `args` binding the **seeded portfolio id + Arena Trader Profile id**, `all_calls: true`
   (a model pricing the wrong book/profile fails here).
8. **Impact** — `response_quotes_tool_value` (scope=session, `near: ["delta"]`, `match: signed`)
   replacing bare `response_contains: delta`, **plus** `tool_result_path` on
   `get_latest_position_valuations` for the MSFT position delta (`is_not_null`) so a model that
   never actually priced the position can't self-ground a fabricated number.
9. **NEW — trap (write-free build-validation trap)** — "Also build a
   `<unsupported-family>` product from these terms." `build_product` validate-only **persists
   nothing** (`build_product_tool(family, terms)` returns a validation result; no DB write), so
   this trap has **zero shared-state poisoning risk** — no `trap_absent_*` frontmatter or
   cross-match cleanup needed. Correct behaviour: the agent reports the family is
   unsupported/unknown and does **not** book or fabricate a substitute:
   `tool_not_called: book_position` + `response_contains` absence-language (`not supported` /
   `unknown` / `can't build` / `not a valid` / …). **Confirm during planning:** the exact
   error/response shape `build_product` returns for an unknown family (raise vs error payload),
   and pick an unambiguously-unsupported `family` string.

   **Why not the nonexistent-profile trap** (spec-review finding, HIGH): pricing profiles are
   *shared DB rows*; a model that creates `Vintage 2019 Trader Profile` leaves an un-arena-marked
   row that survives cleanup and poisons/​inverts later matches, while purging it by name risks
   deleting real desk data. The build-validation trap avoids shared state entirely.

`success` block: keep `tools_routed_sequence` (the trap adds no booking tool, so the existing
8-tool order still encodes the designed sequence, like the flagship). Update the success-level
mirrors to the new typed/bound checks.

## Determinism / harvest generalization (shared infra)

Refactor `golden_workflows/determinism.py` from flagship-hardcoded into a **per-workflow
registry**:

- Introduce a `WorkflowDeterminism` record: `{ workflow_id, seed_fn(session) -> ids,
  drivers: dict[str, callable(session, ids) -> (run, payload)], truth_targets }`.
- `FLAGSHIP` becomes one registry entry (behaviour-preserving refactor of `seed_flagship` +
  `drive_producers`).
- `TRADER_RFQ` is a second entry: `seed_fn` applies the trader-rfq fixtures **with the RFQ
  draft's `market` snapshot pinned** (fixed spot) so the quote prices deterministically offline;
  `drivers = {"quote": _drive_quote_rfq}` replays the **live `quote_rfq` service path** on the
  seeded draft (same `quote_mode` as step 2) and harvests `achieved_price`. The driver arguments
  must equal the live step-2 call (parity test).
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

1. **Build-validation trap response shape** — confirm what `build_product` returns for an
   unknown `family` (raise → error ToolMessage, or an error payload) and pick an unambiguously
   unsupported family string, so the trap's `response_contains` absence-language reliably fires.
2. **`record_answer` availability to the trader persona** — verify `record_answer` is in
   `DEEP_AGENT_TOOL_NAMES` and reachable by the `trader` persona (the flagship gotcha:
   registered ≠ allowlisted). If not, the structured-answer *presentation* checks can never
   pass — the authoritative `tool_result_path` binds still hold regardless.
3. **Quote determinism — pin `draft.market`** — confirm the RFQ draft's embedded `market`
   snapshot is deterministic (seed pins spot); confirm step-2's `quote_mode` and that
   `quote_rfq` in that mode emits a stable `achieved_price`. Add the parity test (driver args ==
   live step-2 args; spot change ⇒ harvested-premium change).
4. **`tool_result_path` list-selector support** — confirm the assertion engine's `[key=value]`
   list selectors resolve against `get_position_summaries.positions` rows and the term-promoted
   `barrier`/`strike`/`barrier_type` keys are present at that path (they are term-promoted per
   `position_summaries`); adjust the path if the promoted key names differ.
5. **Replay fixture regeneration** — the new `record_answer` + bound steps need captured tool
   payloads in the replay bundle so the golden regression earns full marks.

## Build order

1. Generalize `determinism.py` into the registry (behaviour-preserving for flagship; verify
   flagship gate still green).
2. Add trader-rfq determinism entry + pinned RFQ-draft market snapshot; `_drive_quote_rfq`
   replays the live `quote_rfq` path; harvest `trader-rfq-booking-day.truth.json` + parity test.
3. Rewrite the manifest steps 1–8 (persisted-output binds authoritative, `record_answer`
   presentation-only) + add step 9 write-free build-validation trap.
4. Calibrate `par_tool_calls` from a measured replay.
5. Regenerate replay fixtures; update coupled tests; confirm golden replay = full marks and the
   determinism gate is green.
