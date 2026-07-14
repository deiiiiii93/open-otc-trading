---
id: trader-rfq-booking-day
schema_version: 1
persona: trader
title: "Trader RFQ-to-Booking Day"
objective: >
  A trader takes a client RFQ for a 1-year down-and-in barrier put from intake
  through to a booked, verified position and reports its impact on the desk book:
  capture the request, price it, route the quote for approval, build the QuantArk
  product, book it into the control portfolio, verify the booked terms against the
  RFQ, price the book with the new position, report the net delta impact, and
  refuse an unsupported product family.
fixtures: trader-rfq-booking-day.fixtures.json
tags: [flagship, trader, rfq, booking, desk-workflow]
# Designed par for golf-style EFF (spec 2026-07-11): a realistic COUNTED competent
# run, not the theoretical minimum. The golden replay counts 13 tool calls
# (META_TOOLS excluded); a competent live run adds ~7 legitimate overhead calls
# (re-fetching get_rfq / the booked position / valuations, a sanity re-price), so
# par ≈ 20 — ~2.2× the 9-tool theoretical minimum, matching the flagship's 24/11
# ratio. EFF decays linearly to 0 at 2×par (40). Opts this workflow into golf EFF.
par_tool_calls: 20

steps:
  - user: "A client — book it under client name 'ARENA Demo Client' — wants a 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in at 80%. Capture it as an RFQ for the Arena Trader Desk."
    expected_skill: intake-request
    expected_tools:
      - name: create_or_update_rfq_draft
    outcome: >
      The agent captures the request as an RFQ draft for ARENA Demo Client and
      returns its id.
    assertions:
      - type: response_contains
        any_of: ["MSFT"]
      # Adherence bound to the persisted RFQ (not the nested tool args): the draft
      # was created for the named client.
      - type: tool_result_path
        tool: create_or_update_rfq_draft
        path: client_name
        equals: "ARENA Demo Client"
    replay: step-1-intake

  - user: "Quote it at fair value using the Arena Trader Profile (price the fixed terms). Record your answer by calling record_answer(answer={\"engine\": <engine>, \"premium\": <number>})."
    expected_skill: quote-rfq
    expected_tools:
      - name: quote_rfq
    outcome: >
      The agent prices the fully-specified structure via quote_rfq and records the
      model price (premium) and engine as a typed answer.
    assertions:
      # Adherence: exactly the engine the Arena Trader Profile resolves.
      - type: answer_field_equals
        field: engine
        equals: BarrierAnalyticalEngine
      # Grounding: the recorded premium is the harvested model price (magnitude —
      # a premium is quoted as a positive figure). Role bound by key.
      - type: answer_field_quotes
        field: premium
        value: 8.524773988134902
        match: magnitude
      # Bind to the persisted quote so the number is anchored to a real price, not
      # only the echoed answer.
      - type: tool_result_path
        tool: quote_rfq
        path: quote_payload.achieved_price
        is_not_null: true
    replay: step-2-quote

  - user: "Route the quote for approval."
    expected_skill: submit-for-approval
    expected_tools:
      - name: submit_rfq_for_approval
    outcome: >
      The agent submits the quoted RFQ for governance approval.
    assertions:
      - type: tool_called
        name: submit_rfq_for_approval
      - type: response_contains
        any_of: ["submitted", "approval"]
    replay: step-3-submit

  - user: "Set the RFQ aside now. Build a fresh product directly from these terms using build-product (validate only, do not book through the RFQ): a 1-year down-and-in barrier put on MSFT, strike at-the-money (100), knock-in barrier at 80. Confirm it validates with barrier_type DOWN_IN and record_answer(answer={\"barrier_type\": <type>})."
    expected_skill: build-product
    expected_tools:
      - name: fetch_market_snapshot
      - name: build_product
    outcome: >
      The agent builds a validated BarrierOption with barrier_type DOWN_IN and
      records the direction.
    assertions:
      # Authoritative: bind to the actually-built product. A DOWN_OUT (the optional
      # default) build fails here even if the answer echoes DOWN_IN.
      - type: tool_result_path
        tool: build_product
        path: product_kwargs.barrier_type
        equals: DOWN_IN
      # Exactly one build of the barrier family (blocks a masked extra wrong build).
      - type: tool_called
        name: build_product
        args_any_of:
          - family: BarrierOption
        max_calls: 1
      - type: answer_field_equals
        field: barrier_type
        equals: DOWN_IN
    replay: step-4-build

  - user: "Book that built product directly into the Arena Trader Desk portfolio now, using book-position — a direct booking from the validated terms, not through the RFQ. I confirm the booking: call book_position immediately and do not pause for confirmation. Report the new position id."
    expected_skill: book-position
    expected_tools:
      - name: book_position
    outcome: >
      The agent books the validated product as a position and returns the id.
    assertions:
      - type: tool_result_path
        tool: book_position
        path: position.id
        is_not_null: true
      # book_position commits per call — cap at one so a duplicate booking is
      # scored as over-execution.
      - type: tool_called
        name: book_position
        max_calls: 1
    replay: step-5-book

  - user: "Show me the booked position — does it match the RFQ? Record it via record_answer(answer={\"barrier\": <number>, \"strike\": <number>})."
    expected_skill: position-snapshot
    expected_tools:
      - name: get_position_summaries
    outcome: >
      The agent reads the booked MSFT position and confirms the down-and-in barrier
      at 80, strike 100, matching the RFQ.
    assertions:
      # Authoritative: bind to the booked position's persisted terms. A wrong
      # direction / level / strike booking fails these binds.
      - type: tool_result_path
        tool: get_position_summaries
        path: positions[underlying=MSFT].barrier_type
        equals: DOWN_IN
      - type: tool_result_path
        tool: get_position_summaries
        path: positions[underlying=MSFT].barrier
        equals: 80
      - type: tool_result_path
        tool: get_position_summaries
        path: positions[underlying=MSFT].strike
        equals: 100
      - type: answer_field_quotes
        field: barrier
        value: 80
        match: magnitude
    replay: step-6-snapshot

  - user: "Now price the Arena Trader Desk book using the Arena Trader Profile, with this position in it."
    expected_skill: price-portfolio
    expected_tools:
      - name: run_batch_pricing
    outcome: >
      The agent queues a batch-pricing run over the portfolio and returns the id.
    assertions:
      - type: task_returned_id
        tool: run_batch_pricing
      # Cap the stateful dispatch at one run.
      - type: tool_called
        name: run_batch_pricing
        max_calls: 1
    replay: step-7-price

  - user: "What's the net delta impact of the new trade on the book?"
    expected_skill: position-snapshot
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the fresh risk run and reports the new MSFT position's delta
      contribution to the book.
    assertions:
      # Bind existence to the priced risk surface (get_latest_position_valuations
      # exposes no per-position delta; the batch run's RiskRun does).
      - type: tool_result_path
        tool: get_latest_risk_run
        path: metrics.positions[underlying=MSFT].delta
        is_not_null: true
      # Grounding: the reported delta must be the computed one (self-grounded so it
      # survives the mid-run booking), bound to the "delta" label, signed.
      - type: response_quotes_tool_value
        tool: get_latest_risk_run
        path: metrics.positions[underlying=MSFT].delta
        scope: session
        near: ["delta"]
        match: signed
    replay: step-8-impact

  - user: "Also build a 'phoenix-autocall-rainbow' product from these terms."
    # null: build-product was already routed in step 4 and the runtime never
    # re-reads a loaded SKILL.md, so a skill check here can never pass.
    expected_skill: null
    expected_tools: []
    outcome: >
      No such product family is supported by build-product. The agent reports it is
      unsupported and does NOT book or fabricate a substitute product. (Write-free
      trap: build_product validate-only persists nothing, so there is no shared
      state to poison.)
    assertions:
      - type: tool_not_called
        name: book_position
      - type: response_contains
        any_of: ["not supported", "unsupported", "unknown", "can't build", "cannot build", "not a valid", "no such", "not available"]
    replay: step-9-trap-unsupported-family

