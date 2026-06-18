# Position Product Refactor Design

**Date:** 2026-05-28
**Status:** Revised draft for user review
**Scope:** Database and workflow refactor for booking the full equity product set used by RFQ/Try Solve, position import, human UI, and agent tools. This includes complex structured products such as Snowball, Phoenix, KO-reset autocallables, sharkfins, range accruals, touch/barrier products, vanilla-style options, futures, stocks, and funds.

---

## Problem

The app currently treats a position as both the trade/holding record and the product definition. `positions` stores `product_type` and the full `product_kwargs` JSON, then newer structured term tables mirror selected fields from that JSON.

That shape is too weak for real booking. Human and agent workflows need to book positions from product definitions that can really define a trade, query product terms without reading large JSON blobs, and stay aligned with QuantArk. QuantArk separates the product object from `EquityPosition`: product classes own terms, while the position owns quantity, entry price, underlying, engine, entry timestamp, id, and cash legs.

The most important products are the complex structured products. Snowball, Phoenix, KO-reset autocallables, sharkfins, range accruals, and touch/barrier products cannot be second-class JSON blobs. Their barriers, coupon schedules, observation dates, protection terms, and lifecycle-relevant states must be relational and agent-queryable.

## Goals

1. Separate product information from position holdings in the database.
2. Include the full stage-one equity product catalog used by Try Solve and RFQ:
   - Autocall / Snowball -> QuantArk `SnowballOption`
   - Phoenix -> QuantArk `PhoenixOption`
   - KO Reset / Knock-Out Autocall -> QuantArk `KnockOutResetSnowballOption`
   - Vanilla / American / Digital / Asian
   - Barrier / Double Barrier / One Touch / Double No Touch / Double One Touch
   - Range Accrual
   - Single Sharkfin / Double Sharkfin
   - Vertical Spread / Call Put Portfolio / Binary Convex / Ladder Binary as product packages over QuantArk-supported component products
   - Futures / forwards
   - Stocks and funds as QuantArk spot instruments
3. Follow QuantArk's equity product design:
   - product classes own constructor terms,
   - schedules/config objects are product terms,
   - `EquityPosition` owns holding/trade data.
4. Preserve current behavior for existing imports, RFQ booking, pricing, risk, portfolio views, and agent tools during migration.
5. Make human and agent booking paths use the same backend booking service.
6. Keep compatibility fields in API responses while moving canonical product data out of `positions`.

## Non-Goals

- Do not change QuantArk itself.
- Do not remove legacy `positions.product_type` or `positions.product_kwargs` immediately; treat them as compatibility and migration fields until callers are moved.
- Do not deduplicate all historical products aggressively in the first migration.
- Do not build full fund accounting beyond QuantArk ETF-style spot exposure.
- Do not solve currently unsupported pricing models by schema work alone. Product packages can be represented and booked, but pricing readiness still depends on QuantArk/component support.

## QuantArk Alignment

QuantArk reference points:

- `/Users/fuxinyao/quant-ark/asset/equity/product/base_equity_product.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/base_equity_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/snowball_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/phoenix_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/ko_reset_snowball_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/snowball_config.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/phoenix_config.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/observation_schedule.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/barrier_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/double_barrier_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/one_touch_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/double_one_touch_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/asian_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/range_accrual_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/single_sharkfin_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/option/double_sharkfin_option.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/deltaone/spot_instrument.py`
- `/Users/fuxinyao/quant-ark/asset/equity/product/deltaone/futures.py`
- `/Users/fuxinyao/quant-ark/portfolio/equity/position.py`

The Open OTC database model should mirror the same boundary:

- Product row: class identity and constructor terms.
- Product detail rows: QuantArk family-specific config, barrier, coupon, schedule, participation, and payoff terms.
- Position row: portfolio membership, trade id, quantity, entry price, status, booking provenance, engine selection, and lifecycle state.
- Cash legs: position-attached trade economics, not product identity. Cash legs can be added later as a separate table; this design leaves a clean place for them without implementing them in this stage.

## Product Coverage

The product catalog must cover every product currently surfaced by `backend/app/services/try_solve_registry.py` and the RFQ templates.

