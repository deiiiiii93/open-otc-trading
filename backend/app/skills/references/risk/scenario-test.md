---
name: scenario-test
description: Predefined, historical, and custom stress scenarios for portfolio scenario tests.
reference_type: risk
---

## Predefined scenarios
- `market_crash` — spot -20%, vol +50%
- `market_rally` — spot +15%, vol -30%
- `vol_spike` / `vol_crush` — vol +80% / vol -40%
- `rate_hike` / `rate_cut` — +/-200bps
- `severe_downturn` — spot -35%, vol +100%, rate -100bps
- `inflation_shock` — rate up, equity down

## Historical scenarios
- `black_monday_1987` — -22.6% equity
- `financial_crisis_2008` — -40% equity, +120% vol
- `covid_crash_2020` — -34% equity, +200% vol

## Custom scenarios
A custom scenario is `{name, stresses: [{param, stress_type, value, level, target}]}`.
- `param`: `spot` | `vol` | `rate` | `dividend`
- `stress_type`: `ABSOLUTE` | `PERCENTAGE` | `VALUE`
- `level`: `portfolio` | `underlying` (target = symbol). `position`-level is not supported in v1.

## Scenario sets (grids)
- **Generate a grid Set:** `generate_scenario_set(name, axes=[{param, start, stop, step, stress_type, level, target?}])`
  builds the cross product of the axes (one scenario per cell) and saves it as a reusable
  Set. Run it with `run_scenario_test(..., scenario_set=name)`.

## Output
Per-scenario P&L and %, worst/best scenario, 95% VaR/CVaR, per-underlying breakdown,
greeks deltas, excluded positions, and report/export artifact links.
