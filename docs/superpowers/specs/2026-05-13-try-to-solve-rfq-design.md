# Try to Solve RFQ Design

**Date:** 2026-05-13  
**Status:** Approved for planning  
**Scope:** Standalone "Try to Solve" RFQ page, canonical termsheet registry, Excel import/export, and guarded QuantArk solving.

---

## Problem

The current Structured RFQ flow is too generic to define real OTC structured products. It exposes a small `product_kwargs` editor and solve controls, but it does not model the product-specific terms shown in `opt_quote_template_20240710.xlsx`, and it does not clearly separate:

- business-facing termsheet fields,
- workbook import/export fields,
- canonical app fields,
- QuantArk constructor fields,
- quoteable fields that can be solved,
- unsupported fields that are captured but not executable yet.

The result is a page that can demonstrate RFQ mechanics but is barely useful for real structured RFQ work.

## Goals

Build a standalone **Try to Solve** page that can:

1. Represent all 19 product sheets from the workbook template.
2. Show English UI labels while retaining Chinese Excel headers as import/export aliases.
3. Accept both manual entry and Excel import.
4. Handle Excel files with multiple request rows across multiple product sheets.
5. Normalize every row into one canonical termsheet model before validation or solving.
6. Use Pricing Parameter Profiles / Market Data Profiles plus manual market overrides.
7. Solve only when a normalized termsheet can build a real QuantArk product, pricing environment, and engine.
8. Keep unsupported products or quote fields visible as "schema captured, solver pending".
9. Export the request queue back to an Excel-compatible result workbook.

## Non-Goals

- Do not make unsupported products appear solved.
- Do not replace the existing RFQ approval/booking workflow in this step.
- Do not require all QuantArk product gaps to be implemented before the page can render every product.
- Do not expose Chinese labels as the primary UI language.
- Do not make the page a spreadsheet clone; Excel is an import/export format, not the canonical UI.

## Workbook Product Coverage

The registry must include all workbook sheets:

| Sheet | English Product Label | Initial Solver State |
|---|---|---|
| `autocall` | Autocall | solver-ready only after QuantArk mapping validates |
| `phoenix` | Phoenix | solver-ready where mapped to `PhoenixOption` |
| `vanilla` | Vanilla | solver-ready |
| `vertical_spread` | Vertical Spread | schema captured, solver pending unless mapped |
| `digital` | Digital | solver-ready where mapped to digital option fields |
| `binary_convex` | Binary Convex | schema captured, solver pending unless mapped |
| `single_sf` | Single Sharkfin | solver-ready where mapped to `SingleSharkfinOption` |
| `double_sf` | Double Sharkfin | solver-ready where mapped to `DoubleSharkfinOption` |
| `airbag` | Airbag | schema captured, solver pending unless mapped |
| `airbag_spread` | Airbag Spread | schema captured, solver pending unless mapped |
| `asian` | Asian | solver-ready where mapped to `AsianOption` |
| `call_put_portfolio` | Call Put Portfolio | schema captured, solver pending unless mapped |
| `ladder_binary` | Ladder Binary | schema captured, solver pending unless mapped |
| `forward` | Forward | solver-ready where mapped to `Futures` / delta-one forward semantics |
| `range_accrual` | Range Accrual | solver-ready where mapped to `RangeAccrualOption` |
| `one_touch` | One Touch | solver-ready where mapped to `OneTouchOption` |
| `double_no_touch` | Double No Touch | solver-ready where mapped to double-touch product support |
| `double_one_touch` | Double One Touch | solver-ready where mapped to `DoubleOneTouchOption` |
| `knock_out_autocall` | Knock-Out Autocall | solver-ready only after QuantArk mapping validates |

Every product must render in the page even when its solver state is not ready.

## Architecture

### Canonical Termsheet Registry

Add a backend registry as the source of truth for product-specific RFQ terms. Each product definition contains:

- `product_key`: stable app key, usually the workbook sheet name.
- `label`: English UI label.
- `excel_sheet`: workbook sheet name.
- `fields`: ordered product terms with English label, type, required flag, default value, Excel aliases, validation rules, and canonical path.
- `quote_fields`: business-facing solve/output fields with English label, Excel alias, canonical field path, default bounds, and QuantArk support status.
- `quantark_mapping`: product type, engine defaults, product kwargs mapping, market mapping needs, and support status.
- `schedule_sections`: optional table editors for KO, KI, coupon, accrual, Asian averaging, touch observation, or range accrual records.
- `export_columns`: original workbook columns plus app-generated operational result columns.

This registry drives manual forms, Excel parsing, validation, solve menus, and export.

### Normalized Request Model

Excel rows and manual forms both become normalized request drafts:

```text
TrySolveRequestDraft
  batch_id
  row_id
  source
  product_key
  product_label
  source_sheet
  source_row
  client_terms
  product_terms
  quote_request
  market
  quantark
  status
  diagnostics
```

The normalized draft is not automatically an RFQ lifecycle object. It is an intermediate workbench object. Later implementation can promote a solved row into the existing RFQ approval/booking flow.

### Guarded QuantArk Solving

The solve endpoint must follow a strict gate:

1. Validate required canonical terms.
2. Resolve market inputs from selected profiles plus overrides.
3. Build QuantArk `RFQTermsheetInput`.
4. Build QuantArk product, pricing environment, and engine.
5. Confirm selected quote field maps to a registered QuantArk unknown adapter or supported app adapter.
6. Run solve or price.
7. Store result, residual, model price, executable terms, and errors on the request row.

