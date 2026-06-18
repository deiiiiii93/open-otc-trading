# Position Currency (Display / Edit / Import) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface `Position.currency` on the Positions page and Position Detail, make it editable with ISO-4217 validation and a provenance warning, and have the xlsx import read an optional 币种 column (fixing the import channel's USD-default mislabeling).

**Architecture:** One field threaded through existing layers — `PositionOut` → frontend `Position`/`PositionRow` → table/detail; `PortfolioPositionSpec` + `patch_position` for edits; `PositionMapping` → `ProductBookingSpec.currency` → `set_position_currency` for import. No new endpoints, no migrations.

**Tech Stack:** FastAPI + Pydantic v2 + SQLAlchemy (backend), React + vitest + testing-library (frontend), openpyxl (import tests).

**Spec:** `docs/superpowers/specs/2026-06-04-position-currency-display-edit-import-design.md`

**Commands** (run from repo root `/Users/fuxinyao/open-otc-trading` unless noted):
- Backend tests: `.venv/bin/python -m pytest tests/<file>::<test> -v`
- Frontend tests: run from `frontend/`: `npx vitest run src/<path>` (vitest needs frontend/ cwd for jsdom)
- Frontend typecheck: from `frontend/`: `npx tsc --noEmit`

---

### Task 1: Backend — `PositionOut` serializes `currency`

**Files:**
- Modify: `backend/app/schemas.py` (class `PositionOut`, ~line 594)
- Test: `tests/test_position_currency.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_position_currency.py`:

```python
def test_position_out_serializes_currency():
    from datetime import datetime
    from types import SimpleNamespace

    from app.schemas import PositionOut

    now = datetime(2026, 6, 4)
    position = SimpleNamespace(
        id=1, portfolio_id=2, product_id=None, underlying_id=None,
        underlying="000300.SH", product_type="SnowballOption",
        product_kwargs={}, product=None, engine_name="SnowballQuadEngine",
        engine_kwargs={}, quantity=1.0, entry_price=0.0, status="open",
        source_trade_id=None, source_row=None, mapping_status="supported",
        mapping_error=None, source_payload=None, rfq_id=None,
        rfq_quote_version_id=None, trade_effective_date=None,
        currency="USD", created_at=now, updated_at=now,
    )
    out = PositionOut.model_validate(position, from_attributes=True)
    assert out.currency == "USD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_position_currency.py::test_position_out_serializes_currency -v`
Expected: FAIL — `AssertionError` (`out.currency` raises `AttributeError`) or pydantic ignores the attr; either way the assertion cannot pass.

- [ ] **Step 3: Add the field**

In `backend/app/schemas.py`, class `PositionOut`, insert after `entry_price: float` (line ~606):

```python
    currency: str = "CNY"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_position_currency.py -v`
