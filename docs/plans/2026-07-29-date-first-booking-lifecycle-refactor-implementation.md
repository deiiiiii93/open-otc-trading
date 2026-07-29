# Date-First Booking Lifecycle Refactor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make every new Booking, RFQ, import, and hedge Product date-based; calculate remaining maturity from an explicit `as_of`; and migrate existing booked Products through an evidence-backed, restartable replacement ledger.

**Architecture:** Add one deterministic lifecycle resolver before schedule synthesis, QuantArk validation, product hashing, and persistence. All ingress channels may accept calendar tenor shorthand but persist only canonical lifecycle dates; legacy numeric Products remain temporarily readable while a migration service creates replacement date-based Products, repoints Positions, and quarantines ambiguous rows.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLAlchemy 2, Alembic, SQLite/PostgreSQL-compatible SQL, QuantArk 0.3.0 calendar/product APIs, React 19, TypeScript, Vitest, Testing Library, pytest.

---

## Source of truth and execution context

Implement against:

- `docs/plans/2026-07-29-date-first-booking-lifecycle-refactor-design.md`

Use the prepared worktree:

```bash
cd /Users/fuxinyao/open-otc-trading/.claude/worktrees/date-first-booking-lifecycle
git branch --show-current
# expected: codex/date-first-booking-lifecycle
git status --short
# expected before implementation: only this implementation plan after it is committed
```

The worktree uses the main checkout's Python environment:

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle.py -q
```

`frontend/node_modules` is an ignored local symlink to the main checkout's
installed dependencies. Run frontend checks normally from the worktree:

```bash
npm --prefix frontend test -- --run \
  src/routes/Booking.live.test.tsx
npm --prefix frontend exec tsc -- --noEmit
```

Before every commit:

```bash
git diff --check
git status --short
```

Stage only the files listed by the current task. Do not absorb unrelated main
checkout changes.

## Locked implementation resolutions

1. **Canonical write vocabulary is date-only.** `maturity` and
   `maturity_years` are rejected by all executable new-write paths.
2. **Tenor is a string command input.** Accept positive whole-unit labels such as
   `10D`, `2W`, `6M`, `18M`, and `1Y`; do not accept fractional-year floats.
3. **Resolution precedes identity.** Lifecycle resolution and schedule
   validation occur before `product_term_hash` or Product reuse.
4. **Economic anchors are explicit.** Schedule-bearing products use
   `initial_date`; other expiring products use `trade_effective_date`.
   UI/agent code may prefill those dates from the accounting date but the domain
   call must receive the concrete date.
5. **V1 settlement defaults preserve current behavior.** The server-owned
   convention registry uses zero business-day lag for OTC option families,
   because current builders and booked fixtures settle on exercise. An explicit
   settlement date overrides it. The registry is isolated so a desk-approved
   market/product-specific lag can change without changing resolution logic.
6. **Calendar selection is server-owned.** Use QuantArk calendars:
   China/SSE for Chinese exchanges or CNY, US for USD, TARGET for EUR, UK for
   GBP, and weekend-only only when no supported market mapping exists. Record
   the selected calendar in resolution provenance.
7. **Default expiry and settlement roll are FOLLOWING; default day count is
   ACT/365.** An explicit convention object remains injectable in tests and
   future configuration.
8. **Exercise controls option maturity.** Settlement never substitutes for
   exercise. Futures use `maturity_date`; Spot has no expiry.
9. **No wall-clock reads in domain resolution.** Request boundaries supply the
   accounting/effective/as-of date.
10. **Date-aware QuantArk remains authoritative for pricing T.** Open OTC may
    render a read-model maturity using the same QuantArk day-count/calendar
    utilities but never writes it to Product terms.
11. **Asian/Range Accrual settlement is retained.** Until QuantArk constructors
    expose it, `_build_termsheet` removes `settlement_date` only for those two
    classes, reapplies it as `_otc_settlement_date`, and keeps the canonical
    persisted term unchanged.
12. **Historical RFQ evidence is immutable.** New executable quote versions are
    date-only; old quote JSON is not rewritten.
13. **Legacy migration replaces Products.** Never mutate an existing Product's
    economic terms or hash in place.
14. **Ambiguity is a business result.** Migration status `quarantined` is
    terminal for an attempt, inspectable, and not treated as a successful
    conversion.
15. **Column removal is post-soak work.** This branch adds the zero-legacy gate
    and telemetry but does not drop legacy numeric columns. A later migration is
    authorized only after the operational gate is green.

## Delivery sequence

1. Pure lifecycle resolver and conventions.
2. Absolute-date schedule synthesis.
3. Date-first family contracts and tools schema.
4. Scalar, touch, futures, scheduled, and autocallable builders.
5. Central Booking enforcement and Product identity.
6. API/read models and human Booking.
7. RFQ, import, and hedge ingress parity.
8. Agent tools, prompts, skills, HITL, and golden workflows.
9. Durable legacy migration and cutover telemetry.
10. Full regression and documentation.

---

### Task 1: Add the pure lifecycle resolver and convention registry

**Files:**

- Create: `backend/app/services/domains/product_lifecycle.py`
- Test: `tests/test_product_lifecycle.py`

**Step 1: Write failing tenor and explicit-date tests**

Add table-driven tests covering scalar, schedule, futures, and spot semantics:

```python
from datetime import date

import pytest

from app.services.domains.product_lifecycle import (
    LifecycleError,
    resolve_default_conventions,
    resolve_product_lifecycle,
)


def test_scalar_tenor_resolves_from_trade_effective_date():
    conventions = resolve_default_conventions(
        quantark_class="EuropeanVanillaOption",
        underlying="MSFT",
        currency="USD",
    )
    result = resolve_product_lifecycle(
        "EuropeanVanillaOption",
        trade_effective_date=date(2026, 7, 29),
        tenor="1Y",
        conventions=conventions,
    )
    assert result.canonical_terms == {
        "exercise_date": "2027-07-29",
        "settlement_date": "2027-07-29",
    }
    assert result.anchor_date == date(2026, 7, 29)
    assert result.source.value == "tenor_from_trade_effective_date"


def test_schedule_tenor_requires_initial_date():
    with pytest.raises(LifecycleError) as exc:
        resolve_product_lifecycle(
            "SnowballOption",
            trade_effective_date=date(2026, 7, 29),
            tenor="1Y",
            conventions=resolve_default_conventions(
                quantark_class="SnowballOption",
                underlying="000905.SH",
                currency="CNY",
            ),
        )
    assert exc.value.code == "lifecycle_missing_anchor"


def test_futures_uses_maturity_date():
    result = resolve_product_lifecycle(
        "Futures",
        trade_effective_date=date(2026, 7, 29),
        tenor="6M",
        conventions=resolve_default_conventions(
            quantark_class="Futures",
            underlying="IF2612.CFFEX",
            currency="CNY",
        ),
    )
    assert result.canonical_terms == {"maturity_date": "2027-01-29"}
