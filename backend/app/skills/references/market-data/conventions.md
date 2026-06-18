---
name: conventions
description: Durable market-data source, symbol, staleness, and drift conventions for desk workflows.
reference_type: market_data
---

## Sources

A-share spot data comes from AKShare snapshots for indices, sectors, and single
names. HK spot data uses the available AKShare HK feed and may refresh less
frequently. Volatility surfaces, dividend curves, and pricing assumptions
belong to pricing profiles rather than spot market-data profiles unless a
workflow explicitly fetches or builds them.

## Symbol Conventions

CN index symbols use exchange suffixes such as `000300.SH`, `000905.SH`, and
`000852.SH`. A-share single names use `.SH` for Shanghai listings and `.SZ` for
Shenzhen listings. HK indices use desk symbols such as `HSI` and `HSCEI`
without an exchange suffix.

## Refresh Cadence

Intraday spot values are point-in-time snapshots and require explicit refresh.
A-share day-end values settle after the local market close. Volatility inputs
are normally weekly unless the user requests a rebuild or a risk workflow
requires a fresher pricing profile.

## Staleness And Drift

Spot older than one business day is stale for new pricing decisions. Volatility
older than five business days is stale for structured-product repricing. Spot
drift is material when the absolute percentage move versus stored value is above
1 percent for trader workflows or above 2 percent for risk workflows.
