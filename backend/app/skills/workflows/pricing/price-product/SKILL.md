---
name: price-product
description: Price one ad-hoc product spec without portfolio persistence when the user asks exploratory price or value questions. Use when user asks what a product spec would cost without booking or portfolio context, when an RFQ or Snowball workflow needs a one-off model value before persistence, or when page context has enough product terms to run a read-only price.
domain: pricing
workflow_type: action
allowed_envelopes:
  - pet_diagnostic
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - product
optional_context:
  - product_id
  - market
  - engine_name
write_actions: false
confirmation_required: false
success_criteria:
  - price result is returned with engine and key inputs
  - missing product terms or market inputs are named
---

## When to use

- User asks what a product spec would cost without booking or portfolio context.
- RFQ or Snowball workflow needs a one-off model value before persistence.
- Page context has enough product terms to run a read-only price.

## Required inputs

Pass either an existing `product_id` or an inline `product` object (`asset_class`, `product_family`, `quantark_class`, `underlying`, `currency`, `terms`, optional `components`), plus `market` inputs. Read `/skills/references/pricing/engines.md` when engine choice or required inputs are unclear, and call `get_rfq_catalog` for the registered product families and engines.

## Procedure

1. Resolve the product: use `product_id` when it already exists, otherwise assemble the inline `product` object from validated terms.
2. Validate required product terms and market inputs are present.
3. If the engine is path-dependent and path count is unbounded, provide a cost preview first.
4. Call `price_product(product_id=<id>)`, or `price_product(product=<spec>, market=<inputs>, engine_name=<optional>)`.
5. Report price, engine, valuation date, and missing-input caveats.

## Stop conditions

Ask for missing economic terms instead of guessing barriers, tenor, volatility, rates, dividend yield, or observation schedules.

## Output shape

Lead with price or blocked. Include product type, engine, key inputs, valuation date, and caveats.

## References

- `/skills/references/pricing/engines.md`
- `/skills/references/products/snowball-cn.md`

## Example

User: What would a two-year Snowball on CSI 500 with KI 75% and KO 103% price at?
Assistant: Validate terms and market inputs, call `price_product`, then return model value, engine, inputs, and missing assumptions.
