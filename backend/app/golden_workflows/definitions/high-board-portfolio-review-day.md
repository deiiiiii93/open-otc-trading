---
id: high-board-portfolio-review-day
schema_version: 1
persona: high_board
title: "High-Board Portfolio Review Day"
objective: >
  A board overseer reviews the desk: resolves the control book, curates a
  desk-scoped board-review View, counts the Snowball exposure, takes an inline
  composition summary, pulls the prior persisted governance report as evidence,
  and drafts a fresh board governance report.
fixtures: high-board-portfolio-review-day.fixtures.json
tags: [flagship, high-board, oversight, reporting, desk-workflow]

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
        path: kind
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
        path: kind
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
    replay: step-3-count

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
    replay: step-4-batch

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
    replay: step-5-display

  - user: "Draft the board governance report."
    expected_skill: generate-report
    expected_tools:
      - name: write_report_artifact
    outcome: >
      A board governance report artifact is produced as a thread asset via
      write_report_artifact (not create_report).
    assertions:
      - type: tool_called
        name: write_report_artifact
      - type: artifact_exists
        kind: text
      - type: tool_not_called
        name: create_report
    replay: step-6-generate

success:
  assertions:
    # Procedural-fidelity check on the fully-captured tool-call sequence rather
    # than read_file-derived skills_routed (blind to routing toward an already-
    # loaded skill). Each designed skill step maps to its signature tool; same
    # designed order and bar, minus the dedup blind spot.
    - type: tools_routed_sequence
      names: [get_portfolio, create_portfolio, get_positions, run_report_batch, get_report, write_report_artifact]
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
    - "Grounded the final report in governed evidence: the structural counts and the prior persisted governance report."
    - "Did not present the live batch risk total as a precise governed valuation."
---

## Step 1 — Resolve the desk control book

The overseer asks which book the desk control sits in. The agent routes to
`portfolio-membership`, calls `get_portfolio`, and reports it is a Container.

## Step 2 — Create the board-review view

The overseer asks for a board-review view. The agent routes to
`portfolio-maintenance` and calls `create_portfolio` with `kind=view` sourced from
the desk container.

## Step 3 — Count the Snowball exposure

The overseer asks how many Snowballs are in the view. The agent routes to
`portfolio-view-counting` and calls `get_positions` with a `Snowball` filter,
reporting the subset against the view's full membership.

## Step 4 — Inline composition summary

The overseer asks for an inline composition summary. The agent routes to
`batch-run-reports`, calls `run_report_batch`, and returns counts/breakdown with no
persisted artifact.

## Step 5 — Pull prior governance report

The overseer asks for the prior governance report. The agent routes to
`display-report`, calls `list_reports` (filtered by `status="completed"` — NOT by
`report_type`, whose tool filter only accepts `portfolio`/`risk`/`rfq`; the seeded
report's arena marker is a free `report_type` column value, valid for seeding,
reading, the Step-5 assertion, and cleanup, but not a valid `list_reports` filter
value), then `get_report`, and summarizes the seeded report.

## Step 6 — Generate the board governance report

The overseer asks for a fresh board governance report. The agent routes to
`generate-report` and calls `write_report_artifact`, producing a thread artifact.
