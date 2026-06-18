# Design: Direct-Booking Routing + Quant-Ark Product Builders

Date: 2026-05-29
Status: Approved (pending spec review)
Author: desk agent investigation ("Booking test 1" thread)

## Background

Two defects were observed on the Desk Agent thread "Booking test 1" for the
request:

> "Book a snowball option, 000905.SH, 1Y, monthly ko, daily ki, ko level 101%, ki level 70%"

1. **Misrouting.** The orchestrator routed this *direct booking* request down the
   RFQ-drafting path instead of recognizing a booking intent and asking whether a
   quote is needed first.
2. **Broken product construction.** `draft_rfq_from_natural_language` could not
   build a product the quant-ark engine accepts.

### Confirmed root causes (with reproduction)

**Issue #1 — routing.** Persona routing is entirely prompt-driven:
`build_orchestrator(...)` is constructed with `skills=[]`
(`services/deep_agent/orchestrator.py:202`); `workspace_router.py` only decides
*workflow continuity* (continue / new / status), never persona/intent. The
orchestrator prompt (`prompts/orchestrator.md`):

- has **no** routing rule for direct booking. The only "book" in routing is
  line 59, `RFQ approve/reject/release/accept/book → high_board`, which means
  booking an *already-approved RFQ* via `book_rfq_to_position`.
- never mentions `book-position` / "direct booking" in any prompt.
- The closest table match to "Book a snowball …" is line 88,
  `RFQ draft from natural language → trader → draft-rfq`. So the model routed to
  RFQ drafting.

The `book-position` skill exists and is in the trader catalog
(`personas.py:84` → `/skills/workflows/positions/`), and the HITL-gated
`book_position` tool exists (`tools/positions.py:566`). The orchestrator simply
never points at them. Separately, **no prompt or skill contains a quote-vs-book
decision point**, so even correct routing would not ask the user.

**Issue #2 — construction.** Running the exact request through the live code:

```
draft_from_natural_language(msg) →
  ko_barrier: 103.0    (user said 101%)
  ki_barrier: 75.0     (user said 70%)
  ko_observation_schedule: <MISSING>   (user said "monthly ko")
  ki_observation_schedule: <MISSING>   (user said "daily ki")
  missing_fields: ['quantity', 'target']

validate_quantark_build("SnowballOption", that_kwargs, ...) →
  ok: False
  error: "KO observation dates or schedule required for discrete monitoring"
```

The regex extractors (`services/rfq.py:1196-1296`) have no parsing for "ko level",
"ki level", "monthly", or "daily" — they only handle strike/barrier/coupon/vol/
tenor/underlying. The product it emits cannot build in `SnowballQuadEngine`.

The capability to build valid quant-ark snowballs *exists* in
`services/position_adapter.py` (`_snowball_barrier_config`, `_ki_barrier_config`,
`_daily_ki_schedule`, `_ki_schedule`, `_single_barrier_schedule`, SSE-calendar
helpers) — but it is the spreadsheet-**import** path, requires explicit KO dates
in the row (does not synthesize a schedule from "monthly" + tenor), and is not
reachable from the agent's NL/booking tools. The booking validation gate added
recently (`services/domains/booking.py:55-80`) correctly *rejects* a bad
snowball, but nothing *produces* a good one from natural language.

Latent third bug: `book_position` defaults `engine_name="BlackScholesEngine"`
(`tools/positions.py:576`); snowballs need `SnowballQuadEngine`, and the
`book-position` skill never states the mapping, so a correctly-routed booking
would still fail validation unless the agent overrides the engine.

## Goals