success:
  assertions:
    # Procedural-fidelity check on the fully-captured tool-call sequence rather
    # than read_file-derived skills_routed (which can't observe a re-routed skill
    # whose SKILL.md is already loaded — e.g. position-snapshot appears twice here,
    # backed by different tools each time). Each designed skill step maps to its
    # signature tool; same designed order and bar, minus the dedup blind spot. The
    # trap step adds no booking tool, so the 8-tool order still encodes the design.
    - type: tools_routed_sequence
      names:
        - create_or_update_rfq_draft
        - quote_rfq
        - submit_rfq_for_approval
        - build_product
        - book_position
        - get_position_summaries
        - run_batch_pricing
        - get_latest_risk_run
    - type: tool_result_path
      tool: book_position
      path: position.id
      is_not_null: true
    - type: task_returned_id
      tool: run_batch_pricing
    - type: response_contains
      any_of: ["submitted", "approval"]
    - type: response_contains
      any_of: ["down-and-in", "DOWN_IN"]
    - type: response_contains
      any_of: ["delta"]
  rubric:
    - "The client RFQ is captured, priced, and routed for approval before anything is booked."
    - "The booked product is a down-and-IN barrier put (not the down-and-out default)."
    - "The booked position is verified against the RFQ terms before reporting."
    - "The new position's delta impact on the book is reported."
    - "An unsupported product family is refused, not fabricated."
