# Date-First Booking Lifecycle Refactor

**Date:** 2026-07-29
**Status:** Validated design
**Scope:** Product construction, direct Booking, RFQ, import, hedge booking, persistence, pricing read models, agent tools, prompts, skills, HITL, golden workflows, and legacy booked-product migration

## 1. Summary

Replace numeric `maturity` and `maturity_years` as persisted booking inputs with
absolute lifecycle dates.

For options and autocallables, the canonical product terms are
`exercise_date` and, where economically relevant, `settlement_date`.
Schedule-bearing products also retain `initial_date`. Futures use
`maturity_date`. Numeric remaining maturity becomes a calculated read-model
field derived from an explicit `as_of` date.

Users and agents may continue to enter a relative tenor such as `6M` or `1Y`.
Tenor is an ingress convenience, not a product term. A new deterministic
lifecycle resolver converts it into absolute dates before product construction,
validation, hashing, persistence, RFQ versioning, or HITL confirmation.

The rollout is staged:

1. Make every new product write date-only.
2. Retain temporary dual-read support for legacy numeric products.
3. Reconstruct existing booked-product dates from authoritative evidence.
4. Create replacement date-based Product rows and repoint Positions.
5. Quarantine ambiguous products rather than inventing dates.
6. Remove legacy numeric read paths and columns after a zero-live-reference
   gate.

## 2. Problem

A booked product has fixed economic dates, while its remaining time to expiry
changes with the pricing or accounting view date. Persisting `maturity=1.0`
stores a valuation-relative quantity as if it were immutable product identity.

The defect is observable in the current runtime. With the installed
`quantark==0.3.0`:

| Product construction | Remaining maturity at 2026-07-16 | Remaining maturity at 2026-08-16 |
|---|---:|---:|
| European, `exercise_date=2026-09-18` | 0.175342466 | 0.090410959 |
| Barrier, `exercise_date=2027-07-16` | 1.000000000 | 0.915068493 |
| Snowball, `maturity=1.0` | 1.000000000 | 1.000000000 |

The date-based products age correctly because QuantArk calculates maturity from
`pricing_env.valuation_date`. The numeric Snowball remains frozen.

The current live book is predominantly legacy-shaped:

- 11 of 14 Positions persist numeric maturity.
- All three Snowballs persist `maturity=1.0`; each has a settlement date but no
  exercise date.
- Four of five Barriers, three of four Europeans, and the single Futures
  position persist numeric maturity.

This is not isolated to one form. Human Booking is partly date-based, while RFQ
templates, product contracts, builders, agent tools, skills, golden workflows,
and hedge-leg construction still expose or produce numeric maturity. Removing a
single UI field would leave conflicting product identities across ingress
channels.

## 3. Goals

1. Make absolute lifecycle dates the canonical identity of every newly booked
   product.
2. Give human, RFQ, import, hedge, and agent booking paths one deterministic
   lifecycle contract.
3. Preserve tenor shorthand without persisting tenor or numeric maturity.
4. Resolve tenor from the product's economic start date, not from ambient wall
   clock time.
5. Derive settlement using a server-owned convention, with an explicit date
   override.
6. Calculate remaining maturity from an explicit `as_of` date and expose its
   day-count convention.
7. Migrate existing booked products without mutating shared Product identity or
   fabricating ambiguous dates.
8. Preserve immutable RFQ, audit, and source evidence.
9. Keep pricing and risk aligned with QuantArk's date-aware lifecycle behavior.
10. Remove legacy numeric booking behavior after a staged, observable cutover.

## 4. Non-goals

- Do not remove the concept of maturity from pricing analytics. Remaining
  maturity remains a derived engine quantity.
- Do not make `accounting_date` and pricing `valuation_date` interchangeable.
- Do not rewrite immutable historical RFQ quote payloads merely to modernize
  their shape.
- Do not infer product dates from wall-clock `today`.
- Do not accept a universal settlement lag when the market/product convention is
  unknown.
- Do not change payoff formulas or pricing methodology.
- Do not use an LLM to perform calendar arithmetic.
- Do not mutate a shared Product row in place when its economic identity changes.
- Do not mechanically convert fractional years into days for legacy data.

## 5. Locked design decisions

