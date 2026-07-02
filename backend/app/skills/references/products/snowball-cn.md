---
name: snowball-cn
description: CN market overlay for snowball products - calendars, index universe, desk diagnostics.
reference_type: product
region: CN
extends: snowball
---

## Observation Conventions

CN snowballs typically reference one A-share index underlying (commonly
CSI 300, CSI 500, or CSI 1000) with monthly KO observations. Daily KI is
the default convention and uses discrete SSE business-day observations
from trade start plus one day through exercise date. ACT/365 and China
Mainland exchange calendars are the default desk conventions for imported
CN snowballs.

## Diagnostics

Spot within 5 percent of KI indicates elevated gamma and should be flagged
for hedge review. Spot within 2 percent of the next KO observation should
be repriced with fresh market data if the prior run is older than one
business day.