```

Also cover:

- leap-day and end-of-month addition,
- `10D`, `2W`, `18M`,
- invalid `1.5Y`, numeric tenor, zero/negative tenor,
- explicit exercise plus explicit settlement,
- identical tenor/explicit exercise accepted,
- conflicting tenor/explicit exercise rejected,
- settlement before exercise rejected,
- numeric `maturity`/`maturity_years` rejected when present in source terms,
- Spot rejects expiry input and otherwise returns an empty lifecycle envelope,
- repeat calls produce byte-identical serialized provenance.

**Step 2: Run the tests and verify failure**

Run:

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle.py -q
```

Expected: FAIL because `product_lifecycle` does not exist.

**Step 3: Implement lifecycle types and errors**

Implement:

```python
class LifecycleError(ValueError):
    def __init__(self, code: str, message: str, *, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


class LifecycleResolutionSource(str, Enum):
    EXPLICIT_DATES = "explicit_dates"
    TENOR_FROM_INITIAL_DATE = "tenor_from_initial_date"
    TENOR_FROM_TRADE_EFFECTIVE_DATE = "tenor_from_trade_effective_date"
    NO_EXPIRY = "no_expiry"


@dataclass(frozen=True)
class LifecycleConventions:
    calendar_id: str
    expiry_roll: str = "following"
    settlement_lag_business_days: int | None = 0
    settlement_roll: str = "following"
    day_count_convention: str = "ACT_365"


@dataclass(frozen=True)
class LifecycleResolution:
    canonical_terms: dict[str, str]
    anchor_date: date | None
    expiry_field: str | None
    expiry_date: date | None
    settlement_date: date | None
    source: LifecycleResolutionSource
    conventions: LifecycleConventions | None
    input_tenor: str | None
```

Use a strict compiled regex for tenor. Add days/weeks with `timedelta`; add
months/years with the existing month-clamping semantics. Use QuantArk
`Calendar.adjust_date` and `add_business_days` through a private cached calendar
factory.

Implement family sets for schedule products, futures, and spot. Do not infer a
family from loose words inside this module; callers pass the QuantArk class.

**Step 4: Implement and test the convention registry**

`resolve_default_conventions` selects a QuantArk calendar from symbol suffix,
currency, and known Chinese exchanges. It returns `settlement_lag_business_days
= None` for Futures/Spot and `0` for OTC option families.

Add tests proving MSFT selects US, `000905.SH` and `IF2612.CFFEX` select
CHINA_SSE, EUR selects TARGET, and unknown currency selects NONE with the choice
recorded.

**Step 5: Run the focused tests**

Run the Task 1 command again.

Expected: PASS.

**Step 6: Commit**

```bash
git add backend/app/services/domains/product_lifecycle.py tests/test_product_lifecycle.py
git commit -m "feat(booking): add deterministic lifecycle resolver"
```

---

### Task 2: Add absolute-date schedule synthesis

**Files:**

- Modify: `backend/app/services/domains/schedules.py:15-184`
- Modify: `tests/test_schedules.py`
- Modify: `tests/test_asian_schedules.py`

**Step 1: Write failing end-date schedule tests**

Add tests for new date-native functions:

```python
def test_periodic_dates_end_exactly_on_exercise():
    dates = periodic_observation_dates_between(
        start=date(2026, 7, 29),
        end=date(2027, 7, 29),
        lockup_months=3,
        months_step=1,
    )
    assert dates[-1] == date(2027, 7, 29)


def test_asian_records_never_exceed_exercise():
    records = asian_observation_records_between(
        start=date(2026, 7, 29),
        end=date(2027, 7, 29),
        frequency="MONTHLY",
    )
    assert records[-1]["observation_date"] == date(2027, 7, 29)
    assert all(r["observation_date"] <= date(2027, 7, 29) for r in records)
```

Cover holiday rolling that lands after the unadjusted endpoint, duplicate dates,
lockup beyond expiry, weights after deduplication, daily and weekly schedules,
and a caller-supplied terminal date.

**Step 2: Run the schedule tests and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_schedules.py tests/test_asian_schedules.py -q
```

Expected: FAIL because the `*_between` helpers do not exist.

**Step 3: Implement date-native helpers**

Add:

```python
def periodic_observation_dates_between(
    *,
    start: date,
    end: date,
    lockup_months: int,
    months_step: int = 1,
    day_of_month: int | None = None,
) -> list[date]:
    if end <= start:
        raise ValueError("end must be after start")
    if roll_to_business_day(end) != end:
        raise ValueError("end must already be business-day adjusted")
    anchor = start if day_of_month is None else start.replace(
        day=min(day_of_month, calendar.monthrange(start.year, start.month)[1])
    )
    rolled: list[date] = []
    month = lockup_months
    while True:
        candidate = add_months(anchor, month)
        if candidate > end:
            break
        adjusted = roll_to_business_day(candidate)
        if adjusted <= end:
            rolled.append(adjusted)
        month += months_step
    rolled.append(end)
    return list(dict.fromkeys(rolled))


def asian_observation_records_between(
    *,
    start: date,
    end: date,
    frequency: str,
    weights: list[float] | None = None,
) -> list[dict]:
    freq = frequency.upper()
    if freq == "DAILY":
        dates = china_sse_business_days(start + timedelta(days=1), end)
    elif freq == "WEEKLY":
        anchors = []
        current = start + timedelta(days=7)
        while current <= end:
            anchors.append(current)
            current += timedelta(days=7)
        dates = [roll_to_business_day(day) for day in anchors]
        dates = [day for day in dates if day <= end]
        dates.append(end)
    elif freq in FREQUENCY_MONTHS:
        step = FREQUENCY_MONTHS[freq]
        dates = periodic_observation_dates_between(
            start=start,
            end=end,
            lockup_months=step,
            months_step=step,
        )
    else:
        raise ValueError(f"unsupported averaging frequency: {frequency!r}")
    dates = list(dict.fromkeys(dates))
    if weights is not None and len(weights) != len(dates):
        raise ValueError("weights length does not match observation count")
    return [
        {
            "observation_date": day,
            "sequence": index + 1,
            "weight": weights[index] if weights is not None else None,
        }
        for index, day in enumerate(dates)
    ]
```

Generate unadjusted anchors through `end`, roll them, remove rolled dates after
the contractual end, and append the rolled contractual end when the frequency
would otherwise miss it. Deduplicate only after rolling. Reject `end <= start`.

Keep existing numeric helpers temporarily as compatibility wrappers for legacy
tests and migration classification. Mark them with comments that no new builder
may call them.

**Step 4: Run the schedule tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/domains/schedules.py tests/test_schedules.py tests/test_asian_schedules.py
git commit -m "refactor(schedules): synthesize observations from absolute dates"
```

