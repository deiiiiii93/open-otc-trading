# Try to Solve RFQ Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Try to Solve RFQ workbench that renders all workbook product types, imports mixed-product Excel rows, validates/solves QuantArk-ready requests, and exports annotated Excel results.

**Architecture:** Add a backend canonical termsheet registry and stateless try-solve service, expose dedicated `/api/rfq/try-solve/*` endpoints, then build a dense React route driven by the registry. Excel import, manual form entry, validation, solving, and export all operate on the same normalized request queue.

**Tech Stack:** FastAPI, Pydantic, SQLAlchemy read access for market/pricing profiles, OpenPyXL, existing QuantArk adapter helpers, React 19, TypeScript, Vitest, Pytest.

---

## File Structure

Backend:

- Create `backend/app/services/try_solve_registry.py`: product registry for all 19 workbook sheets, English labels, Excel aliases, fields, quote fields, and QuantArk mapping metadata.
- Create `backend/app/services/try_solve.py`: import parser, queue normalization, validation, guarded solve, market resolution, and export workbook writer.
- Modify `backend/app/schemas.py`: request/response models for catalog, rows, batches, validation, solve, batch solve, and export.
- Modify `backend/app/main.py`: add `/api/rfq/try-solve/*` endpoints.
- Test `tests/test_try_solve.py`: focused backend coverage for registry, import, validation/solve, export, and API endpoints.

Frontend:

- Modify `frontend/src/types.ts`: add Try Solve catalog, row, batch, solve, export types and route key.
- Keep `frontend/src/api/client.ts` unchanged because the export endpoint returns a JSON payload with an artifact URL consumed through `window.location.assign`.
- Modify `frontend/src/main.tsx`: register `try-solve` route, nav item, command palette item, and route rendering.
- Create `frontend/src/routes/TrySolve.live.tsx`: API orchestration, profile loading, file import, solve/export calls.
- Create `frontend/src/routes/TrySolve.tsx`: presentational workbench route with header actions, queue, editor, market controls, and solve panel.
- Create `frontend/src/routes/TrySolve.css`: dense operational layout.
- Test `frontend/src/routes/TrySolve.test.tsx` and `frontend/src/routes/TrySolve.live.test.tsx`: UI behavior and API orchestration.

Verification commands:

- Backend focused: `.venv/bin/python -m pytest tests/test_try_solve.py -q`
- Backend regression: `.venv/bin/python -m pytest tests/test_quant_services.py tests/test_position_import_pricing.py tests/test_api.py -q`
- Frontend focused: `npm test -- TrySolve`
- Frontend build: `npm run build`

---

### Task 1: Backend Registry

**Files:**
- Create: `backend/app/services/try_solve_registry.py`
- Modify: `backend/app/schemas.py`
- Test: `tests/test_try_solve.py`

- [ ] **Step 1: Write failing registry tests**

Add `tests/test_try_solve.py` with:

```python
from __future__ import annotations

from app.services.try_solve_registry import (
    PRODUCT_KEYS,
    get_try_solve_catalog,
    registry_by_key,
)


def test_try_solve_catalog_contains_all_workbook_products():
    catalog = get_try_solve_catalog()
    keys = {product["product_key"] for product in catalog["products"]}

    assert keys == {
        "autocall",
        "phoenix",
        "vanilla",
        "vertical_spread",
        "digital",
        "binary_convex",
        "single_sf",
        "double_sf",
        "airbag",
        "airbag_spread",
        "asian",
        "call_put_portfolio",
        "ladder_binary",
        "forward",
        "range_accrual",
        "one_touch",
        "double_no_touch",
        "double_one_touch",
        "knock_out_autocall",
    }
    assert PRODUCT_KEYS == tuple(product["product_key"] for product in catalog["products"])


def test_registry_uses_english_labels_and_excel_aliases():
    vanilla = registry_by_key()["vanilla"]

    assert vanilla.label == "Vanilla"
    assert vanilla.excel_sheet == "vanilla"
    assert vanilla.fields["underlying"].label == "Underlying"
    assert "标的代码" in vanilla.fields["underlying"].excel_aliases
    assert vanilla.quote_fields["premium_rate"].label == "Premium Rate"
    assert vanilla.quote_fields["premium_rate"].excel_header == "期权费率"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_try_solve_catalog_contains_all_workbook_products tests/test_try_solve.py::test_registry_uses_english_labels_and_excel_aliases -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.try_solve_registry'`.

- [ ] **Step 3: Implement registry dataclasses and product coverage**

Create `backend/app/services/try_solve_registry.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


FieldType = Literal["text", "number", "date", "boolean", "select"]
SolverStatus = Literal["solver_ready", "schema_captured"]


@dataclass(frozen=True)
class TrySolveField:
    key: str
    label: str
    field_type: FieldType = "text"
    excel_aliases: tuple[str, ...] = ()
    required: bool = False
    default: Any = None
    options: tuple[str, ...] = ()
    canonical_path: str | None = None


@dataclass(frozen=True)
class TrySolveQuoteField:
    key: str
    label: str
    excel_header: str
    canonical_path: str
    lower_bound: float = 0.0
    upper_bound: float = 2.0
    initial_guess: float | None = None
    solver_ready: bool = False


@dataclass(frozen=True)
class TrySolveProduct:
    product_key: str
    label: str
    excel_sheet: str
    initial_solver_state: SolverStatus
    fields: dict[str, TrySolveField]
    quote_fields: dict[str, TrySolveQuoteField]
    quantark_product_type: str | None = None
    default_engine_name: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_key": self.product_key,
            "label": self.label,
            "excel_sheet": self.excel_sheet,
            "initial_solver_state": self.initial_solver_state,
            "fields": [field.__dict__ for field in self.fields.values()],
            "quote_fields": [field.__dict__ for field in self.quote_fields.values()],
            "quantark_product_type": self.quantark_product_type,
            "default_engine_name": self.default_engine_name,
            "notes": self.notes,
        }
```

Populate shared fields and all 19 products. Keep product definitions compact by composing common fields:

```python
COMMON_FIELDS = {
    "counterparty": TrySolveField("counterparty", "Counterparty", excel_aliases=("交易对手",)),
    "side": TrySolveField("side", "Side", "select", ("客户方向",), True, "buy", ("buy", "sell")),
    "underlying": TrySolveField("underlying", "Underlying", excel_aliases=("标的代码",), required=True),
    "notional": TrySolveField("notional", "Notional", "number", ("名义本金",), True, 1.0),
    "prepay_ratio": TrySolveField("prepay_ratio", "Prepay Ratio", "number", ("预付金比例",), False, 0.0),
    "annualized": TrySolveField("annualized", "Annualized", "boolean", ("是否年化",), False, True),
    "lock_time": TrySolveField("lock_time", "Lock Time", "date", ("锁价时间",)),
    "start_date": TrySolveField("start_date", "Start Date", "date", ("起始日",), True),
    "tenor_months": TrySolveField("tenor_months", "Tenor Months", "number", ("存续时间（月）",)),
    "tenor_days": TrySolveField("tenor_days", "Tenor Days", "number", ("存续时间（天）",)),
    "remarks": TrySolveField("remarks", "Remarks", excel_aliases=("备注",)),
}

QUOTE_FIELDS = {
    "premium_rate": TrySolveQuoteField("premium_rate", "Premium Rate", "期权费率", "premium_rate", -1.0, 1.0, 0.0),
    "fixed_yield": TrySolveQuoteField("fixed_yield", "Fixed Yield", "固定收益率", "fixed_yield", -1.0, 1.0, 0.0),
    "annualized_coupon": TrySolveQuoteField("annualized_coupon", "Annualized Coupon", "年化返息", "barrier_config.ko_rate", -1.0, 2.0, 0.1),
    "absolute_coupon": TrySolveQuoteField("absolute_coupon", "Absolute Coupon", "绝对返息", "absolute_coupon", -1.0, 2.0, 0.1),
    "exercise_yield": TrySolveQuoteField("exercise_yield", "Exercise Yield", "行权收益率", "exercise_yield", -1.0, 2.0, 0.1),
    "coupon_yield": TrySolveQuoteField("coupon_yield", "Coupon Yield", "派息收益率", "coupon_config.coupon_rate", -1.0, 2.0, 0.1),
    "ko_barrier": TrySolveQuoteField("ko_barrier", "Knock-Out Barrier", "敲出障碍", "barrier_config.ko_barrier", 0.01, 10.0, 1.03),
    "strike": TrySolveQuoteField("strike", "Strike", "行权价", "strike", 0.01, 10.0, 1.0),
}
```

