---
name: delta-one
description: Futures and spot instrument semantics and pricing inputs.
reference_type: product
quantark_classes:
  - Futures
  - SpotInstrument
---

## Product Definition

Delta-one instruments track the underlying one-for-one: no optionality, no
barriers. A futures position carries basis to the underlying that decays
toward expiry; a spot instrument is direct exposure. These families are
primarily hedging and inventory instruments.

## Pricing Inputs

Required: initial price and the underlying identifier.
Futures defaults: contract multiplier, maturity in years, basis, basis
decay rate, market price, contract code. Spot defaults: delta-one type,
instrument code, exchange, contract multiplier. All defaults are desk
defaults, configurable.

## Diagnostics

A futures mark diverging from spot plus modeled basis indicates a stale
market price or wrong contract code. Missing contract multiplier on an
imported row silently mis-scales exposure - verify against the exchange
contract specification.
