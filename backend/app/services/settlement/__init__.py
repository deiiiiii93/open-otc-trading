"""Settlement: governance over the cash that lifecycle events imply.

This package tracks, approves and documents cashflows. It deliberately does
NOT compute payoffs — amounts come from the lifecycle event's own data, and
when absent the cashflow is honestly recorded as ``needs_amount``. Pricing
math belongs to QuantArk, and a settlement calculator here would become a
second, unvalidated pricing surface beside the pinned engine.
"""
