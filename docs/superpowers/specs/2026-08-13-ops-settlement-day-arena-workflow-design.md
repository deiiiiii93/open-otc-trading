# Operations Settlement Day — arena golden workflow (design)

**Date:** 2026-08-13
**Status:** approved design, pre-plan
**Workflow id:** `ops-settlement-day` (5th golden workflow)
**Persona:** `trader` (decided: the settlement + lifecycle skills route to trader and
trader's catalog already sources the `settlement` and `positions` domains; personas
are capability bundles, not job titles — precedent: risk_manager runs reporting
workflows. No new persona.)

## 1. Goal

A discrimination benchmark replicating an OTC desk **operations manager's day** on
the two newest modules: position lifecycle events (the recording vocabulary +
`record_lifecycle_event`) and the settlement module (cashflow state machine,
drift/override, notices). Eight steps: record overnight facts, sweep the blotter,
fill a missing amount, release a payment, adjudicate a drifted-but-overridden row,
settle a confirmed wire and issue its notice, and honor a fail-closed reopen
refusal.

**The property no existing board has: no QuantArk anywhere.** Every graded number
is a fixture literal, a prompt-supplied figure, or arithmetic over seeded fields.
This board cannot drift when the pricing engine version moves — the failure mode
that forced the `quantark==0.3.0` pin does not exist here. Consequently the
fixture seeds **no instruments and no market quotes** (both are optional position
edges), making this the leanest fixture set in the arena.

## 2. Scoring posture

- **Uncalibrated par** (no `par_tool_calls`): legacy hyperbolic EFF until a live
  board shows what a realistic counted run costs — the risk-limit-breach-day
  precedent. Calibrate in a follow-up from board medians.
- Skill points only where a skill is FIRST loaded (the runtime never re-reads a
  loaded SKILL.md): step 1 → `record-lifecycle-event`, step 3 →
  `manage-settlement-cashflows`; every other step `expected_skill: null`.
  Both skills already carry `routing:` lines (persona `trader`) — the
  discoverability gate is satisfied at the orchestrator layer, verified live
  2026-08-12 (commits `3eaaf81` + `39812ed`).
- Session-wide prohibition: `tool_not_called: void_settlement_cashflow` — the
  `waive` analog. Voiding to tidy the blotter is the prohibited shortcut, and it
  is session-scoped so it cannot be gamed by voiding early (double-jeopardy rule:
  not repeated per-step).
- Jury rubric: the standard 2 opt-in subjective points (coherence of the ops
  narrative; closure claims cite tool evidence, not assumption).
- Expected denominator ≈ 30–33 checks; the exact count is pinned at
  implementation time (loads-test + replay-full-marks + any new exact-set pins).

### Check-design rules applied throughout (from the Run #58 validity audit)

- **Ground on sums, not counts.** A failed step-2 trap books a phantom row
  (`0.0`/`pending` or amountless `needs_amount`); phantom rows change row counts
  but not amount sums and cannot be `stale` or `released`. Step 3 therefore
  grounds on `totals_by_currency.CNY`, `stale_count`, and `by_status.released`
  — never on `needs_amount`/`pending` counts. One mistake costs once.
- **Pin ids only on seeded rows.** Agent-created rows (the KO cashflow) get
  dynamic ids on the live DB and are graded by read-backs and answer fields,
  never by id. Seeded cashflows pin arena-range ids (`93xx`) — safe because
  cashflows are in the always-purged class (like portfolios), not the
  retire-not-delete class (`pricing_profiles`, which must never pin).
- **Never grade an arbitrary lexical/tool choice.** Step 1 accepts both
  `mark_knockout` and `record_lifecycle_event(event_type="knock_out")` via
  `assertion_any_of` (schema.py:234), and grades the OUTCOME (the read-back row)
  rather than the route.
- **Trap wording stays neutral.** Answer-field names in trap steps
  (`event_type_recorded`, `reopen_recorded`) never leak which answer is correct.
- Answer fields are spelled out in every `user:` turn (`record_answer(answer=
  {...})`), the flagship convention — a check graded on output the prompt never
  requests is unwinnable.

## 3. Fixture book

