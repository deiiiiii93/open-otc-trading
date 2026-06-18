---
name: backtest
description: Engines, hedge mechanics, autocallable lifecycle, outputs, and market-data sourcing for historical hedging backtests.
reference_type: risk
---

## Engines

- `quad` (default) — quadrature pricer; fastest, adequate for vanilla and barrier.
- `pde` — finite-difference PDE; slower, recommended for path-dependent features.
- `mc` — Monte Carlo; slowest, most accurate for complex autocallables.

## Hedge mechanics

Daily delta-hedging is executed per underlying, netted across all positions in
that underlying. Hedge instrument is index futures with quarterly roll (front
contract until five business days before expiry, then roll to the next). If no
listed future exists for the underlying, the engine falls back to spot hedging.
Transaction costs use the profile's `hedge_cost_bps` parameter.

## Autocallable lifecycle events

The engine detects and records per-observation-date events:
- `ko` — knock-out triggered; position exits, final coupon paid.
- `ki` — knock-in triggered; barrier protection removed.
- `autocall` — early redemption at par plus coupon.
- `coupon` — regular coupon paid (whether or not KI occurred).
Positions that exit early are excluded from subsequent daily greeks and trades.

## Outputs

**Portfolio totals:** total P&L, hedge P&L, product P&L, number of hedge trades,
max drawdown, annualized Sharpe ratio, 95% VaR.

**By-underlying breakdown:** daily delta path, cumulative greeks, list of
lifecycle events with dates and values, list of hedge trade timestamps and sizes.

**Excluded positions:** positions that could not be replayed (missing market
data, unsupported payoff) are listed with reasons.

**Artifacts:** quant-ark interactive dashboard URL + CSV export of daily P&L.

## Market data sourcing

The backtest reads historical closes from the `MarketDataProfile` snapshots stored
at each date; gaps are backfilled from akshare (A-share underlyings) or the
SSE/SZSE trading calendar. Dates outside the SSE calendar are skipped silently.
