---
name: explain-market-data-drift
description: Compare current market snapshots with stored pricing inputs and explain stale or drifted values. Use when user asks whether market data is stale, missing, or drifted, when a pricing workflow needs drift evidence before proposing repricing, or when risk needs to know whether input drift could affect metrics.
domain: market-data
workflow_type: diagnostic
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - portfolio_id
optional_context:
  - position_snapshot
  - market_snapshot
  - threshold
write_actions: false
confirmation_required: false
success_criteria:
  - drifted and missing inputs are classified
  - top drift magnitudes and affected positions are identified
routing:
  - request: "Audit market-data freshness/coverage on a portfolio"
    persona: trader
---

## When to use

- User asks whether market data is stale, missing, or different from stored inputs.
- Pricing workflow needs evidence before proposing `price-portfolio`.
- Risk workflow needs to know whether input drift could affect metrics.

## Required inputs

Start from `position-inputs` and a fresh `fetch-market-data` result. Use desk thresholds from `/skills/references/market-data/conventions.md` unless the user provides one.

## Procedure

1. Match each stored input to the current snapshot by underlying and input type.
2. Compute relative and absolute drift; use `run_python` only when the table is too large for reliable in-context arithmetic.
3. Classify inputs as within threshold, drifted, or missing.
4. Return the highest drift rows and whether repricing is justified.

## Stop conditions

Ask for a portfolio or snapshot when neither is available. Do not fetch data from this workflow; call `fetch-market-data` first.

## Output shape

Return verdict, threshold, drifted count, missing count, top drift rows, affected positions, and next action.

## References

- `/skills/references/market-data/conventions.md`

## Example

User: Is market data stale for this portfolio?
Assistant: Compare current snapshots with stored inputs and explain which underlyings drifted enough to justify repricing.
