---
id: trader-rfq-booking-day
schema_version: 1
persona: trader
title: "Trader RFQ-to-Booking Day"
objective: >
  A trader takes a client RFQ for a 1-year down-and-in barrier put from intake
  through to a booked, verified position, reports its impact on the desk book,
  exports the trade ticket, and refuses an unsupported product family: capture the
  request, price it, route the quote for approval, build the QuantArk product, book
  it into the control portfolio, verify the booked terms, price the book, report the
  net delta impact, export a trade ticket, and refuse an unsupported family.
fixtures: trader-rfq-booking-day.fixtures.json
tags: [flagship, trader, rfq, booking, desk-workflow]
# Designed par for golf-style EFF (spec 2026-07-11): a realistic COUNTED competent
# run, not the theoretical minimum. EMPIRICALLY calibrated on the upgraded workflow
# (Run #33, gpt-5.6 terra+luna × 2 trials, both obj 95-96): competent tool-call counts
# were [30, 44, 46, 51] (median 45, mean 43) — leaner than the pre-upgrade workflow
# because the unsupported-family trap short-circuits the build loop and market-quote
# seeding removes retry churn. par set to 35 (near the lean end of the competent band):
# a genuinely lean run earns ~full EFF, an average competent run grades down modestly,
# and EFF decays linearly to 0 at 2×par (70). Opts into golf EFF.
par_tool_calls: 35

# Accounting date PINNED to a concluded trading day (2026-07-17, Run #26): the
# runner seeds it as the agent's Accounting anchor, so a live
# fetch_market_snapshot on the anchor always hits a session whose daily bar has
# concluded and been published. Anchoring on the real run date let models query
# the still-open US session (empty window → spot null → the whole match stalled:
# terra) or substitute a stale date's prices and backdate the trade (luna).
# The anchor must also be RECENT: an anchor a year back (2025-07-16, tried first)
# made the anchor-coherent 1Y maturity (2026-07-16) EXPIRED relative to the real
# valuation date (build_product rejects it) — luna lost two build attempts to
# "expired option" before falling back to maturity_years. 2026-07-16 keeps the
# bar concluded AND anchor+1Y (2027-07-16) valid. Re-pin when 2027-07 approaches.
# The fixtures also seed the QUOTE STORE with the real MSFT close for the anchor
# (401.10 @ 2026-07-16, instrument-wired + profile valuation_date aligned), so
# profile-bound quote/pricing/risk paths read the SAME right market quote the
# agent fetches — no env-default fallback spot 100 incoherence, and the step-7
# delta ground is the true ATM constant (−0.416389, spot-invariant at these
# ratios), verified against AKShare stock_us_daily (close) and
# price_product_with_greeks (delta).
accounting_date: "2026-07-16"

