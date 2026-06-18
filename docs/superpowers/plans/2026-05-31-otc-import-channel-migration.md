# OTC-Import Channel Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `build_product` the single producer/validation gate for the OTC-import channel — every imported family (vanilla, american, digital, barrier, single/double sharkfin, snowball, phoenix) flows through `build_product` and is validated, closing today's gap where phoenix and all scalar imports persist **unvalidated**.

**Architecture (decided 2026-05-31):** OTC imports carry **complete, heterogeneous per-date termsheets** (step-down barrier/rate *lists* zipped against explicit workbook observation dates) for *existing* trades. The uniform schedule synthesizer used by RFQ/try-solve cannot reproduce these without data loss, so the OTC migration means **validate-and-wrap the complete termsheet, never re-synthesize.** Two decisions (locked via stakeholder Q&A):
1. **Booking gate** — generalize `build_product`'s snowball-only "already-built" passthrough to a `prebuilt=True` validate-and-wrap path for any family, then widen `booking.normalize/validate_booking_product_spec` (today snowball-only) to route every gated family through it. One gate then covers OTC import + manual booking + RFQ-book.
2. **Keep the explicit-schedule helpers** — `position_adapter`'s per-date schedule/KI helpers stay (they handle workbook data the uniform synthesizer can't express). The migration unifies only the OUTPUT carrier (`ProductSpec`) + validation gate. This is a documented, deliberate deviation from the spec's literal "retire `position_adapter` synthesis" wording (`2026-05-30-unified-product-schema-design.md` line 345) — honoured because the spec also mandates *no import regression* (line 350).

**Tech Stack:** Python 3.11, pytest (rootdir = repo root, `pythonpath = ["backend"]`, `testpaths = ["tests"]`). QuantArk derivatives library (vendored, path-injected via settings). No frontend changes.

**Pre-flight (worktree isolation):** A concurrent agent shares this repo and churns the shared `HEAD`/branches. Before executing, create an isolated worktree (superpowers:using-git-worktrees) — for this repo (no git remote), branch from local `main` into an **external** path, e.g. `git worktree add /Users/fuxinyao/ots-wt-otc -b feat/otc-import-migration main`, and run pytest with the main repo's venv from the worktree dir: `cd <worktree> && /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest …`. Plan-writing does not need a worktree; execution does.

**Out of scope:** Cleanup channel (step 5 of the strangler-fig: moving family derivation onto the canonical product, removing now-dead builder code) is a separate follow-on plan. No frontend changes. Families `build_product` supports but the OTC channel never emits (Futures, SpotInstrument, AsianOption, OneTouchOption, RangeAccrualOption, DoubleOneTouchOption) are **not** added to the booking gate here — extending the gate to them is a trivial, separately-characterized follow-up.

---

## Background the implementer needs

**Current OTC flow** (`backend/app/services/position_adapter.py`):
`import_positions_from_xlsx` → per row: `map_trade_row(row)` → `PositionMapping(product_type, product_kwargs, engine_name, …)` (a per-family mapper builds the **complete** QuantArk termsheet, including explicit `ko_observation_schedule` records from workbook dates) → for a NEW trade: `book_position(...)`; for an EXISTING trade (same `source_trade_id`): `create_or_get_product(... reuse=True)` **directly** (bypasses the gate).

**Current gate** (`backend/app/services/domains/booking.py`): `book_position` → `prepare_booking_product_spec` → `normalize_booking_product_spec` + `validate_booking_product_spec`. **Both early-return unless `quantark_class in _SNOWBALL_BOOKING_TYPES = {"SnowballOption", "KnockOutResetSnowballOption"}`.** So phoenix + all scalar imports are persisted with **no `build_product`, no `validate_quantark_build`**.

**Why `build_product` can't ingest these complete termsheets today:** for a non-snowball family it runs the raw-term *synthesis* builder (`_build_vanilla` etc.), which reads flat terms (`maturity_years`, `option_type`) and would (a) report `maturity_years` missing (OTC vanilla carries `exercise_date`/`settlement_date`, not `maturity_years`) and (b) drop `exercise_date`/`settlement_date`/`contract_multiplier`/explicit schedules. PhoenixOption isn't in `_PREBUILT_TIDY_CLASSES`, so a complete phoenix termsheet also hits raw synthesis and fails. The `prebuilt=True` path (Task 2) is the fix.

