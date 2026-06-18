---
name: run-greeks-landscape
description: Read or run a persisted portfolio Greeks Landscape across a spot-shift grid. Use when the user asks for Delta/Gamma curves as spot moves, wants the latest Landscape summarized, or requests a new full-portfolio or single-position Landscape calculation.
domain: risk
workflow_type: compound
allowed_envelopes:
  - pet_page
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - greeks_landscape_run_id
  - pricing_parameter_profile_id
  - engine_config_id
  - position_ids
  - spot_min_pct
  - spot_max_pct
  - spot_nodes
write_actions: true
confirmation_required: true
success_criteria:
  - Existing Landscape results are read without queueing a new run
  - New runs preserve the requested portfolio, position scope, pricing profile, engine config, and spot grid
  - Completed results summarize Delta and Gamma curves plus excluded positions
routing:
  - request: "Read or run a portfolio Greeks Landscape"
    persona: risk_manager
---

## When to use

Use for Delta/Gamma curves over spot shifts, not discrete stress-test P&L,
VaR/CVaR, or historical backtests.

## Procedure

1. Resolve `portfolio_id` and optional selected run/profile/engine/positions
   from page context.
2. For a read request, prefer the loaded `greeks_landscape` snapshot. Otherwise
   call `get_greeks_landscape_run(run_id)` or
   `get_latest_greeks_landscape_run(portfolio_id)`.
3. For a new or refreshed calculation, confirm scope and grid, then call
   `run_greeks_landscape`. Defaults are -30% to +30% with 61 nodes.
4. Report queued status and ids. Read the completed run with
   `get_greeks_landscape_run`.
5. Summarize raw or cash Delta/Gamma by portfolio, underlying, or position,
   including excluded positions and valuation timestamp.

## Stop conditions

Ask for the portfolio when missing. Do not queue when the user only asks to
read existing results. The spot range must include zero and keep spots positive.

## Example

User: Run a -20% to +20% Greeks Landscape for this portfolio.
Assistant: Confirm the selected portfolio/profile/engine and 41-node grid,
call `run_greeks_landscape`, then summarize the completed Delta/Gamma curves.