| Decision | Choice |
|---|---|
| Refactor boundary | Full lifecycle migration across every booking ingress and existing booked products |
| Relative tenor | Allowed as shorthand; resolved before product construction and never persisted |
| Tenor anchor | `initial_date` for schedule-bearing products; `trade_effective_date` otherwise |
| Accounting date | May default a new request's effective date; never silently becomes product economics |
| Settlement date | Derived from server-owned lag/calendar/roll convention; explicit override allowed |
| Legacy conversion | Evidence-based; ambiguous products are quarantined |
| Cutover | Date-only new writes, temporary legacy reads, migration, then numeric-path removal |
| Architecture | One central lifecycle normalization boundary |

## 6. Architecture

Create a pure domain module:

`backend/app/services/domains/product_lifecycle.py`

Every path capable of creating or replacing a Product calls this module before:

1. schedule synthesis,
2. QuantArk construction and validation,
3. `product_term_hash`,
4. Product persistence,
5. RFQ quote-version persistence, or
6. HITL confirmation.

The central interface is:

```python
def resolve_product_lifecycle(
    family: str,
    *,
    trade_effective_date: date,
    initial_date: date | None = None,
    tenor: str | None = None,
    exercise_date: date | None = None,
    settlement_date: date | None = None,
    conventions: LifecycleConventions,
) -> LifecycleResolution:
    ...
```

The function is deterministic and DB-free. It never reads wall-clock time. The
caller supplies all request dates; a separate server-owned convention resolver
selects the applicable calendar, roll rules, settlement lag, and day count from
product family, market, underlying, and currency.

`LifecycleResolution` contains:

```python
@dataclass(frozen=True)
class LifecycleResolution:
    canonical_terms: dict[str, Any]
    anchor_date: date
    expiry_field: Literal["exercise_date", "maturity_date"]
    expiry_date: date
    settlement_date: date | None
    source: LifecycleResolutionSource
    conventions: LifecycleConventions
    input_tenor: str | None
```

The returned canonical terms never contain `maturity`, `maturity_years`, or
unresolved `tenor`. Original input and resolution provenance belong in the
booking/RFQ audit payload, outside Product identity.

## 7. Lifecycle semantics

### 7.1 Canonical fields

| Product kind | Required canonical lifecycle fields |
|---|---|
| Scalar options | `exercise_date`; `settlement_date` when applicable |
| American options | final `exercise_date`; `settlement_date` |
| Touch/barrier/sharkfin | `exercise_date`; `settlement_date` |
| Snowball/Phoenix/KO-reset | `initial_date`, final-observation `exercise_date`, `settlement_date` |
| Asian/Range Accrual | `initial_date` when needed, final-observation `exercise_date`, settlement metadata |
| Futures | `maturity_date` |
| Spot instruments | no expiry field |

`exercise_date` is the last exercise or final observation date used to calculate
option remaining maturity. `settlement_date` controls payment timing and must
not generally replace the exercise date. Futures use their market maturity date.

### 7.2 Tenor grammar

Ingress accepts calendar tenor labels, not fractional-year maturity:

```text
<positive integer><D|W|M|Y>
```

Examples: `10D`, `2W`, `6M`, `18M`, `1Y`, `3Y`.

Calendar months and years are added as calendar units. They are not converted to
`365 * maturity` days. The convention policy then rolls the unadjusted date.
Unsupported or fractional labels fail validation.

### 7.3 Anchor rules

- Schedule-bearing products require an explicit `initial_date`. UI and agent
  request builders may prefill it from `trade_effective_date`, but the domain
  request must carry the resulting date explicitly.
- Other expiring products anchor tenor to `trade_effective_date`.
- If a caller supplies an explicit expiry date, tenor is unnecessary.
- Supplying both tenor and an explicit expiry is rejected unless both resolve to
  the identical adjusted date. Accepting an identical pair supports transparent
  UI previews without permitting contradictory economics.
- Historical replay uses the original economic dates; it never resolves against
  the current accounting date.

### 7.4 Settlement rules

Settlement conventions are server-owned and keyed by product/market context.
A convention contains:

```python
@dataclass(frozen=True)
class LifecycleConventions:
    calendar_id: str
    expiry_roll: BusinessDayRoll
    settlement_lag_business_days: int | None
    settlement_roll: BusinessDayRoll
    day_count_convention: str
```

An explicit `settlement_date` overrides convention-derived settlement after
validation. If settlement is required and neither an explicit date nor a
configured convention exists, resolution fails closed. Agents never invent the
lag.

## 8. Product-family coverage

### 8.1 Existing partial date path

The common option builder already has a partial explicit-date path for:

- `EuropeanVanillaOption`
- `AmericanOption`
- `CashOrNothingDigitalOption`
- `BarrierOption`
- `SingleSharkfinOption`
- `DoubleSharkfinOption`

These paths must be standardized on `exercise_date` and settlement conventions,
then numeric construction must be removed from new writes.

### 8.2 QuantArk supports dates; Open OTC builders do not

The installed QuantArk constructors support `exercise_date` and
`settlement_date` for:

- `OneTouchOption`
- `DoubleOneTouchOption`
- `SnowballOption`
- `PhoenixOption`
- `KnockOutResetSnowballOption`

Open OTC builders and product contracts must expose those lifecycle fields and
generate schedules from absolute start/end dates.

### 8.3 Constructor gaps

`AsianOption` and `RangeAccrualOption` accept `exercise_date` in the installed
QuantArk version but do not accept `settlement_date`. The refactor must choose one
of these implementation mechanisms without weakening the Open OTC contract:

1. upgrade/extend QuantArk so the constructors forward settlement lifecycle
   fields, or
2. retain settlement as a clearly named Open OTC attribute applied after product
   construction.

The selected mechanism must be covered by constructor and pricing-adapter tests.
Settlement may not be silently dropped.

### 8.4 Futures and spot

`Futures` must replace numeric maturity with `maturity_date`. `SpotInstrument`
requires no lifecycle expiry and bypasses this resolver after confirming that no
expiry input was supplied.

## 9. Schedule synthesis

Schedule generation must become absolute-date driven.

Existing helpers in `backend/app/services/domains/schedules.py` already provide
SSE business-day rolling and dated observation generation. Extend them so
schedule builders accept:

```python
build_schedule(
    *,
    start_date: date,
    end_date: date,
    frequency: Frequency,
    calendar: Calendar,
    roll: BusinessDayRoll,
    ...
) -> ObservationSchedule
```

Required invariants:

- The final contractual observation date equals `exercise_date`.
- No synthesized record falls before `initial_date` or after
  `exercise_date`.
- Lockout/lockup rules are applied from the absolute start date.
- Business-day rolling cannot create duplicate dates; duplicates are
  deterministically collapsed or rejected according to the product contract.
- A caller-supplied schedule whose terminal date conflicts with
  `exercise_date` is rejected.
- Settlement date is not used as the terminal observation date.
- Existing prebuilt schedules remain valid only when their lifecycle envelope is
  internally consistent.

This removes count-based approximations such as deriving monthly observation
counts from a float maturity and prevents rounding drift between product expiry
and schedule endpoints.

## 10. Booking and product identity

`backend/app/services/domains/booking.py` becomes the single enforcement point:

```text
BookingRequest
  -> resolve lifecycle conventions
  -> resolve_product_lifecycle
  -> synthesize/validate product-specific schedules
  -> build_product
  -> validate_quantark_build
  -> product_term_hash(canonical date terms)
  -> create_or_get_product
  -> create Position and structured term rows
  -> audit
```

No Product is hashed or reused before lifecycle normalization. Products with
different exercise or settlement dates must have different hashes. Economically
identical products entered as `1Y` or as an explicit date may reuse the same
Product because the shorthand is absent from canonical terms.

`trade_effective_date` remains Position-level trade data. It is an input to
resolution but not copied into Product identity unless the product explicitly
owns `initial_date`.

Product replacement and position editing use the same pipeline. An edit that
changes a lifecycle date creates or reuses a different Product and repoints the
Position; it does not mutate the old Product.

## 11. API and UI contract

### 11.1 Write inputs

New booking/product-build inputs support:

```json
{
  "trade_effective_date": "2026-07-29",
  "terms": {
    "initial_date": "2026-07-29",
    "tenor": "1Y",
    "exercise_date": null,
    "settlement_date": null
  }
}
```

