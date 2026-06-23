---
name: asian-fixings
description: Set up an Asian option's fixing calendar and lock in due fixings. Use when a user wants to generate the averaging-date fixing schedule for an Asian position, or to capture (snapshot) the close price for observation dates that have already passed so pricing uses the realized average.
domain: positions
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
  - position_id
optional_context:
  - as_of
write_actions: true
confirmation_required: true
success_criteria:
  - the number of fixing events created is reported
  - the number of fixings captured is reported
  - any still-uncaptured past observation dates are surfaced
routing:
  - request: "Set up or refresh the Asian fixing calendar, or capture a due fixing for an Asian position"
    persona: trader
---

## When to use

- Generate the fixing-date calendar for an Asian (averaging) option.
- Capture the realized close for observation dates that have already passed.
- An Asian position prices coarsely because its past fixings were never captured.

## Required inputs

`position_id` and `portfolio_id` from context or user text. Optional `as_of` limits capture to fixings on/before that date (default today).

## Procedure

The two writes are independent — run only the one(s) the user asked for.

1. Always first call `get_asian_schedule(position_id=<position_id>)` to read the schedule and which observations are already captured.
2. Calendar request only: after confirmation, `generate_asian_fixing_schedule(position_id=<position_id>, portfolio_id=<portfolio_id>)` plants one `fixing` event per averaging date (idempotent — cancels prior active fixing events first). Report `events_created`.
3. Capture request only: after confirmation, `capture_asian_fixings(position_id=<position_id>, portfolio_id=<portfolio_id>, as_of=<as_of>)` snapshots the close for each past, uncaptured date (pass `as_of` only when given; idempotent, never overwrites). Report `captured`.
4. After capturing, call `get_asian_schedule` again: any observation on/before the cutoff still without a captured price is uncaptured — name those dates. They need a `close` quote on the underlying; until captured, pricing uses a coarse uniform average.

## Guardrails

- Generate and capture are persisted writes; confirm before running on a live position.
- Never overwrite a captured fixing — realized observations are immutable.

## Examples

- "Set up the fixing calendar for position 42" → `get_asian_schedule(position_id=42)`, then `generate_asian_fixing_schedule(position_id=42, portfolio_id=7)`.
- "Capture due fixings for position 42 as of 2026-03-31" → `capture_asian_fixings(position_id=42, portfolio_id=7, as_of="2026-03-31")`, then re-read the schedule and name any dates still uncaptured.