| Desk product | Product representation | QuantArk alignment |
|---|---|---|
| Autocall | single product | `SnowballOption` |
| Phoenix | single product | `PhoenixOption` |
| Knock-Out Autocall | single product | `KnockOutResetSnowballOption` |
| Vanilla | single product | `EuropeanVanillaOption` |
| American | single product | `AmericanOption` |
| Digital | single product | `CashOrNothingDigitalOption` |
| Asian | single product + observations | `AsianOption` |
| Barrier | single product | `BarrierOption` |
| Double Barrier | single product | `DoubleBarrierOption` |
| One Touch | single product | `OneTouchOption` |
| Double No Touch | single product | `DoubleOneTouchOption` using no-touch semantics in raw terms |
| Double One Touch | single product | `DoubleOneTouchOption` |
| Range Accrual | single product + observations | `RangeAccrualOption` |
| Single Sharkfin | single product | `SingleSharkfinOption` |
| Double Sharkfin | single product | `DoubleSharkfinOption` |
| Forward | futures-style delta-one product | `Futures` / `DeltaOneEngine` |
| Futures | single product | `Futures` |
| Stock | spot product | `SpotInstrument(deltaone_type=STOCK)` |
| Fund | spot product | `SpotInstrument(deltaone_type=ETF)` |
| Vertical Spread | product package | components are vanilla option products |
| Call Put Portfolio | product package | components are vanilla option products |
| Binary Convex | product package | components are digital/vanilla option products, depending on mapper support |
| Ladder Binary | product package | components are digital option products |

Product packages are first-class products with child components. They are not stored as opaque strategy JSON. Each component points to another product row and carries a weight/quantity/role.

## Architecture Decision

### Chosen: Product Root Table + Family Detail Tables

Create a normalized `products` table plus QuantArk-aligned family tables. Positions reference `products.id`. Structured products get dedicated product-linked detail tables.

Why this is the chosen approach:
- Matches QuantArk's product/position boundary.
- Gives SQL-queryable product terms for agents, risk, and lifecycle workflows.
- Makes Snowball/Phoenix schedules and barriers first-class data.
- Allows staged compatibility with existing `positions.product_kwargs`.
- Supports composite products through product components without inventing non-QuantArk monoliths.

Known implementation cost:
- Requires a migration and mapper layer.
- Existing code needs adapter helpers while old and new fields coexist.

### Rejected: Keep Product JSON but Move It to `products`

Create `products(id, product_type, product_kwargs)` and point positions at it, without family tables.

Rejected because:
- Does not materially improve agent/query contracts.
- Recreates the current JSON problem one table away.
- Fails the requirement to create tables according to QuantArk's product design.
- Leaves Snowball/Phoenix unusable for SQL-grade barrier/coupon queries.

Tradeoff:
- Smallest migration.
- Easy to keep current pricing code running.

### Rejected: One Wide `products` Table

Put every option, futures, stock, fund, autocallable, barrier, coupon, and schedule field into one table.

Rejected because:
- Many nullable, product-specific columns.
- Still needs child tables for schedules.
- Hard to validate and extend.
- Drifts from QuantArk's product-family class structure.

Tradeoff:
- Simple joins for scalar fields.
- Easy to browse in SQLite.

## Data Model

### `products`

One row per canonical product definition.

```sql
products (
    id                    INTEGER PRIMARY KEY,
    asset_class           VARCHAR(40) NOT NULL DEFAULT 'equity',
    product_family        VARCHAR(40) NOT NULL,
    quantark_class        VARCHAR(120),
    display_name          VARCHAR(160),
    underlying            VARCHAR(80) NOT NULL,
    currency              VARCHAR(8) NOT NULL DEFAULT 'USD',
    term_hash             VARCHAR(80) NOT NULL,
    raw_terms             JSON NOT NULL DEFAULT '{}',
    source_payload        JSON,
    created_at            DATETIME NOT NULL,
    updated_at            DATETIME NOT NULL
)
```

`product_family` values for this stage:

- `option`
- `autocallable`
- `barrier`
- `touch`
- `asian`
- `range_accrual`
- `sharkfin`
- `spot`
- `futures`
- `package`

Indexes:

- `ix_products_asset_family` on `(asset_class, product_family)`.
- `ix_products_underlying` on `underlying`.
- `ix_products_quantark_class` on `quantark_class`.
- `ix_products_term_hash` on `term_hash`.