Set `solver_ready=True` only for quote fields that currently map to known QuantArk unknown adapters for the given product. For products without reliable mapping, keep the product in `schema_captured` and the quote fields visible with `solver_ready=False`.

Add:

```python
PRODUCT_KEYS = tuple(product.product_key for product in PRODUCTS)


def registry_by_key() -> dict[str, TrySolveProduct]:
    return {product.product_key: product for product in PRODUCTS}


def registry_by_sheet() -> dict[str, TrySolveProduct]:
    return {product.excel_sheet: product for product in PRODUCTS}


def get_try_solve_catalog() -> dict[str, Any]:
    return {
        "products": [product.to_dict() for product in PRODUCTS],
        "status_options": [
            "draft",
            "missing_terms",
            "missing_market",
            "mapping_pending",
            "unsupported_quote_field",
            "quantark_build_failed",
            "solve_failed",
            "solver_ready",
            "solved",
        ],
    }
```

- [ ] **Step 4: Add schema models**

In `backend/app/schemas.py`, add Pydantic models near the RFQ schemas:

```python
class TrySolveMarketIn(BaseModel):
    pricing_parameter_profile_id: int | None = None
    market_data_profile_id: int | None = None
    valuation_date: datetime | None = None
    spot: float | None = None
    volatility: float | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    day_count_convention: str | None = None
    bus_days_in_year: int | None = None
    calendar: str | None = None


class TrySolveQuoteRequestIn(BaseModel):
    quote_field_key: str = "premium_rate"
    target_label: Literal["price", "premium", "reoffer"] = "price"
    target_value: float = 0.0
    lower_bound: float | None = None
    upper_bound: float | None = None
    initial_guess: float | None = None


class TrySolveRowIn(BaseModel):
    row_id: str
    source: Literal["manual", "excel"] = "manual"
    product_key: str
    source_sheet: str | None = None
    source_row: int | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    raw_values: dict[str, Any] = Field(default_factory=dict)
    market: TrySolveMarketIn = Field(default_factory=TrySolveMarketIn)
    quote_request: TrySolveQuoteRequestIn = Field(default_factory=TrySolveQuoteRequestIn)


class TrySolveRowOut(TrySolveRowIn):
    product_label: str
    status: str
    diagnostics: list[str] = Field(default_factory=list)
    quantark_product_type: str | None = None
    engine_name: str | None = None
    solved_value: float | None = None
    model_price: float | None = None
    residual: float | None = None
    executable_terms: dict[str, Any] | None = None


class TrySolveBatchOut(BaseModel):
    batch_id: str
    rows: list[TrySolveRowOut]
    summary: dict[str, Any] = Field(default_factory=dict)


class TrySolveValidateRequest(BaseModel):
    row: TrySolveRowIn


class TrySolveSolveRequest(BaseModel):
    row: TrySolveRowIn


class TrySolveBatchSolveRequest(BaseModel):
    rows: list[TrySolveRowIn]


class TrySolveExportRequest(BaseModel):
    rows: list[TrySolveRowOut]
    scope: Literal["all", "selected", "solved", "errors"] = "all"
    selected_row_ids: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Run registry tests**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_try_solve_catalog_contains_all_workbook_products tests/test_try_solve.py::test_registry_uses_english_labels_and_excel_aliases -q`

Expected: PASS.

- [ ] **Step 6: Commit backend registry**

```bash
git add backend/app/services/try_solve_registry.py backend/app/schemas.py tests/test_try_solve.py
git commit -m "feat(rfq): add try-solve termsheet registry"
```

---

### Task 2: Excel Import Normalization

**Files:**
- Create: `backend/app/services/try_solve.py`
- Modify: `tests/test_try_solve.py`

- [ ] **Step 1: Write failing import tests**

Append to `tests/test_try_solve.py`:

```python
from pathlib import Path

from openpyxl import Workbook

from app.services.try_solve import import_try_solve_workbook


def _write_try_solve_workbook(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "vanilla"
    ws.append(["询价类型", "交易对手", "客户方向", "标的代码", "名义本金", "起始日", "期权类型", "行权价", "期权费率", "询价状态"])
    ws.append(["期权", "招商财富", "买", "000016.SH", 30000000, "2024/7/11", "Call", 1.0, None, None])
    px = wb.create_sheet("phoenix")
    px.append(["询价类型", "交易对手", "客户方向", "标的代码", "名义本金", "起始日", "观察频率", "敲出障碍", "派息收益率", "询价状态"])
    px.append(["期权", "招商财富", "买", "000905.SH", 100000000, "2023/11/7", "1m", 1.03, None, None])
    wb.save(path)


def test_import_try_solve_workbook_parses_mixed_product_rows(tmp_path: Path):
    workbook = tmp_path / "rfq.xlsx"
    _write_try_solve_workbook(workbook)

    batch = import_try_solve_workbook(workbook)

    assert batch.batch_id
    assert len(batch.rows) == 2
    assert [row.product_key for row in batch.rows] == ["vanilla", "phoenix"]
    assert batch.rows[0].fields["underlying"] == "000016.SH"
    assert batch.rows[0].fields["notional"] == 30000000
    assert batch.rows[0].source_sheet == "vanilla"
    assert batch.rows[0].source_row == 2
    assert batch.rows[1].fields["observation_frequency"] == "1m"
    assert batch.summary["total_rows"] == 2


def test_import_try_solve_workbook_preserves_unknown_columns(tmp_path: Path):
    workbook = tmp_path / "rfq.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "vanilla"
    ws.append(["标的代码", "名义本金", "额外列"])
    ws.append(["000016.SH", 1_000_000, "keep me"])
    wb.save(workbook)

    batch = import_try_solve_workbook(workbook)

    assert batch.rows[0].raw_values["额外列"] == "keep me"
    assert any("Unmapped column: 额外列" in item for item in batch.rows[0].diagnostics)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_import_try_solve_workbook_parses_mixed_product_rows tests/test_try_solve.py::test_import_try_solve_workbook_preserves_unknown_columns -q`

Expected: FAIL with `ImportError` for `import_try_solve_workbook`.

- [ ] **Step 3: Implement import service**

Create `backend/app/services/try_solve.py` with:

```python
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from openpyxl import load_workbook, Workbook

from ..schemas import TrySolveBatchOut, TrySolveRowOut
from .try_solve_registry import TrySolveField, registry_by_sheet


def import_try_solve_workbook(path: str | Path) -> TrySolveBatchOut:
    workbook = load_workbook(Path(path), data_only=True)
    rows: list[TrySolveRowOut] = []
    by_sheet = registry_by_sheet()
    batch_id = f"try-solve-{uuid4().hex[:12]}"

    for sheet_name in workbook.sheetnames:
        product = by_sheet.get(sheet_name)
        if product is None:
            continue
        ws = workbook[sheet_name]
        headers = [_cell_value(ws.cell(row=1, column=col).value) for col in range(1, ws.max_column + 1)]
        alias_map = _alias_map(product.fields)
        for row_index in range(2, ws.max_row + 1):
            values = {
                header: ws.cell(row=row_index, column=col_index).value
                for col_index, header in enumerate(headers, start=1)
                if header
            }
            if not any(value not in (None, "") for value in values.values()):
                continue
            fields: dict[str, Any] = {}
            diagnostics: list[str] = []
            for header, value in values.items():
                key = alias_map.get(header)
                if key is None:
                    diagnostics.append(f"Unmapped column: {header}")
                    continue
                fields[key] = _normalize_cell(value)
            status = "solver_ready" if product.initial_solver_state == "solver_ready" else "mapping_pending"
            rows.append(
                TrySolveRowOut(
                    row_id=f"{sheet_name}:{row_index}",
                    source="excel",
                    product_key=product.product_key,
                    product_label=product.label,
                    source_sheet=sheet_name,
                    source_row=row_index,
                    fields=fields,
                    raw_values={key: _normalize_cell(value) for key, value in values.items()},
                    status=status,
                    diagnostics=diagnostics,
                    quantark_product_type=product.quantark_product_type,
                    engine_name=product.default_engine_name,
                )
            )

    return TrySolveBatchOut(
        batch_id=batch_id,
        rows=rows,
        summary={
            "total_rows": len(rows),
            "solver_ready": sum(1 for row in rows if row.status == "solver_ready"),
            "schema_captured": sum(1 for row in rows if row.status != "solver_ready"),
        },
    )


def _alias_map(fields: dict[str, TrySolveField]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for key, field in fields.items():
        for alias in field.excel_aliases:
            aliases[alias] = key
    return aliases


def _cell_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _normalize_cell(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
```

