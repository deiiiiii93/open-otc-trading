# Unified Product Schema — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden `build_product` into the single canonical product producer — carrier with identity (`product_spec`), a data-driven per-family term contract, a hardened "already-built" discriminator (closes the P1a opaque-error bypass), `(quantark_class, components)`-derived family, and multi-frequency snowball schedule synthesis — without touching any channel (RFQ/try-solve/import).

**Architecture:** This is the **Foundation** step of the unified-product-schema strangler-fig (spec: `docs/superpowers/specs/2026-05-30-unified-product-schema-design.md`, migration step 1). Everything from `build_product` rightward becomes shared and singular; channels keep their own builders until their own later plans. The Foundation makes no channel/caller behavior changes beyond what the carrier migration forces, and lands the P1a regression test.

**Tech Stack:** Python 3, dataclasses, pytest. QuantArk validation via `validate_quantark_build` (`backend/app/services/quantark.py`). No DB writes, no migrations.

**Scope note (read before starting):** This plan covers spec migration step 1 only. The four channel migrations (RFQ, try-solve, OTC import, cleanup) are **separate follow-on plans**. Decisions 4 and 7 (thin adapters; RFQ→book resolution) are *enabled* here (malformed-reject + `solve_target` param) but *exercised* in those later plans.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/services/domains/product_contracts.py` | Per-family term contract as **data** (required-bound / defaulted / solvable keys) + solve-target exemption | **Create** |
| `backend/app/services/domains/product_builders.py` | The single producer: `BuildResult` carrier, `build_product` dispatch, snowball synthesis, hardened discriminator | **Modify** |
| `backend/app/services/domains/schedules.py` | Pure observation-date synthesis; gains multi-frequency generator | **Modify** |
| `backend/app/services/domains/products.py` | `ProductSpec`, `product_family_for_quantark_class` (single derivation point — already exists) | **Read/verify only** |
| `backend/app/services/domains/booking.py` | Booking repair guards on `built.ok` (carrier consequence) | **Modify (small)** |
| `backend/app/tools/products.py` | Agent `build_product` tool — explicit serialization (asdict drops the new property) | **Modify (small)** |
| `tests/test_product_contracts.py` | Contract data + exemption + builder↔contract consistency | **Create** |
| `tests/test_product_builders.py` | Carrier, hardening/P1a, multi-frequency, derivation | **Modify (append)** |
| `tests/test_schedules.py` | Periodic-date generator | **Modify (append)** |
| `tests/test_tools_products.py` | Tool output keeps flat `product_kwargs` + adds `product_spec` | **Modify (append)** |

---

## Task 1: Per-family term contract module

Declarative data backing `missing` reporting and the solve-target exemption. Starts with the snowball family (the one that matters); other families keep imperative `missing` until their channel plans. The builder↔contract **consistency test** binds them so they cannot silently drift.

**Files:**
- Create: `backend/app/services/domains/product_contracts.py`
- Test: `tests/test_product_contracts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_product_contracts.py`:

```python
from __future__ import annotations

from app.services.domains import product_contracts as pc
from app.services.domains.product_builders import build_product


def test_snowball_contract_declares_required_bound_frequency():
    contract = pc.contract_for("SnowballOption")
    assert contract is not None
    assert "observation_frequency" in contract.required_bound
    assert "initial_price" in contract.required_bound
    # the coupon is a legitimate RFQ solve target, so it is solvable
    assert "barrier_config.ko_rate" in contract.solvable


def test_filter_solved_exempts_the_designated_target_only():
    missing = ["barrier_config.ko_rate", "barrier_config.ki_barrier"]
    kept = pc.filter_solved(missing, solve_target="barrier_config.ko_rate")
    assert kept == ["barrier_config.ki_barrier"]


def test_filter_solved_noop_without_target():
    missing = ["barrier_config.ko_rate"]
    assert pc.filter_solved(missing, solve_target=None) == missing


def test_builder_missing_keys_are_declared_in_the_contract():
    """Consistency net: every key the snowball builder can report missing must be
    a declared required-bound contract key — so builder and contract cannot drift.
    """
    contract = pc.contract_for("SnowballOption")
    # An empty-terms snowball reports its full required set.
    result = build_product("SnowballOption", {})
    undeclared = [m for m in result.missing if m not in contract.required_bound]
    assert undeclared == [], undeclared
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_product_contracts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.domains.product_contracts'`