**Key fact that de-risks validation:** for all 8 OTC families, the adapter's `engine_name` is **identical** to `product_builders._ENGINE_BY_CLASS[family]` (verified: BlackScholes / AmericanOptionAnalytical / DigitalOptionAnalytical / BarrierAnalytical / SingleSharkfinOptionAnalytical / DoubleSharkfinOptionAnalytical / SnowballQuad / PhoenixQuad). So `build_product`'s internal `validate_quantark_build` uses the same engine the adapter intends — no engine mismatch.

**Test fixtures to reuse:** `tests/test_position_import_pricing.py` defines `TRADE_HEADERS`, `write_trade_workbook(path, rows)`, and row builders `vanilla_row()`, `shark_row()`, `double_shark_row()`, `snowball_row()`, `phoenix_row()`, `unsupported_row()`, plus `configure_test_db(tmp_path)`. The snowball fixture is a genuine step-down (`敲出价格="105,100"` → `ko_barrier=[105.0,100.0]`).

---

## File Structure

- `backend/app/services/domains/product_builders.py` — add `prebuilt: bool = False` param to `build_product` and the validate-and-wrap branch. One focused dispatcher change; preserves the existing snowball auto-detect path byte-for-byte.
- `backend/app/services/domains/booking.py` — widen the gate: `_GATED_BOOKING_TYPES` (9 families), route every gated family through `build_product` (snowball auto-detect; others `prebuilt=True`) in `normalize_booking_product_spec`, extend `validate_booking_product_spec`'s guard.
- `backend/app/services/position_adapter.py` — gate the existing-position UPDATE branch through `prepare_booking_product_spec`; wrap per-row persistence so one invalid row becomes an error mapping instead of crashing the whole import.
- `backend/app/skills/references/products/build-contract.md` — one short note documenting the `prebuilt` validate-and-wrap entry (so the agent/docs reflect the unified gate).
- Tests: `tests/test_product_builders.py`, `tests/test_product_booking.py` (the existing booking-gate test module), `tests/test_position_import_pricing.py`.

---

### Task 1: Spike — does every OTC family's complete termsheet validate? (THROWAWAY)

**The one genuine unknown:** snowball/ko_reset imports are validated today (booking works), but phoenix and all scalar imports have **never** been through `validate_quantark_build`. Before widening the gate we must confirm each family's adapter output actually validates — and capture any kwarg the validator rejects — so Task 2/3 don't surface a surprise.

**Files:**
- Create (throwaway): `backend/scripts/spike_otc_validate.py`

- [ ] **Step 1: Write the spike script**

Create `backend/scripts/spike_otc_validate.py`:

```python
"""THROWAWAY spike: does each OTC family's complete adapter termsheet pass
validate_quantark_build today? Run from repo root with the project venv.
Delete after recording the outcome in the plan."""
from app.schemas import PricingEnvironmentSnapshot
from app.services.position_adapter import map_trade_row
from app.services.quantark import validate_quantark_build

import sys
sys.path.insert(0, "tests")  # reuse the row builders
from test_position_import_pricing import (  # type: ignore
    vanilla_row, shark_row, double_shark_row, snowball_row, phoenix_row,
)


def american_row():
    row = vanilla_row("T-AMERICAN")
    row["结构类型"] = "美式香草"
    return row


def digital_row():
    row = vanilla_row("T-DIGITAL")
    row["结构类型"] = "欧式二元"
    row["收益率"] = "5%"
    return row


def barrier_row():
    row = vanilla_row("T-BARRIER")
    row["结构类型"] = "基础障碍敲入期权"
    row["敲入价格"] = 80.0
    row["未敲入收益率"] = "1%"
    return row


CASES = [
    ("欧式香草", vanilla_row()),
    ("美式香草", american_row()),
    ("欧式二元", digital_row()),
    ("基础障碍敲入期权", barrier_row()),
    ("单鲨", shark_row()),
    ("双鲨", double_shark_row()),
    ("非保本雪球", snowball_row()),
    ("非保本凤凰立即派息", phoenix_row()),
]

market = PricingEnvironmentSnapshot(spot=100.0, volatility=0.22, rate=0.02, dividend_yield=0.03)
for label, row in CASES:
    m = map_trade_row(row)
    res = validate_quantark_build(m.product_type, dict(m.product_kwargs), market, m.engine_name)
    print(f"{label:>14} {m.product_type:<28} ok={res.ok}  err={res.error}")
```

- [ ] **Step 2: Run the spike**