Expected: all PASS (the file's existing tests must stay green).

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas.py tests/test_position_currency.py
git commit -m "feat(api): PositionOut serializes position currency"
```

---

### Task 2: Backend — PATCH currency with ISO-4217 validation

**Files:**
- Modify: `backend/app/schemas.py` (class `PortfolioPositionSpec`, ~line 572)
- Modify: `backend/app/main.py` (`patch_position`, ~line 2567; booking import block at line 147)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py` (uses the existing `make_client` helper in that file):

```python
def test_patch_position_currency_explicit_and_normalized(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Ccy Book", "base_currency": "CNY"},
    ).json()
    body = {
        "underlying": "000852.SH",
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        "quantity": 1.0,
        "engine_name": "BlackScholesEngine",
    }
    created = client.post(
        f"/api/portfolios/{portfolio['id']}/positions", json=body
    ).json()["positions"][0]

    # Explicit currency (lowercase) -> normalized + persisted.
    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={**body, "currency": "usd"},
    )
    assert patched.status_code == 200
    row = next(p for p in patched.json()["positions"] if p["id"] == created["id"])
    assert row["currency"] == "USD"

    # Omitting currency leaves the stored value unchanged.
    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json=body,
    )
    assert patched.status_code == 200
    row = next(p for p in patched.json()["positions"] if p["id"] == created["id"])
    assert row["currency"] == "USD"

    # Invalid code -> 422.
    rejected = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={**body, "currency": "DOLLARS"},
    )
    assert rejected.status_code == 422


def test_patch_position_product_replacement_rederives_currency(tmp_path: Path):
    client = make_client(tmp_path)
    portfolio = client.post(
        "/api/portfolios",
        json={"name": "Ccy Book 2", "base_currency": "CNY"},
    ).json()
    base = {
        "underlying": "000852.SH",
        "product_type": "EuropeanVanillaOption",
        "product_kwargs": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
        "quantity": 1.0,
        "engine_name": "BlackScholesEngine",
    }
    created = client.post(
        f"/api/portfolios/{portfolio['id']}/positions", json=base
    ).json()["positions"][0]

    # Replacing the product WITHOUT an explicit currency re-derives it from the
    # new product (set_position_currency provenance).
    patched = client.patch(
        f"/api/portfolios/{portfolio['id']}/positions/{created['id']}",
        json={
            **base,
            "product": {
                "asset_class": "equity",
                "product_family": "option",
                "quantark_class": "EuropeanVanillaOption",
                "underlying": "000852.SH",
                "currency": "HKD",
                "terms": {"strike": 100.0, "option_type": "CALL", "maturity": 1.0},
            },
        },
    )
    assert patched.status_code == 200
    row = next(p for p in patched.json()["positions"] if p["id"] == created["id"])
    assert row["currency"] == "HKD"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_patch_position_currency_explicit_and_normalized tests/test_api.py::test_patch_position_product_replacement_rederives_currency -v`
Expected: FAIL — first test: `row["currency"]` stays at its created value (PATCH ignores the unknown field, no 422 for "DOLLARS" either since the field doesn't exist yet → the `== "USD"` assert fails); second test: currency not re-derived.

- [ ] **Step 3: Add the validated field to `PortfolioPositionSpec`**

In `backend/app/schemas.py`, class `PortfolioPositionSpec` (~line 572), add the field after `entry_price: float = 0.0` and a validator at the end of the class (the file already imports `model_validator`, `ISO_4217_CODES`, `normalize_currency` at the top):

```python
class PortfolioPositionSpec(BaseModel):
    underlying: str = "CSI500"
    product_type: str = "EuropeanVanillaOption"
    product_kwargs: dict[str, Any] = Field(
        default_factory=lambda: {
            "strike": 100.0,
            "option_type": "CALL",
            "maturity": 1.0,
        }
    )
    engine_name: str = "BlackScholesEngine"
    engine_kwargs: dict[str, Any] = Field(default_factory=dict)
    quantity: float = 1.0
    entry_price: float = 0.0
    currency: str | None = None
    status: str = "open"
    source_trade_id: str | None = None
    rfq_id: int | None = None
    rfq_quote_version_id: int | None = None
    trade_effective_date: date | datetime | None = None
    product: ProductSpecIn | None = None

    @model_validator(mode="after")
    def _validate_currency(self) -> "PortfolioPositionSpec":
        if self.currency is not None:
            code = normalize_currency(self.currency)
            if code not in ISO_4217_CODES:
                raise ValueError(f"Invalid currency code: {self.currency!r}")
            self.currency = code
        return self
```

- [ ] **Step 4: Apply currency in `patch_position`**

In `backend/app/main.py`:

a) Extend the booking-domain import block (line ~147):

```python
from .services.domains.booking import (
    BookingRequest,
    ProductBookingSpec,
    book_position,
    prepare_booking_product_spec,
    repair_invalid_snowball_booking_terms,
    set_position_currency,
)
```

b) In `patch_position` (~line 2567), after the `if payload.product is not None: ... else: ...` block and **before** `portfolio.updated_at = datetime.utcnow()`, insert:

```python
        # Currency precedence: explicit payload wins; otherwise a product
        # replacement re-derives it from the booked product (same provenance
        # rule as booking). Fields-only patches without currency leave it alone.
        if payload.currency is not None:
            position.currency = payload.currency
        elif payload.product is not None:
            set_position_currency(position)
```

Note: `data = payload.model_dump(mode="json")` already exists above; do NOT add
`currency` to the `editable_fields` set (it would write `None` over the stored
value when omitted).

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_patch_position_currency_explicit_and_normalized tests/test_api.py::test_patch_position_product_replacement_rederives_currency tests/test_api.py::test_patch_position_legacy_payload_updates_product_and_clears_stale_terms -v`
Expected: all PASS (including the pre-existing legacy PATCH test).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_api.py
git commit -m "feat(api): editable position currency with ISO-4217 validation"
```

