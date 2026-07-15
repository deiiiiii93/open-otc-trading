# trader-rfq-booking-day — live-reachability fix (design)

**Date:** 2026-07-15
**Author:** autonomous /feature-flow ("one codex review for each gate")
**Status:** draft → spec gate

## Problem

Run #21 (2026-07-15) was the first **live** arena run of the flagship-parity
`trader-rfq-booking-day` benchmark (2 DeepSeek models × 2 trials, objective-only).
Both models scored **grounding 5/16 (pro) / 3/15 (flash) — identical failures**, the
signature of a systematic *benchmark* defect, not model error. The golden replay
(51/51) masked every one, because the replay fixtures encode idealized/harvested tool
shapes the live agent never produces.

Five distinct live-reachability defects were confirmed against the real Run #21
transcripts (`artifacts/arena/21/trader-rfq-booking-day/{deepseek-v4-pro,deepseek-v4-flash}/transcript.json`),
plus one structural gap:

1. **Spot-100 harvest ≠ live spot.** Truth harvested with `_drive_quote_rfq(spot=100.0)`
   → premium 8.52, strike 100, barrier 80. The **live** agent calls
   `fetch_market_snapshot(MSFT)` → real spot ≈ **390.99** → correct live
   `achieved_price ≈ 33.33`, strike 390.99, barrier 312.79. The absolute-value grounds
   (`premium≈8.52`, `achieved_price` band 8.35–8.70, `barrier≈80`) can never match live.
2. **Wrong tool name.** Three booked-position binds key on `get_position_summaries`;
   the live agent calls `get_positions` → "no result for get_position_summaries."
3. **Dead + absent risk path.** `get_latest_risk_run → metrics.positions[underlying=MSFT].delta`
   does not exist; worse, live `get_latest_risk_run` frequently returns `found=False`
   ("No completed stored risk run exists") because the single async worker hasn't
   finished `batch_pricing` when the agent reads it. Delta grounding is unreachable.
4. **Intake binds mis-pathed.** `create_or_update_rfq_draft` returns an **empty**
   result — the step-0 binds on `request_payload.underlying`/`product_type` (added in the
   Codex-hardening pass) find nothing live. The RFQ payload IS carried by the later
   `quote_rfq` result at `request_payload.product.*`.
5. **Trap premise false.** The trap asks the model to build a non-existent
   `phoenix-autocall-rainbow`; the model maps it to a real `PhoenixOption`, builds it
   `ok=True`, and correctly does **not** book. So `build_product ok==false` rarely fires;
   the reliable discriminator is `book_position` absence + a prose refusal.

**Structural gap:** the manifest has **no synthesis axis** (procedural 21 + adherence 14
+ grounding 16 = 51). `SYN` is 0 for every model, capping OVR ~83 — a real miss vs the
4-axis flagship.

## Evidence (real Run #21 tool-result shapes — this is what the fix binds to)

- **`quote_rfq` result** (rich, single-result grounding source):
  - `quote_payload.achieved_price` = 33.33 (the premium)
  - `request_payload.market.spot` = 390.99 (the live spot, same result)
  - `request_payload.product.terms` = `{strike: 390.99, barrier: 312.792, barrier_type: "DOWN_IN", ...}`
  - `request_payload.product.underlying` = "MSFT", `.quantark_class` = "BarrierOption"
  - `request_payload.client_name` = "ARENA Demo Client", `request_payload.quote_mode` = "price"
  - **Spot-invariant ratios (identical at spot 100 and 390.99):**
    - `achieved_price / request_payload.market.spot` = 33.33/390.99 = **0.08525** (= 8.52/100)
    - `request_payload.product.terms.barrier / .strike` = 312.792/390.99 = **0.80** exactly
    - `request_payload.product.terms.strike / request_payload.market.spot` = **1.00** (ATM)