---

### Task 3: Publish the date-first family contract

**Files:**

- Modify: `backend/app/services/domains/product_contracts.py:101-380`
- Modify: `backend/app/tools/product_term_schema.py`
- Modify: `backend/app/tools/term_completeness.py:64-145`
- Modify: `tests/test_product_contracts.py`
- Modify: `tests/test_product_term_schema.py`
- Modify: `tests/test_tools_products.py`

**Step 1: Replace numeric contract expectations with date/tenor alternatives**

Write failing tests asserting:

```python
contract = contract_for("SnowballOption")
field_names = {field.name for field in contract.fields}
assert {"tenor", "initial_date", "exercise_date", "settlement_date"} <= field_names
assert "maturity_years" not in field_names
```

For option families, term completeness accepts either:

- `exercise_date`, or
- `tenor` plus the required economic anchor supplied in the tool envelope.

For Futures, it accepts `maturity_date` or tenor. For Spot, it publishes no
expiry group. Add a test that `exercise_date` is recognized directly instead of
being reported missing because the old schema names only `maturity_date`.

Add conflict tests proving `maturity`, `maturity_years`, or conflicting
tenor/date representations produce `lifecycle_mixed_expiry`.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_contracts.py tests/test_product_term_schema.py \
  tests/test_tools_products.py -q
```

Expected: FAIL on the old numeric field contract.

**Step 3: Implement canonical FieldSpecs**

Replace `_MATURITY_YEARS`, `_MATURITY_DATE`, and the numeric `_TENOR` with:

```python
_TENOR = FieldSpec(
    "tenor", "string",
    "Calendar tenor shorthand such as 6M or 1Y; resolved and not persisted.",
    one_of="expiry",
)
_EXERCISE_DATE = FieldSpec(
    "exercise_date", "date",
    "Final exercise or observation date.",
    one_of="expiry",
)
_SETTLEMENT_DATE = FieldSpec(
    "settlement_date", "date",
    "Optional explicit settlement override; otherwise server-derived.",
)
_FUTURES_MATURITY_DATE = FieldSpec(
    "maturity_date", "date",
    "Contract maturity date.",
    one_of="expiry",
)
```

Add `initial_date` to schedule-bearing family contracts. Update
`required_bound`, conditional requirements, schema descriptions, and
`one_of_groups`.

Term completeness remains a syntactic preflight. It must not perform date
arithmetic; it returns `requires_anchor=true` when tenor is present.

**Step 4: Run the contract/tool tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py \
  backend/app/tools/product_term_schema.py backend/app/tools/term_completeness.py \
  tests/test_product_contracts.py tests/test_product_term_schema.py tests/test_tools_products.py
git commit -m "refactor(products): publish date-first lifecycle contract"
```

---

### Task 4: Convert scalar, touch, and futures builders to dates

**Files:**

- Modify: `backend/app/services/domains/product_builders.py:156-224`
- Modify: `backend/app/services/domains/product_builders.py:448-560`
- Modify: `tests/test_product_builders.py`

**Step 1: Write the failing builder matrix**

Parameterize:

- European
- American
- Cash Digital
- Barrier
- Single/Double Sharkfin scalar envelope
- OneTouch/DoubleOneTouch
- Futures
- Spot

For each expiring option, supply `exercise_date` and `settlement_date`, assert
the builder retains them, and validate the QuantArk build. For Futures, assert
`maturity_date`. Add explicit rejection tests for `maturity` and
`maturity_years`.

Example:

```python
def test_one_touch_builds_from_dates_only():
    result = build_product(
        "OneTouchOption",
        {
            "barrier": 120.0,
            "barrier_direction": "UP",
            "cash_payoff": 10.0,
            "exercise_date": "2027-07-29",
            "settlement_date": "2027-07-29",
        },
    )
    assert result.ok
    assert result.product_kwargs["exercise_date"] == "2027-07-29"
    assert "maturity" not in result.product_kwargs
```

**Step 2: Run the focused builder tests**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_builders.py -q
```

Expected: FAIL on numeric-only touch/futures builders.

**Step 3: Remove numeric construction from the new builder path**

Refactor `_common_option` to require canonical dates:

```python
def _common_option(terms: dict, out: _Out) -> dict:
    reject_numeric_lifecycle_terms(terms)
    exercise_date = _required_iso_date(terms, "exercise_date", out)
    settlement_date = _optional_iso_date(terms, "settlement_date", out)
    return {
        "exercise_date": exercise_date,
        **({"settlement_date": settlement_date} if settlement_date else {}),
    }
```

Convert touch builders to use it. Convert Futures to require `maturity_date`.
Spot rejects lifecycle keys. Keep persistence-only `_otc_` fields unchanged.

Do not resolve tenor in builders; the lifecycle resolver is the only layer that
does calendar arithmetic.

**Step 4: Run the builder suite**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "refactor(products): build scalar products from lifecycle dates"
```

---

### Task 5: Convert Asian and Range Accrual builders and retain settlement

**Files:**

- Modify: `backend/app/services/domains/product_builders.py:562-674`
- Modify: `backend/app/services/quantark.py:757-829`
- Modify: `tests/test_product_builders.py`
- Modify: `tests/test_asian_pricing_wiring.py`
- Modify: `tests/test_position_pricer_adaptive.py`

**Step 1: Add failing dated-schedule and settlement tests**

For Asian and Range Accrual:

- supply `initial_date`, `exercise_date`, and `settlement_date`,
- materialize records from the date-native schedule helpers,
- assert the last observation equals exercise,
- assert canonical output retains settlement,
- build a QuantArk product successfully,
- assert `_otc_settlement_date` is present on the constructed product.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_builders.py tests/test_asian_pricing_wiring.py \
  tests/test_position_pricer_adaptive.py -q
```

Expected: FAIL because builders still require `maturity_years`, and QuantArk
constructors reject settlement.

**Step 3: Implement date-native builders**

Replace count/maturity synthesis with:

```python
records = asian_observation_records_between(
    start=_required_date(terms, "initial_date"),
    end=_required_date(terms, "exercise_date"),
    frequency=frequency,
    weights=weights,
)
```

Range Accrual uses the same absolute interval for its observation records.
Preserve explicit records only after checking their terminal date and bounds.

**Step 4: Add the narrow QuantArk adapter**

In `_build_termsheet`, before constructing `RFQTermsheetInput`:

```python
if product_type in {"AsianOption", "RangeAccrualOption"}:
    settlement = normalized_product_kwargs.pop("settlement_date", None)
    if settlement is not None:
        otc_attrs["_otc_settlement_date"] = settlement
