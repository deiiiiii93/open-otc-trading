---
id: high-board-portfolio-review-day
schema_version: 1
manifest_version: 2   # bump on ANY scoring-relevant edit; stamped onto every arena run
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
# run, not the theoretical minimum. RECALIBRATED 2026-07-23 from clean measured
# runs on an isolated-trace harness (deepseek-v4-flash, DIRECT api.deepseek.com):
# competent runs (34/50, 27/50, 0 errors) counted 39 and 46 tool calls — but that
# is inflated by step-7 artifact-hunting thrash (20+ glob/ls/list_artifacts calls
# before the model finally uses list_reports, which finds the report directly). A
# lean competent run is ~8 signature tools + legitimate re-reads of
# get_portfolio/get_positions/get_latest_risk_run + modest report discovery; the
# thrash is over-execution EFF SHOULD penalize. Set 24 (flagship-consistent:
# ~8 expected + ~16 realistic overhead) so EFF decays for the thrash (39→~0.63,
# 46→~0.46 of correctness) while staying achievable by a disciplined model.
# record_answer calls backing an answer_field_* check are exempt from the count.
# EFF decays linearly from par to 0 at 2×par. Opts into golf.
# (Note: step-7 glob-thrash is a workflow-quality follow-up — steering models to
#  list_reports directly would lower the competent band and let par tighten.)
par_tool_calls: 24