- [ ] **Step 4: Add registry aliases needed by import tests**

In `try_solve_registry.py`, ensure:

- `vanilla` has `option_type`, `strike`.
- `phoenix` has `observation_frequency`, `ko_barrier`, `coupon_yield`.
- common fields include `underlying`, `notional`, `start_date`.

Use English field labels and Chinese `excel_aliases`.

- [ ] **Step 5: Run import tests**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_import_try_solve_workbook_parses_mixed_product_rows tests/test_try_solve.py::test_import_try_solve_workbook_preserves_unknown_columns -q`

Expected: PASS.

- [ ] **Step 6: Commit import normalization**

```bash
git add backend/app/services/try_solve.py backend/app/services/try_solve_registry.py tests/test_try_solve.py
git commit -m "feat(rfq): import try-solve excel requests"
```

---

### Task 3: Validation and Guarded Solve

**Files:**
- Modify: `backend/app/services/try_solve.py`
- Modify: `tests/test_try_solve.py`

- [ ] **Step 1: Write failing validation and solve tests**

Append:

```python
from app.schemas import TrySolveQuoteRequestIn, TrySolveRowIn
from app.services.try_solve import solve_try_solve_row, validate_try_solve_row


def test_validate_try_solve_row_reports_missing_market():
    row = TrySolveRowIn(
        row_id="manual-1",
        product_key="vanilla",
        fields={"underlying": "000016.SH", "notional": 1_000_000, "strike": 1.0, "option_type": "CALL"},
        quote_request=TrySolveQuoteRequestIn(quote_field_key="strike", target_value=0.1),
    )

    validated = validate_try_solve_row(row)

    assert validated.status == "missing_market"
    assert any("spot" in item for item in validated.diagnostics)


def test_validate_try_solve_row_marks_unsupported_quote_field():
    row = TrySolveRowIn(
        row_id="manual-1",
        product_key="airbag",
        fields={"underlying": "000905.SH", "notional": 1_000_000},
        market={"spot": 100, "volatility": 0.2, "rate": 0.03, "dividend_yield": 0.01},
        quote_request=TrySolveQuoteRequestIn(quote_field_key="premium_rate", target_value=0.1),
    )

    validated = validate_try_solve_row(row)

    assert validated.status == "unsupported_quote_field"


def test_solve_try_solve_row_returns_vanilla_quantark_result():
    row = TrySolveRowIn(
        row_id="manual-1",
        product_key="vanilla",
        fields={
            "underlying": "000016.SH",
            "notional": 1,
            "strike": 100,
            "option_type": "CALL",
            "tenor_months": 12,
        },
        market={"spot": 100, "volatility": 0.2, "rate": 0.03, "dividend_yield": 0.01},
        quote_request=TrySolveQuoteRequestIn(
            quote_field_key="strike",
            target_label="price",
            target_value=8,
            lower_bound=50,
            upper_bound=150,
            initial_guess=100,
        ),
    )

    solved = solve_try_solve_row(row)

    assert solved.status == "solved"
    assert solved.solved_value is not None
    assert solved.model_price is not None
    assert solved.executable_terms is not None
    assert solved.quantark_product_type == "EuropeanVanillaOption"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_validate_try_solve_row_reports_missing_market tests/test_try_solve.py::test_validate_try_solve_row_marks_unsupported_quote_field tests/test_try_solve.py::test_solve_try_solve_row_returns_vanilla_quantark_result -q`

Expected: FAIL with missing functions.

- [ ] **Step 3: Implement row validation**

In `backend/app/services/try_solve.py`, add:

```python
from datetime import datetime

from ..schemas import PricingEnvironmentSnapshot, RFQRequestDraft, TrySolveRowIn
from .quantark import price_product, solve_rfq as quantark_solve_rfq, validate_quantark_build
from .rfq import _executable_terms_for_quote
from .try_solve_registry import registry_by_key


def validate_try_solve_row(row: TrySolveRowIn) -> TrySolveRowOut:
    product = registry_by_key().get(row.product_key)
    if product is None:
        return _row_out(row, "mapping_pending", [f"Unknown product key: {row.product_key}"])

    diagnostics: list[str] = []
    for field in product.fields.values():
        if field.required and row.fields.get(field.key) in (None, ""):
            diagnostics.append(f"Missing required field: {field.label}")

    market_errors = _market_errors(row)
    if market_errors:
        return _row_out(row, "missing_market", diagnostics + market_errors, product=product)

    quote_field = product.quote_fields.get(row.quote_request.quote_field_key)
    if quote_field is None or not quote_field.solver_ready:
        return _row_out(row, "unsupported_quote_field", diagnostics + ["Selected quote field is not solver-ready."], product=product)

    if diagnostics:
        return _row_out(row, "missing_terms", diagnostics, product=product)

    terms = _row_to_rfq_draft(row, product)
    build = validate_quantark_build(
        terms.product_type,
        terms.product_kwargs,
        terms.market,
        terms.engine_spec.engine_name,
        terms.engine_spec.engine_kwargs,
    )
    if not build.ok:
        return _row_out(row, "quantark_build_failed", [build.error or "QuantArk build failed"], product=product)

    return _row_out(row, "solver_ready", [], product=product)
```

Implement helpers:

```python
def _market_errors(row: TrySolveRowIn) -> list[str]:
    market = row.market
    errors = []
    if market.spot is None or market.spot <= 0:
        errors.append("Missing market input: spot")
    if market.volatility is None or market.volatility <= 0:
        errors.append("Missing market input: volatility")
    if market.rate is None:
        errors.append("Missing market input: rate")
    if market.dividend_yield is None:
        errors.append("Missing market input: dividend_yield")
    return errors


def _row_out(
    row: TrySolveRowIn,
    status: str,
    diagnostics: list[str],
    *,
    product: Any | None = None,
    **updates: Any,
) -> TrySolveRowOut:
    product = product or registry_by_key().get(row.product_key)
    return TrySolveRowOut(
        **row.model_dump(),
        product_label=product.label if product else row.product_key,
        status=status,
        diagnostics=diagnostics,
        quantark_product_type=product.quantark_product_type if product else None,
        engine_name=product.default_engine_name if product else None,
        **updates,
    )