```

Do not remove settlement for any other class. The persisted canonical terms stay
unchanged.

**Step 5: Run the focused tests**

Expected: PASS.

**Step 6: Commit**

```bash
git add backend/app/services/domains/product_builders.py \
  backend/app/services/quantark.py tests/test_product_builders.py \
  tests/test_asian_pricing_wiring.py tests/test_position_pricer_adaptive.py
git commit -m "refactor(products): build scheduled options from expiry dates"
```

---

### Task 6: Convert Snowball, Phoenix, and KO-reset builders to dates

**Files:**

- Modify: `backend/app/services/domains/product_builders.py:225-447`
- Modify: `tests/test_product_builders.py`
- Modify: `tests/test_product_booking.py`
- Modify: `tests/test_position_pricer_grid.py`

**Step 1: Add failing autocallable lifecycle tests**

Replace `_snowball_terms()` numeric fixtures with:

```python
{
    "initial_date": "2026-07-29",
    "exercise_date": "2027-07-29",
    "settlement_date": "2027-07-29",
    "initial_price": 100.0,
    "strike": 100.0,
    "lockup_months": 3,
    "ko_barrier": 103.0,
    "ki_barrier": 75.0,
    "ko_rate": 0.12,
}
```

Assert:

- KO/KI records are within `[initial_date, exercise_date]`,
- the final contractual record equals exercise,
- `product_kwargs` contains all three lifecycle dates,
- neither `maturity` nor `maturity_years` exists,
- Phoenix and KO-reset preserve their product-specific configs,
- conflicting prebuilt schedule endpoints fail with
  `lifecycle_schedule_end_mismatch`.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_builders.py tests/test_product_booking.py \
  tests/test_position_pricer_grid.py -q
```

Expected: FAIL because `_build_snowball` still requires numeric maturity.

**Step 3: Refactor `_build_snowball`**

Use `initial_date` and `exercise_date` for schedule generation. Always persist
`exercise_date`; stop deriving only a local exercise date while retaining
`maturity`. Validate prebuilt config records with a shared
`validate_schedule_envelope` helper.

Phoenix and KO-reset continue delegating to the shared Snowball core, so the
shared helper owns lifecycle forwarding once.

**Step 4: Run the focused suites**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py \
  tests/test_product_builders.py tests/test_product_booking.py \
  tests/test_position_pricer_grid.py
git commit -m "refactor(autocallables): persist absolute lifecycle dates"
```

---

### Task 7: Enforce lifecycle normalization before Product hashing

**Files:**

- Modify: `backend/app/services/domains/booking.py:27-305`
- Modify: `backend/app/services/domains/products.py:113-140`
- Modify: `backend/app/services/domains/products.py:471-520`
- Modify: `backend/app/services/domains/products.py:934-970`
- Modify: `backend/app/services/domains/position_terms.py:48-105`
- Modify: `tests/test_product_booking.py`
- Modify: `tests/test_product_models.py`
- Modify: `tests/test_structured_position_terms.py`

**Step 1: Write failing normalization and identity tests**

Add:

```python
def test_tenor_and_explicit_date_reuse_same_product(session, container):
    first = book_position(
        session,
        _request(
            container.id,
            trade_effective_date=datetime(2026, 7, 29),
            terms={"strike": 100, "option_type": "CALL", "tenor": "1Y"},
        ),
    )
    second = book_position(
        session,
        _request(
            container.id,
            trade_effective_date=datetime(2026, 7, 29),
            terms={
                "strike": 100,
                "option_type": "CALL",
                "exercise_date": "2027-07-29",
                "settlement_date": "2027-07-29",
            },
        ),
    )
    assert first.product_id == second.product_id
    assert first.product.raw_terms["terms"]["exercise_date"] == "2027-07-29"
    assert "tenor" not in first.product.raw_terms["terms"]
```

Also test:

- two settlement dates create different hashes,
- numeric maturity fails before Product insert,
- missing trade effective/initial anchor fails,
- booking source payload records resolution provenance,
- structured rows store dates and null numeric columns,
- a failed resolution creates no Product or Position.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_booking.py tests/test_product_models.py \
  tests/test_structured_position_terms.py -q
```

Expected: FAIL because Booking does not pass its trade date into normalization.

**Step 3: Thread the lifecycle envelope through Booking**

Change:

```python
prepare_booking_product_spec(
    product,
    *,
    trade_effective_date,
    engine_name,
) -> ProductBookingSpec
```

It must:

1. reject numeric lifecycle keys,
2. resolve conventions,
3. resolve tenor/explicit dates,
4. merge canonical dates into product terms,
5. remove tenor and aliases,
6. call `build_product`,
7. attach resolution provenance to source payload.

`book_position` passes `request.trade_effective_date` and calls
`create_or_get_product` only after this returns.

Make `_write_option_terms` and `_write_futures_terms` treat date columns as
canonical and leave legacy numeric columns null for new rows.

**Step 4: Run the focused tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/domains/booking.py \
  backend/app/services/domains/products.py \
  backend/app/services/domains/position_terms.py \
  tests/test_product_booking.py tests/test_product_models.py \
  tests/test_structured_position_terms.py
git commit -m "refactor(booking): normalize lifecycle before product identity"
```

---

### Task 8: Add lifecycle preview and calculated maturity read models

**Files:**

- Modify: `backend/app/schemas.py:313-330`
- Modify: `backend/app/schemas.py:717-780`
- Modify: `backend/app/main.py:362-390`
- Modify: `backend/app/main.py:1423-1460`
- Modify: `backend/app/main.py:1708-1740`
- Modify: `backend/app/services/domains/product_lifecycle.py`
- Test: `tests/test_product_lifecycle_api.py`
- Modify: `tests/test_api.py`
- Modify: `tests/test_pricing_preview_endpoint.py`

**Step 1: Write failing API tests**

Cover:

- `POST /api/products/lifecycle/resolve`,
- `POST /api/portfolios/{id}/positions` with tenor and trade date,
- rejection of numeric maturity with typed 422 detail,
- Portfolio retrieval with `maturity_as_of=2026-07-29`,
- remaining maturity decreasing under a later `maturity_as_of`,
- dates present but derived maturity absent when no `as_of` is requested,
- expired lifecycle status instead of a clamped active T.

Expected output:

```json
{
  "exercise_date": "2027-07-29",
  "settlement_date": "2027-07-29",
  "remaining_maturity_years": 1.0,
  "maturity_as_of": "2026-07-29",
  "day_count_convention": "ACT_365",
  "status": "active"
}
```

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle_api.py tests/test_api.py \
  tests/test_pricing_preview_endpoint.py -q
```

Expected: FAIL because the endpoint and schemas do not exist.

**Step 3: Add typed request/response models**

Add:

- `LifecycleResolveRequest`
- `LifecycleResolutionOut`
- `PositionLifecycleOut`
- `PositionOut.lifecycle`

