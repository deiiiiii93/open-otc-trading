# Hedging Module — Part 1: Instrument Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the hedging instrument catalog, the durable hedging map (underlying → allowed contracts), an underlying-aware AKShare loader, and the master-detail marking page — backend + UI only, no agent tools.

**Architecture:** Two new ORM tables (`hedge_instruments` refreshable catalog + `hedge_map_entries` durable marks) keyed by the stable `(exchange, contract_code)` identity, plus a code-level `HedgeUniverseResolver`. The loader reuses the existing `TaskRun` + `submit_async_task` machinery (exactly like `risk_engine.queue_portfolio_risk` / `execute_risk_run_task`), enumerates contracts per `(underlying, family)` via monkeypatchable adapter functions, upserts the catalog, and reconciles — expiring missing contracts *only* when a family enumeration confirmably succeeded so an AKShare outage never wipes the map. Endpoints live in the `create_app` factory in `main.py` (this repo has no router modules); all logic sits in `services/`.

**Tech Stack:** Python 3.11 / SQLAlchemy 2 ORM / Alembic / FastAPI / pytest; React + TypeScript + Vite + Vitest + Testing Library.

---

## Conventions for this plan

- **Worktree:** all work happens in `/Users/fuxinyao/open-otc-trading-hedging` on branch `feature/hedging-instrument-management`. The spec is at `docs/superpowers/specs/2026-06-02-hedging-instrument-management-design.md`.
- **Run one backend test:** from the worktree root, `pytest tests/test_hedging_xyz.py::test_name -v` (pytest config sets `pythonpath=["backend"]`, `testpaths=["tests"]`).
- **Run all backend tests:** `pytest` (from worktree root).
- **Run one frontend test:** `cd frontend && npx vitest run src/routes/Hedging.test.tsx`.
- **DB in tests:** the `session` and `client` fixtures (`tests/conftest.py`) call `database.init_db()`, which creates tables from ORM metadata — so model/domain/loader/API tests do **not** need the migration. The Alembic migration is verified by its own test (Task 2), mirroring `tests/test_underlying_migration.py`.
- **Deviation from spec wording:** the spec says "router (`routers/hedging.py`)"; this repo registers all routes inside `create_app()` in `backend/app/main.py` and the `routers/` package is empty. We follow the real pattern — thin endpoints in `create_app`, all logic in `services/`.

---

## File structure

**Create:**
- `backend/app/services/hedging_universe.py` — `EnumeratedContract`, `FamilySpec`, the `_INDEX_FAMILIES` resolution table, `resolve_families()`, the `ENUMERATORS` registry, and the AKShare adapter functions.
- `backend/app/services/hedging_loader.py` — `HedgeLoadInProgress`, `queue_hedge_load()`, `execute_hedge_load_task()`, and the `_upsert_catalog` / `_expire_missing` / `reconcile_map` helpers + `list_in_scope_underlyings`.
- `backend/app/services/domains/hedging.py` — read/query + mark/unmark/map/purge domain functions.
- `backend/alembic/versions/0020_hedging_instrument_catalog.py` — migration for the two tables.
- `frontend/src/routes/Hedging.tsx` — presentational page.
- `frontend/src/routes/Hedging.live.tsx` — data container.
- `frontend/src/routes/Hedging.test.tsx` — component tests.
- `frontend/src/routes/Hedging.live.test.tsx` — container tests.
- `tests/test_hedging_models.py`, `tests/test_hedging_migration.py`, `tests/test_hedging_universe.py`, `tests/test_hedging_loader.py`, `tests/test_hedging_domain.py`, `tests/test_hedging_api.py`.

**Modify:**
- `backend/app/models.py` — add `TaskKind.HEDGE_LOAD`, `HedgeInstrument`, `HedgeMapEntry`; ensure `Date` + `UniqueConstraint` imports.
- `backend/app/schemas.py` — add hedging Pydantic Out/In models.
- `backend/app/main.py` — add the `/api/hedging/*` endpoints inside `create_app`.
- `frontend/src/types.ts` — add `'hedging'` to `Route`; add hedging row types.
- `frontend/src/main.tsx` — import `HedgingLive`, add nav item, command-palette jump, and conditional render.

---

## Task 1: ORM models + TaskKind

**Files:**
- Modify: `backend/app/models.py`
- Test: `tests/test_hedging_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_models.py
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import HedgeInstrument, HedgeMapEntry, TaskKind, Underlying


def _underlying(session, symbol="000905.SH"):
    u = Underlying(symbol=symbol, asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    return u


def test_hedge_load_task_kind_value():
    assert TaskKind.HEDGE_LOAD.value == "hedge_instrument_load"


def test_hedge_instrument_unique_on_exchange_code(session):
    u = _underlying(session)
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", instrument_type="future",
    ))
    session.flush()
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", instrument_type="future",
    ))
    with pytest.raises(IntegrityError):
        session.flush()


def test_hedge_map_entry_unique_on_underlying_contract(session):
    u = _underlying(session)
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        expiry=date(2026, 6, 21),
    ))
    session.flush()
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    with pytest.raises(IntegrityError):
        session.flush()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_models.py -v`
Expected: FAIL with `ImportError: cannot import name 'HedgeInstrument'`.

- [ ] **Step 3: Add `TaskKind.HEDGE_LOAD`**

In `backend/app/models.py`, extend the enum:

```python
class TaskKind(str, Enum):
    POSITION_PRICING = "position_pricing"
    RISK_RUN = "risk_run"
    REPORT_JOB = "report_job"
    HEDGE_LOAD = "hedge_instrument_load"
```

- [ ] **Step 4: Ensure imports**

At the top of `backend/app/models.py`, confirm `Date` and `UniqueConstraint` are imported from `sqlalchemy` (add them to the existing `from sqlalchemy import (...)` block if missing), and `date` from `datetime`.

- [ ] **Step 5: Add the two models**

Add near the other equity/product models in `backend/app/models.py`:

```python
class HedgeInstrument(Base):
    __tablename__ = "hedge_instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int] = mapped_column(
        ForeignKey("underlyings.id"), index=True
    )
    family: Mapped[str] = mapped_column(String(40))
    series_root: Mapped[str] = mapped_column(String(40))
    exchange: Mapped[str] = mapped_column(String(40))
    contract_code: Mapped[str] = mapped_column(String(80))
    instrument_type: Mapped[str] = mapped_column(String(20))
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    akshare_symbol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="live", server_default="live", index=True
    )
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "exchange", "contract_code",
            name="uq_hedge_instruments_exchange_code",
        ),
        Index(
            "ix_hedge_instruments_underlying_family_status",
            "underlying_id", "family", "status",
        ),
    )


class HedgeMapEntry(Base):
    __tablename__ = "hedge_map_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int] = mapped_column(
        ForeignKey("underlyings.id"), index=True
    )
    exchange: Mapped[str] = mapped_column(String(40))
    contract_code: Mapped[str] = mapped_column(String(80))
    family: Mapped[str] = mapped_column(String(40))
    series_root: Mapped[str] = mapped_column(String(40))
    instrument_type: Mapped[str] = mapped_column(String(20))
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    reconcile_status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active"
    )
    marked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    marked_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (
        UniqueConstraint(
            "underlying_id", "exchange", "contract_code",
            name="uq_hedge_map_entries_underlying_contract",
        ),
    )
```

- [ ] **Step 6: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_models.py -v`
Expected: 3 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py tests/test_hedging_models.py
git commit -m "feat(hedging): add hedge_instrument + hedge_map_entry models"
```

---

## Task 2: Alembic migration `0020`

**Files:**
- Create: `backend/alembic/versions/0020_hedging_instrument_catalog.py`
- Test: `tests/test_hedging_migration.py`

- [ ] **Step 1: Write the failing test** (mirrors `tests/test_underlying_migration.py`)

```python
# tests/test_hedging_migration.py
from __future__ import annotations

import importlib
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def test_alembic_0020_creates_hedging_tables(tmp_path: Path):
    db_path = tmp_path / "migration.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    # underlyings must exist for the FKs; create a minimal stand-in.
    metadata = sa.MetaData()
    sa.Table(
        "underlyings", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False),
    )
    metadata.create_all(engine)

    migration = importlib.import_module(
        "backend.alembic.versions.0020_hedging_instrument_catalog"
    )
    connection = engine.connect()
    context = MigrationContext.configure(connection)
    original_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert {"hedge_instruments", "hedge_map_entries"} <= tables
    instr_cols = {c["name"] for c in inspector.get_columns("hedge_instruments")}
    assert {"exchange", "contract_code", "status", "loaded_at"} <= instr_cols
    uniques = {
        u["name"] for u in inspector.get_unique_constraints("hedge_instruments")
    }
    assert "uq_hedge_instruments_exchange_code" in uniques


def test_alembic_0020_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "migration.sqlite3"
    engine = sa.create_engine(f"sqlite+pysqlite:///{db_path}")
    metadata = sa.MetaData()
    sa.Table(
        "underlyings", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(80), nullable=False),
    )
    metadata.create_all(engine)
    migration = importlib.import_module(
        "backend.alembic.versions.0020_hedging_instrument_catalog"
    )
    connection = engine.connect()
    context = MigrationContext.configure(connection)
    original_op = migration.op
    migration.op = Operations(context)
    try:
        migration.upgrade()
        migration.upgrade()  # second run must not raise
        connection.commit()
    finally:
        migration.op = original_op
        connection.close()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_migration.py -v`
Expected: FAIL with `ModuleNotFoundError: ...0020_hedging_instrument_catalog`.

- [ ] **Step 3: Write the migration** (Core tables only — no ORM imports, guarded by `inspect`)

