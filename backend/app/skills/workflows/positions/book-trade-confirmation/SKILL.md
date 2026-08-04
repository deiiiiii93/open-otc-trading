---
name: book-trade-confirmation
description: Parse uploaded trade confirmation documents (PDF/DOCX, including scans) into structured trades and book the reviewed ones. Use when the user attaches confirmation files to the chat, mentions a trade confirmation / termsheet PDF to book, or asks to turn counterparty confirmations into positions.
domain: positions
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - attachment_paths
optional_context:
  - portfolio_id
write_actions: true
confirmation_required: true
success_criteria:
  - Every attached document is parsed or reported failed with its error
  - Each extracted trade's family, key terms, validation status, and evidence are reported before any booking
  - Booking happens only per-trade after user confirmation, and booked position ids are reported
routing:
  - request: "Book trades from an uploaded confirmation document"
    persona: trader
---

## When to use

- The user attached one or more PDF/DOCX trade confirmations and wants them booked.
- A confirmation was uploaded earlier and its batch needs review or booking.

## Procedure

1. Collect the attachment stored paths from the turn's attachment manifest.
2. Call `parse_trade_confirmation(paths, portfolio_id?)`. Report per-document
   status; for failed documents report the error and stop for those files.
3. For each extracted trade, report family, underlying, quantity, entry price,
   currency, counterparty, validation status, and the evidence quotes. Never
   silently correct a validation failure — show the errors and ask.
4. Book only trades the user confirms, one `book_extracted_trade(trade_id,
   portfolio_id?)` call each (HITL confirmation applies). Report booked
   position ids.
5. `already_booked` responses are terminal — report the existing position id,
   do not retry.

## Failure modes

- `vision-capable extractor required` / extractor unavailable: report it; do
  not attempt to transcribe scans manually.
- `no_target_portfolio`: ask which portfolio to book into.
- Validation errors: surface them for human editing on the Confirmations page
  or apply the user's corrected terms via the review flow — never book around
  the gate.

## Example

User: [attaches ISDA_confirm_2026-08-04.pdf] Please book this trade confirmation.
Assistant: Parse the attachment with `parse_trade_confirmation`, report the
extracted trade's family, terms, validation status, and evidence, then after
the user confirms call `book_extracted_trade` and report the new position id.