- Recognize a *direct booking* intent and ask one quote-first question before
  delegating (Issue #1).
- Provide a deterministic, agent-facing builder that turns LLM-extracted
  structured terms into a quant-ark-validated product, synthesizing KO/KI (and
  averaging) observation schedules where required, for **all** catalog product
  families (Issue #2).
- Retire the brittle regex NL drafter in favor of LLM extraction + deterministic
  builders.

## Non-goals

- Changing the quant-ark engine or pricing math.
- Building a deterministic free-text NL parser (the LLM does extraction).
- Auto-inventing missing economics (lockup, trade start, barrier levels, coupon).
- Changing the RFQ lifecycle state machine or the HITL/approval model.

## Key design decisions (locked with stakeholder)

1. **Division of labor:** the trader persona (LLM) reads NL and fills a
   structured per-family term schema (guided by a skill); a deterministic
   `build_product` tool constructs + validates. (Not a beefed-up regex parser,
   not a single NL-in/product-out tool.)
2. **Booking flow:** on any direct "book a `<product>`" intent the orchestrator
   **always asks quote-first** ("price/quote it first, or book at stated
   terms?") before delegating.
3. **KO schedule synthesis:** **require an explicit lockup.** If the user did not
   state one, the builder reports it as a missing term and the agent asks; it is
   never assumed.
4. **Trade start date:** use an explicit `trade_effective_date` if provided,
   otherwise treat it as a missing term and ask. Not synthesized from the
   accounting anchor or wall clock.
5. **Packaging:** one `build_product(family, terms)` tool backed by a per-family
   builder registry with shared schedule-synthesis helpers. Each builder is an
   independently testable pure function. The regex
   `draft_rfq_from_natural_language` is retired.

## Architecture

```
User text
   │  trader (LLM) reads, fills structured per-family term schema
   ▼
structured terms { family, underlying, levels, frequencies, tenor, dates, ... }
   │  build_product(family, terms)  [deterministic tool, DOMAIN_READ, pure]
   ▼
product_builders registry  (keyed by quantark_class)
   ├─ scalar families      (vanilla, american, digital, barrier, one_touch, futures, spot)
   ├─ schedule families    (asian, single/double sharkfin)
   └─ autocallable families(snowball, ko_reset_snowball, phoenix)
        │  shared: levels→barriers, enum/config coercion, schedules.py synthesis
        ▼
   validate_quantark_build(...)
   ▼
{ ok, quantark_class, engine_name, product_kwargs, missing[], warnings[], validation }
   │
   ├─ ok → book-position (HITL book_position)  OR  draft-rfq/quote-rfq
   └─ missing → agent asks one consolidated clarification, re-calls build_product
```

## Components

### New

1. **`services/domains/schedules.py`** — shared, pure date/schedule synthesis,
   extracted from the private helpers currently in `position_adapter.py`:
   - `china_sse_business_days(start, end) -> list[date]`
   - `monthly_observation_dates(start, maturity, lockup_months, day_of_month) -> list[date]`
   - `build_ko_schedule(dates, barriers, rates, *, annualized, frequency) -> dict`
   - `build_ki_schedule(dates, barrier, *, frequency) -> dict`
   - Reuses the existing SSE holiday loader
     (`quantark_path/util/calendar/holidayfile/china_sse.csv`).
   Both `position_adapter` and `product_builders` import these — one source of
   truth for SSE-calendar logic. The schedule dict shape matches the existing
   adapter output: `{"records": [{"observation_date": "YYYY-MM-DD", "barrier": X,
   "return_rate": r?, "is_rate_annualized": bool?}], "aggregation_mode":
   "STOP_FIRST_HIT", "frequency": "MONTHLY"|"DAILY"|"CUSTOM"}`.

2. **`services/domains/product_builders.py`** — the builder layer.
   - `@dataclass BuildResult(ok, quantark_class, engine_name, product_kwargs,
     missing, warnings, validation)`.
   - `build_product(family: str, terms: dict, *, market: PricingEnvironmentSnapshot
     | None = None) -> BuildResult`.
   - A registry keyed by `quantark_class` (mirrors quant-ark `PRODUCT_BUILDERS`
     and `try_solve_registry`). One pure builder per family.
   - Shared helpers: percentage-level → barrier mapping (`101` → ko_barrier on a
     100 initial), `ObservationType`/config enum coercion (reuse
     `quantark.normalize_quantark_kwargs`), schedule synthesis via `schedules.py`.
   - Recommended engine per family (e.g. SnowballOption → SnowballQuadEngine).
   - Always finishes by calling `quantark.validate_quantark_build(...)` and folds
     the result into `BuildResult.validation`.
   - **Missing-term policy:** any economic input that would otherwise be invented
     (KO lockup, trade start when needed for schedule placement, barrier levels,
     coupon/ko_rate when the family needs it) is appended to `missing`; dependent
     schedules are not constructed; `ok=False`.

3. **`tools/products.py`** — `build_product` `@tool` wrapper,
   `capability_gated(group=ToolGroup.DOMAIN_READ)` (pure, no DB write). Validates
   args via a `ToolBuildProductInput` schema (family + structured terms),
   delegates to `product_builders.build_product`, returns the `BuildResult` as
   JSON. Registered in `tools/__init__.py` and added to the persona tool list.

4. **`skills/workflows/products/build-product/SKILL.md`** — new anchor skill
   (domain `products`, `workflow_type: action`, `write_actions: false`). Teaches:
   identify family from the catalog → extract the structured term schema →
   `build_product` → if `missing`, ask one consolidated clarification → on `ok`,
   route to `book-position` (book as-is) or `draft-rfq`/`quote-rfq` (quote
   first). References `build-contract.md`.

5. **`skills/references/products/build-contract.md`** — per-family term schema:
   what each family requires, which terms are scalar vs. schedule-bearing, which
   are synthesizable vs. must-ask, and the recommended engine per family.

### Changed

- **`prompts/orchestrator.md`**
  - New routing rule for *direct booking from terms* (distinct from "book an
    approved RFQ → high_board" and from "Book this RFQ …" inline).
  - Quote-first clarification contract: on a direct booking intent, ask one
    defaulted question ("quote/price first, or book at stated terms?") before
    any `task(...)`; route to trader `draft-rfq`/`quote-rfq` (quote) or trader
    `book-position` (book as-is) on the answer.
  - Add `book-position` and `build-product` rows to the known-skills table.
- **`prompts/trader.md`** — replace `draft_rfq_from_natural_language` with
  `build_product` in the tools list and the routing-from-skills section.
- **`skills/workflows/positions/book-position/SKILL.md`** — add a step to call
  `build_product` first; state the snowball → `SnowballQuadEngine` engine mapping
  (fixes the latent `BlackScholesEngine` default).
- **`skills/workflows/rfq/draft-rfq/SKILL.md`** — replace the
  `draft_rfq_from_natural_language` step with `build_product`.
- **`services/position_adapter.py`** — import schedule/calendar helpers from
  `schedules.py` (behavior-preserving refactor; remove the now-duplicated private
  helpers).
- **`personas.py`** — add `/skills/workflows/products/` to the trader catalog.
- Retire dead `parse_natural_language_rfq` (`services/quantark.py`).
- Migrate `draft_rfq_from_natural_language` (see Migration).

## `build_product` output contract

```json
{
  "ok": true,
  "quantark_class": "SnowballOption",
  "engine_name": "SnowballQuadEngine",
  "product_kwargs": { "...validated, schedule-bearing..." },
  "missing": [],
  "warnings": [],
  "validation": { "ok": true, "error": null }
}
```

When `missing` is non-empty, `ok=false`, no schedule that depends on a missing
input is constructed, and `validation` is omitted or `{ok:false}`. The agent
surfaces `missing` as a single clarification, then re-calls.

## Data flow — worked example

Request: "Book a snowball option, 000905.SH, 1Y, monthly ko, daily ki, ko level
101%, ki level 70%"

1. Orchestrator detects a **direct booking intent** → asks: "Quote it first, or
   book at the stated terms? (quote / book as-is)". No `task(...)` yet.
2. User: "book as-is" → `task(trader, "Use book-position")`.
3. Trader reads `book-position` → `build-contract`; extracts `SnowballOption`,
   underlying `000905.SH`, `maturity=1.0`, `ko_freq=MONTHLY`, `ki_freq=DAILY`,
   `ko_barrier=101`, `ki_barrier=70`.
4. `build_product(...)` → `missing=["trade_start_date",
   "barrier_config.lockup_months", "barrier_config.ko_rate", "quantity"]`,
   `ok=false` (cannot place monthly KO dates without start + lockup; coupon not
   stated).
5. Trader asks one consolidated clarification.
6. On reply → `build_product` returns `ok=true`: monthly KO schedule (start +
   lockup on the SSE calendar) + daily-business-day KI schedule,
   `engine_name="SnowballQuadEngine"`, validated.
7. Trader composes a confirmation summary → HITL → `book_position(...,
   engine_name="SnowballQuadEngine")`.

## Family coverage tiers

- **Scalar-only:** vanilla, american, digital, barrier, one_touch, futures,
  spot — map strike/levels/option_type/maturity; no schedule. Thin builders.
- **Schedule-bearing:** asian (averaging schedule), single/double sharkfin
  (barrier observation) — reuse `schedules.py`.
- **Autocallable:** snowball, ko_reset_snowball, phoenix — full KO/KI schedule
  synthesis + barrier/coupon/accrual config + enum coercion. The core work.

## Error handling

- Missing economics → reported in `missing`, never fabricated; blocks dependent
  schedule construction; `ok=false`.
- Soft issues (e.g. defaulted day-count, assumed `STOP_FIRST_HIT` aggregation) →
  `warnings`, non-blocking.
- A quant-ark build failure that is not a missing-term case → surfaced verbatim
  in `validation.error` with `ok=false`.

## Migration

- **`parse_natural_language_rfq`** (`services/quantark.py`): dead except its own
  test — remove, update `test_quant_services.py`.
- **`draft_rfq_from_natural_language`**: referenced by an API route in
  `main.py`, `services/agents.py`, `tools/rfq.py`, and tests
  (`test_services_domains_rfq.py`, `test_quant_services.py`, `test_tools_rfq.py`).
  Audit each: re-point the API route / agents path to the build flow, or keep a
  thin compatibility shim only where an external (non-agent) caller requires it;
  remove from the agent tool surface. Update the named tests.

## Testing

- **`tests/test_product_builders.py`** (new): for each family, builder output
  passes `validate_quantark_build`; snowball monthly-KO + daily-KI date
  correctness (count, SSE business-day placement, lockup respected);
  missing-term surfacing (no lockup / no start → `missing`, no fabricated
  schedule); percentage levels (101% / 70% → ko_barrier / ki_barrier).
- **Tool + end-to-end:** `build_product` tool contract; `book_position` with a
  built snowball succeeds end-to-end through the existing booking validation.
- **`tests/test_routing_contracts_phase3.py`:** assert the orchestrator prompt
  contains the booking-intent routing + quote-first clarification language and
  the new known-skills rows.
- **Skill-catalog coupling** (known fragile exact-set + count assertions): update
  `test_skills_catalog.py`, `test_skills_catalog_v2.py`,
  `test_workflow_skills_phase3.py` for the new `products/build-product` skill and
  the new `products/` workflow group; update `test_capability_assignments.py` if
  the new tool's capability group is asserted there.
- **Refactor safety:** existing `position_adapter` snowball/phoenix import tests
  must remain green after the `schedules.py` extraction.

## Risks / open considerations

- **Schedule-shape drift:** the synthesized schedule dict must exactly match what
  `quantark._build_observation_schedule` / quant-ark `ObservationSchedule`
  expects. Mitigation: builders always run `validate_quantark_build`; tests
  assert real builds, not just dict equality.
- **`products/` group introduction** touches `personas.py` and three catalog
  tests; this is expected and enumerated above.
- **Booking-intent disambiguation:** the new rule must not capture "book RFQ-N" /
  "Book this RFQ" (those stay on the RFQ lifecycle path). The orchestrator
  contract must phrase the trigger as "book a *new product from terms*".
- **API-route migration** for `draft_rfq_from_natural_language` may have a
  frontend consumer; the audit step must confirm before retiring.
```
