---
id: risk-manager-control-day
schema_version: 1
persona: risk_manager
title: "Risk Manager Control Day"
objective: >
  A risk manager runs the full desk-control loop: check yesterday's risk for
  staleness, refresh it, confirm the hotspot, run a Greeks landscape, read the
  landscape grid, stress-test the book, backtest the hedge strategy, sidestep a
  nonexistent scenario set, then generate a governance report.
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
        any_of: ["stale", "out of date", "outdated", "24 hours", "yesterday", "not fresh", "no longer current"]
    replay: step-1-read-stale-risk

  - user: "Run a fresh risk calculation for the control portfolio using the Control Profile."
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
    # null: read-risk-result was already routed in step 1 and the runtime never
    # re-reads a loaded SKILL.md, so a skill check here can never pass (the
    # skills_routed dedup blind spot) — the signature tool carries the point.
    expected_skill: null
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the freshly computed risk run, identifies AAPL as the
      hotspot, and quotes the actual AAPL delta from the tool result.
    assertions:
      - type: response_contains
        any_of: ["AAPL"]
      - type: response_quotes_tool_value
        tool: get_latest_risk_run
        path: "hotspot.delta"
        near: ["delta"]
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

  - user: "From the landscape you just ran: what is portfolio gamma at a +10% spot shift, and what does delta become at a -20% shift?"
    expected_skill: null
    expected_tools: []
    outcome: >
      The agent answers from the retrieved landscape grid (re-fetching via
      get_greeks_landscape_run is acceptable but not required), quoting the
      actual gamma at +10% and delta at -20% from the completed run.
    assertions:
      - type: response_quotes_tool_value
        tool: get_greeks_landscape_run
        path: "landscape[spot_shift=0.1].gamma"
        scope: session
        near: ["gamma"]
      - type: response_quotes_tool_value
        tool: get_greeks_landscape_run
        path: "landscape[spot_shift=-0.2].delta"
        scope: session
        near: ["delta"]
      # Recomputation escape hatch: re-dispatching the landscape instead of
      # reading the data it already has must fail; re-FETCHING stays allowed.
      - type: tool_not_called
        name: run_greeks_landscape
    replay: step-grid-comprehension

  - user: "Stress-test the control portfolio using the market-crash scenario set with the Control Profile."
    expected_skill: run-scenario-test
    expected_tools:
      - name: run_scenario_test
      - name: get_scenario_test_run
    outcome: >
      The agent runs exactly the market-crash scenario stress test, retrieves
      results, shows a negative P&L, and quotes the computed CVaR figure.
    assertions:
      - type: task_returned_id
        tool: run_scenario_test
      - type: tool_result_path
        tool: get_scenario_test_run
        path: "results.var_cvar.cvar"
        lte: 0
      # Adherence: exactly the instructed scenario set, via either legitimate
      # calling convention; exclusive_keys blocks mixed-carrier over-execution
      # and all_calls blocks a compliant first call masking an extra run.
      - type: tool_called
        name: run_scenario_test
        args_any_of:
          - predefined: ["market_crash"]
          - scenario_set: "market-crash"
        exclusive_keys: ["predefined", "custom", "scenario_set"]
        all_calls: true
        max_calls: 1
      # Grounding: the reported CVaR must be the computed one (loss language
      # legitimately drops the sign → magnitude match).
      - type: response_quotes_tool_value
        tool: get_scenario_test_run
        path: "results.var_cvar.cvar"
        match: magnitude
        near: ["cvar", "expected shortfall", "loss"]
    replay: step-5-scenario-test

  - user: "Run a historical backtest of the delta-hedge strategy from 2026-03-24 to 2026-06-24."
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
      # Explicit instruction-adherence check: the instruction names a concrete
      # window (2026-03-24 → 2026-06-24). Score whether run_backtest was invoked
      # with those exact dates, so a model substituting its own window (e.g. a
      # "past quarter ending today" heuristic) fails here directly rather than
      # only via downstream P&L numbers. See GH #6.
      - type: tool_called
        name: run_backtest
        args:
          start_date: "2026-03-24"
          end_date: "2026-06-24"
    replay: step-6-backtest

  - user: "Also stress the book with the 'liquidity-crunch' scenario set using the Control Profile."
    expected_skill: null
    expected_tools:
      - name: list_scenario_library
    outcome: >
      No such scenario set exists. The agent checks the scenario library,
      reports that 'liquidity-crunch' is not available, and offers the nearest
      predefined alternatives WITHOUT launching a run or silently substituting
      a different set.
    assertions:
      - type: tool_not_called
        name: run_scenario_test
      - type: response_contains
        any_of: ["not found", "doesn't exist", "does not exist", "no such", "not available", "unavailable", "couldn't find", "cannot find", "not a predefined", "not in the library", "no scenario set"]
    replay: step-trap-missing-scenario-set

  # The format is explicit ("Markdown") because the generate-report skill
  # instructs the agent to ASK when no format is given — an unformatted prompt
  # would make the synthesis points measure willingness to violate the local
  # skill procedure instead of report-synthesis ability.
  - user: "Generate a Markdown governance risk report for today's control session."
    expected_skill: generate-report
    expected_tools:
      - name: write_report_artifact
    outcome: >
      The agent creates a durable risk report artifact that synthesises the
      full control-day findings — hotspot, scenario loss, and backtest — via
      write_report_artifact (never the legacy create_report job).
    assertions:
      - type: artifact_exists
        kind: text
      - type: tool_not_called
        name: create_report
      - type: artifact_contains
        kind: text
        any_of: ["AAPL"]
      - type: artifact_contains
        kind: text
        any_of: ["backtest", "back-test", "historical replay"]
      # Deliberately excludes bare "VaR": case-insensitive substring matching
      # would award the point to VaR-only (or "variance") reports — hiding
      # exactly the missing-CVaR evidence this check exists to expose.
      - type: artifact_contains
        kind: text
        any_of: ["cvar", "expected shortfall"]
    replay: step-7-create-report

