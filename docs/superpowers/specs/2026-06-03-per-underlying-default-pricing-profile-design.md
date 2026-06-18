# Per-underlying default pricing profile — design

Date: 2026-06-03
Status: Approved (design), pending implementation plan

## Problem

`build_default_pricing_profile` (`services/pricing_profiles.py`) builds a pricing
profile from open-position underlyings + per-underlying AKShare spot +
per-underlying defaults (rate/div/vol). Its economics are therefore **entirely
per-underlying** — every position on `000300.SH` gets the same spot, rate,
dividend yield, and volatility.

Yet the builder still emits **one `PricingParameterRow` per position, keyed by
`source_trade_id`**, and *skips any open position that has no trade id*
(recording it under `summary.skipped_positions` with reason
`missing_source_trade_id`). This is a vestige of the original design, when the
only source of pricing params was the external XLSX import
(`import_pricing_parameter_profile_from_xlsx`), where each trade row genuinely
carries distinct params and a trade id is the natural key.

Concrete consequences observed in the live book (profile id 6, "Default
2026-06-03 00:00"):

- **Position #109** (`000300.SH` snowball, no `source_trade_id`) was skipped
  entirely → no row → cannot be priced. The underlying-fallback can't rescue it
  because the profile contains **two identical** complete `000300.SH` rows, so
  the fallback is ambiguous.
- The profile has **104 rows** that are mostly duplicates (e.g. 16 byte-identical
  `000905.SH` rows), one per position, instead of ~10 (one per underlying).
- A second, related vestige: `latest_pricing_rows_by_trade_id` round-trips a
  profile's rows through a `{source_trade_id: row}` dict and both consumers
  immediately call `list(...values())`. That dict step is at best a no-op and at
  worst **lossy** — rows sharing a blank/duplicate trade id silently collapse.

## Goal

Make the default profile **per-underlying**: exactly one `PricingParameterRow`
per live underlying, with no dependence on `source_trade_id`. Positions resolve
their params by underlying at pricing time. The XLSX-import path stays
per-trade and is untouched. Trade-id-less positions (like #109) get priced.

## Non-goals

- No change to the XLSX-import path or its per-trade keying.
- No DB schema migration.
- No backfill/migration of existing default profiles.
- No change to the resolver `resolve_pricing_parameter_row_for_position`.

## Approach (chosen)

Reuse the existing `pricing_parameter_rows` table. A default-profile row is
identified by its `symbol`; its `source_trade_id` is the empty string `""`.
Why `""` rather than `NULL`: the column is `NOT NULL`
(`models.py:1256`, `source_trade_id: Mapped[str] = mapped_column(String(160),
index=True)`), and the resolver already treats a falsy trade id as "not
trade-keyed" (`if trade_id:` guards the exact-match branch). All three indexes on
the table are non-unique and there is no table-level unique constraint, so
multiple `""`-keyed rows per profile are valid. This avoids an Alembic migration.

Within any single profile the keying is homogeneous — a profile is either XLSX
(every row has a real trade id) or default (every row has `""`) — so there is no
mixed-keying ambiguity.

## Resolution semantics (unchanged, verified)

`resolve_pricing_parameter_row_for_position(rows, position)` already does
trade-id-first, then "unique complete row for the underlying":

- **Default profile, position with a trade id**: no row matches the trade id (all
  rows are `""`) → underlying fallback → exactly one complete row per symbol →
  `match_type="underlying"`. ✓
- **Default profile, trade-id-less position (#109)**: exact branch skipped →
  underlying fallback → one complete row → `match_type="underlying"`. ✓
- **XLSX profile**: exact trade-id match as today → `match_type="trade_id"`. ✓

Because the builder emits exactly one row per symbol, the underlying fallback is
never ambiguous for a default profile (today's #109 ambiguity came from the
duplicate per-position rows, which this change removes).

## Backend changes

1. **`services/pricing_profiles.py` — `build_default_pricing_profile`.**
   Replace the per-position loop (the `open_positions` query, the `敲出` filter,
   the `missing_source_trade_id` skip, and the per-position `PricingParameterRow`
   construction, currently ~lines 422-484) with a per-underlying emit:

   ```python
   row_count = 0
   for underlying in underlyings:              # already excludes closed/敲出
       store = existing[underlying]
       manual_inputs = resolved_inputs[underlying]
       inherited = inherited_inputs.get(underlying) or {}
       manual_field_sources = {
           field: ("underlying_default"
                   if getattr(store, field) is not None
                   else "latest_pricing_parameter_profile")
           for field in MANUAL_INPUT_FIELDS
       }
       session.add(PricingParameterRow(
           profile_id=profile.id,
           source_trade_id="",            # per-underlying row: not trade-keyed
           symbol=underlying,
           spot=fetched[underlying]["spot"],
           rate=manual_inputs["rate"],
           dividend_yield=manual_inputs["dividend_yield"],
           volatility=manual_inputs["volatility"],
           source_row=None,
           source_payload=make_json_safe({
               "source": "default_underlying",
               "underlying_default_id": store.id,
               "akshare_symbol": fetched[underlying]["akshare_symbol"],
               "manual_input_sources": manual_field_sources,
               "inherited_pricing_parameter_profile_id":
                   inherited.get("pricing_parameter_profile_id"),
               "inherited_pricing_parameter_row_id":
                   inherited.get("pricing_parameter_row_id"),
           }),
       ))
       row_count += 1
   ```

   - `underlyings` is already the live, distinct, non-`敲出`/non-closed set
     (`open_position_underlying_symbols`), so no per-position iteration is needed.
   - `summary`: keep `row_count` (= `len(underlyings)`), `underlyings`,
     `valuation_date`, `adjust`. **Drop `skipped_positions`** — the concept no
     longer exists.

2. **`services/pricing_profiles.py` — replace `latest_pricing_rows_by_trade_id`.**
   Introduce:

   ```python
   def pricing_rows_for_profile(
       session: Session, *, profile_id: int
   ) -> list[PricingParameterRow]:
       profile = (
           session.query(PricingParameterProfile)
           .options(selectinload(PricingParameterProfile.rows))
           .filter(PricingParameterProfile.id == profile_id)
           .one_or_none()
       )
       if profile is None:
           raise ValueError(f"Pricing parameter profile not found: {profile_id}")
       return list(profile.rows)
   ```

   Remove `latest_pricing_rows_by_trade_id` (no caller needs the dict form).

3. **`services/risk_engine.py` (~line 74).** Replace
   `pricing_rows_by_trade = latest_pricing_rows_by_trade_id(...)` /
   `pricing_rows = list(pricing_rows_by_trade.values())` with
   `pricing_rows = pricing_rows_for_profile(session, profile_id=...)`. Update the
   import.

4. **`services/position_pricer.py` (~lines 156-160, 529-532).** Replace the
   `latest_pricing_rows_by_trade_id(...)` call with
   `pricing_rows_for_profile(...)` returning a list; the call site already does
   `resolve_pricing_parameter_row_for_position(list(pricing_rows...), position)`
   — simplify to pass the list directly. Update the import.
   (Leave `latest_market_inputs_by_trade_id` / `market_inputs.get(...)` alone —
   that is a different concern, `PositionMarketInput`, not pricing rows.)

No change to `resolve_pricing_parameter_row_for_position`,
`resolve_underlying_market_params`, or `pricing_parameter_resolution_message`.

## Testing

Existing `build_default` tests assert the per-trade shape and must be rewritten
to per-underlying. Use non-default input values so assertions are not vacuous.

- **`tests/test_underlying_defaults.py`**
  - `test_build_happy_path`: today asserts `len(profile.rows) == 3` (TRD-1/TRD-2
    on `000300.SH`, TRD-3 on `000852.SH`) and indexes `by_trade["TRD-1"]`. Rewrite
    to assert **one row per symbol** (2 rows), keyed/looked-up by `symbol`, with
    the expected spot/rate/div/vol per underlying, and `source_trade_id == ""`.
  - `test_build_skips_positions_without_trade_id`: **invert.** Booking a position
    without a trade id no longer skips anything; assert its underlying produces a
    row and `summary` has no `skipped_positions` key.
  - `test_default_underlying_profile_rows_match_equivalent_xlsx`: today matches the
    default row to the XLSX row by `TRD-1`. Rework to compare **per-underlying
    economics** (spot/rate/div/vol/symbol) between the default symbol row and the
    equivalent XLSX trade row.
  - `test_build_appends_new_profile_each_invocation`,
    `test_build_fails_*`, `test_build_default_endpoint_*`: verify they still hold;
    adjust any `row_count`/row-count assertions to the per-underlying count.
  - **New** `test_build_default_prices_trade_id_less_position`: seed an open
    position with a blank `source_trade_id` (the #109 case) plus a complete
    underlying default; build; assert the profile has a `""`-keyed symbol row and
    that `resolve_pricing_parameter_row_for_position(rows, position)` returns
    `match_type == "underlying"` and `ok is True`.

- **`tests/test_position_import_pricing.py`**
  - `test_latest_pricing_rows_by_trade_id_uses_profile_rows` (line ~690): rename
    to `test_pricing_rows_for_profile_returns_all_rows`; assert the function
    returns all rows as a list (including ones that share/blank a trade id, to
    pin the no-collapse behavior).
  - Verify the end-to-end import→price tests still pass (XLSX path unchanged).

- **Regression**: a risk run against a per-underlying default profile prices a
  trade-id-less position (integration-level assertion that #109-style positions
  no longer surface a `pricing_error`).

## Rollout

No migration. After merge, the user clicks **Build Default Profile** once to
replace profile 6 with the per-underlying shape (~10 rows). Pre-existing
per-trade default profiles remain valid and still resolve (by trade id or
underlying), so nothing breaks if they are left in place.

## Out of scope (YAGNI)

- Backfilling/rewriting existing profiles' rows.
- Making `source_trade_id` nullable (the `""` convention avoids it).
- Any UI change to the Pricing Parameters page beyond the naturally smaller row
  count it already renders from the profile.
- Guarding against `#<id>`-style trade ids at booking/edit (a separate data-
  hygiene concern noted during investigation; not required by this change).
