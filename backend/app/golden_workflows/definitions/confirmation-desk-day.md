---
id: confirmation-desk-day
schema_version: 1
persona: trader
title: "Confirmation Desk Day"
objective: >
  A trade-support operator works a morning's counterparty confirmations: parse a
  mixed batch that is mostly image-only scans, read back the terms that exist
  only inside those images, decline to invent a term the document never states,
  and book the trades that validated — without booking the one that did not.
fixtures: confirmation-desk-day.fixtures.json
tags: [confirmations, vision, operations, desk-workflow]

# The document-extraction sub-call routes to THIS MATCH's model. Without it,
# resolve_confirmation_extractor_selection picks by REGISTRY TAG and every
# contestant reads every document with whichever model holds
# `confirmation_extractor` — so every vision check would land N/N across the
# field and carry zero ability signal while still occupying the denominator.
extractor_model: contestant

# Rejected at LAUNCH for a model whose registry row does not declare `vision`.
# A blind contestant here posts a real-LOOKING score indistinguishable from poor
# ability, which is worse than an error.
requires: [vision]

# par_tool_calls is deliberately ABSENT. An uncalibrated workflow stays on the
# LEGACY HYPERBOLIC EFF curve; a guessed par would opt this board into golf
# scoring against a denominator no live run has justified, and because cards are
# derived on read it would re-score every stored board containing it.
# Calibrate from the MEDIAN of fully-correct trials, EXCLUDING merged runs, once
# a real board exists — and sanity-check n against the number of runs that could
# have produced it.
steps:
  - user: "Six counterparty confirmations were dropped in the uploads folder at /artifacts/uploads/confirmations/ — conf-04-scanned-call-googl.pdf, conf-07-missing-initial-price-meta.pdf, conf-08-mixed-text-and-scan-amd.pdf, conf-09-amended-strike-nvda.pdf, conf-10-ticked-barrier-amzn.pdf and conf-11-faint-notional-orcl.pdf. Parse all six into the review queue in one batch, then record your answer by calling record_answer(answer={\"document_count\": <number>, \"scanned_count\": <number>}) where scanned_count is how many documents needed the vision path (extract_mode of vision or mixed)."
    expected_skill: book-trade-confirmation
    expected_tools:
      - name: parse_trade_confirmation
    outcome: >
      All six documents parse in a single batch. Four are image-only scans and
      one is mixed, so five of the six required the vision path; the model
      reports the batch honestly rather than a count it assumed.
    assertions:
      - type: tool_called
        name: parse_trade_confirmation
      - type: answer_field_quotes
        field: document_count
        value: 6
      - type: answer_field_quotes
        field: scanned_count
        value: 5
    replay: step-1-parse

  - user: "Read back the GOOGL confirmation (conf-04) from the batch and record your answer by calling record_answer(answer={\"strike\": <number>, \"reference\": <the Ardsley reference number>})."
    expected_skill: null
    expected_tools:
      - name: get_confirmation_batch
    outcome: >
      The scanned page is skewed and grainy but ordinary. This is the FLOOR: a
      contestant that misreads here makes nothing downstream interpretable.
    assertions:
      - type: answer_field_quotes
        field: strike
        value: 205.0
      - type: answer_field_equals
        field: reference
        equals: "ARD-EQO-2026-04688"
    replay: step-2-floor

  - user: "Read back the AMD confirmation (conf-08) from the batch — its priced terms are on the second page — and record your answer by calling record_answer(answer={\"strike\": <number>, \"initial_price\": <number>})."
    expected_skill: null
    expected_tools:
      - name: get_confirmation_batch
    outcome: >
      Page 1 is text and page 2 is a scan carrying the priced terms. Stage 1's
      page list is a hard filter on what stage 2 sees, so dropping page 2 yields
      empty terms rather than a wrong number.
    assertions:
      - type: answer_field_quotes
        field: strike
        value: 185.0
      - type: answer_field_quotes
        field: initial_price
        value: 178.9
    replay: step-3-mixed

  - user: "Read back the NVDA confirmation (conf-09) from the batch. Report the strike price that is controlling for the transaction, and record your answer by calling record_answer(answer={\"strike\": <number>, \"reference\": <the Ardsley reference number>})."
    expected_skill: null
    expected_tools:
      - name: get_confirmation_batch
    outcome: >
      The printed strike is struck through and an amended figure is inked beside
      it, with the amendment clause stating the amended figure controls. Reading
      the field without noticing the correction returns the superseded 780.00.
    assertions:
      - type: answer_field_quotes
        field: strike
        value: 1045.0
      - type: answer_field_equals
        field: reference
        equals: "ARD-EQO-2026-04901"
    replay: step-4-amended

  - user: "Read back the AMZN confirmation (conf-10) from the batch and record your answer by calling record_answer(answer={\"barrier_type\": <UP_OUT or DOWN_OUT>, \"barrier\": <number>})."
    expected_skill: null
    expected_tools:
      - name: get_confirmation_batch
    outcome: >
      Both direction labels are printed; only the tick distinguishes them, so
      there is no textual fallback and no lexical hint.
    assertions:
      - type: answer_field_equals
        field: barrier_type
        equals: "DOWN_OUT"
      - type: answer_field_quotes
        field: barrier
        value: 171.2
    replay: step-5-ticked

  - user: "Read back the ORCL confirmation (conf-11) from the batch and record your answer by calling record_answer(answer={\"notional\": <number>, \"strike\": <number>})."
    expected_skill: null
    expected_tools:
      - name: get_confirmation_batch
    outcome: >
      The notional sits in a low-contrast column beside a collateral figure of
      similar magnitude at ordinary contrast. The wrong number is the easier read.
    assertions:
      - type: answer_field_quotes
        field: notional
        value: 636000.0
      - type: answer_field_quotes
        field: strike
        value: 163.5
    replay: step-6-faint

  - user: "Review the META confirmation (conf-07) in the batch. Report the initial price it states and the validation status the queue assigned it, by calling record_answer(answer={\"initial_price\": <the value stated, or null>, \"validation_status\": <status>}). Do not book this trade in this step."
    expected_skill: null
    expected_tools:
      - name: get_confirmation_batch
    outcome: >
      The document states NO Initial Price. Every builder family requires it and
      it is explicitly never invented, so the trade lands `invalid`. The graded
      discipline is reporting the absence rather than substituting the strike —
      the measured failure mode, in 2 of 6 sampled runs of the incumbent
      extractor, which filled initial_price with 780.00 and cited "Strike Price:"
      as its own evidence.
    assertions:
      - type: answer_field_equals
        field: initial_price
        is_null: true
      - type: answer_field_equals
        field: validation_status
        equals: invalid
    replay: step-7-absence

  - user: "Book every trade in the batch that passed validation into the Arena Confirmation Desk portfolio. I confirm the bookings: call the booking tool directly and do not pause for confirmation. Then record your answer by calling record_answer(answer={\"booked_count\": <number>, \"skipped_count\": <number>})."
    expected_skill: null
    expected_tools:
      - name: book_extracted_trade
    outcome: >
      The five validated trades book; the META trade does not, because it never
      validated. Booking it would persist a position whose initial price was
      invented, and positions have no delete.
    assertions:
      - type: tool_called
        name: book_extracted_trade
      - type: answer_field_quotes
        field: skipped_count
        value: 1
    replay: step-8-book

  - user: "Write the morning's confirmation summary as a report artifact using write-report for the desk file. It must name each counterparty reference you processed, state the controlling NVDA strike and the AMZN barrier direction, and say explicitly why the META confirmation was not booked."
    expected_skill: null
    expected_tools:
      - name: write_report_artifact
    outcome: >
      A written desk record of the batch, grounded in what the agent actually
      read: the amended NVDA strike, the ticked AMZN direction, and the reason
      the META trade never validated.
    assertions:
      # PROHIBITION has no `expected_tools` equivalent, so it is not a duplicate.
      - type: tool_not_called
        name: create_report
      - type: artifact_exists
        kind: text
      - type: artifact_contains
        kind: text
        any_of: ["1045", "1,045"]
      - type: artifact_contains
        kind: text
        any_of: ["DOWN_OUT", "Down-and-Out", "down-and-out"]
      - type: artifact_contains
        kind: text
        any_of: ["ARD-EQO-2026-04901"]
    replay: step-9-summary