`term_hash` is deterministic over `asset_class`, `product_family`, `quantark_class`, `underlying`, `currency`, and normalized `raw_terms`. It supports exact product reuse, but the migration should not merge legacy rows unless reuse is explicitly safe.

`raw_terms` stores the complete normalized QuantArk constructor snapshot for audit and fallback fidelity. It is not the primary query contract for product terms that have relational columns.

### `equity_option_products`

One row for QuantArk `BaseEquityOption`-style scalar terms.

```sql
equity_option_products (
    product_id              INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    strike                  FLOAT,
    option_type             VARCHAR(8),
    exercise_type           VARCHAR(16),
    maturity                FLOAT,
    exercise_date           DATE,
    settlement_date         DATE,
    maturity_date           DATE,
    tenor                   FLOAT,
    tenor_end               VARCHAR(40),
    annualization_day_count VARCHAR(40),
    initial_price           FLOAT,
    contract_multiplier     FLOAT NOT NULL DEFAULT 1.0
)
```

This table is shared by vanilla, American, digital, Asian, barrier, sharkfin, Snowball/Phoenix, and any structured product that inherits option-style scalar terms. Product-specific tables below add the detail that QuantArk models through config classes or specialized constructors.

### `equity_autocallable_products`

One row for Snowball, Phoenix, and KO-reset autocallable core terms.

```sql
equity_autocallable_products (
    product_id                    INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    autocallable_kind             VARCHAR(40) NOT NULL,  -- snowball | phoenix | ko_reset
    is_reverse                    BOOLEAN NOT NULL DEFAULT 0,
    initial_price                 FLOAT NOT NULL,
    strike                        FLOAT NOT NULL,
    contract_multiplier           FLOAT NOT NULL DEFAULT 1.0,
    ko_observation_type           VARCHAR(24),
    ki_observation_type           VARCHAR(24),
    ki_continuous                 BOOLEAN NOT NULL DEFAULT 0,
    disable_ko_after_ki           BOOLEAN NOT NULL DEFAULT 0,
    payoff_rebate_rate            FLOAT,
    payoff_call_rebate_enabled    BOOLEAN NOT NULL DEFAULT 0,
    payoff_call_strike            FLOAT,
    payoff_call_participation_rate FLOAT,
    payoff_include_principal      BOOLEAN NOT NULL DEFAULT 1,
    payoff_participation_rate     FLOAT,
    payoff_protection_type        VARCHAR(24),
    payoff_protection_rate        FLOAT,
    accrual_coupon_pay_type       VARCHAR(24),
    accrual_is_annualized         BOOLEAN NOT NULL DEFAULT 1,
    accrual_is_annualized_ko      BOOLEAN,
    accrual_is_annualized_ki      BOOLEAN,
    accrual_is_annualized_rebate  BOOLEAN,
    reset_rate                    FLOAT
)
```

This table corresponds to QuantArk `SnowballOption`, `PhoenixOption`, `KnockOutResetSnowballOption`, `BarrierConfig`, `PayoffConfig`, and `AccrualConfig`.

### `equity_autocallable_observations`

Observation schedules for Snowball/Phoenix/KO-reset products.

```sql
equity_autocallable_observations (
    id                    INTEGER PRIMARY KEY,
    product_id            INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    observation_role      VARCHAR(24) NOT NULL,  -- ko | ki | coupon | accrual
    sequence              INTEGER NOT NULL,
    observation_date      DATE,
    observation_time      FLOAT,
    barrier_level         FLOAT,
    rate                  FLOAT,
    accrual_factor        FLOAT,
    aggregation           VARCHAR(24),
    weight                FLOAT,
    source_payload        JSON,
    UNIQUE(product_id, observation_role, sequence)
)
```

Agents and risk tools should use this table for questions like upcoming KO dates, nearest KI barrier, coupon observation windows, and schedule consistency checks.

### `equity_phoenix_coupon_products`

Phoenix-specific coupon barrier terms.

```sql
equity_phoenix_coupon_products (
    product_id                  INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    coupon_barrier              FLOAT NOT NULL,
    coupon_rate                 FLOAT NOT NULL,
    coupon_pay_type             VARCHAR(24),
    day_count_convention        VARCHAR(40),
    memory_coupon               BOOLEAN NOT NULL DEFAULT 1,
    fixed_coupon_year_fraction  FLOAT
)
```

