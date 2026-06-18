# Try-Solve Channel Migration (Backend Construction + Solve) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route try-solve's per-row product construction through the single `build_product` producer, so a try-solved row yields canonical `product_kwargs` byte-identical to a direct `build_product` of the same economics — retiring the third independent kwargs factory (`_product_kwargs_for_row`).

**Architecture:** Migration **step 3** of the unified-product-schema strangler-fig (Foundation + RFQ already merged). Try-solve already builds an `RFQRequestDraft` and runs it through the shared `solve_rfq` + `executable_terms_for_quote` machinery the RFQ migration hardened. This plan makes the draft carry the **flat term contract**: a thin adapter maps each row to flat terms, fills the solve target with its initial guess, and calls `build_product` to synthesize a complete termsheet *before* validate/solve; after a solve, snowball-family rows regenerate through `build_product` (decision 7 — already implemented for `_BUILD_PRODUCT_FAMILIES`), while simple families keep the existing top-level patch.

**Tech Stack:** Python 3, pydantic, pytest. `build_product` (`services/domains/product_builders.py`); shared RFQ helpers `_executable_product_kwargs`/`executable_terms_for_quote`/`_BUILD_PRODUCT_FAMILIES` (`services/rfq.py`); QuantArk solver `quantark.solve_rfq`.

**Scope note (read first):** This plan is **backend only** — `services/try_solve.py`, `services/try_solve_registry.py` (read-only), and the try-solve service tests. The **TrySolve grid frontend** (columns/inputs for the snowball schedule fields `lockup_months`/`trade_start_date`/`observation_frequency` + disabling the per-row Solve until the contract is filled) is a **separate follow-on plan** (`try-solve-channel-migration-frontend`), exactly as the RFQ frontend was split. Snowball-family rows are testable at the service layer by supplying those fields in `row.fields` (a free `dict[str, Any]`), so the backend slice is independently verifiable.

**In scope (route through `build_product`), 11 product keys → QuantArk class:**

| product_key | quantark class | build_product builder | tier |
|---|---|---|---|
| vanilla | EuropeanVanillaOption | `_build_vanilla` | simple |
| digital | CashOrNothingDigitalOption | `_build_digital` | simple |
| single_sf | SingleSharkfinOption | `_build_single_sharkfin` | simple |
| double_sf | DoubleSharkfinOption | `_build_double_sharkfin` | simple |
| asian | AsianOption | `_build_asian` | simple |
| range_accrual | RangeAccrualOption | `_build_range_accrual` | simple |
| one_touch | OneTouchOption | `_build_one_touch` | simple |
| forward | Futures | `_build_futures` | simple |
| autocall | SnowballOption | `_build_snowball` | snowball |
| phoenix | PhoenixOption | `_build_phoenix` | snowball |
| knock_out_autocall | KnockOutResetSnowballOption | `_build_ko_reset_snowball` | snowball |

**Out of scope (no `build_product` builder — stay on the legacy `_product_kwargs_for_row` path):** `double_no_touch`, `double_one_touch` (both map to `DoubleOneTouchOption`, which has no builder in `_REGISTRY`). Extending `build_product` to cover them is a separate Foundation task. The migrated adapter must fall back to the legacy path for any key it does not cover, leaving these green.

---

## Key facts about the current code (verified)