Remove numeric maturity from `PortfolioPositionSpec` defaults. Add a structured
Pydantic error translator for `LifecycleError` returning code, message, and
field.

**Step 4: Implement the pure read-model calculation**

Add:

```python
def lifecycle_view(
    *,
    quantark_class: str,
    terms: Mapping[str, Any],
    as_of: date | None,
    conventions: LifecycleConventions,
) -> dict[str, Any]:
    expiry_key = "maturity_date" if quantark_class == "Futures" else "exercise_date"
    expiry_raw = terms.get(expiry_key)
    settlement_raw = terms.get("settlement_date")
    payload: dict[str, Any] = {
        expiry_key: expiry_raw,
        "settlement_date": settlement_raw,
        "remaining_maturity_years": None,
        "maturity_as_of": as_of.isoformat() if as_of else None,
        "day_count_convention": conventions.day_count_convention,
        "status": "active" if expiry_raw else "not_applicable",
    }
    if not expiry_raw or as_of is None:
        return payload
    expiry = date.fromisoformat(str(expiry_raw))
    if as_of >= expiry:
        payload["remaining_maturity_years"] = 0.0
        payload["status"] = "expired"
        return payload
    payload["remaining_maturity_years"] = calculate_lifecycle_year_fraction(
        as_of=as_of,
        expiry=expiry,
        conventions=conventions,
    )
    return payload
```

Use QuantArk `calculate_year_fraction` with the selected calendar/day count.
At or after expiry, return a terminal status and no positive active maturity.

Thread optional `maturity_as_of` through `_portfolio_response`; write endpoints
use the request's explicit trade/accounting date instead of wall clock.

**Step 5: Run focused tests**

Expected: PASS.

**Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py \
  backend/app/services/domains/product_lifecycle.py \
  tests/test_product_lifecycle_api.py tests/test_api.py \
  tests/test_pricing_preview_endpoint.py
git commit -m "feat(api): expose resolved lifecycle and calculated maturity"
```

---

### Task 9: Refactor the human Booking and position-term UI

**Files:**

- Modify: `frontend/src/main.tsx:96-300`
- Modify: `frontend/src/routes/Booking.live.tsx:58-455`
- Modify: `frontend/src/components/ProductTermsForm.tsx:20-165`
- Modify: `frontend/src/components/ProductTermsForm.tsx:991-1055`
- Modify: `frontend/src/components/BookingPricingCompanion.tsx`
- Modify: `frontend/src/components/PositionEditForm.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/routes/Booking.live.test.tsx`
- Modify: `frontend/src/components/ProductTermsForm.test.tsx`
- Modify: `frontend/src/components/BookingPricingCompanion.test.tsx`

**Step 1: Stabilize the existing Booking test baseline**

The current tests compare formatted numeric inputs as raw numbers and fail when
the thousand-separator preference renders `3,888.12`. Update helpers to parse
displayed numeric values or disable the separator explicitly in test setup.
Repair the stale package-component control query separately. Run the existing
Booking tests until they are green before adding lifecycle assertions.

Run:

```bash
npm --prefix frontend test -- --run \
  src/routes/Booking.live.test.tsx \
  src/components/ProductTermsForm.test.tsx
```

Expected after test-only stabilization: PASS with no application behavior
change.

Commit the isolated test repair:

```bash
git add frontend/src/routes/Booking.live.test.tsx \
  frontend/src/components/ProductTermsForm.test.tsx
git commit -m "test(booking): stabilize formatted input assertions"
```

**Step 2: Write failing date-first UI tests**

Assert:

- `BookingLive` receives `accountingDate`,
- trade effective date defaults to it,
- schedule products default initial date from it,
- optional Tenor accepts `1Y`,
- no editable Maturity field exists for any product,
- Futures shows Maturity Date,
- lifecycle preview renders exercise, settlement, T, and `as_of`,
- submit sends trade date plus tenor or canonical dates, never numeric maturity,
- legacy `maturity` is not rendered in extra fields,
- position edit uses the same date contract.

**Step 3: Run and verify failure**

Run the Task 9 frontend command plus
`BookingPricingCompanion.test.tsx`.

Expected: FAIL on missing props and legacy fields.

**Step 4: Implement the UI contract**

Pass `accountingDate` from `main.tsx`. Extend Booking state with
`tradeEffectiveDate`, optional `tenor`, and preview state. Call the server
lifecycle preview after relevant fields change; never calculate business dates
in TypeScript.

Use the returned canonical terms for pricing preview and submission. Display
remaining maturity as:

```text
T 0.9973 · as of 2026-07-29 · ACT/365
```

Replace OneTouch, Range Accrual, Futures, flat Snowball, and legacy extra-field
maturity controls with lifecycle date/tenor controls.

**Step 5: Run frontend tests and typecheck**

```bash
npm --prefix frontend test -- --run \
  src/routes/Booking.live.test.tsx \
  src/components/ProductTermsForm.test.tsx \
  src/components/BookingPricingCompanion.test.tsx
npm --prefix frontend exec tsc -- --noEmit
```

Expected: PASS.

**Step 6: Commit**

```bash
git add frontend/src/main.tsx frontend/src/routes/Booking.live.tsx \
  frontend/src/components/ProductTermsForm.tsx \
  frontend/src/components/BookingPricingCompanion.tsx \
  frontend/src/components/PositionEditForm.tsx frontend/src/types.ts \
  frontend/src/routes/Booking.live.test.tsx \
  frontend/src/components/ProductTermsForm.test.tsx \
  frontend/src/components/BookingPricingCompanion.test.tsx
git commit -m "feat(booking): replace maturity input with lifecycle dates"
```

---

### Task 10: Make RFQ versions and booking date-first

**Files:**

- Modify: `backend/app/services/rfq.py:51-285`
- Modify: `backend/app/services/rfq.py:884-930`
- Modify: `backend/app/services/rfq.py:1238-1385`
- Modify: `backend/app/schemas.py:392-415`
- Modify: `frontend/src/lib/rfqProductFields.ts`
- Modify: `frontend/src/components/RfqQuoteForm.tsx`
- Modify: `frontend/src/routes/ClientRfq.tsx`
- Modify: `tests/test_services_domains_rfq.py`
- Modify: `tests/test_rfq_quote_overrides.py`
- Modify: `tests/test_tools_rfq.py`
- Modify: `frontend/src/components/RfqQuoteForm.test.tsx`
- Modify: `frontend/src/routes/ClientRfq.test.tsx`

**Step 1: Add failing immutable-date RFQ tests**

Prove:

- templates contain `tenor`, never numeric maturity,
- creating an executable quote version resolves dates,
- accepted booking reuses those exact dates after accounting date advances,
- a quote override may explicitly change dates but cannot reintroduce numeric
  maturity,
- RFQ UI has no numeric maturity control.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_services_domains_rfq.py tests/test_rfq_quote_overrides.py \
  tests/test_tools_rfq.py -q
npm --prefix frontend test -- --run \
  src/components/RfqQuoteForm.test.tsx src/routes/ClientRfq.test.tsx
```

