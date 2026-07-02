---
name: autocallable-variants
description: KO-reset and Phoenix autocallable deltas on top of the snowball base semantics.
reference_type: product
quantark_classes:
  - KnockOutResetSnowballOption
  - PhoenixOption
extends: snowball
---

## Product Definition

Both families share the snowball base semantics (see the snowball
reference; inherited here). A KO-reset snowball re-arms after knock-in
with a post-KI barrier leg: a distinct post-KI KO barrier and post-KI KO
coupon apply once knock-in has occurred. A Phoenix pays periodic coupons
whenever the observed level is at or above a coupon barrier, independent
of autocall; with memory coupon enabled, missed coupons are recovered at
the next paying observation.

## Pricing Inputs

In addition to every inherited snowball input: KO-reset requires the
post-KI KO barrier and post-KI KO coupon (post_barrier_config). Phoenix
requires the coupon barrier and coupon rate (coupon_config) and defaults
the memory coupon flag (desk default, configurable).

## Diagnostics

For KO-reset, a knocked-in lifecycle flag switches which barrier leg
governs - re-check hedges at the switch. For Phoenix, spot near the coupon
barrier around an observation makes near-term carry binary; flag for
review.
