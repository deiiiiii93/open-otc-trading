---
name: run-scenario-test
description: Run a portfolio stress / scenario test using the QuantArk stresstest engine. Use when the user asks to stress test a portfolio, run market-crash / vol-spike / rate-hike / historical scenarios, build a custom multi-parameter scenario, or see worst-case P&L, VaR/CVaR, or greeks under stressed markets.
domain: risk
workflow_type: compound
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
  - pricing_parameter_profile_id
optional_context:
  - position_ids
  - scenario_names
  - custom_scenarios
  - scenario_set
write_actions: true
confirmation_required: true
success_criteria:
  - A scenario test run is queued against the named portfolio and pricing profile
  - Results report per-scenario P&L, worst/best, and VaR/CVaR once complete
routing:
  - request: "Stress test or scenario analysis of a portfolio"
    persona: risk_manager
---

## When to use

- User asks to stress test / scenario-test a portfolio, or run market-crash,
  vol-spike, rate-hike, severe-downturn, or historical (1987 / 2008 / COVID) scenarios.
- User wants worst-case P&L, VaR/CVaR, or greeks under stressed markets.
- User describes a custom multi-parameter shock (e.g. "spot -20% and vol +50%").

## Required inputs

`portfolio_id` and `pricing_parameter_profile_id` (the profile supplies the baseline market
the scenarios stress). Scenarios come from predefined names, custom specs, or a saved set.
See `/skills/references/risk/scenario-test.md` for taxonomy and `/skills/references/pricing/engines.md`
for pricing caveats.

## Procedure

1. Confirm the portfolio and pricing parameter profile (use page context when present).
2. Resolve scenarios: `list_scenario_library` for predefined/saved names; for a custom
   shock, build a `custom_scenarios` spec (param spot/vol/rate/dividend, stress_type,
   value, level, target).
3. Confirm with the user (write action), then call `run_scenario_test`.
4. Report the queued run id; when complete, read with `get_scenario_test_run` and
   summarize per-scenario P&L, worst/best, VaR/CVaR, excluded positions, artifacts.

## Stop conditions

Do not invent stress magnitudes the user did not ask for. If the portfolio resolves to no
includable positions, report that instead of queuing. Escalate to `desk_async` for very
large books.

## Output shape

Queued run id + scope; once complete, baseline value, per-scenario P&L / %, worst & best
scenario, 95% VaR/CVaR, per-underlying highlights, excluded-position notes, and
report/export download links.

## References

- `/skills/references/risk/scenario-test.md`
- `/skills/references/pricing/engines.md`

## Example

User: Stress test portfolio 7 against a market crash and COVID, using the EOD profile.
Assistant: Confirm portfolio 7 + EOD profile, resolve the two predefined scenarios,
confirm the write, call `run_scenario_test`, then summarize worst-case P&L and VaR when done.