```python
# backend/alembic/versions/0020_hedging_instrument_catalog.py
"""hedging instrument catalog + map

Revision ID: 0020_hedging_instrument_catalog
Revises: 0019_underlying_master_data
Create Date: 2026-06-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


revision = "0020_hedging_instrument_catalog"
down_revision = "0019_underlying_master_data"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    if table not in _tables():
        return set()
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if "hedge_instruments" not in _tables():
        op.create_table(
            "hedge_instruments",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id"), nullable=False),
            sa.Column("family", sa.String(length=40), nullable=False),
            sa.Column("series_root", sa.String(length=40), nullable=False),
            sa.Column("exchange", sa.String(length=40), nullable=False),
            sa.Column("contract_code", sa.String(length=80), nullable=False),
            sa.Column("instrument_type", sa.String(length=20), nullable=False),
            sa.Column("option_type", sa.String(length=4), nullable=True),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("expiry", sa.Date(), nullable=True),
            sa.Column("multiplier", sa.Float(), nullable=True),
            sa.Column("last_price", sa.Float(), nullable=True),
            sa.Column("akshare_symbol", sa.String(length=80), nullable=True),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="live"),
            sa.Column("loaded_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("exchange", "contract_code", name="uq_hedge_instruments_exchange_code"),
        )
    for name, cols in (
        ("ix_hedge_instruments_underlying_id", ["underlying_id"]),
        ("ix_hedge_instruments_status", ["status"]),
        ("ix_hedge_instruments_underlying_family_status", ["underlying_id", "family", "status"]),
    ):
        if name not in _indexes("hedge_instruments"):
            op.create_index(name, "hedge_instruments", cols)

    if "hedge_map_entries" not in _tables():
        op.create_table(
            "hedge_map_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("underlying_id", sa.Integer(), sa.ForeignKey("underlyings.id"), nullable=False),
            sa.Column("exchange", sa.String(length=40), nullable=False),
            sa.Column("contract_code", sa.String(length=80), nullable=False),
            sa.Column("family", sa.String(length=40), nullable=False),
            sa.Column("series_root", sa.String(length=40), nullable=False),
            sa.Column("instrument_type", sa.String(length=20), nullable=False),
            sa.Column("option_type", sa.String(length=4), nullable=True),
            sa.Column("strike", sa.Float(), nullable=True),
            sa.Column("expiry", sa.Date(), nullable=True),
            sa.Column("reconcile_status", sa.String(length=20), nullable=False, server_default="active"),
            sa.Column("marked_by", sa.String(length=80), nullable=True),
            sa.Column("marked_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("underlying_id", "exchange", "contract_code", name="uq_hedge_map_entries_underlying_contract"),
        )
    if "ix_hedge_map_entries_underlying_id" not in _indexes("hedge_map_entries"):
        op.create_index("ix_hedge_map_entries_underlying_id", "hedge_map_entries", ["underlying_id"])


def downgrade() -> None:
    if "hedge_map_entries" in _tables():
        op.drop_table("hedge_map_entries")
    if "hedge_instruments" in _tables():
        op.drop_table("hedge_instruments")
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_migration.py -v`
Expected: 2 passed.

- [ ] **Step 5: Confirm the migration chain is intact**

Run: `pytest tests/test_underlying_migration.py tests/test_product_migration.py -q`
Expected: still passing (we only appended a new head).

- [ ] **Step 6: Commit**

```bash
git add backend/alembic/versions/0020_hedging_instrument_catalog.py tests/test_hedging_migration.py
git commit -m "feat(hedging): migration 0020 for hedge catalog + map tables"
```

---

## Task 3: Universe resolver (`EnumeratedContract`, `FamilySpec`, `resolve_families`)

**Files:**
- Create: `backend/app/services/hedging_universe.py`
- Test: `tests/test_hedging_universe.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_universe.py
from __future__ import annotations

from app.services.hedging_universe import FamilySpec, resolve_families


def _families(symbol, asset_class="index"):
    return {(s.family, s.series_root) for s in resolve_families(symbol, asset_class)}


def test_csi300_resolves_future_index_option_and_two_etf_options():
    fams = _families("000300.SH")
    assert ("index_future", "IF") in fams
    assert ("index_option", "IO") in fams
    assert ("etf_option", "510300") in fams
    assert ("etf_option", "159919") in fams


def test_csi500_resolves_future_and_etf_option_but_no_index_option():
    fams = _families("000905.SH")
    assert ("index_future", "IC") in fams
    assert ("etf_option", "510500") in fams
    assert not any(f == "index_option" for f, _ in fams)


def test_unknown_underlying_is_unresolvable():
    assert resolve_families("ZZZZ.SH", "index") == []


def test_commodity_resolves_from_asset_class():
    fams = _families("M.DCE", asset_class="commodity")
    assert ("commodity_future", "M") in fams
    assert ("commodity_option", "M") in fams


def test_family_spec_has_enumerator_key():
    spec = resolve_families("000300.SH", "index")[0]
    assert isinstance(spec, FamilySpec)
    assert spec.enumerator_key
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_universe.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.hedging_universe`.

- [ ] **Step 3: Write the resolver (registry only; enumerators added in Task 5)**

```python
# backend/app/services/hedging_universe.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable


@dataclass(frozen=True)
class EnumeratedContract:
    family: str
    series_root: str
    exchange: str
    contract_code: str
    instrument_type: str  # "future" | "option"
    option_type: str | None = None
    strike: float | None = None
    expiry: date | None = None
    multiplier: float | None = None
    last_price: float | None = None
    akshare_symbol: str | None = None


@dataclass(frozen=True)
class FamilySpec:
    family: str
    series_root: str
    enumerator_key: str


# code (symbol before the ".") -> list of (family, series_root, enumerator_key)
_INDEX_FAMILIES: dict[str, list[FamilySpec]] = {
    "000300": [  # 沪深300
        FamilySpec("index_future", "IF", "cffex_future"),
        FamilySpec("index_option", "IO", "cffex_option"),
        FamilySpec("etf_option", "510300", "etf_option"),
        FamilySpec("etf_option", "159919", "etf_option"),
    ],
    "000905": [  # 中证500 — no CFFEX index option
        FamilySpec("index_future", "IC", "cffex_future"),
        FamilySpec("etf_option", "510500", "etf_option"),
    ],
    "000852": [  # 中证1000
        FamilySpec("index_future", "IM", "cffex_future"),
        FamilySpec("index_option", "MO", "cffex_option"),
    ],
    "000016": [  # 上证50
        FamilySpec("index_future", "IH", "cffex_future"),
        FamilySpec("index_option", "HO", "cffex_option"),
        FamilySpec("etf_option", "510050", "etf_option"),
    ],
}


def _code(symbol: str) -> str:
    return symbol.split(".", 1)[0].strip().upper()


def resolve_families(symbol: str, asset_class: str | None = None) -> list[FamilySpec]:
    """Return the instrument families that can hedge an underlying.

    Empty list = unresolvable (surfaced in the UI, skipped by the loader).
    """
    code = _code(symbol)
    if code in _INDEX_FAMILIES:
        return list(_INDEX_FAMILIES[code])
    if (asset_class or "").lower() == "commodity":
        return [
            FamilySpec("commodity_future", code, "commodity_future"),
            FamilySpec("commodity_option", code, "commodity_option"),
        ]
    return []
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_universe.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_universe.py tests/test_hedging_universe.py
git commit -m "feat(hedging): underlying-aware family resolver"
```

---

## Task 4: Catalog upsert, expire, and map reconcile

**Files:**
- Create: `backend/app/services/hedging_loader.py`
- Test: `tests/test_hedging_loader.py` (reconcile portion)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_loader.py
from __future__ import annotations

from datetime import date

from app.models import HedgeInstrument, HedgeMapEntry, Underlying
from app.services.hedging_universe import EnumeratedContract
from app.services import hedging_loader


def _underlying(session, symbol="000905.SH"):
    u = Underlying(symbol=symbol, asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    return u


def _contract(code="IC2406", strike=None, option_type=None):
    return EnumeratedContract(
        family="index_future", series_root="IC", exchange="CFFEX",
        contract_code=code, instrument_type="future",
        option_type=option_type, strike=strike, expiry=date(2026, 6, 21),
        multiplier=200.0, last_price=5600.0, akshare_symbol=code,
    )


def test_upsert_inserts_then_updates(session):
    u = _underlying(session)
    seen = hedging_loader._upsert_catalog(session, [_contract()], u.id)
    session.flush()
    assert seen == {("CFFEX", "IC2406")}
    row = session.query(HedgeInstrument).one()
    assert row.last_price == 5600.0 and row.status == "live"

    # second load with a new price updates in place (no duplicate)
    hedging_loader._upsert_catalog(
        session, [_contract()._replace_last(5700.0)], u.id
    ) if hasattr(_contract(), "_replace_last") else None
    updated = EnumeratedContract(
        family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC2406", instrument_type="future", last_price=5700.0,
    )
    hedging_loader._upsert_catalog(session, [updated], u.id)
    session.flush()
    assert session.query(HedgeInstrument).count() == 1
    assert session.query(HedgeInstrument).one().last_price == 5700.0


def test_expire_missing_only_flags_unseen(session):
    u = _underlying(session)
    hedging_loader._upsert_catalog(session, [_contract("IC2406"), _contract("IC2409")], u.id)
    session.flush()
    hedging_loader._expire_missing(session, u.id, "index_future", {("CFFEX", "IC2406")})
    session.flush()
    by_code = {r.contract_code: r.status for r in session.query(HedgeInstrument).all()}
    assert by_code == {"IC2406": "live", "IC2409": "expired"}


def test_reconcile_map_sets_active_and_stale(session):
    u = _underlying(session)
    hedging_loader._upsert_catalog(session, [_contract("IC2406")], u.id)
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="stale",
    ))
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2403",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active",
    ))
    session.flush()
    hedging_loader.reconcile_map(session)
    session.flush()
    by_code = {e.contract_code: e.reconcile_status for e in session.query(HedgeMapEntry).all()}
    assert by_code == {"IC2406": "active", "IC2403": "stale"}