Expected: FAIL on numeric templates and quote translation.

**Step 3: Resolve lifecycle before executable version persistence**

Replace numeric `COMMON_TEMPLATES`. Natural-language tenor extraction writes
`tenor: "1Y"` rather than `maturity: 1.0`. `_executable_terms_for_quote`
resolves lifecycle using the RFQ trade/economic start and stores canonical
dates.

`_booking_terms` returns already-resolved terms; `book_rfq_to_position` validates
but never resolves the historical tenor again.

**Step 4: Update RFQ frontend fields**

Remove `maturity` from `NUMERIC_KEYS` and alias maps. Add tenor/date field
definitions and server preview use. Remove the fallback template's numeric
maturity.

**Step 5: Run backend/frontend focused tests**

Expected: PASS.

**Step 6: Commit**

```bash
git add backend/app/services/rfq.py backend/app/schemas.py \
  frontend/src/lib/rfqProductFields.ts frontend/src/components/RfqQuoteForm.tsx \
  frontend/src/routes/ClientRfq.tsx tests/test_services_domains_rfq.py \
  tests/test_rfq_quote_overrides.py tests/test_tools_rfq.py \
  frontend/src/components/RfqQuoteForm.test.tsx frontend/src/routes/ClientRfq.test.tsx
git commit -m "refactor(rfq): freeze lifecycle dates in executable quotes"
```

---

### Task 11: Align import and hedge booking ingress

**Files:**

- Modify: `backend/app/services/position_adapter.py:605-720`
- Modify: `backend/app/services/domains/hedging_strategy.py:529-610`
- Modify: `backend/app/services/hedging_legs.py`
- Modify: `tests/test_position_import_pricing.py`
- Modify: `tests/test_import_templates.py`
- Modify: `tests/test_hedging_legs.py`
- Modify: `tests/test_hedging_book.py`
- Modify: `tests/test_hedging_domain.py`

**Step 1: Add failing ingress-parity tests**

Book equivalent vanilla economics through manual domain Booking, import, and
listed hedge booking. Assert identical canonical terms and term hash.

For listed contracts:

- option `Instrument.expiry` becomes `exercise_date`,
- futures `Instrument.expiry` becomes `maturity_date`,
- no `_maturity_years` conversion is called.

Import numeric-only expiry must return a typed mapping error; explicit
final-observation/maturity and settlement columns remain supported.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_position_import_pricing.py tests/test_import_templates.py \
  tests/test_hedging_legs.py tests/test_hedging_book.py \
  tests/test_hedging_domain.py -q
```

Expected: FAIL because hedge legs still derive numeric years.

**Step 3: Delegate both paths through canonical Booking**

Keep the import column mapping, but send mapped dates through
`prepare_booking_product_spec`. Remove hedge `_maturity_years`; carry absolute
instrument expiry by product type. Resolve settlement through the central
convention when not supplied.

**Step 4: Run focused tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/position_adapter.py \
  backend/app/services/domains/hedging_strategy.py \
  backend/app/services/hedging_legs.py tests/test_position_import_pricing.py \
  tests/test_import_templates.py tests/test_hedging_legs.py \
  tests/test_hedging_book.py tests/test_hedging_domain.py
git commit -m "refactor(booking): align import and hedge lifecycle dates"
```

---

### Task 12: Update agent tools and HITL confirmation

**Files:**

- Modify: `backend/app/tools/products.py`
- Modify: `backend/app/tools/positions.py:129-185`
- Modify: `backend/app/tools/positions.py:604-665`
- Modify: `backend/app/tools/_product_inputs.py`
- Modify: `backend/app/services/deep_agent/hitl.py:214-240`
- Modify: `backend/app/services/gateway/cards.py`
- Modify: `tests/test_agent_product_tool_contract.py`
- Modify: `tests/test_agent_tools.py`
- Modify: `tests/test_tools_positions.py`

**Step 1: Write failing typed-tool tests**

Add a `BuildProductInput` contract:

```python
class BuildProductInput(BaseModel):
    family: str
    terms: dict[str, Any]
    underlying: str
    currency: str | None = None
    trade_effective_date: date
```

Test:

- `build_product` resolves tenor and returns canonical dates plus provenance,
- missing anchor yields `lifecycle_missing_anchor`,
- `book_position` rejects numeric maturity,
- `book_position` revalidates a prior build result,
- HITL summary contains trade/initial, exercise/maturity, settlement, source
  tenor, calculated T/as-of, and engine.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_agent_product_tool_contract.py tests/test_agent_tools.py \
  tests/test_tools_positions.py -q
```

Expected: FAIL because the tool lacks lifecycle context and HITL omits dates.

**Step 3: Implement typed lifecycle-aware tools**

The tool wrapper resolves lifecycle and then calls the date-only domain builder.
`book_position` passes `trade_effective_date` through the authoritative Booking
service. Tool results expose structured errors, never prose-only failures.

Update gateway card required/display fields so the server surfaces canonical
dates even when an agent's prose omits them.

**Step 4: Run focused tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/tools/products.py backend/app/tools/positions.py \
  backend/app/tools/_product_inputs.py backend/app/services/deep_agent/hitl.py \
  backend/app/services/gateway/cards.py tests/test_agent_product_tool_contract.py \
  tests/test_agent_tools.py tests/test_tools_positions.py
git commit -m "feat(agent): expose date-first booking tool contract"
```

---

### Task 13: Update prompts, skills, and golden workflows

**Files:**

- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Modify: `backend/app/services/deep_agent/prompts/high_board.md`
- Modify: `backend/app/services/deep_agent/prompts/risk_manager.md`
- Modify: `backend/app/skills/workflows/positions/book-position/SKILL.md`
- Modify: `backend/app/skills/workflows/products/build-product/SKILL.md`
- Modify: `backend/app/skills/references/products/build-contract.md`
- Modify: `backend/app/skills/references/products/vanilla.md`
- Modify: `backend/app/skills/references/products/barrier.md`
- Modify: `backend/app/skills/references/products/digital-touch.md`
- Modify: `backend/app/skills/references/products/asian.md`
- Modify: `backend/app/skills/references/products/sharkfin.md`
- Modify: `backend/app/skills/references/products/snowball.md`
- Modify: `backend/app/skills/references/products/range-accrual.md`
- Modify: `backend/app/skills/references/products/delta-one.md`
- Modify: `backend/app/skills/references/rfq/lifecycle.md`
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md`
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json`
- Modify: other numeric-maturity golden fixtures under `backend/app/golden_workflows/definitions/`
- Modify: `tests/test_trader_rfq_workflow.py`
- Modify: `tests/test_golden_workflow_fixtures.py`
- Modify: `tests/test_golden_workflow_regression.py`