- [ ] **Step 3: Write minimal implementation**

Create `backend/app/services/domains/product_contracts.py`:

```python
"""Per-family term contracts — the declarative source of truth for which economic
inputs a family needs.

A contract is *data*: the required-bound inputs a channel must collect, the
defaulted inputs it may omit, and the fields that are eligible to be an RFQ /
try-solve free variable (the solve target). `build_product` reports anything
required-bound and absent in `missing`; the designated solve target is exempt
(it arrives as bounds + initial guess, not a value — see the unified-product
schema design, decision 6).

Only the snowball family is data-driven today; other families keep imperative
`missing` until their channel migration. The builder<->contract consistency test
(`tests/test_product_contracts.py`) prevents drift.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FamilyContract:
    quantark_class: str
    required_bound: tuple[str, ...]
    defaulted: tuple[str, ...]
    solvable: tuple[str, ...]


# Snowball / autocallable base. KO-reset and Phoenix build on the snowball
# synthesizer and add their own required keys imperatively, so they share this
# base contract for the shared inputs.
_SNOWBALL_CONTRACT = FamilyContract(
    quantark_class="SnowballOption",
    required_bound=(
        "initial_price",
        "maturity_years",
        "trade_start_date",
        "observation_frequency",
        "barrier_config.ko_barrier",
        "barrier_config.ki_barrier",
        "barrier_config.ko_rate",
        "barrier_config.lockup_months",
        "ko_observation_dates",  # only when observation_frequency == CUSTOM
    ),
    defaulted=(
        "ki_convention",
        "ko_rate_annualized",
        "initial_date",
        "settlement_date",
    ),
    solvable=(
        "barrier_config.ko_rate",
        "barrier_config.ki_barrier",
        "coupon_rate",
    ),
)

_CONTRACTS: dict[str, FamilyContract] = {
    "SnowballOption": _SNOWBALL_CONTRACT,
    "KnockOutResetSnowballOption": _SNOWBALL_CONTRACT,
    "PhoenixOption": _SNOWBALL_CONTRACT,
}


def contract_for(quantark_class: str | None) -> FamilyContract | None:
    return _CONTRACTS.get(str(quantark_class or ""))


def filter_solved(missing: list[str], *, solve_target: str | None) -> list[str]:
    """Drop the one designated solve target from a missing list (it is supplied
    as bounds + initial guess, not a bound value). All other gaps stand."""
    if not solve_target:
        return list(missing)
    return [key for key in missing if key != solve_target]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_product_contracts.py -v`
Expected: PASS (4 tests)

Note: `test_builder_missing_keys_are_declared_in_the_contract` passes today because `build_product("SnowballOption", {})` reports `initial_price`, `maturity_years`, `barrier_config.ko_barrier`, `barrier_config.ko_rate`, `barrier_config.lockup_months`, `trade_start_date`, `barrier_config.ki_barrier` — all declared. `observation_frequency` and `ko_observation_dates` join the reported set in Task 4 and are already declared here.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_contracts.py tests/test_product_contracts.py
git commit -m "feat(products): add data-driven per-family term contract (snowball)"
```

---

## Task 2: Extend `BuildResult` carrier with `product_spec`

The locked carrier (spec decision 1 / "Canonical carrier API"): `BuildResult` gains `product_spec: ProductSpec | None` (non-`None` iff `ok`). The standalone `product_kwargs` field becomes a read-only property over `product_spec.terms`, returning `{}` when the build did not succeed — which also realizes decision 1's "no partial kwargs leak on failure." `build_product` grows identity inputs (needed to populate `product_spec`) and a `solve_target` param (decision 6 exemption).

**Design notes (plan-author choices, flag if you disagree):**
1. *Property, not field rename.* Keeping `product_kwargs` as a property keeps ~25 existing `.product_kwargs` readers and both booking call sites green while moving the canonical storage to `product_spec.terms`. The locked decision says "replaced by `product_spec.terms`" — the property satisfies that (terms live on `product_spec`; no separate stored field).
2. *`asdict` drops properties.* The agent tool (`tools/products.py`) currently returns `asdict(result)`; that would silently lose `product_kwargs`. The tool is changed to build the dict explicitly, preserving the flat `product_kwargs` the LLM contract expects and adding `product_spec`.
3. *Identity fallback.* When identity kwargs are omitted (legacy callers like booking/agent that pass only `family, terms`), fall back to `terms.get("underlying")` / `terms.get("currency")` — mirroring the existing `_default_market` fallback.

**Files:**
- Modify: `backend/app/services/domains/product_builders.py` (imports, `BuildResult`, `build_product`)
- Modify: `backend/app/tools/products.py` (explicit serialization)
- Modify: `backend/app/services/domains/booking.py:95-139` (`repair_invalid_snowball_booking_terms` guards on `built.ok`)
- Test: `tests/test_product_builders.py`, `tests/test_tools_products.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_product_builders.py`:

```python
def test_build_result_carries_product_spec_when_ok():
    result = build_product(
        "SnowballOption",
        _snowball_terms(),
        underlying="000905.SH",
        currency="CNY",
    )
    assert result.ok is True
    assert result.product_spec is not None
    spec = result.product_spec
    assert spec.quantark_class == "SnowballOption"
    assert spec.product_family == "autocallable"   # derived from class
    assert spec.underlying == "000905.SH"
    assert spec.currency == "CNY"
    # product_kwargs is now a view onto product_spec.terms
    assert result.product_kwargs == spec.terms
    assert "barrier_config" in spec.terms