```

> Note: the `_replace_last` line above is a guard that no-ops; it documents that `EnumeratedContract` is frozen. Keep the explicit `updated = EnumeratedContract(...)` reload as the real assertion.

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_loader.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.hedging_loader`.

- [ ] **Step 3: Write the helpers**

```python
# backend/app/services/hedging_loader.py
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import HedgeInstrument, HedgeMapEntry
from .hedging_universe import EnumeratedContract


def _upsert_catalog(
    session: Session, contracts: list[EnumeratedContract], underlying_id: int
) -> set[tuple[str, str]]:
    """Insert/refresh catalog rows; return the (exchange, code) keys seen."""
    seen: set[tuple[str, str]] = set()
    now = datetime.utcnow()
    for c in contracts:
        key = (c.exchange, c.contract_code)
        seen.add(key)
        row = (
            session.query(HedgeInstrument)
            .filter(
                HedgeInstrument.exchange == c.exchange,
                HedgeInstrument.contract_code == c.contract_code,
            )
            .one_or_none()
        )
        if row is None:
            session.add(HedgeInstrument(
                underlying_id=underlying_id,
                family=c.family, series_root=c.series_root,
                exchange=c.exchange, contract_code=c.contract_code,
                instrument_type=c.instrument_type, option_type=c.option_type,
                strike=c.strike, expiry=c.expiry, multiplier=c.multiplier,
                last_price=c.last_price, akshare_symbol=c.akshare_symbol,
                status="live", loaded_at=now,
            ))
        else:
            row.underlying_id = underlying_id
            row.family = c.family
            row.series_root = c.series_root
            row.instrument_type = c.instrument_type
            row.option_type = c.option_type
            row.strike = c.strike
            row.expiry = c.expiry
            row.multiplier = c.multiplier
            row.last_price = c.last_price
            row.akshare_symbol = c.akshare_symbol
            row.status = "live"
            row.loaded_at = now
    return seen


def _expire_missing(
    session: Session, underlying_id: int, family: str, seen: set[tuple[str, str]]
) -> None:
    """Flag live rows of this (underlying, family) not seen this run as expired.

    Call ONLY when the family enumeration confirmably succeeded.
    """
    rows = (
        session.query(HedgeInstrument)
        .filter(
            HedgeInstrument.underlying_id == underlying_id,
            HedgeInstrument.family == family,
            HedgeInstrument.status == "live",
        )
        .all()
    )
    for row in rows:
        if (row.exchange, row.contract_code) not in seen:
            row.status = "expired"


def reconcile_map(session: Session) -> None:
    """Recompute every map entry's reconcile_status against live catalog rows."""
    live = {
        (r.exchange, r.contract_code)
        for r in session.query(
            HedgeInstrument.exchange, HedgeInstrument.contract_code
        ).filter(HedgeInstrument.status == "live")
    }
    for entry in session.query(HedgeMapEntry).all():
        entry.reconcile_status = (
            "active" if (entry.exchange, entry.contract_code) in live else "stale"
        )
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_loader.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_loader.py tests/test_hedging_loader.py
git commit -m "feat(hedging): catalog upsert/expire + map reconcile"
```

---

## Task 5: AKShare enumerator adapters

**Files:**
- Modify: `backend/app/services/hedging_universe.py`
- Test: `tests/test_hedging_universe.py` (append a skip-if-unavailable smoke test)

- [ ] **Step 1: Confirm exact AKShare functions**

Invoke the `akshare-data` skill (or `model-researcher`) and confirm, for the current AKShare version, the function + columns that list: (a) live CFFEX index-future contracts, (b) CFFEX index-option chains, (c) SSE/SZSE ETF-option chains, (d) commodity futures/option chains. Record the chosen functions in a short docstring at the top of each adapter. This is the one version-sensitive spot; everything below it is normalization that the unit tests already cover via monkeypatching.

- [ ] **Step 2: Write the failing smoke test** (skips cleanly when AKShare is absent)

```python
# append to tests/test_hedging_universe.py
import pytest

from app.services.hedging_universe import ENUMERATORS


def _akshare_available() -> bool:
    try:
        import akshare  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _akshare_available(), reason="akshare not installed")
def test_cffex_future_enumerator_returns_contracts_live():
    contracts = ENUMERATORS["cffex_future"]("IC")
    assert isinstance(contracts, list)
    if contracts:  # markets closed/holiday may return empty; that's allowed
        c = contracts[0]
        assert c.exchange == "CFFEX" and c.contract_code and c.instrument_type == "future"
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `pytest tests/test_hedging_universe.py::test_cffex_future_enumerator_returns_contracts_live -v`
Expected: FAIL with `ImportError: cannot import name 'ENUMERATORS'` (or skip if AKShare missing — in that case temporarily install or accept the skip and proceed; the unit-tested logic does not depend on this).

- [ ] **Step 4: Implement the adapters + registry**

Append to `backend/app/services/hedging_universe.py`. Each adapter imports AKShare lazily, calls the function confirmed in Step 1, and normalizes into `EnumeratedContract`. Replace the marked call lines with the confirmed function names/columns; the normalization shape stays.

```python
import datetime as _dt


def _as_date(value) -> date | None:
    if value in (None, "", "nan"):
        return None
    if isinstance(value, date):
        return value
    text = str(value)[:10].replace("/", "-")
    try:
        return _dt.date.fromisoformat(text)
    except ValueError:
        return None


