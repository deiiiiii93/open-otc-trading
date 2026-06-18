# DoubleOneTouchOption Builder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `DoubleOneTouchOption` builder to `build_product` and route the try-solve `double_no_touch` / `double_one_touch` channels through it, retiring the last legacy per-row QuantArk-kwargs producer.

**Architecture:** The strangler-fig migration moves the final two try-solve product keys onto the single producer (`build_product`). A new per-family builder (`_build_double_one_touch`) turns flat economic terms into validated `DoubleOneTouchOption` kwargs; the try-solve adapter (`_flat_contract_for_row`) emits the flat contract and sets `touch_type` from `row.product_key` (both keys share one QuantArk class). With these two keys migrated, `_MIGRATED_PRODUCT_KEYS == _SUPPORTED_SOLVE_PRODUCT_KEYS`, so the legacy `_product_kwargs_for_row` fallback becomes dead code and is removed.

**Tech Stack:** Python 3.11, pytest (rootdir = repo root, `pythonpath = ["backend"]`, `testpaths = ["tests"]`). QuantArk derivatives library (vendored). No frontend changes — `double_no_touch` / `double_one_touch` already have grid columns and catalog entries; only their construction path moves.

**Pre-flight (worktree isolation):** A concurrent agent shares this repo and churns the shared `HEAD`/branches. Before executing, create an isolated worktree (superpowers:using-git-worktrees) so cherry-picking/branch state can't interleave. Plan-writing (this file) does not need one; execution does.

**Out of scope:** OTC-import channel (`position_adapter`), and any frontend work. This plan finishes the try-solve channel's producer unification only.

---

## File Structure

- `backend/app/services/domains/product_builders.py` — add `_build_double_one_touch`, register it in `_REGISTRY`, and add the `DoubleOneTouchOption` → `OneTouchAnalyticalEngine` entry to `_ENGINE_BY_CLASS`. (Responsibility: the single deterministic producer for every QuantArk family.)
- `backend/app/services/try_solve.py` — add a `double_no_touch` / `double_one_touch` branch to `_flat_contract_for_row`; add both keys to `_MIGRATED_PRODUCT_KEYS`; in Task 3, delete the now-dead `_product_kwargs_for_row` and collapse its two call sites. (Responsibility: row → flat-contract adapter + build-through orchestration.)
- `tests/test_product_builders.py` — extend the scalar-family build/validate coverage with `DoubleOneTouchOption` (both touch directions) + a missing-terms test.
- `tests/test_try_solve.py` — replace `test_double_one_touch_stays_on_legacy_path` with a test proving the row now routes through `build_product`; the existing `test_validate_try_solve_row_builds_every_quantark_backed_product` must stay green.

---

### Task 1: `_build_double_one_touch` builder + engine map + registry

**Files:**
- Modify: `backend/app/services/domains/product_builders.py` (add builder after `_build_one_touch` ~line 365; add `_ENGINE_BY_CLASS` entry ~line 34; add `_REGISTRY` entry ~line 478)
- Test: `tests/test_product_builders.py`

**Context for the implementer:** `DoubleOneTouchOption` is a twin-barrier touch product. The legacy producer built it as exactly `{maturity, upper_barrier, lower_barrier, rebate, touch_type}` (see the soon-to-be-deleted `_product_kwargs_for_row`). Mirror `_build_one_touch` (lines 344–364): it requires `initial_price` as the validation spot **but does not put it in the kwargs**, and it omits `strike` / `option_type` / `contract_multiplier`. The cash payoff's flat-contract input key is `cash_payoff` (consistent with `one_touch` and `digital`), which maps to the QuantArk kwarg `rebate`. `touch_type` defaults to `DOUBLE_ONE_TOUCH`; `double_no_touch` rows pass `DOUBLE_NO_TOUCH` explicitly.

- [ ] **Step 1: Write the failing tests**

Add two parametrized cases to the existing `test_scalar_families_build_and_validate` table in `tests/test_product_builders.py` (the table around lines 100–124, immediately after the `OneTouchOption` case at lines 114–117):