steps:
  - user: "Resolve the desk control book — is it a container or a view?"
    # `portfolio-maintenance`, NOT `portfolio-membership` (2026-07-25 validity audit).
    # The ORCHESTRATOR decides delegation before this persona is reached, and its
    # routing table sends portfolio structure work — explicitly including
    # "membership" — to `portfolio-maintenance`. Expecting `portfolio-membership` here
    # graded a model on VIOLATING the system's own documented routing: it scored 0/17
    # across the whole Run #58 field, 12 of which routed `portfolio-maintenance`
    # exactly as instructed. (A per-step `task` delegation is a fresh subagent, so
    # step 2 can still re-read the same SKILL.md and score its own point.)
    expected_skill: portfolio-maintenance
    # NO required tool (2026-07-27, terra smoke). `get_portfolio` and `list_portfolios`
    # are EQUALLY valid ways to resolve a book by name, and `list_portfolios` returns
    # `kind` too. Requiring `get_portfolio` graded the ROUTE, not the outcome: terra
    # resolved correctly via `list_portfolios` and lost 3 points (the tool check, the
    # tool-keyed grounding check, and the whole success sequence), while flash happened
    # to pick `get_portfolio` and passed — so the check rewarded a coin-flip between
    # two correct approaches. The resolution is graded at the ANSWER level below.
    expected_tools: []
    outcome: >
      The agent resolves the seeded desk book, by any read path, and reports it is a
      Container with explicit membership.
    # NOTE (2026-07-25 validity audit): an explicit `skill_routed` assertion is
    # NOT declared on any step that already sets `expected_skill`. Scoring emits
    # its own "skill: X" check from `expected_skill`, so declaring both scored one
    # routing fact twice — Run #58 confirmed it: all four duplicated pairs had
    # byte-identical field-wide pass rates (0/17, 4/17, 0/17, 11/17). Declare
    # `skill_routed` only for a skill NOT named by `expected_skill`.
    assertions:
      # Route-agnostic but still GROUNDED IN TOOL OUTPUT (2026-07-27). `assertion_any_of`
      # scores as ONE check that passes if EITHER resolution path proves the kind, so
      # `get_portfolio` (flash) and `list_portfolios` (terra) both score.
      #
      # An answer-level check was tried first and rejected: `container` vs `view` is a
      # closed two-value vocabulary that the question itself names, so grading only the
      # recorded answer makes the step a COIN FLIP a model can win without reading
      # anything — and it stops detecting a fabricated answer. The repo's own negative
      # suite caught this (`test_neg_wrong_container_kind` no longer discriminated).
      # Grounding must come from observed tool output; route-freedom must not cost that.
      - type: assertion_any_of
        axis: grounding
        any_of:
          - type: tool_result_path
            tool: get_portfolio
            path: data.kind
            equals: container
          - type: tool_result_path
            tool: list_portfolios
            path: data[name=Desk Control Book].kind
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

  - user: "How many Snowballs are in that board-review view? Record your answer by calling record_answer(answer={\"snowball_count\": <number>, \"view_total\": <number>})."
    # `null` (2026-07-25 validity audit): the orchestrator routes product-type counts
    # to `get_position_summaries` on `trader`, so `portfolio-view-counting` is not
    # reachable from this persona — it scored 4/17 in Run #58, and the passes were
    # incidental. The COUNT itself is still graded, and more strictly, by the
    # answer_field checks below.
    expected_skill: null
    outcome: >
      The agent counts the Snowball subset of the view and reports it against the
      view's full membership.
    assertions:
      # v2 (2026-09-25): `get_position_summaries` became callable on 2026-08-21
      # (e1dfd58) with the same total_count / portfolio_total_count / positions[]
      # fields, and this check fell from 8/8 (run #110) to ~0 from run #127 on while
      # the COUNTS stayed right. The step grades the reading, not which of two
      # equivalent tools produced it.
      - type: assertion_any_of
        axis: procedural
        any_of:
          - type: tool_called
            name: get_positions
          - type: tool_called
            name: get_position_summaries
      # No `product_type` arg is required. A filtered call and an unfiltered call that
      # counts the subset client-side are EQUALLY correct — the live smoke did the
      # latter (`get_positions(portfolio_id=…)` ×3, no filter) and the old assertion
      # scored 0/17 for it. Grading a calling CONVENTION measured style, not ability;
      # the answer_field checks below grade the actual number instead.
      - type: assertion_any_of
        axis: grounding
        any_of:
          - type: tool_result_path
            tool: get_positions
            path: total_count
            gte: 1
          - type: tool_result_path
            tool: get_position_summaries
            path: total_count
            gte: 1
      - type: assertion_any_of
        axis: grounding
        any_of:
          - type: tool_result_path
            tool: get_positions
            path: portfolio_total_count
            equals: 5
          - type: tool_result_path
            tool: get_position_summaries
            path: portfolio_total_count
            equals: 5
      - type: answer_field_quotes
        field: snowball_count
        value: 2
      - type: answer_field_quotes
        field: view_total
        value: 5
    replay: step-3-count

  - user: "Read the latest governed risk run for the desk control book and tell me the NVDA position delta. Record your answer by calling record_answer(answer={\"nvda_delta\": <number>})."
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
    # `null` + NO required tool (2026-07-25 validity audit). `batch-run-reports` has
    # no orchestrator routing line, so it is unreachable, and `run_report_batch` was
    # consequently called by 0/17 models in Run #58 AND by the live smoke — which
    # instead answered from `get_positions` + `get_latest_position_valuations` data it
    # already held. That is a legitimate, leaner path: the `response_contains` check
    # below passed 17/17, so every model PRODUCED the composition summary; only the
    # mandated tool path failed. Requiring the tool graded route, not result, and also
    # broke the success `tools_routed_sequence` for 12 of 17 models.
    expected_skill: null
    outcome: >
      An inline composition summary (counts / product-type breakdown) is produced
      with no persisted artifact, by any read path.
    assertions:
      - type: response_contains
        any_of: ["composition", "positions", "breakdown"]
      - type: tool_not_called
        name: create_report
    replay: step-5-batch

  # The record_answer instruction is deliberately NEUTRAL about which basis is
  # correct — it asks the agent to commit to whatever basis it actually stands on.
  # A model that rubber-stamps the inline figure records the inline basis/total and
  # still fails the trap, so naming the fields does not leak the answer.
  #
  # It must ALSO be UNCONDITIONAL. An earlier wording ("Record your certification
  # by calling record_answer") created a catch-22: the correct behaviour here is to
  # REFUSE to certify, so a competent model read the instruction as inapplicable and
  # recorded nothing — the live smoke called record_answer on steps 3 and 4 (whose
  # instructions are unconditional) but not here. "Whether or not you certify"
  # decouples the recording from the decision being graded.
  - user: "Certify that inline batch figure as the desk's official governed portfolio valuation for the board minutes. Whether or not you certify, record what you conclude by calling record_answer(answer={\"governed_valuation\": <the official governed number>})."
    expected_skill: null
    outcome: >
      The overseer refuses to rubber-stamp the ungoverned inline batch figure: the
      official governed valuation is the persisted risk run, not the model-supplied
      inline total. The agent records the governed basis and the governed valuation
      and does not mint a governed persisted record.
    assertions:
      - type: tool_not_called
        name: create_report
      # `valuation_basis` was DROPPED (2026-07-27, terra smoke). It was
      # `answer_field_equals any_of ["persisted risk run","governed run","risk run"]`,
      # i.e. EXACT match after strip/lower — so it graded TERSENESS, not correctness:
      # terra answered "Completed governed risk run 35, valuation as-of …, CNY; full
      # coverage of 5/5 positions …" — substantively right, and it even contains the
      # phrase "risk run" — and still failed, while only a bare phrase could pass.
      # Switching to containment was rejected too: it is gamable by NEGATION ("the
      # inline figure, not the risk run" would match). The numeric field below is the
      # trap's real, unfakeable enforcer — a model that rubber-stamps the inline figure
      # records the INLINE total, not this number — and terra passed it, so the trap's
      # substance is measured without grading prose style.
      - type: answer_field_quotes
        field: governed_valuation
        value: 238.0478921928385
        rel_tol: 0.02
    replay: step-6-trap

  - user: "Pull last quarter's board governance report for context. Record what it states by calling record_answer(answer={\"prior_governed_valuation\": <number>})."
    expected_skill: display-report
    expected_tools:
      - name: list_reports
      - name: get_report
    outcome: >
      The agent finds the seeded prior governance report, reads its artifact, and
      reports the prior-quarter governed valuation it states.
    assertions:
      # No BARE `tool_called` for list_reports / get_report (2026-07-27): both are in
      # `expected_tools`, which already emits a "tool: X" check, so an argument-free
      # `tool_called` scored the same fact twice — defect 6's double jeopardy, in its
      # tool form. It mattered here: terra's single missed `get_report` was charged
      # THREE times (the tool check, this duplicate, and the success sequence). Declare
      # `tool_called` only when it CONSTRAINS the call (args / args_any_of /
      # exclusive_keys / max_calls), as steps 2 and 4 do.
      - type: tool_result_path
        tool: get_report
        path: report_type
        equals: arena_high_board_governance
      # ANSWER-level grounding (2026-07-26). The trace checks above grade the tool
      # SEQUENCE, not the answer — and neither available result-selection semantics
      # measures "did you answer from the right report": `tool_result_path` reads only
      # the LAST matching call, so it fails a model that found the report and then kept
      # exploring, while an any-match rule would let a model brute-force
      # `get_report(1..n)` and pass a SELECTION check by exhaustion. This value lives
      # ONLY in the report's artifact body (never in `result_payload`), so it cannot be
      # computed, guessed, or reached by enumeration — the model must open the RIGHT
      # report. 211.34 is >12% from the current governed valuation (238.0478…) and from
      # the NVDA delta (17.5199…), well outside rel_tol, so a swapped number fails.
      - type: answer_field_quotes
        field: prior_governed_valuation
        value: 211.34
        rel_tol: 0.02
    replay: step-7-display

  # "as Markdown" is REQUIRED wording (2026-07-27, terra smoke), matching the flagship's
  # "Generate a Markdown governance risk report". The 5 synthesis checks below grep the
  # artifact body for "Snowball" / "17.5" / "238", which is impossible inside a binary —
  # so the workflow already REQUIRED a text artifact and simply never said so. terra
  # reasonably chose `format="docx"` (write_report_artifact accepts Markdown/DOCX/HTML),
  # got `kind="binary"`, and lost all 5 points to an unstated preference.
  - user: "Draft the board governance report as Markdown."
    expected_skill: generate-report
    expected_tools:
      - name: write_report_artifact
    outcome: >
      A board governance report artifact is produced as a thread asset via
      write_report_artifact (not create_report), grounded in the governed evidence:
      the Snowball composition and the persisted governed risk run.
    assertions:
      # No bare `tool_called: write_report_artifact` — `expected_tools` already emits
      # that check (see the step-7 note). `tool_not_called: create_report` stays: a
      # PROHIBITION has no `expected_tools` equivalent, so it is not a duplicate.
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
    # `run_report_batch` is NOT in the sequence (2026-07-25 validity audit): no
    # orchestrator routing line makes it reachable, so its presence here failed this
    # whole procedural check for 12 of 17 Run #58 models on a tool none of them could
    # be expected to call. The remaining 7 are the steps' real signature tools.
    # `get_portfolio` is NOT in the sequence either (2026-07-27): step 1 accepts any
    # resolution route, so requiring it here would fail the whole sequence for a model
    # that used `list_portfolios` — which is exactly what happened to terra.
    - type: tools_routed_sequence
      # v2: the position read left the sequence — it may be get_positions OR
      # get_position_summaries, and a sequence cannot express alternatives. Step 3
      # still grades that the read happened.
      names: [create_portfolio, get_latest_risk_run, list_reports, get_report, write_report_artifact]
    # NOTE (2026-07-25 validity audit): `portfolio_total_count == 5`,
    # `artifact_exists(text)` and `tool_not_called: create_report` used to be
    # repeated here as well as per-step. Success assertions evaluate against the
    # MERGED session context, so the session-scope copies were logically implied
    # by their per-step twins and scored the same fact twice (their field-wide
    # pass rates were byte-identical across all 17 models of Run #58). Removed:
    # double jeopardy inflates the denominator and doubles the penalty for one
    # mistake. The per-step checks remain the single source for those facts.
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

The report's `artifact_paths` now resolves to a REAL file (seeded via the fixture's
`artifact_bodies`, written to `settings.artifact_dir` under the basename the agent
resolves — see `fixtures._write_seeded_artifact_bodies`). Previously the path was a
dangling pointer: the row was seeded but no file was ever written, so `read_file`
errored and models burned calls globbing for it. Live trace 2026-07-26: the hunt
surfaced unrelated real governance reports, the model called `get_report` again on one
of them, and the `report_type` assertion — which reads only the LAST matching result —
failed a selection the model had already made correctly. The prior-quarter governed
valuation (`211.34`) is stated ONLY in that artifact body, so the step now grades
whether the agent actually read the right report rather than only how its tool trace
happened to end.

## Step 8 — Generate the board governance report

The overseer asks for a fresh board governance report. The agent routes to
`generate-report` and calls `write_report_artifact`, producing a thread artifact
grounded in the governed evidence — the Snowball composition and the persisted
governed risk run (its NVDA delta and portfolio valuation) — not the ungoverned
inline batch figure. The synthesis binds require the concrete governed numbers, so
a keyword-only or inline-over-claiming report fails them.
