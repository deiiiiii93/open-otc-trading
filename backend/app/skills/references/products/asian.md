---
name: asian
description: Asian (average-price) option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - AsianOption
---

## Product Definition

An Asian option pays against the average of scheduled observations of the
underlying rather than the terminal level, reducing sensitivity to any
single fixing. Recorded fixings become part of the trade's lifecycle: once
captured, they are facts, not forecasts.

## Observation Conventions

The averaging schedule derives from the averaging frequency over the
trade's life. If a booked record lacks captured fixings, fall back to the
full number of observations - do not renormalize the average. Calendars
and day count are desk defaults, configurable.

## Pricing Inputs

Required: initial price (spot), maturity in years, strike.
Defaulted: option type, contract multiplier, averaging frequency
(averaging schedule granularity; desk default, configurable).

## Diagnostics

Missing or duplicated captured fixings versus the averaging schedule is a
data-quality issue. Late in the averaging window, remaining optionality
shrinks - large vega on a nearly-averaged trade warrants a model check.
