# Try-Solve Channel Migration (Frontend / Schedule Overrides) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the desk override a snowball-family row's observation frequency and lockup in the Try-Solve grid (instead of the backend's monthly / no-lockup quote defaults), with those values flowing through `build_product` correctly.

**Architecture:** Frontend follow-on to the merged try-solve backend migration. The Try-Solve grid is **catalog-driven** — column inputs come from each product's registry `fields` (serialized via `to_dict`/`asdict`, including `field_type` + `options`), and the live route fetches the backend catalog (`/api/rfq/try-solve/catalog`). So exposing override columns is primarily a **registry change** (the grid auto-renders a `<select>` for `field_type === 'select'`). A backend **frequency-normalization** fix is required so grid/Excel frequency strings (`1m`, `monthly`, `quarterly`) map to `build_product`'s canonical `MONTHLY|QUARTERLY|SEMI_ANNUAL`. The frontend's hardcoded fallback `DEFAULT_TRY_SOLVE_CATALOG` is synced for parity.

**Tech Stack:** Python 3 / pydantic / pytest (backend registry + `try_solve.py`); React / TypeScript / vitest (`TrySolve.tsx`). `build_product` consumes `observation_frequency` (`FREQUENCY_MONTHS = {MONTHLY:1, QUARTERLY:3, SEMI_ANNUAL:6}`) + `lockup_months`.

**Scope note (read first):** Snowball families (`autocall`→SnowballOption, `phoenix`→PhoenixOption, `knock_out_autocall`→KnockOutResetSnowball) only. The backend already solves these with monthly/lockup-0 defaults; this plan makes those two inputs **grid-editable**. No per-row Solve gating is added — the grid already surfaces per-row status (`solver_ready`/`missing_terms`/`solved`), and snowball rows are `solver_ready` by default. The two uncovered keys (`double_no_touch`/`double_one_touch`) are untouched.

---

## Key facts about the current code (verified)

- `TrySolveField(key, label, field_type="text", excel_aliases=(), required=False, default=None, options=())` (`try_solve_registry.py:12`). `field_type` ∈ {text, number, date, select, boolean}. `side` is the template: `TrySolveField("side","Side","select",("客户方向",),True,"buy",("buy","sell"))`.
- `TrySolveProduct.to_dict` (`try_solve_registry.py:47`) serializes `fields` as `[asdict(field) ...]` → catalog exposes `field_type` + `options` + `default`.
- COMMON_FIELDS currently has `observation_frequency = TrySolveField("observation_frequency","Observation Frequency",excel_aliases=("观察频率",))` (type defaults to **text**); NO `lockup_months` field exists. `ko_barrier`/`ki_barrier` are `number` (defaults 1.03 / 0.75).
- Product `field_keys` (`try_solve_registry.py`): `autocall` = (counterparty, side, underlying, notional, initial_price, start_date, ko_barrier, ki_barrier, tenor_months, remarks) — **no observation_frequency, no lockup_months**. `phoenix` = (... start_date, observation_frequency, ko_barrier, ki_barrier, coupon_yield, tenor_months, remarks) — has observation_frequency, **no lockup_months**. `knock_out_autocall` ≈ autocall — **no observation_frequency, no lockup_months**.
- `_flat_contract_for_row` (`try_solve.py`) snowball branch reads `observation_frequency` from `row.fields` and only `.upper()`s it: `str(row.fields.get("observation_frequency") or "MONTHLY").upper()`. So `"1m"`→`"1M"` (NOT in `FREQUENCY_MONTHS`) ⇒ build reports the frequency missing/invalid. **Needs normalization.**
- Frontend grid renders a `<select>` when `fieldItem.field_type === 'select'` using `fieldItem.options` (`TrySolve.tsx:751`); edits call `onFieldChange(rowId, fieldKey, value)` → persisted to `row.fields`.
- `DEFAULT_TRY_SOLVE_CATALOG` (`TrySolve.tsx:54`) is a hardcoded fallback (19 products). The autocall fallback fields lack `ki_barrier`, `observation_frequency`, `lockup_months`. `TrySolve.test.tsx` asserts `DEFAULT_TRY_SOLVE_CATALOG.products` length **19** and that the autocall editor renders e.g. "Knock-Out Barrier".

