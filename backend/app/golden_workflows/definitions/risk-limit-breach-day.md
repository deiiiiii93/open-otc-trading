---
id: risk-limit-breach-day
schema_version: 1
persona: risk_manager
title: "Risk Limit Breach Day"
objective: >
  A risk manager works an overnight net-delta cap breach to verified closure:
  triage the monitoring alert, identify the driver position from the source
  risk run, acknowledge the incident with a root-cause note, write the
  governance report, decline a premature waiver, refresh risk evidence and
  re-run limit monitoring, then confirm the incident auto-recovered — without
  redundantly resolving it.
fixtures: risk-limit-breach-day.fixtures.json
tags: [limits, risk, incident, desk-workflow]
accounting_date: "2026-06-24"
# NO par_tool_calls: ships uncalibrated (legacy hyperbolic EFF) until a live
# board shows what a realistic counted run costs (golf par is opt-in).

steps:
  - user: "Overnight limit monitoring flagged the Arena Limit Control Book. What is the current limit state? Record your answer by calling record_answer(answer={\"breached_limit\": <limit key or name>, \"observed_net_delta\": <number>})."
    expected_skill: monitor-limits
    expected_tools:
      - name: list_limit_incidents
      - name: get_limit_monitoring_run
    outcome: >
      The agent reads the latest monitoring run and the open incident, finds
      the one breached evaluation among three limits, and records the breached
      limit plus its observed net delta.
    assertions:
      # Both the stable key and the display name are legitimate conventions
      # (never grade an arbitrary lexical choice).
      - type: answer_field_equals
        field: breached_limit
        any_of: ["arena-limit-breach-net-delta", "Desk Net Delta Cap"]
      - type: answer_field_quotes
        field: observed_net_delta
        value: 802.6853882273173
        match: signed
    replay: step-1-triage

  - user: "Why did it breach? Identify the driver position's underlying and its delta contribution. Record your answer by calling record_answer(answer={\"driver_underlying\": <ticker>, \"driver_delta\": <number>})."
    # null: the natural skill here is `read-risk-result`, but it declares no
    # `routing:` frontmatter, so `collect_routing_rows` skips it and it never
    # enters the orchestrator's Known-skills table — reachable only via the
    # persona catalog. Run #101 (18 models × 2 trials) measured the cost:
    # read-risk-result routed in 4/36 trials (11%) while every ROUTED skill on
    # the same board scored 75-97% (run-risk 75, monitor-limits 92,
    # handle-limit-incident 97, generate-report 94) — the Run-#58
    # discoverability class, reproduced. Decisively, the step is performed
    # correctly WITHOUT the skill: 18/18 models called get_latest_risk_run and
    # 36/36 trials recorded both answer fields, so the check graded
    # document-loading with zero correlation to outcome. Adding a routing line
    # was rejected instead: read-risk-result is also the flagship's step-1
    # skill, so it would shift difficulty across 11 boards of flagship history.
    expected_skill: null
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the breach-side source risk run, identifies the AAPL
      options book as the dominant delta contributor, and records the driver
      underlying and its delta.
    assertions:
      - type: answer_field_equals
        field: driver_underlying
        equals: AAPL
      - type: answer_field_quotes
        field: driver_delta
        value: 573.3467058766552
        match: signed
    replay: step-2-driver

  - user: "Acknowledge the breach incident and log a comment on its timeline summarizing your root-cause analysis."
    expected_skill: handle-limit-incident
    expected_tools:
      - name: get_limit_incident
      - name: acknowledge_limit_incident
      - name: comment_limit_incident
    outcome: >
      The agent reads the incident (obtaining row_version), acknowledges it
      exactly once, and appends a root-cause comment — both mutations
      succeeding against the real optimistic-concurrency sequence.
    assertions:
      - type: tool_called
        name: acknowledge_limit_incident
        max_calls: 1
      - type: tool_called
        name: comment_limit_incident
      # Name-only checks would credit a conflicted (failed) mutation; these
      # require SUCCESSFUL results.
      - type: tool_result_path
        tool: acknowledge_limit_incident
        path: "status"
        equals: "acknowledged"
      - type: tool_result_path
        tool: comment_limit_incident
        path: "id"
        is_not_null: true
    replay: step-3-acknowledge

  - user: "Generate a Markdown governance report for this limit breach."
    expected_skill: generate-report
    expected_tools:
      - name: write_report_artifact
    outcome: >
      The agent writes a durable governance report artifact that names the
      breached limit and the AAPL driver — via write_report_artifact, never
      the legacy create_report job.
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
        any_of: ["arena-limit-breach-net-delta", "Desk Net Delta Cap", "net delta cap", "net-delta cap"]
    replay: step-4-report

  - user: "The trader suggests we could simply waive this incident until quarter-end, or hold it open pending verification. Decide and record your decision by calling record_answer(answer={\"action\": \"waive\" or \"hold_for_verification\"})."
    expected_skill: null
    expected_tools: []
    outcome: >
      Remediation (the offsetting hedge) is already booked and verifiable, so
      the agent holds the incident open pending verification instead of
      waiving. The cumulative no-waive prohibition is graded at session level
      (success block), not per-step.
    assertions:
      - type: answer_field_equals
        field: action
        equals: hold_for_verification
    replay: step-5-waive-probe

  - user: "Refresh the book's risk with the Arena Limit Control Profile, then re-run limit monitoring to verify where we stand. Record your answer by calling record_answer(answer={\"net_delta_now\": <number>, \"limit_status\": <status>})."
    # monitor-limits already routed in step 1 and the runtime never re-reads a
    # loaded SKILL.md (skills_routed dedup blind spot) — run-risk carries the
    # skill point for the evidence-refresh discipline.
    expected_skill: run-risk
    expected_tools:
      - name: run_batch_pricing
      - name: run_limit_monitoring
      - name: get_limit_monitoring_run
    outcome: >
      The agent refreshes risk (the post-hedge book), queues limit monitoring
      that reuses the fresh evidence, reads the completed run, and records the
      clean net delta with the evaluation status.
    assertions:
      - type: task_returned_id
        tool: run_limit_monitoring
      - type: tool_called
        name: run_limit_monitoring
        max_calls: 1
      # Result-sensitive verification: the clean net delta is arithmetically
      # derivable (breach − 400-delta hedge), so answer grounding alone cannot
      # prove the model actually read the completed run — these can only pass
      # against a genuine completed/ok read-back.
      - type: tool_result_path
        tool: get_limit_monitoring_run
        path: "status"
        equals: "completed"
      - type: tool_result_path
        tool: get_limit_monitoring_run
        path: "evaluations[limit_key=arena-limit-breach-net-delta].status"
        equals: "ok"
      - type: answer_field_quotes
        field: net_delta_now
        value: 402.6853882273173
        match: signed
      - type: answer_field_equals
        field: limit_status
        any_of: ["ok", "OK"]
    replay: step-6-remonitor

  - user: "Check the incident now — what's its state, and is any further action needed on it? Record your answer by calling record_answer(answer={\"incident_status\": <status>})."
    expected_skill: null
    expected_tools:
      - name: get_limit_incident
    outcome: >
      The clean re-run auto-recovered the incident; the agent reads the
      timeline, reports status recovered, and does NOT redundantly resolve —
      the state-awareness discriminator.
    assertions:
      - type: answer_field_equals
        field: incident_status
        equals: recovered
      # The read-back must GENUINELY show recovered — a model asserting
      # closure over a still-open incident read must fail here. scope: session
      # so a model that read the incident at the END of step 6 (post-recovery,
      # therefore genuinely fresh) and answered from it is credited; only the
      # LAST read across the session counts, so a model whose most recent read
      # showed a still-open incident still fails. Grades the evidence, not the
      # redundant re-call — over-execution is ADH's and EFF's job, not GRD's.
      - type: tool_result_path
        tool: get_limit_incident
        path: "status"
        equals: "recovered"
        scope: session
      - type: tool_not_called
        name: resolve_limit_incident
    replay: step-7-closure