---

### Task 3: Backend — import reads optional 币种 column; CNY channel default; re-import refresh

**Files:**
- Modify: `backend/app/services/position_adapter.py`
- Test: `tests/test_position_import_pricing.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_position_import_pricing.py`:

a) Append `"币种"` to the `TRADE_HEADERS` list (after `"派息收益率"`):

```python
    "派息收益率",
    "币种",
]
```

b) Append the tests:

```python
def test_import_currency_defaults_to_cny_without_column_value(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])  # 币种 cell left blank
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Default Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    # Pins the import-channel default: CNY, not the generic spec default (USD).
    assert position.currency == "CNY"
    assert position.product.currency == "CNY"


def test_import_reads_currency_column_per_row(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    usd_row = vanilla_row("T-USD")
    usd_row["币种"] = "usd"  # normalized to USD
    cny_row = vanilla_row("T-CNY")
    write_trade_workbook(xlsx_path, [usd_row, cny_row])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Column Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    usd = session.query(Position).filter_by(source_trade_id="T-USD").one()
    cny = session.query(Position).filter_by(source_trade_id="T-CNY").one()
    assert usd.currency == "USD"
    assert cny.currency == "CNY"


def test_import_invalid_currency_becomes_error_row(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    bad_row = vanilla_row("T-BAD-CCY")
    bad_row["币种"] = "DOLLARS"
    write_trade_workbook(xlsx_path, [bad_row, vanilla_row("T-GOOD")])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Error Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    batch = import_positions_from_xlsx(
        session, portfolio_id=portfolio.id, xlsx_path=xlsx_path
    )
    session.commit()

    bad = session.query(Position).filter_by(source_trade_id="T-BAD-CCY").one()
    good = session.query(Position).filter_by(source_trade_id="T-GOOD").one()
    assert bad.mapping_status == "error"
    assert "Invalid currency code" in (bad.mapping_error or "")
    assert good.mapping_status == "supported"
    assert batch.error_count == 1
    assert any(
        "Invalid currency code" in entry.get("error", "")
        for entry in batch.summary["errors"]
    )


def test_reimport_refreshes_currency(tmp_path: Path):
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row("T-REFRESH")])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Ccy Refresh Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()

    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()
    position = session.query(Position).filter_by(source_trade_id="T-REFRESH").one()
    assert position.currency == "CNY"

    updated = vanilla_row("T-REFRESH")
    updated["币种"] = "USD"
    write_trade_workbook(xlsx_path, [updated])
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    session.commit()

    session.refresh(position)
    assert position.currency == "USD"
```

Note: the import loop stores its `errors` list as `summary={"errors": errors[:50]}`
on `PositionImportBatch` (position_adapter.py:192) — there is no `batch.errors`
attribute.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_position_import_pricing.py -k currency -v`
Expected: FAIL —
- `test_import_currency_defaults_to_cny_without_column_value`: position currency is `"USD"` (the mislabeling bug this pins).
- `test_import_reads_currency_column_per_row`: both `"USD"` (default), `T-USD` passes by luck of the bug or both fail — either way `T-CNY == "CNY"` fails.
- `test_import_invalid_currency_becomes_error_row`: `bad.mapping_status == "supported"`.
- `test_reimport_refreshes_currency`: first assert (`== "CNY"`) already fails.

- [ ] **Step 3: Implement in `position_adapter.py`**

a) Extend imports (top of file):

```python
from dataclasses import dataclass, replace
```

and add after the existing `.domains.products` import block:

```python
from .currency_codes import ISO_4217_CODES, normalize_currency
```

and extend the `.domains.booking` import:

```python
from .domains.booking import (
    BookingRequest,
    ProductBookingSpec,
    book_position,
    prepare_booking_product_spec,
    set_position_currency,
)
```

b) Add the field to `PositionMapping` (frozen dataclass, ~line 52):

```python
@dataclass(frozen=True)
class PositionMapping:
    underlying: str
    product_type: str
    product_kwargs: dict[str, Any]
    engine_name: str
    engine_kwargs: dict[str, Any]
    quantity: float
    entry_price: float
    status: str
    mapping_status: str
    mapping_error: str | None = None
    currency: str | None = None
