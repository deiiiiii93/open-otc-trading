---
name: parameters
description: Pricing parameter model — profile resolution order, assumption sets, and per-field attribution for position pricing and risk runs.
reference_type: pricing
---

## PricingParameterProfile (trade-keyed; xlsx-imported or agent-created)

Two stores feed pricing. Per-field resolution order in the pricer:
override -> pricing-parameter-profile row -> assumption-set row -> missing.


- Rows carry (source_trade_id, symbol, rate, dividend_yield, volatility).
- Row matching for a position: exact source_trade_id first; otherwise a
  UNIQUE COMPLETE row for the underlying. A row missing any of r/q/vol is
  "incomplete" and refused — what-if rows must carry all three fields
  (copy current values for the ones you are not changing).
- Empty source_trade_id = underlying-level row; trade-id matching only
  fires for positions that themselves carry a trade id.
- source_type: xlsx (imported), agent (agent-created),
  default_underlying_archived (read-only migration artifacts — never edit,
  never delete; historical runs reference them).
- Spots are NOT stored here. Observations live in the quote store.

## AssumptionSet (instrument-keyed; derived-only)

- Built from open-position scope: Instrument defaults resolve first, then
  the latest PricingParameterRow per underlying; per-field provenance is
  recorded in each row's source_payload.
- Never write AssumptionRows directly — set instrument defaults, rebuild.
- build refuses with unfilled_underlyings when any open underlying still
  misses a field after resolution: set those defaults, retry.

## Consuming a profile

run_batch_pricing accepts pricing_parameter_profile_id; run
diagnostics record market_input_source per field, so attribution is
verifiable after the run.

COVERAGE IS STRICT: once a profile is selected, positions it cannot
resolve (no trade-id row, no unique complete underlying row) are REFUSED —
they do not fall back to assumption sets. A one-underlying what-if profile
against a whole portfolio fails every other underlying's positions
("Selected pricing profile cannot extract pricing parameters…",
match_type "missing"). Either cover every in-scope underlying in the
profile, or narrow the run with position_ids to the covered positions.