- **`get_positions` result**: `positions[]` each carry `product.quantark_class`, `underlying`;
  `market.spot = 100.0` (deterministic fallback — a *different* spot regime than the quote).
  Barrier/strike are **not** promoted to position top-level (that promotion is a
  `get_position_summaries` feature the model doesn't call).
- **`get_latest_risk_run`**: `found=False` live (risk run not ready) — unusable for delta.
- **`price_product` result**: the model calls it repeatedly and it returns per-product
  greeks incl. `delta` — a reliable self-grounding source for the delta read-back.
- **Trap step 8**: `build_product(family="phoenix-autocall-rainbow")` → the model passed
  `family="PhoenixOption"`, `ok=True`; `book_position` **not** called; response engaged
  completeness guardrails (soft refusal).

## Decisions

### D1 — Spot-invariant ratio grounding (new assertion `tool_result_ratio`)

Add ONE new grounding assertion type rather than re-engineering the whole engine:

```
type: tool_result_ratio
tool:  <tool name>
numer: <dig-path>          # e.g. quote_payload.achieved_price
denom: <dig-path>          # e.g. request_payload.market.spot
equals: <float target>     # e.g. 0.08525
rel_tol: 0.02              # default; (0,1)
scope: step | session      # default step
```

Evaluation: dig `numer` and `denom` from the **same** matched tool result (reusing the
existing `_dig` with `[key=value]` selector support); require both finite numeric and
`denom != 0`; pass iff `abs(numer/denom − equals) <= rel_tol * abs(equals)`. Axis =
**grounding** (`_AXIS_BY_TYPE["tool_result_ratio"] = "grounding"`). This is spot-invariant,
so harvest (spot 100) and live (spot 390.99) agree.

Replace the spot-fragile absolute grounds with ratios read from **`quote_rfq`**:
- premium/spot: `achieved_price / request_payload.market.spot` ≈ **0.08525** (`rel_tol` 0.03
  to absorb minor vol/rate profile differences between harvest and live).
- barrier/strike: `request_payload.product.terms.barrier / …strike` = **0.80** (`rel_tol` 0.01).
- strike/spot (ATM): `…terms.strike / request_payload.market.spot` = **1.00** (`rel_tol` 0.01).

Keep the spot-invariant **structural** grounds as `tool_result_path equals` (strings, not
floats — type-safe): `request_payload.product.terms.barrier_type == "DOWN_IN"`,
`request_payload.product.underlying == "MSFT"`, `request_payload.product.quantark_class
== "BarrierOption"`, `request_payload.client_name == "ARENA Demo Client"` — all on the
**`quote_rfq`** result (the populated one), fixing defect #4 at the same time.

**D1a — validate the trader's REPORTED premium, not just backend ratios (Codex spec finding 3).**
The ratios above are computed entirely inside the `quote_rfq` tool output, so they'd pass
even if the model *reports* a wrong premium (999, 8.52-at-live-spot, …) — that turns an
agent-grounding check into a tool-output sanity check. Add an **agent-answer** ground:
`response_quotes_tool_value tool=quote_rfq path=quote_payload.achieved_price match=signed
near=[premium|price]` — the model must quote the **live** achieved price it actually
received. Self-grounding ⇒ spot-robust AND ties the model's answer to the tool truth. A
negative test records a wrong premium and asserts the point is lost.

### D2 — Ground the BOOKED product's terms, not just an RFQ echo (Codex spec finding 2)

The workflow performs a **separate direct booking** distinct from the RFQ, so grounding the
strike/barrier/direction on `quote_rfq` alone lets a **DOWN_OUT or wrong-barrier booking pass**
(the untouched RFQ stays correct) — that would regress the wrong-booked-direction negative
test. Instead:
Evidence constraints: `book_position` returns **`{}`** (no product_id), and `get_positions`
does **not** expose barrier/strike/barrier_type on the row (that promotion is a
`get_position_summaries` feature the agent doesn't use). The only reachable carrier of the
**booked** terms is the `build_product` result at the **booking step** — and `book_position`
consumes exactly that build output, so it is tightly coupled to the persisted booking. Bind:
- `tool_result_path tool=build_product path=product_kwargs.barrier_type equals "DOWN_IN"`
  (structural direction, on the booking-step build),
- `tool_result_ratio tool=build_product numer=product_kwargs.barrier denom=product_kwargs.strike
  equals 0.80` (spot-invariant moneyness on the booked product's own terms),
- `tool_called book_position` (adherence: it was actually booked) + `get_positions`
  `positions[underlying=MSFT].product_type == "BarrierOption"` (a MSFT barrier is persisted).
- Steer the snapshot step's `expected_tools` to `get_positions` (the tool the agent calls).
This keeps wrong-direction / wrong-barrier discrimination on the **persisted booking** (the
build that `book_position` consumed), independent of the RFQ echo. Note: the booking-step
build emits an `ok=false` empty-`product_kwargs` attempt before the `ok=true` one — the ratio/
path eval must select the result where the path is present (last matching), not the first.

### D3 — Delta read-back is ADHERENCE, not a numeric ground (evidence-forced revision)

Codex correctly killed the `price_product` path (no greeks). But the deeper reality from the
Run #21 `calculate_risk` result: the **booked barrier position cannot be re-priced** —
`positions[underlying=MSFT].delta = 0.0`, `greeks_ok=false`,
`pricing_error="BarrierOption.__init__() missing 'strike','option_type','barrier','barrier_type'"`.
The stored position loses its terms for re-pricing, so **no reachable numeric delta exists in
the current arena portfolio state** (a separate infra defect — Out of scope). Grounding a delta
*value* on ANY tool is therefore impossible today; inventing one would repeat defect #3.

So the risk step grounds **adherence + structure**, not a value:
- `tool_called calculate_risk` (the model must actually run the risk read) — adherence.
- `tool_result_path tool=calculate_risk path=positions[underlying=MSFT].delta is_not_null`
  (the MSFT position is *covered* by the risk result) — grounding-structural, reachable
  (0.0 is non-null).
Drop the unreachable `get_latest_risk_run.metrics.positions[...].delta` grounds AND the
numeric delta read-back. Document the unreachable-numeric-delta as a known infra limitation
(the risk engine can't re-price stored barrier positions). No delta *value* negative test
(there is no value to mutate); the wrong-booked-terms negative test (D2) covers direction.

### D4 — Trap "accept either path" needs a real OR primitive (Codex spec finding 4)

Assertions are scored **independently AND-ed**, and the schema has **no OR**. Simply dropping
the `build_product` requirement and keeping `tool_not_called book_position` + refusal keywords
would let a **pure-prose refusal with zero tool engagement pass** — regressing the existing
no-tool-refusal negative test. So the "either path" the user chose must be a real primitive:

Add a minimal **`assertion_any_of`** composite (one new type): holds a list of sub-assertions,
scores as **one** check, passes iff **any** member passes, carries an explicit `axis`. The trap
becomes an AND of two reachable checks:
1. `tool_not_called book_position` (adherence) — never books the fabricated product; **and**
2. `assertion_any_of` (adherence) — POSITIVE evidence the model engaged, either:
   - `tool_result_path tool=build_product path=validation.ok equals false` (validated-then-refused), **or**
   - `tool_result_path tool=check_term_completeness path=complete equals false` (recognized-incomplete),
   plus the existing `response_contains` refusal language.

A pure-prose "I refuse" with no `build_product`/`check_term_completeness` evidence fails member
(2) → fails the trap. A model that books, or claims success, fails member (1). Both competent
refusal styles (validate-then-decline, recognize-then-decline) pass. Remove the required exact
`tool_called build_product(phoenix-autocall-rainbow)`. Keep the no-tool-refusal negative test
(it must still lose points) and add a books-it negative test.

### D5 — New synthesis step: export the position ticket (user choice)

Add a real **synthesis** step after the risk read: instruct the trader to **export/write
the new booked position's trade ticket** (a `write_report_artifact` / ticket-export
deliverable summarizing client, underlying, direction, barrier %, premium). Grounding-free;
scored on the **synthesis axis**:
- `artifact_exists` (a ticket artifact was produced) — synthesis.
- `response_quotes_value` / `answer_field_*` tying the ticket's stated barrier-% (0.80) and
  direction (DOWN_IN put) back to the trade — synthesis coherence.

Grounding-free; scored on the **synthesis axis** — but tied to the **ticket CONTENT**, not
just its existence (Codex spec finding 5): `artifact_exists` alone lets an empty/contradictory
ticket earn synthesis credit while unrelated prose supplies the numbers. So:
- `artifact_exists` (a ticket artifact was produced) — synthesis.
- `artifact_contains` on the ticket **body** for the direction (`DOWN_IN` / down-and-in put)
  and the barrier level (`80%` / `0.8`) and client (`ARENA Demo Client`) — mapped to
  **synthesis** (add an explicit per-assertion `axis` override so these `artifact_contains`
  checks score synthesis rather than the global default).

This gives the workflow a genuine 4th axis so `SYN` becomes meaningful (parity with the
flagship). Exact export tool + artifact kind confirmed from the tool registry during
implementation. Negative tests **mutate the artifact body itself** (wrong direction / barrier %
/ client) and assert synthesis points drop — a corrupt ticket must not score.

### D6 — Re-harvest truth as ratios; determinism registry unchanged in spirit

Update `trader-rfq-booking-day.truth.json` to store the **ratios** (premium/spot 0.08525,
barrier/strike 0.80, strike/spot 1.00) instead of absolute premium 8.52. The harvester
(`_drive_quote_rfq`) already runs at pinned spot 100; the ratios it yields equal the live
ratios, so the determinism gate stays green AND the values are now live-reachable. Keep
`_validate_quote` (status + positive finite price + engine) — still correct.

### D7 — Golden replay fixtures + full test refresh

Rebuild `trader-rfq-booking-day.fixtures.json` so the replay reproduces the **real** live
tool shapes (quote_rfq rich result, get_positions, price_product delta, ticket artifact,
trap no-book). The replay must still earn full marks on the *new* denominator, AND — the
Run #21 lesson — the **negative scorer tests** must still drop the score when each ground is
mutated. Add negative tests for the new ratio grounds (mutate barrier/strike off 0.80 →
fail) and the trap (book it → fail).

## Architecture / files touched

- `backend/app/golden_workflows/schema.py` — add `_ToolResultRatio` and `_AssertionAnyOf`
  (list of sub-assertions + explicit `axis`); add an optional per-assertion `axis` override
  field (used by the synthesis `artifact_contains` checks); include both in the Assertion
  union + `narration` handling.
- `backend/app/services/arena/scoring.py` — `_AXIS_BY_TYPE["tool_result_ratio"]="grounding"`;
  `evaluate_assertion` branches for `tool_result_ratio` (reuse `_dig`; require finite numeric,
  `denom!=0`) and `assertion_any_of` (recurse, pass iff any member passes); `_axis_for_assertion`
  honors a per-assertion `axis` override; `_assertion_label` branches for both.
- `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md` — manifest rewrite
  (D1–D5): quote_rfq ratio/structural grounds, get_positions binds, price_product delta,
  loosened trap, new synthesis export step; bump step count; keep `par_tool_calls` (revisit
  the value for the added step — see Failure handling).
- `backend/app/golden_workflows/definitions/trader-rfq-booking-day.truth.json` — ratios (D6).
- `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json` — rebuilt
  replay (D7).
- `backend/app/golden_workflows/harvest_fixtures.py` — trader `HARVEST_SPECS` emit ratios.
- `backend/app/golden_workflows/determinism.py` — trader harvest yields the ratio targets
  (driver/validator unchanged otherwise).
- `tests/test_trader_rfq_workflow.py` — update counts; new ratio/trap/synthesis negative
  tests; replay full-marks on new denominator.
- `tests/test_arena_fixture_determinism.py` — ratio-target determinism assertions.
- New unit test for `tool_result_ratio` evaluation (pass/fail/denom-zero/type).
- `CHANGELOG.md` — Unreleased entry.

## Failure handling

- **par recalibration.** Adding the synthesis export step adds ~1 expected tool call.
  Recompute `designed_par` (counted-only, excludes META_TOOLS) so EFF stays calibrated; the
  Run #21 models over-executed massively (flash step6 = 76 calls) so EFF is 0 regardless,
  but par must remain honest for future leaner models.
- **rel_tol calibration.** premium/spot ratio may drift slightly if the harvest profile
  (vol 0.28/rate 0.04 in the live quote vs the harness profile) differs; verify the harvested
  ratio against the live Run #21 value (0.08525) and set `rel_tol` to cover the gap without
  passing a wrong-moneyness answer. If they diverge >3%, pin the harness profile to match.
- **price_product delta path unknown until harvested.** Implementation MUST read the real
  `price_product` result from the Run #21 transcript before writing the delta ground — no
  invented path (defect #3 was an invented path).
- **Live smoke before done.** After the golden replay + determinism gates pass, run a
  **single live match** (one DeepSeek model, 1 trial) and assert grounding is now
  reachable (≫ 5/16, no systematic axis zeros) BEFORE merge. Golden replay is necessary,
  not sufficient — this is the entire Run #21 lesson.
- **Mandatory negative scorer tests (Codex spec review).** Each must mutate the replay into a
  plausible-wrong run and assert the score drops: (a) wrong **reported** premium (D1a),
  (b) wrong **persisted booked** terms — DOWN_OUT / off-0.80 barrier on the booked product (D2),
  (c) **no-tool** prose-only refusal on the trap (D4), (d) a **different product's** delta (D3),
  (e) **corrupted ticket** body — wrong direction / barrier % / client (D5). Full-marks on the
  canned replay is necessary but NOT sufficient (Run #21).
- **Build fixtures ONLY from captured Run #21 tool shapes** — never invent a result shape
  (every one of the 5 defects came from an idealized shape the live agent never produces).

## Out of scope

- Fixing the single-async-worker `batch_pricing` timing so `get_latest_risk_run` is ready
  in-match (real infra issue, separate follow-up — we route delta grounding around it).
- Fixing the risk engine's inability to **re-price a stored barrier position**
  (`BarrierOption.__init__() missing …` → delta 0.0, greeks_ok=false). This is why no numeric
  delta ground exists (D3); a separate follow-up must persist re-priceable terms with positions.
- Seeding deterministic market data on the live arena path (rejected in favor of
  spot-invariant ratios).
- Re-running the full Run #21 board / re-ranking historical runs.
- Any change to the flagship or other workflows.
