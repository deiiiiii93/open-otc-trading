---
id: trader-rfq-booking-day
schema_version: 1
persona: trader
title: "Trader RFQ-to-Booking Day"
objective: >
  A trader takes a client RFQ for a 1-year down-and-in barrier put from intake
  through to a booked, verified position and reports its impact on the desk book:
  capture the request, quote it, route the quote for approval, build the QuantArk
  product, book it into the control portfolio, verify the booked terms against the
  RFQ, price the book with the new position, and report the net delta impact.
fixtures: trader-rfq-booking-day.fixtures.json
tags: [flagship, trader, rfq, booking, desk-workflow]

steps:
  - user: "A client — book it under client name 'ARENA Demo Client' — wants a 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in at 80%. Capture it as an RFQ for the Arena Trader Desk."
    expected_skill: intake-request
    expected_tools:
      - name: create_or_update_rfq_draft
    outcome: >
      The agent captures the request as an RFQ draft and returns its id.
    assertions:
      - type: response_contains
        any_of: ["MSFT"]
    replay: step-1-intake

  - user: "Quote it using the Arena Trader Profile."
    expected_skill: quote-rfq
    expected_tools:
      - name: solve_rfq
      - name: quote_rfq
    outcome: >
      The agent solves the draft and persists a quote, reporting the solved value
      and engine.
    assertions:
      - type: response_contains
        any_of: ["quote", "quoted", "solved", "engine"]
    replay: step-2-quote

  - user: "Route the quote for approval."
    expected_skill: submit-for-approval
    expected_tools:
      - name: submit_rfq_for_approval
    outcome: >
      The agent submits the quoted RFQ for governance approval.
    assertions:
      - type: response_contains
        any_of: ["submitted", "approval"]
    replay: step-3-submit

  - user: "Risk has the quote. Build the product so we can book it — 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in at 80%."
    expected_skill: build-product
    expected_tools:
      - name: fetch_market_snapshot
      - name: build_product
    outcome: >
      The agent builds a validated BarrierOption with barrier_type DOWN_IN.
    assertions:
      - type: response_contains
        any_of: ["down-and-in", "DOWN_IN", "down and in"]
    replay: step-4-build

  - user: "Approved — book it into the Arena Trader Desk portfolio."
    expected_skill: book-position
    expected_tools:
      - name: book_position
    outcome: >
      The agent books the validated product as a position and returns the id.
    assertions:
      - type: tool_result_path
        tool: book_position
        path: position_id
        is_not_null: true
    replay: step-5-book

  - user: "Show me the booked position — does it match the RFQ?"
    expected_skill: position-snapshot
    expected_tools:
      - name: get_position_summaries
    outcome: >
      The agent reads the booked position and confirms the down-and-in barrier
      at 80% matches the RFQ.
    assertions:
      - type: response_contains
        any_of: ["80", "down-and-in", "DOWN_IN"]
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
    replay: step-7-price

  - user: "What's the net delta impact of the new trade on the book?"
    expected_skill: position-snapshot
    expected_tools:
      - name: get_latest_position_valuations
    outcome: >
      The agent reads the fresh valuations and reports the new position's delta
      contribution to the book.
    assertions:
      - type: response_contains
        any_of: ["delta"]
    replay: step-8-impact

success:
  assertions:
    - type: skills_routed_sequence
      names:
        - intake-request
        - quote-rfq
        - submit-for-approval
        - build-product
        - book-position
        - position-snapshot
        - price-portfolio
        - position-snapshot
    - type: tool_result_path
      tool: book_position
      path: position_id
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
    - "The client RFQ is captured, quoted, and routed for approval before anything is booked."
    - "The booked product is a down-and-IN barrier put (not the down-and-out default)."
    - "The booked position is verified against the RFQ terms before reporting."
    - "The new position's delta impact on the book is reported at the end."
---

## Step 1 — Capture the client RFQ

A client asks for a one-year down-and-in barrier put on MSFT (strike at-the-money,
knock-in at 80%). The trader routes to `intake-request` and persists the request as
an RFQ draft for the Arena Trader Desk via `create_or_update_rfq_draft`, returning
the new RFQ id.

## Step 2 — Quote the RFQ

The trader quotes the draft against the **Arena Trader Profile**. The agent routes to
`quote-rfq`, calls `solve_rfq` on the full draft and `quote_rfq` to persist the quote,
and reports the solved value and engine (`BarrierAnalyticalEngine`).

## Step 3 — Route the quote for approval

Before anything is built, the quoted RFQ is sent for governance sign-off. The agent
routes to `submit-for-approval` and calls `submit_rfq_for_approval`, moving the RFQ
from `quoted` to `submitted`.

## Step 4 — Build the product

With the quote in hand, the trader builds the bookable product. The agent routes to
`build-product`, fetches spot via `fetch_market_snapshot`, and calls `build_product`
with the down-and-in barrier terms. `barrier_type` is **DOWN_IN** — the optional
default is DOWN_OUT, so a careless build would silently book the wrong direction.

## Step 5 — Book the position

Approved, the trader books the validated product into the Arena Trader Desk portfolio
via `book-position` / `book_position`, returning the new position id.

## Step 6 — Verify the booked position

The trader checks the booking against the RFQ. The agent routes to
`position-snapshot` / `get_position_summaries` and confirms the booked position is a
down-and-in barrier at 80% on MSFT, matching the request.

## Step 7 — Price the book with the new position

The trader prices the desk book — including the new trade — against the Arena Trader
Profile. The agent routes to `price-portfolio` and queues `run_batch_pricing` over the
portfolio, returning the task id.

## Step 8 — Report the book impact

Finally the trader asks for the new trade's effect on the book. The agent routes to
`position-snapshot` / `get_latest_position_valuations`, reads the fresh valuations,
and reports the new MSFT put's delta contribution.
