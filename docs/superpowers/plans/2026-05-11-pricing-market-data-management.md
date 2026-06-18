# Pricing And Market Data Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build global pricing parameter profile management with XLSX import, global AKShare market-data profiles, spot-only feed into pricing profiles, and repo-consistent frontend pages.

**Architecture:** Add global profile models/tables beside the existing portfolio and market-input models. Keep current portfolio pricing flows intact, but allow a valuation run to select a global pricing profile keyed by `source_trade_id`; explicit overrides keep highest precedence. Frontend pages use the same AppShell/PageHeader/Tile/Table/Modal route pattern already used by Positions, Risk, Reports, and Portfolios.

**Tech Stack:** FastAPI, SQLAlchemy ORM, Alembic, Pydantic, openpyxl, pandas/AKShare service, React, TypeScript, Vitest, Testing Library.

---

## File Structure

Backend persistence and schemas:

- Modify `backend/app/models.py`: add `PricingParameterProfile`, `PricingParameterRow`, `MarketDataProfile`; add `pricing_parameter_profile_id` to `PositionValuationRun`.
- Add `backend/alembic/versions/0005_global_pricing_market_profiles.py`: idempotent SQLite-safe migration.
- Modify `backend/app/schemas.py`: add profile request/response schemas and add `pricing_parameter_profile_id` to `PositionPriceRequest` and `PositionValuationRunOut`.

Backend services and API:

- Add `backend/app/services/pricing_profiles.py`: XLSX read/import, profile row lookup, AKShare spot-to-pricing-profile helper.
- Modify `backend/app/services/position_pricer.py`: accept `pricing_parameter_profile_id`, load profile rows by trade id, and resolve inputs with the approved precedence.
- Modify `backend/app/main.py`: expose profile APIs and pass profile id into pricing runs.
- Modify `backend/app/services/market_data.py`: keep fetch normalization as-is; profile persistence happens in `main.py` or a small helper in `pricing_profiles.py`.

Frontend:

- Modify `frontend/src/types.ts`: add pricing parameter and market data profile types; extend `Route` and `PositionValuationRun`.
- Modify `frontend/src/main.tsx`: add nav items, command palette items, and route rendering.
- Add `frontend/src/routes/PricingParameters.live.tsx`, `frontend/src/routes/PricingParameters.tsx`, `frontend/src/routes/PricingParameters.css`.
- Add `frontend/src/routes/MarketData.live.tsx`, `frontend/src/routes/MarketData.tsx`, `frontend/src/routes/MarketData.css`.
- Modify `frontend/src/routes/Positions.live.tsx` and `frontend/src/routes/Positions.tsx`: load pricing profiles and include selector values in pricing requests.

Tests:

- Modify `tests/test_position_import_pricing.py`: service tests for global pricing profiles and pricing precedence.
- Modify `tests/test_api.py`: API tests for profile import/list/detail, AKShare market-data profiles, spot-only feed, and selected-profile pricing.
- Add `frontend/src/routes/PricingParameters.live.test.tsx`.
- Add `frontend/src/routes/MarketData.live.test.tsx`.
- Modify `frontend/src/routes/Positions.live.test.tsx`: selector and request payload coverage.

---

### Task 1: Global Pricing Profile Persistence And XLSX Import

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/schemas.py`
- Add: `backend/alembic/versions/0005_global_pricing_market_profiles.py`
- Add: `backend/app/services/pricing_profiles.py`
- Test: `tests/test_position_import_pricing.py`

- [ ] **Step 1: Write failing service tests**

Append these tests and imports to `tests/test_position_import_pricing.py`.

```python
from app.models import PricingParameterProfile, PricingParameterRow
from app.services.pricing_profiles import (
    import_pricing_parameter_profile_from_xlsx,
    latest_pricing_rows_by_trade_id,
)
```

```python
def test_import_global_pricing_parameter_profile_from_xlsx(tmp_path: Path):
    market_path = tmp_path / "market-20260430.xlsx"
    write_market_workbook(market_path, ["T-VANILLA", "T-SECOND"], spot=101.0)
    session = configure_test_db(tmp_path)

    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="2026-04-30 Close",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    assert profile.name == "2026-04-30 Close"
    assert profile.valuation_date == datetime(2026, 4, 30)
    assert profile.source_type == "xlsx"
    assert profile.summary["row_count"] == 2
    assert profile.summary["duplicate_trade_ids"] == []

    rows = session.query(PricingParameterRow).filter_by(profile_id=profile.id).order_by(PricingParameterRow.source_trade_id).all()
    assert [row.source_trade_id for row in rows] == ["T-SECOND", "T-VANILLA"]
    assert rows[0].spot == 101.0
    assert rows[0].rate == 0.02
    assert rows[0].dividend_yield == 0.03
    assert rows[0].volatility == 0.22
    assert rows[0].source_payload["row_number"] == 3


def test_import_global_pricing_profile_reports_duplicate_trade_ids(tmp_path: Path):
    market_path = tmp_path / "market.xlsx"
    write_market_workbook(market_path, ["T-VANILLA", "T-VANILLA"], spot=101.0)
    session = configure_test_db(tmp_path)

    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Duplicate Check",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    assert profile.summary["row_count"] == 1
    assert profile.summary["duplicate_trade_ids"] == ["T-VANILLA"]
    rows = session.query(PricingParameterRow).filter_by(profile_id=profile.id).all()
    assert len(rows) == 1
    assert rows[0].source_row == 3


def test_latest_pricing_rows_by_trade_id_uses_profile_rows(tmp_path: Path):
    market_path = tmp_path / "market.xlsx"
    write_market_workbook(market_path, ["T-VANILLA"], spot=101.0)
    session = configure_test_db(tmp_path)
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Lookup Profile",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    rows = latest_pricing_rows_by_trade_id(session, profile_id=profile.id)

    assert set(rows) == {"T-VANILLA"}
    assert rows["T-VANILLA"].profile_id == profile.id
    assert rows["T-VANILLA"].spot == 101.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_position_import_pricing.py::test_import_global_pricing_parameter_profile_from_xlsx tests/test_position_import_pricing.py::test_import_global_pricing_profile_reports_duplicate_trade_ids tests/test_position_import_pricing.py::test_latest_pricing_rows_by_trade_id_uses_profile_rows -q
```

Expected: FAIL with import/model errors for `PricingParameterProfile`, `PricingParameterRow`, or `app.services.pricing_profiles`.

- [ ] **Step 3: Add ORM models**

In `backend/app/models.py`, add these relationships to `Portfolio` only if no relationship to global profiles is needed. Do not attach profiles to `Portfolio`; they are global. Add these classes after `MarketInputImportBatch` and add the `pricing_parameter_profile_id` column to `PositionValuationRun`.

```python
class PricingParameterProfile(Base):
    __tablename__ = "pricing_parameter_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    valuation_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_type: Mapped[str] = mapped_column(String(40), default="xlsx")
    source_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    rows: Mapped[list["PricingParameterRow"]] = relationship(
        back_populates="profile",
        cascade="all, delete-orphan",
        order_by="PricingParameterRow.source_trade_id",
    )


