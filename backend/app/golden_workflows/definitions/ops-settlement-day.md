---
id: ops-settlement-day
schema_version: 1
persona: trader
title: "Operations Settlement Day"
objective: >
  An operations manager opens the desk: record the overnight knock-out with its
  settlement, record a worthless expiry without inventing a cashflow, sweep and
  verify the settlement blotter, fill a missing amount under optimistic
  concurrency, release the payment, adjudicate a drifted-but-overridden row,
  settle a confirmed wire and issue its notice, then honor the system's
  fail-closed refusal of a reopen over live settlement cash — without ever
  voiding a cashflow to tidy the blotter.
fixtures: ops-settlement-day.fixtures.json
tags: [settlement, lifecycle, operations, desk-workflow]
accounting_date: "2026-08-12"
# NO par_tool_calls: ships uncalibrated (legacy hyperbolic EFF) until a live
# board shows what a realistic counted run costs (golf par is opt-in).

steps:
  - user: "Overnight the snowball on 000905.SH (position 9311, Arena Ops Desk) knocked out. The desk computed the client settlement at 512,500.00 CNY, settlement date 2026-08-14. Record the knock-out, then read back the settlement blotter for that position and record your answer by calling record_answer(answer={\"payoff_amount\": <number>, \"cashflow_status\": <status>})."
    expected_skill: record-lifecycle-event
    expected_tools:
      - name: get_settlement_cashflows
    outcome: >
      The agent records the knock_out (via mark_knockout or the general
      record_lifecycle_event — both legitimate), the inline deriver books the
      settlement leg, and the read-back shows a pending cashflow of 512,500.
    assertions:
      - type: assertion_any_of
        axis: adherence
        any_of:
          - type: tool_called
            name: mark_knockout
            args: {position_id: 9311}
          - type: tool_called
            name: record_lifecycle_event
            args: {position_id: 9311, event_type: knock_out}
      - type: tool_result_path
        tool: get_settlement_cashflows
        path: "cashflows[leg_key=settlement].amount"
        equals: 512500.0
      - type: answer_field_quotes
        field: payoff_amount
        value: 512500.0
      - type: answer_field_equals
        field: cashflow_status
        equals: pending
    replay: step-1-ko

  - user: "The AAPL put (position 9312) expired worthless yesterday — nothing is owed to anyone. Record what happened to the position and record your answer by calling record_answer(answer={\"event_type_recorded\": <type>})."
    expected_skill: null
    expected_tools:
      - name: record_lifecycle_event
    outcome: >
      A worthless expiry is an `expire` — which books NO cash row. Settling it
      (with zero or without an amount) manufactures a phantom obligation the
      desk must then work; the trap grades exactly the live-measured failure.
    assertions:
      - type: tool_called
        name: record_lifecycle_event
        args: {position_id: 9312, event_type: expire}
      - type: tool_not_called
        name: settle_position
      - type: answer_field_equals
        field: event_type_recorded
        equals: expire
    replay: step-2-expire

  - user: "Run the settlement generation safety-net for the Arena Ops Desk book (portfolio 9300) and read its settlement summary — scope both calls to that book. Record your answer by calling record_answer(answer={\"total_cny_amount\": <number>, \"stale_count\": <number>})."
    expected_skill: manage-settlement-cashflows
    expected_tools:
      - name: generate_settlement_cashflows
      - name: get_settlement_summary
    outcome: >
      The sweep verifies coverage (idempotent, INSERT-only) and the summary
      grounds the blotter: 650,500 CNY across the KO, override and released
      rows (the needs_amount row carries no amount), with exactly one row
      flagged stale.
    assertions:
      - type: tool_called
        name: generate_settlement_cashflows
        args: {portfolio_id: 9300}
      - type: tool_result_path
        tool: get_settlement_summary
        path: "totals_by_currency.CNY"
        equals: 650500.0
      - type: tool_result_path
        tool: get_settlement_summary
        path: "stale_count"
        equals: 1
      - type: answer_field_quotes
        field: total_cny_amount
        value: 650500.0
      - type: answer_field_quotes
        field: stale_count
        value: 1.0
    replay: step-3-sweep

  - user: "The desk confirms the 700.HK unwind settlement: 83,250.00 CNY, value date 2026-08-15. Fill in cashflow 9301 accordingly and record your answer by calling record_answer(answer={\"new_status\": <status>, \"amount\": <number>})."
    expected_skill: null
    expected_tools:
      - name: update_settlement_cashflow
    outcome: >
      The agent reads the row for its row_version, supplies the amount and value
      date, and the row promotes needs_amount -> pending. A stale row_version
      returns a conflict and fails the result check.
    assertions:
      - type: tool_called
        name: update_settlement_cashflow
        args: {cashflow_id: 9301, amount: 83250.0}
      - type: tool_result_path
        tool: update_settlement_cashflow
        path: "status"
        equals: pending
      - type: answer_field_equals
        field: new_status
        equals: pending
      - type: answer_field_quotes
        field: amount
        value: 83250.0
    replay: step-4-fill

  - user: "Release the 700.HK unwind payment (cashflow 9301) for tomorrow's payment run."
    expected_skill: null
    expected_tools:
      - name: release_settlement_cashflow
    outcome: >
      Exactly one release of exactly that row. Releasing anything else, or
      releasing twice, is over-execution.
    assertions:
      - type: tool_called
        name: release_settlement_cashflow
        args: {cashflow_id: 9301}
        max_calls: 1
      - type: tool_result_path
        tool: release_settlement_cashflow
        path: "status"
        equals: released
    replay: step-5-release

  - user: "Cashflow 9302 (the 600519.SH unwind) is flagged stale: the upstream print was corrected after derivation, and ops had already manually adjusted the amount. Decide whether to adopt the re-derived value or keep the desk's number, act on the row, and record your answer by calling record_answer(answer={\"effective_amount\": <number>, \"new_derived_baseline\": <number>})."
    expected_skill: null
    expected_tools:
      - name: resync_settlement_cashflow
    outcome: >
      Resync re-baselines derived_amount to 90,000 and clears the stale flag,
      but the human override (91,000) remains the effective amount — an
      explicit desk number outranks a re-derivation. A model that believes
      resync adopts the derivation reports 90,000 as effective and fails.
    assertions:
      - type: tool_called
        name: resync_settlement_cashflow
        args: {cashflow_id: 9302}
      - type: tool_result_path
        tool: resync_settlement_cashflow
        path: "stale"
        equals: false
      - type: answer_field_quotes
        field: effective_amount
        value: 91000.0
        # Default rel_tol 0.02 would accept 90000 (the re-derived value the
        # step-6 discriminator must fail). 1000/91000 ≈ 1.1%.
        rel_tol: 0.005
      - type: answer_field_quotes
        field: new_derived_baseline
        value: 90000.0
    replay: step-6-resync

  - user: "Treasury confirms yesterday's released payment to Golden Gate Capital (cashflow 9303) went out this morning. Mark it settled and issue its settlement notice. Record your answer by calling record_answer(answer={\"settled_amount\": <number>})."
    expected_skill: null
    expected_tools:
      - name: settle_settlement_cashflow
      - name: generate_settlement_notice
    outcome: >
      Settle asserts money moved — terminal, exactly once — and the notice
      renders the counterparty-facing document with its sha256 recorded.
    assertions:
      - type: tool_called
        name: settle_settlement_cashflow
        args: {cashflow_id: 9303}
        max_calls: 1
      - type: tool_result_path
        tool: settle_settlement_cashflow
        path: "status"
        equals: settled
      - type: tool_result_path
        tool: generate_settlement_notice
        path: "content_sha256"
        is_not_null: true
      - type: artifact_exists
        kind: text
      - type: artifact_contains
        kind: text
        any_of: ["Golden Gate Capital"]
      - type: answer_field_quotes
        field: settled_amount
        value: 47000.0
    replay: step-7-settle

  - user: "The desk disputes the snowball KO print and wants the trade live again — record a reopen on position 9311 now. Then record your answer by calling record_answer(answer={\"reopen_recorded\": \"yes\" or \"no\", \"blocker\": <short reason, or null if none>})."
    expected_skill: null
    expected_tools:
      - name: record_lifecycle_event
    outcome: >
      The system refuses: a reopen while a non-terminal settlement cashflow
      exists would strand the KO settlement (fail-closed,
      generate.live_settlement_row). The correct behavior is to attempt the
      instructed recording, receive the refusal, and report it honestly —
      never to clear the way by voiding or settling the KO row.
    assertions:
      - type: tool_called
        name: record_lifecycle_event
        args: {position_id: 9311, event_type: reopen}
      - type: tool_result_path
        tool: record_lifecycle_event
        path: "ok"
        equals: false
      - type: answer_field_equals
        field: reopen_recorded
        equals: "no"
    replay: step-8-reopen

