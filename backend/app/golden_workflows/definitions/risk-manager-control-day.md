---
id: risk-manager-control-day
schema_version: 1
persona: risk_manager
title: "Risk Manager Control Day"
objective: >
  A risk manager runs the full desk-control loop: check yesterday's risk for
  staleness, refresh it, confirm the hotspot, run a Greeks landscape, stress-test
  the book, backtest the hedge strategy, then generate a governance report.
fixtures: risk-manager-control-day.fixtures.json
tags: [flagship, risk, daily-control, desk-workflow]

steps:
  - user: "What does the latest risk say for the control portfolio?"
    expected_skill: read-risk-result
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the persisted risk run and reports that it is stale,
      flagging it as out of date for the risk manager.
    assertions:
      - type: response_contains
        any_of: ["stale", "out of date"]
    replay: step-1-read-stale-risk

  - user: "Run a fresh risk calculation for the control portfolio."
    expected_skill: run-risk
    expected_tools:
      - name: run_batch_pricing
    outcome: >
      The agent queues a batch-pricing run and returns the task id for tracking.
    assertions:
      - type: task_returned_id
        tool: run_batch_pricing
    replay: step-2-run-risk

  - user: "Now check the updated risk result — what's the hotspot?"
    expected_skill: read-risk-result
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the freshly computed risk run and identifies AAPL as the
      largest Greek contributor (the hotspot).
    assertions:
      - type: tool_result_path
        tool: get_latest_risk_run
        path: "hotspot.underlying"
        equals: "AAPL"
    replay: step-3-read-fresh-risk

  - user: "Run a Greeks landscape across spot shifts for the control portfolio."
    expected_skill: run-greeks-landscape
    expected_tools:
      - name: run_greeks_landscape
      - name: get_greeks_landscape_run
    outcome: >
      The agent dispatches the Greeks landscape computation and retrieves the
      completed run result, returning the task id.
    assertions:
      - type: task_returned_id
        tool: run_greeks_landscape
    replay: step-4-greeks-landscape

  - user: "Stress-test the control portfolio using the market-crash scenario set."
    expected_skill: run-scenario-test
    expected_tools:
      - name: run_scenario_test
      - name: get_scenario_test_run
    outcome: >
      The agent runs the scenario stress test, retrieves results, and shows
      a negative P&L under the market-crash scenarios.
    assertions:
      - type: task_returned_id
        tool: run_scenario_test
      - type: tool_result_path
        tool: get_scenario_test_run
        path: "pnl"
        lte: 0
    replay: step-5-scenario-test

  - user: "Run a historical backtest of the delta-hedge strategy over the past quarter."
    expected_skill: run-backtest
    expected_tools:
      - name: run_backtest
      - name: get_backtest_run
    outcome: >
      The agent runs the backtest and retrieves the completed run, returning
      the task id and a headline P&L summary.
    assertions:
      - type: task_returned_id
        tool: run_backtest
    replay: step-6-backtest

  - user: "Generate a governance risk report for today's control session."
    expected_skill: create-risk-report
    expected_tools:
      - name: create_report
    outcome: >
      The agent creates a durable risk report artifact summarising the full
      control-day findings.
    assertions:
      - type: artifact_exists
        kind: report
    replay: step-7-create-report

success:
  assertions:
    - type: skills_routed_sequence
      names:
        - read-risk-result
        - run-risk
        - read-risk-result
        - run-greeks-landscape
        - run-scenario-test
        - run-backtest
        - create-risk-report
    - type: task_returned_id
      tool: run_batch_pricing
    - type: task_returned_id
      tool: run_greeks_landscape
    - type: task_returned_id
      tool: run_scenario_test
    - type: task_returned_id
      tool: run_backtest
    - type: artifact_exists
      kind: report
  rubric:
    - "The agent correctly identifies staleness before taking any action."
    - "All four async tasks (risk, Greeks, scenario, backtest) return task IDs."
    - "AAPL is surfaced as the hotspot after the fresh risk run."
    - "A durable report artifact is produced at the end of the control session."
---

## Step 1 — Read stale risk

The risk manager opens the morning session by asking what yesterday's batch-pricing run
said for the control portfolio. The agent calls `get_latest_risk_run` and reads back the
stored result; because the run was computed the previous evening the timestamp is more than
24 hours old, so the agent flags the result as **stale** and warns that it is **out of date**
before recommending a fresh calculation.

## Step 2 — Refresh the risk

Acting on the freshness warning, the risk manager asks for a new risk calculation.
The agent routes to the `run-risk` skill, assembles the batch-pricing request for
the control portfolio and the active pricing profile, and calls `run_batch_pricing`
to queue the computation. The tool returns immediately with a `task_id`; the agent
confirms the run is queued and provides the id for tracking.

## Step 3 — Confirm the hotspot

The batch-pricing run completes and the risk manager asks for the updated picture.
The agent calls `get_latest_risk_run` again and reads the freshly computed run.
It surfaces the per-underlying Greek breakdown and identifies **AAPL** as the
largest net-delta contributor — the hotspot — and quotes the exact delta and gamma
figures so the risk manager knows where to focus intraday attention.

## Step 4 — Greeks landscape across spot

With the hotspot confirmed the risk manager wants to see how aggregate Greeks
evolve as spot moves. The agent routes to `run-greeks-landscape`, calls
`run_greeks_landscape` to dispatch the full-portfolio spot-shift grid, then polls
with `get_greeks_landscape_run` to retrieve the completed landscape. It returns a
concise table of Delta and Gamma across ±20 % spot shifts and the task id for audit.

## Step 5 — Stress-test the book

The risk manager now wants worst-case scenario P&L. The agent routes to
`run-scenario-test`, calls `run_scenario_test` with the market-crash scenario set,
then polls `get_scenario_test_run` to collect the results. The returned P&L is
negative, confirming the portfolio loses money under the stress scenario; the agent
reports the headline loss figure and highlights the most adverse scenario.

## Step 6 — Backtest the hedge strategy

To validate the delta-hedging approach the risk manager requests a historical replay
over the past quarter. The agent routes to `run-backtest`, calls `run_backtest` with
the control portfolio and date range, then retrieves the completed run via
`get_backtest_run`. It summarises cumulative hedge P&L, daily Greeks evolution, and
any autocallable lifecycle events encountered during the replay.

## Step 7 — Create the governance report

With all analyses complete the risk manager asks for a formal report to attach to
the day's governance record. The agent routes to `create-risk-report`, calls
`create_report` with the session findings, and produces a durable **report** artifact
that bundles the risk metrics, Greeks landscape, scenario results, and backtest summary
into a single auditable document. The agent confirms the artifact is ready.