```

c) In `map_trade_row` (~line 266), route the mapper result through a currency
post-step (replaces the bare `return mapper(row)`):

```python
    mapper = mapping_by_structure.get(structure_type)
    if mapper is None:
        return _unsupported_mapping(row, f"Unsupported structure type: {structure_type or '<blank>'}")
    return _apply_row_currency(mapper(row), row)


def _apply_row_currency(mapping: PositionMapping, row: dict[str, Any]) -> PositionMapping:
    """Optional 币种 (or Currency) column: blank -> leave None (channel default
    CNY applies downstream); invalid -> raise so the import loop isolates the row
    as an error row, consistent with booking-gate rejections."""
    raw = text_value(row.get("币种") or row.get("Currency"))
    if not raw:
        return mapping
    code = normalize_currency(raw)
    if code not in ISO_4217_CODES:
        raise ValueError(f"Invalid currency code: {raw!r}")
    return replace(mapping, currency=code)
```

IMPORTANT: `map_trade_row` is called inside `try/except Exception` in the import
loop — raising converts the row to an error row AND records the error entry.
But `_apply_row_currency` raising for UNSUPPORTED rows would be wrong — the
`_unsupported_mapping` early-return above bypasses it, which is correct.

d) In `_product_booking_spec_from_mapping` (~line 232), override the spec
currency (pins the import channel default to CNY):

```python
def _product_booking_spec_from_mapping(
    mapping: PositionMapping,
    source_payload: dict[str, Any],
) -> ProductBookingSpec:
    spec = product_spec_from_position_payload(
        {
            "underlying": mapping.underlying,
            "product_type": mapping.product_type,
            "product_kwargs": mapping.product_kwargs,
            "source_payload": source_payload,
        }
    )
    # Import channel: 币种 column wins, else CNY. Never inherit the generic
    # spec default (USD) — Chinese OTC trade sheets are CNY-denominated.
    return ProductBookingSpec(**{**spec.__dict__, "currency": mapping.currency or "CNY"})
```

e) In the existing-trade re-import branch of `import_positions_from_xlsx`
(~line 119-143, the `else:` after `if existing is None:`), add
`set_position_currency(position)` right after `link_position_underlying(...)`:

```python
                position.product = product
                position.product_id = product.id
                hydrate_position_product_fields(position)
                link_position_underlying(session, position, source="import")
                set_position_currency(position)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_position_import_pricing.py -v`
Expected: all PASS (whole file — the pre-existing import tests must stay green; they assert no currency so the CNY default doesn't break them).

- [ ] **Step 5: Run the wider backend suite**

Run: `.venv/bin/python -m pytest tests/ -x -q`
Expected: all PASS. If `test_cross_channel_equivalence.py` or booking-gate tests fail on product currency, the failure means a fixture pinned the old USD default — inspect and update the fixture's expected currency to CNY only when the test goes through the import channel (agent/try-solve/RFQ channels are NOT touched by this change).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/position_adapter.py tests/test_position_import_pricing.py
git commit -m "feat(import): optional per-row currency column; CNY channel default; re-import refresh"
```

---

### Task 4: Frontend — currency display (types, row mapping, CCY column, detail item)

**Files:**
- Modify: `frontend/src/types.ts` (type `Position`, ~line 463)
- Modify: `frontend/src/routes/Positions.tsx` (`PositionRow` ~line 18; `columns` ~line 381; `PositionDetail` Contract Snapshot ~line 770)
- Modify: `frontend/src/routes/Positions.live.tsx` (row mapping ~line 356)
- Modify (fixtures): `frontend/src/routes/Positions.test.tsx`, `frontend/src/components/PositionEditForm.test.tsx`, `frontend/src/components/PositionLifecycleTimeline.test.tsx`
- Test: `frontend/src/routes/Positions.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/routes/Positions.test.tsx`:

a) Add `currency: 'CNY',` to the `positionRows[0]` fixture (after `entry_price: 0,`).

b) Append inside the `describe('Positions', ...)` block:

```tsx
  it('renders the CCY column for each row', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);
    expect(screen.getByText('CCY')).toBeInTheDocument();
    expect(screen.getAllByText('CNY').length).toBeGreaterThan(0);
  });

  it('shows the position currency in the Contract Snapshot', async () => {
    render(<Positions {...baseProps} rows={positionRows} />);
    await userEvent.click(screen.getAllByText('T-VANILLA')[0]);
    const dialog = screen.getByRole('dialog', { name: /position detail/i });
    const snapshot = sectionByHeading(dialog, 'Contract Snapshot');
    expect(within(snapshot).getByText('Currency')).toBeInTheDocument();
    expect(within(snapshot).getByText('CNY')).toBeInTheDocument();
  });
```