```

- [ ] **Step 4: Implement minimal product mapping for vanilla**

In `_row_to_rfq_draft`, support at least `vanilla` for green tests:

```python
def _row_to_rfq_draft(row: TrySolveRowIn, product: Any) -> RFQRequestDraft:
    fields = row.fields
    quote_field = product.quote_fields[row.quote_request.quote_field_key]
    maturity = _maturity_years(fields)
    market = PricingEnvironmentSnapshot(
        asset_name=str(fields.get("underlying") or row.product_key),
        spot=float(row.market.spot or 0),
        volatility=float(row.market.volatility or 0),
        rate=float(row.market.rate or 0),
        dividend_yield=float(row.market.dividend_yield or 0),
        valuation_date=row.market.valuation_date or datetime.utcnow(),
        day_count_convention=row.market.day_count_convention or "ACT_365",
        bus_days_in_year=row.market.bus_days_in_year or 244,
    )
    if row.product_key == "vanilla":
        product_kwargs = {
            "strike": float(fields.get("strike") or 100.0),
            "option_type": str(fields.get("option_type") or "CALL").upper(),
            "maturity": maturity,
            "contract_multiplier": 1.0,
        }
    else:
        product_kwargs = dict(fields)

    return RFQRequestDraft(
        client_name=str(fields.get("counterparty") or "Try Solve"),
        underlying=str(fields.get("underlying") or market.asset_name),
        side="sell" if str(fields.get("side", "buy")).lower() in {"sell", "卖"} else "buy",
        quantity=float(fields.get("notional") or 1.0),
        quote_mode="solve",
        product_type=product.quantark_product_type or product.product_key,
        product_kwargs=product_kwargs,
        market=market,
        engine_spec={"engine_name": product.default_engine_name or "BlackScholesEngine"},
        unknown={
            "field_path": quote_field.canonical_path,
            "display_label": quote_field.label,
            "lower_bound": row.quote_request.lower_bound if row.quote_request.lower_bound is not None else quote_field.lower_bound,
            "upper_bound": row.quote_request.upper_bound if row.quote_request.upper_bound is not None else quote_field.upper_bound,
            "initial_guess": row.quote_request.initial_guess if row.quote_request.initial_guess is not None else quote_field.initial_guess,
        },
        target={"label": row.quote_request.target_label, "value": row.quote_request.target_value},
    )


def _maturity_years(fields: dict[str, Any]) -> float:
    months = fields.get("tenor_months")
    days = fields.get("tenor_days")
    if months not in (None, ""):
        return max(float(months), 1.0) / 12.0
    if days not in (None, ""):
        return max(float(days), 1.0) / 365.0
    return 1.0
```

- [ ] **Step 5: Implement solve**

```python
def solve_try_solve_row(row: TrySolveRowIn) -> TrySolveRowOut:
    validated = validate_try_solve_row(row)
    if validated.status != "solver_ready":
        return validated
    product = registry_by_key()[row.product_key]
    draft = _row_to_rfq_draft(row, product)
    result = quantark_solve_rfq(draft)
    if not result.ok:
        return _row_out(row, "solve_failed", [result.error or "QuantArk solve failed"], product=product)
    quote_payload = dict(result.data)
    executable = _executable_terms_for_quote(draft, "solve", quote_payload)
    residual = quote_payload.get("residual")
    return _row_out(
        row,
        "solved",
        [],
        product=product,
        solved_value=quote_payload.get("solved_value"),
        model_price=quote_payload.get("achieved_price"),
        residual=float(residual) if residual is not None else None,
        executable_terms=executable,
    )
```

- [ ] **Step 6: Run validation/solve tests**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_validate_try_solve_row_reports_missing_market tests/test_try_solve.py::test_validate_try_solve_row_marks_unsupported_quote_field tests/test_try_solve.py::test_solve_try_solve_row_returns_vanilla_quantark_result -q`

Expected: PASS.

- [ ] **Step 7: Commit validation and solve**

```bash
git add backend/app/services/try_solve.py tests/test_try_solve.py
git commit -m "feat(rfq): validate and solve try-solve rows"
```

---

### Task 4: Excel Export

**Files:**
- Modify: `backend/app/services/try_solve.py`
- Modify: `tests/test_try_solve.py`

- [ ] **Step 1: Write failing export test**

Append:

```python
from openpyxl import load_workbook

from app.schemas import TrySolveRowOut
from app.services.try_solve import export_try_solve_workbook


def test_export_try_solve_workbook_writes_status_and_results(tmp_path: Path):
    output = tmp_path / "try-solve-results.xlsx"
    row = TrySolveRowOut(
        row_id="vanilla:2",
        source="excel",
        product_key="vanilla",
        product_label="Vanilla",
        source_sheet="vanilla",
        source_row=2,
        fields={"underlying": "000016.SH", "notional": 1_000_000, "strike": 100},
        raw_values={"标的代码": "000016.SH", "名义本金": 1_000_000},
        quote_request={"quote_field_key": "strike", "target_label": "price", "target_value": 8},
        status="solved",
        solved_value=98.5,
        model_price=8.0,
        residual=0.0,
        quantark_product_type="EuropeanVanillaOption",
        engine_name="BlackScholesEngine",
    )

    export_try_solve_workbook([row], output)

    wb = load_workbook(output, data_only=True)
    ws = wb["vanilla"]
    headers = [cell.value for cell in ws[1]]
    values = dict(zip(headers, [cell.value for cell in ws[2]]))
    assert values["标的代码"] == "000016.SH"
    assert values["Solve Status"] == "solved"
    assert values["Solved Value"] == 98.5
    assert values["Model Price"] == 8.0
    assert values["QuantArk Product"] == "EuropeanVanillaOption"
```

- [ ] **Step 2: Run export test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_export_try_solve_workbook_writes_status_and_results -q`

Expected: FAIL with missing `export_try_solve_workbook`.

- [ ] **Step 3: Implement workbook export**

In `backend/app/services/try_solve.py`, add:

```python
EXPORT_COLUMNS = (
    "Solve Status",
    "Model Price",
    "Residual",
    "Error",
    "Solved Field",
    "Solved Value",
    "Target Label",
    "Target Value",
    "QuantArk Product",
    "Engine",
)


def export_try_solve_workbook(rows: list[TrySolveRowOut], output_path: str | Path) -> Path:
    grouped: dict[str, list[TrySolveRowOut]] = {}
    for row in rows:
        sheet = row.source_sheet or row.product_key
        grouped.setdefault(sheet, []).append(row)

    wb = Workbook()
    default = wb.active
    wb.remove(default)

    for sheet, sheet_rows in grouped.items():
        ws = wb.create_sheet(sheet[:31])
        raw_headers = _ordered_raw_headers(sheet_rows)
        headers = raw_headers + [col for col in EXPORT_COLUMNS if col not in raw_headers]
        ws.append(headers)
        for row in sheet_rows:
            export_values = _export_values(row)
            ws.append([export_values.get(header) for header in headers])

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output)
    return output


def _ordered_raw_headers(rows: list[TrySolveRowOut]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key in row.raw_values:
            if key not in seen:
                seen.append(key)
    return seen


def _export_values(row: TrySolveRowOut) -> dict[str, Any]:
    quote_request = row.quote_request
    values = dict(row.raw_values)
    values.update(
        {
            "Solve Status": row.status,
            "Model Price": row.model_price,
            "Residual": row.residual,
            "Error": "; ".join(row.diagnostics),
            "Solved Field": quote_request.quote_field_key,
            "Solved Value": row.solved_value,
            "Target Label": quote_request.target_label,
            "Target Value": quote_request.target_value,
            "QuantArk Product": row.quantark_product_type,
            "Engine": row.engine_name,
        }
    )
    return values
```

- [ ] **Step 4: Run export test**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_export_try_solve_workbook_writes_status_and_results -q`

Expected: PASS.

- [ ] **Step 5: Commit export service**

```bash
git add backend/app/services/try_solve.py tests/test_try_solve.py
git commit -m "feat(rfq): export try-solve workbooks"
```

---

### Task 5: Backend API Endpoints

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/services/try_solve.py`
- Modify: `tests/test_try_solve.py`

- [ ] **Step 1: Write failing API tests**

Append:

```python
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _make_try_solve_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'api.sqlite3'}",
        artifact_dir=tmp_path / "artifacts",
    )
    return TestClient(create_app(settings))


def test_try_solve_catalog_endpoint(tmp_path: Path):
    client = _make_try_solve_client(tmp_path)

    response = client.get("/api/rfq/try-solve/catalog")

    assert response.status_code == 200
    assert len(response.json()["products"]) == 19