Run: `python -m pytest -q tests/test_position_import_pricing.py -k nothing` once first to confirm imports resolve, then:
Run: `cd backend && python -m scripts.spike_otc_validate` — or from repo root: `python backend/scripts/spike_otc_validate.py` (ensure `PYTHONPATH=backend` so `app...` imports resolve; the project venv + `pythonpath=["backend"]` in pyproject is for pytest only, so for the script run `PYTHONPATH=backend python backend/scripts/spike_otc_validate.py`).

Expected: 8 lines, one per family, printing `ok=True/False` and any error.

- [ ] **Step 3: Record the outcome (in this plan, below)**

Write the result inline here:

```
SPIKE OUTCOME (fill in):
  欧式香草   EuropeanVanillaOption          ok=?
  美式香草   AmericanOption                 ok=?
  欧式二元   CashOrNothingDigitalOption     ok=?
  基础障碍   BarrierOption                  ok=?
  单鲨       SingleSharkfinOption           ok=?
  双鲨       DoubleSharkfinOption           ok=?
  非保本雪球 SnowballOption                 ok=?  (expected True — booking works today)
  非保本凤凰 PhoenixOption                  ok=?
```

**Decision rule:**
- **All `ok=True`** → proceed with Task 2/3 as written (validate-and-wrap is pure gain).
- **Any `ok=False`** → STOP and report to your human partner with the exact error. A failing family means the adapter emits a kwarg `validate_quantark_build` rejects — that is a pre-existing latent bug being surfaced by the gate. Do NOT silently make the gate lenient. Options to discuss: fix the adapter's kwarg for that family (preferred), or scope that family out of the gate this pass.

- [ ] **Step 4: Delete the spike + commit nothing**

Run: `rm backend/scripts/spike_otc_validate.py`
The spike is throwaway; do not commit it. (No commit for Task 1.)

---

### Task 2: `build_product(prebuilt=True)` — validate-and-wrap a complete termsheet

**Files:**
- Modify: `backend/app/services/domains/product_builders.py` (`build_product`, the dispatcher ~lines 573–644)
- Test: `tests/test_product_builders.py`

**Context:** `build_product`'s dispatcher currently has two branches: a snowball "already-built tidy" branch (`family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict)`) and the raw-synthesis `else`. Add a third entry: when the caller passes `prebuilt=True`, treat `terms` as a complete QuantArk termsheet — skip synthesis and the `missing` check, validate, and wrap into a `ProductSpec`. For snowball-family the existing tidy + malformed-schedule rejection (decision 8) must still apply even under `prebuilt=True`. Non-snowball families just pass `dict(terms)` straight to `validate_quantark_build`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_product_builders.py`:

```python
def test_prebuilt_wraps_complete_vanilla_termsheet_without_synthesis():
    # A complete OTC-style vanilla termsheet: carries exercise/settlement dates +
    # contract_multiplier and NO maturity_years. The raw builder would reject it;
    # prebuilt validate-and-wrap accepts it verbatim.
    terms = {
        "strike": 100.0, "option_type": "CALL",
        "exercise_date": "2026-12-31", "settlement_date": "2027-01-04",
        "contract_multiplier": 10000.0,
    }
    result = build_product("EuropeanVanillaOption", terms, prebuilt=True)
    assert result.ok is True, result.validation
    assert result.engine_name == "BlackScholesEngine"
    assert result.missing == []
    # verbatim: no synthesis, no dropped keys
    assert result.product_kwargs == terms
    assert result.product_spec is not None
    assert result.product_spec.quantark_class == "EuropeanVanillaOption"


def test_prebuilt_false_still_runs_raw_synthesis_for_scalars():
    # Regression: without prebuilt, the raw builder is used (maturity_years -> maturity).
    result = build_product(
        "EuropeanVanillaOption",
        {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
    )
    assert result.ok is True
    assert "maturity" in result.product_kwargs and "maturity_years" not in result.product_kwargs


def test_prebuilt_rejects_malformed_schedule_less_snowball():
    # Decision-8 hardening must still apply under prebuilt: a nested barrier_config
    # with no synthesized ko_observation_schedule is malformed, not silently wrapped.
    terms = {
        "initial_price": 100.0, "strike": 100.0, "maturity": 1.0,
        "barrier_config": {"ko_barrier": 103.0, "ko_rate": 0.1},  # no schedule
    }
    result = build_product("SnowballOption", terms, prebuilt=True)
    assert result.ok is False
    assert result.product_spec is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_product_builders.py::test_prebuilt_wraps_complete_vanilla_termsheet_without_synthesis tests/test_product_builders.py::test_prebuilt_false_still_runs_raw_synthesis_for_scalars tests/test_product_builders.py::test_prebuilt_rejects_malformed_schedule_less_snowball -v`