def _as_float(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def enumerate_cffex_futures(series_root: str) -> list[EnumeratedContract]:
    """CFFEX index futures for a series (IF/IC/IM/IH).

    AKShare function confirmed in Task 5 Step 1: <record here>.
    """
    import akshare as ak

    df = ak.futures_zh_realtime(symbol=series_root)  # CONFIRM in Step 1
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        code = str(r.get("symbol") or r.get("代码") or "").upper()
        if not code or not code.startswith(series_root):
            continue
        out.append(EnumeratedContract(
            family="index_future", series_root=series_root, exchange="CFFEX",
            contract_code=code, instrument_type="future",
            last_price=_as_float(r.get("trade") or r.get("最新价")),
            akshare_symbol=code,
        ))
    return out


def enumerate_cffex_options(series_root: str) -> list[EnumeratedContract]:
    """CFFEX index options (IO/MO/HO). AKShare fn confirmed in Step 1."""
    import akshare as ak

    df = ak.option_cffex_zh_spot(symbol=series_root)  # CONFIRM in Step 1
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        code = str(r.get("instrument") or r.get("合约代码") or "").upper()
        if not code:
            continue
        out.append(EnumeratedContract(
            family="index_option", series_root=series_root, exchange="CFFEX",
            contract_code=code, instrument_type="option",
            option_type="C" if "看涨" in str(r) or str(r.get("type")) == "C" else "P",
            strike=_as_float(r.get("strike") or r.get("行权价")),
            expiry=_as_date(r.get("expire") or r.get("到期日")),
            last_price=_as_float(r.get("last") or r.get("最新价")),
            akshare_symbol=code,
        ))
    return out


def enumerate_etf_options(series_root: str) -> list[EnumeratedContract]:
    """SSE/SZSE ETF options for an ETF root (510300/510500/...).

    AKShare fn confirmed in Step 1. Exchange is SSE for 51xxxx, SZSE for 159xxx.
    """
    import akshare as ak

    exchange = "SZSE" if series_root.startswith("159") else "SSE"
    df = ak.option_finance_board(symbol=series_root)  # CONFIRM in Step 1
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        code = str(r.get("合约编码") or r.get("instrument") or "").strip()
        if not code:
            continue
        out.append(EnumeratedContract(
            family="etf_option", series_root=series_root, exchange=exchange,
            contract_code=code, instrument_type="option",
            option_type="C" if str(r.get("方向") or r.get("type")).upper().startswith(("C", "看涨", "认购")) else "P",
            strike=_as_float(r.get("行权价") or r.get("strike")),
            expiry=_as_date(r.get("到期日") or r.get("expire")),
            last_price=_as_float(r.get("最新价") or r.get("last")),
            akshare_symbol=code,
        ))
    return out


def enumerate_commodity_futures(series_root: str) -> list[EnumeratedContract]:
    """Commodity futures for a product code. AKShare fn confirmed in Step 1."""
    import akshare as ak

    df = ak.futures_zh_realtime(symbol=series_root)  # CONFIRM in Step 1
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        code = str(r.get("symbol") or r.get("代码") or "").upper()
        if not code:
            continue
        out.append(EnumeratedContract(
            family="commodity_future", series_root=series_root,
            exchange=str(r.get("exchange") or "").upper() or "UNKNOWN",
            contract_code=code, instrument_type="future",
            last_price=_as_float(r.get("trade") or r.get("最新价")),
            akshare_symbol=code,
        ))
    return out


def enumerate_commodity_options(series_root: str) -> list[EnumeratedContract]:
    """Commodity options for a product code. AKShare fn confirmed in Step 1."""
    import akshare as ak

    df = ak.option_commodity_contract_sina(symbol=series_root)  # CONFIRM in Step 1
    out: list[EnumeratedContract] = []
    for _, r in df.iterrows():
        code = str(r.get("contract") or r.get("合约") or "").upper()
        if not code:
            continue
        out.append(EnumeratedContract(
            family="commodity_option", series_root=series_root,
            exchange=str(r.get("exchange") or "").upper() or "UNKNOWN",
            contract_code=code, instrument_type="option",
            option_type="C" if str(r.get("type")).upper().startswith("C") else "P",
            strike=_as_float(r.get("strike") or r.get("行权价")),
            expiry=_as_date(r.get("expire") or r.get("到期日")),
            akshare_symbol=code,
        ))
    return out


ENUMERATORS: dict[str, Callable[[str], list[EnumeratedContract]]] = {
    "cffex_future": enumerate_cffex_futures,
    "cffex_option": enumerate_cffex_options,
    "etf_option": enumerate_etf_options,
    "commodity_future": enumerate_commodity_futures,
    "commodity_option": enumerate_commodity_options,
}
```

- [ ] **Step 5: Run the smoke test (or accept skip)**

Run: `pytest tests/test_hedging_universe.py -v`
Expected: resolver tests pass; the smoke test passes if AKShare is installed and a market source responds, otherwise it is skipped. If it fails on a column/function mismatch, adjust the adapter per the real AKShare output from Step 1.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/hedging_universe.py tests/test_hedging_universe.py
git commit -m "feat(hedging): AKShare enumerator adapters + registry"
```

---

## Task 6: In-scope underlyings + loader task (queue/execute, outage-safe)

**Files:**
- Modify: `backend/app/services/hedging_loader.py`
- Test: `tests/test_hedging_loader.py` (append)

- [ ] **Step 1: Write the failing test** (monkeypatch enumerators — no AKShare)

```python
# append to tests/test_hedging_loader.py
from app.models import Portfolio, Position, TaskRun, TaskStatus
from app.services.hedging_universe import EnumeratedContract


def _open_position(session, underlying):
    pf = Portfolio(name=f"pf-{underlying.symbol}", base_currency="CNY")
    session.add(pf)
    session.flush()
    pos = Position(
        portfolio_id=pf.id, underlying_id=underlying.id, underlying=underlying.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    )
    session.add(pos)
    session.flush()
    return pos


def test_list_in_scope_includes_open_positions_and_mapped(session):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)
    orphan = _underlying(session, "000300.SH")
    session.add(HedgeMapEntry(
        underlying_id=orphan.id, exchange="CFFEX", contract_code="IF2406",
        family="index_future", series_root="IF", instrument_type="future",
    ))
    session.flush()
    symbols = {u.symbol for u in hedging_loader.list_in_scope_underlyings(session)}
    assert symbols == {"000905.SH", "000300.SH"}


def test_execute_load_populates_catalog_and_reconciles(session, monkeypatch):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)

    def fake_future(series_root):
        return [EnumeratedContract(
            family="index_future", series_root=series_root, exchange="CFFEX",
            contract_code=f"{series_root}2406", instrument_type="future",
            last_price=5600.0,
        )]

    monkeypatch.setitem(hedging_loader_enumerators(), "cffex_future", fake_future)
    # ETF option enumerator returns nothing (still counts as success)
    monkeypatch.setitem(hedging_loader_enumerators(), "etf_option", lambda s: [])

    task = hedging_loader.queue_hedge_load(session)
    session.commit()
    hedging_loader.execute_hedge_load_task(task.id, session_factory=_factory(session))

    session.expire_all()
    codes = {r.contract_code for r in session.query(HedgeInstrument).all()}
    assert "IC2406" in codes
    done = session.get(TaskRun, task.id)
    assert done.status == TaskStatus.COMPLETED.value
    assert done.progress_current == done.progress_total > 0
    assert done.result_payload["families"]


def test_concurrent_load_is_rejected(session):
    hedging_loader.queue_hedge_load(session)
    session.commit()
    try:
        hedging_loader.queue_hedge_load(session)
        raise AssertionError("expected HedgeLoadInProgress")
    except hedging_loader.HedgeLoadInProgress as exc:
        assert exc.task_id is not None


def test_family_error_does_not_expire_existing(session, monkeypatch):
    held = _underlying(session, "000905.SH")
    _open_position(session, held)
    hedging_loader._upsert_catalog(session, [_contract("IC2406")], held.id)
    session.commit()

    def boom(series_root):
        raise RuntimeError("akshare down")

    monkeypatch.setitem(hedging_loader_enumerators(), "cffex_future", boom)
    monkeypatch.setitem(hedging_loader_enumerators(), "etf_option", lambda s: [])
    task = hedging_loader.queue_hedge_load(session)
    session.commit()
    hedging_loader.execute_hedge_load_task(task.id, session_factory=_factory(session))

    session.expire_all()
    row = session.query(HedgeInstrument).filter_by(contract_code="IC2406").one()
    assert row.status == "live"  # NOT expired despite the failed fetch
    done = session.get(TaskRun, task.id)
    assert done.status == TaskStatus.COMPLETED_WITH_ERRORS.value
    assert done.result_payload["errors"]


# --- test helpers ---
def hedging_loader_enumerators():
    from app.services import hedging_universe
    return hedging_universe.ENUMERATORS


def _factory(session):
    """Return a callable yielding the SAME session the test inspects."""
    class _S:
        def __call__(self):
            return session
    # prevent execute_hedge_load_task from closing the test's session
    session.close = lambda: None  # type: ignore[assignment]
    return _S()
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_loader.py -v`
Expected: FAIL with `AttributeError: module 'app.services.hedging_loader' has no attribute 'queue_hedge_load'`.

- [ ] **Step 3: Implement queue/execute + in-scope extraction**

Append to `backend/app/services/hedging_loader.py`:

```python
from sqlalchemy.orm import sessionmaker

from .. import database
from ..models import (
    HedgeInstrument as _HI,  # noqa: F401  (already imported above; keep single import)
)
from ..models import Position, TaskKind, TaskRun, TaskStatus, Underlying
from .task_runner import (
    ACTIVE_TASK_STATUSES,
    mark_task_finished,
    mark_task_running,
    update_task_progress,
)


class HedgeLoadInProgress(Exception):
    def __init__(self, task_id: int):
        super().__init__(f"Hedge load already running: {task_id}")
        self.task_id = task_id


def list_in_scope_underlyings(session: Session) -> list[Underlying]:
    open_ids = {
        row[0]
        for row in session.query(Position.underlying_id)
        .filter(Position.status == "open", Position.underlying_id.isnot(None))
        .distinct()
    }
    mapped_ids = {
        row[0] for row in session.query(HedgeMapEntry.underlying_id).distinct()
    }
    ids = open_ids | mapped_ids
    if not ids:
        return []
    return (
        session.query(Underlying)
        .filter(Underlying.id.in_(ids))
        .order_by(Underlying.symbol)
        .all()
    )


def queue_hedge_load(session: Session) -> TaskRun:
    existing = (
        session.query(TaskRun)
        .filter(
            TaskRun.kind == TaskKind.HEDGE_LOAD.value,
            TaskRun.status.in_(ACTIVE_TASK_STATUSES),
        )
        .first()
    )
    if existing is not None:
        raise HedgeLoadInProgress(existing.id)
    task = TaskRun(
        kind=TaskKind.HEDGE_LOAD.value,
        status=TaskStatus.QUEUED.value,
        progress_current=0,
        progress_total=0,
        message="Queued hedge instrument load",
    )
    session.add(task)
    session.flush()
    return task


def execute_hedge_load_task(
    task_id: int, session_factory: sessionmaker | None = None
) -> None:
    session = (session_factory or database.SessionLocal)()
    try:
        _execute(session, task_id)
    finally:
        session.close()


def _execute(session: Session, task_id: int) -> None:
    from .hedging_universe import ENUMERATORS, resolve_families

    underlyings = list_in_scope_underlyings(session)
    work: list[tuple[Underlying, object]] = []
    unresolvable: list[str] = []
    for u in underlyings:
        specs = resolve_families(u.symbol, u.asset_class)
        if not specs:
            unresolvable.append(u.symbol)
            continue
        for spec in specs:
            work.append((u, spec))

    mark_task_running(
        session, task_id, message="Loading hedge instruments", total=len(work)
    )
    session.commit()

    summary: dict = {"families": [], "unresolvable": unresolvable, "errors": []}
    done = 0
    for u, spec in work:
        enumerator = ENUMERATORS.get(spec.enumerator_key)
        try:
            contracts = enumerator(spec.series_root) if enumerator else []
            seen = _upsert_catalog(session, contracts, u.id)
            _expire_missing(session, u.id, spec.family, seen)
            summary["families"].append(
                {"underlying": u.symbol, "family": spec.family, "count": len(contracts)}
            )
        except Exception as exc:  # noqa: BLE001 — per-family isolation by design
            summary["errors"].append(
                {"underlying": u.symbol, "family": spec.family, "error": str(exc)}
            )
        done += 1
        update_task_progress(
            session, task_id, current=done,
            message=f"{u.symbol} · {spec.family}",
        )
        session.commit()

    reconcile_map(session)
    task = session.get(TaskRun, task_id)
    task.result_payload = summary
    status = (
        TaskStatus.COMPLETED_WITH_ERRORS.value
        if summary["errors"]
        else TaskStatus.COMPLETED.value
    )
    mark_task_finished(
        session, task_id, status=status, message="Hedge instrument load complete"
    )
    session.commit()
```

> Clean up the duplicate `HedgeInstrument` import: it is already imported at the top of the file from Task 4 — remove the `as _HI` alias line and just rely on the existing import. Keep one import for `HedgeMapEntry` too.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_loader.py -v`
Expected: all passing (7 tests total across Tasks 4 + 6).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_loader.py tests/test_hedging_loader.py
git commit -m "feat(hedging): async loader task (queue/execute, outage-safe, 409 guard)"
```

---

## Task 7: Domain operations (overview, list, mark/unmark, map, purge)

**Files:**
- Create: `backend/app/services/domains/hedging.py`
- Test: `tests/test_hedging_domain.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_domain.py
from __future__ import annotations

from datetime import date

from app.models import HedgeInstrument, HedgeMapEntry, Portfolio, Position, Underlying
from app.services.domains import hedging as hedging_domain


def _underlying(session, symbol="000905.SH"):
    u = Underlying(symbol=symbol, asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    return u


def _instrument(session, u, code="IC2406", status="live", family="index_future"):
    row = HedgeInstrument(
        underlying_id=u.id, family=family, series_root="IC", exchange="CFFEX",
        contract_code=code, instrument_type="future", status=status,
        expiry=date(2026, 6, 21), last_price=5600.0,
    )
    session.add(row)
    session.flush()
    return row


def test_mark_creates_entry_with_snapshot_and_active_status(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    created = hedging_domain.mark(session, [inst.id], actor="desk_user")
    session.flush()
    assert len(created) == 1
    entry = session.query(HedgeMapEntry).one()
    assert entry.contract_code == "IC2406"
    assert entry.series_root == "IC"
    assert entry.reconcile_status == "active"
    assert entry.marked_by == "desk_user"


def test_mark_is_idempotent(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    assert session.query(HedgeMapEntry).count() == 1


def test_mark_expired_instrument_lands_stale(session):
    u = _underlying(session)
    inst = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    assert session.query(HedgeMapEntry).one().reconcile_status == "stale"


def test_unmark_deletes_by_instrument_id(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    hedging_domain.unmark(session, instrument_ids=[inst.id])
    session.flush()
    assert session.query(HedgeMapEntry).count() == 0


def test_list_instruments_annotates_allowed(session):
    u = _underlying(session)
    a = _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    hedging_domain.mark(session, [a.id], actor="a")
    session.flush()
    rows = hedging_domain.list_instruments(session, underlying_id=u.id, family="index_future")
    allowed = {r["contract_code"]: r["allowed"] for r in rows}
    assert allowed == {"IC2406": True, "IC2409": False}


def test_list_instruments_filters(session):
    u = _underlying(session)
    _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    rows = hedging_domain.list_instruments(
        session, underlying_id=u.id, family="index_future", search="2409"
    )
    assert [r["contract_code"] for r in rows] == ["IC2409"]


def test_underlyings_overview_counts(session):
    u = _underlying(session)
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    a = _instrument(session, u, code="IC2406")
    _instrument(session, u, code="IC2409")
    stale = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [a.id, stale.id], actor="a")
    session.flush()
    overview = hedging_domain.underlyings_overview(session)
    row = next(r for r in overview if r["symbol"] == u.symbol)
    fam = next(f for f in row["families"] if f["family"] == "index_future")
    assert fam["allowed"] == 2 and fam["total"] >= 2
    assert row["stale_count"] == 1
    assert row["unresolvable"] is False


def test_get_map_groups_by_underlying(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    grouped = hedging_domain.get_map(session)
    assert grouped[0]["underlying_id"] == u.id
    assert grouped[0]["entries"][0]["contract_code"] == "IC2406"


def test_purge_stale_removes_only_stale(session):
    u = _underlying(session)
    live = _instrument(session, u, code="IC2406")
    stale = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [live.id, stale.id], actor="a")
    session.flush()
    removed = hedging_domain.purge_stale(session, underlying_id=u.id)
    session.flush()
    assert removed == 1
    assert {e.contract_code for e in session.query(HedgeMapEntry).all()} == {"IC2406"}
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_domain.py -v`
Expected: FAIL with `ModuleNotFoundError: app.services.domains.hedging`.

- [ ] **Step 3: Implement the domain**

```python
# backend/app/services/domains/hedging.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import HedgeInstrument, HedgeMapEntry, Position, Underlying
from ..hedging_universe import resolve_families


def _allowed_keys(session: Session, underlying_id: int) -> set[tuple[str, str]]:
    return {
        (e.exchange, e.contract_code)
        for e in session.query(
            HedgeMapEntry.exchange, HedgeMapEntry.contract_code
        ).filter(HedgeMapEntry.underlying_id == underlying_id)
    }


def list_instruments(
    session: Session,
    *,
    underlying_id: int,
    family: str | None = None,
    instrument_type: str | None = None,
    option_type: str | None = None,
    strike_min: float | None = None,
    strike_max: float | None = None,
    search: str | None = None,
    allowed_only: bool = False,
    status: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[dict[str, Any]]:
    q = session.query(HedgeInstrument).filter(
        HedgeInstrument.underlying_id == underlying_id
    )
    if family:
        q = q.filter(HedgeInstrument.family == family)
    if instrument_type:
        q = q.filter(HedgeInstrument.instrument_type == instrument_type)
    if option_type:
        q = q.filter(HedgeInstrument.option_type == option_type)
    if strike_min is not None:
        q = q.filter(HedgeInstrument.strike >= strike_min)
    if strike_max is not None:
        q = q.filter(HedgeInstrument.strike <= strike_max)
    if status:
        q = q.filter(HedgeInstrument.status == status)
    if search:
        q = q.filter(HedgeInstrument.contract_code.ilike(f"%{search}%"))
    q = q.order_by(HedgeInstrument.expiry, HedgeInstrument.strike, HedgeInstrument.contract_code)
    rows = q.offset(offset).limit(min(max(1, limit), 5000)).all()
    allowed = _allowed_keys(session, underlying_id)
    result = [_instrument_dict(r, allowed) for r in rows]
    if allowed_only:
        result = [r for r in result if r["allowed"]]
    return result


def _instrument_dict(r: HedgeInstrument, allowed: set[tuple[str, str]]) -> dict[str, Any]:
    return {
        "id": r.id,
        "underlying_id": r.underlying_id,
        "family": r.family,
        "series_root": r.series_root,
        "exchange": r.exchange,
        "contract_code": r.contract_code,
        "instrument_type": r.instrument_type,
        "option_type": r.option_type,
        "strike": r.strike,
        "expiry": r.expiry.isoformat() if r.expiry else None,
        "multiplier": r.multiplier,
        "last_price": r.last_price,
        "status": r.status,
        "allowed": (r.exchange, r.contract_code) in allowed,
    }


def mark(session: Session, instrument_ids: list[int], *, actor: str | None = None) -> list[HedgeMapEntry]:
    created: list[HedgeMapEntry] = []
    now = datetime.utcnow()
    for inst in (
        session.query(HedgeInstrument)
        .filter(HedgeInstrument.id.in_(instrument_ids))
        .all()
    ):
        existing = (
            session.query(HedgeMapEntry)
            .filter(
                HedgeMapEntry.underlying_id == inst.underlying_id,
                HedgeMapEntry.exchange == inst.exchange,
                HedgeMapEntry.contract_code == inst.contract_code,
            )
            .one_or_none()
        )
        if existing is not None:
            continue
        entry = HedgeMapEntry(
            underlying_id=inst.underlying_id,
            exchange=inst.exchange,
            contract_code=inst.contract_code,
            family=inst.family,
            series_root=inst.series_root,
            instrument_type=inst.instrument_type,
            option_type=inst.option_type,
            strike=inst.strike,
            expiry=inst.expiry,
            reconcile_status="active" if inst.status == "live" else "stale",
            marked_by=actor,
            marked_at=now,
        )
        session.add(entry)
        created.append(entry)
    return created


def unmark(
    session: Session,
    *,
    instrument_ids: list[int] | None = None,
    map_entry_ids: list[int] | None = None,
) -> int:
    removed = 0
    if map_entry_ids:
        removed += (
            session.query(HedgeMapEntry)
            .filter(HedgeMapEntry.id.in_(map_entry_ids))
            .delete(synchronize_session=False)
        )
    if instrument_ids:
        keys = [
            (i.underlying_id, i.exchange, i.contract_code)
            for i in session.query(HedgeInstrument)
            .filter(HedgeInstrument.id.in_(instrument_ids))
            .all()
        ]
        for uid, exch, code in keys:
            removed += (
                session.query(HedgeMapEntry)
                .filter(
                    HedgeMapEntry.underlying_id == uid,
                    HedgeMapEntry.exchange == exch,
                    HedgeMapEntry.contract_code == code,
                )
                .delete(synchronize_session=False)
            )
    return removed


def get_map(session: Session, *, underlying_id: int | None = None) -> list[dict[str, Any]]:
    q = session.query(HedgeMapEntry)
    if underlying_id is not None:
        q = q.filter(HedgeMapEntry.underlying_id == underlying_id)
    grouped: dict[int, dict[str, Any]] = {}
    for e in q.order_by(HedgeMapEntry.underlying_id, HedgeMapEntry.contract_code).all():
        bucket = grouped.setdefault(
            e.underlying_id, {"underlying_id": e.underlying_id, "entries": []}
        )
        bucket["entries"].append({
            "id": e.id,
            "exchange": e.exchange,
            "contract_code": e.contract_code,
            "family": e.family,
            "series_root": e.series_root,
            "instrument_type": e.instrument_type,
            "option_type": e.option_type,
            "strike": e.strike,
            "expiry": e.expiry.isoformat() if e.expiry else None,
            "reconcile_status": e.reconcile_status,
        })
    return list(grouped.values())


def purge_stale(session: Session, *, underlying_id: int) -> int:
    return (
        session.query(HedgeMapEntry)
        .filter(
            HedgeMapEntry.underlying_id == underlying_id,
            HedgeMapEntry.reconcile_status == "stale",
        )
        .delete(synchronize_session=False)
    )


def underlyings_overview(session: Session) -> list[dict[str, Any]]:
    open_ids = {
        row[0]
        for row in session.query(Position.underlying_id)
        .filter(Position.status == "open", Position.underlying_id.isnot(None))
        .distinct()
    }
    mapped_ids = {row[0] for row in session.query(HedgeMapEntry.underlying_id).distinct()}
    ids = open_ids | mapped_ids
    if not ids:
        return []
    underlyings = (
        session.query(Underlying)
        .filter(Underlying.id.in_(ids))
        .order_by(Underlying.symbol)
        .all()
    )
    out: list[dict[str, Any]] = []
    for u in underlyings:
        instruments = (
            session.query(HedgeInstrument)
            .filter(HedgeInstrument.underlying_id == u.id)
            .all()
        )
        allowed = _allowed_keys(session, u.id)
        families: dict[str, dict[str, int]] = {}
        last_loaded: datetime | None = None
        for inst in instruments:
            fam = families.setdefault(inst.family, {"total": 0, "allowed": 0})
            fam["total"] += 1
            if (inst.exchange, inst.contract_code) in allowed:
                fam["allowed"] += 1
            if last_loaded is None or (inst.loaded_at and inst.loaded_at > last_loaded):
                last_loaded = inst.loaded_at
        stale_count = (
            session.query(HedgeMapEntry)
            .filter(
                HedgeMapEntry.underlying_id == u.id,
                HedgeMapEntry.reconcile_status == "stale",
            )
            .count()
        )
        out.append({
            "underlying_id": u.id,
            "symbol": u.symbol,
            "display_name": u.display_name,
            "asset_class": u.asset_class,
            "unresolvable": resolve_families(u.symbol, u.asset_class) == [],
            "last_loaded_at": last_loaded.isoformat() if last_loaded else None,
            "stale_count": stale_count,
            "families": [
                {"family": k, "total": v["total"], "allowed": v["allowed"]}
                for k, v in sorted(families.items())
            ],
        })
    return out
```

- [ ] **Step 4: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_domain.py -v`
Expected: all passing (10 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/hedging.py tests/test_hedging_domain.py
git commit -m "feat(hedging): domain ops (overview, list, mark/unmark, map, purge)"
```

---

## Task 8: API schemas + endpoints

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/main.py`
- Test: `tests/test_hedging_api.py`

- [ ] **Step 1: Write the failing test** (uses the `client` fixture; monkeypatch the loader so no AKShare / no real threadpool)

```python
# tests/test_hedging_api.py
from __future__ import annotations

from app.models import HedgeInstrument, Portfolio, Position, Underlying
from app import database


def _seed(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    session.add(Position(
        portfolio_id=pf.id, underlying_id=u.id, underlying=u.symbol,
        product_type="SnowballOption", quantity=1.0, entry_price=0.0, status="open",
    ))
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC",
        exchange="CFFEX", contract_code="IC2406", instrument_type="future",
        status="live", last_price=5600.0,
    ))
    session.commit()
    return u