# Grounding is LIVE-REACHABLE (spec 2026-07-15): the live agent fetches REAL market
# data, so absolute-value grounds (premium 8.52 @ spot 100) drift. All numeric grounds
# are spot- AND contract-multiplier-INVARIANT ratios read from real captured tool
# shapes: premium/(spot×multiplier)=0.08525, barrier/strike=0.80, strike/spot=1.00.
steps:
  - user: "A client — book it under client name 'ARENA Demo Client' — wants a 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in at 80%. Capture it as an RFQ for the Arena Trader Desk. The desk accounting date is 2026-07-16: fix the ATM strike, the 80% knock-in level, the 1-year maturity (running from that date), and the RFQ market off the real MSFT close for 2026-07-16 — fetch that date's snapshot; never fabricate or substitute market data."
    expected_skill: intake-request
    expected_tools:
      - name: create_or_update_rfq_draft
    outcome: >
      The agent captures the request as an RFQ draft for ARENA Demo Client and
      returns its id.
    assertions:
      # The create_or_update_rfq_draft RESULT is empty on the live path, so the RFQ
      # terms are grounded on the populated quote_rfq result at step 2 (D4). Here we
      # verify the request was captured and names the instrument.
      - type: tool_called
        name: create_or_update_rfq_draft
      - type: response_contains
        any_of: ["MSFT"]
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
      # --- Grounding on the quote_rfq result (the populated RFQ carrier). All numeric
      # grounds are spot/multiplier-INVARIANT ratios (live-reachable at any real spot).
      # premium / (spot × contract_multiplier) = 0.08525 for BOTH the multiplier-1 and
      # multiplier-100 captured regimes (rel_tol 0.03 absorbs profile vol/rate variance).
      - type: tool_result_ratio
        tool: quote_rfq
        numer: quote_payload.achieved_price
        denom: request_payload.market.spot
        denom_mult: request_payload.product.terms.contract_multiplier
        equals: 0.08525
        rel_tol: 0.03
      # barrier/strike = 0.80 (knock-in at 80% of an ATM strike) — scale-free.
      - type: tool_result_ratio
        tool: quote_rfq
        numer: request_payload.product.terms.barrier
        denom: request_payload.product.terms.strike
        equals: 0.80
        rel_tol: 0.01
      # strike/spot = 1.00 (at-the-money) — scale-free.
      - type: tool_result_ratio
        tool: quote_rfq
        numer: request_payload.product.terms.strike
        denom: request_payload.market.spot
        equals: 1.00
        rel_tol: 0.01
      # Structural (spot-invariant strings) — also fixes the empty-draft intake binds.
      - type: tool_result_path
        tool: quote_rfq
        path: request_payload.product.underlying
        equals: MSFT
      - type: tool_result_path
        tool: quote_rfq
        path: request_payload.product.quantark_class
        equals: BarrierOption
      - type: tool_result_path
        tool: quote_rfq
        path: request_payload.client_name
        equals: "ARENA Demo Client"
      - type: tool_result_path
        tool: quote_rfq
        path: request_payload.product.terms.barrier_type
        equals: DOWN_IN
      # D1a: the model's REPORTED premium must be the live achieved price it received
      # (self-grounded → spot-robust AND ties the answer to the tool truth). A wrong
      # reported premium (999 / a stale 8.52 at live spot) fails here.
      - type: response_quotes_tool_value
        tool: quote_rfq
        path: quote_payload.achieved_price
        near: ["premium", "price"]
        match: signed
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

  - user: "Set the RFQ aside now. Build a fresh product directly from these terms using build-product (validate only, do not book through the RFQ): a 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in barrier at 80% of strike. Fetch the real MSFT market snapshot for the desk accounting date 2026-07-16 and set initial_price/strike/barrier off that close. Never fabricate a price and never substitute another date's market data — if the 2026-07-16 snapshot comes back empty, stop and report the failure instead of proceeding on assumed numbers. Confirm it validates with barrier_type DOWN_IN and record_answer(answer={\"barrier_type\": <type>})."
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
      # Moneyness on the built product — scale-free ratio (barrier at 80% of strike).
      - type: tool_result_ratio
        tool: build_product
        numer: product_kwargs.barrier
        denom: product_kwargs.strike
        equals: 0.80
        rel_tol: 0.01
      # Data provenance (2026-07-17, Run #26): the build must price off the REAL
      # snapshot for the pinned accounting date — a stale/other-date fetch (luna's
      # 2025-01-02 close), an empty-window fallback, or a fabricated spot fails
      # here, not just on the ratio.
      - type: tool_result_path
        tool: fetch_market_snapshot
        path: data.latest.date
        equals: "2026-07-16"
      - type: tool_called
        name: build_product
        args_any_of:
          - family: BarrierOption
      - type: answer_field_equals
        field: barrier_type
        equals: DOWN_IN
    replay: step-4-build

  - user: "Book that built product directly into the Arena Trader Desk portfolio now, using book-position — a direct booking from the validated terms, not through the RFQ. Book it with trade_effective_date 2026-07-16 — the accounting date the terms were built from — and never backdate the trade or invent dates. I confirm the booking: call book_position immediately and do not pause for confirmation. Report the new position id."
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
      # --- D2: bind direction + moneyness to the AUTHORITATIVE booking payload (the
      # book_position CALL args), NOT the decoupled build result — a run could build
      # DOWN_IN then book DOWN_OUT/another barrier (Codex plan finding). book_position
      # consumes args.product.terms.
      # all_calls + max_calls 1: exactly one booking AND it matches DOWN_IN — a second
      # (duplicate) booking is over-execution and must fail (Codex code-review).
      - type: tool_called
        name: book_position
        args_any_of:
          - product:
              terms:
                barrier_type: DOWN_IN
        all_calls: true
        max_calls: 1
      - type: tool_result_ratio
        tool: book_position
        source: call
        numer: product.terms.barrier
        denom: product.terms.strike
        equals: 0.80
        rel_tol: 0.01
      # Anti-backdating (2026-07-17, Run #26): the trade must be dated at the
      # accounting date the terms were built from — not the snapshot's historical
      # date, not omitted (luna booked trade_effective_date=2025-01-02 to match a
      # stale fetch). trade_effective_date is a real book_position input.
      - type: tool_called
        name: book_position
        args_any_of:
          - trade_effective_date: "2026-07-16"
    replay: step-5-book

  - user: "Show me the booked position — confirm it's the MSFT down-and-in barrier we just booked."
    expected_skill: position-snapshot
    expected_tools:
      - name: get_positions
    outcome: >
      The agent reads the booked MSFT position via get_positions and confirms a
      persisted MSFT BarrierOption.
    assertions:
      # get_positions is the tool the agent actually calls; it exposes product_type on
      # the row (barrier/strike are NOT promoted here — those are grounded on the
      # booking/quote). A MSFT BarrierOption must be persisted.
      - type: tool_result_path
        tool: get_positions
        path: positions[underlying=MSFT].product_type
        equals: BarrierOption
      - type: response_contains
        any_of: ["down-and-in", "DOWN_IN"]
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
      - type: tool_called
        name: run_batch_pricing
        max_calls: 1
    replay: step-7-price

  - user: "What's the net delta impact of the new trade on the book? Run the risk calculation on the desk book."
    expected_skill: run-risk
    expected_tools:
      - name: get_latest_risk_run
    outcome: >
      The agent reads the desk book's fresh stored risk run (the batch-pricing
      run from the pricing step already persists risk metrics; a new run may be
      queued when none is current) and reports the new MSFT position's delta
      impact.
    assertions:
      # 2026-07-17 (Run #26): the canonical desk risk read is the PERSISTED run —
      # read-before-compute policy (stored results for persisted books), the
      # run-risk skill, and the flagship's contract all use get_latest_risk_run.
      # Demanding in-memory calculate_risk here contradicted all three and cost
      # BOTH Run #26 models 3 checks each despite correct delta answers. Either
      # competent path scores; calculate_risk stays as the ad-hoc alternative.
      - type: assertion_any_of
        axis: adherence
        any_of:
          - type: tool_called
            name: get_latest_risk_run
          - type: tool_called
            name: calculate_risk
      # NUMERIC grounding (2026-07-17, quote-seeded coherent regime): the risk
      # read must contain the TRUE engine delta for the new MSFT put, not just
      # any data. The fixtures seed the quote store (MSFT close 401.10 as of the
      # pinned accounting date 2026-07-16, instrument-wired), so the persisted
      # batch run prices the booked put AT the accounting-date close — same
      # regime as an in-memory calculate_risk on the agent's own fetch. Both
      # paths therefore converge on the ATM constant, harvested via
      # price_product_with_greeks (strike 401.10 / barrier 320.88, profile
      # r/q/vol .04/.01/.28, T=1.0): delta ≈ −0.416389 (spot-invariant at these
      # ratios — the same constant held at the 502.06 regime). The 0.02 band on
      # the persisted member absorbs T-encoding variance (maturity 1.0 vs an
      # absolute 2027-07-16 expiry); the 0.10 band on the in-memory member also
      # absorbs agent-default vol/rate substitution (−0.3888 at .20/.03/0).
      # A stale-strike booking (Run #26, luna) prices OTM at this spot → its
      # delta lands far outside the band: moneyness IS discriminated here.
      - type: assertion_any_of
        axis: grounding
        any_of:
          - type: tool_result_path
            tool: get_latest_risk_run
            path: metrics.positions[underlying=MSFT].delta
            equals: -0.416389
            rel_tol: 0.02
          - type: tool_result_path
            tool: calculate_risk
            path: positions[underlying=MSFT].delta
            equals: -0.416389
            rel_tol: 0.10
      # The REPORTED delta must quote the tool truth near the word "delta" —
      # the anti-fabrication binding: a self-supplied number the tools never
      # produced fails here even when it happens to be plausible.
      - type: assertion_any_of
        axis: grounding
        any_of:
          - type: response_quotes_tool_value
            tool: get_latest_risk_run
            path: metrics.positions[underlying=MSFT].delta
            near: ["delta"]
            match: signed
          - type: response_quotes_tool_value
            tool: calculate_risk
            path: positions[underlying=MSFT].delta
            near: ["delta"]
            match: signed
      - type: response_contains
        any_of: ["delta"]
    replay: step-8-impact

  - user: "Export a trade ticket for the new booked position via write_report_artifact — include the client name, underlying, direction (down-and-in put), the knock-in barrier as a percent of strike, and the quoted premium."
    # null: no new skill point — position-snapshot / reporting skills were already
    # routed and the runtime never re-reads a loaded SKILL.md.
    expected_skill: null
    expected_tools:
      - name: write_report_artifact
    outcome: >
      The agent writes a downloadable trade-ticket artifact summarizing the booked
      MSFT down-and-in barrier put for ARENA Demo Client.
    assertions:
      - type: tool_called
        name: write_report_artifact
      # SYNTHESIS: the ticket must EXIST and its CONTENT must carry the trade facts —
      # an empty/contradictory ticket must not earn synthesis credit (Codex spec
      # finding). artifact_exists/artifact_contains default to the synthesis axis.
      - type: artifact_exists
        kind: text
      - type: artifact_contains
        kind: text
        any_of: ["MSFT"]
      - type: artifact_contains
        kind: text
        any_of: ["down-and-in", "DOWN_IN"]
      # Strict knock-in LEVEL — the generic word "knock-in" is not a level, so a ticket
      # that omits the 80% barrier must fail (Codex code-review).
      - type: artifact_contains
        kind: text
        any_of: ["80%", "80 %", "0.8"]
      - type: artifact_contains
        kind: text
        any_of: ["ARENA Demo Client"]
    replay: step-9-ticket

  - user: "Also build a 'cliquet-ratchet' product from these terms."
    # null: build-product was already routed and the runtime never re-reads a loaded
    # SKILL.md, so a skill check here can never pass.
    expected_skill: null
    # No fixed expected_tool: the model may validate the fabricated family EITHER via
    # build_product OR check_term_completeness (D4 assertion_any_of scores that), so a
    # single required tool here would contradict "accept either path".
    #
    # The family must be one with NO catalog near-match: the original
    # 'phoenix-autocall-rainbow' failed as a trap in Run #30 because PhoenixOption IS
    # a registered class — diligent agents looked up the catalog, found it, and built
    # successfully, so the "unsupported" checks could never fire. 'cliquet-ratchet'
    # maps to nothing in the registry: an honest build attempt gets ok=false /
    # "Unknown QuantArk class", and only a fabricating agent can "succeed".
    expected_tools: []
    outcome: >
      No such product family is supported. The agent validates and declines — either
      by getting a build/completeness rejection or by recognizing the family is
      unsupported — and does NOT book or fabricate a substitute. (Write-free trap:
      build_product validate-only persists nothing, so there is no state to poison.)
    assertions:
      # D4: the fabricated family may be legitimately declined TWO competent ways —
      # a validated build/completeness rejection OR a recognized refusal. Expressed as
      # a real OR (assertion_any_of), NOT independent ANDs (which would let a pure-prose
      # refusal pass — Codex plan finding). At least one POSITIVE validation-evidence
      # member must fire, so a no-tool hallucinated refusal fails.
      - type: tool_not_called
        name: book_position
      # Real tool contracts (verified): build_product(unknown family) returns top-level
      # ok=false (validation is null); check_term_completeness(unknown class) returns a
      # non-null "Unknown QuantArk class" error (no `complete` field). Either is valid
      # positive validation evidence (Codex code-review).
      - type: assertion_any_of
        axis: adherence
        any_of:
          - type: tool_result_path
            tool: build_product
            path: ok
            equals: false
          - type: tool_result_path
            tool: check_term_completeness
            path: error
            is_not_null: true
      - type: response_contains
        any_of: ["not supported", "unsupported", "unknown", "can't build", "cannot build", "not a valid", "no such", "not available", "incomplete", "missing"]
    replay: step-10-trap-unsupported-family