Expected: the first and third FAIL with `TypeError: build_product() got an unexpected keyword argument 'prebuilt'`; the second PASSES (it doesn't use the new kwarg).

- [ ] **Step 3: Add the `prebuilt` parameter + branch**

In `backend/app/services/domains/product_builders.py`, change the `build_product` signature — add `prebuilt: bool = False` after `solve_target`:

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
    prebuilt: bool = False,
) -> BuildResult:
```

Then replace the dispatcher branch. The CURRENT code is:

```python
    if family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict):
        if not _looks_prebuilt(terms):
            # Nested barrier_config with no synthesized schedule: neither the flat
            # contract nor a complete product. Reject as malformed rather than
            # tidying it into the opaque quad "KO observation … required" error.
            return BuildResult(
                ok=False, quantark_class=family, engine_name=engine_name,
                missing=[], warnings=[],
                validation={"ok": False, "error": _MALFORMED_PREBUILT_ERROR},
                product_spec=None,
            )
        # Already-built kwargs: tidy in place, skip raw-term synthesis. There is no
        # missing-key check here, so `solve_target` does not apply on this branch
        # (a prebuilt shape already carries its coupon); it is honored only on the
        # raw-synthesis path below.
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
```

Replace it with (note: the snowball auto-detect condition is preserved exactly; `prebuilt` is an additional way into the validate-and-wrap branch, and non-snowball prebuilt skips tidy):

```python
    snowball_built = family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict)
    if prebuilt or snowball_built:
        if family in _PREBUILT_TIDY_CLASSES and isinstance(terms.get("barrier_config"), dict):
            if not _looks_prebuilt(terms):
                # Nested barrier_config with no synthesized schedule: neither the flat
                # contract nor a complete product. Reject as malformed rather than
                # tidying it into the opaque quad "KO observation … required" error.
                return BuildResult(
                    ok=False, quantark_class=family, engine_name=engine_name,
                    missing=[], warnings=[],
                    validation={"ok": False, "error": _MALFORMED_PREBUILT_ERROR},
                    product_spec=None,
                )
            # Already-built snowball kwargs: tidy in place, skip raw-term synthesis.
            product_kwargs = _tidy_built_snowball(terms)
        else:
            # prebuilt=True for a non-snowball family: the caller (e.g. the OTC import
            # adapter) asserts `terms` is a complete QuantArk termsheet. Validate-and-
            # wrap verbatim — never re-synthesize (it would drop explicit dates and
            # per-date schedules the workbook supplied). `solve_target` does not apply.
            product_kwargs = dict(terms)
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
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_product_builders.py -q`
Expected: PASS (all three new tests + no regression in the existing ~50 builder tests; the snowball prebuilt/tidy tests are unchanged because the auto-detect condition is preserved).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "feat(builders): build_product(prebuilt=True) validate-and-wrap path"
```

---

### Task 3: Widen the booking gate to every OTC family

**Files:**
- Modify: `backend/app/services/domains/booking.py` (`_SNOWBALL_BOOKING_TYPES` ~line 43; `normalize_booking_product_spec` ~lines 46–73; `validate_booking_product_spec` ~lines 76–93)
- Test: `tests/test_product_booking.py` (the existing booking-gate test module)

**Context:** Today `normalize`/`validate` are no-ops unless snowball. Widen to `_GATED_BOOKING_TYPES` (the 9 families the OTC channel + common booking use). Snowball-family terms may arrive raw (synthesize) OR complete (tidy) — keep the auto-detect (`prebuilt=False`) for those. Every other gated family arrives complete (from the OTC adapter, or the `build_product` tool that produced the booking spec), so pass `prebuilt=True`. `ProductBookingSpec` is a subclass of `ProductSpec` (fields: `asset_class, product_family, quantark_class, underlying, currency, terms, components, display_name, source_payload`).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_product_booking.py` (reuse its existing imports where present):

```python
from app.services.domains.booking import (
    ProductBookingSpec,
    normalize_booking_product_spec,
)


def _complete_vanilla_spec():
    return ProductBookingSpec(
        asset_class="equity",
        product_family="option",
        quantark_class="EuropeanVanillaOption",
        underlying="000852.SH",
        currency="CNY",
        terms={
            "strike": 100.0, "option_type": "CALL",
            "exercise_date": "2026-12-31", "settlement_date": "2027-01-04",
            "contract_multiplier": 10000.0,
        },
        components=[],
        display_name=None,
        source_payload={},
    )


