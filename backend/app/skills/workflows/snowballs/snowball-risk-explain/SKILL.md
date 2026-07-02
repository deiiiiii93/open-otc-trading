---
name: snowball-risk-explain
description: Explain CN Snowball KI/KO proximity, gamma risk, hedge feasibility, and latest stored risk context. Use when user asks about Snowball risk, hedge feasibility, gamma near KI, or autocall exposure, when a risk manager needs product-specific explanation of stored risk metrics, or when a Snowball book audit needs positions near KI or KO explained.
domain: snowballs
workflow_type: diagnostic
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - position_id_or_portfolio_id
optional_context:
  - portfolio_id
  - risk_run_id
  - market_snapshot
write_actions: false
confirmation_required: false
success_criteria:
  - KI/KO proximity and risk drivers are stated
  - stale or missing risk runs are identified
routing:
  - request: "Snowball risk, hedge feasibility, gamma near KI"
    persona: risk_manager
---

## When to use

- User asks about Snowball risk, hedge feasibility, gamma near KI, or autocall exposure.
- Risk manager needs product-specific explanation of stored risk metrics.
- Snowball book audit needs positions near KI or KO explained.

## Required inputs

Use `position_id` for single-position questions and `portfolio_id` for book-wide scans. If the user gave a portfolio name, resolve it with `list_portfolios` first. Call `get_product_reference_doc` with `SnowballOption` for KI/KO conventions.

## Procedure

1. For a book-wide "KO % From Spot", near KO, or autocall proximity scan, call `query_snowball_ko_from_spot` with the resolved `portfolio_id` and `within_pct` instead of pulling full product terms position-by-position.
2. For a single position, apply `snowball-term-interpretation` to identify KI, KO, lifecycle, and observation schedule.
3. Read latest risk through `read-risk-result` when portfolio context is available.
4. Compare spot to KI and next KO levels and explain gamma or autocall risk.
5. If risk is missing or stale, recommend `run-risk` rather than recalculating inline.
6. If the desk wants to act on a hedging suggestion (book the recommended
   instruments), hand off to `hedge-portfolio` — `book_hedge` (HITL) books
   hedge-tagged legs; never `book-position`.

## Stop conditions

Do not propose hedges without risk metrics or stated assumptions. Escalate to `desk_async` for book-wide Snowball risk scans.

## Output shape

Return risk verdict, KI/KO proximity, latest risk freshness, hedge caveats, and
recommended next workflow (`hedge-portfolio` for actionable hedges).

## References

- `get_product_reference_doc(SnowballOption)`

## Example

User: Is position 42 dangerous near KI?
Assistant: Interpret KI/KO terms, compare spot proximity, read latest risk if available, and explain gamma and hedge caveats.
