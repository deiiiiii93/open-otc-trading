# High-Board Portfolio Review — Flagship Discrimination-Benchmark Parity

**Date:** 2026-07-23
**Status:** Draft (spec review pending)
**Sub-project:** 3 of 3 — the last of the three golden arena workflows to reach
flagship parity, after `risk-manager-control-day` (the flagship) and
`trader-rfq-booking-day` (merged 2026-07-14/15, Run #33 board 2026-07-20).

## Goal

Raise `high-board-portfolio-review-day` from a shallow 6-step routing check to a
full **discrimination benchmark** on par with the other two flagships: four scored
axes (procedural / adherence / grounding / synthesis), grounding anchored on a
**harvested truth value** (not invented), a structured-answer capture path, a
**write-free over-claim trap**, a **calibrated `par_tool_calls`** so EFF is
golf-scored, a **fixture-determinism gate** so the grounding number can't drift
green, and a **mandatory pre-merge live smoke** proving every grounding path is
*live-reachable* (a canned replay earning full marks proves only satisfiability).

The persona is `high_board` — a portfolio-**oversight/governance** role, not a
pricing role. Its honest groundable evidence is (a) **structural** facts (view
membership counts, product-type breakdown) and (b) one **governed numeric risk**
figure read from a *persisted* risk run. The central design tension — and the
source of the trap — is the difference between a **governed** valuation (persisted,
audited risk run) and an **inline, ungoverned** batch figure the model reconstructs.

## Non-goals

- **No scoring-kernel change.** `services/arena/scoring.py` (`card_from_axes`,
  golf EFF, `_AXIS_BY_TYPE`, `designed_par`, `par_calibrated`) is already
  workflow-agnostic — it must not be touched. Reaching parity is a manifest +
  fixtures + determinism-registry job only.
- **No new SKILL.md.** The workflow routes to skills that already exist
  (`portfolio-membership`, `portfolio-maintenance`, `portfolio-view-counting`,
  `batch-run-reports`, `display-report`, `generate-report`). Adding a skill would
  break the exact-set catalog tests in ~6 files for no benefit.
- **No new persona.** `high_board` already exists (`personas.py::board_spec`) and
  holds the same full toolset as the other personas (differentiation is by system
  prompt only).
- **No full multi-model board this session.** The finish line is: pre-merge
  single-model live smoke (deepseek-v4-flash, DIRECT channel) + par calibration +
  merge. A Run #34-style board is a separate follow-up (as Run #33 was for
  trader-rfq).

## Current gap

| Pattern element | flagship / trader-rfq | high-board (today) |
|---|---|---|
| Steps | 9 / 10 | 6 |
| Scored axes | 4 (PRC/ADH/GRD/SYN) balanced | mostly PRC + a little GRD/ADH; **no real SYN discrimination**, thin GRD |
| Harvested grounding truth | `*.truth.json`, byte-deterministic | **none** — no truth.json |
| Structured answers | `record_answer` + `answer_field_quotes` | none |
| `par_tool_calls` (golf EFF) | 24 / 35 | **unset** → EFF stays legacy hyperbolic |
| Trap step | write-free (nonexistent set / unsupported family) | **none** |
| Determinism registry | registered, gated | **not registered** |
| Live-reachability proof | live smoke pre-merge | never live-smoked |

## Grounding strategy (the central design decision)

A three-tier hybrid, all **live-reachable** (verified in the pre-merge smoke) and
all **spot/multiplier-robust** by construction — governance grounding is on integer
structural counts and an absolute delta that is deterministic under the arena's
no-market fallback-spot path.