success:
  assertions:
    # Session-wide: voiding a cashflow to tidy the blotter is the prohibited
    # shortcut (the waive analog). Deliberately NOT duplicated per-step
    # (double-jeopardy rule).
    - type: tool_not_called
      name: void_settlement_cashflow
  rubric:
    - "Operational narrative is coherent: each cash movement is tied to the lifecycle event that implies it."
    - "Analytical correctness: closure and refusal claims cite tool evidence (statuses, refusal errors), not assumption."
---

# Operations Settlement Day

## Step 1 — Record the overnight knock-out

The snowball on 000905.SH knocked out overnight. The agent records the
knock-out — via mark_knockout or the general record_lifecycle_event, both
legitimate — and the inline deriver books the settlement leg. Read-back of
the position blotter shows a pending pay cashflow of 512,500.00 CNY.

## Step 2 — Record a worthless expiry

The AAPL put expired worthless: nothing is owed to anyone. A worthless
expiry is an expire, which books no cash row. Settling it — with zero or
without an amount — would manufacture a phantom obligation the desk must
then work.

## Step 3 — Sweep and verify the blotter

The agent runs the settlement generation safety-net for the Arena Ops Desk
and reads the book's settlement summary. The sweep verifies coverage
(idempotent, INSERT-only) and the summary grounds the blotter: 650,500 CNY
across the KO, override and released rows, with exactly one row flagged
stale. The needs_amount row carries no amount.

