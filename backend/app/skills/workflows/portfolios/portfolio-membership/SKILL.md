---
name: portfolio-membership
description: Resolve a portfolio name or id and explain whether its positions are explicit or view-derived. Use when user names a portfolio and the id or membership model is unclear, when a workflow must distinguish Container portfolios from View portfolios, or when user asks why a position is or is not included in a portfolio.
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
  - portfolio_name
  - product_type
write_actions: false
confirmation_required: false
success_criteria:
  - portfolio kind and membership source are stated
  - position query path is clear
---

## When to use

- User names a portfolio and the id or membership model is unclear.
- Workflow needs to distinguish Container portfolios from View portfolios.
- User asks why a position is or is not included in a portfolio.

## Required inputs

Use `portfolio_id` when present. If only a name is present, call `list_portfolios` and confirm the resolved id when ambiguous.

## Procedure

1. Resolve the portfolio with `list_portfolios` or `get_portfolio`.
2. State whether membership is explicit or view-derived.
3. Call `get_positions(portfolio_id=<id>, product_type=<optional>)` when membership examples or counts are needed.
4. Return membership source, filters, and query caveats.

## Stop conditions

Do not mutate portfolio rules. Ask the user to choose when more than one portfolio matches a label.

## Output shape

Return portfolio id, name, kind, membership source, active filters, position count if read, and ambiguity notes.

## References

- `/skills/references/portfolios/model.md`

## Example

User: Why is this position in the Snowballs view?
Assistant: Resolve the portfolio, explain view-derived membership, and cite the rule or source behind inclusion.