success:
  assertions:
    # Procedural-fidelity check on the fully-captured tool-call sequence. Each designed
    # signature step maps to the tool the agent actually calls (get_positions,
    # get_latest_risk_run, write_report_artifact). The trap adds no booking tool.
    - type: tools_routed_sequence
      names:
        - create_or_update_rfq_draft
        - quote_rfq
        - submit_rfq_for_approval
        - build_product
        - book_position
        - get_positions
        - run_batch_pricing
        - get_latest_risk_run
        - write_report_artifact
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
    - "The booked position is verified before reporting."
    - "The new position's delta impact on the book is reported from a successful risk calc."
    - "A trade ticket is exported for the booked position."
    - "An unsupported product family is refused, not fabricated."
---

## Step 1 — Capture the client RFQ

A client asks for a one-year down-and-in barrier put on MSFT (strike at-the-money,
knock-in at 80%). The trader routes to `intake-request` and persists the request as
an RFQ draft for the Arena Trader Desk via `create_or_update_rfq_draft`, returning
the new RFQ id. The draft is captured under client name **ARENA Demo Client**. (The
draft-creation result is thin on the live path, so the RFQ terms are grounded on the
populated `quote_rfq` result at step 2.)

## Step 2 — Price the RFQ

All terms are specified, so quoting the RFQ is **pricing** the fixed structure. The
agent routes to `quote-rfq`, calls `quote_rfq` against the **Arena Trader Profile**,
and records the model price (the premium) and engine (`BarrierAnalyticalEngine`) via
`record_answer`. Grounding is spot- and contract-multiplier-invariant:
premium/(spot×multiplier)=0.08525, barrier/strike=0.80, strike/spot=1.00 — reachable
at any real live spot.

