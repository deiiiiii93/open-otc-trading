---
name: run-backtest
description: Historical hedging backtest of a portfolio's positions, netted per underlying, replaying daily delta-hedging P&L, greeks, autocallable lifecycle events, and risk metrics over a date range.
domain: risk
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
  - start_date
  - end_date
optional_context:
  - position_ids
  - pricing_parameter_profile_id
  - engine
  - vol_source
  - vol_window
write_actions: true
confirmation_required: true
success_criteria:
  - A backtest run is queued against the named portfolio and replay window
  - Results report total/hedge/product P&L, max drawdown, Sharpe, VaR95, and per-underlying lifecycle events once complete
routing:
  - request: "Historical backtest or hedge replay of a portfolio"
    persona: risk_manager
---

## When to use

Replay history with daily delta-hedging to see hedge P&L, greeks, autocallable
lifecycle events (KO/KI/coupon/autocall), and risk metrics. Not for forward
scenario shocks (run-scenario-test) or a single as-of valuation (run-risk).

## Required inputs

`portfolio_id`, `start_date`, `end_date`. Optional: `position_ids` (scope),
`pricing_parameter_profile_id` (defaults to portfolio's active profile),
`engine` (`quad`|`pde`|`mc`), `vol_source` (`realized`|`flat`), `vol_window`
(rolling days). See `/skills/references/risk/backtest.md` for mechanics.

## Procedure

1. Confirm portfolio and replay window; use page context if present.
2. Confirm write action, then call `run_backtest` (async → `run_id`, `task_id`).
3. Poll `get_backtest_run(run_id)` until `completed` or `failed`.
4. Summarize total/hedge/product P&L, max drawdown, Sharpe, VaR95, and
   per-underlying lifecycle events; link the quant-ark dashboard.

## Stop conditions

- Run failed → surface `results.error`.
- All positions excluded → report `excluded_positions` reasons.
- Very large book or long window → escalate to `desk_async`.

## Output shape

`run_id`, `status`, portfolio totals (P&L, drawdown, Sharpe, VaR95),
`by_underlying` (greeks path, events, trades), excluded-position notes.

## References

- `/skills/references/risk/backtest.md`
- `/skills/references/pricing/engines.md`

## Example

User: Backtest portfolio 3 over Jan–Apr 2024.
Assistant: Confirm portfolio 3 + window 2024-01-02→2024-04-30, confirm write,
call `run_backtest(portfolio_id=3, start_date="2024-01-02", end_date="2024-04-30")`,
poll to completion, summarize P&L/drawdown/Sharpe/VaR95 and KO/coupon events.