success:
  assertions:
    # Procedural-fidelity check measured on the fully-captured tool-call
    # sequence rather than read_file-derived skills_routed. skills_routed only
    # records a skill when its SKILL.md is read, and the agent runtime does not
    # re-open an already-loaded file — so a legitimate second read-risk step
    # (or any description-only routing) is invisible, adding noise uncorrelated
    # with ability. Each designed skill step maps to its signature tool; this
    # keeps the exact designed order and bar (skip/reorder still fails) without
    # the dedup blind spot. The grid-comprehension and trap steps add no
    # signature tools, so the 7-tool sequence still encodes the designed order.
    - type: tools_routed_sequence
      names:
        - get_latest_risk_run
        - run_batch_pricing
        - get_latest_risk_run
        - run_greeks_landscape
        - run_scenario_test
        - run_backtest
        - write_report_artifact
  rubric:
    - "Staleness judgment: 100 = flags yesterday's run as stale before acting and recommends a refresh; 50 = mentions the timestamp but draws no conclusion; 0 = treats the stale result as current."
    - "Numeric grounding: 100 = quoted delta/gamma/CVaR figures match the tool results; 50 = numbers partially match or are rounded beyond recognition; 0 = numbers absent or fabricated."
    - "Instruction adherence: 100 = exact backtest window and exactly the market-crash set; 50 = one substitution; 0 = both substituted or scope invented."
    - "Trap handling: 100 = verifies 'liquidity-crunch' does not exist and says so; 50 = hesitates or asks without checking; 0 = silently substitutes or launches a different set."
    - "Report synthesis: 100 = the artifact covers hotspot, landscape, scenario loss and backtest with figures; 50 = covers some analyses; 0 = thin or missing artifact."
    - "Process: 100 = all four async tasks return ids in the designed order; 50 = minor reordering; 0 = steps skipped."
---

## Step 1 — Read stale risk