(`sectionByHeading` is the existing helper at the top of this test file. If
`PositionDetailSection` titles don't render as headings, fall back to
`within(dialog).getByText('Contract Snapshot').closest('section')`.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/Positions.test.tsx`
Expected: the two new tests FAIL (`CCY` / `Currency` not found). Pre-existing tests still pass (the fixture gained a field; `PositionRow` doesn't require it yet).

- [ ] **Step 3: Thread the type and data**

a) `frontend/src/types.ts`, type `Position` — add after `entry_price: number;`:

```ts
  currency: string;
```

b) `frontend/src/routes/Positions.tsx`, type `PositionRow` — add after `entry_price: number;`:

```ts
  currency: string;
```

c) `frontend/src/routes/Positions.live.tsx`, the `rows` mapping (~line 368) — add after `entry_price: Number(position.entry_price ?? 0),`:

```ts
      currency: position.currency,
```

d) `frontend/src/routes/Positions.tsx`, the `columns` array — insert between the
`entry_price` column object and the `price` column (line ~444):

```ts
    { key: 'currency', header: 'CCY', width: '0.5fr' },
```

e) `frontend/src/routes/Positions.tsx`, `PositionDetail` Contract Snapshot —
add after `<DetailItem label="Product" value={row.product_type} />` (~line 776):

```tsx
                <DetailItem label="Currency" value={row.currency} />
```

- [ ] **Step 4: Fix the other PositionRow fixtures (type now requires currency)**

a) `frontend/src/components/PositionEditForm.test.tsx` — add `currency: 'CNY',`
to the `row` fixture (after `entry_price: 100,`).

b) `frontend/src/components/PositionLifecycleTimeline.test.tsx` — its `row`
fixture is a `PositionRow` literal (line ~8): add `currency: 'CNY',` after
`entry_price: 0,` (line 14).

c) `frontend/src/routes/Positions.test.tsx` — any other inline `PositionRow`
literals (the greeks test at ~line 304 spreads `positionRows[0]`, so it
inherits; verify with `npx tsc --noEmit`).

- [ ] **Step 5: Run tests + typecheck to verify green**

Run (from `frontend/`): `npx vitest run src/routes/Positions.test.tsx src/components/PositionEditForm.test.tsx src/components/PositionLifecycleTimeline.test.tsx && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/Positions.tsx frontend/src/routes/Positions.live.tsx frontend/src/routes/Positions.test.tsx frontend/src/components/PositionEditForm.test.tsx frontend/src/components/PositionLifecycleTimeline.test.tsx
git commit -m "feat(positions-ui): currency column and Contract Snapshot field"
```

---

### Task 5: Frontend — editable currency in the Edit Position form (+ kill hardcoded USD)

**Files:**
- Modify: `frontend/src/components/PositionEditForm.tsx`
- Modify: `frontend/src/routes/Positions.live.tsx` (`handleEditPosition`, ~line 250)
- Test: `frontend/src/components/PositionEditForm.test.tsx`

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/PositionEditForm.test.tsx` (the `row` fixture has
`currency: 'CNY'` from Task 4 and `product.currency: 'USD'`):

a) The existing test `submits legacy fields and the nested product object`
pins the hardcoded value — update its `product` expectation from
`currency: 'USD'` to `currency: 'CNY'` (the form now sends the form's
currency, seeded from `row.currency`).

b) Append:

```tsx
  it('submits the edited currency and uses it in the product spec', async () => {
    const onSave = vi.fn();
    render(<PositionEditForm row={row} onSave={onSave} saving={false} />);

    const input = screen.getByLabelText('Currency');
    await userEvent.clear(input);
    await userEvent.type(input, 'hkd');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalled());
    const updates = onSave.mock.calls[0][1];
    expect(updates.currency).toBe('HKD');
    expect(updates.product).toEqual(expect.objectContaining({ currency: 'HKD' }));
  });

  it('warns when the currency deviates from the booked trade currency', async () => {
    render(<PositionEditForm row={row} onSave={vi.fn()} saving={false} />);
    // Seeded CNY vs product.currency USD -> warning visible immediately.
    expect(screen.getByText(/differs from booked trade currency \(USD\)/i)).toBeInTheDocument();

    const input = screen.getByLabelText('Currency');
    await userEvent.clear(input);
    await userEvent.type(input, 'USD');
    expect(screen.queryByText(/differs from booked trade currency/i)).not.toBeInTheDocument();
  });

  it('rejects a malformed currency before submitting', async () => {
    const onSave = vi.fn();
    render(<PositionEditForm row={row} onSave={onSave} saving={false} />);

    const input = screen.getByLabelText('Currency');
    await userEvent.clear(input);
    await userEvent.type(input, 'C1');
    await userEvent.click(screen.getByRole('button', { name: /save changes/i }));

    expect(await screen.findByText(/currency must be a 3-letter ISO code/i)).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/PositionEditForm.test.tsx`
Expected: the three new tests FAIL (`getByLabelText('Currency')` not found) and the updated legacy expectation FAILS (form still hardcodes `'USD'`).

- [ ] **Step 3: Implement the form changes**

In `frontend/src/components/PositionEditForm.tsx`:

a) Seed the form state (inside `useState({...})`, after `entry_price: ...`):

```ts
    currency: row.currency,
```

b) Add the input in the edit grid, after the Entry Price label block:

```tsx
        <label className="wl-positions__term-field">
          <span>Currency</span>
          <input
            value={form.currency}
            maxLength={3}
            onChange={(e) => update('currency', e.target.value.toUpperCase())}
          />
        </label>
```

c) Add the mismatch warning immediately after the closing
`</div>` of `wl-positions__edit-grid` (before `<ProductTermsForm ...>`):

```tsx
      {form.currency && row.product?.currency && form.currency !== row.product.currency && (
        <div className="wl-positions__edit-warning" role="status">
          Currency {form.currency} differs from booked trade currency ({row.product.currency})
          — risk will re-bucket under the new currency.
        </div>
      )}
```

d) Validate in `handleSubmit` (after the `entry_price` check):

```ts
    if (!/^[A-Z]{3}$/.test(form.currency)) {
      setError('Currency must be a 3-letter ISO code');
      return;
    }
```

e) In the `onSave(row, {...})` call: add `currency: form.currency,` after
`entry_price,` and replace the hardcoded product currency:

```ts
      currency: form.currency,
```
and in the `product:` object replace `currency: 'USD',` with:

```ts
        currency: form.currency,
```

f) Style hook (only if no suitable class exists): `wl-positions__edit-warning`
— add to `frontend/src/routes/Positions.css`:

```css
.wl-positions__edit-warning { color: var(--warn, #b45309); font-size: 12px; }
```

(If `Positions.css` defines a warning/notice class already — search for
`warn` — reuse it instead of adding a new one.)

- [ ] **Step 4: Forward currency in the live PATCH body**

In `frontend/src/routes/Positions.live.tsx` `handleEditPosition` (~line 255),
add with the other `if (updates.X !== undefined)` lines:

```ts
      if (updates.currency !== undefined) patchBody.currency = updates.currency;
```

- [ ] **Step 5: Run tests + typecheck**

Run (from `frontend/`): `npx vitest run src/components/PositionEditForm.test.tsx src/routes/Positions.test.tsx && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/PositionEditForm.tsx frontend/src/routes/Positions.live.tsx frontend/src/routes/Positions.css frontend/src/components/PositionEditForm.test.tsx
git commit -m "feat(positions-ui): editable position currency with provenance warning"
```

---

### Task 6: Full verification

- [ ] **Step 1: Backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 2: Frontend suite + typecheck**

Run (from `frontend/`): `npx vitest run && npx tsc --noEmit`
Expected: all PASS, tsc clean.

- [ ] **Step 3: Live smoke (servers usually already running: backend :8000, vite :5173)**

- `curl -s http://localhost:8000/api/portfolios | python3 -c "import json,sys; d=json.load(sys.stdin); print([p['positions'][0].get('currency') for p in d if p['positions']][:3])"` → currencies present.
- Browser: Positions page shows the CCY column; open a row → Contract Snapshot shows Currency; Edit Position shows the input and the warning when deviating.

- [ ] **Step 4: Final commit (if any stragglers)**

```bash
git status --short   # expect clean; commit anything intentional that remains
```