Time-varying coupon barriers are stored in `equity_autocallable_observations` with `observation_role='coupon'`.

### `equity_barrier_products`

Barrier products with one or two barriers.

```sql
equity_barrier_products (
    product_id          INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    barrier_kind        VARCHAR(32) NOT NULL,  -- single | double
    barrier             FLOAT,
    barrier_type        VARCHAR(32),
    upper_barrier       FLOAT,
    lower_barrier       FLOAT,
    rebate              FLOAT,
    monitoring_type     VARCHAR(24)
)
```

This table covers `BarrierOption` and `DoubleBarrierOption`.

### `equity_touch_products`

Touch/no-touch products.

```sql
equity_touch_products (
    product_id          INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    touch_kind          VARCHAR(32) NOT NULL,  -- one_touch | double_one_touch | double_no_touch
    barrier             FLOAT,
    upper_barrier       FLOAT,
    lower_barrier       FLOAT,
    touch_type          VARCHAR(32),
    payout              FLOAT,
    rebate              FLOAT,
    monitoring_type     VARCHAR(24)
)
```

This table covers `OneTouchOption` and `DoubleOneTouchOption`, including double-no-touch desk semantics through `touch_kind`.

### `equity_asian_products` and `equity_asian_observations`

```sql
equity_asian_products (
    product_id          INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    averaging_method    VARCHAR(24),
    averaging_kind      VARCHAR(24),
    n_observations      INTEGER
)

equity_asian_observations (
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sequence            INTEGER NOT NULL,
    observation_date    DATE,
    observation_time    FLOAT,
    observed_price      FLOAT,
    weight              FLOAT,
    PRIMARY KEY(product_id, sequence)
)
```

### `equity_range_accrual_products` and `equity_range_accrual_observations`

```sql
equity_range_accrual_products (
    product_id          INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    lower_barrier       FLOAT NOT NULL,
    upper_barrier       FLOAT NOT NULL,
    accrual_rate        FLOAT NOT NULL,
    observation_type    VARCHAR(24),
    day_count_convention VARCHAR(40)
)

equity_range_accrual_observations (
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    sequence            INTEGER NOT NULL,
    observation_date    DATE,
    observation_time    FLOAT,
    lower_barrier       FLOAT,
    upper_barrier       FLOAT,
    weight              FLOAT,
    PRIMARY KEY(product_id, sequence)
)
```

### `equity_sharkfin_products`

```sql
equity_sharkfin_products (
    product_id           INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    sharkfin_kind        VARCHAR(16) NOT NULL,  -- single | double
    strike               FLOAT,
    barrier              FLOAT,
    upper_barrier        FLOAT,
    lower_barrier        FLOAT,
    option_type          VARCHAR(8),
    participation_rate   FLOAT,
    coupon               FLOAT,
    rebate               FLOAT,
    observation_type     VARCHAR(24)
)
```

This table covers `SingleSharkfinOption` and `DoubleSharkfinOption`.

### `equity_spot_products`

One row for QuantArk `SpotInstrument` terms.

```sql
equity_spot_products (
    product_id            INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    deltaone_type         VARCHAR(16) NOT NULL,  -- STOCK | ETF | INDEX
    instrument_code       VARCHAR(80) NOT NULL,
    exchange              VARCHAR(40),
    contract_multiplier   FLOAT NOT NULL DEFAULT 1.0
)
```

Stage mapping:

- Stock booking: `quantark_class='SpotInstrument'`, `deltaone_type='STOCK'`.
- Fund booking: `quantark_class='SpotInstrument'`, `deltaone_type='ETF'`.

### `equity_futures_products`

One row for QuantArk `Futures` terms.

```sql
equity_futures_products (
    product_id          INTEGER PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    contract_code       VARCHAR(80) NOT NULL,
    multiplier          FLOAT NOT NULL DEFAULT 1.0,
    maturity            FLOAT,
    maturity_date       DATE,
    basis               FLOAT NOT NULL DEFAULT 0.0,
    basis_decay_rate    FLOAT NOT NULL DEFAULT 1.0,
    market_price        FLOAT
)
```

### `equity_product_components`

Product packages store child product composition here.