or explicit dates:

```json
{
  "trade_effective_date": "2026-07-29",
  "terms": {
    "exercise_date": "2027-07-29",
    "settlement_date": "2027-08-02"
  }
}
```

`maturity` and `maturity_years` are rejected for all new booking-capable API and
tool calls. Legacy compatibility serializers may still read them from unmigrated
rows during the transition.

### 11.2 Preview/build output

Preview and build responses include the resolved dates and provenance:

```json
{
  "canonical_terms": {
    "exercise_date": "2027-07-29",
    "settlement_date": "2027-08-02"
  },
  "lifecycle_resolution": {
    "source": "tenor_from_trade_effective_date",
    "anchor_date": "2026-07-29",
    "input_tenor": "1Y",
    "calendar": "CHINA_SSE",
    "expiry_roll": "FOLLOWING",
    "settlement_lag_business_days": 2,
    "settlement_roll": "FOLLOWING"
  }
}
```

### 11.3 Calculated maturity read model

Position/Product read models may include:

```json
{
  "lifecycle": {
    "exercise_date": "2027-07-29",
    "settlement_date": "2027-08-02",
    "remaining_maturity_years": 1.0,
    "maturity_as_of": "2026-07-29",
    "day_count_convention": "ACT_365",
    "status": "active"
  }
}
```

The calculated value must always include `maturity_as_of` and its day-count
convention. Pricing and risk derive it from the active pricing environment's
`valuation_date`; a desk display may request calculation as of the accounting
date. Neither path persists the result.

At or after exercise, the lifecycle status is `expired`, `exercised`, or another
product-specific terminal status. The system must not silently pass a clamped
zero maturity into an otherwise active pricing workflow.

### 11.4 Booking interface

The Booking and RFQ interfaces:

- remove editable Maturity fields,
- add an optional tenor shorthand control,
- require or prefill the economic start date,
- show resolved exercise and settlement dates before submission,
- show calculated remaining maturity as read-only with `as_of`,
- clear legacy extra-field rendering of `maturity`,
- submit `trade_effective_date`, which the current human Booking page omits.

## 12. RFQ, import, and hedge flows

### 12.1 RFQ

RFQ templates and natural-language extraction may collect tenor, but the server
resolves dates before an executable quote version is persisted. Each quote
version therefore freezes actual dates.

`book_rfq_to_position`:

- books the accepted quote's canonical dates,
- validates those dates again,
- never re-resolves the original tenor against a later accounting date, and
- preserves the historical quote payload unchanged as evidence.

Future RFQ templates and quote forms must not emit numeric maturity.

### 12.2 Position import

Position import already maps final-observation/maturity and settlement columns
to dates and is the reference ingress pattern. The import path must delegate to
the central lifecycle resolver for validation and convention-derived settlement.
New imports must reject numeric-only expiry input.

### 12.3 Hedge booking

Listed option and futures hedge legs currently convert an absolute instrument
expiry into `maturity_years`. Replace that conversion with:

- option expiry -> `exercise_date`,
- futures expiry -> `maturity_date`,
- instrument settlement metadata -> `settlement_date` or convention resolution.

`book_hedge` remains operationally distinct and HITL-gated, but it must obey the
same persisted Product invariant.

## 13. Tools, agents, skills, and approvals

### 13.1 Tools

Update:

- `build_product`
- `get_product_term_schema`
- `check_term_completeness`
- `book_position`
- `book_rfq_to_position`
- shared product input models
- hedge-booking tools

`build_product` must receive an explicit trade/economic anchor when resolving
tenor. Product-term schema and completeness must treat `exercise_date` as the
canonical option expiry, with tenor as an alternative ingress form. Tool errors
use the same structured lifecycle error codes as REST APIs.

`book_position` revalidates canonical dates even when given a prior
`build_product` result. It must not trust an LLM-carried calculated maturity.

### 13.2 Agents

Update the orchestrator, trader, high-board, async-agent context, and relevant
risk-manager hedge instructions:

- Use accounting date only to default a new trade request or answer relative
  desk-date questions.
- Use economic start dates for tenor resolution.
- Never calculate dates in prose.
- Call the deterministic builder/resolver.
- Never send `maturity` or `maturity_years` to booking tools.
- Reuse accepted RFQ dates exactly.