def test_underlyings_endpoint_lists_in_scope(client, session):
    _seed(session)
    resp = client.get("/api/hedging/underlyings")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["symbol"] == "000905.SH"
    assert body[0]["families"][0]["family"] == "index_future"


def test_instruments_endpoint_annotates_allowed(client, session):
    u = _seed(session)
    resp = client.get(f"/api/hedging/instruments?underlying_id={u.id}&family=index_future")
    assert resp.status_code == 200
    rows = resp.json()
    assert rows[0]["contract_code"] == "IC2406"
    assert rows[0]["allowed"] is False


def test_mark_then_map_then_unmark(client, session):
    u = _seed(session)
    inst_id = session.query(HedgeInstrument).one().id
    assert client.post("/api/hedging/map/mark", json={"instrument_ids": [inst_id]}).status_code == 200
    mp = client.get("/api/hedging/map").json()
    assert mp[0]["entries"][0]["contract_code"] == "IC2406"
    assert client.post("/api/hedging/map/unmark", json={"instrument_ids": [inst_id]}).status_code == 200
    assert client.get("/api/hedging/map").json() == []


def test_load_returns_task_id_and_rejects_concurrent(client, session, monkeypatch):
    _seed(session)
    # Do not actually run the threadpool job in the test.
    monkeypatch.setattr(
        "app.services.task_runner.submit_async_task", lambda *a, **k: None
    )
    first = client.post("/api/hedging/instruments/load")
    assert first.status_code == 200
    task_id = first.json()["task_id"]
    second = client.post("/api/hedging/instruments/load")
    assert second.status_code == 409
    assert second.json()["detail"]["in_flight_task_id"] == task_id
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `pytest tests/test_hedging_api.py -v`
Expected: FAIL with 404s (endpoints not registered).