---

## Task 1: Backend — normalize observation frequency aliases

`_flat_contract_for_row` must map grid/Excel frequency strings to `build_product`'s canonical tokens so an overridden frequency actually builds.

**Files:**
- Modify: `backend/app/services/try_solve.py`
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_snowball_row_frequency_alias_normalizes_to_canonical():
    from app.services.try_solve import _flat_contract_for_row, _pricing_market, _maturity_years

    def _flat(freq):
        row = TrySolveRowIn(row_id="r1", product_key="autocall",
                            fields={"underlying": "000905.SH", "notional": 1_000_000,
                                    "start_date": "2026-05-13", "tenor_months": 12,
                                    "ko_barrier": 1.03, "ki_barrier": 0.75,
                                    "observation_frequency": freq},
                            market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
                            quote_request=TrySolveQuoteRequestIn(quote_field_key="annualized_coupon", initial_guess=0.15))
        product = registry_by_key()["autocall"]
        market = _pricing_market(row)
        return _flat_contract_for_row(row, product, market, _maturity_years(row),
                                      product.quote_fields["annualized_coupon"])

    assert _flat("1m")["observation_frequency"] == "MONTHLY"
    assert _flat("quarterly")["observation_frequency"] == "QUARTERLY"
    assert _flat("6M")["observation_frequency"] == "SEMI_ANNUAL"
    assert _flat("MONTHLY")["observation_frequency"] == "MONTHLY"   # already canonical
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py -k "frequency_alias_normalizes" -v`
Expected: FAIL — `"1m"`→`"1M"` (not `MONTHLY`).

- [ ] **Step 3: Implement**

In `backend/app/services/try_solve.py`, add a normalizer and use it in the snowball branch:

```python
# Grid/Excel frequency strings -> build_product's canonical observation tokens
# (FREQUENCY_MONTHS = {MONTHLY:1, QUARTERLY:3, SEMI_ANNUAL:6}).
_FREQUENCY_ALIASES = {
    "1M": "MONTHLY", "M": "MONTHLY", "MONTH": "MONTHLY", "MONTHLY": "MONTHLY",
    "3M": "QUARTERLY", "Q": "QUARTERLY", "QUARTER": "QUARTERLY", "QUARTERLY": "QUARTERLY",
    "6M": "SEMI_ANNUAL", "H": "SEMI_ANNUAL", "SEMIANNUAL": "SEMI_ANNUAL",
    "SEMI_ANNUAL": "SEMI_ANNUAL", "SEMI-ANNUAL": "SEMI_ANNUAL",
}


def _normalize_frequency(value: Any, default: str = "MONTHLY") -> str:
    token = str(value or "").strip().upper()
    return _FREQUENCY_ALIASES.get(token, token or default)
```

Then in the snowball branch of `_flat_contract_for_row`, replace:
```python
            "observation_frequency": str(row.fields.get("observation_frequency") or "MONTHLY").upper(),
```
with:
```python
            "observation_frequency": _normalize_frequency(row.fields.get("observation_frequency")),
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py -k "frequency_alias_normalizes" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/try_solve.py tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(try-solve): normalize observation-frequency aliases to canonical tokens"
```

---

## Task 2: Backend registry — expose observation_frequency (select) + lockup_months for snowball products

**Files:**
- Modify: `backend/app/services/try_solve_registry.py`
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write the failing test**

```python
def test_snowball_products_expose_schedule_override_fields():
    products = registry_by_key()
    for key in ("autocall", "phoenix", "knock_out_autocall"):
        keys = set(products[key].fields)
        assert "observation_frequency" in keys, key
        assert "lockup_months" in keys, key
    freq = products["autocall"].fields["observation_frequency"]
    assert freq.field_type == "select"
    assert freq.options == ("MONTHLY", "QUARTERLY", "SEMI_ANNUAL")
    assert products["autocall"].fields["lockup_months"].field_type == "number"