def test_try_solve_import_endpoint_accepts_mixed_workbook(tmp_path: Path):
    client = _make_try_solve_client(tmp_path)
    workbook = tmp_path / "rfq.xlsx"
    _write_try_solve_workbook(workbook)

    with workbook.open("rb") as handle:
        response = client.post(
            "/api/rfq/try-solve/import",
            files={"file": ("rfq.xlsx", handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )

    assert response.status_code == 200
    assert response.json()["summary"]["total_rows"] == 2


def test_try_solve_export_endpoint_returns_download_url(tmp_path: Path):
    client = _make_try_solve_client(tmp_path)

    response = client.post(
        "/api/rfq/try-solve/export",
        json={
            "scope": "all",
            "rows": [
                {
                    "row_id": "vanilla:2",
                    "source": "manual",
                    "product_key": "vanilla",
                    "product_label": "Vanilla",
                    "fields": {"underlying": "000016.SH"},
                    "raw_values": {"标的代码": "000016.SH"},
                    "quote_request": {"quote_field_key": "strike", "target_label": "price", "target_value": 8},
                    "status": "solved",
                    "diagnostics": [],
                    "solved_value": 98.5,
                    "model_price": 8.0,
                    "residual": 0.0,
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["url"].endswith(".xlsx")
```

- [ ] **Step 2: Run API tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_try_solve_catalog_endpoint tests/test_try_solve.py::test_try_solve_import_endpoint_accepts_mixed_workbook tests/test_try_solve.py::test_try_solve_export_endpoint_returns_download_url -q`

Expected: FAIL with `404`.

- [ ] **Step 3: Add export response schema**

In `backend/app/schemas.py`:

```python
class TrySolveExportOut(BaseModel):
    filename: str
    url: str
    row_count: int
    scope: str
```

- [ ] **Step 4: Wire endpoints**

In `backend/app/main.py`, import schemas and services:

```python
from .schemas import (
    TrySolveBatchOut,
    TrySolveBatchSolveRequest,
    TrySolveExportOut,
    TrySolveExportRequest,
    TrySolveSolveRequest,
    TrySolveValidateRequest,
)
from .services.try_solve import (
    export_try_solve_workbook,
    import_try_solve_workbook,
    solve_try_solve_row,
    validate_try_solve_row,
)
from .services.try_solve_registry import get_try_solve_catalog
```

Add endpoints near RFQ routes:

```python
@app.get("/api/rfq/try-solve/catalog")
def get_try_solve_catalog_route():
    return get_try_solve_catalog()


@app.post("/api/rfq/try-solve/import", response_model=TrySolveBatchOut)
def import_try_solve_excel(file: UploadFile = File(...)):
    upload_path = _store_upload(file, "try-solve")
    return import_try_solve_workbook(upload_path)


@app.post("/api/rfq/try-solve/validate")
def validate_try_solve(payload: TrySolveValidateRequest):
    return validate_try_solve_row(payload.row)


@app.post("/api/rfq/try-solve/solve")
def solve_try_solve(payload: TrySolveSolveRequest):
    return solve_try_solve_row(payload.row)


@app.post("/api/rfq/try-solve/solve-batch")
def solve_try_solve_batch(payload: TrySolveBatchSolveRequest):
    rows = [solve_try_solve_row(row) for row in payload.rows]
    return {"rows": rows, "summary": {"total_rows": len(rows), "solved": sum(1 for row in rows if row.status == "solved")}}


@app.post("/api/rfq/try-solve/export", response_model=TrySolveExportOut)
def export_try_solve(payload: TrySolveExportRequest):
    rows = _filter_try_solve_export_rows(payload)
    filename = f"try-solve-results-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.xlsx"
    output = active_settings.artifact_dir / filename
    export_try_solve_workbook(rows, output)
    return TrySolveExportOut(filename=filename, url=f"/api/artifacts/{filename}", row_count=len(rows), scope=payload.scope)
```

Add a local helper inside `create_app`:

```python
def _filter_try_solve_export_rows(payload: TrySolveExportRequest):
    rows = payload.rows
    if payload.scope == "selected":
        selected = set(payload.selected_row_ids)
        return [row for row in rows if row.row_id in selected]
    if payload.scope == "solved":
        return [row for row in rows if row.status == "solved"]
    if payload.scope == "errors":
        return [row for row in rows if row.status not in {"solved", "solver_ready"}]
    return rows
```

- [ ] **Step 5: Run API tests**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py::test_try_solve_catalog_endpoint tests/test_try_solve.py::test_try_solve_import_endpoint_accepts_mixed_workbook tests/test_try_solve.py::test_try_solve_export_endpoint_returns_download_url -q`

Expected: PASS.

- [ ] **Step 6: Commit API endpoints**

```bash
git add backend/app/main.py backend/app/schemas.py backend/app/services/try_solve.py tests/test_try_solve.py
git commit -m "feat(rfq): expose try-solve workbench api"
```

---

### Task 6: Frontend Route Skeleton

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/main.tsx`
- Create: `frontend/src/routes/TrySolve.tsx`
- Create: `frontend/src/routes/TrySolve.css`
- Test: `frontend/src/routes/TrySolve.test.tsx`

- [ ] **Step 1: Write failing route skeleton test**

Create `frontend/src/routes/TrySolve.test.tsx`:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TrySolve } from './TrySolve';
import type { TrySolveCatalog } from '../types';

const catalog: TrySolveCatalog = {
  products: [
    {
      product_key: 'vanilla',
      label: 'Vanilla',
      excel_sheet: 'vanilla',
      initial_solver_state: 'solver_ready',
      fields: [],
      quote_fields: [],
      quantark_product_type: 'EuropeanVanillaOption',
      default_engine_name: 'BlackScholesEngine',
      notes: '',
    },
    {
      product_key: 'phoenix',
      label: 'Phoenix',
      excel_sheet: 'phoenix',
      initial_solver_state: 'solver_ready',
      fields: [],
      quote_fields: [],
      quantark_product_type: 'PhoenixOption',
      default_engine_name: 'PhoenixQuadEngine',
      notes: '',
    },
  ],
  status_options: [],
};

describe('TrySolve', () => {
  it('renders page header actions and product library', () => {
    render(
      <TrySolve
        catalog={catalog}
        rows={[]}
        selectedRowId={null}
        onSelectRow={() => {}}
        onImportExcel={vi.fn()}
        onExportExcel={vi.fn()}
        onUpdateRow={() => {}}
        onValidateRow={vi.fn()}
        onSolveRow={vi.fn()}
        onSolveReadyQueue={vi.fn()}
      />,
    );

    expect(screen.getByText('TRY TO SOLVE')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /import excel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /export excel/i })).toBeInTheDocument();
    expect(screen.getByText('Vanilla')).toBeInTheDocument();
    expect(screen.getByText('Phoenix')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run frontend test to verify it fails**

Run: `npm test -- TrySolve.test.tsx`

Expected: FAIL because `TrySolve` and types do not exist.

- [ ] **Step 3: Add TypeScript types**

In `frontend/src/types.ts`, add route key:

```ts
| 'try-solve'
```

Add types:

```ts
export type TrySolveField = {
  key: string;
  label: string;
  field_type: 'text' | 'number' | 'date' | 'boolean' | 'select';
  excel_aliases: string[];
  required: boolean;
  default?: unknown;
  options: string[];
  canonical_path?: string | null;
};

export type TrySolveQuoteField = {
  key: string;
  label: string;
  excel_header: string;
  canonical_path: string;
  lower_bound: number;
  upper_bound: number;
  initial_guess?: number | null;
  solver_ready: boolean;
};

export type TrySolveProduct = {
  product_key: string;
  label: string;
  excel_sheet: string;
  initial_solver_state: 'solver_ready' | 'schema_captured';
  fields: TrySolveField[];
  quote_fields: TrySolveQuoteField[];
  quantark_product_type?: string | null;
  default_engine_name?: string | null;
  notes: string;
};

export type TrySolveCatalog = {
  products: TrySolveProduct[];
  status_options: string[];
};

export type TrySolveRow = {
  row_id: string;
  source: 'manual' | 'excel';
  product_key: string;
  product_label: string;
  source_sheet?: string | null;
  source_row?: number | null;
  fields: Record<string, unknown>;
  raw_values: Record<string, unknown>;
  market: Record<string, unknown>;
  quote_request: {
    quote_field_key: string;
    target_label: 'price' | 'premium' | 'reoffer';
    target_value: number;
    lower_bound?: number | null;
    upper_bound?: number | null;
    initial_guess?: number | null;
  };
  status: string;
  diagnostics: string[];
  quantark_product_type?: string | null;
  engine_name?: string | null;
  solved_value?: number | null;
  model_price?: number | null;
  residual?: number | null;
  executable_terms?: Record<string, unknown> | null;
};
```

- [ ] **Step 4: Implement route skeleton**

Create `frontend/src/routes/TrySolve.tsx`:

```tsx
import { Upload, Download, Play, CheckCircle2 } from 'lucide-react';
import { Button } from '../components/Button';
import { PageHeader } from '../components/PageHeader';
import type { TrySolveCatalog, TrySolveRow } from '../types';
import './TrySolve.css';

type Props = {
  catalog: TrySolveCatalog | null;
  rows: TrySolveRow[];
  selectedRowId: string | null;
  onSelectRow: (rowId: string) => void;
  onImportExcel: (file: File) => void;
  onExportExcel: (scope: 'all' | 'selected' | 'solved' | 'errors') => void;
  onUpdateRow: (row: TrySolveRow) => void;
  onValidateRow: (row: TrySolveRow) => void;
  onSolveRow: (row: TrySolveRow) => void;
  onSolveReadyQueue: () => void;
};

export function TrySolve({
  catalog,
  rows,
  selectedRowId,
  onSelectRow,
  onImportExcel,
  onExportExcel,
  onUpdateRow,
  onValidateRow,
  onSolveRow,
  onSolveReadyQueue,
}: Props) {
  const selected = rows.find((row) => row.row_id === selectedRowId) ?? rows[0] ?? null;
  const chips = [`${rows.length} requests`, `${rows.filter((row) => row.status === 'solved').length} solved`];

  return (
    <>
      <PageHeader
        title="TRY TO SOLVE"
        chips={chips}
        action={
          <div className="wl-try-solve__header-actions">
            <label className="wl-try-solve__file-button">
              <Upload size={16} aria-hidden />
              Import Excel
              <input
                type="file"
                accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                aria-label="Import try solve excel"
                onChange={(event) => {
                  const file = event.currentTarget.files?.[0];
                  if (file) onImportExcel(file);
                  event.currentTarget.value = '';
                }}
              />
            </label>
            <Button onClick={() => onExportExcel('all')}>
              <Download size={16} aria-hidden />
              Export Excel
            </Button>
          </div>
        }
      />
      <div className="wl-try-solve">
        <aside className="wl-try-solve__queue">
          <section>
            <h2>Request Queue</h2>
            {rows.length === 0 ? (
              <p className="wl-try-solve__empty">No imported or manual requests.</p>
            ) : (
              rows.map((row) => (
                <button
                  key={row.row_id}
                  type="button"
                  className={`wl-try-solve__queue-row ${row.row_id === selected?.row_id ? 'wl-try-solve__queue-row--active' : ''}`.trim()}
                  onClick={() => onSelectRow(row.row_id)}
                >
                  <span>{row.product_label}</span>
                  <small>{row.source_sheet ? `Row ${row.source_row}` : row.row_id}</small>
                  <em>{row.status}</em>
                </button>
              ))
            )}
          </section>
          <section>
            <h2>Product Library</h2>
            <div className="wl-try-solve__product-list">
              {(catalog?.products ?? []).map((product) => (
                <div key={product.product_key} className="wl-try-solve__product">
                  <span>{product.label}</span>
                  <small>{product.initial_solver_state === 'solver_ready' ? 'Solver ready' : 'Schema captured'}</small>
                </div>
              ))}
            </div>
          </section>
        </aside>
        <main className="wl-try-solve__editor">
          <h2>{selected ? selected.product_label : 'Select a request'}</h2>
          <div className="wl-try-solve__panel-empty">Canonical termsheet editor</div>
        </main>
        <aside className="wl-try-solve__solve">
          <h2>Solve Panel</h2>
          <Button disabled={!selected} onClick={() => selected && onValidateRow(selected)}>
            <CheckCircle2 size={16} aria-hidden />
            Validate Termsheet
          </Button>
          <Button disabled={!selected} onClick={() => selected && onSolveRow(selected)}>
            <Play size={16} aria-hidden />
            Try to Solve Current
          </Button>
          <Button onClick={onSolveReadyQueue}>Solve Ready Queue</Button>
          <div className="wl-try-solve__panel-empty">Result and diagnostics</div>
        </aside>
      </div>
    </>
  );
}
```

Create `frontend/src/routes/TrySolve.css` with a compact layout:

```css
.wl-try-solve {
  display: grid;
  grid-template-columns: 280px minmax(0, 1fr) 320px;
  gap: var(--gap-3);
  align-items: start;
}
.wl-try-solve__header-actions {
  display: flex;
  gap: var(--gap-2);
  align-items: center;
}
.wl-try-solve__file-button {
  display: inline-flex;
  align-items: center;
  gap: var(--gap-1);
  border: 1px solid var(--line);
  background: var(--paper);
  color: var(--ink);
  border-radius: 6px;
  padding: 7px 10px;
  font-size: var(--type-small-size);
  cursor: pointer;
}
.wl-try-solve__file-button input { display: none; }
.wl-try-solve__queue,
.wl-try-solve__editor,
.wl-try-solve__solve {
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--paper);
  padding: var(--gap-3);
}
.wl-try-solve__queue,
.wl-try-solve__solve {
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
}
.wl-try-solve h2 {
  margin: 0 0 var(--gap-2);
  font-size: var(--type-body-size);
}
.wl-try-solve__queue-row {
  width: 100%;
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 2px var(--gap-2);
  text-align: left;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 6px;
  padding: 8px;
  color: var(--ink);
}
.wl-try-solve__queue-row--active {
  border-color: var(--accent);
  background: var(--paper-2);
}
.wl-try-solve__queue-row small,
.wl-try-solve__queue-row em,
.wl-try-solve__product small,
.wl-try-solve__empty {
  color: var(--ink-2);
  font-size: var(--type-small-size);
}
.wl-try-solve__queue-row em {
  grid-column: 1 / -1;
  font-style: normal;
}
.wl-try-solve__product-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.wl-try-solve__product {
  display: flex;
  justify-content: space-between;
  gap: var(--gap-2);
  padding: 7px 0;
  border-bottom: 1px solid var(--line);
}
.wl-try-solve__panel-empty {
  border: 1px dashed var(--line);
  border-radius: 6px;
  color: var(--ink-2);
  padding: var(--gap-4);
}
@media (max-width: 1180px) {
  .wl-try-solve { grid-template-columns: 1fr; }
}
```

- [ ] **Step 5: Register navigation**

In `frontend/src/main.tsx`:

- import `TrySolveLive`,
- add nav item `{ route: 'try-solve' as const, label: 'Try to Solve' }`,
- add command palette item `jump-try-solve`,
- render `{route === 'try-solve' && <TrySolveLive />}`.

- [ ] **Step 6: Run route skeleton test**

Run: `npm test -- TrySolve.test.tsx`

Expected: PASS.

- [ ] **Step 7: Commit frontend skeleton**

```bash
git add frontend/src/types.ts frontend/src/main.tsx frontend/src/routes/TrySolve.tsx frontend/src/routes/TrySolve.css frontend/src/routes/TrySolve.test.tsx
git commit -m "feat(rfq): add try-solve route skeleton"
```

---

### Task 7: Frontend API Orchestration and Workbench Behavior

**Files:**
- Create: `frontend/src/routes/TrySolve.live.tsx`
- Modify: `frontend/src/routes/TrySolve.tsx`
- Modify: `frontend/src/routes/TrySolve.css`
- Test: `frontend/src/routes/TrySolve.live.test.tsx`

- [ ] **Step 1: Write failing live route test**

Create `frontend/src/routes/TrySolve.live.test.tsx`:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { TrySolveLive } from './TrySolve.live';

vi.mock('../api/client', () => ({
  api: vi.fn(),
  uploadForm: vi.fn(),
}));

import { api, uploadForm } from '../api/client';

describe('TrySolveLive', () => {
  beforeEach(() => {
    vi.mocked(api).mockReset();
    vi.mocked(uploadForm).mockReset();
  });

  it('loads catalog and imports an excel workbook into the queue', async () => {
    vi.mocked(api).mockResolvedValueOnce({
      products: [{ product_key: 'vanilla', label: 'Vanilla', excel_sheet: 'vanilla', initial_solver_state: 'solver_ready', fields: [], quote_fields: [], notes: '' }],
      status_options: [],
    });
    vi.mocked(uploadForm).mockResolvedValueOnce({
      batch_id: 'batch-1',
      rows: [{ row_id: 'vanilla:2', source: 'excel', product_key: 'vanilla', product_label: 'Vanilla', fields: {}, raw_values: {}, market: {}, quote_request: { quote_field_key: 'strike', target_label: 'price', target_value: 8 }, status: 'solver_ready', diagnostics: [] }],
      summary: { total_rows: 1 },
    });

    render(<TrySolveLive />);
    await screen.findByText('Vanilla');

    const file = new File(['xlsx'], 'rfq.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    await userEvent.upload(screen.getByLabelText(/import try solve excel/i), file);

    await screen.findByText(/Row 2/i);
    expect(uploadForm).toHaveBeenCalledWith('/api/rfq/try-solve/import', expect.any(FormData));
  });

  it('solves the selected row and updates queue state', async () => {
    vi.mocked(api)
      .mockResolvedValueOnce({
        products: [{ product_key: 'vanilla', label: 'Vanilla', excel_sheet: 'vanilla', initial_solver_state: 'solver_ready', fields: [], quote_fields: [], notes: '' }],
        status_options: [],
      })
      .mockResolvedValueOnce({
        row_id: 'manual-1',
        source: 'manual',
        product_key: 'vanilla',
        product_label: 'Vanilla',
        fields: {},
        raw_values: {},
        market: {},
        quote_request: { quote_field_key: 'strike', target_label: 'price', target_value: 8 },
        status: 'solved',
        diagnostics: [],
        solved_value: 98.5,
        model_price: 8,
      });

    render(<TrySolveLive initialRows={[{ row_id: 'manual-1', source: 'manual', product_key: 'vanilla', product_label: 'Vanilla', fields: {}, raw_values: {}, market: {}, quote_request: { quote_field_key: 'strike', target_label: 'price', target_value: 8 }, status: 'solver_ready', diagnostics: [] }]} />);

    await userEvent.click(await screen.findByRole('button', { name: /try to solve current/i }));

    await waitFor(() => expect(screen.getByText(/solved/i)).toBeInTheDocument());
    expect(api).toHaveBeenCalledWith('/api/rfq/try-solve/solve', expect.objectContaining({ method: 'POST' }));
  });
});
```

- [ ] **Step 2: Run live test to verify it fails**

Run: `npm test -- TrySolve.live.test.tsx`

Expected: FAIL because `TrySolve.live` does not exist and the presentational route is incomplete.

- [ ] **Step 3: Implement live route**

Create `frontend/src/routes/TrySolve.live.tsx`:

```tsx
import { useEffect, useState } from 'react';
import { api, uploadForm } from '../api/client';
import type { TrySolveCatalog, TrySolveRow } from '../types';
import { TrySolve } from './TrySolve';

type Props = { initialRows?: TrySolveRow[] };

export function TrySolveLive({ initialRows = [] }: Props) {
  const [catalog, setCatalog] = useState<TrySolveCatalog | null>(null);
  const [rows, setRows] = useState<TrySolveRow[]>(initialRows);
  const [selectedRowId, setSelectedRowId] = useState<string | null>(initialRows[0]?.row_id ?? null);

  useEffect(() => {
    void api<TrySolveCatalog>('/api/rfq/try-solve/catalog').then(setCatalog);
  }, []);

  const replaceRow = (next: TrySolveRow) => {
    setRows((current) => current.map((row) => (row.row_id === next.row_id ? next : row)));
  };

  const handleImportExcel = async (file: File) => {
    const form = new FormData();
    form.append('file', file);
    const batch = await uploadForm<{ rows: TrySolveRow[] }>('/api/rfq/try-solve/import', form);
    setRows(batch.rows);
    setSelectedRowId(batch.rows[0]?.row_id ?? null);
  };

  const handleValidateRow = async (row: TrySolveRow) => {
    const next = await api<TrySolveRow>('/api/rfq/try-solve/validate', {
      method: 'POST',
      body: JSON.stringify({ row }),
    });
    replaceRow(next);
  };

  const handleSolveRow = async (row: TrySolveRow) => {
    const next = await api<TrySolveRow>('/api/rfq/try-solve/solve', {
      method: 'POST',
      body: JSON.stringify({ row }),
    });
    replaceRow(next);
  };

  const handleSolveReadyQueue = async () => {
    const ready = rows.filter((row) => row.status === 'solver_ready');
    const result = await api<{ rows: TrySolveRow[] }>('/api/rfq/try-solve/solve-batch', {
      method: 'POST',
      body: JSON.stringify({ rows: ready }),
    });
    const byId = new Map(result.rows.map((row) => [row.row_id, row]));
    setRows((current) => current.map((row) => byId.get(row.row_id) ?? row));
  };

  const handleExportExcel = async (scope: 'all' | 'selected' | 'solved' | 'errors') => {
    const result = await api<{ url: string }>('/api/rfq/try-solve/export', {
      method: 'POST',
      body: JSON.stringify({ rows, scope, selected_row_ids: selectedRowId ? [selectedRowId] : [] }),
    });
    window.location.assign(result.url);
  };

  return (
    <TrySolve
      catalog={catalog}
      rows={rows}
      selectedRowId={selectedRowId}
      onSelectRow={setSelectedRowId}
      onImportExcel={handleImportExcel}
      onExportExcel={handleExportExcel}
      onUpdateRow={replaceRow}
      onValidateRow={handleValidateRow}
      onSolveRow={handleSolveRow}
      onSolveReadyQueue={handleSolveReadyQueue}
    />
  );
}
```

- [ ] **Step 4: Expand presentational editor enough for live tests**

In `TrySolve.tsx`, ensure selected row displays source row and status text in stable elements. Add result display:

```tsx
{selected && (
  <dl className="wl-try-solve__result">
    <div><dt>Status</dt><dd>{selected.status}</dd></div>
    {selected.solved_value != null && <div><dt>Solved Value</dt><dd>{selected.solved_value}</dd></div>}
    {selected.model_price != null && <div><dt>Model Price</dt><dd>{selected.model_price}</dd></div>}
  </dl>
)}
```

- [ ] **Step 5: Run live frontend tests**

Run: `npm test -- TrySolve`

Expected: PASS.

- [ ] **Step 6: Commit live route**

```bash
git add frontend/src/routes/TrySolve.live.tsx frontend/src/routes/TrySolve.tsx frontend/src/routes/TrySolve.css frontend/src/routes/TrySolve.live.test.tsx
git commit -m "feat(rfq): wire try-solve workbench interactions"
```

---

### Task 8: Profile-Backed Market Inputs and Form Editing

**Files:**
- Modify: `frontend/src/routes/TrySolve.live.tsx`
- Modify: `frontend/src/routes/TrySolve.tsx`
- Modify: `frontend/src/routes/TrySolve.css`
- Modify: `backend/app/services/try_solve.py`
- Modify: `tests/test_try_solve.py`
- Modify: `frontend/src/routes/TrySolve.test.tsx`

- [ ] **Step 1: Write failing backend profile resolution test**

Append:

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, PricingParameterProfile, PricingParameterRow
from app.services.try_solve import resolve_try_solve_market


def test_resolve_try_solve_market_uses_profile_then_manual_overrides(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'profiles.sqlite3'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    profile = PricingParameterProfile(name="2026-05-13", valuation_date="2026-05-13", source_type="xlsx", status="active")
    profile.rows.append(PricingParameterRow(source_trade_id="T1", symbol="000016.SH", spot=100, volatility=0.2, rate=0.03, dividend_yield=0.01))
    session.add(profile)
    session.commit()

    row = TrySolveRowIn(
        row_id="manual-1",
        product_key="vanilla",
        fields={"underlying": "000016.SH"},
        market={"pricing_parameter_profile_id": profile.id, "spot": 101},
    )

    market = resolve_try_solve_market(session, row)

    assert market.spot == 101
    assert market.volatility == 0.2
    assert market.rate == 0.03
    assert market.dividend_yield == 0.01
```

- [ ] **Step 2: Write failing frontend market controls test**

In `frontend/src/routes/TrySolve.test.tsx`, add:

```tsx
it('renders market profile and manual override controls for selected row', () => {
  render(
    <TrySolve
      catalog={catalog}
      rows={[{ row_id: 'manual-1', source: 'manual', product_key: 'vanilla', product_label: 'Vanilla', fields: {}, raw_values: {}, market: {}, quote_request: { quote_field_key: 'strike', target_label: 'price', target_value: 8 }, status: 'solver_ready', diagnostics: [] }]}
      selectedRowId="manual-1"
      pricingProfiles={[{ id: 1, name: 'Profile 1' }]}
      marketDataProfiles={[{ id: 2, name: 'Market 1' }]}
      onSelectRow={() => {}}
      onImportExcel={vi.fn()}
      onExportExcel={vi.fn()}
      onUpdateRow={() => {}}
      onValidateRow={vi.fn()}
      onSolveRow={vi.fn()}
      onSolveReadyQueue={vi.fn()}
    />,
  );

  expect(screen.getByLabelText(/pricing parameter profile/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/spot override/i)).toBeInTheDocument();
  expect(screen.getByLabelText(/volatility override/i)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_try_solve.py::test_resolve_try_solve_market_uses_profile_then_manual_overrides -q
npm test -- TrySolve.test.tsx
```

Expected: backend fails missing resolver; frontend fails missing props/controls.

- [ ] **Step 4: Implement backend market resolution**

In `backend/app/services/try_solve.py`, add:

```python
from sqlalchemy.orm import Session
from ..models import PricingParameterProfile


def resolve_try_solve_market(session: Session, row: TrySolveRowIn) -> PricingEnvironmentSnapshot:
    profile_values: dict[str, Any] = {}
    if row.market.pricing_parameter_profile_id is not None:
        profile = session.get(PricingParameterProfile, row.market.pricing_parameter_profile_id)
        symbol = str(row.fields.get("underlying") or "")
        if profile is not None:
            matched = next((item for item in profile.rows if item.symbol == symbol), None)
            if matched is not None:
                profile_values = {
                    "spot": matched.spot,
                    "volatility": matched.volatility,
                    "rate": matched.rate,
                    "dividend_yield": matched.dividend_yield,
                    "valuation_date": profile.valuation_date,
                    "asset_name": matched.symbol,
                }
    return PricingEnvironmentSnapshot(
        asset_name=str(profile_values.get("asset_name") or row.fields.get("underlying") or "UNKNOWN"),
        valuation_date=row.market.valuation_date or profile_values.get("valuation_date") or datetime.utcnow(),
        spot=float(row.market.spot if row.market.spot is not None else profile_values.get("spot") or 0),
        volatility=float(row.market.volatility if row.market.volatility is not None else profile_values.get("volatility") or 0),
        rate=float(row.market.rate if row.market.rate is not None else profile_values.get("rate") or 0),
        dividend_yield=float(row.market.dividend_yield if row.market.dividend_yield is not None else profile_values.get("dividend_yield") or 0),
        day_count_convention=row.market.day_count_convention or "ACT_365",
        bus_days_in_year=row.market.bus_days_in_year or 244,
    )
```

Then thread optional `session` into `validate_try_solve_row` and `solve_try_solve_row`, using `resolve_try_solve_market(session, row)` when a session is provided.

- [ ] **Step 5: Implement frontend controls**

Add optional props to `TrySolve`:

```ts
pricingProfiles?: Array<{ id: number; name: string }>;
marketDataProfiles?: Array<{ id: number; name: string }>;
```

Render controls in center editor:

```tsx
<section className="wl-try-solve__market">
  <h3>Market Inputs</h3>
  <label>
    Pricing Parameter Profile
    <select
      aria-label="Pricing Parameter Profile"
      value={String(selected.market.pricing_parameter_profile_id ?? '')}
      onChange={(event) => updateSelectedMarket('pricing_parameter_profile_id', event.target.value ? Number(event.target.value) : null)}
    >
      <option value="">Manual</option>
      {pricingProfiles.map((profile) => <option key={profile.id} value={profile.id}>{profile.name}</option>)}
    </select>
  </label>
  <input aria-label="Spot override" value={String(selected.market.spot ?? '')} onChange={(event) => updateSelectedMarket('spot', numberOrNull(event.target.value))} />
  <input aria-label="Volatility override" value={String(selected.market.volatility ?? '')} onChange={(event) => updateSelectedMarket('volatility', numberOrNull(event.target.value))} />
</section>
```

Implement `updateSelectedMarket` inside `TrySolve` by calling `onUpdateRow({ ...selected, market: { ...selected.market, [key]: value } })`.

- [ ] **Step 6: Load profile lists in live route**

In `TrySolve.live.tsx`, load:

- `/api/pricing-parameter-profiles`
- `/api/market-data/profiles`

Pass compact `id`/`name` arrays to `TrySolve`.

- [ ] **Step 7: Run focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_try_solve.py::test_resolve_try_solve_market_uses_profile_then_manual_overrides -q
npm test -- TrySolve
```

Expected: PASS.

- [ ] **Step 8: Commit market/form editing**

```bash
git add backend/app/services/try_solve.py tests/test_try_solve.py frontend/src/routes/TrySolve.live.tsx frontend/src/routes/TrySolve.tsx frontend/src/routes/TrySolve.css frontend/src/routes/TrySolve.test.tsx
git commit -m "feat(rfq): add try-solve market inputs"
```

---

### Task 9: Final Verification and Browser Check

**Files:**
- Modify only files needed for test fixes.

- [ ] **Step 1: Run backend focused suite**

Run: `.venv/bin/python -m pytest tests/test_try_solve.py -q`

Expected: PASS.

- [ ] **Step 2: Run related backend regression suites**

Run: `.venv/bin/python -m pytest tests/test_quant_services.py tests/test_position_import_pricing.py tests/test_api.py -q`

Expected: PASS. If unrelated pre-existing failures appear, capture the exact failing test names and error messages.

- [ ] **Step 3: Run frontend tests**

Run: `npm test -- TrySolve`

Expected: PASS.

- [ ] **Step 4: Build frontend**

Run: `npm run build`

Expected: PASS.

- [ ] **Step 5: Start dev server and inspect UI**

Run backend and frontend using the repo’s normal local commands. If a server is already running, reuse or choose the next available port.

Open the local app with Browser and verify:

- sidebar/nav includes `Try to Solve`,
- page header has `Import Excel` and `Export Excel` top-right,
- product library lists all 19 products,
- importing a multi-sheet workbook populates the request queue,
- selecting a queue row updates the editor,
- unsupported rows show solver-pending or unsupported status,
- vanilla solve returns a solved result.

- [ ] **Step 6: Final git status**

Run: `git status --short`

Expected: clean or only files intentionally left uncommitted by the user.

---

## Self-Review Notes

Spec coverage:

- All 19 products: Task 1.
- English UI labels with Excel aliases: Tasks 1 and 6.
- Manual entry and Excel import: Tasks 2, 6, and 7.
- Multi-row mixed-product import queue: Tasks 2, 6, and 7.
- Market profiles plus overrides: Task 8.
- Guarded QuantArk solving: Task 3.
- Excel export: Tasks 4 and 5.
- Dedicated API namespace: Task 5.
- Frontend route and workflow: Tasks 6 through 8.

The plan intentionally does not implement promotion into the persisted RFQ approval lifecycle, matching the approved non-goal.
