---
name: snowball-term-interpretation
description: Explain CN Snowball payoff terms, KI/KO schedules, lifecycle flags, and imported term conventions. Use when user asks what Snowball terms, barriers, or lifecycle fields mean, when an imported row or position has ambiguous KI, KO, coupon, tenor, or observation fields, or when pricing or risk workflow needs term interpretation before computation.
domain: snowballs
workflow_type: read
allowed_envelopes:
  - pet_page
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - terms
optional_context:
  - position_id
  - product_key
write_actions: false
confirmation_required: false
success_criteria:
  - payoff terms and lifecycle flags are explained
  - ambiguous or missing economics are identified
routing:
  - request: "Snowball terms or payoff interpretation"
    persona: trader
---

## When to use

- User asks what Snowball terms, barriers, or lifecycle fields mean.
- Imported row or position has ambiguous KI, KO, coupon, tenor, or observation fields.
- Pricing or risk workflow needs Snowball term interpretation before computation.

## Required inputs

Use position terms, imported row fields, or explicit user text. Call `get_product_reference_doc` with `SnowballOption` for payoff invariants and conventions (resolved base + regional overlay); do not read raw files under `/skills/references/products/`.

## Procedure

1. Identify underlying, notional, tenor, strike, KI, KO, coupon, and observation schedules.
2. Explain KI convention, KO observation timing, and lifecycle state.
3. Verify completeness with `check_term_completeness` (never from memory); flag its `missing_required` set and inconsistent fields.
4. Route to `snowball-pricing` or `snowball-risk-explain` when the user asks for numbers.

## Stop conditions

Do not infer missing barriers or lifecycle state from product name alone. Ask for the missing term or source row.

## Output shape

Return interpretation first, then normalized terms, missing fields, lifecycle caveats, and next workflow.

## References

- `get_product_reference_doc(SnowballOption)`

## Example

User: What does this Snowball KI field mean?
Assistant: Explain the KI convention, observation schedule, lifecycle effect, and any missing terms needed for pricing.