```python
        ("DoubleOneTouchOption",
         {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
          "cash_payoff": 10.0, "touch_type": "DOUBLE_ONE_TOUCH", "maturity_years": 1.0},
         "OneTouchAnalyticalEngine"),
        ("DoubleOneTouchOption",
         {"initial_price": 100.0, "upper_barrier": 120.0, "lower_barrier": 80.0,
          "cash_payoff": 10.0, "touch_type": "DOUBLE_NO_TOUCH", "maturity_years": 1.0},
         "OneTouchAnalyticalEngine"),
```

Then add a standalone test (place it after `test_vanilla_missing_strike_reported`, ~line 137):

```python
def test_double_one_touch_missing_barriers_and_payoff_reported():
    result = build_product("DoubleOneTouchOption",
                           {"initial_price": 100.0, "maturity_years": 1.0})
    assert result.ok is False
    assert "upper_barrier" in result.missing
    assert "lower_barrier" in result.missing
    assert "cash_payoff" in result.missing


def test_double_one_touch_kwargs_match_legacy_shape_and_default_touch_type():
    result = build_product("DoubleOneTouchOption",
                           {"initial_price": 100.0, "upper_barrier": 120.0,
                            "lower_barrier": 80.0, "cash_payoff": 10.0,
                            "maturity_years": 1.0})
    assert result.ok is True, result.validation
    kwargs = result.product_kwargs
    # Exact legacy shape: rebate (not cash_payoff), no strike/option_type/multiplier,
    # no initial_price in the kwargs (it is only the validation spot).
    assert set(kwargs) == {"maturity", "upper_barrier", "lower_barrier",
                           "rebate", "touch_type"}
    assert kwargs["rebate"] == 10.0
    assert kwargs["touch_type"] == "DOUBLE_ONE_TOUCH"  # default when omitted
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest "tests/test_product_builders.py::test_double_one_touch_missing_barriers_and_payoff_reported" "tests/test_product_builders.py::test_double_one_touch_kwargs_match_legacy_shape_and_default_touch_type" -v`

Expected: FAIL. The missing-terms test fails because `build_product` returns `warnings=["unsupported_family: DoubleOneTouchOption"]` (no builder registered) rather than the three missing keys; the shape test fails for the same reason (`result.ok is False`). The parametrized cases fail with `result.engine_name == "BlackScholesEngine"` (no `_ENGINE_BY_CLASS` entry) and `result.ok is False`.

- [ ] **Step 3: Add the engine mapping**

In `backend/app/services/domains/product_builders.py`, add to the `_ENGINE_BY_CLASS` dict (after the `"OneTouchOption": "OneTouchAnalyticalEngine",` line ~25):

```python
    "DoubleOneTouchOption": "OneTouchAnalyticalEngine",
```

- [ ] **Step 4: Add the builder**

In `backend/app/services/domains/product_builders.py`, add this function immediately after `_build_one_touch` (after line 364):

```python
def _build_double_one_touch(terms: dict, *, quantark_class: str) -> _Out:
    # DoubleOneTouchOption straddles two barriers; payoff is `rebate`, and
    # `touch_type` (DOUBLE_ONE_TOUCH / DOUBLE_NO_TOUCH) selects touch-in vs
    # touch-out. Mirrors OneTouch: no strike/option_type/contract_multiplier,
    # and initial_price is the validation spot only (not a product kwarg).
    out = _Out()
    _initial_price(terms, out)  # required as the position's S0 / validation spot
    pk: dict[str, Any] = {}
    m = _num(terms.get("maturity_years"))
    if m is None:
        out.missing.append("maturity_years")
    else:
        pk["maturity"] = m
    upper = _num(_require(terms, out, "upper_barrier"))
    lower = _num(_require(terms, out, "lower_barrier"))
    cash = _num(_require(terms, out, "cash_payoff"))
    if upper is not None:
        pk["upper_barrier"] = upper
    if lower is not None:
        pk["lower_barrier"] = lower
    if cash is not None:
        pk["rebate"] = cash
    pk["touch_type"] = str(terms.get("touch_type", "DOUBLE_ONE_TOUCH")).upper()
    out.product_kwargs = pk
    return out
```

