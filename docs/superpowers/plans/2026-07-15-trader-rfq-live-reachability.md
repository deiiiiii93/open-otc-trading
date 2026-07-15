# trader-rfq-booking-day live-reachability fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use `- [ ]`.

**Goal:** Make `trader-rfq-booking-day` grounding *live-reachable* (Run #21 scored grounding
5/16 on benchmark defects) and add a 4th (synthesis) axis, verified by a live smoke — not just
the golden replay.

**Architecture:** Two new workflow-agnostic assertion primitives (`tool_result_ratio` for
spot-invariant grounding; `assertion_any_of` for the trap's "either path") + a per-assertion
`axis` override; then a manifest rewrite that binds only to **captured Run #21 tool shapes**,
new truth ratios, a rebuilt replay bundle, and negative scorer tests that prove each ground
discriminates.

**Tech Stack:** FastAPI/pydantic golden-workflow schema, arena scoring kernel, pytest.

## Global Constraints (verbatim)

- **Never invent a tool-result shape.** Every path/ratio/label binds to a real shape captured
  from `artifacts/arena/21/trader-rfq-booking-day/deepseek-v4-pro/transcript.json`.
- `tool_result_path equals` is **type-strict** (`type(a) is type(b)`) — bind strings as strings,
  ratios via `tool_result_ratio` (float compare with `rel_tol`), never `equals` a raw float.
- **The evaluator lives in `backend/app/golden_workflows/assertions.py`** (`evaluate_assertion`:288,
  `_dig`:177, `_last_result`:281) — put every new EVALUATION branch there; `scoring.py` only maps
  axis (`_AXIS_BY_TYPE`/`_axis_for_assertion`) and labels (`_assertion_label`). (Codex plan finding 5.)
- **Contract-multiplier is NOT invariant.** pro used `contract_multiplier=1` (achieved 33.33),
  flash used `100` (achieved 3333.1). premium must normalize by BOTH spot and multiplier:
  `achieved_price/(spot×contract_multiplier)` = 0.08525 for both. Cover multiplier-1 AND -100
  captured shapes in tests. (Codex plan finding 3.)
- Golden replay must earn full marks on the NEW denominator AND every negative test must drop it.
- Update `CHANGELOG.md` under `[Unreleased]` before finishing.
- Determinism gate stays green; harvester re-run, not hand-edited.

### Captured real shapes (bind to these exactly)

- `quote_rfq` result: `quote_payload.achieved_price`=33.331; `request_payload.market.spot`=390.99;
  `request_payload.product.terms.{strike=390.99,barrier=312.792,barrier_type="DOWN_IN"}`;
  `request_payload.product.{underlying="MSFT",quantark_class="BarrierOption"}`;
  `request_payload.client_name="ARENA Demo Client"`.
  Invariants: achieved_price/spot=0.08525; barrier/strike=0.80; strike/spot=1.00.
- booking-step `build_product` (ok=true one): `product_kwargs.{barrier_type="DOWN_IN",
  barrier=312.792,strike=390.99}` (an ok=false empty-`product_kwargs` attempt precedes it).
- `book_position` result: `{}` (empty). `calculate_risk` result:
  `positions[underlying=MSFT].delta`=0.0 (`greeks_ok=false`; non-null but no real value).
- `write_report_artifact` tool exists (`backend/app/tools/reporting.py:152`).

---

### Task 1: `tool_result_ratio` assertion (spot- AND multiplier-invariant grounding)

**Files:**
- Modify: `backend/app/golden_workflows/schema.py` (add model, add to Assertion union)
- Modify: `backend/app/golden_workflows/assertions.py` (`evaluate_assertion` branch, reuse `_dig`/`_last_result`; add a `_last_call` args helper)
- Modify: `backend/app/services/arena/scoring.py` (`_AXIS_BY_TYPE`, `_assertion_label` ONLY)
- Test: `tests/test_tool_result_ratio.py` (new)

**Interfaces — Produces:** a new assertion type usable in manifests:
`{type: tool_result_ratio, tool, numer, denom, equals: float, rel_tol=0.02, scope="step",
source="result"|"call"=result, denom_mult: <path>|None=None}`.
Value = `dig(numer) / (dig(denom) * (dig(denom_mult) if set else 1))`, read from the last matching
tool **result** (`source=result`) or tool **call args** (`source=call`).

- [ ] **Step 1 (test first):** in `tests/test_tool_result_ratio.py`, cover: (a) pro shape —
  `quote_payload.achieved_price=33.33`, `request_payload.market.spot=390.99`,
  `request_payload.product.terms.contract_multiplier=1` → `equals=0.08525, rel_tol=0.03,
  denom=…market.spot, denom_mult=…terms.contract_multiplier` → PASS; (b) **flash shape** —
  `achieved_price=3333.101, spot=390.99, contract_multiplier=100` with the SAME assertion → PASS
  (proves multiplier normalization); (c) `equals=0.05` → FAIL; (d) `denom` digs 0 → FAIL;
  (e) missing/non-numeric path → FAIL; (f) `source="call"` reads from `ctx.tool_calls[...].args`.
- [ ] **Step 2:** run it — FAIL (unknown type).
- [ ] **Step 3:** add `_ToolResultRatio(BaseModel)` in schema.py (fields above) with a
  `model_validator` enforcing `0<rel_tol<1`, non-empty `numer`/`denom`, `source∈{result,call}`;
  add to the `Assertion = Annotated[Union[...]]`.
- [ ] **Step 4:** in **assertions.py** `evaluate_assertion`, add the `tool_result_ratio` branch:
  select the last matching result (`_last_result`) or call-args (new `_last_call`) by `source`,
  `_dig` numer/denom/denom_mult, guard finite numeric + non-zero denominator product, pass iff
  `abs(value - equals) <= rel_tol*abs(equals)`, else a descriptive msg. In scoring.py add
  `_AXIS_BY_TYPE["tool_result_ratio"]="grounding"` and an `_assertion_label` branch
  `f"{a.tool}[{a.source}] {a.numer}/({a.denom}×{a.denom_mult}) ≈ {a.equals}"`.
- [ ] **Step 5:** run tests — PASS. Commit `feat(arena): tool_result_ratio grounding assertion`.

### Task 2: `assertion_any_of` composite + per-assertion `axis` override

**Files:**
- Modify: `backend/app/golden_workflows/schema.py`
- Modify: `backend/app/golden_workflows/assertions.py` (`evaluate_assertion` recursion)
- Modify: `backend/app/services/arena/scoring.py` (`_axis_for_assertion`, `_assertion_label` ONLY)
- Test: `tests/test_assertion_any_of.py` (new)

**Interfaces — Produces:**
`{type: assertion_any_of, axis: "adherence", any_of: [<assertion>, ...]}` scores as ONE check,
passes iff any member passes. And every assertion gains an optional `axis: <str>|None` override
consumed by `_axis_for_assertion`.

- [ ] **Step 1 (test):** two members, one passing one failing → `evaluate_assertion` returns
  `(True, "")`; both failing → `(False, "no member passed: …")`. Assert `_axis_for_assertion`
  returns the composite's declared `axis`. Assert a `tool_result_path` with `axis="synthesis"`
  overrides its default grounding axis.
- [ ] **Step 2:** run — FAIL.
- [ ] **Step 3:** add `_AssertionAnyOf` (fields `type`, `axis: str`, `any_of: list[Assertion]`
  with `min_length=2`); add optional `axis: str|None=None` to each assertion model (or a shared
  mixin) — validate `axis` ∈ the known axis set when present. Add to the union (forward-ref the
  recursive `Assertion` type; `model_rebuild()` if needed).
- [ ] **Step 4:** scoring.py — `evaluate_assertion` `assertion_any_of` branch recurses over
  members with the same ctx, returns pass on first success; `_axis_for_assertion(a)` returns
  `a.axis` when set else the `_AXIS_BY_TYPE` default; `_assertion_label` → `"any of: [...]"`.
- [ ] **Step 5:** run — PASS. Commit `feat(arena): assertion_any_of + per-assertion axis override`.

### Task 3: manifest rewrite (D1/D1a/D2/D3/D4/D5 + synthesis step)

**Files:**
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md`

Rewrite grounding to bind only to captured shapes:
- [ ] **Step 1 (quote step — D1/D1a):** replace absolute premium/barrier grounds with, on
  `quote_rfq`: `tool_result_ratio achieved_price/…market.spot ≈ 0.08525 (rel_tol 0.03)`;
  `tool_result_ratio …terms.barrier/…terms.strike ≈ 0.80 (rel_tol 0.01)`;
  `tool_result_ratio …terms.strike/…market.spot ≈ 1.00 (rel_tol 0.01)`; structural
  `tool_result_path equals` for `…product.underlying="MSFT"`, `…product.quantark_class="BarrierOption"`,
  `…client_name="ARENA Demo Client"`, `…product.terms.barrier_type="DOWN_IN"`; and the agent-answer
  ground `response_quotes_tool_value tool=quote_rfq path=quote_payload.achieved_price match=signed near=[premium,price]`.
- [ ] **Step 2 (booking step — D2, bind to the ACTUAL booking payload, Codex plan finding 1):**
  the authoritative booked terms are `book_position` **call args** `product.terms.*` (captured:
  `barrier_type=DOWN_IN, barrier=312.792, strike=390.99`), NOT the decoupled build result. Bind:
  `tool_called book_position args_any_of=[{product:{terms:{barrier_type:"DOWN_IN"}}}]` (direction)
  + `tool_result_ratio tool=book_position source=call numer=product.terms.barrier
  denom=product.terms.strike equals 0.80` (moneyness on the booked payload). Keep the build-step
  `product_kwargs.barrier_type` as a secondary structural check.
- [ ] **Step 3 (snapshot step — D2):** set `expected_tools` to `get_positions`; bind
  `tool_result_path get_positions positions[underlying=MSFT].product_type equals "BarrierOption"`.
- [ ] **Step 4 (risk step — D3, require SUCCESSFUL risk, Codex plan finding 2):**
  `tool_called calculate_risk` +
  `tool_result_path calculate_risk positions[underlying=MSFT].greeks_ok equals true` +
  `tool_result_path calculate_risk positions[underlying=MSFT].pricing_ok equals true` +
  `response_quotes_tool_value tool=calculate_risk path=positions[underlying=MSFT].delta
  match=signed near=[delta]` (bind the REPORTED delta to the computed one). Captured: flash
  greeks_ok=true delta=-0.4164 (reachable); pro greeks_ok=false (correctly loses the point — it
  genuinely failed to re-price). Remove all `get_latest_risk_run.metrics…delta` and any
  `delta is_not_null` grounds (0.0-on-failure must NOT earn credit).
- [ ] **Step 5 (trap step — D4):** AND of `tool_not_called book_position` (adherence) and
  `assertion_any_of axis=adherence [ tool_result_path build_product validation.ok equals false,
  tool_result_path check_term_completeness complete equals false ]` plus `response_contains`
  refusal terms. Remove the required exact `tool_called build_product(phoenix-autocall-rainbow)`.
- [ ] **Step 6 (NEW synthesis step — D5):** append a step: user asks to *export the booked
  position's trade ticket* via `write_report_artifact`; `expected_skill` per the export skill (or
  `null`); assertions: `artifact_exists (kind=<ticket kind>)` [synthesis] +
  `artifact_contains kind=<ticket> any_of=["DOWN_IN"/"down-and-in"] axis=synthesis` +
  `artifact_contains any_of=["80"] axis=synthesis` + `artifact_contains any_of=["ARENA Demo Client"] axis=synthesis`.
- [ ] **Step 7:** recompute `par_tool_calls` (counted-only; +~1 for the export) and set it.
- [ ] **Step 8:** `.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py::test_trader_rfq_bundle_loads -x`
  (expect the step-list/skill assertions to need updating in Task 5). Commit manifest.

### Task 4: truth ratios + harvester + determinism

**Files:**
- Modify: `…/trader-rfq-booking-day.truth.json`, `harvest_fixtures.py`, `determinism.py`
- Modify: `tests/test_arena_fixture_determinism.py`

- [ ] **Step 1:** update `_drive_quote_rfq`/`HARVEST_SPECS` so the trader harvest emits the three
  ratios (premium/spot, barrier/strike, strike/spot) from the pinned-spot(100) quote — they equal
  the live ratios by construction.
- [ ] **Step 2:** re-run `.venv/bin/python -m app.golden_workflows.harvest_fixtures` (isolated
  clean DB) → regenerated `truth.json` holding `{premium_spot_ratio:0.08525, barrier_strike_ratio:0.80,
  strike_spot_ratio:1.00}` (values from the harvest, not hand-typed).
- [ ] **Step 3:** update `test_trader_rfq_grounding_targets_match_truth_file` to assert the manifest
  ratio `equals` targets match `truth.json`. Add a determinism test that the harvested ratios are
  reproducible. Run `tests/test_arena_fixture_determinism.py` — PASS. Commit.

### Task 5: rebuild replay + full-marks + negative scorer tests

**Files:**
- Modify: `…/trader-rfq-booking-day.fixtures.json` (rebuild from captured shapes)
- Modify: `tests/test_trader_rfq_workflow.py`

- [ ] **Step 1:** rebuild `fixtures.json` so each step's `tool_results` carry the **captured**
  shapes (quote_rfq rich result, booking build_product ok=false-then-ok=true, get_positions with a
  MSFT BarrierOption row, calculate_risk with `positions[underlying=MSFT].delta`, a ticket artifact,
  trap: build ok? + check_term_completeness complete=false + no book). Response texts must quote the
  live achieved price (33.33) with `premium` near-anchor, and the ticket body must contain DOWN_IN/80/client.
- [ ] **Step 2:** update `test_trader_rfq_bundle_loads` (new step list incl synthesis step +
  `expected_skill`s), `test_trader_rfq_is_par_calibrated`, and `test_trader_rfq_regression_replay_scores_full`
  (assert `passed==total`, new denominator).
- [ ] **Step 3:** run replay test — iterate fixtures until 100%. **Never** relax a check to pass;
  fix the fixture to the real shape.
- [ ] **Step 4 (negative tests — mandatory, Codex):** add/adjust `_score_with_mutation` tests:
  (a) wrong **reported** premium in response text → D1a fails; (b) DOWN_OUT / off-0.80 barrier in
  the **`book_position` CALL ARGS** (the authoritative booking payload, not the build) → D2 fails;
  (c) trap: clear tool_calls+tool_results (pure prose) → D4 any_of fails; (d) corrupt ticket body
  (wrong direction / barrier% / client) → D5 synthesis fails; (e) books the trap product → D4
  fails; (f) risk `greeks_ok=false` / corrupted reported delta → D3 fails. Each asserts
  `passed < total`.
- [ ] **Step 5:** full run `tests/test_trader_rfq_workflow.py` — PASS. Commit.

### Task 6: CHANGELOG + regression sweep + live smoke

- [ ] **Step 1:** `CHANGELOG.md` `[Unreleased]` — live-reachability fix + 2 new assertion types + synthesis axis.
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/test_golden_workflow_regression.py tests/test_arena_scoring.py tests/test_flagship*.py tests/test_arena_fixture_determinism.py -q` — flagship replay must still earn full marks (shared-kernel no-regression); fix any coupling.
- [ ] **Step 3 (live smoke — PRE-merge gate, Codex plan finding 4):** run ONE live match against
  the **worktree code** BEFORE merge — in-process via the arena `run_match(..., drive=...)` seam on
  the **DIRECT DeepSeek channel** (`api.deepseek.com`, `deepseek-v4-flash`; pythonpath=backend →
  worktree, no server, sidesteps ZenMux quota). Pinned pass thresholds (not "≫"): **grounding
  ≥ 10 of the grounding checks**, **synthesis ≥ 3 of 4**, and **no axis at 0**. Capture the
  match's `score_breakdown.objective.axes` (passed/total per axis) as evidence in the commit/PR.
  **Rollback:** if the thresholds are not met, do NOT merge — reopen implementation (the worktree
  stays intact). A single trial is enough to prove *reachability* (the defect was systematic-zero);
  it is not a ranking claim.
- [ ] **Step 4:** only after Step 3 passes → finishing-a-development-branch → merge.