The risk manager still never calls direct `book_position`; its hedge path is
updated separately through `book_hedge`.

### 13.3 Skills and references

Update:

- `workflows/positions/book-position`
- `workflows/products/build-product`
- `references/products/build-contract`
- vanilla, barrier, digital/touch, Asian, sharkfin, Snowball, range-accrual, and
  delta-one product references
- RFQ lifecycle references
- hedge-booking workflow references

Product references describe dates as the contractual economics and tenor as
input shorthand. They must no longer train agents to ask for or emit numeric
maturity.

### 13.4 HITL and gateway cards

Booking confirmations must show:

- product family and underlying,
- quantity and portfolio,
- trade effective/initial date,
- source tenor when supplied,
- resolved exercise or futures maturity date,
- settlement date,
- settlement convention or explicit-override marker,
- calculated remaining maturity and `as_of`,
- engine selection.

The dates displayed in the confirmation are the exact dates committed after
approval.

## 14. Error handling

Lifecycle failures are deterministic domain errors:

| Code | Meaning |
|---|---|
| `lifecycle_missing_anchor` | Tenor was supplied without the required economic start date |
| `lifecycle_invalid_tenor` | Tenor syntax or value is unsupported |
| `lifecycle_missing_expiry` | Neither explicit expiry nor tenor was supplied |
| `lifecycle_mixed_expiry` | Numeric maturity or conflicting expiry representations were supplied |
| `lifecycle_resolution_mismatch` | Tenor and explicit expiry resolve to different dates |
| `lifecycle_missing_convention` | Required calendar or settlement convention is unavailable |
| `lifecycle_invalid_exercise_date` | Exercise is not after the economic start |
| `lifecycle_invalid_settlement_date` | Settlement precedes exercise or violates its convention |
| `lifecycle_schedule_end_mismatch` | Schedule terminal date conflicts with exercise date |
| `lifecycle_expired_at_booking` | The requested product is already expired at its booking anchor |
| `lifecycle_unsupported_settlement` | The adapter would drop required settlement economics |
| `legacy_lifecycle_ambiguous` | Existing numeric terms cannot be reconstructed safely |

REST returns a typed 422 response for invalid input. Read-only tools return
`ok=false`, the error code, field path, and remediation. Booking transactions
create neither Product nor Position when lifecycle resolution or QuantArk
validation fails.

Unexpected calendar/provider failures are operational errors, not missing
economics. They fail closed and remain retryable only when no write occurred.

## 15. Persistence and migration

### 15.1 Transitional schema

Keep existing nullable numeric columns temporarily:

- `equity_option_products.maturity`
- `equity_option_products.tenor`
- `equity_futures_products.maturity`

New Product writes must leave them null. Canonical date columns remain:

- `exercise_date`
- `settlement_date`
- `maturity_date`
- `initial_date` where product-owned

Add a durable migration ledger, or an equivalently queryable existing audit
representation, with:

```text
source_product_id
replacement_product_id
status = pending | migrated | quarantined | unused_legacy
resolution_method
evidence
canonical_terms
error_code
created_at
completed_at
```

The migration must be restartable and idempotent.

### 15.2 Evidence priority

For each Product referenced by a Position, resolve dates using the strongest
available evidence:

1. existing explicit `exercise_date`/`maturity_date`,
2. authoritative final observation in a persisted schedule,
3. immutable source/import payload dates,
4. accepted RFQ executable terms,
5. listed instrument contract expiry,
6. explicit original tenor plus `initial_date`/`trade_effective_date` and known
   conventions,
7. settlement date only when a known, invertible settlement convention proves
   the exercise date.

Numeric maturity alone is insufficient. A fractional year plus a start date is
usable only when evidence proves it represented a calendar tenor and the
original convention is known.

### 15.3 Replacement, not mutation

For every resolvable legacy Product:

1. build canonical date terms,
2. construct and QuantArk-validate a replacement Product,
3. compute its canonical hash,
4. create or reuse the replacement row,
5. repoint affected Positions transactionally,
6. rebuild structured term rows and barrier/lifecycle state,
7. record the source/replacement link and audit event.

