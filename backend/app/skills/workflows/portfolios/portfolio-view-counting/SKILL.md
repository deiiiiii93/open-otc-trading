---
name: portfolio-view-counting
description: Count positions, products, and filtered subsets in a portfolio without changing membership. Use when user asks how many positions are in a portfolio or view, asks for Snowball count, product-type count, status count, or recent effective-date count, or when a page assistant needs a compact count from current context.
domain: portfolios
workflow_type: read
allowed_envelopes:
  - pet_page
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - product_type
  - status
  - effective_last_days
write_actions: false
confirmation_required: false
success_criteria:
  - total count and applied filters are reported
  - product-type or status breakdown is explicit when requested
---

## When to use

- User asks how many positions are in a portfolio or view.
- User asks for Snowball count, product-type count, status count, or recent effective-date count.
- Page assistant needs a compact count from current context or `get_positions`.

## Required inputs

Use `portfolio_id`, plus optional filters such as `product_type`, `status`, or `effective_last_days`.

## Procedure

1. Prefer loaded page count when it exactly matches the requested filters.
2. Otherwise call `get_positions(portfolio_id=<id>, product_type=<optional>, status=<optional>, effective_last_days=<optional>)`.
3. Use `total_count` when returned; otherwise count rows in the response.
4. Report missing-date count when effective-date filtering is used.

## Stop conditions

Ask for portfolio id when missing. Do not infer product type from portfolio name if the user asked for exact counts.

## Output shape

Return count first, then portfolio id, filters, missing-date count, and whether the result came from loaded page context or a tool read.

## References

- `/skills/references/portfolios/model.md`

## Example

User: How many Snowball positions are in this portfolio?
Assistant: Use `get_positions` with `product_type="snowball"` and report `total_count` plus filters.
