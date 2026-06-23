---
name: asian-fixings
description: Set up an Asian option's fixing calendar and lock in due fixings. Use when a user wants to generate the averaging-date fixing schedule for an Asian position, or to capture (snapshot) the close price for observation dates that have already passed so pricing uses the realized average.
domain: positions
workflow_type: write
allowed_envelopes:
  - desk_workflow
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

- A user wants to generate the fixing-date calendar for an Asian (averaging) option.
- A user wants to lock in (capture) the realized close for observation dates that have already passed.
- An Asian position prices coarsely because its past fixings were never captured.

## Required inputs

`position_id` and `portfolio_id` from page context or user text. Optional `as_of` limits capture to fixings on or before that date (default today).

## Procedure

1. Call `get_asian_schedule(position_id=<position_id>)` to read the averaging schedule and which observations already have a captured price.
2. Call `generate_asian_fixing_schedule(position_id=<position_id>, portfolio_id=<portfolio_id>)` to plant one informational `fixing` lifecycle event per averaging date. This is idempotent — re-running cancels prior active fixing events before re-creating them, so it is safe to refresh after a reschedule.
3. Call `capture_asian_fixings(position_id=<position_id>, portfolio_id=<portfolio_id>)` to snapshot the official close for every observation whose date has passed and is not yet captured. This is idempotent and never overwrites an existing fixing.
4. Report `events_created` and `captured`. Capture needs a `close` market quote on the underlying for each past date; if some remain uncaptured, tell the user — pricing falls back to a coarse uniform average until they are captured.

## Guardrails

- Both generate and capture are persisted writes; confirm before running on a live position.
- Never overwrite an already-captured fixing — captured prices are immutable realized observations.