def test_build_result_product_spec_none_when_missing():
    result = build_product("SnowballOption", {"initial_price": 100.0})
    assert result.ok is False
    assert result.product_spec is None
    # no partial kwargs leak on failure
    assert result.product_kwargs == {}


def test_build_product_solve_target_is_exempt_from_missing():
    """The designated solve target (the unknown coupon) is supplied as a
    placeholder and must NOT be reported missing — the rest of the contract still
    must be bound (decision 6)."""
    terms = _snowball_terms()  # ko_rate present as the placeholder/initial guess
    result = build_product(
        "SnowballOption", terms, solve_target="barrier_config.ko_rate"
    )
    assert "barrier_config.ko_rate" not in result.missing
    assert result.ok is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py::test_build_result_carries_product_spec_when_ok -v`
Expected: FAIL with `AttributeError: 'BuildResult' object has no attribute 'product_spec'`

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/domains/product_builders.py`, add imports near the top (after the existing `from . import schedules`):

```python
from . import product_contracts, schedules
from .products import ProductSpec, product_family_for_quantark_class
```

(Replace the existing `from . import schedules` line with the combined import above. If a circular import arises at startup, instead import `ProductSpec`/`product_family_for_quantark_class` lazily inside `build_product`.)

Replace the `BuildResult` dataclass (currently lines 44-52):

```python
@dataclass(frozen=True)
class BuildResult:
    ok: bool
    quantark_class: str
    engine_name: str
    missing: list[str]
    warnings: list[str]
    validation: dict[str, Any] | None
    product_spec: "ProductSpec | None" = None

    @property
    def product_kwargs(self) -> dict[str, Any]:
        """Validated QuantArk kwargs. The single carrier is ``product_spec.terms``;
        this view returns ``{}`` when the build did not succeed, so partial kwargs
        never leak from a failed build."""
        return dict(self.product_spec.terms) if self.product_spec is not None else {}
```

Replace `build_product` (currently lines 513-544) with:

```python
def build_product(
    family: str,
    terms: dict[str, Any],
    *,
    market: PricingEnvironmentSnapshot | None = None,
    underlying: str | None = None,
    currency: str | None = None,
    components: list[dict[str, Any]] | None = None,
    asset_class: str = "equity",
    display_name: str | None = None,
    solve_target: str | None = None,
) -> BuildResult:
    engine_name = _ENGINE_BY_CLASS.get(family, "BlackScholesEngine")
    builder = _REGISTRY.get(family)
    if builder is None:
        return BuildResult(
            ok=False, quantark_class=family, engine_name=engine_name,
            missing=[], warnings=[f"unsupported_family: {family}"],
            validation=None, product_spec=None,
        )
    if family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict):
        # Already-built kwargs: tidy in place, skip raw-term synthesis.
        product_kwargs = _tidy_built_snowball(terms)
        warnings: list[str] = []
    else:
        out = builder(terms, quantark_class=family)
        missing = product_contracts.filter_solved(out.missing, solve_target=solve_target)
        if missing:
            return BuildResult(
                ok=False, quantark_class=family, engine_name=engine_name,
                missing=missing, warnings=out.warnings, validation=None,
                product_spec=None,
            )
        product_kwargs = out.product_kwargs
        warnings = out.warnings
    market = market or _default_market(terms)
    res = validate_quantark_build(family, dict(product_kwargs), market, engine_name)
    spec = (
        ProductSpec(
            asset_class=asset_class,
            product_family=product_family_for_quantark_class(family, components=components),
            quantark_class=family,
            underlying=str(underlying or terms.get("underlying") or "UNKNOWN"),
            currency=str(currency or terms.get("currency") or "CNY"),
            terms=product_kwargs,
            components=list(components or []),
            display_name=display_name,
        )
        if res.ok
        else None
    )
    return BuildResult(
        ok=bool(res.ok), quantark_class=family, engine_name=engine_name,
        missing=[], warnings=warnings,
        validation={"ok": res.ok, "error": res.error},
        product_spec=spec,
    )
```