- [ ] **Step 5: Register the builder**

In `backend/app/services/domains/product_builders.py`, add to `_REGISTRY` (after the `"OneTouchOption": _build_one_touch,` line ~478):

```python
    "DoubleOneTouchOption": _build_double_one_touch,
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest tests/test_product_builders.py -v`

Expected: PASS. All scalar-family cases (including the two new `DoubleOneTouchOption` rows) pass, and the two new standalone tests pass. No other test in the file regresses.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "feat(builders): add DoubleOneTouchOption build_product builder"
```

---

### Task 2: Route try-solve double-touch rows through `build_product`

**Files:**
- Modify: `backend/app/services/try_solve.py` (`_MIGRATED_PRODUCT_KEYS` ~line 703; `_flat_contract_for_row` — add a branch before the snowball branch ~line 789)
- Test: `tests/test_try_solve.py`

**Context for the implementer:** `_flat_contract_for_row` (lines 737–812) maps a row to the flat term contract `build_product` consumes. Every input is filled with a fallback (via `_term_price` / `_term_amount`), so `missing` is always empty for simple families — the solve-target value arrives as its initial guess and the solver overwrites it downstream. `base` (lines 751–755) already supplies `initial_price`, `maturity_years`, and `contract_multiplier`; the touch builder ignores `contract_multiplier`, so the emitted kwargs still match the legacy shape exactly. Both product keys share the QuantArk class `DoubleOneTouchOption`, so `touch_type` must be derived from `row.product_key`. `double_no_touch` / `double_one_touch` are the only keys still absent from `_MIGRATED_PRODUCT_KEYS`.

- [ ] **Step 1: Write the failing test**

In `tests/test_try_solve.py`, **replace** `test_double_one_touch_stays_on_legacy_path` (lines 1080–1096) with the following two tests:

```python
def test_double_touch_flat_contract_sets_touch_type_from_product_key():
    from app.services.try_solve import (
        _flat_contract_for_row, _pricing_market, _maturity_years,
    )

    def _flat(product_key):
        row = TrySolveRowIn(
            row_id="r1", product_key=product_key,
            fields={"underlying": "000905.SH", "notional": 1_000_000,
                    "start_date": "2026-05-13", "tenor_months": 12,
                    "upper_barrier": 1.2, "lower_barrier": 0.8},
            market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
            quote_request=TrySolveQuoteRequestIn(
                quote_field_key="rebate", initial_guess=0.1,
                target_label="price", target_value=0.2),
        )
        product = registry_by_key()[product_key]
        market = _pricing_market(row)
        return _flat_contract_for_row(
            row, product, market, _maturity_years(row),
            product.quote_fields["rebate"],
        )

    no_touch = _flat("double_no_touch")
    one_touch = _flat("double_one_touch")
    assert no_touch["touch_type"] == "DOUBLE_NO_TOUCH"
    assert one_touch["touch_type"] == "DOUBLE_ONE_TOUCH"
    # flat-contract input keys (build_product INPUT names), not QuantArk kwargs
    for flat in (no_touch, one_touch):
        assert {"upper_barrier", "lower_barrier", "cash_payoff",
                "initial_price", "maturity_years"} <= set(flat)