def test_quarterly_override_flows_to_a_quarterly_schedule(session):
    from datetime import date, timedelta
    from app.services.try_solve import solve_try_solve_row

    start = (date.today() + timedelta(days=30)).isoformat()
    row = TrySolveRowIn(row_id="r1", product_key="autocall",
                        fields={"underlying": "000905.SH", "notional": 1_000_000,
                                "start_date": start, "tenor_months": 12,
                                "ko_barrier": 1.03, "ki_barrier": 0.75,
                                "observation_frequency": "QUARTERLY", "lockup_months": 3},
                        market=TrySolveMarketIn(spot=100.0, volatility=0.2, rate=0.03, dividend_yield=0.0),
                        quote_request=TrySolveQuoteRequestIn(quote_field_key="annualized_coupon",
                                                             initial_guess=0.15, target_label="price", target_value=5.0))
    out = solve_try_solve_row(row, session)
    assert out.status == "solved", out.diagnostics
    records = out.executable_terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"]["records"]
    # quarterly over 1y with a 3-month lockup -> 4 observations (months 3,6,9,12)
    assert len(records) == 4
    assert out.executable_terms["product_kwargs"]["barrier_config"]["ko_observation_schedule"]["frequency"] == "QUARTERLY"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py -k "expose_schedule_override_fields or quarterly_override_flows" -v`
Expected: FAIL — fields absent / observation_frequency is `text`.

- [ ] **Step 3: Implement**

In `backend/app/services/try_solve_registry.py`, change `observation_frequency` to a select and add `lockup_months` in `COMMON_FIELDS`:
```python
    "observation_frequency": TrySolveField(
        "observation_frequency", "Observation Frequency", "select", ("观察频率",),
        False, "MONTHLY", ("MONTHLY", "QUARTERLY", "SEMI_ANNUAL"),
    ),
    "lockup_months": TrySolveField(
        "lockup_months", "Lockup Months", "number", (), False, 0.0,
    ),
```

Add the two keys to `autocall` and `knock_out_autocall` `field_keys`, and add `lockup_months` to `phoenix` `field_keys` (it already has `observation_frequency`). Place them right after `start_date` for a natural grid order, e.g. autocall:
```python
        field_keys=(
            "counterparty", "side", "underlying", "notional", "initial_price",
            "start_date", "observation_frequency", "lockup_months",
            "ko_barrier", "ki_barrier", "tenor_months", "remarks",
        ),
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py -k "expose_schedule_override_fields or quarterly_override_flows" -v`
Expected: PASS.

- [ ] **Step 5: Catalog-shape regression**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py -q`
Some catalog tests assert exact field sets/counts. For each failure: if it pins the OLD snowball field list, update it to include the two new fields; do NOT weaken assertions about other products. `observation_frequency` becoming a `select` may also surface in a serialization assertion — update it to the select shape.

- [ ] **Step 6: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add backend/app/services/try_solve_registry.py tests/test_try_solve.py
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(try-solve): expose observation_frequency (select) + lockup_months for snowball rows"
```

---

## Task 3: Frontend — sync the fallback catalog + verify grid renders the override columns

The live grid auto-renders from the backend catalog; sync the hardcoded `DEFAULT_TRY_SOLVE_CATALOG` so storybook/offline parity holds, and pin that the autocall editor shows a frequency select + lockup input.

**Files:**
- Modify: `frontend/src/routes/TrySolve.tsx` (`DEFAULT_TRY_SOLVE_CATALOG` autocall/phoenix/knock_out_autocall)
- Test: `frontend/src/routes/TrySolve.test.tsx`

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/routes/TrySolve.test.tsx`:
```tsx
  it('exposes snowball schedule-override columns (frequency select + lockup)', async () => {
    render(<TrySolve catalog={DEFAULT_TRY_SOLVE_CATALOG} rows={DEFAULT_TRY_SOLVE_ROWS} />);
    // autocall row is selected by default (xl-12 autocall)
    expect(screen.getByRole('form', { name: /autocall field editor/i })).toBeInTheDocument();

    const freq = screen.getByLabelText('Observation Frequency');
    expect(freq.tagName).toBe('SELECT');
    expect(within(freq).getByRole('option', { name: 'MONTHLY' })).toBeInTheDocument();
    expect(within(freq).getByRole('option', { name: 'QUARTERLY' })).toBeInTheDocument();
    expect(within(freq).getByRole('option', { name: 'SEMI_ANNUAL' })).toBeInTheDocument();

    expect(screen.getByLabelText('Lockup Months')).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/routes/TrySolve.test.tsx -t "schedule-override columns"`
