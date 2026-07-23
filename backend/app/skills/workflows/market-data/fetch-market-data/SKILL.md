---
name: fetch-market-data
description: Fetch current market snapshots for one or more underlyings using desk symbol conventions. Use when user asks to refresh or inspect market data for named underlyings, when a workflow needs current spot, index, or snapshot data, or when drift analysis needs a fresh snapshot before comparison.
domain: market-data
workflow_type: read
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - underlyings
optional_context:
  - start_date
  - end_date
  - asset_class
write_actions: false
confirmation_required: false
success_criteria:
  - snapshot results are returned per underlying
  - failed symbols and normalization decisions are listed
routing:
  - request: "Fetch current market data"
    persona: trader
---

## When to use

- Workflow needs current spot, index, or market snapshot data.
- User asks to refresh or inspect market data for named underlyings.
- Market-data drift analysis needs a fresh snapshot before comparison.

## Required inputs

Use a list of underlyings and the accounting date window. Read `/skills/references/market-data/conventions.md` for symbol and asset-class conventions.

## Procedure

1. Normalize each underlying to the desk symbol convention.
2. For each underlying, call `fetch_market_snapshot(symbol=<one symbol>, asset_class=<class>, start_date=<date>, end_date=<date>)`.
3. Collect successful snapshots, empty results, and failures separately.
4. Return the per-symbol fetch status and any normalization caveats.

## Stop conditions

Do not pass a list as one `symbol`. Ask for date range or asset class when symbol conventions are ambiguous.

Never fabricate market data and never substitute another date's data for the requested window: if a snapshot comes back empty or with `source_metadata.fallback=true` for the requested date, report the miss for that symbol instead of silently re-querying a different date and presenting its prices as the requested ones. Fetch the accounting-anchor date by default; a same-day window for a not-yet-concluded session legitimately returns no rows — treat that as "data not yet published", not as a missing symbol.

## Output shape

Return fetched count, failed count, date window, symbol mapping, and the next workflow that needs the snapshot.

## References

- `/skills/references/market-data/conventions.md`

## Example

User: Fetch current data for CSI 300 and CSI 500.
Assistant: Normalize symbols, call `fetch_market_snapshot` once per symbol, and return successes and failures.