(The hardened `_looks_prebuilt` reject branch lands in Task 3; leave the tidy branch as-is here.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py -v`
Expected: PASS — including all pre-existing builder tests (the `product_kwargs` property keeps `result.product_kwargs[...]` reads green).

- [ ] **Step 5: Update the agent tool serialization (asdict drops the property)**

Replace `build_product_tool` body in `backend/app/tools/products.py` (currently lines 32-33):

```python
    result = build_product(family, dict(terms or {}))
    return {
        "ok": result.ok,
        "quantark_class": result.quantark_class,
        "engine_name": result.engine_name,
        "product_kwargs": result.product_kwargs,
        "missing": result.missing,
        "warnings": result.warnings,
        "validation": result.validation,
        "product_spec": asdict(result.product_spec) if result.product_spec else None,
    }
```

Append to `tests/test_tools_products.py`:

```python
def test_build_product_tool_keeps_flat_product_kwargs_and_adds_product_spec():
    from app.tools.products import build_product_tool

    payload = build_product_tool.invoke(
        {
            "family": "SnowballOption",
            "terms": {
                "initial_price": 100.0,
                "maturity_years": 1.0,
                "ko_barrier_pct": 101,
                "ki_barrier_pct": 70,
                "ko_rate": 0.15,
                "ko_frequency": "MONTHLY",
                "ki_convention": "DAILY",
                "lockup_months": 3,
                "trade_start_date": "2026-01-05",
            },
        }
    )
    assert payload["ok"] is True
    # flat product_kwargs preserved for the LLM build contract
    assert "barrier_config" in payload["product_kwargs"]
    # product_spec now travels alongside
    assert payload["product_spec"]["quantark_class"] == "SnowballOption"
    assert payload["product_spec"]["product_family"] == "autocallable"
```

Run: `cd backend && python -m pytest ../tests/test_tools_products.py -v`
Expected: PASS

- [ ] **Step 6: Guard booking repair on build success (carrier consequence)**

In `backend/app/services/domains/booking.py`, in `repair_invalid_snowball_booking_terms`, replace the build + compare (currently lines 106-111):

```python
        current = dict(position.product_kwargs or {})
        built = build_product(position.product_type or "SnowballOption", current)
        if not built.ok:
            # A schedule-less legacy shape can no longer be silently tidied
            # (see Task 3 hardening); skip rather than persist a malformed product.
            continue
        normalized = built.product_kwargs
        if normalized == current:
            continue
```

Run: `cd backend && python -m pytest ../tests/test_product_booking.py -v`
Expected: PASS (the existing repair/booking tests stay green; `built.product_kwargs` is the property)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/domains/product_builders.py backend/app/tools/products.py backend/app/services/domains/booking.py tests/test_product_builders.py tests/test_tools_products.py
git commit -m "feat(products): extend BuildResult with product_spec carrier; thread identity + solve_target"
```

---

## Task 3: Harden the "already-built" discriminator (decision 8 / P1a)

Today `family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict)` routes to tidy/pass-through. The current RFQ snowball template is *nested kwargs with a `barrier_config` but no observation schedule* — it hits that branch, skips synthesis, reports `missing=[]`, and fails with the opaque quad error `KO observation dates or schedule required`. The branch must require **evidence of a synthesized schedule**, else reject the shape as malformed (it is neither the flat contract nor a complete product).

**Files:**
- Modify: `backend/app/services/domains/product_builders.py` (`_looks_prebuilt`, `_MALFORMED_PREBUILT_ERROR`, `build_product` branch)
- Test: `tests/test_product_builders.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_product_builders.py`:

```python
def test_prebuilt_snowball_without_schedule_is_rejected_not_silently_tidied():
    """P1a regression: the *current RFQ template shape* — a nested barrier_config
    with levels but NO ko_observation_schedule — must not slip through the tidy
    branch with missing=[] and the opaque quad error. Reject it as malformed.
    """
    rfq_template_shape = {
        "initial_price": 100.0,
        "strike": 100.0,
        "maturity": 1.0,
        "barrier_config": {
            "ko_barrier": 103.0,
            "ko_rate": 0.15,
            "ki_barrier": 75.0,
        },  # NOTE: no ko_observation_schedule
    }
    result = build_product("SnowballOption", rfq_template_shape)

    assert result.ok is False
    # not the opaque downstream quad error
    error = (result.validation or {}).get("error") or ""
    assert "KO observation" not in error
    assert "malformed" in error.lower()
    assert result.product_spec is None


def test_prebuilt_snowball_with_schedule_is_still_tidied():
    """A genuinely-built snowball (schedule present) still takes the tidy path."""
    built = build_product("SnowballOption", _snowball_terms())  # synthesizes a schedule
    assert built.ok
    rebuilt = build_product("SnowballOption", dict(built.product_kwargs))
    assert rebuilt.ok is True
    assert "ko_observation_schedule" in rebuilt.product_kwargs["barrier_config"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py::test_prebuilt_snowball_without_schedule_is_rejected_not_silently_tidied -v`
Expected: FAIL — today the template shape returns `ok=False` with `validation.error` containing `"KO observation"` (the opaque path), so the `"malformed" in error` assertion fails.

- [ ] **Step 3: Write minimal implementation**

In `backend/app/services/domains/product_builders.py`, add near `_PREBUILT_TIDY_CLASSES` (after line 455):

```python
_MALFORMED_PREBUILT_ERROR = (
    "malformed Snowball terms: barrier_config present without a synthesized "
    "ko_observation_schedule; supply flat economic terms (ko_barrier_pct, "
    "lockup_months, trade_start_date, observation_frequency, …) instead"
)


def _looks_prebuilt(terms: dict[str, Any]) -> bool:
    """A terms dict is 'already built' only if it carries a non-empty KO
    observation schedule — the evidence that synthesis already ran. A nested
    barrier_config with levels but no schedule is neither the flat contract nor a
    complete product, and must not be silently tidied (decision 8)."""
    barrier_config = terms.get("barrier_config")
    if not isinstance(barrier_config, dict):
        return False
    schedule = barrier_config.get("ko_observation_schedule")
    return isinstance(schedule, dict) and bool(schedule.get("records"))
```

In `build_product`, replace the tidy branch (the `if family in _PREBUILT_TIDY_CLASSES ...` block from Task 2) with:

```python
    if family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict):
        if not _looks_prebuilt(terms):
            return BuildResult(
                ok=False, quantark_class=family, engine_name=engine_name,
                missing=[], warnings=[],
                validation={"ok": False, "error": _MALFORMED_PREBUILT_ERROR},
                product_spec=None,
            )
        # Already-built kwargs: tidy in place, skip raw-term synthesis.
        product_kwargs = _tidy_built_snowball(terms)
        warnings = []
    else:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py -v`
Expected: PASS (both new tests + all prior)

- [ ] **Step 5: Verify no regression in booking's prebuilt path**

Run: `cd backend && python -m pytest ../tests/test_product_booking.py -v`
Expected: PASS — `test_book_rfq_sourced_snowball_with_prebuilt_schedules` (schedule present → tidied) and `test_normalize_snowball_booking_terms_promotes_coupon_and_drops_empty_schedules` (KO schedule has records → tidied) both stay green.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "fix(products): reject schedule-less prebuilt snowball shape; close P1a opaque-error bypass"
```

---

## Task 4: Multi-frequency snowball schedule synthesis (P2b/B)

Observation frequency becomes a **required-bound** input (`MONTHLY | QUARTERLY | SEMI_ANNUAL | CUSTOM`). `schedules.py` gains a periodic generator (monthly stays byte-identical as the step=1 case); `_build_snowball` reads `observation_frequency` (alias `ko_frequency`), reports it missing when absent, and synthesizes KO dates at the right cadence. `CUSTOM` carries an explicit `ko_observation_dates` list.

**Files:**
- Modify: `backend/app/services/domains/schedules.py` (`periodic_observation_dates`, `FREQUENCY_MONTHS`, `monthly_observation_dates` wrapper)
- Modify: `backend/app/services/domains/product_builders.py` (`_build_snowball` frequency handling)
- Modify: `tests/test_product_booking.py` (`_RAW_SNOWBALL_TERMS` gains a frequency key — the only fixture lacking one)
- Test: `tests/test_schedules.py`, `tests/test_product_builders.py`

- [ ] **Step 1: Write the failing test (schedules generator)**

Append to `tests/test_schedules.py`:

```python
def test_periodic_observation_dates_quarterly_count():
    dates = schedules.periodic_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=3, months_step=3
    )
    # months 3,6,9,12 -> 4 observations
    assert len(dates) == 4
    assert all(schedules.is_china_sse_business_day(d) for d in dates)