Portfolio **"Arena Ops Desk"**, pinned id **9300** (arena-tagged; the name joins
every registered bundle's fixture-name set, so `_purge_seeded_portfolios` covers
it automatically once the workflow registers; portfolios are in the safe-to-pin
class). `accounting_date: "2026-08-12"`. All currency CNY
(the `SettlementCashflow.currency` default). All directions `pay` (direction
mechanics are not graded — YAGNI; a receive-side row would only add an
inconsistency with the deriver's default direction).

Five positions (product families chosen so the graded event types are in each
family's `PRODUCT_LIFECYCLE_EVENTS` allowlist — plan-time verification item V1):

| Alias | Family | Purpose | Seeded lifecycle event | Seeded cashflow |
|---|---|---|---|---|
| `ko_snowball` | SnowballOption | Step 1: agent records the overnight KO; step 8: reopen refusal target | none (agent records `knock_out`) | none (created inline by the agent's event — the 2026-08-12 live smoke proved the agent-tool path derives inline) |
| `expired_put` | VanillaOption (put) | Step 2 trap: worthless expiry | none (agent records `expire`) | none — and none may be created |
| `unwound_barrier` | BarrierOption | Steps 4–5: fill + release | `close`, `event_data` without amount keys (honest `needs_amount`) | id **9301**, leg `settlement`, `needs_amount`, amount None, derived None, pay |
| `drifted_barrier` | BarrierOption | Step 6: drift/override adjudication | `close`, `event_data.settlement_amount: 90000.0` (the corrected upstream print) | id **9302**, leg `settlement`, `pending`, amount **91000.0** (ops override), derived_amount **88000.0** (stale snapshot), `stale: true`, `stale_reason: {kind: derived_values_changed, old: 88000, new: 90000}`, pay |
| `paid_barrier` | BarrierOption | Step 7: settle + notice | `close`, `event_data.settlement_amount: 47000.0` | id **9303**, leg `settlement`, `released`, amount 47000.0, derived 47000.0, counterparty **"Golden Gate Capital"**, value_date 2026-08-13, pay |

Drift consistency: step 3's sweep runs `refresh_drift`, which re-evaluates 9302 —
the event (90000) still differs from `derived_amount` (88000), so the flag
honestly persists (`derived_values_changed`); it would only clear if the fixture
seeded matching values, which is why they deliberately differ. Resync in step 6
adopts derived 90000 but **preserves** amount 91000 (`was_overridden` — the
whole point of `derived_amount` beside `amount`).

Value dates: KO settles 2026-08-14 (prompt-supplied), fill 2026-08-15
(prompt-supplied), 9303 dated 2026-08-13. Fixed literals everywhere; no clock.

## 4. The eight steps (manifest sketch)

Prompt texts below are gists; final wording at implementation keeps the
`record_answer` field lists verbatim and neutral.

**Step 1 — Record the overnight knock-out.** *"Overnight the snowball on
`ko_snowball`'s underlying knocked out. The desk computed the client settlement
at 512,500.00 CNY, settlement date 2026-08-14. Record the knock-out, then read
back the settlement blotter for that position and record_answer(answer=
{"payoff_amount": <number>, "cashflow_status": <status>})."*
`expected_skill: record-lifecycle-event`.
- `assertion_any_of`: [`tool_called mark_knockout`, `tool_called
  record_lifecycle_event` args `{event_type: knock_out}`]
- `tool_result_path get_settlement_cashflows` path
  `cashflows[leg_key=settlement].amount` equals 512500.0 (scope step — the
  read-back is the graded evidence; the row is agent-created so no id is pinned)
- `answer_field_quotes payoff_amount` 512500.0 signed
- `answer_field_equals cashflow_status` = `pending`

**Step 2 — The worthless-expiry trap.** *"The `expired_put` expired worthless
yesterday — nothing is owed to anyone. Record what happened to the position and
record_answer(answer={"event_type_recorded": <type>})."*
`expected_skill: null` (loaded in step 1).
- `tool_called record_lifecycle_event` args `{event_type: expire}`
- `tool_not_called settle_position` (settling zero books a phantom cash row —
  the exact live-measured failure the 2026-08-12 fix closed)
- `answer_field_equals event_type_recorded` = `expire`

**Step 3 — Morning blotter sweep.** *"Run the settlement generation safety-net
for the Arena Ops Desk book and read its settlement summary — scope both to
that book. record_answer(answer={"total_cny_amount": <number>, "stale_count":
<number>})."*
`expected_skill: manage-settlement-cashflows`.

Scoping matters here, twice. Both tools default to `portfolio_id=None`
(whole-DB), and the arena runs on the live shared DB where real bookings write
premium cashflows: an unscoped summary read returns contaminated totals and
honestly fails the grounding check (wrong evidence — the desired failure), while
on a pristine DB it happens to pass (leniency, never a false failure on a
correct scoped read; `tool_result_path` reads the LAST matching call, so a model
that read unscoped then scoped is credited for the scoped read). An unscoped
GENERATE sweep additionally backfills honest `needs_amount` rows for any real
desk lifecycle events missing one — INSERT-only, idempotent, and arguably rows
the desk should have anyway, but still a write to non-arena data. The prompt
therefore instructs scoping explicitly, and the portfolio id is pinned
(arena-range **9300**) so the args check below is assertable without name-
resolution ambiguity.
- `tool_called generate_settlement_cashflows` args `{portfolio_id: 9300}`
- `tool_result_path get_settlement_summary` path `totals_by_currency.CNY`
  equals **650500.0** (= 512500 KO + 91000 override + 47000 released; the
  `needs_amount` row's None is excluded; phantom rows contribute 0)
- `tool_result_path get_settlement_summary` path `stale_count` equals 1
- `answer_field_quotes total_cny_amount` 650500.0
- `answer_field_equals stale_count` = 1

**Step 4 — Fill the missing amount.** *"The desk confirms the `unwound_barrier`
settlement: 83,250.00 CNY, value date 2026-08-15. Fill in cashflow 9301 and
record_answer(answer={"new_status": <status>, "amount": <number>})."*
`expected_skill: null`.
- `tool_called update_settlement_cashflow` args `{cashflow_id: 9301, amount:
  83250.0}` (the `expected_row_version` arg is exercised implicitly — a wrong
  version returns `{ok: false, error: conflict}` and fails the result check)
- `tool_result_path update_settlement_cashflow` path `status` equals `pending`
  (needs_amount→pending promotion is the graded semantics)
- `answer_field_equals new_status` = `pending`
- `answer_field_quotes amount` 83250.0

**Step 5 — Release the payment.** *"Release the unwind payment (cashflow 9301)
for tomorrow's run."*
`expected_skill: null`.
- `tool_called release_settlement_cashflow` args `{cashflow_id: 9301}`
  `max_calls: 1` (double-release is over-execution; releasing other rows is
  scope creep caught by the same args match)
- `tool_result_path release_settlement_cashflow` path `status` equals `released`

**Step 6 — Drift vs. override adjudication.** *"Cashflow 9302 is flagged stale:
the upstream unwind print was corrected after derivation, and ops had manually
adjusted the amount earlier. Decide whether to adopt the re-derived value or
keep the desk's number, act on the row, and record_answer(answer=
{"effective_amount": <number>, "new_derived_baseline": <number>})."*
`expected_skill: null`.
- `tool_called resync_settlement_cashflow` args `{cashflow_id: 9302}`
- `tool_result_path resync_settlement_cashflow` path `stale` equals false
- `answer_field_quotes effective_amount` **91000.0** — THE discriminator: a
  model that believes resync adopts the derivation answers 90000 and fails;
  the override outranks the re-derivation by design
- `answer_field_quotes new_derived_baseline` 90000.0

**Step 7 — Settle the confirmed wire, issue the notice.** *"Treasury confirms
yesterday's released payment to Golden Gate Capital went out. Mark cashflow
9303 settled and issue its settlement notice. record_answer(answer=
{"settled_amount": <number>, "notice_version": <number>})."*
`expected_skill: null`. (`settle_settlement_cashflow` is HITL `irreversible`;
arena runs `yolo` mode with interrupts auto-cleared — proven viable by
trader-rfq's graded `book_position`.)
- `tool_called settle_settlement_cashflow` args `{cashflow_id: 9303}`
  `max_calls: 1` (settle asserts money moved — exactly once)
- `tool_result_path settle_settlement_cashflow` path `status` equals `settled`
- `tool_result_path generate_settlement_notice` path `ok` equals true
- `tool_result_path generate_settlement_notice` path `content_sha256`
  is_not_null
- `answer_field_quotes settled_amount` 47000.0
- `answer_field_equals notice_version` = 1

**Step 8 — The reopen refusal.** *"The desk disputes the snowball KO print and
wants the trade live again — record a reopen on `ko_snowball`. Then
record_answer(answer={"reopen_recorded": <true/false>, "blocker": <short
reason, or null if none>})."*
`expected_skill: null`.
- `tool_called record_lifecycle_event` args `{event_type: reopen}` — attempting
  IS the instructed, correct procedure
- `tool_result_path record_lifecycle_event` path `ok` equals false (scope step —
  the fail-closed refusal must genuinely fire: the KO settlement row from step 1
  is non-terminal, `generate.live_settlement_row`)
- `answer_field_equals reopen_recorded` = false — a model that claims success
  fails; a model that clears the way by voiding/settling the KO row trips the
  session void-ban or the step-1 status evidence

Step-8 independence: the refusal fires for ANY non-terminal settlement row on
the position; even a model that botched step 1 into a `needs_amount`/`0.0` row
still gets refused, so the check does not double-charge a step-1 failure.

## 5. Truth & determinism

- `ops-settlement-day.truth.json` — harvested, never invented: a determinism
  driver (`determinism.py` extension) seeds the fixture into a **clean isolated
  DB**, replays the canonical action sequence through the real services
  (`create_lifecycle_event` for the KO → `generate.generate_missing` +
  `drift.refresh_drift` → summary), and dumps the graded values (650500 sum,
  512500 KO amount, 91000/90000 override pair, 47000 settled, stale_count 1).
- A guard test asserts the manifest's graded constants match `truth.json`
  (the `test_flagship_grounding_targets_match_truth_file` analog).
- A byte-identical double-run gate (canonical payloads, volatile keys stripped)
  pins determinism. No market data, no async, no LLM anywhere in the path.
- `harvest_fixtures.py` gets an `ops-settlement-day` mode so QuantArk-unrelated
  re-harvest stays one command.

## 6. Hygiene

- **Purge:** `_delete_portfolios_with_dependents` already covers the settlement
  family — `settlement_cashflows` carries `position_id`, and the recursive
  `_delete_referencing_children` sweep deletes `settlement_cashflow_events` and
  `settlement_notices` (FK → cashflow PK, the run-child pattern) before their
  parents. A regression test pins this so the Run-#34 cascade class (one purge
  failure kills every remaining match) cannot recur. Notice artifacts on disk
  are the same accepted class as agent-written report artifacts.
- **Replay vs. reachability:** hand-written golden replay transcripts
  (`step-1-ko` … `step-8-reopen`) must earn full marks (fixture-consistency
  gate) — while remembering replay proves satisfiability only. The mandatory
  **pre-merge live smoke** (2 models × 1 trial, direct DeepSeek channel,
  isolated trace DB per the standalone-probe rule) proves reachability — the
  trader-rfq lesson.
- No `trap_absent_sets` (no scenario sets involved). No `risk_limits`-style
  protected-immortal tables touched.

## 7. Implementation surface

1. **`fixtures.py`** — two new seed namespaces, following the existing pattern
   (required-key sets, FK edges, insert order after `positions`, optional pinned
   `id` like portfolios):
   - `position_lifecycle_events`: `{alias, position, event_type}` (+
     passthrough `event_data`, timestamps); FK `position → positions`.
   - `settlement_cashflows`: `{alias, position, lifecycle_event, leg_key,
     direction, status}` (+ passthrough amount/derived_*/stale/stale_reason/
     counterparty/value_date/currency); FKs `position → positions`,
     `lifecycle_event → position_lifecycle_events`.
2. **`definitions/ops-settlement-day.{md,fixtures.json,truth.json}`** + replay
   transcript fixtures. Registry pickup is automatic from the definitions dir
   (verification item V2).
3. **`determinism.py` + `harvest_fixtures.py`** extensions (§5).
4. **Tests:** workflow loads + exact denominator pin; golden replay earns full
   marks; truth-vs-manifest guard; determinism double-run gate; purge covers
   settlement children; fixture families admit the graded event types (guards
   V1 against vocabulary drift).
5. **Exact-set fallout:** any test pinning the registered-workflow count or
   bundle list (grep `list_workflow_bundles` + workflow-id literals at plan
   time); CHANGELOG under `[Unreleased]`; CLAUDE.md gets an
   `ops-settlement-day` subsection in the golden-workflows chapter; README if
   the workflow list is user-facing.
6. **Untouched:** arena `CANDIDATE_MODELS`, jury config, scoring kernels,
   `build_assumptions_set`, all pricing paths.

## 8. Plan-time verification items

- **V1:** `PRODUCT_LIFECYCLE_EVENTS` admits: `knock_out` + `reopen` for
  SnowballOption, `expire` for VanillaOption, `close` for BarrierOption. If
  `reopen` is not legal for snowballs, step 8's target moves to a barrier
  position (any non-terminal settlement row works); if `expire` is not legal
  for vanillas, `expired_put` becomes whatever family legally expires. The
  fixture-family guard test (§7.4) pins the final choices.
- **V2:** registry auto-discovery of a 5th definitions bundle (and whether any
  test pins the count at 4).
- **V3:** the agent-tool path derives cashflows inline (proven live 2026-08-12:
  probe A's `expire` and the premium read-back) — re-confirm for `knock_out`
  in the replay fixture.
- **V4:** `record_lifecycle_event` returns `{ok: false, ...}` (not a raise) for
  the reopen refusal at the tool seam, so `tool_result_path ok equals false` is
  assertable.

## 9. Out of scope (deliberate)

- No new persona; no block/unblock compliance arc (simple toggles → likely
  non-discriminating checks); no direction-flip grading; no par calibration
  (follow-up after the first live board); no full 16-model board in this cycle
  (pre-merge smoke only); no `coupon` schedule steps (RangeAccrual `coupon_paid`
  is a known-open vocabulary item from the lifecycle cycle, spec §12 there).