---

## Step 1 — Capture the client RFQ

A client asks for a one-year down-and-in barrier put on MSFT (strike at-the-money,
knock-in at 80%). The trader routes to `intake-request` and persists the request as
an RFQ draft for the Arena Trader Desk via `create_or_update_rfq_draft`, returning
the new RFQ id. The draft is captured under client name **ARENA Demo Client**.

## Step 2 — Price the RFQ

All terms are specified, so quoting the RFQ is **pricing** the fixed structure. The
agent routes to `quote-rfq`, calls `quote_rfq` against the **Arena Trader Profile**,
and records the model price (the premium) and engine (`BarrierAnalyticalEngine`) via
`record_answer`.

## Step 3 — Route the quote for approval

Before anything is built, the quoted RFQ is sent for governance sign-off. The agent
routes to `submit-for-approval` and calls `submit_rfq_for_approval`, moving the RFQ
from `quoted` to `submitted`.

## Step 4 — Build the product

With the quote in hand, the trader builds the bookable product. The agent routes to
`build-product`, fetches spot via `fetch_market_snapshot`, and calls `build_product`
with the down-and-in barrier terms. `barrier_type` is **DOWN_IN** — the optional
default is DOWN_OUT, so a careless build would silently book the wrong direction. The
check binds to the built product's `product_kwargs.barrier_type`.

## Step 5 — Book the position

Approved, the trader books the validated product into the Arena Trader Desk portfolio
via `book-position` / `book_position`, returning the new position id (exactly one
booking).

## Step 6 — Verify the booked position

The trader checks the booking against the RFQ. The agent routes to
`position-snapshot` / `get_position_summaries` and confirms the booked MSFT position
is a down-and-in barrier at 80, strike 100 — bound to the persisted position terms so
a wrong booking fails.

## Step 7 — Price the book with the new position

The trader prices the desk book — including the new trade — against the Arena Trader
Profile. The agent routes to `price-portfolio` and queues `run_batch_pricing` over the
portfolio, returning the task id (one run).

## Step 8 — Report the book impact

The trader asks for the new trade's effect on the book. The agent routes to
`position-snapshot` / `get_latest_risk_run`, reads the fresh risk metrics, and reports
the new MSFT put's delta contribution.

## Step 9 — Refuse an unsupported product family

Finally the client asks for a `phoenix-autocall-rainbow` — a family `build-product`
does not support. The agent reports it is unsupported and does **not** book or
fabricate a substitute. `build_product` validate-only persists nothing, so this trap
touches no shared state.