def test_gate_validates_complete_scalar_and_returns_unchanged():
    spec = _complete_vanilla_spec()
    normalized = normalize_booking_product_spec(spec)
    # complete + valid -> validate-and-wrap returns the same terms (no synthesis)
    assert normalized.terms == spec.terms


def test_gate_rejects_invalid_scalar_with_precise_error():
    spec = _complete_vanilla_spec()
    spec = ProductBookingSpec(**{**spec.__dict__, "terms": {"option_type": "CALL"}})  # no strike/dates
    import pytest
    with pytest.raises(ValueError) as exc:
        normalize_booking_product_spec(spec)
    # surfaces build_product's precise diagnostic, not an opaque downstream error
    assert "EuropeanVanillaOption" in str(exc.value)
```

NOTE: confirm `ProductBookingSpec`'s field set by reading `booking.py` (it is a dataclass; `**spec.__dict__` round-trips). If `source_payload` or any field is absent from the dataclass, drop it from `_complete_vanilla_spec`.

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_product_booking.py -k "gate_validates or gate_rejects" -v`
Expected: `test_gate_validates_complete_scalar_and_returns_unchanged` PASSES trivially today (non-snowball is a no-op → returns unchanged) — that's fine, it's a characterization guard. `test_gate_rejects_invalid_scalar_with_precise_error` FAILS today (no-op returns the spec; no ValueError raised).

- [ ] **Step 3: Add the gated-family set + auto-detect set**

In `backend/app/services/domains/booking.py`, replace:

```python
_SNOWBALL_BOOKING_TYPES = {"SnowballOption", "KnockOutResetSnowballOption"}
```

with:

```python
# Snowball-family terms may arrive raw (synthesize) or complete (tidy) — let
# build_product auto-detect, so prebuilt stays False for these.
_SNOWBALL_BOOKING_TYPES = {"SnowballOption", "KnockOutResetSnowballOption"}

# Every family routed through the single build_product validation gate at booking
# time. The OTC import channel emits all of these; non-snowball families always
# arrive as complete QuantArk termsheets (from the import adapter or the
# build_product tool), so they take the prebuilt validate-and-wrap path.
_GATED_BOOKING_TYPES = _SNOWBALL_BOOKING_TYPES | {
    "PhoenixOption",
    "EuropeanVanillaOption",
    "AmericanOption",
    "CashOrNothingDigitalOption",
    "BarrierOption",
    "SingleSharkfinOption",
    "DoubleSharkfinOption",
}
```

- [ ] **Step 4: Route every gated family through build_product in `normalize`**

Replace the body of `normalize_booking_product_spec`:

```python
def normalize_booking_product_spec(product: ProductBookingSpec) -> ProductBookingSpec:
    if product.quantark_class not in _SNOWBALL_BOOKING_TYPES:
        return product
    # The single shared builder handles both raw economic terms (synthesize
    # schedules + accrual dates) and already-built kwargs (tidy in place), so the
    # booking, pricing, and agent paths can never diverge on Snowball term shape.
    from .product_builders import build_product

    built = build_product(
        product.quantark_class or "SnowballOption", dict(product.terms or {})
    )
    if built.missing:
        raise ValueError(
            f"Incomplete {product.quantark_class} booking terms; missing: "
            + ", ".join(built.missing)
        )
    if not built.ok:
        # build_product is the single gate: surface its precise diagnostic now
        # (e.g. the malformed schedule-less-shape message) instead of returning
        # unchanged and letting the raw quad validator re-raise an opaque error
        # ("KO observation … required") downstream.
        error = (built.validation or {}).get("error") or "invalid product terms"
        raise ValueError(
            f"Invalid {product.quantark_class} booking terms: {error}"
        )
    if built.product_kwargs == product.terms:
        return product
    return replace(product, terms=built.product_kwargs)
```

with:

```python
def normalize_booking_product_spec(product: ProductBookingSpec) -> ProductBookingSpec:
    if product.quantark_class not in _GATED_BOOKING_TYPES:
        return product
    # build_product is the single producer/gate. Snowball-family terms may be raw
    # (synthesize) or complete (tidy) — auto-detect. Every other gated family
    # arrives as a complete QuantArk termsheet (OTC import adapter / build_product
    # tool), so validate-and-wrap it verbatim via prebuilt=True (re-synthesis would
    # drop the workbook's explicit dates and per-date schedules).
    from .product_builders import build_product

    built = build_product(
        product.quantark_class or "SnowballOption",
        dict(product.terms or {}),
        prebuilt=product.quantark_class not in _SNOWBALL_BOOKING_TYPES,
    )
    if built.missing:
        raise ValueError(
            f"Incomplete {product.quantark_class} booking terms; missing: "
            + ", ".join(built.missing)
        )
    if not built.ok:
        # Surface build_product's precise diagnostic now instead of returning
        # unchanged and letting the raw quad validator re-raise an opaque error
        # downstream.
        error = (built.validation or {}).get("error") or "invalid product terms"
        raise ValueError(
            f"Invalid {product.quantark_class} booking terms: {error}"
        )
    if built.product_kwargs == product.terms:
        return product
    return replace(product, terms=built.product_kwargs)
```

- [ ] **Step 5: Widen `validate_booking_product_spec`'s guard**

In `validate_booking_product_spec`, change the early-return guard from `_SNOWBALL_BOOKING_TYPES` to `_GATED_BOOKING_TYPES`:

```python
def validate_booking_product_spec(
    product: ProductBookingSpec, *, engine_name: str
) -> None:
    if product.quantark_class not in _GATED_BOOKING_TYPES:
        return
    from ..quantark import validate_quantark_build

    market = _validation_market_for_product(product)
    result = validate_quantark_build(
        product.quantark_class or "SnowballOption",
        dict(product.terms or {}),
        market,
        engine_name,
    )
    if not result.ok:
        raise ValueError(
            f"Invalid {product.quantark_class} booking terms: {result.error}"
        )
```

- [ ] **Step 6: Run the new tests + the full booking/builder/positions suites**

Run: `python -m pytest tests/test_product_booking.py tests/test_product_builders.py tests/test_services_domains_positions.py -q`
Expected: PASS, including both new gate tests. If any existing booking/positions test breaks, it is pinning a *previously-unvalidated* family booking with terms that don't validate — read it: either the test fixture is incomplete (fix the fixture to a complete termsheet) or it surfaces a real latent invalid-terms bug (STOP, report to your human partner with the family + validator error). Do not weaken the gate to make a test pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/domains/booking.py tests/test_product_booking.py
git commit -m "feat(booking): route every OTC family through the build_product gate"
```

---

### Task 4: Gate the OTC update path + make per-row failures non-fatal

**Files:**
- Modify: `backend/app/services/position_adapter.py` (`import_positions_from_xlsx` loop ~lines 82–145)
- Test: `tests/test_position_import_pricing.py`

**Context:** Two gaps remain in the import loop: (1) the EXISTING-position UPDATE branch calls `create_or_get_product(...)` directly, bypassing `prepare_booking_product_spec` — so re-imports of phoenix/scalars stay ungated; (2) `book_position(...)` is called with no try/except, so now that more families validate, a single invalid row would raise and crash the whole import. Fix both: validate updates through the gate, and turn a per-row booking failure into an error mapping that is still recorded.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_position_import_pricing.py` (reuses `write_trade_workbook`, `configure_test_db`, the row builders, and the import entrypoint):

```python
def test_import_validates_all_families_and_isolates_bad_rows(tmp_path):
    from app.models import Portfolio, PortfolioKind
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="OTC", kind=PortfolioKind.CONTAINER.value)
    session.add(portfolio)
    session.flush()

    good_vanilla = vanilla_row("T-OK-VANILLA")
    good_phoenix = phoenix_row("T-OK-PHOENIX")
    bad = vanilla_row("T-BAD")
    bad["行权价格"] = None  # missing strike -> mapping raises -> error row, not a crash

    path = tmp_path / "trades.xlsx"
    write_trade_workbook(path, [good_vanilla, good_phoenix, bad])

    batch = import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=path)

    # the whole import completes; the bad row is isolated as an error
    assert batch.error_count >= 1
    assert batch.supported_count >= 2
    positions = {p.source_trade_id: p for p in session.query(Position).all()}
    assert positions["T-OK-VANILLA"].mapping_status == "supported"
    assert positions["T-OK-PHOENIX"].mapping_status == "supported"
    assert positions["T-BAD"].mapping_status == "error"
```