If any gate fails, the row remains visible with a precise status such as:

- `missing_terms`
- `missing_market`
- `mapping_pending`
- `unsupported_quote_field`
- `quantark_build_failed`
- `solve_failed`
- `solved`

## Page Design

The page uses a dense three-pane operational layout.

### Header

Top-right global actions:

- `Import Excel`
- `Export Excel`

These actions belong in the page header, not inside the product sidebar.

### Left Pane: Request Queue and Product Library

The left pane shows:

- imported request queue,
- product, sheet, row, underlying, solve status,
- import summary,
- product library for manual request creation.

Excel import can create many request rows across many products. Selecting one queue item loads it into the center editor. Manual creation adds a new queue item from the product library.

### Center Pane: Canonical Termsheet Editor

The center pane shows the selected request:

- common terms: counterparty, side, underlying, notional, prepay ratio, lock time, start date, tenor,
- product-specific fields in English,
- schedule/table editors for products that need observation rows,
- market profile selectors and manual overrides.

The UI stores source Excel aliases in metadata, but labels remain English.

### Right Pane: Solve Panel

The right pane shows:

- solve field menu built from product quote fields,
- target label and target value,
- lower bound, upper bound, initial guess,
- validation action,
- solve current request action,
- solve ready queue action,
- result, residual, model price, executable QuantArk terms, and diagnostics.

## Excel Import

Import accepts a workbook shaped like `opt_quote_template_20240710.xlsx`.

Behavior:

1. Iterate all recognized product sheets.
2. Treat each non-empty row after the header as one request.
3. Map Chinese headers to canonical field paths via the registry.
4. Preserve the original sheet name, row number, raw cell values, and parse diagnostics.
5. Create a request queue with one normalized draft per row.
6. Mark each row with parse status and solver readiness.

Unknown columns are preserved in `source_payload` and surfaced as diagnostics instead of discarded.

## Excel Export

Export writes the current queue to an Excel-compatible workbook.

Export supports these scopes:

- all rows,
- selected rows,
- solved rows,
- errors only.

For rows imported from Excel, export preserves the workbook sheet grouping and original request columns where possible. For manually created rows, export writes rows into the matching product sheet shape.

Export fills:

- the original quote/output columns where the solved field maps to one of them,
- `Solve Status`,
- `Model Price`,
- `Residual`,
- `Error`,
- `Solved Field`,
- `Solved Value`,
- `Target Label`,
- `Target Value`,
- `QuantArk Product`,
- `Engine`,
- `Executable Terms` metadata reference or compact JSON.

## Quote Field Vocabulary

The workbook quote/output columns become the business-facing quote menu, translated into English labels. Examples:

| Excel Header | English UI Label | Canonical Meaning |
|---|---|---|
| `期权费率` | Premium Rate | solve or report premium rate |
| `固定收益率` | Fixed Yield | solve or report fixed yield |
| `年化返息` | Annualized Coupon | usually maps to coupon or KO return rate |
| `绝对返息` | Absolute Coupon | non-annualized coupon/return amount |
| `行权收益率` | Exercise Yield | payoff yield at exercise |
| `派息收益率` | Coupon Yield | Phoenix coupon rate where supported |
| `敲出障碍` | Knock-Out Barrier | KO barrier where supported |
| `行权价` | Strike | strike where supported |

Each product definition controls which quote fields are offered and whether each field is currently solver-ready.

## Market Inputs

The page supports both profile-backed and manual market inputs:

- Pricing Parameter Profile selector,
- Market Data Profile selector where applicable,
- valuation date,
- underlying spot,
- volatility,
- rate,
- dividend yield,
- day count convention,
- business calendar.

Manual overrides take precedence over selected profile values. The resolved market snapshot is stored on each queue row before validation/solve.

## Backend API Shape

Add endpoints under a "try solve" namespace, separate from the persisted RFQ approval lifecycle:

- `GET /api/rfq/try-solve/catalog`
- `POST /api/rfq/try-solve/import`
- `POST /api/rfq/try-solve/validate`
- `POST /api/rfq/try-solve/solve`
- `POST /api/rfq/try-solve/solve-batch`
- `POST /api/rfq/try-solve/export`

The existing `/api/rfq/catalog` and `/api/client/rfq/form` routes can continue to exist. They should not be overloaded with Excel batch semantics.

## Data Persistence

For the first implementation, the request queue can be client-held plus server-returned payloads, unless export requires temporary server-side file handles. This avoids adding a premature database lifecycle.

Persisted RFQ rows should only be created later when a user explicitly promotes a solved queue item into the approval workflow.

## Testing

Backend tests should cover:

- catalog includes all 19 product definitions,
- Excel import parses mixed-product multi-row workbooks,
- Chinese headers map to English/canonical fields,
- unsupported products are captured with solver-pending status,
- supported vanilla solve still returns a QuantArk quote,
- export preserves product sheets and writes solve result/status columns.

Frontend tests should cover:

- product library renders all product labels,
- import button is in the page header,
- imported multi-row queue renders row statuses,
- selecting a queue row loads the form,
- solve field menu uses English labels,
- export scope controls are available.

## Open Implementation Risks

- Some workbook products may not have a direct QuantArk product yet.
- Some quote fields, especially coupon and return conventions, may need product-specific interpretation before solving is trustworthy.
- Schedule generation from tenor/frequency must match existing QuantArk calendar and observation semantics.
- Exporting full executable terms as JSON can make workbooks noisy; the implementation should prefer compact metadata and include detailed JSON only when requested.

