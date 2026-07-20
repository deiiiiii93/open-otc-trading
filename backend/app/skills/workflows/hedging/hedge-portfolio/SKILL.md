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

## Procedure

1. Read `/skills/references/hedging/strategy.md`.
2. Guard with `get_hedgeable_underlyings(portfolio_id)`. Stop on no/stale risk.
   Preserve its exact evidence tuple from the result and attached artifact ref.
3. Manual entry: use the user's signed legs with `strategy="manual"`;
   `underlying` names the exposure, not the hedge contract.
4. Solve entry: confirm the underlying/strategy, then call `propose_hedge`. Its
   artifact supersedes the guard artifact as booking evidence.
5. Present quantities, bands, residuals, and binding greeks. Never book an
   infeasible solution. Re-solve after edits.
6. Before booking, apply the reference's freshness/recovery rules. Call
   `book_hedge` with the exact evidence tuple, strategy, spot, and legs. HITL is
   the booking gate.
7. Report the hedge-tagged position ids.

## Stop conditions

Never guess Greeks, reuse expired/superseded evidence, or use `book_position`.
`stale_hedge_proposal` is a hard stop: refresh risk and re-solve.

## References

- `/skills/references/hedging/strategy.md`

## Example

User: Book short 2 IC futures as the CSI500 hedge.
Assistant: guard → retain fresh artifact tuple → `book_hedge(...,
strategy="manual", legs=[IC quantity -2])` → HITL → report ids.