**Step 1: Add failing static contract assertions**

Add tests scanning active prompts, skills, and executable golden calls:

```python
FORBIDDEN_BOOKING_KEYS = {'"maturity"', '"maturity_years"'}

def test_active_booking_guidance_has_no_numeric_maturity_contract():
    for path in ACTIVE_BOOKING_GUIDANCE:
        text = path.read_text()
        assert not any(key in text for key in FORBIDDEN_BOOKING_KEYS)
```

Allow explanatory migration references only in the migration skill/doc, not in
active build/book examples.

Golden `build_product` and `book_position` calls must contain the accounting
date as trade effective, `exercise_date=2027-07-16`, and convention-derived
settlement.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_trader_rfq_workflow.py tests/test_golden_workflow_fixtures.py \
  tests/test_golden_workflow_regression.py -q
```

Expected: FAIL on current numeric examples.

**Step 3: Rewrite active guidance**

State consistently:

- accounting date may prefill a new trade date,
- economic start anchors tenor,
- deterministic tools resolve dates,
- agents never perform calendar arithmetic,
- accepted RFQ dates are reused exactly,
- no booking tool receives numeric maturity.

Update all fixture products that are executable booking evidence. Historical
prose may say “one-year tenor” but tool payloads must be date-based.

**Step 4: Run golden and skill tests**

Expected: PASS.

**Step 5: Commit**

Stage only the listed prompt, skill, fixture, and test files, then:

```bash
git commit -m "docs(agent): teach date-first booking lifecycle"
```

---

### Task 14: Add the durable legacy migration ledger

**Files:**

- Create: `backend/alembic/versions/0052_product_lifecycle_migrations.py`
- Modify: `backend/app/models.py:802-890`
- Test: `tests/test_product_lifecycle_migration_schema.py`

**Step 1: Write failing ORM and Alembic tests**

Define expected model fields:

```python
class ProductLifecycleMigration(Base):
    id: int
    source_product_id: int
    replacement_product_id: int | None
    status: str
    resolution_method: str | None
    evidence: dict
    canonical_terms: dict | None
    error_code: str | None
    created_at: datetime
    completed_at: datetime | None
```

Assert uniqueness on `source_product_id` so retries update the same ledger row.
Assert migration upgrade/downgrade from head `0051`.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle_migration_schema.py -q
```

Expected: FAIL because model/table do not exist.

**Step 3: Implement migration-local Core SQL**

Migration `0052_product_lifecycle_migrations` must use local `sa.Table`/DDL only;
never import ORM models or services. Add indexes on status, source, and
replacement. Foreign keys use `ON DELETE RESTRICT` for the source and
`SET NULL` for replacement.

Add ORM relationships only if they do not create ambiguous Product ownership;
plain indexed IDs are acceptable and simpler.

**Step 4: Run schema and full migration tests**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle_migration_schema.py tests/test_product_migration.py -q
```

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/alembic/versions/0052_product_lifecycle_migrations.py \
  backend/app/models.py tests/test_product_lifecycle_migration_schema.py
git commit -m "feat(migration): add product lifecycle replacement ledger"
```

---

### Task 15: Implement evidence-based Product replacement

**Files:**

- Create: `backend/app/services/domains/product_lifecycle_migration.py`
- Create: `scripts/migrate_product_lifecycle.py`
- Test: `tests/test_product_lifecycle_migration.py`
- Test: `tests/test_product_lifecycle_migration_cli.py`

**Step 1: Write failing classification tests**

Cover evidence priority:

1. existing explicit expiry,
2. final persisted schedule observation,
3. import/source payload,
4. accepted RFQ executable terms,
5. listed instrument expiry,
6. original tenor plus explicit start and known convention,
7. invertible settlement convention,
8. numeric-only ambiguity.

Expected classification:

```python
result = classify_product_lifecycle(session, product)
assert result.status == "migratable"
assert result.resolution_method == "final_observation"
assert result.canonical_terms["exercise_date"] == "2027-06-24"
```

For numeric-only:

```python
assert result.status == "quarantined"
assert result.error_code == "legacy_lifecycle_ambiguous"
```

**Step 2: Write failing replacement/idempotency tests**

Test:

- dry run never writes,
- apply creates/reuses a replacement Product,
- all Positions sharing the source are repointed transactionally,
- source Product remains unchanged,
- structured term rows and barrier state refresh,
- audit event links source and replacement,
- rerun returns the existing ledger/replacement,
- failure rolls back repoints and leaves an inspectable failed/quarantined
  ledger state in a separate short transaction.

**Step 3: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle_migration.py \
  tests/test_product_lifecycle_migration_cli.py -q
```

Expected: FAIL because service and CLI do not exist.

**Step 4: Implement pure classification then transactional apply**

Expose two explicit operations:

- `classify_product_lifecycle(session: Session, product: Product) ->
  LifecycleMigrationCandidate`, which reads evidence in the documented priority
  order and returns either canonical terms or a quarantine reason without
  writing.
- `apply_product_lifecycle_migration(session: Session, candidate:
  LifecycleMigrationCandidate, *, actor: str) ->
  ProductLifecycleMigration`, which creates/reuses the replacement, repoints all
  affected Positions, refreshes structured state, and records the ledger/audit
  rows in the caller's transaction.

Do not call `commit()` inside the domain function. The CLI owns short
transactions and resumes by ledger status.

CLI:

```bash
python scripts/migrate_product_lifecycle.py \
  --database-url sqlite:////tmp/open_otc_lifecycle.sqlite3 --dry-run
python scripts/migrate_product_lifecycle.py \
  --database-url sqlite:////tmp/open_otc_lifecycle.sqlite3 --apply
python scripts/migrate_product_lifecycle.py \
  --database-url sqlite:////tmp/open_otc_lifecycle.sqlite3 \
  --status quarantined --json-out /tmp/open_otc_lifecycle_quarantine.json
```

It must default to dry-run, print counts by resolution method/status, and require
`--apply` for writes.

**Step 5: Run focused tests**

Expected: PASS.

**Step 6: Run a read-only dry run against a copied database**

```bash
cp /Users/fuxinyao/open-otc-trading/data/open_otc.sqlite3 /tmp/open_otc_lifecycle_plan.sqlite3
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python \
  scripts/migrate_product_lifecycle.py \
  --database-url sqlite:////tmp/open_otc_lifecycle_plan.sqlite3 --dry-run
```

Expected: all 14 booked Positions classified; no replacement/ledger writes in
dry-run; numeric-only ambiguous rows reported explicitly.

**Step 7: Commit**

```bash
git add backend/app/services/domains/product_lifecycle_migration.py \
  scripts/migrate_product_lifecycle.py tests/test_product_lifecycle_migration.py \
  tests/test_product_lifecycle_migration_cli.py