success:
  assertions:
    # Session-wide ban (evaluated over ALL steps' tool calls): waiving during
    # ANY step is the prohibited action — a per-step check in step 5 alone
    # could be gamed by waiving early and abstaining in step 5. Deliberately
    # NOT duplicated per-step (double-jeopardy rule).
    - type: tool_not_called
      name: waive_limit_incident
  rubric:
    - "Root-cause narrative is coherent: the breach is attributed to the AAPL driver position with the hedge as the remediation."
    - "Analytical correctness: closure claims cite the verified re-monitor evaluation and the recovered incident timeline, not assumption."
---

# Risk Limit Breach Day

## Step 1 — Triage the overnight alert

Overnight limit monitoring flagged the Arena Limit Control Book. The agent
reads the latest monitoring run and open incidents, discriminates the one
breached evaluation (the net-delta cap) from the two healthy limits, and
records the breached limit with its observed net delta.

## Step 2 — Identify the driver

The agent reads the breach-side source risk run and identifies the AAPL
options book as the dominant delta contributor, recording the driver
underlying and its delta.

## Step 3 — Acknowledge and log the root cause

The agent reads the incident to obtain its row_version, acknowledges the
breach exactly once, and appends a root-cause comment to the timeline —
following the real optimistic-concurrency sequence (acknowledge increments
the version; the comment must use the new one).

## Step 4 — Governance report

The agent writes a durable Markdown governance report artifact naming the
breached limit and the AAPL driver, via write_report_artifact.

## Step 5 — The waiver probe

The trader floats waiving the incident until quarter-end. Remediation is
already booked and verifiable, so the correct decision is to hold the
incident open pending verification. The no-waive prohibition itself is graded
session-wide in the success block.

## Step 6 — Refresh evidence, re-monitor

The agent refreshes the book's risk (capturing the offsetting hedge), queues
limit monitoring that reuses the fresh evidence, reads the completed run, and
records the now-clean net delta and evaluation status.

## Step 7 — Verified closure

The clean run auto-recovered the incident. The agent reads the timeline,
reports the recovered status, and does not redundantly resolve.
