---
name: read-before-compute-policy
description: Read stored quantitative results before proposing compute actions.
policy_type: runtime_policy
applies_to:
  - trader
  - risk_manager
  - async_agent
---

## Read-before-Compute (mandatory order)

For any question about stored quantitative results (price, PnL, market value,
Greeks, NAV, risk, exposure, VaR, hedge feasibility):

1. **READ FIRST** — call the persona's stored-result reader
   (`get_latest_position_valuations` for trader; `get_latest_risk_run` for
   risk_manager; `get_positions` for inventory) before anything else.
2. **INSPECT** — check freshness (`Latest pricing run` / `Latest risk totals`
   line in the context) and completeness (rows present vs. portfolio size).
3. **ANSWER** from stored data when the data covers the question.
4. **PROPOSE, DO NOT RUN** if data is stale or missing. State exactly what's
   missing and offer the persisted action — wait for user confirmation before
   invoking the compute tool (`run_batch_pricing`).

Only call in-memory compute tools (`price_product`, `calculate_risk`) for a
*new ad-hoc spec or supplied snapshot* that is not a persisted position. Never
call the persisted compute tool to answer a question about existing stored
prices/metrics — that is what the stored-result reader is for.