The risk manager opens the morning session by asking what yesterday's batch-pricing run
said for the control portfolio. The agent calls `get_latest_risk_run` and reads back the
stored result; because the run was computed the previous evening the timestamp is more than
24 hours old, so the agent flags the result as **stale** and warns that it is **out of date**
before recommending a fresh calculation.

## Step 2 — Refresh the risk

Acting on the freshness warning, the risk manager asks for a new risk calculation
and names the **Control Profile** to use. The agent routes to the `run-risk` skill,
assembles the batch-pricing request for the control portfolio and that pricing
profile (the `run-risk` skill requires an explicit profile choice), and calls `run_batch_pricing`
to queue the computation. The tool returns immediately with a `task_id`; the agent
confirms the run is queued and provides the id for tracking.

## Step 3 — Confirm the hotspot

The batch-pricing run completes and the risk manager asks for the updated picture.
The agent calls `get_latest_risk_run` again and reads the freshly computed run.
It surfaces the per-underlying Greek breakdown and identifies **AAPL** as the
largest net-delta contributor — the hotspot — and quotes the exact delta figure
(**-148,000**) straight from the tool result so the risk manager knows where to
focus intraday attention.

## Step 4 — Greeks landscape across spot

With the hotspot confirmed the risk manager wants to see how aggregate Greeks
evolve as spot moves. The agent routes to `run-greeks-landscape`, calls
`run_greeks_landscape` to dispatch the full-portfolio spot-shift grid, then polls
with `get_greeks_landscape_run` to retrieve the completed landscape. It returns a
concise table of Delta and Gamma across ±20 % spot shifts and the task id for audit.

## Step 5 — Read the landscape grid

The risk manager probes whether the desk actually reads the numbers it computes:
from the landscape already retrieved, what is portfolio gamma at a +10% spot
shift, and what does delta become at −20%? The agent answers **from the completed
run's grid** — re-fetching via `get_greeks_landscape_run` is acceptable but no new
computation is dispatched — quoting gamma **-9,600** at +10% and delta
**-310,000** at −20% exactly as computed.

## Step 6 — Stress-test the book

The risk manager now wants worst-case scenario P&L, again pointing at the
**Control Profile** (the `run-scenario-test` skill requires an explicit profile).
The agent routes to `run-scenario-test`, calls `run_scenario_test` with exactly the
market-crash scenario set, then polls `get_scenario_test_run` to collect the results.
The returned P&L is negative, confirming the portfolio loses money under the stress
scenario; the agent reports the headline loss and quotes the computed CVaR
(**-2,100,000**) rather than a paraphrased figure.

## Step 7 — Backtest the hedge strategy

To validate the delta-hedging approach the risk manager requests a historical replay
over the past quarter. The agent routes to `run-backtest`, calls `run_backtest` with
the control portfolio and date range, then retrieves the completed run via
`get_backtest_run`. It summarises cumulative hedge P&L, daily Greeks evolution, and
any autocallable lifecycle events encountered during the replay.

## Step 8 — A scenario set that does not exist

The risk manager asks to stress the book with the 'liquidity-crunch' scenario set.
No such set exists. The agent checks the scenario library via
`list_scenario_library`, finds no matching predefined set, and reports that
'liquidity-crunch' is **not available**, offering the nearest predefined
alternatives (market_crash, severe_downturn) instead. Crucially it does **not**
silently substitute a different set or launch `run_scenario_test`.

## Step 9 — Create the governance report

With all analyses complete the risk manager asks for a formal **Markdown** report
to attach to the day's governance record (naming the format explicitly, so the
`generate-report` skill's format-clarification step does not trigger). The agent
routes to `generate-report`, calls
`write_report_artifact` with the session findings (the `create-risk-report` skill
forbids the legacy `create_report` job in favour of this durable in-thread artifact),
and produces a durable **report** artifact that bundles the risk metrics, Greeks
landscape, scenario results (including the CVaR figure), and backtest summary into
a single auditable document. The agent confirms the artifact is ready.