- [ ] **Step 3: Add schemas**

Append to `backend/app/schemas.py`:

```python
class HedgeFamilyCount(BaseModel):
    family: str
    total: int
    allowed: int


class HedgeUnderlyingOut(BaseModel):
    underlying_id: int
    symbol: str
    display_name: str | None = None
    asset_class: str
    unresolvable: bool
    last_loaded_at: str | None = None
    stale_count: int
    families: list[HedgeFamilyCount]


class HedgeInstrumentOut(BaseModel):
    id: int
    underlying_id: int
    family: str
    series_root: str
    exchange: str
    contract_code: str
    instrument_type: str
    option_type: str | None = None
    strike: float | None = None
    expiry: str | None = None
    multiplier: float | None = None
    last_price: float | None = None
    status: str
    allowed: bool


class HedgeMapEntryOut(BaseModel):
    id: int
    exchange: str
    contract_code: str
    family: str
    series_root: str
    instrument_type: str
    option_type: str | None = None
    strike: float | None = None
    expiry: str | None = None
    reconcile_status: str


class HedgeMapGroupOut(BaseModel):
    underlying_id: int
    entries: list[HedgeMapEntryOut]


class HedgeMarkRequest(BaseModel):
    instrument_ids: list[int]


class HedgeUnmarkRequest(BaseModel):
    instrument_ids: list[int] | None = None
    map_entry_ids: list[int] | None = None


class HedgeLoadStartedOut(BaseModel):
    task_id: int


class HedgeLoadStatusOut(BaseModel):
    task_id: int
    status: str
    progress_current: int
    progress_total: int
    message: str | None = None
    summary: dict | None = None
```

- [ ] **Step 4: Register endpoints inside `create_app`**

In `backend/app/main.py`, inside `create_app` (alongside the other `@app.*` routes, e.g. near the risk/tasks endpoints), add:

```python
    @app.get("/api/hedging/underlyings", response_model=list[HedgeUnderlyingOut])
    def hedging_underlyings(session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        return hedging_domain.underlyings_overview(session)

    @app.get("/api/hedging/instruments", response_model=list[HedgeInstrumentOut])
    def hedging_instruments(
        underlying_id: int,
        family: str | None = None,
        instrument_type: str | None = None,
        option_type: str | None = None,
        strike_min: float | None = None,
        strike_max: float | None = None,
        search: str | None = None,
        allowed_only: bool = False,
        status: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        session: Session = Depends(get_db),
    ):
        from .services.domains import hedging as hedging_domain
        return hedging_domain.list_instruments(
            session, underlying_id=underlying_id, family=family,
            instrument_type=instrument_type, option_type=option_type,
            strike_min=strike_min, strike_max=strike_max, search=search,
            allowed_only=allowed_only, status=status, limit=limit, offset=offset,
        )

    @app.post("/api/hedging/map/mark", response_model=list[HedgeInstrumentOut])
    def hedging_mark(payload: HedgeMarkRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        hedging_domain.mark(session, payload.instrument_ids, actor="desk_user")
        session.commit()
        if not payload.instrument_ids:
            return []
        first = session.get(HedgeInstrument, payload.instrument_ids[0])
        if first is None:
            return []
        return hedging_domain.list_instruments(
            session, underlying_id=first.underlying_id
        )

    @app.post("/api/hedging/map/unmark", response_model=dict)
    def hedging_unmark(payload: HedgeUnmarkRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        removed = hedging_domain.unmark(
            session,
            instrument_ids=payload.instrument_ids,
            map_entry_ids=payload.map_entry_ids,
        )
        session.commit()
        return {"removed": removed}

    @app.get("/api/hedging/map", response_model=list[HedgeMapGroupOut])
    def hedging_map(underlying_id: int | None = None, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        return hedging_domain.get_map(session, underlying_id=underlying_id)

    @app.post("/api/hedging/map/purge-stale", response_model=dict)
    def hedging_purge_stale(underlying_id: int, session: Session = Depends(get_db)):
        from .services.domains import hedging as hedging_domain
        removed = hedging_domain.purge_stale(session, underlying_id=underlying_id)
        session.commit()
        return {"removed": removed}

    @app.post("/api/hedging/instruments/load", response_model=HedgeLoadStartedOut)
    def hedging_load(session: Session = Depends(get_db)):
        from .services import hedging_loader
        from .services.task_runner import submit_async_task
        try:
            task = hedging_loader.queue_hedge_load(session)
        except hedging_loader.HedgeLoadInProgress as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "A hedge instrument load is already running.",
                    "in_flight_task_id": exc.task_id,
                },
            )
        session.commit()
        submit_async_task(
            hedging_loader.execute_hedge_load_task,
            task.id,
            database.SessionLocal,
        )
        return {"task_id": task.id}

    @app.get("/api/hedging/instruments/load/{task_id}", response_model=HedgeLoadStatusOut)
    def hedging_load_status(task_id: int, session: Session = Depends(get_db)):
        task = session.get(TaskRun, task_id)
        if task is None or task.kind != TaskKind.HEDGE_LOAD.value:
            raise HTTPException(status_code=404, detail="Hedge load not found")
        return {
            "task_id": task.id,
            "status": task.status,
            "progress_current": task.progress_current,
            "progress_total": task.progress_total,
            "message": task.message,
            "summary": task.result_payload,
        }
```