```sql
equity_product_components (
    id                    INTEGER PRIMARY KEY,
    parent_product_id     INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    component_product_id  INTEGER NOT NULL REFERENCES products(id),
    component_role        VARCHAR(40) NOT NULL,  -- long_leg | short_leg | digital_leg | hedge_leg
    quantity              FLOAT NOT NULL DEFAULT 1.0,
    weight                FLOAT NOT NULL DEFAULT 1.0,
    sequence              INTEGER NOT NULL,
    source_payload        JSON,
    UNIQUE(parent_product_id, sequence)
)
```

This supports vertical spreads, call-put portfolios, binary convex structures, and ladder binaries without flattening them into a custom JSON-only product.

### `positions`

Positions become holdings/trade records.

Add:

```sql
product_id INTEGER REFERENCES products(id)
```

Keep canonical on `positions`:

- `portfolio_id`
- `product_id`
- `quantity`
- `entry_price`
- `status`
- `source_trade_id`
- `source_row`
- `mapping_status`
- `mapping_error`
- `source_payload`
- `rfq_id`
- `rfq_quote_version_id`
- `trade_effective_date`
- `engine_name`
- `engine_kwargs`
- `version`
- timestamps

Keep temporary compatibility fields:

- `underlying`
- `product_type`
- `product_kwargs`

Compatibility fields are populated from the product at write time and returned in old API shapes until frontend, tools, and pricing are fully moved.

Position-linked lifecycle state remains position-linked. Product-linked tables define original contract terms; lifecycle events and state overrides describe what happened to a booked holding.

## Existing Structured-Term Tables

The repo already has position-linked mirrors such as `option_core_terms`, `snowball_terms`, `snowball_ko_schedule`, and `position_barrier_state`. During migration:

1. Keep those tables working for existing tools.
2. Treat them as compatibility mirrors derived from product-linked tables and position lifecycle state.
3. Refresh them from the booking service until all tools can query product-linked tables directly.
4. Do not make new product truth depend on `positions.product_kwargs`.

## Backend Services

### Product Mapper

Add a product-domain service responsible for:

- normalizing QuantArk product terms,
- computing deterministic `term_hash`,
- creating or reusing product rows,
- writing the correct family detail rows,
- writing product component rows for product packages,
- hydrating compatibility fields from product rows,
- reconstructing QuantArk constructor payloads for pricing and RFQ validation.

Suggested module:

- `backend/app/services/domains/products.py`

Core functions:

```python
def create_or_get_product(session, spec) -> Product: ...
def product_spec_from_position_payload(payload) -> ProductSpec: ...
def product_spec_from_executable_terms(terms) -> ProductSpec: ...
def compatibility_terms(product) -> dict[str, Any]: ...
def hydrate_position_product_fields(position) -> None: ...
```

The service should be the only write path for product rows.

### Booking Service

Add one booking service used by human UI, RFQ booking, import, and agent tools.

Suggested module:

- `backend/app/services/domains/booking.py`

Core responsibility:

1. Validate the target portfolio is a container portfolio.
2. Create or reuse the product.
3. Create the position with `product_id`.
4. Populate legacy compatibility fields.
5. Upsert position-linked compatibility mirrors needed by existing tools.
6. Refresh barrier state when applicable.
7. Record an audit event.

Existing direct `Position(...)` creation paths should move behind this service over time.

### Product Query Service

Add structured product query helpers for agent and UI use:

- find products by QuantArk class, family, underlying, or term hash,
- retrieve full normalized product terms,
- query Snowball/Phoenix KO/KI/coupon schedules,
- query products with upcoming observations,
- query product packages and component legs.

These helpers should replace ad hoc inspection of `positions.product_kwargs`.

## Human and Agent Workflows

Human and agent booking must converge on the same backend contract. The UI and tools may collect inputs differently, but both submit a booking payload shaped around:

```text
product:
  asset_class
  product_family
  quantark_class
  underlying
  currency
  terms
  components
position:
  portfolio_id
  quantity
  entry_price
  status
  source_trade_id
  trade_effective_date
  engine_name
  engine_kwargs
provenance:
  source
  rfq_id
  rfq_quote_version_id
  source_payload
```

Agent-facing tools should not ask the model to write raw SQL or directly mutate both product and position tables. They should call deterministic booking/query helpers with validated Pydantic schemas.

