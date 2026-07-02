---
name: product-term-interpretation
description: Explain payoff terms and conventions for any non-snowball product family (vanilla, Asian, digital, touch, barrier, sharkfin, range accrual, autocallable variants, delta-one). Use when a position, imported row, or user question has ambiguous terms for these families, or a pricing or risk workflow needs term interpretation before computation.
domain: products
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
  - payoff terms and conventions are explained
  - ambiguous or missing economics are identified
routing:
  - request: "Non-snowball product terms or payoff interpretation"
    persona: trader
---

## When to use

- User asks what a non-snowball product's terms, barriers, or fields mean.
- Imported row or position for these families has ambiguous economics.
- Pricing or risk workflow needs term interpretation before computation.

## Required inputs

Use position terms, imported row fields, or explicit user text. Identify
the QuantArk family, then call `get_product_reference_doc` with it for the
resolved reference (definitions, conventions, required pricing inputs,
diagnostics). Do not read raw files under `/skills/references/products/`.

## Procedure

1. Identify the family and its economic fields from terms or the source row.
2. Call `get_product_reference_doc`; explain each term against it.
3. Flag missing or inconsistent required inputs that block pricing.
4. Route to the pricing workflow when the user asks for numbers.

## Stop conditions

Do not infer missing barriers, payoffs, or lifecycle state from the
product name alone. Snowball-family questions route to
`snowball-term-interpretation` instead.

## Output shape

Interpretation first, then normalized terms, missing fields, caveats, and
next workflow.

## Example

User: What does the accrual rate on this range accrual mean?
Assistant: Explain in-range accrual per observation, the range barriers,
and any missing required inputs.