| Tier | Step(s) | Mechanism | Determinism source |
|---|---|---|---|
| **A. Harvested-truth anchor** | 4→5 (governed risk) | **Primary (non-gameable):** `tool_result_path get_latest_risk_run metrics.positions[underlying=NVDA].delta equals <nvda truth> rel_tol 0.02` — scored from the **actual completed-run result**, bound to the seeded desk portfolio via `portfolio_id` on both the dispatch and the read. **Secondary (presentation, race-proof):** `answer_field_quotes nvda_delta` via `record_answer`. | Harvested offline from the persisted risk run via the determinism registry; **fallback-spot** determinism (no market seeded), same path the flagship's AAPL delta rides |
| **A′. Governed valuation (trap target)** | 7 (trap) | `answer_field_quotes governed_valuation` == harvested **`desk_portfolio_valuation`** (persisted-run portfolio `market_value`), distinct from the NVDA delta and the inline batch total | Same persisted risk run, harvested offline (a second truth target) |
| **B. Persisted structural binds** | 1, 2, 3, 8 | `tool_result_path` on container `kind`, view `kind`, `total_count`/`portfolio_total_count`, prior-report `report_type` | Fixed by the seed (position count = 5; report_type marker) — integer/string, no engine float |
| **C. Self-grounded + bound** | 3 | `answer_field_quotes` on `snowball_count` / `view_total` (structural counts the model must report) | Seed-fixed integers |

Design rationales (each pre-empts a known failure mode):

- **Why NVDA, not AAPL, for the numeric anchor.** AAPL has **two** seeded
  positions (Snowball + Vanilla), so `positions[underlying=AAPL].delta` is
  ambiguous; NVDA has exactly one (`EuropeanVanillaOption`), so the underlying
  selector resolves uniquely. `[position_id=N]` is rejected — ids are not stable in
  a clean harvest DB.
- **Why the number is scored from the completed-run result, not just quoted text
  (Codex spec finding).** `answer_field_quotes` alone compares model **text** to an
  offline constant — a model could recite the harvested number without the governed
  run producing it, or read a *different* completed run. So the **primary** grounding
  bind is `tool_result_path … metrics.positions[underlying=NVDA].delta equals <truth>
  rel_tol 0.02` on the **actual** `get_latest_risk_run` result. Provenance is pinned
  two ways: (a) both `run_batch_pricing` (dispatch) and `get_latest_risk_run` (read)
  carry `portfolio_id = desk`, so the scored delta comes from a run over the seeded
  book, not a reconstructed snapshot; (b) the dispatched run must **provably** use the
  seeded pricing profile. `run_batch_pricing` takes an **optional** profile id and
  does **not** auto-resolve one from the portfolio (Codex spec finding) — an
  unprofiled dispatch would price on fallback assumptions and make the harvested
  *profiled* delta unreachable/misattributed. So the design is made **executable**,
  not deferred: **the workflow adds an explicit profile-discovery step** — the agent
  lists the desk's pricing-parameter profiles (`get_/list_` pricing profiles) and
  passes the resolved id to `run_batch_pricing` — and the plan pins the assertions
  `tool_called run_batch_pricing {portfolio_id: desk, pricing_parameter_profile_id:
  <resolved>}` **and** a verification that the completed run carries that same
  `pricing_parameter_profile_id`. Seed **exactly one** profile so discovery is
  unambiguous. **The plan must confirm the flagship's exact live risk-grounding
  mechanism** (how its `run_batch_pricing` binds the profile) and mirror it — the
  flagship is a proven live-reachable board, so a working pattern exists; the live
  smoke is the final arbiter. `answer_field_quotes nvda_delta` stays as the
  **secondary** race-proof presentation check.
- **Why the async race doesn't break the primary bind.** `run_batch_pricing`
  dispatches to a background thread and returns only a `task_id`; a `get_latest_risk_run`
  issued too early returns `found=False` (what bit trader-rfq). The step design has
  the model **poll `get_latest_risk_run` until `found=True`**; assertions read the
  **last** non-error result, so the scored delta is the completed run's. The live
  smoke is the arbiter that this actually reaches `found=True` in-match.
- **Why "latest completed run" is the dispatched run (dispatch-to-read provenance,
  Codex finding).** No cross-call id-equality assertion primitive exists in the
  engine; instead the identity is guaranteed structurally: the arena match runs on a
  **clean seeded DB with no pre-existing risk runs**, and `run_batch_pricing`
  `max_calls: 1` forbids a second dispatch — so exactly one completed run exists for
  the desk portfolio, and it additionally carries the **seeded `pricing_parameter_
  profile_id`** (nothing else in a clean DB produces a run with that profile). Latest
  == dispatched by construction. The plan should still bind the read to the dispatched
  `risk_run_id` **if** an id-addressable read or cross-call equality primitive turns
  up; otherwise the structural guarantee holds and the live smoke confirms it.
