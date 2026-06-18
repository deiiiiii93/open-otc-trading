---
name: strategy
description: Durable hedging-strategy conventions — the four strategies, hard/soft band semantics, and cash-greek definitions for the hedge-portfolio workflow.
reference_type: hedging
---

## Strategies

Per **underlying**, size integer lots of hedge instruments to push greeks into
bands, lexicographically (hard tier first, then soft tier, then smallest total lots).

| Strategy | Hard (must hold) | Soft (minimize violation) |
|---|---|---|
| delta_neutral | delta_cash | - |
| delta_neutral_enhanced | delta_cash | gamma_cash, vega |
| delta_gamma_neutral | delta_cash, gamma_cash | vega |
| full_neutral | delta_cash, gamma_cash, vega | - |

## Conventions

- delta_cash = delta*S, gamma_cash = gamma*S^2/100, vega is raw model units.
- Bands are per-underlying absolute cash widths (one width per greek; a greek is
  hard or soft depending on the strategy). Defaults apply unless overridden.
- Futures/spot move delta only; options are required to move gamma/vega. A
  gamma/vega-hard strategy with no option leg is infeasible.
- Greek targets come from the latest completed risk run; if none exists, run risk
  first. Hedge legs book into the same portfolio, tagged with the risk_run_id.

## Manual tag

- `strategy="manual"` marks desk-sized hedges: the user stated the legs and
  quantities (up front, or by dictating quantities during proposal review).
- Solver strategy names mark MILP-sized hedges. The tag records who sized the
  hedge; both book through `book_hedge` and carry the source risk_run_id.
