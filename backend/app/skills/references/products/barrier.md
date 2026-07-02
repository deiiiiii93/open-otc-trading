---
name: barrier
description: Single-barrier (knock-in/knock-out) option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - BarrierOption
---

## Product Definition

A barrier option is a vanilla payoff gated by a barrier event. Knock-out
variants cancel the option when the barrier is touched (optionally paying
a rebate); knock-in variants activate it. The barrier type encodes
direction and gating (e.g. up-and-out, down-and-in). Barrier events are
sticky lifecycle facts once observed.

## Pricing Inputs

Required: initial price (spot), maturity in years, strike, barrier.
Defaulted: option type, contract multiplier, barrier type (desk default,
configurable), rebate (amount paid on knock-out; defaults to none).

## Diagnostics

Spot near the barrier concentrates gamma and makes valuation sensitive to
observation timing; flag for hedge review. An imported row whose barrier
type disagrees with its moneyness at trade date is a data-quality issue.