class PricingParameterRow(Base):
    __tablename__ = "pricing_parameter_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("pricing_parameter_profiles.id"), index=True)
    source_trade_id: Mapped[str] = mapped_column(String(160), index=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    spot: Mapped[float | None] = mapped_column(Float, nullable=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_row: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    profile: Mapped[PricingParameterProfile] = relationship(back_populates="rows")
```

In `PositionValuationRun`, add:

```python
    pricing_parameter_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("pricing_parameter_profiles.id"),
        nullable=True,
        index=True,
    )
    pricing_parameter_profile: Mapped["PricingParameterProfile | None"] = relationship()
```

- [ ] **Step 4: Add Alembic migration**

Create `backend/alembic/versions/0005_global_pricing_market_profiles.py`.

```python
"""global pricing and market data profiles

Revision ID: 0005_global_pricing_market_profiles
Revises: 0004_portfolio_kind_and_membership
Create Date: 2026-05-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0005_global_pricing_market_profiles"
down_revision = "0004_portfolio_kind_and_membership"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    insp = inspect(op.get_bind())
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    tables = _tables()
    if "pricing_parameter_profiles" not in tables:
        op.create_table(
            "pricing_parameter_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("source_type", sa.String(length=40), nullable=False, server_default="xlsx"),
            sa.Column("source_path", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="completed"),
            sa.Column("summary", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_pricing_parameter_profiles_valuation_date", "pricing_parameter_profiles", ["valuation_date"])

    tables = _tables()
    if "pricing_parameter_rows" not in tables:
        op.create_table(
            "pricing_parameter_rows",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("profile_id", sa.Integer(), sa.ForeignKey("pricing_parameter_profiles.id"), nullable=False),
            sa.Column("source_trade_id", sa.String(length=160), nullable=False),
            sa.Column("symbol", sa.String(length=80), nullable=False),
            sa.Column("spot", sa.Float(), nullable=True),
            sa.Column("rate", sa.Float(), nullable=True),
            sa.Column("dividend_yield", sa.Float(), nullable=True),
            sa.Column("volatility", sa.Float(), nullable=True),
            sa.Column("source_row", sa.Integer(), nullable=True),
            sa.Column("source_payload", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_pricing_parameter_rows_profile_id", "pricing_parameter_rows", ["profile_id"])
        op.create_index("ix_pricing_parameter_rows_source_trade_id", "pricing_parameter_rows", ["source_trade_id"])
        op.create_index("ix_pricing_parameter_rows_symbol", "pricing_parameter_rows", ["symbol"])

    if "pricing_parameter_profile_id" not in _columns("position_valuation_runs"):
        op.add_column("position_valuation_runs", sa.Column("pricing_parameter_profile_id", sa.Integer(), nullable=True))
        op.create_index("ix_position_valuation_runs_pricing_parameter_profile_id", "position_valuation_runs", ["pricing_parameter_profile_id"])


def downgrade() -> None:
    if "position_valuation_runs" in _tables() and "pricing_parameter_profile_id" in _columns("position_valuation_runs"):
        op.drop_index("ix_position_valuation_runs_pricing_parameter_profile_id", table_name="position_valuation_runs")
        op.drop_column("position_valuation_runs", "pricing_parameter_profile_id")
    if "pricing_parameter_rows" in _tables():
        op.drop_index("ix_pricing_parameter_rows_symbol", table_name="pricing_parameter_rows")
        op.drop_index("ix_pricing_parameter_rows_source_trade_id", table_name="pricing_parameter_rows")
        op.drop_index("ix_pricing_parameter_rows_profile_id", table_name="pricing_parameter_rows")
        op.drop_table("pricing_parameter_rows")
    if "pricing_parameter_profiles" in _tables():
        op.drop_index("ix_pricing_parameter_profiles_valuation_date", table_name="pricing_parameter_profiles")
        op.drop_table("pricing_parameter_profiles")
```

- [ ] **Step 5: Add schemas**

In `backend/app/schemas.py`, add:

```python
class PricingParameterRowOut(BaseModel):
    id: int
    profile_id: int
    source_trade_id: str
    symbol: str
    spot: float | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    source_row: int | None = None
    source_payload: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PricingParameterProfileOut(BaseModel):
    id: int
    name: str
    valuation_date: datetime
    source_type: str
    source_path: str | None = None
    status: str
    summary: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    rows: list[PricingParameterRowOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}
```

- [ ] **Step 6: Implement pricing profile import service**

Create `backend/app/services/pricing_profiles.py`.

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, selectinload

from ..models import PricingParameterProfile, PricingParameterRow
from .position_adapter import make_json_safe
from .position_pricer import infer_valuation_date, read_market_rows


def import_pricing_parameter_profile_from_xlsx(
    session: Session,
    *,
    xlsx_path: str | Path,
    name: str | None = None,
    valuation_date: datetime | None = None,
    sheet_name: str | None = None,
) -> PricingParameterProfile:
    path = Path(xlsx_path)
    effective_date = valuation_date or infer_valuation_date(path) or datetime.utcnow()
    rows_by_trade_id = read_market_rows(path, sheet_name=sheet_name)
    seen: set[str] = set()
    duplicates: list[str] = []
    for trade_id in rows_by_trade_id:
        if trade_id in seen and trade_id not in duplicates:
            duplicates.append(trade_id)
        seen.add(trade_id)

    profile = PricingParameterProfile(
        name=name or f"Pricing Parameters {effective_date:%Y-%m-%d}",
        valuation_date=effective_date,
        source_type="xlsx",
        source_path=str(path),
        status="completed",
        summary={
            "row_count": len(rows_by_trade_id),
            "duplicate_trade_ids": sorted(duplicates),
            "sheet_name": sheet_name,
        },
    )
    session.add(profile)
    session.flush()

    for trade_id, row in rows_by_trade_id.items():
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=trade_id,
                symbol=row["symbol"],
                spot=row.get("spot"),
                rate=row.get("rate"),
                dividend_yield=row.get("dividend_yield"),
                volatility=row.get("volatility"),
                source_row=row.get("source_row"),
                source_payload={"row_number": row.get("source_row"), "row": make_json_safe(row.get("raw", {}))},
            )
        )
    session.flush()
    return profile


def latest_pricing_rows_by_trade_id(session: Session, *, profile_id: int) -> dict[str, PricingParameterRow]:
    profile = (
        session.query(PricingParameterProfile)
        .options(selectinload(PricingParameterProfile.rows))
        .filter(PricingParameterProfile.id == profile_id)
        .one_or_none()
    )
    if profile is None:
        raise ValueError(f"Pricing parameter profile not found: {profile_id}")
    return {row.source_trade_id: row for row in profile.rows}
```

- [ ] **Step 7: Fix duplicate detection**

The service above cannot detect duplicates after `read_market_rows()` collapses duplicate keys. Replace `read_market_rows()` with a helper that returns both collapsed rows and duplicates.

In `backend/app/services/position_pricer.py`, refactor `read_market_rows()` to call a private reader:

```python
def read_market_rows_with_diagnostics(path: Path, *, sheet_name: str | None = None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    workbook = load_workbook(path, data_only=True, read_only=True)
    worksheet = workbook[sheet_name] if sheet_name else workbook[workbook.sheetnames[0]]
    header_values = next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True))
    headers = [str(value).strip() if value is not None else "" for value in header_values]
    required = {"交易编号", "标的物代码", "波动率", "无风险利率", "分红/融券率"}
    missing = sorted(required - set(headers))
    if missing:
        raise ValueError(f"Missing required market headers: {', '.join(missing)}")

    rows: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for source_row, values in enumerate(worksheet.iter_rows(min_row=2, values_only=True), start=2):
        row = {headers[index]: value for index, value in enumerate(values) if index < len(headers) and headers[index]}
        trade_id = text_value(row.get("交易编号"))
        if not trade_id:
            continue
        if trade_id in rows and trade_id not in duplicates:
            duplicates.append(trade_id)
        rows[trade_id] = {
            "trade_id": trade_id,
            "symbol": normalize_symbol(row.get("标的物代码")),
            "volatility": parse_number(row.get("波动率"), None),
            "rate": parse_number(row.get("无风险利率"), None),
            "dividend_yield": parse_number(row.get("分红/融券率"), None),
            "spot": parse_number(row.get("标的物价格"), None),
            "source_row": source_row,
            "raw": row,
        }
    return rows, sorted(duplicates)


def read_market_rows(path: Path, *, sheet_name: str | None = None) -> dict[str, dict[str, Any]]:
    rows, _duplicates = read_market_rows_with_diagnostics(path, sheet_name=sheet_name)
    return rows
```

Then update `pricing_profiles.py`:

```python
from .position_pricer import infer_valuation_date, read_market_rows_with_diagnostics
```

```python
    rows_by_trade_id, duplicates = read_market_rows_with_diagnostics(path, sheet_name=sheet_name)
```

Remove the local `seen` loop.

- [ ] **Step 8: Run service tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_position_import_pricing.py::test_import_global_pricing_parameter_profile_from_xlsx tests/test_position_import_pricing.py::test_import_global_pricing_profile_reports_duplicate_trade_ids tests/test_position_import_pricing.py::test_latest_pricing_rows_by_trade_id_uses_profile_rows -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

```bash
git add backend/app/models.py backend/app/schemas.py backend/alembic/versions/0005_global_pricing_market_profiles.py backend/app/services/pricing_profiles.py tests/test_position_import_pricing.py
git commit -m "feat(pricing): add global pricing parameter profiles"
```

---

### Task 2: Pricing API And Valuation Resolution

**Files:**
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/position_pricer.py`
- Test: `tests/test_api.py`
- Test: `tests/test_position_import_pricing.py`

- [ ] **Step 1: Write failing API and pricing precedence tests**

Append to `tests/test_api.py`:

```python
def test_pricing_parameter_profile_import_list_detail_and_pricing(tmp_path: Path):
    client = make_client(tmp_path)
    trade_path = tmp_path / "trades.xlsx"
    market_path = tmp_path / "market.xlsx"
    write_trade_workbook(trade_path, [vanilla_row()])
    write_market_workbook(market_path, ["T-VANILLA"], spot=102.0)

    portfolio = client.post("/api/portfolios", json={"name": "Profile Priced Book"}).json()
    with trade_path.open("rb") as handle:
        imported_positions = client.post(
            f"/api/portfolios/{portfolio['id']}/positions/import",
            files={"file": ("trades.xlsx", handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"sheet_name": "汇总"},
        )
    assert imported_positions.status_code == 200

    with market_path.open("rb") as handle:
        imported_profile = client.post(
            "/api/pricing-parameter-profiles/import",
            files={"file": ("market.xlsx", handle, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            data={"valuation_date": "2026-04-30", "name": "2026-04-30 Close"},
        )
    assert imported_profile.status_code == 200
    profile_payload = imported_profile.json()
    assert profile_payload["name"] == "2026-04-30 Close"
    assert profile_payload["summary"]["row_count"] == 1
    assert profile_payload["rows"][0]["source_trade_id"] == "T-VANILLA"

    listed = client.get("/api/pricing-parameter-profiles")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == profile_payload["id"]
    detail = client.get(f"/api/pricing-parameter-profiles/{profile_payload['id']}")
    assert detail.status_code == 200
    assert detail.json()["rows"][0]["spot"] == 102.0

    priced = client.post(
        f"/api/portfolios/{portfolio['id']}/positions/price",
        json={
            "valuation_date": "2026-04-30T00:00:00",
            "pricing_parameter_profile_id": profile_payload["id"],
        },
    )
    assert priced.status_code == 200
    price_payload = priced.json()
    assert price_payload["pricing_parameter_profile_id"] == profile_payload["id"]
    assert price_payload["overrides"]["pricing_parameter_profile_id"] == profile_payload["id"]
    assert price_payload["results"][0]["market_inputs"]["spot"] == 102.0
    assert price_payload["results"][0]["market_inputs"]["market_input_source"] == "pricing_parameter_profile"
```

Append to `tests/test_position_import_pricing.py`:

```python
def test_pricing_profile_precedence_between_profile_and_overrides(tmp_path: Path):
    trade_path = tmp_path / "trades.xlsx"
    market_path = tmp_path / "market.xlsx"
    write_trade_workbook(trade_path, [vanilla_row()])
    write_market_workbook(market_path, ["T-VANILLA"], spot=102.0)
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Profile Override Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=trade_path)
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Profile Inputs",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        pricing_parameter_profile_id=profile.id,
        overrides=MarketOverrides(spot=103.0),
        spot_fetcher=lambda symbol, valuation_date: (_ for _ in ()).throw(AssertionError("fallback should not run")),
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(valuation_run_id=run.id).one()
    assert run.pricing_parameter_profile_id == profile.id
    assert run.overrides["pricing_parameter_profile_id"] == profile.id
    assert result.market_inputs["spot"] == 103.0
    assert result.market_inputs["rate"] == 0.02
    assert result.market_inputs["market_input_source"] == "pricing_parameter_profile"
    assert result.market_inputs["spot_metadata"]["source"] == "override"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py::test_pricing_parameter_profile_import_list_detail_and_pricing tests/test_position_import_pricing.py::test_pricing_profile_precedence_between_profile_and_overrides -q
```

Expected: FAIL because endpoints and pricing request fields do not exist yet.

- [ ] **Step 3: Extend schemas**

In `backend/app/schemas.py`, update `PositionPriceRequest`:

```python
    pricing_parameter_profile_id: int | None = None
```

Update `PositionValuationRunOut`:

```python
    pricing_parameter_profile_id: int | None = None
```

- [ ] **Step 4: Include profile id in valuation output**

In `backend/app/main.py`, update `_valuation_run_out()`:

```python
        pricing_parameter_profile_id=run.pricing_parameter_profile_id,
```

- [ ] **Step 5: Add profile API endpoints**

In `backend/app/main.py`, import models/schemas/services:

```python
    PricingParameterProfile,
    PricingParameterRow,
```

```python
    PricingParameterProfileOut,
```

```python
from .services.pricing_profiles import import_pricing_parameter_profile_from_xlsx
```

Inside `create_app()`, add:

```python
    @app.get("/api/pricing-parameter-profiles", response_model=list[PricingParameterProfileOut])
    def list_pricing_parameter_profiles(session: Session = Depends(get_db)):
        return (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .order_by(PricingParameterProfile.valuation_date.desc(), PricingParameterProfile.created_at.desc())
            .all()
        )

    @app.get("/api/pricing-parameter-profiles/{profile_id}", response_model=PricingParameterProfileOut)
    def get_pricing_parameter_profile(profile_id: int, session: Session = Depends(get_db)):
        profile = (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .filter(PricingParameterProfile.id == profile_id)
            .one_or_none()
        )
        if profile is None:
            raise HTTPException(status_code=404, detail="Pricing parameter profile not found")
        return profile

    @app.post("/api/pricing-parameter-profiles/import", response_model=PricingParameterProfileOut)
    def import_pricing_parameter_profile(
        file: UploadFile = File(...),
        sheet_name: str | None = Form(None),
        valuation_date: str | None = Form(None),
        name: str | None = Form(None),
        session: Session = Depends(get_db),
    ):
        upload_path = _store_upload(file, "pricing-parameters")
        try:
            profile = import_pricing_parameter_profile_from_xlsx(
                session,
                xlsx_path=upload_path,
                sheet_name=sheet_name,
                valuation_date=_parse_optional_datetime(valuation_date),
                name=name,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="pricing_parameters.imported",
            actor="desk_user",
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"source_path": str(upload_path), "summary": profile.summary},
        )
        session.commit()
        persisted = (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .filter(PricingParameterProfile.id == profile.id)
            .one()
        )
        return persisted
```

- [ ] **Step 6: Thread profile id into the pricing endpoint**

In `backend/app/main.py`, update `price_portfolio_positions_endpoint()`:

```python
                pricing_parameter_profile_id=payload.pricing_parameter_profile_id,
```

- [ ] **Step 7: Modify pricing service signature and row loading**

In `backend/app/services/position_pricer.py`, import:

```python
from ..models import PricingParameterRow
from .pricing_profiles import latest_pricing_rows_by_trade_id
```

This import creates a cycle if `pricing_profiles.py` imports `read_market_rows_with_diagnostics` from `position_pricer.py`. If a cycle appears, move `infer_valuation_date`, `read_market_rows`, and `read_market_rows_with_diagnostics` into `backend/app/services/market_input_workbooks.py`, then import them from both services.

Update `price_portfolio_positions()` signature:

```python
    pricing_parameter_profile_id: int | None = None,
```

Before creating the run:

```python
    pricing_rows = (
        latest_pricing_rows_by_trade_id(session, profile_id=pricing_parameter_profile_id)
        if pricing_parameter_profile_id is not None
        else {}
    )
```

Add to `run_overrides`:

```python
    if pricing_parameter_profile_id is not None:
        run_overrides["pricing_parameter_profile_id"] = pricing_parameter_profile_id
```

Add to `PositionValuationRun(...)`:

```python
        pricing_parameter_profile_id=pricing_parameter_profile_id,
```

Pass rows into `_price_position()`:

```python
            pricing_rows=pricing_rows,
```

- [ ] **Step 8: Modify `_price_position()` to use profile rows**

Update signature:

```python
    pricing_rows: dict[str, PricingParameterRow],
```

Replace:

```python
    market_input = market_inputs.get(trade_id or "")
    if market_input is None:
        return _failed(position, f"Missing persisted market inputs for trade_id={trade_id}", "market", engine_name=resolved_engine_name)
```

with:

```python
    pricing_row = pricing_rows.get(trade_id or "")
    market_input = market_inputs.get(trade_id or "")
    if pricing_row is None and market_input is None:
        return _failed(position, f"Missing pricing parameters for trade_id={trade_id}", "market", engine_name=resolved_engine_name)
```

Replace the market input resolution block with:

```python
    symbol = (
        pricing_row.symbol
        if pricing_row is not None and pricing_row.symbol
        else market_input.symbol
        if market_input is not None and market_input.symbol
        else position.underlying
    )
    if overrides.spot is not None:
        spot = overrides.spot
        spot_meta = {"source": "override"}
    elif pricing_row is not None and pricing_row.spot is not None:
        spot = pricing_row.spot
        spot_meta = {"source": "pricing_parameter_profile", "pricing_parameter_row_id": pricing_row.id, "profile_id": pricing_row.profile_id}
    elif market_input is not None and market_input.spot is not None:
        spot = market_input.spot
        spot_meta = {"source": "position_market_inputs", "market_input_id": market_input.id}
    else:
        if symbol not in symbol_spot_cache:
            symbol_spot_cache[symbol] = spot_fetcher(symbol, valuation_date)
        spot, spot_meta = symbol_spot_cache[symbol]

    rate = (
        overrides.rate
        if overrides.rate is not None
        else pricing_row.rate
        if pricing_row is not None and pricing_row.rate is not None
        else market_input.rate
        if market_input is not None
        else None
    )
    dividend_yield = (
        overrides.dividend_yield
        if overrides.dividend_yield is not None
        else pricing_row.dividend_yield
        if pricing_row is not None and pricing_row.dividend_yield is not None
        else market_input.dividend_yield
        if market_input is not None
        else None
    )
    volatility = (
        overrides.volatility
        if overrides.volatility is not None
        else pricing_row.volatility
        if pricing_row is not None and pricing_row.volatility is not None
        else market_input.volatility
        if market_input is not None
        else None
    )
```

In `resolved_market_inputs`, replace `market_input_id` with nullable source fields:

```python
        "market_input_source": "pricing_parameter_profile" if pricing_row is not None else "position_market_inputs",
        "pricing_parameter_profile_id": pricing_row.profile_id if pricing_row is not None else None,
        "pricing_parameter_row_id": pricing_row.id if pricing_row is not None else None,
        "market_input_id": market_input.id if market_input is not None else None,
```

- [ ] **Step 9: Run backend tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py::test_pricing_parameter_profile_import_list_detail_and_pricing tests/test_position_import_pricing.py::test_pricing_profile_precedence_between_profile_and_overrides tests/test_api.py::test_position_and_market_upload_then_batch_price -q
```

Expected: PASS.

- [ ] **Step 10: Commit Task 2**

```bash
git add backend/app/main.py backend/app/schemas.py backend/app/services/position_pricer.py backend/app/services/pricing_profiles.py tests/test_api.py tests/test_position_import_pricing.py
git commit -m "feat(pricing): price portfolios with global parameter profiles"
```

---

### Task 3: AKShare Market Data Profiles And Spot-Only Feed

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/alembic/versions/0005_global_pricing_market_profiles.py`
- Modify: `backend/app/schemas.py`
- Modify: `backend/app/main.py`
- Modify: `backend/app/services/pricing_profiles.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

Append to `tests/test_api.py`:

```python
def test_akshare_market_data_profile_list_detail_and_spot_feed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = make_client(tmp_path)

    def fake_snapshot(payload):
        from app.schemas import MarketDataSnapshot
        return MarketDataSnapshot(
            name=payload.name or "000300 fake",
            source="akshare",
            symbol=payload.symbol,
            asset_class=payload.asset_class,
            data={
                "rows": [{"date": "2026-04-30", "open": 100.0, "high": 103.0, "low": 99.0, "close": 102.5, "volume": 1000}],
                "latest": {"date": "2026-04-30", "close": 102.5},
                "spot": 102.5,
            },
            source_metadata={"source_name": "fake", "fallback": False},
        )

    monkeypatch.setattr(app_main_module, "fetch_akshare_snapshot", fake_snapshot)

    created = client.post(
        "/api/market-data/profiles/akshare",
        json={
            "symbol": "000300",
            "asset_class": "index",
            "start_date": "2026-04-01",
            "end_date": "2026-04-30",
            "name": "CSI300 April",
            "adjust": "qfq",
        },
    )
    assert created.status_code == 200
    profile = created.json()
    assert profile["name"] == "CSI300 April"
    assert profile["data"]["spot"] == 102.5
    assert profile["source_metadata"]["fallback"] is False

    listed = client.get("/api/market-data/profiles")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == profile["id"]

    detail = client.get(f"/api/market-data/profiles/{profile['id']}")
    assert detail.status_code == 200
    assert detail.json()["data"]["rows"][0]["close"] == 102.5

    spot_profile = client.post(
        f"/api/market-data/profiles/{profile['id']}/create-spot-pricing-profile",
        json={"name": "CSI300 spot profile", "valuation_date": "2026-04-30T00:00:00", "source_trade_ids": ["T-VANILLA"]},
    )
    assert spot_profile.status_code == 200
    rows = spot_profile.json()["rows"]
    assert rows[0]["source_trade_id"] == "T-VANILLA"
    assert rows[0]["symbol"] == "000300"
    assert rows[0]["spot"] == 102.5
    assert rows[0]["rate"] is None
    assert rows[0]["dividend_yield"] is None
    assert rows[0]["volatility"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py::test_akshare_market_data_profile_list_detail_and_spot_feed -q
```

Expected: FAIL because `MarketDataProfile` and new endpoints do not exist.

- [ ] **Step 3: Add market data model**

In `backend/app/models.py`, add after `MarketSnapshot`:

```python
class MarketDataProfile(Base):
    __tablename__ = "market_data_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    source: Mapped[str] = mapped_column(String(80), default="akshare")
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    asset_class: Mapped[str] = mapped_column(String(40), default="index")
    start_date: Mapped[str] = mapped_column(String(20))
    end_date: Mapped[str] = mapped_column(String(20))
    adjust: Mapped[str] = mapped_column(String(20), default="qfq")
    valuation_date: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
```

- [ ] **Step 4: Extend migration**

In `0005_global_pricing_market_profiles.py`, inside `upgrade()` after pricing tables:

```python
    tables = _tables()
    if "market_data_profiles" not in tables:
        op.create_table(
            "market_data_profiles",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=160), nullable=False),
            sa.Column("source", sa.String(length=80), nullable=False, server_default="akshare"),
            sa.Column("symbol", sa.String(length=80), nullable=False),
            sa.Column("asset_class", sa.String(length=40), nullable=False, server_default="index"),
            sa.Column("start_date", sa.String(length=20), nullable=False),
            sa.Column("end_date", sa.String(length=20), nullable=False),
            sa.Column("adjust", sa.String(length=20), nullable=False, server_default="qfq"),
            sa.Column("valuation_date", sa.DateTime(), nullable=False),
            sa.Column("data", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("source_metadata", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_market_data_profiles_symbol", "market_data_profiles", ["symbol"])
```

In `downgrade()` before dropping pricing tables:

```python
    if "market_data_profiles" in _tables():
        op.drop_index("ix_market_data_profiles_symbol", table_name="market_data_profiles")
        op.drop_table("market_data_profiles")
```

- [ ] **Step 5: Add market schemas**

In `backend/app/schemas.py`, add:

```python
class MarketDataProfileOut(BaseModel):
    id: int
    name: str
    source: str
    symbol: str
    asset_class: str
    start_date: str
    end_date: str
    adjust: str
    valuation_date: datetime
    data: dict[str, Any]
    source_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SpotPricingProfileCreate(BaseModel):
    name: str | None = None
    valuation_date: datetime | None = None
    source_trade_ids: list[str] = Field(default_factory=list)
```

- [ ] **Step 6: Add spot-only profile helper**

In `backend/app/services/pricing_profiles.py`, add:

```python
from ..models import MarketDataProfile
```

```python
def create_spot_pricing_profile_from_market_data(
    session: Session,
    *,
    market_data_profile_id: int,
    source_trade_ids: list[str],
    name: str | None = None,
    valuation_date: datetime | None = None,
) -> PricingParameterProfile:
    market_profile = session.get(MarketDataProfile, market_data_profile_id)
    if market_profile is None:
        raise ValueError(f"Market data profile not found: {market_data_profile_id}")
    spot = (market_profile.data or {}).get("spot")
    if spot is None:
        raise ValueError("Market data profile has no latest close spot")
    effective_date = valuation_date or market_profile.valuation_date
    profile = PricingParameterProfile(
        name=name or f"{market_profile.symbol} spot {effective_date:%Y-%m-%d}",
        valuation_date=effective_date,
        source_type="market_data_spot",
        source_path=None,
        status="completed",
        summary={
            "row_count": len(source_trade_ids),
            "market_data_profile_id": market_profile.id,
            "spot_only": True,
        },
    )
    session.add(profile)
    session.flush()
    for trade_id in source_trade_ids:
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=trade_id,
                symbol=market_profile.symbol,
                spot=float(spot),
                rate=None,
                dividend_yield=None,
                volatility=None,
                source_row=None,
                source_payload={"market_data_profile_id": market_profile.id, "latest": (market_profile.data or {}).get("latest")},
            )
        )
    session.flush()
    return profile
```

- [ ] **Step 7: Add market profile endpoints**

In `backend/app/main.py`, import:

```python
    MarketDataProfile,
```

```python
    MarketDataProfileOut,
    SpotPricingProfileCreate,
```

```python
from .services.pricing_profiles import create_spot_pricing_profile_from_market_data
```

Add endpoints:

```python
    @app.get("/api/market-data/profiles", response_model=list[MarketDataProfileOut])
    def list_market_data_profiles(session: Session = Depends(get_db)):
        return (
            session.query(MarketDataProfile)
            .order_by(MarketDataProfile.valuation_date.desc(), MarketDataProfile.created_at.desc())
            .all()
        )

    @app.get("/api/market-data/profiles/{profile_id}", response_model=MarketDataProfileOut)
    def get_market_data_profile(profile_id: int, session: Session = Depends(get_db)):
        profile = session.get(MarketDataProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Market data profile not found")
        return profile

    @app.post("/api/market-data/profiles/akshare", response_model=MarketDataProfileOut)
    def create_akshare_market_data_profile(payload: AkshareSnapshotRequest, session: Session = Depends(get_db)):
        normalized = fetch_akshare_snapshot(payload)
        profile = MarketDataProfile(
            name=normalized.name,
            source="akshare",
            symbol=normalized.symbol,
            asset_class=normalized.asset_class,
            start_date=payload.start_date,
            end_date=payload.end_date,
            adjust=payload.adjust,
            valuation_date=normalized.valuation_date,
            data=normalized.data,
            source_metadata=normalized.source_metadata,
        )
        session.add(profile)
        session.flush()
        record_audit(
            session,
            event_type="market_data.profile.akshare",
            actor="desk_user",
            subject_type="market_data_profile",
            subject_id=profile.id,
            payload={"symbol": profile.symbol, "source_metadata": profile.source_metadata},
        )
        session.commit()
        return profile

    @app.post(
        "/api/market-data/profiles/{profile_id}/create-spot-pricing-profile",
        response_model=PricingParameterProfileOut,
    )
    def create_spot_pricing_profile(
        profile_id: int,
        payload: SpotPricingProfileCreate,
        session: Session = Depends(get_db),
    ):
        try:
            profile = create_spot_pricing_profile_from_market_data(
                session,
                market_data_profile_id=profile_id,
                source_trade_ids=payload.source_trade_ids,
                name=payload.name,
                valuation_date=payload.valuation_date,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="pricing_parameters.spot_profile_created",
            actor="desk_user",
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"market_data_profile_id": profile_id, "row_count": len(payload.source_trade_ids)},
        )
        session.commit()
        return (
            session.query(PricingParameterProfile)
            .options(selectinload(PricingParameterProfile.rows))
            .filter(PricingParameterProfile.id == profile.id)
            .one()
        )
```

- [ ] **Step 8: Run market profile tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py::test_akshare_market_data_profile_list_detail_and_spot_feed tests/test_api.py::test_manual_and_akshare_fallback_snapshots -q
```

Expected: PASS.

- [ ] **Step 9: Commit Task 3**

```bash
git add backend/app/models.py backend/alembic/versions/0005_global_pricing_market_profiles.py backend/app/schemas.py backend/app/main.py backend/app/services/pricing_profiles.py tests/test_api.py
git commit -m "feat(market-data): persist AKShare profiles and spot feeds"
```

---

### Task 4: Frontend Types, Navigation, And API Contracts

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/main.tsx`
- Test: existing typecheck/build commands

- [ ] **Step 1: Add frontend types**

In `frontend/src/types.ts`, extend `Route`:

```ts
export type Route =
  | 'chat'
  | 'rfq'
  | 'positions'
  | 'pricing-parameters'
  | 'market-data'
  | 'portfolios'
  | 'risk'
  | 'reports'
  | 'client';
```

Add:

```ts
export type PricingParameterRow = {
  id: number;
  profile_id: number;
  source_trade_id: string;
  symbol: string;
  spot?: number | null;
  rate?: number | null;
  dividend_yield?: number | null;
  volatility?: number | null;
  source_row?: number | null;
  source_payload?: Record<string, any> | null;
  created_at: string;
  updated_at: string;
};

export type PricingParameterProfile = {
  id: number;
  name: string;
  valuation_date: string;
  source_type: string;
  source_path?: string | null;
  status: string;
  summary: Record<string, any>;
  created_at: string;
  updated_at: string;
  rows: PricingParameterRow[];
};

export type MarketDataProfile = {
  id: number;
  name: string;
  source: string;
  symbol: string;
  asset_class: string;
  start_date: string;
  end_date: string;
  adjust: string;
  valuation_date: string;
  data: Record<string, any>;
  source_metadata: Record<string, any>;
  created_at: string;
  updated_at: string;
};
```

Extend `PositionValuationRun`:

```ts
  pricing_parameter_profile_id?: number | null;
```

- [ ] **Step 2: Add route wiring after the route files exist**

In `frontend/src/main.tsx`, make these edits in the same commit that creates the route components in Tasks 5 and 6. The final import block should include:

```ts
import { PricingParametersLive } from './routes/PricingParameters.live';
import { MarketDataLive } from './routes/MarketData.live';
```

Add nav items:

```ts
  { route: 'pricing-parameters' as const, label: 'Pricing Parameters' },
  { route: 'market-data' as const, label: 'Market Data' },
```

Add command items:

```ts
    { id: 'jump-pricing-parameters', group: 'Jump To', label: 'Pricing Parameters', shortcut: '↵' },
    { id: 'jump-market-data',        group: 'Jump To', label: 'Market Data',         shortcut: '↵' },
```

Add route rendering:

```tsx
        {route === 'pricing-parameters' && <PricingParametersLive />}
        {route === 'market-data' && <MarketDataLive />}
```

- [ ] **Step 3: Run frontend typecheck after Tasks 5 and 6**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/components/Sidebar.test.tsx
```

Expected after route files exist: PASS.

- [ ] **Step 4: Commit with Task 5 or Task 6**

Do not commit Task 4 alone unless route components already exist; otherwise TypeScript imports will be broken. Include these edits with the first frontend route commit.

---

### Task 5: Pricing Parameters Page

**Files:**
- Add: `frontend/src/routes/PricingParameters.live.tsx`
- Add: `frontend/src/routes/PricingParameters.tsx`
- Add: `frontend/src/routes/PricingParameters.css`
- Add: `frontend/src/routes/PricingParameters.live.test.tsx`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/types.ts`

- [ ] **Step 1: Write failing frontend tests**

Create `frontend/src/routes/PricingParameters.live.test.tsx`.

```tsx
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PricingParametersLive } from './PricingParameters.live';

const profile = {
  id: 7,
  name: '2026-04-30 Close',
  valuation_date: '2026-04-30T00:00:00',
  source_type: 'xlsx',
  source_path: '/tmp/market.xlsx',
  status: 'completed',
  summary: { row_count: 1, duplicate_trade_ids: [], error_count: 0 },
  created_at: '2026-05-11T00:00:00',
  updated_at: '2026-05-11T00:00:00',
  rows: [{
    id: 70,
    profile_id: 7,
    source_trade_id: 'T-VANILLA',
    symbol: '000852.SH',
    spot: 101,
    rate: 0.02,
    dividend_yield: 0.03,
    volatility: 0.22,
    source_row: 2,
    source_payload: {},
    created_at: '2026-05-11T00:00:00',
    updated_at: '2026-05-11T00:00:00',
  }],
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('PricingParametersLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders profiles and row table in the repo management style', async () => {
    globalThis.fetch = vi.fn(async () => response([profile])) as unknown as typeof fetch;
    render(<PricingParametersLive />);

    await waitFor(() => expect(screen.getByText('PRICING PARAMETERS')).toBeInTheDocument());
    expect(screen.getByText('2026-04-30 Close')).toBeInTheDocument();
    expect(screen.getByText('T-VANILLA')).toBeInTheDocument();
    expect(screen.getByText('000852.SH')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /import xlsx/i })).toBeInTheDocument();
  });

  it('submits the import modal as multipart form data', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/pricing-parameter-profiles') return response([]);
      if (url === '/api/pricing-parameter-profiles/import' && init?.method === 'POST') return response(profile);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<PricingParametersLive />);

    await waitFor(() => expect(screen.getByText('PRICING PARAMETERS')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /import xlsx/i }));
    await userEvent.type(screen.getByLabelText('Profile name'), '2026-04-30 Close');
    await userEvent.type(screen.getByLabelText('Valuation date'), '2026-04-30');
    const file = new File(['xlsx'], 'market.xlsx', { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
    await userEvent.upload(screen.getByLabelText('Pricing parameters xlsx'), file);
    await userEvent.click(screen.getByRole('button', { name: /^import$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/pricing-parameter-profiles/import', expect.objectContaining({ method: 'POST' })));
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/PricingParameters.live.test.tsx
```

Expected: FAIL because route files do not exist.

- [ ] **Step 3: Add presentational component**

Create `frontend/src/routes/PricingParameters.tsx` using existing components.

```tsx
import { useMemo, useRef, useState, type ChangeEvent, type FormEvent } from 'react';
import { Upload } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { PageHeader } from '../components/PageHeader';
import { Table, type Column } from '../components/Table';
import { Tile } from '../components/Tile';
import type { PricingParameterProfile, PricingParameterRow } from '../types';
import './PricingParameters.css';

type Props = {
  profiles: PricingParameterProfile[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  importing: boolean;
  feedback: string | null;
  onSelectProfile: (id: number) => void;
  onImport: (payload: { file: File; name: string; valuationDate: string; sheetName: string }) => void;
};

export function PricingParameters({
  profiles,
  selectedId,
  loading,
  error,
  importing,
  feedback,
  onSelectProfile,
  onImport,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const [name, setName] = useState('');
  const [valuationDate, setValuationDate] = useState('');
  const [sheetName, setSheetName] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const selected = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? profiles[0] ?? null,
    [profiles, selectedId],
  );
  const rows = selected?.rows ?? [];
  const columns: Column<PricingParameterRow>[] = [
    { key: 'source_trade_id', header: 'TRADE', width: '1.6fr' },
    { key: 'symbol', header: 'SYMBOL', width: '1fr' },
    { key: 'spot', header: 'SPOT', numeric: true, render: (row) => fmt(row.spot) },
    { key: 'volatility', header: 'VOL', numeric: true, render: (row) => fmt(row.volatility) },
    { key: 'rate', header: 'RATE', numeric: true, render: (row) => fmt(row.rate) },
    { key: 'dividend_yield', header: 'DIV', numeric: true, render: (row) => fmt(row.dividend_yield) },
    { key: 'source_row', header: 'ROW', numeric: true, render: (row) => row.source_row ?? '-' },
  ];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!file) return;
    onImport({ file, name, valuationDate, sheetName });
    setModalOpen(false);
    setFile(null);
    setName('');
    setValuationDate('');
    setSheetName('');
    if (fileRef.current) fileRef.current.value = '';
  };

  const onFile = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.currentTarget.files?.[0] ?? null);
  };

  return (
    <>
      <PageHeader
        title="PRICING PARAMETERS"
        chips={selected ? ['global', shortDate(selected.valuation_date), `${rows.length} rows`] : ['global', loading ? 'loading' : 'empty']}
        action={
          <Button type="button" onClick={() => setModalOpen(true)} disabled={importing}>
            <Upload size={16} aria-hidden="true" />
            <span>Import XLSX</span>
          </Button>
        }
      />
      {error && <Empty message={`Could not load pricing parameters: ${error}`} />}
      {!error && (
        <div className="wl-pricing-params">
          <aside className="wl-pricing-params__list" aria-label="Pricing parameter profiles">
            {profiles.map((profile) => (
              <button
                key={profile.id}
                className={`wl-pricing-params__profile ${selected?.id === profile.id ? 'is-active' : ''}`}
                onClick={() => onSelectProfile(profile.id)}
                type="button"
              >
                <strong>{profile.name}</strong>
                <span>{shortDate(profile.valuation_date)} · {profile.summary?.row_count ?? profile.rows.length} rows</span>
              </button>
            ))}
          </aside>
          <section className="wl-pricing-params__detail">
            {selected ? (
              <>
                <div className="wl-pricing-params__tiles">
                  <Tile label="Rows" value={String(rows.length)} />
                  <Tile label="Duplicates" value={String((selected.summary?.duplicate_trade_ids ?? []).length)} />
                  <Tile label="Status" value={selected.status} />
                </div>
                <Table columns={columns} rows={rows} rowKey={(row) => row.id} />
              </>
            ) : (
              <Empty message={loading ? 'Loading pricing parameter profiles...' : 'No pricing parameter profiles yet.'} />
            )}
          </section>
        </div>
      )}
      {feedback && <p className="wl-pricing-params__feedback">{feedback}</p>}
      <Modal open={modalOpen} onOpenChange={setModalOpen} title="Import Pricing Parameters">
        <form className="wl-pricing-params__form" onSubmit={submit}>
          <label>Profile name<input aria-label="Profile name" value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Valuation date<input aria-label="Valuation date" type="date" value={valuationDate} onChange={(event) => setValuationDate(event.target.value)} /></label>
          <label>Sheet name<input aria-label="Sheet name" value={sheetName} onChange={(event) => setSheetName(event.target.value)} /></label>
          <input ref={fileRef} aria-label="Pricing parameters xlsx" type="file" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={onFile} />
          <Button type="submit" variant="primary" disabled={!file || importing}>Import</Button>
        </form>
      </Modal>
    </>
  );
}

function shortDate(value: string): string {
  return value.slice(0, 10);
}

function fmt(value: number | null | undefined): string {
  return value == null ? '-' : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}
```

- [ ] **Step 4: Add live component**

Create `frontend/src/routes/PricingParameters.live.tsx`.

```tsx
import { useEffect, useState } from 'react';
import { api, uploadForm } from '../api/client';
import type { PricingParameterProfile } from '../types';
import { PricingParameters } from './PricingParameters';

export function PricingParametersLive() {
  const [profiles, setProfiles] = useState<PricingParameterProfile[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const rows = await api<PricingParameterProfile[]>('/api/pricing-parameter-profiles');
      setProfiles(rows);
      setSelectedId((current) => current ?? rows[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const onImport = async (payload: { file: File; name: string; valuationDate: string; sheetName: string }) => {
    const form = new FormData();
    form.append('file', payload.file);
    if (payload.name.trim()) form.append('name', payload.name.trim());
    if (payload.valuationDate.trim()) form.append('valuation_date', payload.valuationDate.trim());
    if (payload.sheetName.trim()) form.append('sheet_name', payload.sheetName.trim());
    setImporting(true);
    setFeedback(null);
    try {
      const profile = await uploadForm<PricingParameterProfile>('/api/pricing-parameter-profiles/import', form);
      setFeedback(`Imported ${profile.summary?.row_count ?? profile.rows.length} rows.`);
      setSelectedId(profile.id);
      await load();
    } catch (e) {
      setFeedback(`Could not import pricing parameters: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setImporting(false);
    }
  };

  return (
    <PricingParameters
      profiles={profiles}
      selectedId={selectedId}
      loading={loading}
      error={error}
      importing={importing}
      feedback={feedback}
      onSelectProfile={setSelectedId}
      onImport={onImport}
    />
  );
}
```

- [ ] **Step 5: Add CSS using repo conventions**

Create `frontend/src/routes/PricingParameters.css`.

```css
.wl-pricing-params {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: var(--gap-4);
  min-height: 0;
}
.wl-pricing-params__list {
  border: 1px solid var(--hairline);
  border-radius: var(--radius-2);
  background: var(--panel);
  padding: var(--gap-2);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-pricing-params__profile {
  border: 1px solid transparent;
  border-radius: var(--radius-2);
  background: transparent;
  color: var(--ink);
  padding: var(--gap-2);
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wl-pricing-params__profile span {
  color: var(--ink-2);
  font-size: var(--type-small-size);
}
.wl-pricing-params__profile.is-active {
  border-color: var(--line);
  background: var(--paper);
}
.wl-pricing-params__detail {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
}
.wl-pricing-params__tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--gap-3);
}
.wl-pricing-params__form {
  display: grid;
  gap: var(--gap-3);
}
.wl-pricing-params__form label {
  display: grid;
  gap: var(--gap-1);
  color: var(--ink-2);
  font-size: var(--type-small-size);
}
.wl-pricing-params__form input {
  border: 1px solid var(--line);
  border-radius: var(--radius-2);
  background: var(--paper);
  color: var(--ink);
  padding: 8px 10px;
}
.wl-pricing-params__feedback {
  margin: var(--gap-3) 0 0;
  color: var(--ink-2);
}
```

- [ ] **Step 6: Wire route and types**

Apply Task 4 `frontend/src/types.ts` and `frontend/src/main.tsx` edits now.

- [ ] **Step 7: Run frontend test**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/PricingParameters.live.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Commit Task 5**

```bash
git add frontend/src/types.ts frontend/src/main.tsx frontend/src/routes/PricingParameters.live.tsx frontend/src/routes/PricingParameters.tsx frontend/src/routes/PricingParameters.css frontend/src/routes/PricingParameters.live.test.tsx
git commit -m "feat(ui): add pricing parameters management page"
```

---

### Task 6: Market Data Page

**Files:**
- Add: `frontend/src/routes/MarketData.live.tsx`
- Add: `frontend/src/routes/MarketData.tsx`
- Add: `frontend/src/routes/MarketData.css`
- Add: `frontend/src/routes/MarketData.live.test.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Write failing frontend tests**

Create `frontend/src/routes/MarketData.live.test.tsx`.

```tsx
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MarketDataLive } from './MarketData.live';

const profile = {
  id: 9,
  name: 'CSI300 April',
  source: 'akshare',
  symbol: '000300',
  asset_class: 'index',
  start_date: '2026-04-01',
  end_date: '2026-04-30',
  adjust: 'qfq',
  valuation_date: '2026-05-11T00:00:00',
  data: {
    spot: 102.5,
    latest: { date: '2026-04-30', close: 102.5 },
    rows: [{ date: '2026-04-30', open: 100, high: 103, low: 99, close: 102.5, volume: 1000 }],
  },
  source_metadata: { fallback: false, source_name: 'fake' },
  created_at: '2026-05-11T00:00:00',
  updated_at: '2026-05-11T00:00:00',
};

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('MarketDataLive', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders AKShare profiles and OHLC rows', async () => {
    globalThis.fetch = vi.fn(async () => response([profile])) as unknown as typeof fetch;
    render(<MarketDataLive />);

    await waitFor(() => expect(screen.getByText('MARKET DATA')).toBeInTheDocument());
    expect(screen.getByText('CSI300 April')).toBeInTheDocument();
    expect(screen.getByText('000300')).toBeInTheDocument();
    expect(screen.getByText('2026-04-30')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /fetch akshare/i })).toBeInTheDocument();
  });

  it('submits an AKShare fetch request', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      if (url === '/api/market-data/profiles') return response([]);
      if (url === '/api/market-data/profiles/akshare' && init?.method === 'POST') return response(profile);
      return response({});
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<MarketDataLive />);

    await waitFor(() => expect(screen.getByText('MARKET DATA')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /fetch akshare/i }));
    await userEvent.clear(screen.getByLabelText('Symbol'));
    await userEvent.type(screen.getByLabelText('Symbol'), '000300');
    await userEvent.type(screen.getByLabelText('Start date'), '2026-04-01');
    await userEvent.type(screen.getByLabelText('End date'), '2026-04-30');
    await userEvent.click(screen.getByRole('button', { name: /^fetch$/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/market-data/profiles/akshare', expect.objectContaining({ method: 'POST' })));
    const body = JSON.parse(String(fetchMock.mock.calls.find(([url]) => url === '/api/market-data/profiles/akshare')?.[1]?.body));
    expect(body).toMatchObject({ symbol: '000300', asset_class: 'index', start_date: '2026-04-01', end_date: '2026-04-30' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/MarketData.live.test.tsx
```

Expected: FAIL because route files do not exist.

- [ ] **Step 3: Add presentational component**

Create `frontend/src/routes/MarketData.tsx` using the same compact layout as Task 5.

```tsx
import { useMemo, useState, type FormEvent } from 'react';
import { RadioTower } from 'lucide-react';
import { Button } from '../components/Button';
import { Empty } from '../components/Empty';
import { Modal } from '../components/Modal';
import { PageHeader } from '../components/PageHeader';
import { Table, type Column } from '../components/Table';
import { Tile } from '../components/Tile';
import type { MarketDataProfile } from '../types';
import './MarketData.css';

type OhlcRow = {
  date: string;
  open?: number;
  high?: number;
  low?: number;
  close?: number;
  volume?: number;
};

type FetchPayload = {
  symbol: string;
  assetClass: 'stock' | 'index' | 'futures';
  startDate: string;
  endDate: string;
  adjust: string;
  name: string;
};

type Props = {
  profiles: MarketDataProfile[];
  selectedId: number | null;
  loading: boolean;
  error: string | null;
  fetching: boolean;
  feedback: string | null;
  onSelectProfile: (id: number) => void;
  onFetch: (payload: FetchPayload) => void;
};

export function MarketData({
  profiles,
  selectedId,
  loading,
  error,
  fetching,
  feedback,
  onSelectProfile,
  onFetch,
}: Props) {
  const [modalOpen, setModalOpen] = useState(false);
  const [symbol, setSymbol] = useState('000300');
  const [assetClass, setAssetClass] = useState<'stock' | 'index' | 'futures'>('index');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [adjust, setAdjust] = useState('qfq');
  const [name, setName] = useState('');
  const selected = useMemo(
    () => profiles.find((profile) => profile.id === selectedId) ?? profiles[0] ?? null,
    [profiles, selectedId],
  );
  const rows = ((selected?.data?.rows ?? []) as OhlcRow[]);
  const columns: Column<OhlcRow>[] = [
    { key: 'date', header: 'DATE', width: '1fr' },
    { key: 'open', header: 'OPEN', numeric: true, render: (row) => fmt(row.open) },
    { key: 'high', header: 'HIGH', numeric: true, render: (row) => fmt(row.high) },
    { key: 'low', header: 'LOW', numeric: true, render: (row) => fmt(row.low) },
    { key: 'close', header: 'CLOSE', numeric: true, render: (row) => fmt(row.close) },
    { key: 'volume', header: 'VOL', numeric: true, render: (row) => fmt(row.volume) },
  ];

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onFetch({ symbol, assetClass, startDate, endDate, adjust, name });
    setModalOpen(false);
  };

  return (
    <>
      <PageHeader
        title="MARKET DATA"
        chips={selected ? ['AKShare', selected.symbol, `spot ${fmt(selected.data?.spot)}`] : ['AKShare', loading ? 'loading' : 'empty']}
        action={
          <Button type="button" onClick={() => setModalOpen(true)} disabled={fetching}>
            <RadioTower size={16} aria-hidden="true" />
            <span>Fetch AKShare</span>
          </Button>
        }
      />
      {error && <Empty message={`Could not load market data: ${error}`} />}
      {!error && (
        <div className="wl-market-data">
          <aside className="wl-market-data__list" aria-label="Market data profiles">
            {profiles.map((profile) => (
              <button
                key={profile.id}
                className={`wl-market-data__profile ${selected?.id === profile.id ? 'is-active' : ''}`}
                onClick={() => onSelectProfile(profile.id)}
                type="button"
              >
                <strong>{profile.name}</strong>
                <span>{profile.symbol} · {profile.end_date}</span>
              </button>
            ))}
          </aside>
          <section className="wl-market-data__detail">
            {selected ? (
              <>
                <div className="wl-market-data__tiles">
                  <Tile label="Latest close" value={fmt(selected.data?.spot)} />
                  <Tile label="Rows" value={String(rows.length)} />
                  <Tile label="Fallback" value={selected.source_metadata?.fallback ? 'yes' : 'no'} />
                </div>
                <Table columns={columns} rows={rows} rowKey={(row) => row.date} />
              </>
            ) : (
              <Empty message={loading ? 'Loading market data profiles...' : 'No market data profiles yet.'} />
            )}
          </section>
        </div>
      )}
      {feedback && <p className="wl-market-data__feedback">{feedback}</p>}
      <Modal open={modalOpen} onOpenChange={setModalOpen} title="Fetch AKShare">
        <form className="wl-market-data__form" onSubmit={submit}>
          <label>Profile name<input aria-label="Profile name" value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>Symbol<input aria-label="Symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} /></label>
          <label>Asset class<select aria-label="Asset class" value={assetClass} onChange={(event) => setAssetClass(event.target.value as FetchPayload['assetClass'])}><option value="index">index</option><option value="stock">stock</option><option value="futures">futures</option></select></label>
          <label>Start date<input aria-label="Start date" type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} /></label>
          <label>End date<input aria-label="End date" type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} /></label>
          <label>Adjust<input aria-label="Adjust" value={adjust} onChange={(event) => setAdjust(event.target.value)} /></label>
          <Button type="submit" variant="primary" disabled={fetching || !symbol || !startDate || !endDate}>Fetch</Button>
        </form>
      </Modal>
    </>
  );
}

function fmt(value: unknown): string {
  return value == null || value === '' ? '-' : Number(value).toLocaleString(undefined, { maximumFractionDigits: 6 });
}
```

- [ ] **Step 4: Add live component**

Create `frontend/src/routes/MarketData.live.tsx`.

```tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { MarketDataProfile } from '../types';
import { MarketData } from './MarketData';

export function MarketDataLive() {
  const [profiles, setProfiles] = useState<MarketDataProfile[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  const load = async () => {
    setError(null);
    try {
      const rows = await api<MarketDataProfile[]>('/api/market-data/profiles');
      setProfiles(rows);
      setSelectedId((current) => current ?? rows[0]?.id ?? null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, []);

  const onFetch = async (payload: { symbol: string; assetClass: 'stock' | 'index' | 'futures'; startDate: string; endDate: string; adjust: string; name: string }) => {
    setFetching(true);
    setFeedback(null);
    try {
      const profile = await api<MarketDataProfile>('/api/market-data/profiles/akshare', {
        method: 'POST',
        body: JSON.stringify({
          symbol: payload.symbol,
          asset_class: payload.assetClass,
          start_date: payload.startDate,
          end_date: payload.endDate,
          adjust: payload.adjust,
          name: payload.name || undefined,
        }),
      });
      setFeedback(`Saved ${profile.symbol} market data profile.`);
      setSelectedId(profile.id);
      await load();
    } catch (e) {
      setFeedback(`Could not fetch AKShare data: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setFetching(false);
    }
  };

  return (
    <MarketData
      profiles={profiles}
      selectedId={selectedId}
      loading={loading}
      error={error}
      fetching={fetching}
      feedback={feedback}
      onSelectProfile={setSelectedId}
      onFetch={onFetch}
    />
  );
}
```

- [ ] **Step 5: Add CSS matching pricing page**

Create `frontend/src/routes/MarketData.css`.

```css
.wl-market-data {
  display: grid;
  grid-template-columns: 260px 1fr;
  gap: var(--gap-4);
  min-height: 0;
}
.wl-market-data__list {
  border: 1px solid var(--hairline);
  border-radius: var(--radius-2);
  background: var(--panel);
  padding: var(--gap-2);
  display: flex;
  flex-direction: column;
  gap: var(--gap-2);
}
.wl-market-data__profile {
  border: 1px solid transparent;
  border-radius: var(--radius-2);
  background: transparent;
  color: var(--ink);
  padding: var(--gap-2);
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.wl-market-data__profile span {
  color: var(--ink-2);
  font-size: var(--type-small-size);
}
.wl-market-data__profile.is-active {
  border-color: var(--line);
  background: var(--paper);
}
.wl-market-data__detail {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--gap-3);
}
.wl-market-data__tiles {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--gap-3);
}
.wl-market-data__form {
  display: grid;
  gap: var(--gap-3);
}
.wl-market-data__form label {
  display: grid;
  gap: var(--gap-1);
  color: var(--ink-2);
  font-size: var(--type-small-size);
}
.wl-market-data__form input,
.wl-market-data__form select {
  border: 1px solid var(--line);
  border-radius: var(--radius-2);
  background: var(--paper);
  color: var(--ink);
  padding: 8px 10px;
}
.wl-market-data__feedback {
  margin: var(--gap-3) 0 0;
  color: var(--ink-2);
}
```

- [ ] **Step 6: Run frontend tests**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/MarketData.live.test.tsx frontend/src/routes/PricingParameters.live.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit Task 6**

```bash
git add frontend/src/routes/MarketData.live.tsx frontend/src/routes/MarketData.tsx frontend/src/routes/MarketData.css frontend/src/routes/MarketData.live.test.tsx frontend/src/main.tsx frontend/src/types.ts
git commit -m "feat(ui): add market data management page"
```

---

### Task 7: Positions Pricing Profile Selector

**Files:**
- Modify: `frontend/src/routes/Positions.live.tsx`
- Modify: `frontend/src/routes/Positions.tsx`
- Modify: `frontend/src/types.ts`
- Test: `frontend/src/routes/Positions.live.test.tsx`

- [ ] **Step 1: Write failing selector test**

Append to `frontend/src/routes/Positions.live.test.tsx`:

```tsx
it('sends selected pricing parameter profile for portfolio pricing', async () => {
  const profiles = [{
    id: 7,
    name: '2026-04-30 Close',
    valuation_date: '2026-04-30T00:00:00',
    source_type: 'xlsx',
    source_path: null,
    status: 'completed',
    summary: { row_count: 1 },
    rows: [],
    created_at: '2026-05-11T00:00:00',
    updated_at: '2026-05-11T00:00:00',
  }];
  const postBodies: unknown[] = [];
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === portfoliosPath) return response([portfolio]);
    if (url === '/api/portfolios/1/runs') return response([]);
    if (url === '/api/portfolios/1/market-inputs') return response([marketInput]);
    if (url === '/api/pricing-parameter-profiles') return response(profiles);
    if (url === '/api/portfolios/1/positions/price' && init?.method === 'POST') {
      postBodies.push(JSON.parse(String(init.body)));
      return response({ ...fullRun, pricing_parameter_profile_id: 7, overrides: { pricing_parameter_profile_id: 7 } });
    }
    return response({});
  }) as unknown as typeof fetch;

  render(<PositionsLive />);

  await waitFor(() => expect(screen.getByText('T-VANILLA')).toBeInTheDocument());
  await userEvent.selectOptions(screen.getByLabelText('Pricing profile'), '7');
  await userEvent.click(screen.getByRole('button', { name: /run pricing/i }));

  await waitFor(() => expect(postBodies).toHaveLength(1));
  expect(postBodies[0]).toMatchObject({ pricing_parameter_profile_id: 7 });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/Positions.live.test.tsx -t "selected pricing parameter profile"
```

Expected: FAIL because the selector is missing.

- [ ] **Step 3: Extend Positions props and request type**

In `frontend/src/routes/Positions.tsx`, update `PositionPricingRequest`:

```ts
  pricing_parameter_profile_id?: number;
```

Add option type:

```ts
export type PricingProfileOption = {
  id: number;
  name: string;
  valuation_date: string;
};
```

Add props:

```ts
  pricingProfiles: PricingProfileOption[];
  selectedPricingProfileId: number | null;
  onSelectPricingProfile: (profileId: number | null) => void;
```

- [ ] **Step 4: Add selector to page header actions**

In `Positions`, inside `.wl-positions__actions` before `Run Pricing`, add:

```tsx
            <label className="wl-positions__select-field" htmlFor="positions-pricing-profile">
              <span>Pricing profile</span>
              <select
                id="positions-pricing-profile"
                aria-label="Pricing profile"
                value={selectedPricingProfileId ?? ''}
                onChange={(event) => onSelectPricingProfile(event.target.value ? Number(event.target.value) : null)}
              >
                <option value="">None</option>
                {pricingProfiles.map((profile) => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name} · {profile.valuation_date.slice(0, 10)}
                  </option>
                ))}
              </select>
            </label>
```

In `buildPricingRequest()`, include:

```ts
  if (ticket.pricing_parameter_profile_id != null) {
    request.pricing_parameter_profile_id = ticket.pricing_parameter_profile_id;
  }
```

For single-position dialog initial ticket state, include the selected page profile id in the ticket passed to `onPricePosition`.

- [ ] **Step 5: Load profiles in live component**

In `frontend/src/routes/Positions.live.tsx`, import `PricingParameterProfile`.

Add state:

```ts
  const [pricingProfiles, setPricingProfiles] = useState<PricingParameterProfile[]>([]);
  const [selectedPricingProfileId, setSelectedPricingProfileId] = useState<number | null>(null);
```

In `load()`, fetch profiles with portfolios:

```ts
      const [portfolios, pricingProfiles] = await Promise.all([
        api<Portfolio[]>(POSITIONS_PORTFOLIO_PATH),
        api<PricingParameterProfile[]>('/api/pricing-parameter-profiles'),
      ]);
      setPricingProfiles(pricingProfiles);
```

Update `handleRunPricing()` body:

```ts
      await api(`/api/portfolios/${portfolio.id}/positions/price`, {
        method: 'POST',
        body: JSON.stringify({
          ...(selectedPricingProfileId == null ? {} : { pricing_parameter_profile_id: selectedPricingProfileId }),
        }),
      });
```

Pass props:

```tsx
      pricingProfiles={pricingProfiles.map((profile) => ({
        id: profile.id,
        name: profile.name,
        valuation_date: profile.valuation_date,
      }))}
      selectedPricingProfileId={selectedPricingProfileId}
      onSelectPricingProfile={setSelectedPricingProfileId}
```

- [ ] **Step 6: Run Positions test**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/Positions.live.test.tsx -t "selected pricing parameter profile"
```

Expected: PASS.

- [ ] **Step 7: Run broader frontend tests**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/Positions.live.test.tsx frontend/src/routes/PricingParameters.live.test.tsx frontend/src/routes/MarketData.live.test.tsx
```

Expected: PASS.

- [ ] **Step 8: Commit Task 7**

```bash
git add frontend/src/routes/Positions.live.tsx frontend/src/routes/Positions.tsx frontend/src/routes/Positions.live.test.tsx frontend/src/types.ts
git commit -m "feat(positions): select pricing parameter profiles"
```

---

### Task 8: Final Verification And Browser Check

**Files:**
- Verify only, with small fixes if tests reveal integration errors.

- [ ] **Step 1: Run focused backend suite**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py::test_pricing_parameter_profile_import_list_detail_and_pricing tests/test_api.py::test_akshare_market_data_profile_list_detail_and_spot_feed tests/test_api.py::test_position_and_market_upload_then_batch_price tests/test_position_import_pricing.py::test_market_workbook_join_and_override_precedence tests/test_position_import_pricing.py::test_pricing_profile_precedence_between_profile_and_overrides -q
```

Expected: PASS.

- [ ] **Step 2: Run focused frontend suite**

Run:

```bash
npm --prefix frontend run test -- --run frontend/src/routes/PricingParameters.live.test.tsx frontend/src/routes/MarketData.live.test.tsx frontend/src/routes/Positions.live.test.tsx
```

Expected: PASS.

- [ ] **Step 3: Run typecheck or build**

Run:

```bash
npm --prefix frontend run build
```

Expected: PASS and no TypeScript route/type errors.

- [ ] **Step 4: Start app for visual verification**

Run backend and frontend according to the repo’s current local workflow. If existing scripts are available, prefer those. If not, use:

```bash
.venv/bin/python -m uvicorn app.main:create_app --factory --app-dir backend --reload
npm --prefix frontend run dev
```

Expected: backend health responds and Vite prints a localhost URL.

- [ ] **Step 5: Browser-check route rendering**

Use the Browser plugin or Playwright to open the Vite URL. Verify:

- AppShell shows `Pricing Parameters` and `Market Data` nav items.
- `Pricing Parameters` renders `PRICING PARAMETERS`, compact profile list, summary tiles, table, and import modal.
- `Market Data` renders `MARKET DATA`, compact profile list, summary tiles, OHLC table, and fetch modal.
- `Positions` renders the pricing profile selector without shifting existing buttons or breaking the position detail modal.
- The new pages use the same compact tokenized style as existing pages.

- [ ] **Step 6: Commit verification fixes if any**

If verification required code changes:

```bash
git add <changed-files>
git commit -m "fix: polish pricing and market data management flows"
```

If no fixes were needed, do not create an empty commit.