- [ ] **Step 5: Ensure imports in `main.py`**

Confirm the new schema names (`HedgeUnderlyingOut`, `HedgeInstrumentOut`, `HedgeMapGroupOut`, `HedgeMarkRequest`, `HedgeUnmarkRequest`, `HedgeLoadStartedOut`, `HedgeLoadStatusOut`) are imported from `.schemas`, and that `HedgeInstrument`, `TaskRun`, `TaskKind` are imported from `.models` and `database` is in scope (it is used elsewhere in `main.py`). Add any missing names to the existing import blocks.

- [ ] **Step 6: Run the test and confirm it passes**

Run: `pytest tests/test_hedging_api.py -v`
Expected: 4 passed.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_hedging_api.py
git commit -m "feat(hedging): REST endpoints + schemas for catalog/map/load"
```

---

## Task 9: Frontend types + presentational page

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/routes/Hedging.tsx`, `frontend/src/routes/Hedging.test.tsx`

- [ ] **Step 1: Add types**

In `frontend/src/types.ts`: add `'hedging'` to the `Route` union (find the `export type Route = ...` line and add the member), and append:

```typescript
export type HedgeFamilyCount = { family: string; total: number; allowed: number };

export type HedgeUnderlying = {
  underlying_id: number;
  symbol: string;
  display_name: string | null;
  asset_class: string;
  unresolvable: boolean;
  last_loaded_at: string | null;
  stale_count: number;
  families: HedgeFamilyCount[];
};

export type HedgeInstrument = {
  id: number;
  underlying_id: number;
  family: string;
  series_root: string;
  exchange: string;
  contract_code: string;
  instrument_type: string;
  option_type: string | null;
  strike: number | null;
  expiry: string | null;
  multiplier: number | null;
  last_price: number | null;
  status: string;
  allowed: boolean;
};
```

- [ ] **Step 2: Write the failing component test**

```tsx
// frontend/src/routes/Hedging.test.tsx
import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Hedging } from './Hedging';
import type { HedgeInstrument, HedgeUnderlying } from '../types';

const underlying: HedgeUnderlying = {
  underlying_id: 1, symbol: '000905.SH', display_name: 'CSI 500', asset_class: 'index',
  unresolvable: false, last_loaded_at: '2026-06-02T09:12:00', stale_count: 1,
  families: [{ family: 'index_future', total: 3, allowed: 1 }],
};

const instruments: HedgeInstrument[] = [
  { id: 10, underlying_id: 1, family: 'index_future', series_root: 'IC', exchange: 'CFFEX',
    contract_code: 'IC2406', instrument_type: 'future', option_type: null, strike: null,
    expiry: '2026-06-21', multiplier: 200, last_price: 5600, status: 'live', allowed: true },
  { id: 11, underlying_id: 1, family: 'index_future', series_root: 'IC', exchange: 'CFFEX',
    contract_code: 'IC2409', instrument_type: 'future', option_type: null, strike: null,
    expiry: '2026-09-20', multiplier: 200, last_price: 5588, status: 'live', allowed: false },
];

function setup(overrides = {}) {
  const props = {
    underlyings: [underlying], selectedUnderlyingId: 1, selectedFamily: 'index_future',
    instruments, loading: false, loadInProgress: false, loadMessage: null,
    onSelectUnderlying: vi.fn(), onSelectFamily: vi.fn(), onLoad: vi.fn(),
    onMark: vi.fn(), onUnmark: vi.fn(), onPurgeStale: vi.fn(), ...overrides,
  };
  render(<Hedging {...props} />);
  return props;
}

describe('Hedging', () => {
  it('renders the underlying rail with allowed/total counts', () => {
    setup();
    expect(screen.getByText('000905.SH')).toBeTruthy();
    expect(screen.getByText(/1\/3/)).toBeTruthy();
  });

  it('shows the attention banner when stale marks exist', () => {
    setup();
    expect(screen.getByText(/no longer live/i)).toBeTruthy();
  });

  it('calls onMark with selected instrument ids', () => {
    const props = setup();
    fireEvent.click(screen.getByLabelText('select IC2409'));
    fireEvent.click(screen.getByText('Mark'));
    expect(props.onMark).toHaveBeenCalledWith([11]);
  });

  it('calls onLoad when the load button is clicked', () => {
    const props = setup();
    fireEvent.click(screen.getByText(/Load from AKShare/i));
    expect(props.onLoad).toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run it and confirm it fails**

Run: `cd frontend && npx vitest run src/routes/Hedging.test.tsx`
Expected: FAIL — cannot resolve `./Hedging`.

- [ ] **Step 4: Implement the presentational component**

```tsx
// frontend/src/routes/Hedging.tsx
import { useState } from 'react';
import type { HedgeInstrument, HedgeUnderlying } from '../types';

type Props = {
  underlyings: HedgeUnderlying[];
  selectedUnderlyingId: number | null;
  selectedFamily: string | null;
  instruments: HedgeInstrument[];
  loading: boolean;
  loadInProgress: boolean;
  loadMessage: string | null;
  onSelectUnderlying: (id: number) => void;
  onSelectFamily: (family: string) => void;
  onLoad: () => void;
  onMark: (instrumentIds: number[]) => void;
  onUnmark: (instrumentIds: number[]) => void;
  onPurgeStale: (underlyingId: number) => void;
};

export function Hedging(props: Props) {
  const {
    underlyings, selectedUnderlyingId, selectedFamily, instruments,
    loadInProgress, loadMessage, onSelectUnderlying, onSelectFamily,
    onLoad, onMark, onUnmark, onPurgeStale,
  } = props;
  const [selected, setSelected] = useState<Set<number>>(new Set());

  const current = underlyings.find((u) => u.underlying_id === selectedUnderlyingId) ?? null;

  const toggle = (id: number) => {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  };

  return (
    <div className="hedging">
      <header className="hedging-bar">
        <button onClick={onLoad} disabled={loadInProgress}>
          {loadInProgress ? '⟳ Loading…' : '⟳ Load from AKShare'}
        </button>
        {loadInProgress && <span className="hedging-progress">{loadMessage}</span>}
      </header>

      <div className="hedging-body">
        <nav className="hedging-rail">
          {underlyings.map((u) => {
            const fam = u.families.map((f) => `${f.allowed}/${f.total}`).join(' ');
            return (
              <button
                key={u.underlying_id}
                className={u.underlying_id === selectedUnderlyingId ? 'active' : ''}
                onClick={() => onSelectUnderlying(u.underlying_id)}
              >
                <span>{u.symbol}</span>{' '}
                <small>{fam}</small>
                {u.stale_count > 0 && <span className="badge-stale">{u.stale_count}</span>}
                {u.unresolvable && <small> ⚠ unresolvable</small>}
              </button>
            );
          })}
        </nav>

        <section className="hedging-main">
          {current && current.stale_count > 0 && (
            <div className="hedging-attn">
              ⚠ {current.stale_count} marked contracts for {current.symbol} are no longer
              live (expired/rolled).{' '}
              <button onClick={() => onPurgeStale(current.underlying_id)}>Purge stale</button>
            </div>
          )}

          <div className="hedging-tabs">
            {current?.families.map((f) => (
              <button
                key={f.family}
                className={f.family === selectedFamily ? 'active' : ''}
                onClick={() => onSelectFamily(f.family)}
              >
                {f.family}
              </button>
            ))}
          </div>

          <table className="hedging-table">
            <thead>
              <tr><th></th><th>contract</th><th>expiry</th><th>strike</th><th>C/P</th><th>last</th><th>state</th></tr>
            </thead>
            <tbody>
              {instruments.map((i) => (
                <tr key={i.id} className={i.status === 'expired' ? 'expired' : ''}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`select ${i.contract_code}`}
                      checked={selected.has(i.id)}
                      onChange={() => toggle(i.id)}
                    />
                  </td>
                  <td>{i.contract_code}</td>
                  <td>{i.expiry ?? '—'}</td>
                  <td>{i.strike ?? '—'}</td>
                  <td>{i.option_type ?? '—'}</td>
                  <td>{i.last_price ?? '—'}</td>
                  <td>{i.allowed ? <span className="badge-ok">allowed</span> : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="hedging-actions">
            <span>Selected: {selected.size}</span>
            <button onClick={() => onMark([...selected])}>Mark</button>
            <button onClick={() => onUnmark([...selected])}>Unmark</button>
          </div>
        </section>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the test and confirm it passes**

Run: `cd frontend && npx vitest run src/routes/Hedging.test.tsx`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/Hedging.tsx frontend/src/routes/Hedging.test.tsx
git commit -m "feat(hedging): presentational marking page + types"
```

---

## Task 10: Frontend container + route registration

**Files:**
- Create: `frontend/src/routes/Hedging.live.tsx`, `frontend/src/routes/Hedging.live.test.tsx`
- Modify: `frontend/src/main.tsx`

- [ ] **Step 1: Write the failing container test**

```tsx
// frontend/src/routes/Hedging.live.test.tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { HedgingLive } from './Hedging.live';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

const underlyings = [{
  underlying_id: 1, symbol: '000905.SH', display_name: 'CSI 500', asset_class: 'index',
  unresolvable: false, last_loaded_at: '2026-06-02T09:12:00', stale_count: 0,
  families: [{ family: 'index_future', total: 2, allowed: 1 }],
}];

const instruments = [{
  id: 10, underlying_id: 1, family: 'index_future', series_root: 'IC', exchange: 'CFFEX',
  contract_code: 'IC2406', instrument_type: 'future', option_type: null, strike: null,
  expiry: '2026-06-21', multiplier: 200, last_price: 5600, status: 'live', allowed: true,
}];

describe('HedgingLive', () => {
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('loads underlyings then instruments for the first underlying', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/hedging/underlyings')) return response(underlyings);
      if (url.includes('/api/hedging/instruments?')) return response(instruments);
      return response([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<HedgingLive />);
    await waitFor(() => expect(screen.getByText('000905.SH')).toBeTruthy());
    await waitFor(() => expect(screen.getByText('IC2406')).toBeTruthy());
  });

  it('POSTs to the load endpoint when Load is clicked', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/hedging/underlyings')) return response(underlyings);
      if (url.includes('/api/hedging/instruments?')) return response(instruments);
      if (url.includes('/api/hedging/instruments/load')) return response({ task_id: 7 });
      return response([]);
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<HedgingLive />);
    await waitFor(() => expect(screen.getByText('000905.SH')).toBeTruthy());
    fireEvent.click(screen.getByText(/Load from AKShare/i));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([u]) => String(u).includes('/instruments/load'))).toBe(true),
    );
  });
});
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd frontend && npx vitest run src/routes/Hedging.live.test.tsx`
Expected: FAIL — cannot resolve `./Hedging.live`.

- [ ] **Step 3: Implement the container**

```tsx
// frontend/src/routes/Hedging.live.tsx
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { HedgeInstrument, HedgeUnderlying, PageContextReporter } from '../types';
import { Hedging } from './Hedging';

