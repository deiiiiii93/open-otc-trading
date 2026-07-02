---
name: digital-touch
description: Cash-or-nothing digital and one-touch/double-one-touch semantics and inputs.
reference_type: product
quantark_classes:
  - CashOrNothingDigitalOption
  - OneTouchOption
  - DoubleOneTouchOption
---

## Product Definition

These families pay a fixed cash amount on a trigger. A cash-or-nothing
digital pays the cash payoff at maturity if the terminal level is beyond
the strike. A one-touch pays if the underlying ever touches a barrier
before maturity; a double one-touch references an upper barrier and a
lower barrier. Touch type controls pay-at-touch versus pay-at-maturity.

## Pricing Inputs

Digital - required: initial price (spot), maturity in years, strike, cash
payoff. Defaulted: option type, contract multiplier.
One-touch - required: initial price, maturity in years, barrier, cash
payoff. Defaulted: barrier direction, touch type.
Double one-touch - required: initial price, maturity in years, upper
barrier, lower barrier, cash payoff. Defaulted: touch type.

## Diagnostics

Spot near any barrier concentrates gamma and digital risk at the trigger;
flag for hedge review. Ambiguous barrier direction on an imported one-touch
is a blocking interpretation gap - ask, do not infer.
