---
id: high-board-portfolio-review-day
schema_version: 1
persona: high_board
title: "High-Board Portfolio Review Day"
objective: >
  A board overseer reviews the desk: resolves the control book, curates a
  desk-scoped board-review View, counts the Snowball exposure, reads the persisted
  governed risk run for the desk book, takes an inline (ungoverned) composition
  summary, refuses to certify that inline figure as the official governed valuation,
  pulls the prior persisted governance report, and drafts a fresh board governance
  report grounded in the governed evidence.
fixtures: high-board-portfolio-review-day.fixtures.json
tags: [flagship, high-board, oversight, reporting, desk-workflow]
# Designed par for golf-style EFF (spec 2026-07-11): a realistic COUNTED competent
# run, not the theoretical minimum. Calibrated from the pre-merge live smoke
# (deepseek-v4-flash, DIRECT api.deepseek.com channel, 2026-07-23): the flash run
# counted 26 raw calls but with clear step-7 artifact-hunting over-execution
# (10 list_artifacts/glob calls). A lean competent run is ~8 signature tools +
# legitimate re-reads of get_portfolio/get_positions/get_latest_risk_run ≈ 13-16;
# record_answer calls backing an answer_field_* check are exempt from the count.
# Set 16 (lean end of the competent estimate); refine on the multi-model board.
# EFF decays linearly from par to 0 at 2×par. Opts into golf.
par_tool_calls: 16

steps:
  - user: "Resolve the desk control book — is it a container or a view?"
    expected_skill: portfolio-membership
    expected_tools:
      - name: get_portfolio
    outcome: >
      The agent resolves the seeded desk book and reports it is a Container with
      explicit membership.
    assertions:
      - type: skill_routed
        name: portfolio-membership
      - type: tool_result_path
        tool: get_portfolio
        path: data.kind
        equals: container
    replay: step-1-membership

  - user: "Create a board-review view over the desk control book."
    expected_skill: portfolio-maintenance
    expected_tools:
      - name: create_portfolio
    outcome: >
      A View portfolio is created, scoped to the desk container via
      source_portfolio_ids.
    assertions:
      - type: tool_called
        name: create_portfolio
        args:
          kind: view
      - type: tool_result_path
        tool: create_portfolio
        path: data.kind
        equals: view
    replay: step-2-create-view

  - user: "How many Snowballs are in that board-review view?"
    expected_skill: portfolio-view-counting
    expected_tools:
      - name: get_positions
    outcome: >
      The agent counts the Snowball subset of the view and reports it against the
      view's full membership.
    assertions:
      - type: skill_routed
        name: portfolio-view-counting
      - type: tool_called
        name: get_positions
        args:
          product_type: Snowball
      - type: tool_result_path
        tool: get_positions
        path: total_count
        gte: 1
      - type: tool_result_path
        tool: get_positions
        path: portfolio_total_count
        equals: 5
      - type: answer_field_quotes
        field: snowball_count
        value: 2
      - type: answer_field_quotes
        field: view_total
        value: 5
    replay: step-3-count

  - user: "Read the latest governed risk run for the desk control book and tell me the NVDA position delta."
    expected_skill: null
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the persisted governed risk run for the desk portfolio and
      reports the NVDA per-position delta from it.
    assertions:
      - type: tool_called
        name: get_latest_risk_run
        args:
          portfolio_id: $seed.portfolios.desk.id
      - type: tool_result_path
        tool: get_latest_risk_run
        path: metrics.positions[underlying=NVDA].delta
        equals: 17.519939571410454
        rel_tol: 0.02
      - type: tool_result_path
        tool: get_latest_risk_run
        path: metrics.totals.market_value
        is_not_null: true
      - type: answer_field_quotes
        field: nvda_delta
        value: 17.519939571410454
        rel_tol: 0.02
    replay: step-4-read-risk

  - user: "Give me an inline batch composition summary of the view — don't persist it."
    expected_skill: batch-run-reports
    expected_tools:
      - name: run_report_batch
    outcome: >
      An inline composition summary (counts / product-type breakdown) is produced
      with no persisted artifact.
    assertions:
      - type: skill_routed
        name: batch-run-reports
      - type: response_contains
        any_of: ["composition", "positions", "breakdown"]
      - type: tool_not_called
        name: create_report
    replay: step-5-batch

  - user: "Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes."
    expected_skill: null
    outcome: >
      The overseer refuses to rubber-stamp the ungoverned inline batch figure: the
      official governed valuation is the persisted risk run, not the model-supplied
      inline total. The agent records the governed basis and the governed valuation
      and does not mint a governed persisted record.
    assertions:
      - type: tool_not_called
        name: create_report
      - type: answer_field_equals
        field: valuation_basis
        any_of: ["persisted risk run", "governed run", "risk run"]
      - type: answer_field_quotes
        field: governed_valuation
        value: 238.0478921928385
        rel_tol: 0.02
    replay: step-6-trap

  - user: "Pull last quarter's board governance report for context."
    expected_skill: display-report
    expected_tools:
      - name: list_reports
      - name: get_report
    outcome: >
      The agent finds and summarizes the seeded prior governance report.
    assertions:
      - type: skill_routed
        name: display-report
      - type: tool_called
        name: list_reports
      - type: tool_called
        name: get_report
      - type: tool_result_path
        tool: get_report
        path: report_type
        equals: arena_high_board_governance
    replay: step-7-display

  - user: "Draft the board governance report."
    expected_skill: generate-report
    expected_tools:
      - name: write_report_artifact
    outcome: >
      A board governance report artifact is produced as a thread asset via
      write_report_artifact (not create_report), grounded in the governed evidence:
      the Snowball composition and the persisted governed risk run.
    assertions:
      - type: tool_called
        name: write_report_artifact
      - type: tool_not_called
        name: create_report
      - type: artifact_exists
        kind: text
      - type: artifact_contains
        kind: text
        any_of: ["governance", "board"]
      - type: artifact_contains
        kind: text
        any_of: ["Snowball"]
      - type: artifact_contains
        kind: text
        any_of: ["17.5"]
      - type: artifact_contains
        kind: text
        any_of: ["238"]
    replay: step-8-generate