The source Product remains historical evidence and is not mutated in place.
Immutable RFQ quote versions are not rewritten.

Ambiguous Products are quarantined with their exact evidence and remediation
reason. Pricing/booking policy for a quarantined live Position must be explicit:
continue temporary legacy read with a visible warning, or block pricing if the
numeric term cannot be trusted. It must never appear migrated.

### 15.4 Cutover gates

Numeric columns and compatibility reads may be removed only when:

- no new-write endpoint or tool accepts numeric maturity,
- no live Position references a numeric-only Product,
- no active RFQ executable terms are numeric-only,
- no hedge booking path emits numeric maturity,
- all quarantined live Positions are remediated or explicitly retired,
- agent skills and golden workflows contain no numeric booking contract,
- observability reports zero legacy writes for the agreed soak period.

Historical raw payloads may retain numeric values as immutable evidence, but
they are excluded from executable Product terms.

## 16. Alternatives considered

### Chosen: central lifecycle normalization boundary

One deterministic resolver is called by every ingress before product hashing.
This gives all channels the same date arithmetic, validation, provenance, and
failure semantics.

### Rejected: resolve independently at each ingress

Having UI, RFQ, tools, imports, and hedge services calculate their own dates
would minimize initial changes but duplicate calendars and defaulting rules.
Historical replay and cross-channel product reuse would be difficult to prove.

### Rejected: leave normalization to QuantArk construction

QuantArk can derive remaining maturity from dates, but preserving tenor or
numeric maturity until pricing leaves Product hashes valuation-relative and
does not create immutable booked economics.

### Rejected: big-bang migration

Deploying date-only reads and writes with one data rewrite has a larger rollback
surface and would force ambiguous legacy products into fabricated dates or an
outage. A staged cutover isolates new-write correctness from historical
remediation.

## 17. Testing strategy

### 17.1 Pure lifecycle resolver

Test:

- tenor grammar and invalid values,
- scalar versus schedule-bearing anchors,
- explicit-date paths,
- identical and conflicting tenor/date pairs,
- leap years and month-end dates,
- holidays and each supported roll convention,
- settlement lag and explicit overrides,
- missing convention failures,
- exercise/settlement ordering,
- deterministic repeated resolution,
- no dependency on wall-clock time.

### 17.2 Product-family matrix

For every supported family, prove:

- date-only construction succeeds,
- numeric maturity is rejected on new-write paths,
- canonical terms contain the correct expiry field,
- required settlement is retained,
- schedule endpoints equal exercise date,
- QuantArk construction succeeds,
- remaining maturity decreases under a later valuation date.

Explicitly cover European, American, Digital, Barrier, OneTouch,
DoubleOneTouch, Asian, Range Accrual, Single/Double Sharkfin, Snowball, Phoenix,
KO-reset, Futures, Spot, and product packages/components.

### 17.3 Ingress parity

Book equivalent economics through:

- human Booking API,
- agent `build_product` + `book_position`,
- accepted RFQ,
- position import,
- listed hedge booking.

Assert that every path produces identical canonical Product terms and term hash.

### 17.4 Persistence and identity

Test:

- shorthand and explicit-date inputs reuse the same Product,
- different settlement dates do not reuse a Product,
- Position trade date remains position-owned,
- product editing repoints rather than mutates,
- structured term rows mirror canonical dates,
- legacy source Products remain unchanged.

### 17.5 Migration

Test:

- dry-run classification,
- every evidence-priority path,
- ambiguous quarantine,
- idempotent reruns,
- transactional Position repointing,
- shared Product replacement,
- failure rollback,
- structured-row and barrier-state refresh,
- zero-live-reference gate.

### 17.6 Agents and UI

Test:

- term schema/completeness date alternatives,
- prompts and skills never emit numeric maturity,
- HITL shows exact resolved dates,
- accepted RFQ booking does not re-resolve tenor,
- Booking/RFQ forms have no editable maturity field,
- calculated maturity is read-only and displays `as_of`,
- golden trader booking uses dates in both build and booking calls.

### 17.7 Regression

Keep existing pricing and risk outputs stable for equivalent date-based
economics. Run focused booking/builder/contract, RFQ, import, hedging, pricing,
agent-tool, golden-workflow, frontend Booking, position-edit, and RFQ suites,
followed by the full backend and frontend test suites.