def test_periodic_observation_dates_semi_annual_count():
    dates = schedules.periodic_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=6, months_step=6
    )
    # months 6,12 -> 2 observations
    assert len(dates) == 2


def test_monthly_observation_dates_unchanged_as_step_one():
    # back-compat: the monthly helper must reproduce its old output exactly
    dates = schedules.monthly_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=3
    )
    assert len(dates) == 10  # months 3..12 inclusive
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_schedules.py::test_periodic_observation_dates_quarterly_count -v`
Expected: FAIL with `AttributeError: module 'app.services.domains.schedules' has no attribute 'periodic_observation_dates'`

- [ ] **Step 3: Write minimal implementation (schedules)**

In `backend/app/services/domains/schedules.py`, add after `add_months` / before `monthly_observation_dates`:

```python
FREQUENCY_MONTHS: dict[str, int] = {
    "MONTHLY": 1,
    "QUARTERLY": 3,
    "SEMI_ANNUAL": 6,
}


def periodic_observation_dates(
    *,
    start: date,
    maturity_years: float,
    lockup_months: int,
    months_step: int = 1,
    day_of_month: int | None = None,
) -> list[date]:
    """KO observation dates from ``start`` + lockup through maturity, stepping by
    ``months_step`` months, each rolled forward to the next SSE business day.

    ``months_step=1`` reproduces ``monthly_observation_dates`` exactly (months
    lockup..total inclusive). Larger steps give quarterly (3) / semi-annual (6).
    """
    total_months = round(maturity_years * 12)
    anchor = start if day_of_month is None else start.replace(
        day=min(day_of_month, calendar.monthrange(start.year, start.month)[1])
    )
    dates: list[date] = []
    m = lockup_months
    while m <= total_months:
        dates.append(roll_to_business_day(add_months(anchor, m)))
        m += months_step
    return dates