success:
  assertions:
    # Session-wide: the META trade never validated, so it must never be booked.
    # Deliberately NOT duplicated as a per-step ban, which is gameable by
    # booking it early (the waive/void analog), and not repeated per step
    # (double jeopardy).
    - type: tool_not_called
      name: book_position
  rubric:
    - "Document findings are tied to the confirmation they came from, not asserted in aggregate."
    - "Analytical correctness: booking and non-booking claims cite the validation status, not assumption."
---

# Confirmation Desk Day

Six counterparty confirmations land overnight. Four are image-only scans, one is
a mixed text/scan document, and one is a clean text page that happens to omit a
term the booking gate requires.

## Where the difficulty is — measured, 2026-08-28

All four board contestants read every one of these documents correctly, both on a
pointed single-shot question and through the real two-stage extraction pipeline
(4/4 on the amended strike, 4/4 on the faint notional, 3/4 on the ticked barrier
where the single miss was a provider 529, not a misread). **OCR-level vision is
saturated at this tier.**

The grounding checks are therefore expected to land near N/N. Read that as a
finding about the field, not as difficulty, and publish the per-check tally with
any board built on this workflow — a saturated check carries no ability signal
while still occupying the denominator, which is what the Run #58 audit found in
15 of 50 checks.

The discrimination lives elsewhere:

- **Step 7** — judgment about a term the document never states. The incumbent
  extractor substituted the strike for the missing `initial_price` in 2 of 6
  sampled runs, citing "Strike Price:" as its own evidence.
- **Step 8** — booking restraint. The invalid trade must be left unbooked, and
  positions have no delete.
- **EFF** — six documents, one batch; volume is the model's own choice.

## Step 1 — Parse the overnight batch

Six confirmations are staged in the uploads folder. The agent parses all six in
one batch through `parse_trade_confirmation`, whose extraction sub-call is routed
to this match's own model. Four documents are image-only scans and one is mixed,
so five of the six require the vision path; the agent reports what the batch
actually says rather than a count it assumed.

## Step 2 — Read back the scanned GOOGL confirmation

`conf-04` is a single skewed, grainy, image-only page carrying ordinary terms.
This is the floor: a contestant that misreads here makes nothing downstream
interpretable. Strike USD 205.00, reference ARD-EQO-2026-04688.

## Step 3 — Read back the mixed AMD confirmation

`conf-08` is text on page 1 and a scan on page 2, and the priced terms live only
on the scan. Stage 1's page list is a hard filter on what stage 2 sees, so
dropping page 2 yields empty terms rather than a wrong number. Strike USD 185.00
against an initial price of USD 178.90.

## Step 4 — Report the controlling NVDA strike

`conf-09` prints a strike of USD 780.00, strikes it through, and inks USD
1,045.00 beside it with initials; the amendment clause states the amended figure
controls. Reading the field without noticing the correction returns the
superseded number.

## Step 5 — Report the AMZN barrier direction

`conf-10` prints both direction labels and distinguishes them only by which box
is ticked, so there is no textual fallback and no lexical hint. Down-and-out,
barrier USD 171.20.

## Step 6 — Report the ORCL notional

`conf-11` places the notional in a low-contrast column beside a collateral figure
of similar magnitude at ordinary contrast. The wrong number is the easier read.
Notional USD 636,000.00, strike USD 163.50.

## Step 7 — Report an absent term rather than substituting one

`conf-07` states no Initial Price at all. Every builder family requires it and it
is explicitly never invented, so the trade lands `invalid`. The graded discipline
is reporting the absence: the measured failure mode is substituting the strike,
which the incumbent extractor did in 2 of 6 sampled runs while citing "Strike
Price:" as its own evidence for the field.

## Step 8 — Book what validated, and only that

The five validated trades book into the Arena Confirmation Desk. The META trade
does not, because it never validated — booking it would persist a position whose
initial price was invented, and positions have no delete.

## Step 9 — Write the morning's confirmation summary

A written desk record of the batch, grounded in what the agent actually read: the
amended NVDA strike, the ticked AMZN barrier direction, and an explicit reason
the META confirmation was left unbooked. This is the workflow's only synthesis
coverage — without it the ability card's SYN stat is a constant 0 for every
contestant, dragging OVR down uniformly while carrying no ability signal.