## API Compatibility

`PositionOut` should continue to return:

- `underlying`
- `product_type`
- `product_kwargs`
- `engine_name`
- `engine_kwargs`

Add:

- `product_id`
- `product`

Old callers can keep working, while new callers use `product`.

For create/patch, accept both shapes during transition:

- legacy: `PortfolioPositionSpec` with `product_type` and `product_kwargs`,
- new: `PortfolioPositionSpec` with `product` object.

If both are supplied, `product` wins and legacy fields are derived from it.

## Migration Plan

1. Create product tables, component tables, observation tables, and `positions.product_id`.
2. For every existing position:
   - build a product spec from `underlying`, `product_type`, and `product_kwargs`,
   - insert one product row and all matching family rows,
   - insert observation rows for Snowball/Phoenix/Asian/range-accrual schedules,
   - insert component rows when the legacy/product mapper identifies a package,
   - set `positions.product_id`,
   - leave legacy fields unchanged.
3. Add a non-null enforcement only after all write paths have moved behind the booking/product service.
4. Keep `kwargs_migrated_at` and existing structured term mirrors working during the transition.

The initial migration should preserve one product per existing position unless an exact product reuse path is explicitly invoked by new booking code. This avoids accidental historical position merging.

## Frontend

The frontend should keep the existing compact desk style.

Stage-one UI changes:

- Position create/edit forms expose a product picker/editor backed by the new product spec shape.
- Snowball/Phoenix/autocallable editors must expose barriers, coupon/accrual/protection terms, and schedules as structured controls, not only raw JSON.
- Product packages expose component legs in a compact table.
- Existing product-term JSON editor remains available as an escape hatch.
- Position detail shows product information as a distinct read-only/product tab area, while position quantity, status, entry price, and lifecycle fields remain position-level.
- RFQ booking display continues to show executable terms, but final booking submits through the shared booking service.

## Testing

Backend coverage:

- Migration backfills products and `positions.product_id` from legacy positions.
- Migration backfills Snowball/Phoenix schedules into `equity_autocallable_observations`.
- Legacy create endpoint still returns the same `PositionOut` compatibility fields.
- New create path books Snowball, Phoenix, KO-reset Snowball, range accrual, sharkfin, touch, barrier, vanilla, stock, fund, and futures products.
- New create path books product packages with child components.
- RFQ booking creates a product row and position row from executable terms.
- Try Solve solved rows can promote into the same booking service.
- Agent booking tool calls the same service as human booking.
- Structured product query helpers answer near-barrier and upcoming-observation queries without reading `positions.product_kwargs`.
- Pricing/risk still work against migrated positions.

Frontend coverage:

- Position create/edit handles Snowball, Phoenix, KO-reset, range accrual, sharkfin, touch/barrier, vanilla, stock, fund, futures, and package specs.
- Position detail renders product fields separately from position fields.
- Existing positions page still renders legacy rows after migration.
- RFQ/Try Solve booking paths show executable product terms and submit through the shared booking service.

Smoke checks:

- `alembic upgrade head`
- targeted backend tests around products, booking, RFQ, Try Solve, pricing, and position tools
- frontend build and focused position/RFQ/Try Solve UI tests

## Rollout

1. Land schema and migration with compatibility fields intact.
2. Add product and booking service.
3. Move RFQ booking to booking service because executable terms are the cleanest product source.
4. Move Try Solve promotion/booking to booking service.
5. Move human position create/edit to booking service.
6. Move import path to product creation plus position booking.
7. Add agent booking/query tools over the new contract.
8. Move near-barrier/upcoming-observation tools from position-linked mirrors to product-linked tables.
9. Keep old fields as read-compatible until all consumers are converted.

## Open Decisions Resolved

- Funds are modeled as QuantArk `SpotInstrument` with `deltaone_type=ETF`.
- Stage one includes the full RFQ/Try Solve structured product catalog, not only vanilla options, futures, stocks, and funds.
- Snowball, Phoenix, KO-reset autocallables, and their schedules are first-class product-linked relational data.
- The architecture is Product Root Table + Family Detail Tables.
- Legacy `positions.product_kwargs` remains during transition but stops being canonical.
- Product deduplication is opt-in for new booking; migration preserves historical rows one-for-one.
