# Barrier Build Probe — Findings (2026-06-29)

Probe of the real `build_product("BarrierOption", terms)` service (called directly,
bypassing the agent capability gate) to ground the trader-rfq-booking-day workflow's
build step (spec §6).

## Clean DOWN_IN build (the correct product)

```
terms = {initial_price: 100, strike: 100, barrier: 80, maturity_years: 1,
         option_type: "PUT", barrier_type: "DOWN_IN"}
```
→ `ok=True`, `quantark_class=BarrierOption`, `engine_name=BarrierAnalyticalEngine`,
`product_kwargs.barrier_type=DOWN_IN`.

## Layer 1 — optional-default trap: CONFIRMED (primary discriminator)

| terms | ok | barrier_type built |
|---|---|---|
| `…, barrier_type="DOWN_IN"` | True | **DOWN_IN** (correct) |
| `…` (barrier_type omitted) | True | **DOWN_OUT** (default — WRONG product) |

Omitting `barrier_type` is **not** a missing field and **not** a rejection — it
silently builds a down-and-OUT. No term-collection card, no HITL. A model that fails
to carry "down-and-in" from the RFQ into the build books the wrong payoff, caught at
step-6 snapshot verification.

## Layer 2 — context-recoverable validation rejection: DROPPED

| candidate barrier | ok | missing |
|---|---|---|
| `120` (down barrier ABOVE spot) | True | [] — builds fine, NOT rejected |
| `100` (barrier == spot) | True | [] — builds fine, NOT rejected |
| `-5` (negative) | False | [] — rejected, but implausible (no model submits this) |
| barrier omitted | False | `['barrier']` — would raise a term-collection CARD (HITL) |

The only `ok=False, missing=[]` rejection is a negative barrier, which no model would
plausibly produce. The plausible "careless" placements (above/at spot) build without
rejection. So there is **no plausible, context-recoverable validation rejection** for
`BarrierOption`.

**Decision (per spec §6):** Layer 2 is dropped. Layer 1 (the DOWN_OUT default trap)
stands alone — it is a strong, HITL-free discriminator on its own. No bad barrier
value is engineered into the fixtures; the replay path uses the clean DOWN_IN build.

## Consequences for Task 4

- Replay `step-4-build` returns `ok=True` with `barrier_type=DOWN_IN`,
  `engine_name=BarrierAnalyticalEngine` (already the case in the plan's fixtures).
- The objective manifest rewards the end state (a DOWN_IN position matching the RFQ),
  never a rejection — unchanged.
- No Layer-2 fixtures/assertions to add.