## Step 3 — Route the quote for approval

Before anything is built, the quoted RFQ is sent for governance sign-off. The agent
routes to `submit-for-approval` and calls `submit_rfq_for_approval`.

## Step 4 — Build the product

The trader builds the bookable product. The agent routes to `build-product`, fetches
spot via `fetch_market_snapshot` for the pinned accounting date (2026-07-16 — a
concluded session, so the live fetch always returns rows), and calls `build_product`
with the down-and-in barrier terms. `barrier_type` is **DOWN_IN** (the optional
default is DOWN_OUT); the check binds to the built product's `product_kwargs`,
moneyness to barrier/strike=0.80, and data provenance to the fetched snapshot's
`data.latest.date` — a stale/other-date fetch, an empty-window fallback, or a
fabricated spot fails the ground. The step prompt also forbids substituting another
date's market data or proceeding on assumed numbers.

## Step 5 — Book the position

The trader books the validated product into the Arena Trader Desk portfolio via
`book-position` / `book_position`. Direction and moneyness are grounded on the
**booking call's** own terms — the authoritative payload `book_position` commits.
The booking must be dated `trade_effective_date` = the accounting date the terms
were built from; backdating to a stale snapshot date (Run #26, luna) or omitting
the date fails the adherence check.

## Step 6 — Verify the booked position

The trader checks the booking. The agent routes to `position-snapshot` /
`get_positions` and confirms a persisted MSFT `BarrierOption`.

## Step 7 — Price the book with the new position

The trader prices the desk book — including the new trade — against the Arena Trader
Profile via `run_batch_pricing`.

## Step 8 — Report the book impact

The trader asks for the new trade's delta impact. The desk's canonical risk read is
the PERSISTED run: the batch-pricing run from step 7 already computed and stored
risk metrics, so the agent reads them back via `get_latest_risk_run` (queueing a
fresh `run_batch_pricing` first if the stored run is stale or missing) and reports
the MSFT put's delta. An in-memory `calculate_risk` on a caller-supplied snapshot
also earns the read credit (real computed data), but the persisted run is the
policy-compliant path — read-before-compute reserves in-memory compute for
non-persisted ad-hoc specs.

Grounding is NUMERIC: the per-position delta must be the true engine constant for
the deterministic regime. The fixtures seed the quote store (MSFT close 401.10 as
of the pinned accounting date, instrument-wired), so the persisted batch run prices
the booked put AT the accounting-date close — the same regime as an in-memory
`calculate_risk` on the agent's own fetch. Both converge on the ATM constant
≈ −0.416389 (persisted ±2%, in-memory ±10%), and the reported number must quote
the tool value near "delta" (anti-fabrication binding).

## Step 9 — Export the trade ticket

The trader exports a downloadable trade ticket for the booked position via
`write_report_artifact`. The ticket content must carry the trade facts (client,
direction, knock-in %, premium) — this is the workflow's synthesis deliverable.

## Step 10 — Refuse an unsupported product family

Finally the client asks for a `cliquet-ratchet` — a family with no near-match in
the registered catalog (the earlier `phoenix-autocall-rainbow` trap collapsed in
Run #30 because PhoenixOption is a real, buildable class). The agent validates
and declines (a build/completeness rejection or a recognized refusal) and does
**not** book or fabricate a substitute. `build_product` validate-only persists
nothing, so this trap touches no shared state.
