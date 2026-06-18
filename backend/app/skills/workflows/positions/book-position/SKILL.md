---
name: book-position
description: Book a new position by creating a normalized product and recording it against a portfolio after HITL confirmation. Use when a trader wants to book a structured product directly into a portfolio without an RFQ, when product terms are validated and ready to persist, or when an accepted deal needs a position row created from explicit product terms.
domain: positions
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
  - product
optional_context:
  - quantity
  - entry_price
  - trade_effective_date
write_actions: true
confirmation_required: true
success_criteria:
  - booked position id and product id are returned
  - unsupported product family or missing terms are reported without persistence
routing:
  - request: "Book a product directly into a portfolio from terms"
    persona: trader
---

## When to use

- Trader books a structured product directly into a portfolio without
  an RFQ, from validated terms or an accepted deal.

## Required inputs

Use `portfolio_id` and a `product` object (`product_family` required;
`asset_class` defaults to equity, `currency` to USD; plus `quantark_class`,
`underlying`, `terms`, and optional `components`), `quantity`, and optional
`entry_price`, `status`, `trade_effective_date`, and `engine_name`. Call
`get_rfq_catalog` for valid product families and engines. Read
`/skills/references/pricing/engines.md` when engine choice is unclear.

## Procedure

1. If the product terms are natural-language, first run `build-product` to get
   validated product terms and the recommended `engine_name`. Autocallables
   must use their quad engine (SnowballOption → `SnowballQuadEngine`,
   KnockOutResetSnowballOption → `KOResetSnowballQuadEngine`, PhoenixOption →
   `PhoenixQuadEngine`); never book an autocallable with `BlackScholesEngine`.
2. Validate family support and required terms; if incomplete, run
   `build-product` (`propose_term_form` loop) first.
3. Compose a confirmation summary with portfolio, product, quantity, entry
   price, and engine.
4. After confirmation, call `book_position(portfolio_id=<id>, product=<spec>,
   quantity=<qty>, entry_price=<optional>, engine_name=<recommended>)`.
5. Return the booked position id, product id, and product summary.

## Stop conditions

Do not book an unsupported product family or guess missing economic terms — ask
instead. Never book hedging instruments against book exposure here — use
`hedge-portfolio` (`book_hedge`) or the hedge tag is lost.

## Output shape

Booked or blocked first; then position id, product id, portfolio, family,
quantity, missing terms.

## References

- `/skills/references/pricing/engines.md`

## Example

User: Book 100 lots of a two-year CSI 500 Snowball, KI 75% KO 103%, into portfolio 6.
Assistant: Validate the autocallable terms, summarize for confirmation, then call
`book_position` and report the new position id and product id.
