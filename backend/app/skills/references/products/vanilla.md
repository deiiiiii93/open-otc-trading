---
name: vanilla
description: European and American vanilla option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - EuropeanVanillaOption
  - AmericanOption
---

## Product Definition

A vanilla option pays the intrinsic value of a call or put on a single
underlying. European exercise settles only at maturity; American exercise
allows early exercise any time up to maturity, which adds an early-exercise
premium priced by the engine, not by convention.

## Pricing Inputs

Required: initial price (spot), maturity in years, strike.
Defaulted: option type (call or put; desk default, configurable) and
contract multiplier. American and European variants share the same term
set; the family choice itself selects the exercise style.

## Diagnostics

Deep in-the-money American puts warrant an early-exercise check against
carry. A vanilla whose quoted volatility input is stale versus the desk
surface is a data-quality issue before it is a pricing issue.
