---
name: sharkfin
description: Single and double sharkfin option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - SingleSharkfinOption
  - DoubleSharkfinOption
---

## Product Definition

A sharkfin combines a participating vanilla leg with a knock-out barrier:
payoff grows with the underlying at the participation rate until the
barrier knocks the structure out, after which the holder receives the
knocked-out terms. The single variant carries one barrier; the double
variant carries an upper barrier and a lower barrier around the strike.

## Pricing Inputs

Single - required: initial price (spot), maturity in years, strike,
barrier. Double - required: initial price, maturity in years, strike,
lower barrier, upper barrier. Both default: option type, contract
multiplier, participation rate (desk default, configurable).

## Diagnostics

Spot near a knock-out barrier means the participating leg can vanish;
gamma and vega concentrate there - flag for hedge review. A participation
rate far from recent desk levels on an imported row warrants source
verification before pricing.