(Confirm `PortfolioKind` import path against `position_adapter.py` / `models.py`; mirror however `tests/test_position_import_pricing.py`'s existing DB-backed tests build a container portfolio — reuse that helper if present.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest "tests/test_position_import_pricing.py::test_import_validates_all_families_and_isolates_bad_rows" -v`
Expected: behaviour depends on current code — likely the bad row already becomes an error via the `map_trade_row` try/except (missing strike raises in the mapper), so the *isolation* half may pass; the part that newly matters is that `T-OK-PHOENIX` is now validated. If the bad input instead slips past mapping and raises inside `book_position`, the test FAILS by raising (un-caught) — which is exactly the per-row robustness gap. Adjust the `bad` row so it maps cleanly but fails *validation* if needed (e.g. a barrier row with a contradictory barrier) to exercise the booking-time raise; otherwise this test stands as the isolation guard.

- [ ] **Step 3: Gate the update branch + wrap per-row persistence**

In `backend/app/services/position_adapter.py`, the loop currently does (NEW branch then UPDATE branch) without booking-time error handling. Wrap the persistence in a try/except and route the update branch through the gate. Replace the block from `existing = (...)` through `touched_positions.append(position)` with:

```python
        existing = (
            session.query(Position)
            .filter(Position.portfolio_id == portfolio.id, Position.source_trade_id == trade_id)
            .one_or_none()
        )
        try:
            if existing is None:
                position = book_position(
                    session,
                    BookingRequest(
                        portfolio_id=portfolio.id,
                        product=_product_booking_spec_from_mapping(mapping, source_payload),
                        quantity=mapping.quantity,
                        entry_price=mapping.entry_price,
                        status=mapping.status,
                        source_trade_id=trade_id,
                        source_row=row_number,
                        mapping_status=mapping.mapping_status,
                        mapping_error=mapping.mapping_error,
                        source_payload=source_payload,
                        engine_name=mapping.engine_name,
                        engine_kwargs=mapping.engine_kwargs,
                        actor="desk_user",
                        source="import",
                    ),
                )
            else:
                position = existing
                # Route updates through the same single gate as inserts so a
                # re-imported phoenix/scalar is validated, not trusted as-is.
                spec = prepare_booking_product_spec(
                    _product_booking_spec_from_mapping(mapping, source_payload),
                    engine_name=mapping.engine_name,
                )
                product = create_or_get_product(session, spec, reuse=True)
                position.product = product
                position.product_id = product.id
                hydrate_position_product_fields(position)
                link_position_underlying(session, position, source="import")
                position.engine_name = mapping.engine_name
                position.engine_kwargs = mapping.engine_kwargs
                position.quantity = mapping.quantity
                position.entry_price = mapping.entry_price
                position.status = mapping.status
                position.source_trade_id = trade_id
                position.source_row = row_number
                position.mapping_status = mapping.mapping_status
                position.mapping_error = mapping.mapping_error
                position.source_payload = source_payload
                reset_position_term_rows(session, position.id)
                session.add(position)
        except ValueError as exc:
            # The single gate rejected this row's terms. Isolate it as an error row
            # (booked with empty kwargs, which the gate skips) so one bad trade does
            # not abort the whole import.
            error_count += 1
            errors.append({"row": row_number, "trade_id": trade_id, "error": str(exc)})
            mapping = _error_mapping(row, str(exc))
            position = book_position(
                session,
                BookingRequest(
                    portfolio_id=portfolio.id,
                    product=_product_booking_spec_from_mapping(mapping, source_payload),
                    quantity=mapping.quantity,
                    entry_price=mapping.entry_price,
                    status=mapping.status,
                    source_trade_id=trade_id,
                    source_row=row_number,
                    mapping_status=mapping.mapping_status,
                    mapping_error=mapping.mapping_error,
                    source_payload=source_payload,
                    engine_name=mapping.engine_name,
                    engine_kwargs=mapping.engine_kwargs,
                    actor="desk_user",
                    source="import",
                ),
            )
        touched_positions.append(position)
```

Then import `prepare_booking_product_spec` at the top of the file — change:

```python
from .domains.booking import BookingRequest, ProductBookingSpec, book_position
```

to:

```python
from .domains.booking import (
    BookingRequest,
    ProductBookingSpec,
    book_position,
    prepare_booking_product_spec,
)
```

NOTE on the error-row re-book: `_error_mapping` sets `product_kwargs={}` and `quantark_class`/`product_type` to the structure label, which is **not** in `_GATED_BOOKING_TYPES`, so the gate is a no-op and the error row persists without re-raising. Verify this against the real `_error_mapping` / `product_spec_from_position_payload` behaviour; if an empty-kwargs spec still trips the gate, instead persist the error row by skipping `book_position` and recording the error in the batch only (discuss with your human partner).

- [ ] **Step 4: Run the import suite**

Run: `python -m pytest tests/test_position_import_pricing.py -q`
Expected: PASS, including the new isolation test and all existing adapter/import tests (the step-down snowball, sharkfin schedules, KI conventions, etc. are unchanged — they go through the unchanged snowball auto-detect path).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/position_adapter.py tests/test_position_import_pricing.py
git commit -m "feat(import): gate OTC update path + isolate invalid rows"
```

---

### Task 5: Document the deviation + reference note

**Files:**
- Modify: `backend/app/skills/references/products/build-contract.md`
- Test: `tests/test_reference_docs.py` (schema/marker guard — no new test needed; just keep it green)

**Context:** The agent-facing reference describes the per-family term contracts. Add a short note that complete termsheets (e.g. from OTC import) are validated-and-wrapped via `build_product(prebuilt=True)` rather than re-synthesized — so the "single producer" framing stays accurate, and a future reader understands why the OTC import adapter keeps its own schedule helpers.

- [ ] **Step 1: Add the note**

In `backend/app/skills/references/products/build-contract.md`, after the scalar/option table (do not disturb the existing rows), add a short paragraph:

```markdown
**Already-built termsheets (OTC import).** Channels that ingest *existing* trades
(e.g. the OTC position import) supply a complete QuantArk termsheet with explicit
observation dates and per-date barrier/rate schedules. These are validated and
wrapped verbatim via `build_product(..., prebuilt=True)` — never re-synthesized —
because a uniform periodic schedule cannot express a step-down. The single
producer/validation gate still applies; only the *input* is already complete.
```

Ensure the wording trips none of `test_reference_docs.py`'s archaeology-marker regex (no commit hashes, no `v1`, no `fixed this mistake`, etc.).

- [ ] **Step 2: Run the reference-doc guard**

Run: `python -m pytest tests/test_reference_docs.py -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add backend/app/skills/references/products/build-contract.md
git commit -m "docs(builders): note prebuilt validate-and-wrap for OTC import"
```

---

### Task 6: Full regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite from the repo root**

Run: `python -m pytest -q`
Expected: PASS apart from any **pre-existing, unrelated** failures (record them and confirm they fail identically on the base commit before dismissing — e.g. optional-dep `langchain_quickjs`/`deepseek`, and note: a `test_capability_assignments.py::test_quant_agent_tools_count_unchanged` 63-vs-64 failure is a known concurrent-agent drift, not from this work). The relevant invariants:
- `tests/test_product_builders.py` — `prebuilt=True` validate-and-wrap for scalars; snowball auto-detect/tidy/malformed-rejection unchanged.
- `tests/test_services_domains_booking.py` — every gated family validated; precise rejection on invalid terms.
- `tests/test_position_import_pricing.py` — all 8 families import + validate; bad rows isolated; step-down/sharkfin/KI schedules unchanged.

- [ ] **Step 2: Finish the development branch**

Announce: "I'm using the finishing-a-development-branch skill to complete this work."
**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present the options, execute the choice. Given the concurrent agent churning `main`, prefer the worktree-isolated keep-or-merge flow.

---

## Notes / risks

- **The spike (Task 1) is the real de-risk.** Phoenix + scalars have never been validated on import. If a family fails `validate_quantark_build`, that is a latent bug the gate now surfaces — handle it deliberately (fix the adapter kwarg), don't weaken the gate.
- **No re-synthesis, by decision.** `position_adapter`'s per-date schedule helpers stay. This deviates from the spec's literal "retire `position_adapter` synthesis," and is intentional: OTC trades carry heterogeneous step-down schedules the uniform synthesizer cannot express, and the spec mandates no import regression. Documented in Task 5.
- **Snowball path is byte-identical.** The snowball-family auto-detect condition in `build_product` is preserved exactly; snowball/ko_reset bookings still pass `prebuilt=False`. The widened gate only adds *new* validation for the 7 non-(SnowballOption/KnockOutResetSnowballOption) families.
- **Blast radius is bounded to 9 families.** Families `build_product` supports but OTC never emits (Futures/Spot/Asian/OneTouch/RangeAccrual/DoubleOneTouch) are deliberately left out of `_GATED_BOOKING_TYPES`; extending the gate to them is a characterized follow-up, not this plan.
- **`build_product` internal engine == adapter engine** for all 8 OTC families (verified), so `normalize`'s validate and `validate_booking_product_spec` agree; no double-gate engine mismatch.
- **Per-row isolation** prevents one malformed imported trade from aborting a whole workbook import — a robustness improvement the broadened validation makes necessary.
