---
name: hedge-portfolio
description: Size and book a per-underlying greek hedge — solver-sized (four
  hedging strategies) or desk-stated legs booked with the manual tag. Use when
  a desk wants to neutralize delta/gamma/vega within bands, book explicit hedge
  legs, or act on an in-thread hedging recommendation.
domain: hedging
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - underlying
  - strategy
  - legs
  - bands
write_actions: true
confirmation_required: true
success_criteria:
  - sized legs with residual greeks and feasibility are returned before booking
  - infeasible hard bands are reported with the binding greek, never booked silently
  - booked legs are tagged with risk_run_id and sizing strategy (solver name or manual)
  - hedge legs are never booked through book_position
routing:
  - request: "Solve/size a portfolio greek hedge (strategies, bands)"
    persona: risk_manager
  - request: "Book stated hedge legs / act on a hedge recommendation"
    persona: trader
---

## When to use

- Solve entry: desk wants per-underlying greeks neutralized.
- Manual entry: desk states explicit hedge legs/quantities or acts on an
  in-thread recommendation.

## Procedure

1. Guard (both entries): `get_hedgeable_underlyings(portfolio_id)`. On
   `no_risk_run`, stop — ask to run risk first. Warn if stale. Keep
   `risk_run_id` and `spot` — `book_hedge` needs them.
2. Manual entry — user stated instrument(s) + signed quantities: go to step 6
   with `strategy="manual"` and the stated legs. `underlying` is the hedged
   exposure's symbol, not the hedge instrument's code.
3. Solve entry: pick `underlying` + `strategy` (confirm if unspecified); call
   `propose_hedge(portfolio_id, underlying, strategy)`.
4. Present legs, bands, quantities, residuals. If `infeasible`, report binding
   greek(s) + shortfall; suggest an option leg or wider band. Do not book.
5. Review loop: on comments, re-solve with overridden `legs`/`bands`/`strategy`
   and re-present. If the user dictates quantities, switch to step 6 with
   `strategy="manual"`.
6. Book: `book_hedge(portfolio_id, underlying, risk_run_id, strategy, spot,
   legs)`. The HITL confirmation card is the booking gate.
7. Report booked position ids — hedge-tagged, on the Hedging page.

## Stop conditions

Never book an infeasible hard-band solution, guess greek targets without a
completed risk run, or book hedge legs via `book_position` (loses the hedge
tag).

## Output shape

Feasibility (solve) or stated legs (manual) first; then strategy or `manual`,
per-leg quantities, residual/binding greeks, booked ids.

## References

- `/skills/references/hedging/strategy.md`

## Example

User: Book the short 2 IC futures as the CSI500 hedge.
Assistant: get_hedgeable_underlyings(4) → fresh → book_hedge(4, "000905.SH",
run_id, "manual", spot, [future IC qty −2]) → HITL card → ids.
