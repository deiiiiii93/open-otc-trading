---
name: engines
description: Durable pricing-engine conventions and input requirements for desk workflows.
reference_type: pricing
---

## Engine Families

European vanilla options use analytic Black-Scholes style valuation and are
cheap to run. Snowball products use path-aware structured-product engines with
observation calendars and barrier state. Phoenix products use event-driven
structured-product engines with coupon and barrier events. Engine choice should
follow product type and contract terms rather than persona or page context.

## Required Inputs

Analytic vanilla valuation requires spot, volatility, risk-free rate, dividend
yield, and tenor. Structured-product valuation requires those market inputs plus
notional, strike, barriers, coupon terms, observation schedules, settlement
dates, and lifecycle state. Missing schedules or lifecycle state should be
treated as contract-data gaps rather than market-data gaps.

## Cost Classes

Single analytic valuations are cheap. Single structured valuations are medium
unless the engine configuration expands path count or grid size. Portfolio-wide
structured-product repricing is expensive and should be previewed before
execution, especially when Snowball or Phoenix trades are included.

## Selection Rule

Pick the engine from product type and validated contract terms. Do not infer a
cheaper engine from a short user request. If the requested product has
path-dependent barriers, use a structured-product engine even when the user asks
for a quick quote.