git commit -m "feat(migration): replace legacy products from lifecycle evidence"
```

---

### Task 16: Add compatibility warnings and the zero-legacy cutover gate

**Files:**

- Modify: `backend/app/services/domains/products.py:327-410`
- Create: `backend/app/services/domains/product_lifecycle_cutover.py`
- Create: `backend/app/tools/product_lifecycle.py`
- Modify: `backend/app/tools/__init__.py`
- Modify: `backend/app/services/agents.py`
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Test: `tests/test_product_lifecycle_cutover.py`
- Modify: `tests/test_agent_tools.py`

**Step 1: Write failing gate and warning tests**

`lifecycle_cutover_status(session)` must report:

- new executable Products containing forbidden keys,
- live Positions referencing numeric-only Products,
- active RFQ executable terms with forbidden keys,
- quarantined live Positions,
- migration ledger counts,
- `ready_for_legacy_removal`.

Compatibility reads for an unmigrated numeric Product return:

```json
{
  "legacy_lifecycle": true,
  "warning": "numeric_maturity_pending_migration"
}
```

They must never rewrite the Product or claim a date.

**Step 2: Run and verify failure**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle_cutover.py tests/test_agent_tools.py -q
```

Expected: FAIL because the gate/tool do not exist.

**Step 3: Implement read-only status and tool**

Add `get_product_lifecycle_cutover_status` as a DOMAIN_READ tool. It reports
server-generated counts and sample IDs only; it performs no migration.

Keep compatibility serialization narrowly scoped to existing Product rows. New
write validation is unconditional and cannot be disabled by a feature flag.

**Step 4: Run focused tests**

Expected: PASS.

**Step 5: Commit**

```bash
git add backend/app/services/domains/products.py \
  backend/app/services/domains/product_lifecycle_cutover.py \
  backend/app/tools/product_lifecycle.py backend/app/tools/__init__.py \
  backend/app/services/agents.py backend/app/services/deep_agent/prompts/trader.md \
  tests/test_product_lifecycle_cutover.py tests/test_agent_tools.py
git commit -m "feat(booking): expose lifecycle migration cutover status"
```

---

### Task 17: Run cross-layer regression and update operator documentation

**Files:**

- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`
- Modify: `README.md`
- Modify: `docs/plans/2026-07-29-date-first-booking-lifecycle-refactor-design.md`
  only if implementation discovered and resolved a genuine design discrepancy
- Test: all affected suites

Use `@test-runner` discipline for the final matrix: record exact commands and
do not call the branch green if any required slice is skipped.

**Step 1: Run backend domain, ingress, tool, and migration suites**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_product_lifecycle.py \
  tests/test_schedules.py \
  tests/test_asian_schedules.py \
  tests/test_product_builders.py \
  tests/test_product_booking.py \
  tests/test_product_contracts.py \
  tests/test_product_models.py \
  tests/test_structured_position_terms.py \
  tests/test_product_term_schema.py \
  tests/test_product_lifecycle_api.py \
  tests/test_services_domains_rfq.py \
  tests/test_rfq_quote_overrides.py \
  tests/test_position_import_pricing.py \
  tests/test_hedging_legs.py \
  tests/test_hedging_book.py \
  tests/test_agent_product_tool_contract.py \
  tests/test_tools_positions.py \
  tests/test_tools_rfq.py \
  tests/test_product_lifecycle_migration_schema.py \
  tests/test_product_lifecycle_migration.py \
  tests/test_product_lifecycle_migration_cli.py \
  tests/test_product_lifecycle_cutover.py -q
```

Expected: PASS.

**Step 2: Run pricing aging proof**

Add/retain a regression that builds the same date-based Product at two valuation
dates and asserts later T is smaller for European, Barrier, Snowball, Asian,
Range Accrual, and Futures.

Run:

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_pricing_preview_endpoint.py tests/test_position_pricer_adaptive.py \
  tests/test_position_pricer_grid.py tests/test_asian_pricing_wiring.py -q
```

Expected: PASS.

**Step 3: Run agent/golden suites**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest \
  tests/test_agent_tools.py tests/test_trader_rfq_workflow.py \
  tests/test_golden_workflow_fixtures.py \
  tests/test_golden_workflow_regression.py -q
```

Expected: PASS.

**Step 4: Run frontend suites and typecheck**

```bash
npm --prefix frontend test -- --run \
  src/routes/Booking.live.test.tsx \
  src/components/ProductTermsForm.test.tsx \
  src/components/BookingPricingCompanion.test.tsx \
  src/components/RfqQuoteForm.test.tsx \
  src/routes/ClientRfq.test.tsx
npm --prefix frontend exec tsc -- --noEmit
```

Expected: PASS.

**Step 5: Run full backend and frontend regression**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest -q
npm --prefix frontend test -- --run
```

Expected: PASS. If the full frontend suite retains known environmental chart
warnings, warnings are acceptable; test failures are not.

**Step 6: Update documentation**

Document:

- date-first Product identity,
- tenor resolution and anchor policy,
- lifecycle preview/read fields,
- API/tool error codes,
- migration dry-run/apply commands,
- quarantine remediation,
- cutover status tool,
- the fact that numeric columns remain until a later post-soak migration.

Add an `[Unreleased]` CHANGELOG entry. Update the Booking/product subsystem
section in `CLAUDE.md`.

**Step 7: Run static checks**

```bash
PYTHONPATH=backend \
  /Users/fuxinyao/open-otc-trading/.venv/bin/python -m compileall -q \
  backend/app scripts/migrate_product_lifecycle.py
git diff --check
```

Expected: PASS.

**Step 8: Commit final documentation**

```bash
git add CHANGELOG.md CLAUDE.md README.md \
  docs/plans/2026-07-29-date-first-booking-lifecycle-refactor-design.md
git commit -m "docs: document date-first booking lifecycle operations"
```

---

## Post-implementation operational sequence

Do not run `--apply` against the user's live database as part of code
implementation or tests.

After merge and explicit operator approval:

1. Back up the live database.
2. Run the migration CLI in dry-run mode.
3. Review every evidence method and quarantined Product.
4. Apply only resolvable replacements.
5. Re-run pricing/risk reconciliation for every repointed Position.
6. Remediate or retire quarantined live Positions.
7. Observe zero new numeric writes for the agreed soak period.
8. Confirm `ready_for_legacy_removal=true`.
9. Write and review a separate Alembic plan to drop numeric columns and remove
   compatibility reads.

The post-soak removal is deliberately not pre-authorized by this implementation
plan.

## Implementation handoff

Execute tasks strictly in order. Each commit must leave its focused tests green.
Do not combine the migration apply operation with feature implementation, and
do not merge this branch into `main` until the complete regression matrix and
the copied-database dry run are reviewed.