- `solve_try_solve_row` (`try_solve.py:242`): `validate_try_solve_row` → `_row_to_rfq_draft` → `solve_rfq(draft)` → `executable_terms_for_quote(draft, "solve", quote_payload)`. Already uses the RFQ solve + executable-terms path.
- `_row_to_rfq_draft` (`try_solve.py`): builds `RFQRequestDraft(product_type=product.quantark_product_type, product_kwargs=_product_kwargs_for_row(...), unknown={field_path: quote_field.canonical_path, ...}, quote_mode="solve", ...)`.
- `_product_kwargs_for_row` (`try_solve.py:663`): the legacy `if row.product_key == ...: return {hardcoded QuantArk kwargs}` factory. Snowball branches emit a degenerate `barrier_config` with `ko_observation_dates: [maturity]` (single date, no real schedule) — the malformed shape `build_product` now rejects.
- `validate_try_solve_row` (`try_solve.py:~190`): builds the draft, runs `validate_quantark_build(draft.product_type, draft.product_kwargs, ...)`; returns `solver_ready`/`quantark_build_failed`/`missing_terms`.
- `_BUILD_PRODUCT_FAMILIES` (`rfq.py`) = `{SnowballOption, KnockOutResetSnowballOption, PhoenixOption}`. `executable_terms_for_quote` regenerates via `build_product` (decision 7) only for these; everything else gets the top-level `_set_quantark_unknown_path` patch.
- `_executable_product_kwargs` (`rfq.py`) is snowball-gated (passes non-snowball families through unchanged), so it is **not** reusable for try-solve's simple families — try-solve needs its own build-through adapter.
- `build_product` input contract differs from the legacy kwargs: `_common_option` requires **`maturity_years`** (legacy emits `maturity`); every family requires **`initial_price`** (the row's `_reference_price`); `_build_range_accrual` takes **flat** `lower_barrier`/`upper_barrier`/`accrual_rate` (legacy nests them in `range_config`).
- `TrySolveRowIn.fields` (`schemas.py:343`) is `dict[str, Any]` — snowball schedule inputs can be read from it without a schema change.
- Helpers to reuse: `_reference_price(row, market)`, `_term_price(row, key, ref, fallback)`, `_term_rate(row, key, fallback)`, `_term_amount(row, key, ref, fallback)`, `_maturity_years(row)`, `_pricing_market(row)`.

---

## Task 1: Spike — characterize each in-scope family's flat contract + solve target (throwaway)

**This is exploration, not TDD.** Deliverable: a recorded per-family mapping table the construction task depends on. Throw the script away after.

**The questions:** (1) For each of the 11 in-scope keys, what FLAT contract terms make `build_product` return `ok` (exact key names — `maturity_years`, `initial_price`, flat range keys, `_build_futures`'s inputs)? (2) What is each family's solve-target `field_path` (`quote_field.canonical_path`), and is it a top-level scalar (simple → top-level patch suffices) or a path into a synthesized schedule (snowball → needs regeneration)? (3) Confirm `forward`→`Futures` builds from the row's forward fields.

**Files:**
- Create (throwaway): `backend/scratch_try_solve_spike.py`

- [ ] **Step 1: Write the spike script**

```python
# backend/scratch_try_solve_spike.py  (THROWAWAY)
from datetime import date, timedelta
from app.services.domains.product_builders import build_product
from app.services.try_solve_registry import registry_by_key

future = (date.today() + timedelta(days=30)).isoformat()
# Minimal flat contracts per family (initial_price=100 reference, maturity_years=1).
CASES = {
    "EuropeanVanillaOption": {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0, "contract_multiplier": 1.0},
    "CashOrNothingDigitalOption": {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0, "cash_payoff": 10.0, "contract_multiplier": 1.0},
    "SingleSharkfinOption": {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0, "barrier": 120.0, "participation_rate": 1.0, "contract_multiplier": 1.0},
    "DoubleSharkfinOption": {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0, "lower_barrier": 80.0, "upper_barrier": 120.0, "participation_rate": 1.0, "contract_multiplier": 1.0},
    "AsianOption": {"initial_price": 100.0, "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0, "averaging_frequency": "MONTHLY", "contract_multiplier": 1.0},
    "RangeAccrualOption": {"initial_price": 100.0, "maturity_years": 1.0, "lower_barrier": 80.0, "upper_barrier": 120.0, "accrual_rate": 0.1, "observation_frequency": "DAILY", "contract_multiplier": 1.0},
    "OneTouchOption": {"initial_price": 100.0, "maturity_years": 1.0, "barrier": 120.0, "cash_payoff": 10.0, "barrier_direction": "UP", "touch_type": "ONE_TOUCH", "contract_multiplier": 1.0},
    "Futures": {"initial_price": 100.0, "maturity_years": 1.0, "underlying": "000905.SH", "basis": 0.0, "contract_multiplier": 1.0},
    "SnowballOption": {"initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0, "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15, "lockup_months": 3, "trade_start_date": future, "observation_frequency": "MONTHLY", "contract_multiplier": 1.0},
    "PhoenixOption": {"initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0, "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.0, "coupon_barrier_pct": 85.0, "coupon_rate": 0.01, "lockup_months": 3, "trade_start_date": future, "observation_frequency": "MONTHLY", "contract_multiplier": 1.0},
    "KnockOutResetSnowballOption": {"initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0, "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": 0.15, "post_ko_barrier_pct": 100.0, "post_ko_rate": 0.10, "lockup_months": 3, "trade_start_date": future, "observation_frequency": "MONTHLY", "contract_multiplier": 1.0},
}
for cls, flat in CASES.items():
    b = build_product(cls, flat, underlying="000905.SH", currency="CNY")
    print(f"{cls}: ok={b.ok} missing={b.missing} err={(b.validation or {}).get('error')}")

# Solve targets: dump each in-scope product's quote_fields -> canonical_path.
reg = registry_by_key()
for key in ("vanilla","digital","single_sf","double_sf","asian","range_accrual",
            "one_touch","forward","autocall","phoenix","knock_out_autocall"):
    p = reg.get(key)
    if p is None:
        print(f"{key}: (not in registry)"); continue
    paths = {qf_key: qf.canonical_path for qf_key, qf in p.quote_fields.items() if getattr(qf, "solver_ready", False)}
    print(f"{key} -> {p.quantark_product_type}: solver paths {paths}")
```

- [ ] **Step 2: Run it and record findings**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python scratch_try_solve_spike.py`

Record in "Spike findings" below: for each class the `ok`/`missing` (so the construction map uses the exact required flat keys), and for each product_key the solver `canonical_path`s. Flag any class that does NOT build with the minimal contract (adjust the flat keys until `ok=True`, e.g. `_build_futures` may want a different key than `basis`/`underlying`).

- [ ] **Step 3: Record the decision and delete the script**

```bash
rm /Users/fuxinyao/open-otc-trading/backend/scratch_try_solve_spike.py
```

### Spike findings (recorded from Task 1 run on 2026-05-31)
- **Build coverage: ALL 11 classes build `ok=True missing=[]`** with the proposed minimal flat contracts in the Task-1 script — including `Futures` (`{initial_price, maturity_years, underlying, basis, contract_multiplier}`). **No per-family flat-key changes needed**; Task 2's `_flat_contract_for_row` map stands as written.
- **Solver `canonical_path`(s) per product_key:**
  - vanilla→`strike`; digital→`payout`; single_sf→`strike`/`barrier`/`participation_rate`; double_sf→`strike`/`upper_barrier`/`lower_barrier`/`participation_rate`; asian→`strike`; one_touch→`barrier`/`rebate`; forward→`basis` — all **top-level scalars** (existing patch path handles them).
  - range_accrual→`range_config.accrual_rate` (+`upper_barrier`/`lower_barrier`) — **nested but scalar**; the patch path navigates dotted paths, so no regeneration needed (RangeAccrualOption is not in `_BUILD_PRODUCT_FAMILIES`).
  - autocall→`barrier_config.ko_rate` (field key **`annualized_coupon`**) / `barrier_config.ko_barrier` (`ko_barrier`).
  - phoenix→`barrier_config.ko_rate` (`annualized_coupon`) / `coupon_config.coupon_rate` (`coupon_yield`) / `barrier_config.ko_barrier`.
  - knock_out_autocall→`barrier_config.ko_rate` (`annualized_coupon`) / `barrier_config.ko_barrier`.
- **CORRECTION 1 (quote_field key):** autocall/knock_out_autocall expose **`annualized_coupon`** for the coupon solve (NOT `coupon_yield`). Task 5's e2e + any snowball test must use `quote_field_key="annualized_coupon"`. phoenix uses `coupon_yield`→`coupon_config.coupon_rate`.
- **CORRECTION 2 (decision-6 placeholder fill):** the build-through must fill the solve target's FLAT key with `row.quote_request.initial_guess` before `build_product`. Two targets are RENAMED vs their flat input key, so a try-solve `_SOLVE_TARGET_FLAT_KEY` map is required (added to Task 3): `payout`→`cash_payoff`, `range_config.accrual_rate`→`accrual_rate`, `barrier_config.ko_rate`→`ko_rate`, `coupon_config.coupon_rate`→`coupon_rate`, `barrier_config.ko_barrier`→`ko_barrier_pct`; identity for `strike`/`barrier`/`lower_barrier`/`upper_barrier`/`participation_rate`/`basis`. Targets absent from the map are left as `_flat_contract_for_row` set them.
- **Known limitation (out of scope):** solving `barrier_config.ko_barrier` binds back to `ko_barrier_pct` (percent) while the solver finds an ABSOLUTE barrier — exact only when `initial_price==100`. The common snowball solve target is the coupon (`barrier_config.ko_rate`), which is clean; the ko_barrier solve target is left approximate (noted, not fixed here).

---

## Task 2: Map each row to the FLAT term contract

Replace the legacy `_product_kwargs_for_row` (which returns QuantArk kwargs) with `_flat_contract_for_row` (which returns `build_product` INPUT terms), for the 11 in-scope keys; the 2 out-of-scope keys raise `_UNMIGRATED` so callers fall back to the legacy path.

**Files:**
- Modify: `backend/app/services/try_solve.py`
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_flat_contract_for_vanilla_uses_build_product_inputs():
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import _flat_contract_for_row, _pricing_market, _maturity_years
    from app.services.try_solve_registry import registry_by_key

    row = TrySolveRowIn(product_key="vanilla",
                        fields={"underlying": "000905.SH", "spot": 100.0, "option_type": "CALL"},
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="strike", initial_guess=100.0))
    product = registry_by_key()["vanilla"]
    market = _pricing_market(row)
    flat = _flat_contract_for_row(row, product, market, _maturity_years(row),
                                  product.quote_fields["strike"])
    assert flat["initial_price"] == 100.0          # S0 from the reference price
    assert "maturity_years" in flat and "maturity" not in flat
    assert flat["option_type"] == "CALL"


def test_flat_contract_for_autocall_carries_snowball_contract_terms():
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import _flat_contract_for_row, _pricing_market, _maturity_years
    from app.services.try_solve_registry import registry_by_key

    row = TrySolveRowIn(product_key="autocall",
                        fields={"underlying": "000905.SH", "spot": 100.0,
                                "ko_barrier": 103.0, "ki_barrier": 75.0,
                                "lockup_months": 3, "trade_start_date": "2099-01-05",
                                "observation_frequency": "MONTHLY"},
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="coupon_yield", initial_guess=0.15))
    product = registry_by_key()["autocall"]
    market = _pricing_market(row)
    flat = _flat_contract_for_row(row, product, market, _maturity_years(row),
                                  next(iter(product.quote_fields.values())))
    assert "barrier_config" not in flat            # FLAT, not nested
    for key in ("ko_barrier_pct", "ki_barrier_pct", "lockup_months",
                "trade_start_date", "observation_frequency"):
        assert key in flat, key
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "flat_contract" -v`
Expected: FAIL — `_flat_contract_for_row` not defined.

- [ ] **Step 3: Implement**

In `backend/app/services/try_solve.py`, add the migrated-key set and the flat mapper. Keep `_product_kwargs_for_row` for now (Task 6 removes the migrated branches); `_flat_contract_for_row` is the new build_product-input mapper:

```python
# product_keys whose construction is migrated to build_product (the single
# producer). Out-of-scope keys (double_no_touch/double_one_touch -> no builder)
# are absent and fall back to the legacy _product_kwargs_for_row path.
_MIGRATED_PRODUCT_KEYS = frozenset({
    "vanilla", "digital", "single_sf", "double_sf", "asian",
    "range_accrual", "one_touch", "forward",
    "autocall", "phoenix", "knock_out_autocall",
})
_SNOWBALL_PRODUCT_KEYS = frozenset({"autocall", "phoenix", "knock_out_autocall"})


def _flat_contract_for_row(
    row: TrySolveRowIn,
    product: TrySolveProduct,
    market: PricingEnvironmentSnapshot,
    maturity: float,
    quote_field: TrySolveQuoteField,
) -> dict[str, Any]:
    """Map a row to the FLAT term contract that build_product consumes. The solve
    target is filled with its initial guess (a placeholder) so synthesis yields a
    complete, priceable termsheet the solver can start from (decision 6)."""
    del quote_field
    reference = _reference_price(row, market)
    strike = _term_price(row, "strike", reference, row.quote_request.initial_guess)
    option_type = str(row.fields.get("option_type") or "CALL").upper()
    base = {"initial_price": reference, "maturity_years": maturity, "contract_multiplier": 1.0}

    if row.product_key == "vanilla":
        return {**base, "strike": strike, "option_type": option_type}
    if row.product_key == "digital":
        return {**base, "strike": strike, "option_type": option_type,
                "cash_payoff": _term_amount(row, "payout", reference, 0.1)}
    if row.product_key == "single_sf":
        return {**base, "strike": strike, "option_type": option_type,
                "barrier": _term_price(row, "barrier", reference, 1.2),
                "participation_rate": _term_rate(row, "participation_rate", 1.0)}
    if row.product_key == "double_sf":
        return {**base, "strike": strike, "option_type": option_type,
                "lower_barrier": _term_price(row, "lower_barrier", reference, 0.8),
                "upper_barrier": _term_price(row, "upper_barrier", reference, 1.2),
                "participation_rate": _term_rate(row, "participation_rate", 1.0)}
    if row.product_key == "asian":
        return {**base, "strike": strike, "option_type": option_type,
                "averaging_frequency": str(row.fields.get("averaging_frequency") or "MONTHLY").upper()}
    if row.product_key == "range_accrual":
        return {**base,
                "lower_barrier": _term_price(row, "lower_barrier", reference, 0.8),
                "upper_barrier": _term_price(row, "upper_barrier", reference, 1.2),
                "accrual_rate": _term_rate(row, "coupon_yield", 0.1),
                "observation_frequency": str(row.fields.get("observation_frequency") or "DAILY").upper()}
    if row.product_key == "one_touch":
        barrier = _term_price(row, "barrier", reference, 1.2)
        return {**base, "barrier": barrier, "cash_payoff": _term_amount(row, "rebate", reference, 0.1),
                "barrier_direction": "UP" if barrier >= reference else "DOWN",
                "touch_type": "ONE_TOUCH"}
    if row.product_key == "forward":
        return {**base, "underlying": str(row.fields.get("underlying") or row.product_key),
                "basis": _term_rate(row, "basis", 0.0)}
    if row.product_key in _SNOWBALL_PRODUCT_KEYS:
        flat = {**base, "strike": _term_price(row, "strike", reference, 1.0),
                "ko_barrier_pct": _pct_term(row, "ko_barrier", reference, 1.03),
                "ki_barrier_pct": _pct_term(row, "ki_barrier", reference, 0.75),
                "ko_rate": _term_rate(row, "coupon_yield", 0.1),
                "lockup_months": _as_float(row.fields.get("lockup_months")),
                "trade_start_date": row.fields.get("trade_start_date"),
                "observation_frequency": str(row.fields.get("observation_frequency") or "").upper() or None}
        if row.product_key == "phoenix":
            flat["ko_rate"] = _term_rate(row, "annualized_coupon", 0.0)
            flat["coupon_barrier_pct"] = _pct_term(row, "ki_barrier", reference, 0.75)
            flat["coupon_rate"] = _term_rate(row, "coupon_yield", 0.1)
        if row.product_key == "knock_out_autocall":
            flat["post_ko_barrier_pct"] = flat["ko_barrier_pct"]
            flat["post_ko_rate"] = flat["ko_rate"]
        return flat
    raise _UnmigratedProductKey(row.product_key)


class _UnmigratedProductKey(Exception):
    """Raised for product_keys not yet routed through build_product (callers
    fall back to the legacy _product_kwargs_for_row path)."""


def _pct_term(row: TrySolveRowIn, field_key: str, reference: float, fallback_mult: float) -> float:
    """A barrier expressed as a percent of initial_price (build_product's *_pct
    inputs). Rows carry absolute or multiple-of-spot barriers; normalize to %."""
    absolute = _term_price(row, field_key, reference, fallback_mult)
    return (absolute / reference) * 100.0 if reference else absolute
```

(If Task 1 recorded different required keys for any class, adjust that branch.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "flat_contract" -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/try_solve.py tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(try-solve): map rows to the flat term contract for build_product"
```

---

## Task 3: Adapter — synthesize a complete termsheet from the row (build-through)

`_build_row_termsheet(row, product, market, maturity, quote_field) -> (product_kwargs, missing)`: for migrated keys, build the flat contract and call `build_product`; for unmigrated keys, return the legacy kwargs unchanged so callers stay green.

**Files:**
- Modify: `backend/app/services/try_solve.py`
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_row_termsheet_synthesizes_complete_vanilla():
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import _build_row_termsheet, _pricing_market, _maturity_years
    from app.services.try_solve_registry import registry_by_key

    row = TrySolveRowIn(product_key="vanilla",
                        fields={"underlying": "000905.SH", "spot": 100.0, "option_type": "CALL"},
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="strike", initial_guess=100.0))
    product = registry_by_key()["vanilla"]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(row, product, market, _maturity_years(row),
                                           product.quote_fields["strike"])
    assert missing == []
    assert kwargs["maturity"] == _maturity_years(row)   # build_product output uses `maturity`
    assert kwargs["initial_price"] == 100.0


def test_build_row_termsheet_reports_missing_snowball_schedule_inputs():
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import _build_row_termsheet, _pricing_market, _maturity_years
    from app.services.try_solve_registry import registry_by_key

    row = TrySolveRowIn(product_key="autocall",
                        fields={"underlying": "000905.SH", "spot": 100.0,
                                "ko_barrier": 103.0, "ki_barrier": 75.0},  # no lockup/start/freq
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="coupon_yield", initial_guess=0.15))
    product = registry_by_key()["autocall"]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(row, product, market, _maturity_years(row),
                                           next(iter(product.quote_fields.values())))
    assert kwargs == {}
    assert any("observation_frequency" in m for m in missing)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "build_row_termsheet" -v`
Expected: FAIL — `_build_row_termsheet` not defined.

- [ ] **Step 3: Implement**

```python
from .domains.product_builders import build_product

def _build_row_termsheet(
    row: TrySolveRowIn,
    product: TrySolveProduct,
    market: PricingEnvironmentSnapshot,
    maturity: float,
    quote_field: TrySolveQuoteField,
) -> tuple[dict[str, Any], list[str]]:
    """Complete QuantArk termsheet for a migrated row via build_product (filling
    the solve target with its initial guess). Unmigrated keys fall back to the
    legacy factory. Returns (product_kwargs, missing); missing non-empty iff the
    row's contract is unfilled (e.g. a snowball missing schedule inputs)."""
    if row.product_key not in _MIGRATED_PRODUCT_KEYS:
        return _product_kwargs_for_row(row, product, market, maturity, quote_field), []
    flat = _flat_contract_for_row(row, product, market, maturity, quote_field)
    built = build_product(
        product.quantark_product_type or "EuropeanVanillaOption",
        flat,
        underlying=str(row.fields.get("underlying") or row.product_key),
        currency=market.currency,
        solve_target=quote_field.canonical_path,
    )
    if built.missing:
        return {}, built.missing
    return dict(built.product_kwargs), []
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "build_row_termsheet" -v`
Expected: PASS (both).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/try_solve.py tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(try-solve): build-through adapter synthesizes termsheet via build_product"
```

---

## Task 4: Route validate + solve through the adapter

Make `validate_try_solve_row` gate on the build_product contract (precise missing, not the opaque quad), and `solve_try_solve_row` materialize the complete termsheet before `solve_rfq`, keeping the FLAT draft for snowball-family `executable_terms_for_quote` regeneration (decision 7).

**Files:**
- Modify: `backend/app/services/try_solve.py` (`validate_try_solve_row`, `solve_try_solve_row`, `_row_to_rfq_draft`)
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_solve_vanilla_row_matches_direct_build(session):
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import solve_try_solve_row
    from app.services.domains.product_builders import build_product

    row = TrySolveRowIn(product_key="vanilla",
                        fields={"underlying": "000905.SH", "spot": 100.0, "option_type": "CALL"},
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="strike",
                                                             initial_guess=100.0, target_label="price", target_value=8.0))
    out = solve_try_solve_row(row, session)
    assert out.status == "solved", out.diagnostics
    # executable terms are a complete, bookable termsheet (build_product output shape)
    assert out.executable_terms["product_kwargs"]["maturity"]   # `maturity`, not `maturity_years`
    assert "initial_price" in out.executable_terms["product_kwargs"]


def test_solve_autocall_row_without_schedule_inputs_reports_missing(session):
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import solve_try_solve_row

    row = TrySolveRowIn(product_key="autocall",
                        fields={"underlying": "000905.SH", "spot": 100.0,
                                "ko_barrier": 103.0, "ki_barrier": 75.0},  # no schedule inputs
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="coupon_yield",
                                                             initial_guess=0.15, target_label="price", target_value=0.0))
    out = solve_try_solve_row(row, session)
    assert out.status in {"missing_terms", "quantark_build_failed"}
    # precise contract gap, never the opaque "KO observation ... required"
    assert not any("KO observation" in d for d in out.diagnostics)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "matches_direct_build or without_schedule_inputs" -v`
Expected: FAIL — vanilla executable terms currently carry `maturity_years` from the legacy path / autocall surfaces the opaque quad error.

- [ ] **Step 3: Implement**

In `validate_try_solve_row`, replace the `validate_quantark_build(draft...)` block (for migrated keys) with the build-through:

```python
    if row.product_key in _MIGRATED_PRODUCT_KEYS:
        market = _pricing_market(resolved_row)
        quote_field = product.quote_fields[row.quote_request.quote_field_key]
        kwargs, missing = _build_row_termsheet(
            resolved_row, product, market, _maturity_years(resolved_row), quote_field
        )
        if missing:
            return _row_out(resolved_row, "missing_terms", diagnostics + missing, product)
        result = validate_quantark_build(
            product.quantark_product_type, kwargs, market,
            product.default_engine_name, {},
        )
        if not result.ok:
            return _row_out(resolved_row, "quantark_build_failed",
                            diagnostics + [result.error or "QuantArk product build failed"], product)
        return _row_out(resolved_row, "solver_ready", diagnostics, product)
    # legacy path unchanged for unmigrated keys
    draft = _row_to_rfq_draft(resolved_row, product)
    result = validate_quantark_build(draft.product_type, draft.product_kwargs, draft.market,
                                     draft.engine_spec.engine_name, draft.engine_spec.engine_kwargs)
    ...  # (existing tail)
```

In `solve_try_solve_row`, materialize the complete termsheet before `solve_rfq` and keep the FLAT draft for snowball regeneration:

```python
    resolved_row, _diagnostics = _row_with_resolved_market(session, row)
    product = registry_by_key().get(row.product_key)
    flat_draft = _row_to_rfq_draft(resolved_row, product)   # product_kwargs = FLAT contract (migrated) / legacy
    market = _pricing_market(resolved_row)
    quote_field = product.quote_fields[row.quote_request.quote_field_key]

    priced_draft = flat_draft
    if row.product_key in _MIGRATED_PRODUCT_KEYS:
        exec_kwargs, missing = _build_row_termsheet(
            resolved_row, product, market, _maturity_years(resolved_row), quote_field
        )
        if missing:
            return _row_out(resolved_row, "missing_terms", missing, product)
        priced_draft = flat_draft.model_copy(update={"product_kwargs": exec_kwargs})

    result = solve_rfq(priced_draft)
    if not result.ok:
        return _row_out(resolved_row, "solve_failed", [result.error or "QuantArk solve failed"], product)

    quote_payload = result.data or {}
    from .rfq import executable_terms_for_quote, _BUILD_PRODUCT_FAMILIES
    # Snowball families regenerate from the FLAT draft (decision 7); simple
    # families top-level-patch the COMPLETE termsheet.
    terms_draft = flat_draft if flat_draft.product_type in _BUILD_PRODUCT_FAMILIES else priced_draft
    executable_terms = executable_terms_for_quote(terms_draft, "solve", quote_payload)
    return _row_out(resolved_row, "solved", _diagnostics, product,
                    solved_value=quote_payload.get("solved_value"),
                    model_price=quote_payload.get("achieved_price"),
                    residual=quote_payload.get("residual"),
                    executable_terms=executable_terms)
```

And make `_row_to_rfq_draft` set `product_kwargs=_flat_contract_for_row(...)` for migrated keys (else `_product_kwargs_for_row(...)`):

```python
    if row.product_key in _MIGRATED_PRODUCT_KEYS:
        product_kwargs = _flat_contract_for_row(row, product, market, maturity, quote_field)
    else:
        product_kwargs = _product_kwargs_for_row(row, product, market, maturity, quote_field)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "matches_direct_build or without_schedule_inputs" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/try_solve.py tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(try-solve): validate+solve through build_product; flat draft for snowball regen"
```

---

## Task 5: End-to-end — a snowball-family row solves and equals a direct build

Prove a fully-specified autocall row (schedule inputs in `row.fields`) solves and its executable termsheet's KO schedule equals a direct `build_product` of the same solved economics (decision 7 across the try-solve channel).

**Files:**
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write the test**

```python
def test_solve_autocall_row_regenerates_schedule_equal_to_direct_build(session):
    from datetime import date, timedelta
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import solve_try_solve_row
    from app.services.domains.product_builders import build_product

    start = (date.today() + timedelta(days=30)).isoformat()
    row = TrySolveRowIn(product_key="autocall",
                        fields={"underlying": "000905.SH", "spot": 100.0,
                                "ko_barrier": 103.0, "ki_barrier": 75.0,
                                "lockup_months": 3, "trade_start_date": start,
                                "observation_frequency": "MONTHLY"},
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="coupon_yield",
                                                             initial_guess=0.15, target_label="price", target_value=5.0))
    out = solve_try_solve_row(row, session)
    assert out.status == "solved", out.diagnostics
    solved = out.solved_value
    sched = out.executable_terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"]
    # every record's coupon reflects the SOLVED value, not the 0.15 placeholder
    assert all(abs(r["return_rate"] - solved) < 1e-9 for r in sched["records"])
    direct = build_product("SnowballOption",
                           {"initial_price": 100.0, "strike": 100.0, "maturity_years": 1.0,
                            "ko_barrier_pct": 103.0, "ki_barrier_pct": 75.0, "ko_rate": solved,
                            "lockup_months": 3, "trade_start_date": start, "observation_frequency": "MONTHLY",
                            "contract_multiplier": 1.0},
                           underlying="000905.SH", currency="CNY")
    assert direct.ok
    assert sched == direct.product_kwargs["barrier_config"]["ko_observation_schedule"]
```

(Set `target_value` from the spike's observed price band if 5.0 does not bracket; the `solved`-vs-direct equality is independent of the exact target.)

- [ ] **Step 2: Run it**

Run: `cd /Users/fuxinyao/open-otc-trading/backend && python -m pytest ../tests/test_try_solve.py -k "regenerates_schedule_equal_to_direct_build" -v`
Expected: PASS. If the solve does not bracket, adjust `target_value` to the spike's band; confirm `_maturity_years` default keeps the schedule short enough to price.

- [ ] **Step 3: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "test(try-solve): autocall row solve regenerates schedule == direct build"
```

---

## Task 6: Retire the migrated legacy branches + full regression

Remove the now-dead `_product_kwargs_for_row` branches for the 11 migrated keys (leaving only the 2 unmigrated `double_*touch` branches + the final `raise`), and run the full try-solve + RFQ + Foundation regression.

**Files:**
- Modify: `backend/app/services/try_solve.py`
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Delete migrated branches**

In `_product_kwargs_for_row`, delete the `vanilla/digital/single_sf/double_sf/asian/forward/range_accrual/one_touch/autocall/phoenix/knock_out_autocall` branches; keep only `double_no_touch`/`double_one_touch` and the trailing `raise ValueError(...)`. (Those two keys still reach it via the `_MIGRATED_PRODUCT_KEYS` fallback in `_row_to_rfq_draft`/`_build_row_termsheet`.)

- [ ] **Step 2: Pin that unmigrated keys still use the legacy path**

```python
def test_double_one_touch_stays_on_legacy_path(session):
    from app.schemas import TrySolveRowIn, TrySolveQuoteRequestIn
    from app.services.try_solve import solve_try_solve_row

    row = TrySolveRowIn(product_key="double_one_touch",
                        fields={"underlying": "000905.SH", "spot": 100.0,
                                "upper_barrier": 120.0, "lower_barrier": 80.0},
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="rebate",
                                                             initial_guess=0.1, target_label="price", target_value=0.2))
    out = solve_try_solve_row(row, session)
    assert out.status in {"solved", "solve_failed", "quantark_build_failed", "missing_terms", "unsupported_quote_field"}
    # whatever the outcome, it never hit build_product (no DoubleOneTouch builder)
    assert not any("Unsupported product family" in d for d in out.diagnostics)
```

- [ ] **Step 3: Run the full try-solve + cross-channel regression**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py tests/test_services_domains_rfq.py tests/test_product_builders.py tests/test_product_booking.py -q`
Expected: all PASS. Investigate any failure; do not weaken tests. (Run from repo root.)

- [ ] **Step 4: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/try_solve.py tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "refactor(try-solve): retire migrated kwargs factory branches"
```

---

## Self-Review

**Spec coverage (migration step 3 / try-solve):**
- All 11 build_product-covered keys route through `build_product` → **Tasks 2-4** ✓
- Adapter fills the solve target with its initial guess → **Task 3** (`_build_row_termsheet` + `_flat_contract_for_row` placeholder) ✓
- Validate gates on the contract (precise missing) → **Task 4** ✓
- Snowball-family regeneration (decision 7) reused via `executable_terms_for_quote` + flat draft → **Tasks 4, 5** ✓
- Snowball schedule inputs read from `row.fields` (no schema change) → **Tasks 2, 4** ✓
- Try-solved row == direct `build_product` → **Task 5** ✓
- Unmigrated keys (`double_no_touch`/`double_one_touch`) stay green on legacy path → **Tasks 3, 6** ✓

**Deferred (separate plans, explicitly):** TrySolve grid frontend (schedule columns + per-row Solve gating); `build_product` builders for `DoubleOneTouchOption` (would bring the last 2 keys in); OTC-import channel; final cleanup.

**The one genuine risk** is isolated in **Task 1 (spike)**: the exact required flat keys per family and each family's solve `canonical_path`. Tasks 2-4 carry a concrete proposed map; the spike confirms/adjusts per family. `_build_futures` (forward) is the least-certain contract — the spike flags it.

**Placeholder scan:** the only deliberately-unfilled values are `target_value` for solve tests (spike's price band — steps say how to set) and the Spike findings section. No "TBD"/"add error handling"/"similar to" placeholders.

**Type/name consistency:** `_MIGRATED_PRODUCT_KEYS`/`_SNOWBALL_PRODUCT_KEYS` (Task 2) used in Tasks 3-6; `_flat_contract_for_row` (Task 2) used by `_build_row_termsheet` (Task 3) + `_row_to_rfq_draft` (Task 4); `_build_row_termsheet` returns `(dict, list[str])` consistently; reuses `_BUILD_PRODUCT_FAMILIES`/`executable_terms_for_quote` from `rfq.py` (Task 4).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-31-try-solve-channel-migration-backend.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks. NOTE: do **Task 1 (spike) first and report its findings** before dispatching Task 2, because the recorded per-family flat keys / solve paths may change the exact mappings in Tasks 2-4.

**2. Inline Execution** — execute tasks in this session using executing-plans, with the Task 1 spike as a hard checkpoint.

**Which approach?**