```

Replace the body of `monthly_observation_dates` (keep its signature) with a delegation:

```python
def monthly_observation_dates(
    *, start: date, maturity_years: float, lockup_months: int, day_of_month: int | None = None
) -> list[date]:
    """Monthly KO observation dates from `start`+lockup through maturity.

    Thin wrapper over `periodic_observation_dates` with a one-month step.
    """
    return periodic_observation_dates(
        start=start,
        maturity_years=maturity_years,
        lockup_months=lockup_months,
        months_step=1,
        day_of_month=day_of_month,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest ../tests/test_schedules.py -v`
Expected: PASS (new + existing schedule tests, including `test_monthly_observation_dates_respects_lockup_and_count`)

- [ ] **Step 5: Write the failing test (builder frequency)**

Append to `tests/test_product_builders.py`:

```python
def test_snowball_frequency_required_when_absent():
    terms = _snowball_terms()
    terms.pop("ko_frequency")  # no observation_frequency either
    result = build_product("SnowballOption", terms)
    assert "observation_frequency" in result.missing
    assert result.ok is False


def test_snowball_quarterly_builds_four_ko_observations():
    terms = _snowball_terms(observation_frequency="QUARTERLY", lockup_months=3)
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert result.missing == [], result.missing
    records = result.product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]
    assert len(records) == 4
    assert result.product_kwargs["barrier_config"]["ko_observation_schedule"]["frequency"] == "QUARTERLY"
    assert result.ok is True