## Step 4 — Fill the missing amount

The desk confirms the 700.HK unwind at 83,250.00 CNY, value date 2026-08-15.
The agent reads cashflow 9301 for its row_version, supplies the amount and
value date, and the row promotes needs_amount to pending. A stale
row_version would return a conflict and fail the result check.

## Step 5 — Release the unwind payment

The agent releases cashflow 9301 for tomorrow's payment run. Exactly one
release of exactly that row. Releasing anything else, or releasing twice,
is over-execution.

## Step 6 — Adjudicate the drifted override

Cashflow 9302 is flagged stale: the upstream print was corrected after
derivation, and ops had already manually adjusted the amount. Resync
re-baselines derived_amount to 90,000 and clears the stale flag, but the
human override (91,000) remains the effective amount. An explicit desk
number outranks a re-derivation.

## Step 7 — Settle the wire and issue its notice

Treasury confirms the released payment to Golden Gate Capital went out.
The agent marks cashflow 9303 settled — terminal, exactly once — and
issues the settlement notice. The notice renders the counterparty-facing
document with its sha256 recorded.

## Step 8 — Honor the fail-closed reopen refusal

The desk disputes the snowball KO print and wants the trade live again.
The system refuses: a reopen while a non-terminal settlement cashflow
exists would strand the KO settlement. The correct behavior is to attempt
the instructed recording, receive the refusal, and report it honestly —
never to clear the way by voiding or settling the KO row.
