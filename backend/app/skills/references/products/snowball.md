---
name: snowball
description: Region-neutral snowball autocallable payoff semantics and pricing inputs.
reference_type: product
quantark_classes:
  - SnowballOption
---

## Product Definition

A snowball is a path-dependent autocallable on a single underlying.
Scheduled knock-out (KO) observations pay accrued coupon and principal
early when the observed level is at or above the KO barrier. If the trade
never knocks out and never knocks in, it returns full principal at
maturity. If it knocks in (KI) and never knocks out, terminal payoff takes
equity loss versus strike. Knock-in is sticky once observed: lifecycle
fields override any assumption that the KI barrier is still conditional.

## Observation Conventions

KO observations follow the trade's observation frequency and KO
observation dates (explicit dates required when frequency is CUSTOM). The
KI convention is a term of the trade: daily discrete observation, a single
European observation at maturity, or no-KI (removes the knock-in leg).
A lockup period of some months may suppress early KO observations. Day
count and holiday calendar are desk defaults, configurable per deployment.

## Pricing Inputs

Required: initial price (spot), maturity in years, trade start date,
observation frequency, KO barrier, KI barrier, KO coupon (ko_rate),
lockup months, and KO observation dates when the frequency is CUSTOM.
Defaulted: KI convention, annualized-coupon convention (ko_rate_annualized),
initial date, settlement date. Lifecycle state (knocked-in flag) is a
required interpretation input before any valuation.

## Diagnostics

Spot near the KI barrier indicates elevated gamma and warrants hedge
review. Spot near the next KO observation warrants repricing with fresh
market data. A knocked-in lifecycle flag that disagrees with the imported
lifecycle state is a data-quality issue before it is a pricing issue.