def test_snowball_custom_frequency_uses_supplied_dates():
    terms = _snowball_terms(
        observation_frequency="CUSTOM",
        ko_observation_dates=["2026-06-05", "2026-09-07", "2026-12-07"],
        lockup_months=0,
    )
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert result.missing == [], result.missing
    records = result.product_kwargs["barrier_config"]["ko_observation_schedule"]["records"]
    assert len(records) == 3
    assert result.ok is True


def test_snowball_custom_frequency_missing_dates_reported():
    terms = _snowball_terms(observation_frequency="CUSTOM")
    terms.pop("ko_frequency", None)
    result = build_product("SnowballOption", terms)
    assert "ko_observation_dates" in result.missing
    assert result.ok is False
```

- [ ] **Step 6: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py::test_snowball_frequency_required_when_absent -v`
Expected: FAIL — today frequency is ignored (hardcoded monthly), so the build succeeds and `observation_frequency` is not in `missing`.

- [ ] **Step 7: Write minimal implementation (builder)**

In `backend/app/services/domains/product_builders.py`, inside `_build_snowball`, add frequency resolution alongside the other input reads (after the `ki_convention = ...` line, ~line 140):

```python
    frequency = str(
        terms.get("observation_frequency") or terms.get("ko_frequency") or ""
    ).upper()
    custom_dates_raw = terms.get("ko_observation_dates")
```

Add the frequency validation to the missing checks (with the other `if ... is None: out.missing.append(...)` calls, before the `if out.missing:` guard):

```python
    if frequency not in {"MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "CUSTOM"}:
        out.missing.append("observation_frequency")
    elif frequency == "CUSTOM" and not isinstance(custom_dates_raw, list):
        out.missing.append("ko_observation_dates")
```

Replace the KO-date synthesis (currently lines 170-184, the `exercise = ...` through the `bc = {...}` KO schedule) with frequency-aware synthesis:

```python
    exercise = schedules.add_months(start, round(maturity * 12))
    if frequency == "CUSTOM":
        ko_dates = [date.fromisoformat(str(d)) for d in custom_dates_raw]
    else:
        ko_dates = schedules.periodic_observation_dates(
            start=start,
            maturity_years=maturity,
            lockup_months=int(lockup),
            months_step=schedules.FREQUENCY_MONTHS[frequency],
        )
    bc = {
        "ko_barrier": ko_barrier,
        "ko_rate": ko_rate,
        "ko_observation_type": "DISCRETE",
        "ko_observation_schedule": schedules.build_ko_schedule(
            dates=ko_dates,
            barriers=[ko_barrier] * len(ko_dates),
            rates=[ko_rate] * len(ko_dates),
            annualized=annualized,
            frequency=frequency,
        ),
    }
```

- [ ] **Step 8: Update the one fixture that omits frequency**

In `tests/test_product_booking.py`, add a frequency key to `_RAW_SNOWBALL_TERMS` (currently lines 17-28) so the characterization tests stay green:

```python
    "lockup_months": 0,
    "observation_frequency": "MONTHLY",
}
```

(Insert `"observation_frequency": "MONTHLY",` before the closing brace.)

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py ../tests/test_product_booking.py ../tests/test_product_contracts.py -v`
Expected: PASS — including `test_builder_missing_keys_are_declared_in_the_contract` (now `observation_frequency`/`ko_observation_dates` appear in `missing` and are declared in Task 1's contract).

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/domains/schedules.py backend/app/services/domains/product_builders.py tests/test_schedules.py tests/test_product_builders.py tests/test_product_booking.py
git commit -m "feat(products): multi-frequency snowball KO synthesis; frequency required-bound (P2b)"
```

---

## Task 5: Family-from-`(class, components)` threading + docs + full-suite gate

`product_family_for_quantark_class` is already the single derivation point and already returns `"package"` for non-empty components. The Foundation work is to confirm `build_product` threads `components` into it (done in Task 2) and pin that contract with a test, refresh the agent contract doc, and gate the whole Foundation on a green suite.

