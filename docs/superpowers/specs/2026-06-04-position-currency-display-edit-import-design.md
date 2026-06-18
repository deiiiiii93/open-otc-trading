# Position Currency: Display, Edit, Import

**Date:** 2026-06-04
**Status:** Approved (design review with user)

## Problem

`Position.currency` exists in the DB (String(8), non-null, default/server-default CNY,
added by the currency-convention project) and drives currency-aware risk bucketing —
but it is invisible and mostly unwritable outside booking:

- `PositionOut` does not serialize it, so no frontend surface can show it.
- The Positions table and Position Detail drawer have no currency field.
- The Edit Position flow cannot change it; worse, `PositionEditForm` hardcodes
  `currency: 'USD'` into every product spec it saves, silently creating USD products
  on edit (the likely source of portfolio X's surprise USD snowball; `ProductSpecIn`
  shares the same `"USD"` default).
- The xlsx import (汇总 sheet) has no currency handling at all — and worse than
  "defaults to CNY": `product_spec_from_position_payload` (products.py:341) defaults
  the spec currency to **USD**, so every imported product/position is currently
  mislabeled USD (confirmed in the dev DB: all imported positions carry USD).
  The real sheet (161 columns) has no currency column today.
- The import's *re-import* branch (existing trade) bypasses `set_position_currency`,
  so even if the product carried a currency, an update would not refresh the position.

## Decisions (user-approved)

1. **Editable with validation and warning** — currency is editable in the Position
   Detail "Edit Position" form, ISO-4217 validated, with a non-blocking warning when
   it deviates from the booked trade's currency. (Not display-only.)
2. **Import: optional 币种 column** — read a per-row currency when a 币种 (or
   `Currency`) header is present; absent column/blank cell → CNY (current behavior);
   invalid value → that row becomes an error row (gate philosophy: validate, don't
   trust). Not a required column; no portfolio-level import picker.
3. Fix the hardcoded `'USD'` in `PositionEditForm` as part of this work.
   `ProductSpecIn`'s `"USD"` default is flagged but **out of scope**.

## Design

### A. Display (read path)

| Layer | Change |
|---|---|
| `backend/app/schemas.py` `PositionOut` | add `currency: str = "CNY"` — `model_config from_attributes` picks it up at every existing serialization site; no endpoint changes. |
| `frontend/src/types.ts` `Position` | add `currency: string`. |
| `frontend/src/routes/Positions.tsx` `PositionRow` | add `currency: string`. |
| `frontend/src/routes/Positions.live.tsx` row mapping | `currency: position.currency`. |
| Positions table (`Positions.tsx` columns array) | new narrow column `{ key: 'currency', header: 'CCY' }` between ENTRY and PRICE (currency qualifies the money columns). |
| Position Detail → Contract Snapshot | `<DetailItem label="Currency" value={row.currency} />` alongside the Product fields. |

### B. Edit (write path: explicit user edit)

- `PortfolioPositionSpec` (`schemas.py`) gains `currency: str | None = None` with a
  model validator using `normalize_currency` + `ISO_4217_CODES` (same pattern as
  `FxRateCreate` / `AgentThreadUpdate.report_currency`). Invalid → 422. `None` →
  unchanged (backward compatible: existing clients omit it).
- `patch_position` (`main.py`), **both** branches (product-replacing and
  fields-only):
  - if `payload.currency` is provided → `position.currency = <normalized>`;
  - else if the product was replaced → `set_position_currency(position)` (today the
    PATCH never touches position currency; this aligns it with booking provenance).
  - Precedence: explicit `payload.currency` wins over product-derived.
- `PositionEditForm` (`frontend/src/components/PositionEditForm.tsx`):
  - currency text input seeded from `row.currency`, uppercased, 3 letters;
  - inline non-blocking warning when the entered value differs from
    `row.product?.currency` (the booked trade's currency): *"differs from booked
    trade currency (X) — risk will re-bucket under the new currency"*;
  - submits `currency` in the PATCH body, and the product spec it constructs sends
    the form's currency instead of the hardcoded `'USD'`;
  - backend 422 for invalid codes surfaces through the form's existing error slot.

### C. Import (write path: xlsx)

- `map_trade_row` (`backend/app/services/position_adapter.py`) gains a post-step
  applied uniformly after the per-structure mapper (no changes to the 10 mappers):
  read `row["币种"]` (fallback `row["Currency"]`); if non-empty, normalize via
  `normalize_currency`:
  - valid → attach to the mapping so it lands on `ProductBookingSpec.currency`
    (riding the existing provenance chain: spec → `build_product` →
    `Product.currency` → `set_position_currency` → position);
  - invalid → the row becomes an error row (`_error_mapping`), consistent with how
    the booking gate isolates bad rows;
  - blank / column absent → no attachment; defaults stay CNY (current behavior).
- `PositionMapping` (frozen dataclass) gains `currency: str | None = None`;
  `_product_booking_spec_from_mapping` overrides the spec currency with
  `mapping.currency or "CNY"` — this both forwards the 币种 value and pins the
  import channel's default to CNY, fixing the USD mislabeling at its source
  (`product_spec_from_position_payload`'s generic USD default no longer leaks
  into imports).
- **Repair path for existing mislabeled rows:** `product_term_hash` includes
  currency, so re-importing the same file under the fixed code mints new CNY
  products and (with the gap fix below) refreshes positions to CNY. No DB
  migration needed; re-import is the documented repair.
- **Gap fix:** the existing-trade re-import branch in `import_positions_from_xlsx`
  calls `set_position_currency(position)` after assigning the new product, so
  re-imports refresh currency exactly like fresh imports.
- `REQUIRED_HEADERS` is unchanged (币种 is optional).

## Error handling

- Backend PATCH: invalid currency → 422 with the offending code in the message.
- Import: invalid 币种 value → per-row error entry in the batch errors list (file
  import continues), position demoted/booked as error row via the existing paths.
- Frontend: warning is advisory only; saving is never blocked by a mismatch.

## Testing

Backend (pytest):
- `PositionOut` round-trips `currency` from a Position row.
- PATCH: sets a valid currency (both product and fields-only branches); rejects an
  invalid code with 422; omitting `currency` leaves the stored value unchanged;
  product-replacement without explicit currency re-derives via
  `set_position_currency`.
- Import: sheet with 币种 column → per-row currencies persisted on positions; row
  with invalid 币种 → error row, others import; sheet without the column → CNY;
  re-import of an existing trade with a changed 币种 → currency refreshed.

Frontend (vitest):
- Positions table renders the CCY column value.
- Position Detail Contract Snapshot shows Currency.
- Edit form: seeds from `row.currency`; PATCH body contains `currency`; the product
  spec no longer contains hardcoded `'USD'`; mismatch warning appears when the value
  differs from `row.product.currency` and disappears when equal.

## Out of scope

- FX conversion / display of converted values (covered by the currency-convention
  project's `convert_currency` tool and FxRate table).
- `ProductSpecIn.currency = "USD"` default used by other channels (flagged as a
  follow-up; changing it affects booking/agent channels beyond this task).
- Quanto support (project-wide non-goal).
- DB backfill of existing mislabeled rows (re-import under the fixed code is the
  repair path; see Part C).