## 18. Acceptance criteria

The refactor is complete when:

1. No new Booking, RFQ, import, hedge, API, or agent-tool write accepts
   `maturity` or `maturity_years`.
2. Tenor shorthand resolves deterministically from the economic start date.
3. Every persisted expiring Product has its canonical absolute expiry date.
4. Required settlement economics are never dropped.
5. Remaining maturity changes when `as_of` changes and is never persisted.
6. Product hashes are computed only from canonical date terms.
7. Accepted RFQs book the exact dates frozen in their quote version.
8. HITL cards show the exact lifecycle dates to be committed.
9. Every live legacy Position is migrated or explicitly quarantined.
10. No live Position references a numeric-only Product at the removal gate.
11. Product-family, ingress-parity, migration, agent, and UI regression suites
    pass.
12. The legacy numeric read path and relational numeric maturity columns are
    removed after the soak gate.

## 19. Implementation sequence

1. Add the pure lifecycle types, convention resolver, and unit tests.
2. Make schedule synthesis accept absolute end dates.
3. Convert product builders and family contracts to canonical dates.
4. Enforce lifecycle normalization in domain Booking before product hashing.
5. Update REST schemas, read models, and human Booking/position-edit UI.
6. Update RFQ, import, and hedge-booking paths.
7. Update product tools, completeness/schema tools, prompts, skills, HITL, and
   gateway cards.
8. Update golden workflows and fixtures.
9. Add migration ledger and dry-run/classification report.
10. Migrate resolvable booked Products and quarantine ambiguous ones.
11. Enable legacy-write telemetry and run the staged soak period.
12. Remove legacy readers and numeric columns after all cutover gates pass.

## 20. Expected file surface

Core:

- `backend/app/services/domains/product_lifecycle.py` — new
- `backend/app/services/domains/booking.py`
- `backend/app/services/domains/product_builders.py`
- `backend/app/services/domains/product_contracts.py`
- `backend/app/services/domains/products.py`
- `backend/app/services/domains/schedules.py`
- `backend/app/services/domains/position_terms.py`
- `backend/app/models.py`
- new Alembic migration(s)

Ingress and pricing:

- `backend/app/schemas.py`
- `backend/app/main.py`
- `backend/app/services/rfq.py`
- `backend/app/services/position_adapter.py`
- `backend/app/services/domains/hedging_strategy.py`
- `backend/app/services/hedging_legs.py`
- `backend/app/services/quantark.py`

Tools and agents:

- `backend/app/tools/products.py`
- `backend/app/tools/positions.py`
- `backend/app/tools/product_term_schema.py`
- `backend/app/tools/term_completeness.py`
- `backend/app/tools/_product_inputs.py`
- `backend/app/tools/__init__.py`
- `backend/app/services/agents.py`
- `backend/app/services/deep_agent/prompts/orchestrator.md`
- `backend/app/services/deep_agent/prompts/trader.md`
- `backend/app/services/deep_agent/prompts/high_board.md`
- `backend/app/services/deep_agent/prompts/risk_manager.md`
- `backend/app/services/deep_agent/hitl.py`
- `backend/app/services/gateway/cards.py`

Skills and workflows:

- `backend/app/skills/workflows/positions/book-position/SKILL.md`
- `backend/app/skills/workflows/products/build-product/SKILL.md`
- `backend/app/skills/references/products/build-contract.md`
- maturity-bearing product reference documents
- RFQ and hedge workflow references
- `backend/app/golden_workflows/definitions/`

Frontend:

- `frontend/src/routes/Booking.live.tsx`
- `frontend/src/components/ProductTermsForm.tsx`
- `frontend/src/components/BookingPricingCompanion.tsx`
- `frontend/src/lib/rfqProductFields.ts`
- `frontend/src/components/RfqQuoteForm.tsx`
- `frontend/src/routes/ClientRfq.tsx`
- position-edit and API type/client surfaces
- corresponding Vitest suites

Tests:

- new pure lifecycle resolver and migration tests
- existing product builder, booking, contract, model, RFQ, import, hedging,
  agent-tool, API, golden-workflow, pricing, and frontend suites
