---
name: snowball-cn
description: Durable China Snowball product conventions and diagnostics for desk workflows.
reference_type: product
---

## Product Definition

A China-market Snowball is a path-dependent autocallable on one A-share index
underlying, typically CSI 300, CSI 500, or CSI 1000. Scheduled KO observations
pay accrued coupon and principal early when the closing level is at or above the
KO barrier. If the trade never knocks out and never knocks in, it returns full
principal at maturity. If it knocks in and never knocks out, terminal payoff
takes equity loss versus strike.

## Observation Conventions

KO observations are scheduled monthly. Daily KI is the default for CN Snowballs
and uses discrete SSE business-day observations from trade start plus one day
through exercise date. European KI uses a single maturity observation, and no-KI
trades remove the knock-in leg. Knock-in is sticky once observed, so lifecycle
fields must override any assumption that the KI barrier is still conditional.

## Pricing Inputs

The desk adapter maps standard CN Snowballs to `SnowballQuadEngine`. Required
inputs are spot, volatility, risk-free rate, dividend yield or discrete
dividends, KO schedule, KI convention, strike, notional, coupon, and lifecycle
state. ACT/365 and China Mainland exchange calendars are the default desk
conventions for imported CN Snowballs.

## Diagnostics

Spot within 5 percent of KI indicates elevated gamma and should be flagged for
hedge review. Spot within 2 percent of the next KO observation should be
repriced with fresh market data if the prior run is older than one business day.
A knocked-in lifecycle flag that disagrees with the imported lifecycle state is
a data-quality issue before it is a pricing issue.
