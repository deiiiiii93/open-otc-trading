---
name: snowball-pricing
description: Price or explain valuation drivers for CN Snowball products using the Snowball reference and pricing workflow. Use when user asks why a Snowball price, PnL, value, or quote looks high or low, when a Snowball ad-hoc spec needs read-only model pricing, or when a portfolio Snowball valuation looks stale and may need persisted repricing.
domain: snowballs
workflow_type: compound
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - position_id
optional_context:
  - portfolio_id
  - terms
  - market_inputs
  - pricing_parameter_profile_id
write_actions: false
confirmation_required: false
success_criteria:
  - Snowball pricing driver is explained or priced
  - persisted repricing need is routed to price-portfolio
routing:
  - request: "Snowball pricing or valuation drivers"
    persona: trader
---

## When to use

- User asks why a Snowball price, PnL, value, or quote looks high or low.
- A Snowball ad-hoc spec needs read-only model pricing.
- Portfolio Snowball valuation looks stale and may need persisted repricing.

## Required inputs

Use `position_id` or explicit Snowball terms. Call `get_product_reference_doc` with `SnowballOption` for payoff and conventions; read `/skills/references/pricing/engines.md`.

## Procedure

1. Apply `snowball-term-interpretation` to confirm KI, KO, coupon, tenor, and lifecycle state.
2. For ad-hoc specs, route to `price-product`.
3. For persisted positions, read latest valuation and market inputs through `position-diagnosis`.
4. If a write is needed, recommend `price-portfolio` with affected positions and pricing profile.

## Stop conditions

Do not mutate stored valuations from this workflow. Escalate to `desk_async` when a large Snowball book needs repricing.

## Output shape

Return pricing verdict, key drivers, lifecycle state, market-input caveats, latest valuation status, and next workflow.

## References

- `get_product_reference_doc(SnowballOption)`
- `/skills/references/pricing/engines.md`

## Example

User: Why is Snowball position 42 worth less today?
Assistant: Check terms, lifecycle, latest valuation, and market inputs, then explain drivers or route to `price-portfolio`.
