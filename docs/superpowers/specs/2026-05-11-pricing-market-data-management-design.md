# Pricing Parameters And Market Data Management Design

Date: 2026-05-11
Status: Approved for implementation planning

## Goal

Add two repo-consistent management pages:

- A global pricing parameters page with XLSX import.
- A global market data page with AKShare connection and saved snapshots.

Pricing parameters are global, date based, and trade-id scoped. One valuation date has a profile containing a set of pricing parameter rows. AKShare snapshots are also saved as global market-data profiles and can later feed pricing. At this stage, AKShare feeds only `spot` from the latest close.

## Approved Approach

Use new global profile tables and adapt existing endpoints. This avoids forcing global data through current portfolio-scoped `PositionMarketInput` records while preserving existing Positions and pricing workflows.

Rejected approaches:

- Reusing portfolio-scoped market inputs as fake global profiles, because that fragments a date profile by portfolio.
- Building a full market-data platform now, because curves, surfaces, instruments, and derived volatility are outside the current spot-only scope.

## Data Model

### PricingParameterProfile

Global parent entity for one dated set of pricing inputs.

Fields:

- `id`
- `name`
- `valuation_date`
- `source_type`, initially `xlsx`
- `source_path`
- `status`
- `summary`
- `created_at`
- `updated_at`

### PricingParameterRow

Child row keyed by `profile_id + source_trade_id`.

Fields:

- `id`
- `profile_id`
- `source_trade_id`
- `symbol`
- `spot`
- `rate`
- `dividend_yield`
- `volatility`
- `source_row`
- `source_payload`
- `created_at`
- `updated_at`

Rows are resolved against positions by `Position.source_trade_id`. Unmatched rows remain visible in the profile for reconciliation.

### MarketDataProfile

Global AKShare snapshot profile.

Fields:

- `id`
- `name`
- `source`, initially `akshare`
- `symbol`
- `asset_class`
- `start_date`
- `end_date`
- `adjust`
- `valuation_date`
- `data`
- `source_metadata`
- `created_at`
- `updated_at`

The stored `data` contains normalized OHLC rows and the latest close. The latest close is the only pricing feed at this stage.

## Pricing Resolution

`POST /api/portfolios/{portfolio_id}/positions/price` gains `pricing_parameter_profile_id`.

Resolution precedence:

1. Explicit run overrides.
2. Selected global pricing parameter profile.
3. Existing portfolio-scoped market inputs for backward compatibility.
4. Existing AKShare spot fallback.

The valuation run response, persisted run, and audit payload include `pricing_parameter_profile_id` when one is used.

If a selected profile has no row for a trade id, the missing row should be surfaced as a per-position pricing error unless a later fallback supplies usable inputs. The result payload should make the source of each market input clear.

## Backend API

Pricing parameters:

- `GET /api/pricing-parameter-profiles`
- `POST /api/pricing-parameter-profiles/import`
- `GET /api/pricing-parameter-profiles/{profile_id}`

The import endpoint accepts XLSX file upload, optional sheet name, profile name, and valuation date. If valuation date is omitted, infer it using the existing filename-date behavior where possible.

Market data:

- `GET /api/market-data/profiles`
- `GET /api/market-data/profiles/{profile_id}`
- `POST /api/market-data/profiles/akshare`

The AKShare endpoint accepts symbol, asset class, start date, end date, adjust mode, and optional profile name. It fetches via the existing AKShare service, persists the request/result, and records fallback/source metadata.

Spot feed:

- Add a narrowly scoped action that creates or updates pricing parameter spot rows from a market-data profile's latest close.
- The action must only write `spot`; it must not derive volatility, rate, dividend yield, or other parameters.

## Frontend Routes

Add two AppShell routes near the existing trading/risk pages:

- `Pricing Parameters`
- `Market Data`

The routes should be wired through the same main entry pattern already used by the repo. They should also be available from command palette jump actions if the implementation keeps command items in sync with navigation.

## UI Requirements

The new pages must match the existing repo UI. They should reuse the same compact operational style as `Positions`, `Risk`, `Reports`, and `Portfolios`.

Implementation constraints:

- Use `AppShell`, `PageHeader`, `Tile`, `Table`, `Button`, `Modal`, `Skeleton`, and `Empty` patterns where applicable.
- Use existing design tokens and route-local CSS conventions.
- Keep layouts dense, organized, and management-oriented.
- Do not introduce a new dashboard visual language.
- Do not use marketing-style sections, oversized cards, or unrelated decorative styling.

### Pricing Parameters Page

Header:

- Title: `PRICING PARAMETERS`
- Chips: global, selected valuation date, row count
- Primary action: `Import XLSX`

Layout:

- Profile list sorted by valuation date/import time.
- Selected profile summary.
- Summary tiles for imported, matched, unmatched, duplicate, and error counts.
- Row table columns: trade id, symbol, spot, volatility, rate, dividend yield, source row, status.
- Import modal with file picker, sheet name, valuation date, and profile name.

### Market Data Page

Header:

- Title: `MARKET DATA`
- Chips: AKShare, selected symbol, latest close
- Primary action: `Fetch AKShare`

Layout:

- Saved profile list by symbol/date.
- Selected AKShare request and metadata.
- Summary tiles for latest close, row count, source, fallback status.
- OHLC table columns: date, open, high, low, close, volume.
- Fetch modal with symbol, asset class, date range, adjust mode, and profile name.
- Secondary spot-only action for creating/updating pricing parameter rows.

### Positions Integration

Add a compact pricing profile selector to portfolio pricing and single-position pricing controls using the same select/input styles already present on the Positions page.

Manual override fields remain available for ad hoc pricing and keep first precedence.

## Error Handling

XLSX import:

- Missing required headers returns HTTP 400 with missing header names.
- Invalid valuation date returns HTTP 400 with a clear message.
- Duplicate trade ids keep the last row, and the import summary reports duplicates.
- Unmatched trade ids are retained and visible.

AKShare:

- Unavailable or empty AKShare data records fallback/source metadata.
- Persist failed or fallback profiles for auditability, matching the current backend behavior around AKShare snapshot metadata.

Pricing:

- Missing pricing profile returns HTTP 404.
- Missing profile row for a trade id appears in the affected position's pricing error when no fallback supplies usable inputs.
- Pricing result payloads identify whether inputs came from manual overrides, pricing profile rows, portfolio market inputs, or AKShare fallback.

## Testing

Backend:

- Global pricing profile XLSX import.
- Pricing profile list/detail.
- Duplicate trade id summary behavior.
- Pricing run with selected profile.
- Pricing input precedence.
- AKShare profile persistence.
- Spot-only feed from market-data profile to pricing parameter rows.
- API errors for missing profiles and invalid imports.

Frontend:

- Pricing Parameters page loading, empty, error, import modal, and row table states.
- Market Data page loading, empty, error, fetch modal, metadata, and OHLC table states.
- Positions pricing request includes `pricing_parameter_profile_id` when selected.
- Existing Positions behavior remains intact.

Browser verification after implementation:

- Both new pages render inside AppShell.
- CSS matches the existing compact route style.
- Import/fetch modals open and submit.
- Pricing profile selector is visible and does not disrupt current Positions flows.

## Scope Boundaries

In scope:

- Global profile persistence.
- XLSX pricing parameter import.
- AKShare profile fetch/persistence.
- Spot-only handoff from AKShare profile to pricing parameters.
- Positions pricing selector integration.
- Repo-consistent management UI.

Out of scope:

- Volatility derivation from historical data.
- Curve/surface management.
- Instrument master data.
- Scheduled market-data jobs.
- User permissions or approval workflows for profile writes.
