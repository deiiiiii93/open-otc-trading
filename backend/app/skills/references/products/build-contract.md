---
name: build-contract
description: Per-family structured term schema for build_product. Lists required vs synthesizable terms and the recommended engine per quant-ark product family.
reference_type: product
---

## How to use

The `build_product(family, terms)` tool constructs a quant-ark-validated product
from structured `terms`. Extract terms from the user's request; do NOT invent
economics. If `build_product` returns a non-empty `missing` list, ask the user
for exactly those fields in one message, then call `build_product` again.

When the build succeeds (`ok` is true), the result also carries `product_spec`
with product identity fields (`product_family`, `underlying`, `currency`, `terms`);
`product_spec` is absent (None) on any failed build.

Barrier/strike levels accept either an absolute key (`ko_barrier`) or a
percent-of-initial key (`ko_barrier_pct`, e.g. `101` for 101%) — percents are
resolved against `initial_price`.

`initial_price` (the initial fixing S0) is **required for every family** and is
never invented. If it is absent, `build_product` returns `initial_price` in
`missing`. Do not guess it: call `fetch_market_snapshot` for the underlying and
propose the latest spot as the suggested S0, then ask the user to confirm or
override before calling `build_product` again.

## Autocallables (KO/KI observation schedules are synthesized)

| Family | Engine |
|---|---|
| `SnowballOption` | `SnowballQuadEngine` |
| `KnockOutResetSnowballOption` | `KOResetSnowballQuadEngine` |
| `PhoenixOption` | `PhoenixQuadEngine` |

**SnowballOption** — required: `maturity_years`, `ko_barrier`|`ko_barrier_pct`,
`ki_barrier`|`ki_barrier_pct` (unless `ki_convention: "NONE"`), `ko_rate`
(coupon), `lockup_months`, `trade_start_date` (ISO `YYYY-MM-DD`),
`observation_frequency` (`MONTHLY` | `QUARTERLY` | `SEMI_ANNUAL` | `CUSTOM`).
When `CUSTOM`, `ko_observation_dates` (list of ISO date strings) is also
required.
Optional: `strike` (default = initial fixing),
`ki_convention` (`DAILY` default | `EUROPEAN` | `NONE`), `ko_rate_annualized`.
Synthesized from the above: a KO observation schedule at the requested
frequency (from `trade_start_date` + `lockup_months` on the desk's exchange
calendar - a desk default, configurable) and
a daily (or European) KI observation schedule.

**KnockOutResetSnowballOption** — all SnowballOption terms PLUS required
`post_ko_barrier`|`post_ko_barrier_pct` and `post_ko_rate` (the post-KI reset
KO-only schedule).

**PhoenixOption** — SnowballOption barriers/schedule PLUS required `coupon_rate`
and `coupon_barrier`|`coupon_barrier_pct`. `ko_rate` is optional here (defaults
to 0 because coupons accrue via the coupon leg, not the KO leg).

## Scalar option / instrument families

| Family | Engine | Required terms |
|---|---|---|
| `EuropeanVanillaOption` | `BlackScholesEngine` | `strike`, `maturity_years` (+ `option_type` CALL/PUT) |
| `AmericanOption` | `AmericanOptionAnalyticalEngine` | `strike`, `maturity_years` (+ `option_type`) |
| `CashOrNothingDigitalOption` | `DigitalOptionAnalyticalEngine` | `strike`, `cash_payoff`, `maturity_years` (+ `option_type`) |
| `BarrierOption` | `BarrierAnalyticalEngine` | `strike`, `barrier`, `maturity_years`; optional `barrier_type` (default `DOWN_OUT`), `rebate` |
| `OneTouchOption` | `OneTouchAnalyticalEngine` | `barrier`, `cash_payoff`, `maturity_years`; optional `barrier_direction` (`UP` default/`DOWN`), `touch_type` (`ONE_TOUCH` default/`NO_TOUCH`) |
| `DoubleOneTouchOption` | `OneTouchAnalyticalEngine` | `upper_barrier`, `lower_barrier`, `cash_payoff`, `maturity_years`; optional `touch_type` (`DOUBLE_ONE_TOUCH` default/`DOUBLE_NO_TOUCH`) |
| `Futures` | `DeltaOneEngine` | `underlying`, `maturity_years`; optional `contract_multiplier` |
| `SpotInstrument` | `DeltaOneEngine` | `underlying`; optional `deltaone_type` (`INDEX` default/`STOCK`/`ETF`/`FUTURES`) |

## Averaging / barrier-window families

| Family | Engine | Required terms |
|---|---|---|
| `AsianOption` | `AsianOptionAnalyticalEngine` | `strike`, `maturity_years`; optional `averaging_frequency` (`MONTHLY` default/`DAILY`) → observation count, `option_type` |
| `SingleSharkfinOption` | `SingleSharkfinOptionAnalyticalEngine` | `strike`, `barrier`, `maturity_years`; optional `participation_rate` (default 1.0), `option_type` |
| `DoubleSharkfinOption` | `DoubleSharkfinOptionAnalyticalEngine` | `strike`, `lower_barrier`, `upper_barrier`, `maturity_years`; optional `participation_rate`, `option_type` |
| `RangeAccrualOption` | `RangeAccrualAnalyticalEngine` | `maturity_years`, `lower_barrier`|`lower_barrier_pct`, `upper_barrier`|`upper_barrier_pct`, `accrual_rate`; optional `observation_frequency` (`DAILY` default/`MONTHLY`) |

## Already-built termsheets (OTC import)

Channels that ingest *existing* trades — notably the OTC position import — supply a
complete QuantArk termsheet with explicit observation dates and per-date
barrier/rate schedules (e.g. a step-down snowball). These are validated and wrapped
verbatim via `build_product(..., prebuilt=True)` — never re-synthesized — because a
uniform periodic schedule cannot express a step-down. The single
producer/validation gate still applies (the same gate manual and RFQ bookings use);
only the *input* is already complete, so the per-channel import adapter keeps its
own schedule helpers.