type Props = { onPageContextChange?: PageContextReporter };

export function HedgingLive({ onPageContextChange }: Props) {
  const [underlyings, setUnderlyings] = useState<HedgeUnderlying[]>([]);
  const [selectedUnderlyingId, setSelectedUnderlyingId] = useState<number | null>(null);
  const [selectedFamily, setSelectedFamily] = useState<string | null>(null);
  const [instruments, setInstruments] = useState<HedgeInstrument[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadInProgress, setLoadInProgress] = useState(false);
  const [loadMessage, setLoadMessage] = useState<string | null>(null);

  useEffect(() => {
    onPageContextChange?.({ page: 'hedging', path: location.pathname } as never);
  }, [onPageContextChange]);

  const loadUnderlyings = async () => {
    const rows = await api<HedgeUnderlying[]>('/api/hedging/underlyings');
    setUnderlyings(rows);
    setLoading(false);
    if (rows.length && selectedUnderlyingId === null) {
      setSelectedUnderlyingId(rows[0].underlying_id);
      setSelectedFamily(rows[0].families[0]?.family ?? null);
    }
    return rows;
  };

  useEffect(() => { void loadUnderlyings(); }, []);

  useEffect(() => {
    if (selectedUnderlyingId === null || selectedFamily === null) return;
    const params = new URLSearchParams({
      underlying_id: String(selectedUnderlyingId),
      family: selectedFamily,
    });
    void api<HedgeInstrument[]>(`/api/hedging/instruments?${params.toString()}`).then(setInstruments);
  }, [selectedUnderlyingId, selectedFamily]);

  const pollLoad = async (taskId: number) => {
    const status = await api<{ status: string; message: string | null }>(
      `/api/hedging/instruments/load/${taskId}`,
    );
    setLoadMessage(status.message);
    if (status.status === 'queued' || status.status === 'running') {
      setTimeout(() => void pollLoad(taskId), 1500);
    } else {
      setLoadInProgress(false);
      await loadUnderlyings();
    }
  };

  const onLoad = async () => {
    setLoadInProgress(true);
    try {
      const { task_id } = await api<{ task_id: number }>(
        '/api/hedging/instruments/load', { method: 'POST' },
      );
      void pollLoad(task_id);
    } catch {
      setLoadInProgress(false);
    }
  };

  const refreshInstruments = async () => {
    if (selectedUnderlyingId === null || selectedFamily === null) return;
    const params = new URLSearchParams({
      underlying_id: String(selectedUnderlyingId), family: selectedFamily,
    });
    setInstruments(await api<HedgeInstrument[]>(`/api/hedging/instruments?${params.toString()}`));
  };

  const onMark = async (ids: number[]) => {
    if (!ids.length) return;
    await api('/api/hedging/map/mark', { method: 'POST', body: JSON.stringify({ instrument_ids: ids }) });
    await Promise.all([refreshInstruments(), loadUnderlyings()]);
  };

  const onUnmark = async (ids: number[]) => {
    if (!ids.length) return;
    await api('/api/hedging/map/unmark', { method: 'POST', body: JSON.stringify({ instrument_ids: ids }) });
    await Promise.all([refreshInstruments(), loadUnderlyings()]);
  };

  const onPurgeStale = async (underlyingId: number) => {
    await api(`/api/hedging/map/purge-stale?underlying_id=${underlyingId}`, { method: 'POST' });
    await loadUnderlyings();
  };

  return (
    <Hedging
      underlyings={underlyings}
      selectedUnderlyingId={selectedUnderlyingId}
      selectedFamily={selectedFamily}
      instruments={instruments}
      loading={loading}
      loadInProgress={loadInProgress}
      loadMessage={loadMessage}
      onSelectUnderlying={(id) => {
        setSelectedUnderlyingId(id);
        const u = underlyings.find((x) => x.underlying_id === id);
        setSelectedFamily(u?.families[0]?.family ?? null);
      }}
      onSelectFamily={setSelectedFamily}
      onLoad={onLoad}
      onMark={onMark}
      onUnmark={onUnmark}
      onPurgeStale={onPurgeStale}
    />
  );
}
```

> If `PageContextReporter`'s payload type rejects `{ page: 'hedging', ... }`, drop the `useEffect` page-context block — it is a convenience for the (future) agent layer and not required for Part 1.

- [ ] **Step 4: Register the route in `main.tsx`**

In `frontend/src/main.tsx`: (a) add `import { HedgingLive } from './routes/Hedging.live';`; (b) add `{ route: 'hedging' as const, label: 'Hedging' }` to the nav list (near the `underlyings`/`risk` entries); (c) add `{ id: 'jump-hedging', group: 'Jump To', label: 'Hedging', shortcut: '↵' }` to the command-palette items; (d) add the render line near the other routes:

```tsx
        {route === 'hedging' && <HedgingLive onPageContextChange={handlePageContextChange} />}
```

- [ ] **Step 5: Run the container test and confirm it passes**

Run: `cd frontend && npx vitest run src/routes/Hedging.live.test.tsx`
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/Hedging.live.tsx frontend/src/routes/Hedging.live.test.tsx frontend/src/main.tsx
git commit -m "feat(hedging): live container + route registration"
```

---

## Task 11: Full-suite verification + minimal styling

**Files:**
- Create: `frontend/src/routes/Hedging.css` (imported by `Hedging.tsx`)
- Test: whole suites

- [ ] **Step 1: Add minimal CSS so the page renders sanely**

Create `frontend/src/routes/Hedging.css` with the classes used in `Hedging.tsx` (`.hedging`, `.hedging-bar`, `.hedging-body`, `.hedging-rail`, `.hedging-main`, `.hedging-attn`, `.hedging-tabs`, `.hedging-table`, `.hedging-actions`, `.badge-stale`, `.badge-ok`, `tr.expired`). Mirror the structure of an existing route CSS file (e.g. `frontend/src/routes/Underlyings.css`) for spacing/border tokens. Add `import './Hedging.css';` at the top of `Hedging.tsx`.

- [ ] **Step 2: Run the full backend suite**

Run: `pytest -q`
Expected: all green, including the new `tests/test_hedging_*` files and the pre-existing suites (no regressions). If the skills-catalog tests changed count, that's a mistake — Part 1 adds no `SKILL.md`; investigate.

- [ ] **Step 3: Run the full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: all green, including both `Hedging` test files.

- [ ] **Step 4: Typecheck + build the frontend**

Run: `cd frontend && npx tsc --noEmit && npm run build`
Expected: no type errors; build succeeds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/Hedging.css frontend/src/routes/Hedging.tsx
git commit -m "style(hedging): marking page styles + full-suite green"
```

---

## Self-review (completed during planning)

- **Spec coverage:** §4 data model → Task 1–2; resolver → Task 3; loader/reconcile/outage-safety → Task 4 + 6; in-scope extraction → Task 6/7; API (load/409, filtered catalog, mark/unmark, map, purge, load-status) → Task 8; page (Layout A + attention banner + load flow) → Task 9–11; error handling/edge cases → exercised by Task 4/6/7/8 tests; testing strategy → each task is TDD; "no skills added" → asserted in Task 11 Step 2.
- **Placeholder scan:** the only deferred item is the exact AKShare function names, which is a deliberate, spec-sanctioned verification step (Task 5 Step 1) behind monkeypatchable adapters — all dependent logic is unit-tested without AKShare.
- **Type consistency:** `EnumeratedContract`/`FamilySpec`/`ENUMERATORS`/`resolve_families` (Task 3/5) are used identically in Task 6; `_upsert_catalog`/`_expire_missing`/`reconcile_map` (Task 4) are reused unchanged in Task 6; domain function signatures (Task 7) match the endpoint calls (Task 8); the presentational `Props` (Task 9) match what the container passes (Task 10) and what the component test asserts.

## Hand-off to Part 2

Part 2 consumes `GET /api/hedging/map` (allowed, `active` instruments per underlying) and layers on instrument greeks/pricing, target selection, quantity solving, booking via the existing DeltaOne/option product types, and the agent `@tool` + workflow `SKILL.md`.