def test_double_one_touch_routes_through_build_product(session):
    from app.services.try_solve import _build_row_termsheet, _pricing_market, _maturity_years

    row = TrySolveRowIn(
        row_id="r1", product_key="double_one_touch",
        fields={"underlying": "000905.SH", "notional": 1_000_000,
                "start_date": "2026-05-13", "tenor_months": 12,
                "upper_barrier": 1.2, "lower_barrier": 0.8},
        market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
        quote_request=TrySolveQuoteRequestIn(
            quote_field_key="rebate", initial_guess=0.1,
            target_label="price", target_value=0.2),
    )
    product = registry_by_key()["double_one_touch"]
    market = _pricing_market(row)
    kwargs, missing = _build_row_termsheet(
        row, product, market, _maturity_years(row),
        product.quote_fields["rebate"],
    )
    assert missing == []
    # build_product output: rebate (not cash_payoff), touch direction preserved
    assert kwargs["touch_type"] == "DOUBLE_ONE_TOUCH"
    assert "rebate" in kwargs and "cash_payoff" not in kwargs
    assert set(kwargs) == {"maturity", "upper_barrier", "lower_barrier",
                           "rebate", "touch_type"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest "tests/test_try_solve.py::test_double_touch_flat_contract_sets_touch_type_from_product_key" "tests/test_try_solve.py::test_double_one_touch_routes_through_build_product" -v`

Expected: FAIL. `_flat_contract_for_row` has no double-touch branch, so it falls through to `raise _UnmigratedProductKey(row.product_key)` (line 812) — the first test errors with `_UnmigratedProductKey`. The second test fails because `double_one_touch` is not in `_MIGRATED_PRODUCT_KEYS`, so `_build_row_termsheet` takes the legacy fallback and returns the legacy `{upper_barrier, lower_barrier, rebate, touch_type, maturity}` — but built via `_product_kwargs_for_row`, the assertion `"cash_payoff" not in kwargs` would pass yet the routing-through-build_product intent isn't met; more concretely the legacy shape is returned without going through `build_product`. (It fails deterministically once Step 3/4 are pending because the flat branch does not exist.)

- [ ] **Step 3: Add the flat-contract branch**

In `backend/app/services/try_solve.py`, inside `_flat_contract_for_row`, add this branch immediately before the `if row.product_key in _SNOWBALL_PRODUCT_KEYS:` block (before line 789):

```python
    if row.product_key in {"double_no_touch", "double_one_touch"}:
        # Both keys share the QuantArk class DoubleOneTouchOption; touch direction
        # rides in touch_type, derived from product_key. cash_payoff -> rebate is
        # handled by the builder (consistent with one_touch/digital).
        return {**base,
                "upper_barrier": _term_price(row, "upper_barrier", reference, 1.2),
                "lower_barrier": _term_price(row, "lower_barrier", reference, 0.8),
                "cash_payoff": _term_amount(row, "rebate", reference, 0.1),
                "touch_type": ("DOUBLE_NO_TOUCH"
                               if row.product_key == "double_no_touch"
                               else "DOUBLE_ONE_TOUCH")}
```

- [ ] **Step 4: Add both keys to `_MIGRATED_PRODUCT_KEYS`**

In `backend/app/services/try_solve.py`, update `_MIGRATED_PRODUCT_KEYS` (lines 700–707). Replace the block:

```python
# product_keys whose construction is migrated to build_product (the single
# producer). Out-of-scope keys (double_no_touch/double_one_touch -> no builder)
# are absent and fall back to the legacy _product_kwargs_for_row path.
_MIGRATED_PRODUCT_KEYS = frozenset({
    "vanilla", "digital", "single_sf", "double_sf", "asian",
    "range_accrual", "one_touch", "forward",
    "autocall", "phoenix", "knock_out_autocall",
})
```

with:

```python
# product_keys whose construction is migrated to build_product (the single
# producer). With double_no_touch/double_one_touch now routed through the
# DoubleOneTouchOption builder, this equals _SUPPORTED_SOLVE_PRODUCT_KEYS — the
# legacy _product_kwargs_for_row producer is fully retired (see Task 3).
_MIGRATED_PRODUCT_KEYS = frozenset({
    "vanilla", "digital", "single_sf", "double_sf", "asian",
    "range_accrual", "one_touch", "forward",
    "autocall", "phoenix", "knock_out_autocall",
    "double_no_touch", "double_one_touch",
})
```

- [ ] **Step 5: Run the new tests to verify they pass**

Run: `python -m pytest "tests/test_try_solve.py::test_double_touch_flat_contract_sets_touch_type_from_product_key" "tests/test_try_solve.py::test_double_one_touch_routes_through_build_product" -v`

Expected: PASS.

- [ ] **Step 6: Run the full try-solve + builder suites to verify no regression**

Run: `python -m pytest tests/test_try_solve.py tests/test_product_builders.py -q`

Expected: PASS. In particular `test_validate_try_solve_row_builds_every_quantark_backed_product` (lines 112–153) still reports `solver_ready` for `double_no_touch` / `double_one_touch`, now via the build-through path, with `engine_name == "OneTouchAnalyticalEngine"`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/try_solve.py tests/test_try_solve.py
git commit -m "feat(try-solve): route double-touch rows through build_product"
```

---

### Task 3: Retire the dead legacy `_product_kwargs_for_row` producer

**Files:**
- Modify: `backend/app/services/try_solve.py` (`_build_row_termsheet` ~lines 815–841; `_row_to_rfq_draft` ~lines 597–602; delete `_product_kwargs_for_row` ~lines 844–869)
- Test: `tests/test_try_solve.py`

**Context for the implementer:** After Task 2, `_MIGRATED_PRODUCT_KEYS == _SUPPORTED_SOLVE_PRODUCT_KEYS`, so every supported product key takes the build-through path. The legacy `_product_kwargs_for_row` is now unreachable for any supported key. Both `validate_try_solve_row` (line 580) and `solve_try_solve_row` gate on `_SUPPORTED_SOLVE_PRODUCT_KEYS` before reaching these helpers, so the legacy branches are dead. Remove them. `_flat_contract_for_row` keeps `raise _UnmigratedProductKey(row.product_key)` (line 812) as the defensive guard for a genuinely unknown family — that single guard is sufficient.

- [ ] **Step 1: Write the failing test**

In `tests/test_try_solve.py`, add this test (place it after `test_double_one_touch_routes_through_build_product` from Task 2):

```python
def test_product_kwargs_for_row_legacy_producer_is_removed():
    import app.services.try_solve as ts
    # The legacy per-row QuantArk-kwargs producer is fully retired; every
    # supported product key now builds via build_product.
    assert not hasattr(ts, "_product_kwargs_for_row")
    assert ts._MIGRATED_PRODUCT_KEYS == ts._SUPPORTED_SOLVE_PRODUCT_KEYS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest "tests/test_try_solve.py::test_product_kwargs_for_row_legacy_producer_is_removed" -v`

Expected: FAIL on the first assertion — `_product_kwargs_for_row` still exists as a module attribute.

- [ ] **Step 3: Collapse the `_build_row_termsheet` fallback**

In `backend/app/services/try_solve.py`, replace the body of `_build_row_termsheet` (lines 815–841). Replace:

```python
def _build_row_termsheet(
    row: TrySolveRowIn,
    product: TrySolveProduct,
    market: PricingEnvironmentSnapshot,
    maturity: float,
    quote_field: TrySolveQuoteField,
) -> tuple[dict[str, Any], list[str]]:
    """Complete QuantArk termsheet for a migrated row via build_product (the
    single producer). `solve_target` exempts the solved field from `missing`
    (filter_solved). Unmigrated keys fall back to the legacy factory. Returns
    (product_kwargs, missing); missing is non-empty iff the row's contract is
    unfilled (e.g. a snowball missing schedule inputs)."""
    if row.product_key not in _MIGRATED_PRODUCT_KEYS:
        return _product_kwargs_for_row(row, product, market, maturity, quote_field), []
    from .domains.product_builders import build_product

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

with:

```python
def _build_row_termsheet(
    row: TrySolveRowIn,
    product: TrySolveProduct,
    market: PricingEnvironmentSnapshot,
    maturity: float,
    quote_field: TrySolveQuoteField,
) -> tuple[dict[str, Any], list[str]]:
    """Complete QuantArk termsheet for a row via build_product (the single
    producer). Every supported product key is migrated, so there is no legacy
    fallback; an unknown family raises _UnmigratedProductKey from
    _flat_contract_for_row. `solve_target` exempts the solved field from
    `missing` (filter_solved). Returns (product_kwargs, missing); missing is
    non-empty iff the row's contract is unfilled (e.g. a snowball missing
    schedule inputs)."""
    from .domains.product_builders import build_product

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

- [ ] **Step 4: Collapse the `_row_to_rfq_draft` branch**

In `backend/app/services/try_solve.py`, replace the migrated/legacy split in `_row_to_rfq_draft` (lines 597–602). Replace:

```python
    # Migrated keys carry the FLAT term contract (build_product input); the
    # complete termsheet is synthesized downstream (build-through) before solve.
    if row.product_key in _MIGRATED_PRODUCT_KEYS:
        product_kwargs = _flat_contract_for_row(row, product, market, maturity, quote_field)
    else:
        product_kwargs = _product_kwargs_for_row(row, product, market, maturity, quote_field)
    reference = _reference_price(row, market)
```

with:

```python
    # Every supported key carries the FLAT term contract (build_product input);
    # the complete termsheet is synthesized downstream (build-through) before solve.
    product_kwargs = _flat_contract_for_row(row, product, market, maturity, quote_field)
    reference = _reference_price(row, market)
```

- [ ] **Step 5: Delete `_product_kwargs_for_row`**

In `backend/app/services/try_solve.py`, delete the entire `_product_kwargs_for_row` function (lines 844–869, from `def _product_kwargs_for_row(` through `raise ValueError(f"Product is not mapped to solver terms: {row.product_key}")`).

- [ ] **Step 6: Run the legacy-removal test + full try-solve suite**

Run: `python -m pytest tests/test_try_solve.py -q`

Expected: PASS, including `test_product_kwargs_for_row_legacy_producer_is_removed`. Watch for any remaining reference to `_product_kwargs_for_row` — there should be none (a stray import or comment would surface as a NameError or a failed grep in Step 7).

- [ ] **Step 7: Grep to confirm the symbol is gone**

Run: `grep -rn "_product_kwargs_for_row" backend/ tests/`

Expected: no output (the function and all references are removed).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/try_solve.py tests/test_try_solve.py
git commit -m "refactor(try-solve): remove dead legacy _product_kwargs_for_row producer"
```

---

### Task 4: Full regression + finish

**Files:** none (verification only)

- [ ] **Step 1: Run the full backend suite from the repo root**

Run: `python -m pytest -q`

Expected: PASS (no regressions). The relevant invariants:
- `tests/test_product_builders.py` — `DoubleOneTouchOption` builds and validates with `OneTouchAnalyticalEngine`, both touch directions.
- `tests/test_try_solve.py` — double-touch rows route through `build_product`; `test_validate_try_solve_row_builds_every_quantark_backed_product` green; legacy producer removed.
- `tests/test_product_contracts.py` — unaffected (the data-driven contract dict covers only the snowball family; the touch family reports `missing` imperatively, like `one_touch`).

- [ ] **Step 2: Finish the development branch**

Announce: "I'm using the finishing-a-development-branch skill to complete this work."
**REQUIRED SUB-SKILL:** Use superpowers:finishing-a-development-branch — verify tests, present the 4 options, execute the choice. (Given the concurrent agent on the shared repo, prefer the worktree-isolated merge flow used previously.)

---

## Notes / risks

- **Engine mapping is load-bearing.** Without the `_ENGINE_BY_CLASS["DoubleOneTouchOption"]` entry (Task 1, Step 3), `build_product` validates the product against `BlackScholesEngine`; Task 1's `result.engine_name == "OneTouchAnalyticalEngine"` assertion and Task 2's `solver_ready` integration test both catch a miss.
- **`cash_payoff` → `rebate` naming.** The flat-contract input key is `cash_payoff` (the builder reports it in `missing` under that name); the QuantArk kwarg is `rebate`. The try-solve quote field's `canonical_path` for the payoff is `rebate` — but because `_flat_contract_for_row` always fills `cash_payoff`, `missing` is empty and `filter_solved` never needs to match it, so there is no canonical-path/missing-key mismatch in practice (same as the existing `one_touch` path).
- **Touch direction lives in terms, not family.** Both product keys map to `DoubleOneTouchOption`; `touch_type` is the only discriminator and is also what `_touch_kind` (products.py:1007) reads to split `double_no_touch` vs `double_one_touch` for persistence. Keep it in the kwargs.
- **No frontend changes.** The two products already exist in the try-solve catalog/grid; this migration only swaps their construction path.
```