- **Why absolute delta is safe here (unlike trader-rfq's premium).** The live arena
  path seeds no market data, so risk prices against QuantArk's deterministic
  fallback spot — the absolute delta is byte-reproducible live (the flagship proves
  this for its AAPL delta). trader-rfq's premium differed only because its quote path
  *fetched real market data*; the governed-risk path does not.
- **Why never a bare int/float `equals` via `tool_result_path` on an engine number.**
  `_exact` is type-STRICT (`type(a) is type(b)`) and QuantArk stores floats — so the
  numeric anchor uses `answer_field_quotes`/`rel_tol`, never `equals`. Structural
  counts (`portfolio_total_count: 5`) are genuine ints and safe with `equals`.

## Manifest rewrite (9 steps)

Each step names the axes it earns. Exact YAML lands in the plan.

1. **Resolve the desk control book** — `get_portfolio`. PRC: `skill_routed
   portfolio-membership`, expected_skill/expected_tools. GRD: `tool_result_path
   kind equals "container"` (string, type-safe).
2. **Create the board-review view** — `create_portfolio(kind=view,
   source_portfolio_ids=[desk])`. PRC: expected_tools. ADH: `tool_called
   {kind: view}`. GRD: `tool_result_path kind equals "view"`.
3. **Count the Snowball exposure** — `get_positions(product_type=Snowball)` on the
   view. PRC: `skill_routed portfolio-view-counting`. ADH: `tool_called
   {product_type: Snowball}`. GRD: `tool_result_path total_count gte 1`,
   `tool_result_path portfolio_total_count equals 5`, plus `record_answer` →
   `answer_field_quotes snowball_count` / `view_total` (structural self-grounding).
4. **Resolve the pricing profile, run the governed risk pass** — first discover the
   seeded profile (`list_/get_` pricing-parameter profiles), then `run_batch_pricing`
   on the **desk book by `portfolio_id`** binding that profile id. PRC:
   expected_tools (profile-list + `run_batch_pricing`) + `task_returned_id` (async
   dispatch id). ADH: `tool_called run_batch_pricing {portfolio_id: desk,
   pricing_parameter_profile_id: <resolved>}`, `max_calls: 1` (no duplicate stateful
   dispatch). GRD/provenance: the completed run must carry that same
   `pricing_parameter_profile_id` (verified at step 5's read).
5. **Read governed risk, report NVDA delta** — `get_latest_risk_run(portfolio_id=desk)`,
   **poll to `found=True`**. PRC: expected_tools; ADH: `tool_called
   get_latest_risk_run {portfolio_id: desk}`. GRD (anchor, non-gameable):
   `tool_result_path metrics.positions[underlying=NVDA].delta equals <truth>
   rel_tol 0.02` on the completed-run result. GRD (secondary, race-proof):
   `record_answer nvda_delta` → `answer_field_quotes` vs `truth.json`.
6. **Inline composition summary — do NOT persist** — `run_report_batch`. PRC:
   `skill_routed batch-run-reports`. ADH: `response_contains
   [composition|breakdown|positions]`, `tool_not_called create_report`.
7. **TRAP — over-claim governed valuation.** Overseer asks to *certify the desk's
   official governed portfolio valuation for the board minutes*, immediately after
   the ungoverned inline composition summary (step 6) — tempting the model to
   rubber-stamp the inline batch figure. Competent behavior: the governed valuation
   is the **persisted risk run** (step 5), not the model-supplied inline batch total;
   the overseer reports the governed number and/or declines, and does **not** mint a
   governed persisted record. `expected_skill: null` (no skill point).
   **Scored by two deterministic signals — no self-attested prose, no LLM judge**
   (Codex spec finding; the objective axis is deterministic by arena design, so a
   semantic verifier is out of scope):
   - **ADH (tool-signature prohibition):** `tool_not_called create_report` — a model
     that "certifies" by persisting a governed record via `create_report` fails. This
     is the exact prohibition pattern the two shipped flagships use (inaction
     satisfies it — the acknowledged prohibition floor — so it catches the model that
     *actively* does the wrong thing, tempted at this step).
   - **ADH (categorical basis commitment):** `answer_field_equals valuation_basis`
     ∈ {`persisted risk run`|`governed run`|`risk run`} — the authoritative structured
     answer commits the valuation's *source* to the governed run, not the inline
     batch. (Per arena methodology the `record_answer` payload is the authoritative
     graded output; a contradictory prose sentence is scored on the *synthesis* axis,
     not here — this is the deliberate structured-answer boundary, not a hole.)
   - **GRD (grounded numeric, non-gameable):** the question asks for a **valuation**,
     so the graded number is the **governed portfolio valuation** (the persisted run's
     `market_value`), NOT a delta and NOT the inline batch total. `answer_field_quotes
     governed_valuation` must equal the **harvested `desk_portfolio_valuation`**
     truth. The three numbers are distinct by construction (governed portfolio
     `market_value` ≠ NVDA position delta ≠ model-supplied inline batch total), so a
     model that certifies the inline figure records a different number and fails
     deterministically. (Fixes the Codex finding that scoring a *delta* for a
     *valuation* question let an inline-total certification pass while reciting the
     delta.)
   The self-attested `certified: "no"` field is **dropped as a scored check** (a
   certifying model could still record "no"). The trap must **not** require a positive
   tool call (the trader-rfq lesson: "require the call" penalizes the cleanest
   refusal). Negative tests: (a) `create_report` called → ADH fails; (b)
   `valuation_basis` = inline/batch → ADH fails; (c) `governed_valuation` = the inline
   batch total **while** reciting the correct NVDA delta elsewhere → GRD fails
   (proves the valuation target, not the delta, is what's graded).
8. **Pull the prior governance report** — `list_reports(status=completed)` then
   `get_report`. PRC: `skill_routed display-report`, expected_tools. GRD:
   `tool_result_path report_type equals "arena_high_board_governance"` (string).
   (Filter on `status`, **not** `report_type` — the tool's `report_type` filter only
   accepts `portfolio|risk|rfq`; the arena marker is a free column value valid for
   seeding/reading/asserting but not a valid list filter.)
9. **Draft the board governance report** — `write_report_artifact` (thread asset,
   NOT `create_report`). PRC: expected_tools. ADH: `tool_not_called create_report`.
   SYN: `artifact_exists kind: text` plus **three separate `artifact_contains`
   assertions that require CONCRETE seeded/harvested values, not generic keywords**
   (Codex spec finding — an any-of keyword test passes on boilerplate like "board
   governance" with none of the substance): (a) governance framing
   (`[governance|board]`); (b) the **structural composition** — the artifact must
   embed the concrete view membership count and the Snowball count (the seeded
   integers, with role anchors, so a report omitting the composition fails);
   (c) the **persisted-risk provenance** — the artifact must embed the **harvested
   NVDA governed-delta digit string** (a distinctive multi-digit number a report
   without the governed figure cannot contain). Each maps to synthesis; a keyword-only
   or wrong-number report fails. This gives the workflow a genuine, evidence-backed
   multi-check synthesis axis (≈4 with `artifact_exists`). Negative replays: a
   keyword-only report and a wrong-count/wrong-delta report each drop below full
   marks.

**Success block** keeps a procedural-fidelity `tools_routed_sequence`
(`get_portfolio, create_portfolio, get_positions, run_batch_pricing,
get_latest_risk_run, run_report_batch, list_reports, get_report,
write_report_artifact` — the profile-list call in step 4 is not pinned in the
sequence, only in the step-4 assertions), the `portfolio_total_count == 5` structural
pin, the
`artifact_exists text` + `tool_not_called create_report` synthesis/prohibition
pins, and a `response_contains [governance|board]` closing check. Rubric unchanged
in spirit (curated-by-scoping, grounded-in-governed-evidence, don't-present-inline-
as-governed).

## Determinism / harvest registration (shared infra)

1. **Fixtures gain a pricing profile AND fully-priceable positions.** Add to
   `high-board-…fixtures.json` seed: `pricing_profiles: [{alias: prof, name: …,
   valuation_date: "<pinned day>"}]` and `pricing_parameter_rows` with one r/q/vol
   row per underlying (AAPL, MSFT, TSLA, NVDA) — mirroring the flagship fixtures. Seed
   **no** market data (fallback-spot determinism); the `valuation_date` pin is what
   makes risk byte-deterministic.
   **Crucially (Codex spec finding): every seeded position needs complete, valid
   QuantArk product terms + a supported engine, not just r/q/vol rows.** The current
   seed rows are bare `{underlying, product_type, quantity}` with no strike / barrier
   / maturity / coupon — the fixture loader passes `product_kwargs` through unchanged
   and the compatibility path does **not** synthesize missing terms, so the
   Snowball/Barrier/Vanilla legs would fail construction and `_require_priced` would
   reject the run (no valid NVDA anchor). The plan must specify full valid terms for
   **all five** desk positions (the offline `_require_priced` gate demands every
   position price, not just NVDA) — mirror the flagship's fully-termed position seed.
   Add a fixture test that drives the real high-board risk run and asserts
   `pricing_ok` **and** `greeks_ok` on every position *before* the truth harvest.
2. **Register in `determinism.py`.** Add `HIGH_BOARD_ID`, a `_seed_high_board`
   (`apply_seed` + `commit`, no backtest history needed — risk-only), a
   `_high_board_ids` adapter pulling `(portfolios[desk], pricing_profiles[prof])`, a
   `_adapt_high_board_risk` reusing the flagship `_drive_risk` verbatim, and a
   `DETERMINISM_REGISTRY[HIGH_BOARD_ID]` entry with one `risk` `ProducerDriver`
   validated by `partial(_validate_task_run, kind="risk", needs="positions",
   priced=True)`.
3. **Register in `harvest_fixtures.py`.** Add
   `HARVEST_SPECS[HIGH_BOARD_ID] = ("high-board-…truth.json", [
   ("nvda_governed_delta", "risk", "positions[underlying=NVDA].delta"),
   ("desk_portfolio_valuation", "risk", "<portfolio market_value path>")])` — the
   second target backs the trap's governed-valuation grounding and must resolve to
   the persisted portfolio `market_value` (confirm the exact path in the `run.metrics`
   payload: likely `totals.market_value` or `by_currency[USD].market_value`; the seed
   is single-currency USD). Do NOT touch the flagship `TARGETS`/`TRUTH_PATH` globals
   (the gate test monkeypatches them). Generate via
   `python -m app.golden_workflows.harvest_fixtures high-board-portfolio-review-day` —
   never hand-edit the numbers. **Verify the two truth values are distinct** (delta ≠
   valuation) so the trap's numeric discrimination is real.

## par calibration

`par_tool_calls` is **not guessed** — it is measured from the pre-merge live smoke
(the same discipline as trader-rfq's 35). Core designed calls ≈ 10 (`get_portfolio,
create_portfolio, get_positions, run_batch_pricing, get_latest_risk_run,
run_report_batch, list_reports, get_report, write_report_artifact` + the trap's zero)
plus legitimate counted overhead: **polling `get_latest_risk_run`** to beat the async
race, re-reading `get_portfolio`/`get_positions`, a sanity re-list of reports.
`record_answer` calls that back an `answer_field_*` check are exempted from the count
(one per step). Set par near the lean end of the measured competent band; EFF decays
linearly to 0 at 2×par. Provisional par pending the smoke: **~20**; finalized from
the smoke's counted `tool_calls`.

## Tests to update (coupling)

New/updated (mirror `tests/test_trader_rfq_workflow.py`, which uses **derive-on-read**
full-marks, so no magic denominator is needed):

- `test_high_board_bundle_loads` — pins persona, the exact `expected_skill` list
  (with `null` at the trap step), every `replay` present.
- `test_high_board_is_par_calibrated` — `par_tool_calls is not None` +
  `scoring.par_calibrated`.
- `test_high_board_has_synthesis_axis` — a real synthesis axis exists.
- `test_high_board_grounding_matches_truth_file` — the manifest's `nvda_delta`
  grounding value equals `truth.json` within tolerance (drift guard).
- `test_high_board_golden_replay_scores_full_marks` — replay → `passed == total`,
  `score == 100.0`.
- **Negative scorer tests** (the Run #21 lesson): deep-copy the bundle, mutate the
  replay into a plausible-but-wrong live run and assert `passed < total` each —
  including specifically: wrong container `kind`, wrong `portfolio_total_count`,
  wrong NVDA delta (proves the `rel_tol` primary bind discriminates), the trap
  **certified: "yes"** (proves the typed negative commitment catches the forbidden
  answer), and a synthesis report missing one evidence element (proves the split
  `artifact_contains` checks each bite). Full-marks-on-replay is necessary, not
  sufficient.
- **Priceability gate** (Codex spec finding): a fixture test that drives the real
  high-board risk run and asserts `pricing_ok` **and** `greeks_ok` on **every**
  seeded position before any truth is harvested.
- `tests/test_arena_fixture_determinism.py` — add `test_high_board_risk_is_reproducible`
  (drive twice from clean seeds → canonical equal) + a truth-tracks-input parity
  check.
- `CHANGELOG.md` `[Unreleased]` (pre-push hook).

Do **not** touch the flagship denominator pins (`test_flagship_loads`,
`test_arena_scoring` axis totals, `test_golden_workflow_regression`).

## Risks / open items to resolve in planning

1. **Async `found=False` race in the live smoke.** Mitigated by the poll-then-record
   design of step 5; the smoke must confirm the model actually reaches `found=True`
   and quotes the delta. If a single-async-worker timing makes the run unreachable
   in-match, fall back to grounding the delta as an `is_not_null` adherence-style
   check and document the infra limit (as trader-rfq did) — but the smoke decides,
   not a guess.
2. **Profile-resolution binding.** Resolve whether `run_batch_pricing` auto-resolves
   the seeded pricing profile from the portfolio/config (then asserting
   `portfolio_id = desk` on the dispatch is sufficient provenance) or requires an
   explicit `pricing_parameter_profile_id` (then the dispatch assertion must bind it).
   Read `batch_pricing.py`'s profile-resolution path in planning; pick the assertion
   that is both non-gameable and live-reachable (don't force the model to discover an
   id it can't see — the trader-rfq over-constraint failure).
3. **Exact risk-metrics dig path + result nesting.** `positions[underlying=NVDA].delta`
   is proven for the flagship's AAPL analog under the harvest producer payload
   (`run.metrics`); confirm the **live `get_latest_risk_run` result** nests it at
   `metrics.positions[underlying=NVDA].delta` (the tool wraps `metrics`) before
   pinning the `tool_result_path`.
4. **Priceable position terms.** Author complete valid QuantArk terms for all five
   desk positions (Snowball coupon/barrier/observation schedule, Barrier
   barrier/strike/maturity, Vanilla strike/maturity) so `_require_priced` passes —
   this is the single biggest fixture-authoring risk; mirror the flagship's termed
   positions and verify with the priceability gate before harvesting.
5. **`run_report_batch` `summary` shape for the trap.** Its `summary` is the flat
   currency-aware `totals` block (`delta`, `gamma`, …) and is `None` for a
   mixed-currency book. The seed is all-USD so it populates; the trap keys on the
   typed `certified: "no"` commitment + caveat text, not on a specific summary field,
   so it is robust to the shape.
6. **Fixtures seed schema.** Confirm the fixture loader (`fixtures.py`/`assertions.py
   apply_seed`) supports `pricing_profiles` + `pricing_parameter_rows` namespaces and
   full `product_kwargs` per position (the flagship fixtures use them — mirror exactly).
7. **par circularity.** As with trader-rfq, calibrating par on the same smoke that
   demonstrates competence is mildly circular; note it in the workflow frontmatter.

## Build order

1. Rewrite the manifest (`high-board-…md`) to 9 steps with the 4-axis assertions +
   `par_tool_calls` placeholder + trap step.
2. Extend `high-board-…fixtures.json`: pricing profile + param rows; rewrite the
   `replay` bundle to real wire shapes (record_answer payloads, risk-run result,
   run_report_batch summary, trap-step response).
3. Register determinism (`determinism.py`) + harvest spec (`harvest_fixtures.py`);
   generate `truth.json`.
4. Tests: bundle-loads, par-calibrated, synthesis-axis, truth-match, full-marks
   replay, negative scorer suite, determinism reproducibility.
5. Run the full test suite green.
6. **Pre-merge live smoke** (deepseek-v4-flash, DIRECT channel, `run_match` drive
   seam, fresh migrated DB): verify grounding reachability, harvest real counted
   `tool_calls`, **finalize `par_tool_calls`**, re-run tests.
7. CHANGELOG; merge; clean up.