Expected: FAIL — no "Observation Frequency" / "Lockup Months" in the autocall fallback fields.

- [ ] **Step 3: Implement**

In `frontend/src/routes/TrySolve.tsx`, add the two fields to the `autocall` and `knock_out_autocall` `fields` arrays (after `start_date`), and `lockup_months` to `phoenix`:
```tsx
        field('start_date', 'Start Date', 'date', { required: true }),
        field('observation_frequency', 'Observation Frequency', 'select', { default: 'MONTHLY', options: ['MONTHLY', 'QUARTERLY', 'SEMI_ANNUAL'] }),
        field('lockup_months', 'Lockup Months', 'number', { default: 0 }),
```
(`phoenix` already has `observation_frequency` in the fallback? It does NOT — add both there too, matching the backend registry which has observation_frequency on phoenix; add `lockup_months` and, if missing, `observation_frequency` as the select.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run src/routes/TrySolve.test.tsx`
Expected: PASS (new test + the existing 19-product / field-editor assertions still green).

- [ ] **Step 5: Commit**

```bash
git -C /Users/fuxinyao/open-otc-trading add frontend/src/routes/TrySolve.tsx frontend/src/routes/TrySolve.test.tsx
git -C /Users/fuxinyao/open-otc-trading commit -m "feat(try-solve-frontend): snowball schedule-override grid columns (frequency select + lockup)"
```

---

## Task 4: Full backend + frontend regression

**Files:** none (verification only)

- [ ] **Step 1: Backend regression**

Run: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/test_try_solve.py tests/test_services_domains_rfq.py tests/test_product_builders.py -q`
Expected: all PASS.

- [ ] **Step 2: Frontend suite + typecheck**

Run: `cd /Users/fuxinyao/open-otc-trading/frontend && npx vitest run && npx tsc -b`
Expected: all tests PASS, `tsc -b` exits 0. (ChatComposer WIP, if failing, is pre-existing — do NOT touch it.)

- [ ] **Step 3: Commit (if any test-fixture touch-ups were needed)**

```bash
git -C /Users/fuxinyao/open-otc-trading add -A backend tests frontend/src
git -C /Users/fuxinyao/open-otc-trading commit -m "test(try-solve): regression fixes for schedule-override fields"
```

---

## Self-Review

**Spec coverage (try-solve frontend / schedule overrides):**
- Observation frequency editable per snowball row (as a constrained select) → **Tasks 2, 3** ✓
- Lockup editable per snowball row → **Tasks 2, 3** ✓
- Overrides actually flow through `build_product` (frequency aliases normalized; quarterly yields a quarterly schedule) → **Tasks 1, 2** ✓
- Live grid auto-renders from the catalog; fallback catalog synced → **Task 3** ✓

**Deferred (explicitly):** per-row Solve gating (grid already surfaces row status; snowball rows are solver_ready by default); `DoubleOneTouchOption` builder; OTC-import channel; cleanup.

**The one risk** is catalog-shape test coupling: changing `observation_frequency` to a `select` and adding `lockup_months` shifts the snowball field sets. Task 2 Step 5 / Task 4 explicitly catch and update those exact-set/serialization assertions (update to match, never weaken).

**Placeholder scan:** none — every step has concrete code/commands. **Type/name consistency:** `_normalize_frequency`/`_FREQUENCY_ALIASES` (Task 1) used in the snowball branch; registry `observation_frequency`/`lockup_months` (Task 2) consumed by `_flat_contract_for_row` (already reads these keys) and rendered by `TrySolve.tsx` (Task 3); `field_type:'select'` + `options` match the grid renderer at `TrySolve.tsx:751`.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-31-try-solve-channel-migration-frontend.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks.

**2. Inline Execution** — execute tasks in this session using executing-plans.

**Which approach?**