**Files:**
- Test: `tests/test_product_builders.py`
- Modify: `backend/app/skills/references/products/build-contract.md` (frequency + carrier note)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_product_builders.py`:

```python
def test_build_product_threads_components_into_family_derivation():
    """A packaged product (non-empty components) derives product_family == 'package',
    not the single-leg class family — components must reach the single derivation
    point (decision 3)."""
    result = build_product(
        "SnowballOption",
        _snowball_terms(),
        underlying="000905.SH",
        currency="CNY",
        components=[{"component_role": "leg", "quantity": 1.0}],
    )
    assert result.ok is True
    assert result.product_spec.product_family == "package"
    assert result.product_spec.components == [{"component_role": "leg", "quantity": 1.0}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest ../tests/test_product_builders.py::test_build_product_threads_components_into_family_derivation -v`
Expected: PASS immediately **only if** Task 2 already threaded `components`. If it FAILS with `product_family == "autocallable"`, the `components` kwarg is not reaching `product_family_for_quantark_class` — fix `build_product` to pass `components=components` (Task 2 step 3 already does). Re-run until green.

(This task's test characterizes an already-built behavior; it is the regression pin for decision 3 at the `build_product` boundary.)

- [ ] **Step 3: Refresh the agent build-contract reference**

In `backend/app/skills/references/products/build-contract.md`, ensure the snowball term list states `observation_frequency` is **required** (values `MONTHLY | QUARTERLY | SEMI_ANNUAL | CUSTOM`; `CUSTOM` needs `ko_observation_dates`), and add a one-line note that `build_product` now also returns `product_spec` (identity: `product_family`, `underlying`, `currency`, `terms`) alongside the flat `product_kwargs`. Read the file first; make the edits match its existing structure.

- [ ] **Step 4: Run the full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS — the full suite green (the pre-change baseline was 1015 passing; expect 1015 + the new Foundation tests). Investigate any failure before proceeding; do not adjust tests to pass without understanding the cause.

- [ ] **Step 5: Commit**

```bash
git add tests/test_product_builders.py backend/app/skills/references/products/build-contract.md
git commit -m "test(products): pin components->package derivation; refresh build-contract doc"
```

---

## Self-Review

**Spec coverage (migration step 1 / "Foundation"):**
- (a) canonical carrier (decision 1) → **Task 2** ✓
- (b) harden already-built detection (decision 8) + P1a regression → **Task 3** ✓
- (c) derive `product_family` from `(quantark_class, components)` (decision 3) → **Task 2** (threading) + **Task 5** (pin) ✓
- (d) extract term contract + drive `missing` from it (decision 5) → **Task 1** (data + consistency test); snowball builder consults it for frequency + solve-target exemption ✓
- (e) multi-frequency KO synthesis (P2b/B) → **Task 4** ✓
- solve target = bound-by-guess, not missing (decision 6) → **Task 1** `filter_solved` + **Task 2** `solve_target` param ✓

**Deliberately out of scope (separate plans):** decisions 4 (channel adapters emit flat) and 7 (RFQ→book resolution) are *enabled* here (malformed-reject closes the nested-shape door; `solve_target` exemption exists) but exercised in the RFQ / try-solve / import / cleanup plans. Full data-driven `missing` for non-snowball families is deferred to each family's channel migration; the consistency test prevents snowball drift now.

**Placeholder scan:** none — every code step shows the full code and exact run/commit commands.

**Type/name consistency:** `BuildResult.product_spec` (Task 2) is read in Tasks 3/5; `product_kwargs` property (Task 2) is read in Tasks 3/4; `product_contracts.filter_solved` + `contract_for` (Task 1) consumed in Task 2; `schedules.periodic_observation_dates` + `FREQUENCY_MONTHS` (Task 4) consumed by `_build_snowball` in the same task; `_looks_prebuilt` / `_MALFORMED_PREBUILT_ERROR` (Task 3) used in the Task 2/3 `build_product` branch. The `observation_frequency` canonical key with `ko_frequency` alias is consistent across builder, contract, fixtures, and tool test.

**Known behavior changes (intended, tested):**
1. Failed builds expose `product_kwargs == {}` (was: partial kwargs). Existing negative tests assert on `missing`, not partial kwargs; the one reader of partial kwargs (`test_product_builders.py:48`) still passes (`{}.get("barrier_config", {})` → `{}`).
2. Schedule-less legacy snowball shapes are rejected (Task 3); `repair_invalid_snowball_booking_terms` now skips them instead of tidying (Task 2 step 6) — safer than persisting a schedule-less product.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-31-unified-product-schema-foundation.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