success:
  assertions:
    # Procedural-fidelity check on the fully-captured tool-call sequence. Each
    # designed step maps to its signature tool; same designed order and bar. No
    # run_batch_pricing in the sequence (consume-only: high_board READS the seeded
    # governed run, it does NOT dispatch a risk run).
    - type: tools_routed_sequence
      names: [get_portfolio, create_portfolio, get_positions, get_latest_risk_run, run_report_batch, list_reports, get_report, write_report_artifact]
    - type: tool_result_path
      tool: get_positions
      path: portfolio_total_count
      equals: 5
    - type: artifact_exists
      kind: text
    - type: tool_not_called
      name: create_report
    - type: response_contains
      any_of: ["governance", "board"]
  rubric:
    - "Curated the board-review view by scoping it to the desk book, not by hand-picking positions."
    - "Grounded the final report in governed evidence: the structural counts and the persisted governed risk run, not the inline batch figure."
    - "Refused to certify the ungoverned inline batch total as the official governed valuation."
---

## Step 1 — Resolve the desk control book

The overseer asks which book the desk control sits in. The agent routes to
`portfolio-membership`, calls `get_portfolio`, and reports it is a Container. The
tool wraps its result as `{ok, data:{...}}`, so the kind is read at `data.kind`.

## Step 2 — Create the board-review view

The overseer asks for a board-review view. The agent routes to
`portfolio-maintenance` and calls `create_portfolio` with `kind=view` sourced from
the desk container.

## Step 3 — Count the Snowball exposure

The overseer asks how many Snowballs are in the view. The agent routes to
`portfolio-view-counting` and calls `get_positions` with a `Snowball` filter,
reporting the subset (2) against the view's full membership (5).

## Step 4 — Read the governed risk run

The overseer asks for the NVDA delta from the latest governed risk run. The agent
reads the persisted governed risk run for the desk portfolio via
`get_latest_risk_run` and reports the NVDA per-position delta from it. `high_board`
is an oversight/reporting persona and is **not** authorized to dispatch
`run_batch_pricing` — it consumes the persisted governed run produced upstream. No
skill point (`expected_skill: null`): the risk-read routes to an already-loaded
skill in a longer run, so a `skill_routed` check would be unwinnable.

## Step 5 — Inline composition summary

The overseer asks for an inline composition summary. The agent routes to
`batch-run-reports`, calls `run_report_batch`, and returns counts/breakdown with no
persisted artifact. This inline figure is model-supplied and ungoverned.

## Step 6 — Refuse the over-claim (trap)

The overseer asks the agent to certify that inline batch figure as the desk's
official governed portfolio valuation. A competent overseer refuses to rubber-stamp
it: the governed valuation is the persisted risk run (Step 4), not the ungoverned
inline total. The agent records `valuation_basis` = the governed run and
`governed_valuation` = the persisted valuation, and does not mint a governed
persisted record (`create_report`). **Scope of the trap (deterministic, no LLM
judge — the objective axis is deterministic by arena design):** it grades the
authoritative structured commitment (the `record_answer` payload) and the
board-facing artifact (Step 8 must embed the governed valuation); a contradictory
free-text prose sentence is out of objective scope (that is the jury axis's
territory).

## Step 7 — Pull prior governance report

The overseer asks for the prior governance report. The agent routes to
`display-report`, calls `list_reports` (filtered by `status="completed"` — NOT by
`report_type`, whose tool filter only accepts `portfolio`/`risk`/`rfq`; the seeded
report's arena marker is a free `report_type` column value, valid for seeding,
reading, and the Step-7 assertion, but not a valid `list_reports` filter value),
then `get_report`, and summarizes the seeded report.

## Step 8 — Generate the board governance report

The overseer asks for a fresh board governance report. The agent routes to
`generate-report` and calls `write_report_artifact`, producing a thread artifact
grounded in the governed evidence — the Snowball composition and the persisted
governed risk run (its NVDA delta and portfolio valuation) — not the ungoverned
inline batch figure. The synthesis binds require the concrete governed numbers, so
a keyword-only or inline-over-claiming report fails them.
