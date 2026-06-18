---
name: position-inputs
description: Enumerate unique market-data dependencies required by a portfolio position snapshot. Use when user asks what market data a portfolio needs, when a market-data fetch or drift workflow needs the full underlying and input set, or when a portfolio snapshot already exists and needs dependency compression.
domain: positions
workflow_type: read
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - position_snapshot
write_actions: false
confirmation_required: false
success_criteria:
  - unique underlying and input-type pairs are listed
  - top dependencies by position count are identified
---

## When to use

- User asks what market data a portfolio needs.
- Market-data fetch or drift workflow needs the full underlying and input set.
- A portfolio snapshot already exists and needs dependency compression.

## Required inputs

Start from a `position-snapshot` result. If no snapshot exists, run that workflow first.

## Procedure

1. Extract each position underlying from the snapshot.
2. For each position, collect required input types: spot, volatility, rate, dividend yield, and dividend schedule when terms require it.
3. Use `run_python` only when the position list is too large to count reliably in context.
4. Return deduped pairs and counts by pair.

## Stop conditions

Ask for `portfolio_id` when neither snapshot nor portfolio context is available.

## Output shape

Return unique pair count, top pairs by position count, and any positions whose inputs could not be inferred.

## References

- `/skills/references/market-data/conventions.md`

## Example

User: What market inputs do these positions depend on?
Assistant: Enumerate unique underlying and input-type pairs and list the highest blast-radius dependencies.
