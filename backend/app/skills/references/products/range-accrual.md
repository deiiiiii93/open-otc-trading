---
name: range-accrual
description: Range accrual option semantics and pricing inputs.
reference_type: product
quantark_classes:
  - RangeAccrualOption
---

## Product Definition

A range accrual accrues coupon for each scheduled observation on which the
underlying fixes inside the range between the lower barrier and the upper
barrier. Observations outside the range accrue nothing. The payoff is the
accumulated accrual paid per the trade's settlement terms.

## Observation Conventions

Accrual observations follow the trade's observation frequency; each
observation is an independent in-range test against the same range unless
the terms say otherwise. Calendars and day count are desk defaults,
configurable.

## Pricing Inputs

Required: initial price (spot), maturity in years, lower barrier, upper
barrier, accrual rate (the per-period coupon accrued while in range).
Defaulted: observation frequency, contract multiplier.

## Diagnostics

Spot hovering at a range edge makes daily accrual binary and concentrates
risk; flag for hedge review. Verify the accrual rate quotation (per period
versus annualized) on imported rows - a mismatch is a data-quality issue.
