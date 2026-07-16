---
name: build-product
description: Construct a quant-ark-validated product from natural-language terms before booking or quoting, guiding the user through any missing economics with an interactive term-collection card. Use when a user states product economics that must become a concrete product, when book-position or draft-rfq needs validated product terms, or when a direct booking has incomplete terms that must be completed before persistence.
domain: products
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - request_text
optional_context:
  - product_family
  - trade_effective_date
write_actions: false
confirmation_required: false
success_criteria:
  - validated product terms and recommended engine are returned
  - missing economics are collected via a term-collection card, never invented
routing:
  - request: "Construct/validate a quant-ark product from terms"
    persona: trader
---

## When to use

- User states product economics that must become a concrete, priceable product.
- `book-position` or `draft-rfq` needs validated product terms and an engine.
- A direct booking has incomplete/invalid terms that must be completed first.

## Required inputs

Request text plus family, underlying, tenor, barriers, frequencies, dates.
See `/skills/references/products/build-contract.md`.

## Procedure

1. Identify the family. Call `get_rfq_catalog` if unclear.
2. Call `get_product_term_schema(family)` for the legal fields, types, required, and
   **enum values**; extract `terms` from the RFQ/context using those exact names/enums
   (per `build-contract.md`) — never guess an enum or omit a required field.
3. Call `build_product(family=<class>, terms=<extracted>)`.
4. If `missing` is non-empty, call `propose_term_form` — one typed field per key with a
   label, one-line `help`, convention `choices` (≤5), and a `default` chip (never silently
   adopted: fetch `fetch_market_snapshot` for `initial_price`, today for `trade_start_date`;
   user confirms). Reply once directing the user to the card.
5. On card response, merge and call `build_product` again; loop until `ok`.
6. Hand validated terms and `engine_name` to the booking or RFQ step.

## Stop conditions

Do not guess initial fixing, lockup, trade start, barrier levels, or coupon —
present them on the card. Do not book or persist from this skill.

## Output shape

Built-or-blocked, then family, engine, validated terms, still-missing (card).

## References

- `/skills/references/products/build-contract.md`

## Example

User: Book a 1Y CSI 500 Snowball, KO 103% monthly, into portfolio 6.
Agent: `build_product` reports `initial_price`/`ki_barrier`/`trade_start_date` missing →
fetch spot, `propose_term_form` (S0, KI 70/75%/None, start today). `ok` → `book-position`.
