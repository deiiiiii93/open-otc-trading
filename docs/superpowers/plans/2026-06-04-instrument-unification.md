# Instrument Master / Market Data / Pricing-Param Unification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One instrument master (underlyings + listed contracts, roles computed), one quote store every fetcher writes through, and pricing-parameter profiles split into instrument assumptions vs trade-keyed position params — per spec `docs/superpowers/specs/2026-06-04-instrument-market-data-unification-design.md`.

**Architecture:** A′ role-based single `instruments` table (renamed `underlyings`, id space preserved → `positions.underlying_id` values never change) + `market_quotes` + `assumption_sets/rows`. Python-side compatibility via SQLAlchemy synonyms/aliases (`Underlying = Instrument`, `asset_class = synonym("kind")`) so the wide consumer surface keeps compiling while phases rewire it honestly. **One alembic migration (0024) written at the end of Phase 2** against the settled models (tests run on `init_db` schema until then); live-DB upgrade after backend verification.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 + alembic (migration-local Core tables — house rule), pytest (`.venv/bin/python -m pytest` from repo root; rely on exit code, summary line is suppressed), React + vitest (**run from `frontend/`** or jsdom is missing).

**Spec adjustments discovered during grounding (authoritative for this plan):**
1. Alembic head is `0023_hedge_bands_default_unique` → the migration is **0024**, not 0022.
2. `UnderlyingPricingDefault = Underlying` is already a synonym — manual r/q/vol defaults are **columns on the registry row** (`models.py:463-465`). They stay as columns on `instruments`; there is **no separate defaults table** and **no table rename** for defaults. The defaults API keeps its existing `/api/underlying-pricing-defaults` paths this cycle (renaming the URL adds churn with no behavior change — honest-naming priority is on stores).
3. `kind` keeps the **existing asset_class vocabulary** (`index|etf|stock|futures|sge_spot`) + new `listed_option`. (Spec wrote `future`/`spot`; reality wins — `resolve_families` and the AKShare router speak these strings.)
4. A **PricingParameters page already exists** (`frontend/src/routes/PricingParameters.*`, `UnderlyingDefaultsPanel.tsx`, `PricingBuildConsole.tsx`) — Phase 3 reshapes it instead of creating it.
5. The frontend reload-from-positions plan (`docs/superpowers/plans/2026-06-04-market-data-reload-from-positions.md`) is **superseded**: the refresh loop moves server-side (`POST /api/market-data/quotes/refresh`). Do not execute that plan.

**Verification commands used throughout:**
```bash
# backend (repo root)
.venv/bin/python -m pytest tests/ -x -q; echo "exit=$?"
# single file
.venv/bin/python -m pytest tests/test_instruments.py -q; echo "exit=$?"
# frontend
cd frontend && npx vitest run --reporter=basic; echo "exit=$?"
# types
cd frontend && npx tsc --noEmit; echo "exit=$?"
```

---

## Phase 0 — Foundation: models + instruments service

### Task 1: Instrument / MarketQuote / AssumptionSet models

**Files:**
- Modify: `backend/app/models.py:441-489` (Underlying → Instrument), `:492-503` (Position FK), Product/MarketDataProfile/HedgeMapEntry/HedgeBand FK strings
- Test: `tests/test_instrument_models.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_instrument_models.py
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_instrument_table_and_compat_aliases():
    from app.models import Instrument, Underlying, UnderlyingPricingDefault

    assert Instrument.__tablename__ == "instruments"
    # Compatibility aliases keep the wide consumer surface compiling.
    assert Underlying is Instrument
    assert UnderlyingPricingDefault is Instrument
    cols = set(Instrument.__table__.columns.keys())
    assert {
        "symbol", "kind", "contract_code", "series_root", "expiry",
        "multiplier", "strike", "option_type", "parent_id", "loaded_at",
        "rate", "dividend_yield", "volatility", "status",
        "akshare_symbol", "akshare_asset_class",
    } <= cols
    assert "asset_class" not in cols  # renamed to kind; synonym only


def test_kind_synonym_and_parent_link():
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        index = Instrument(symbol="000905.SH", kind="index", status="active")
        session.add(index)
        session.flush()
        fut = Instrument(
            symbol="IC2606.CFFEX", kind="futures", contract_code="IC2606",
            exchange="CFFEX", series_root="IC", parent_id=index.id,
            expiry=datetime(2026, 6, 19).date(), multiplier=200.0,
            status="active",
        )
        session.add(fut)
        session.commit()
        assert fut.asset_class == "futures"      # synonym reads kind
        assert fut.parent.symbol == "000905.SH"  # self-relationship


def test_position_fk_targets_instruments():
    from app.models import Position

    fk = next(iter(Position.__table__.columns["underlying_id"].foreign_keys))
    assert fk.target_fullname == "instruments.id"


def test_market_quote_and_assumption_tables():
    from app.models import AssumptionRow, AssumptionSet, MarketQuote

    assert MarketQuote.__tablename__ == "market_quotes"
    assert {"instrument_id", "as_of", "price", "price_type", "source",
            "market_data_profile_id", "meta"} <= set(
        MarketQuote.__table__.columns.keys()
    )
    assert AssumptionSet.__tablename__ == "assumption_sets"
    assert {"set_id", "instrument_id", "rate", "dividend_yield", "volatility"} <= set(
        AssumptionRow.__table__.columns.keys()
    )


def test_hedge_map_entry_gains_instrument_id():
    from app.models import HedgeMapEntry

    assert "instrument_id" in HedgeMapEntry.__table__.columns
```

- [ ] **Step 2: Run, verify it fails** — `.venv/bin/python -m pytest tests/test_instrument_models.py -q; echo "exit=$?"` → ImportError `Instrument`.

- [ ] **Step 3: Implement.** In `backend/app/models.py` replace the `Underlying` class (lines 441-489) with:

```python
class Instrument(Base):
    """Role-based instrument master: underlyings AND listed contracts.

    kind = security type (index|etf|stock|futures|sge_spot|listed_option),
    NEVER a role. Roles are computed: "underlying" := positions reference it;
    "allowed hedge" := hedge_map_entries reference it.
    """

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    kind: Mapped[str] = mapped_column(
        String(40), default="index", server_default="index", nullable=False
    )
    market: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(40), nullable=True)
    currency: Mapped[str] = mapped_column(
        String(8), default="CNY", server_default="CNY", nullable=False
    )
    akshare_symbol: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    akshare_asset_class: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(
        String(40), default="draft", server_default="draft", index=True, nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    # Manual per-instrument r/q/vol defaults (the layer that feeds assumption
    # builds). Historically the UnderlyingPricingDefault synonym.
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Listed-contract terms (null for non-contracts).
    contract_code: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    series_root: Mapped[str | None] = mapped_column(String(40), nullable=True)
    expiry: Mapped[date | None] = mapped_column(Date, nullable=True)
    multiplier: Mapped[float | None] = mapped_column(Float, nullable=True)
    strike: Mapped[float | None] = mapped_column(Float, nullable=True)
    option_type: Mapped[str | None] = mapped_column(String(4), nullable=True)
    # Contractual underlier (IC2606 -> 000905.SH; LH option -> LH2609 future).
    # NOT hedge-routing — that stays config in hedging_universe.
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
    # Last seen by a contract load; drives expire-missing.
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    underlying = synonym("symbol")
    asset_class = synonym("kind")

    parent: Mapped["Instrument | None"] = relationship(remote_side=[id])
    positions: Mapped[list["Position"]] = relationship(back_populates="underlying_record")
    products: Mapped[list["Product"]] = relationship(back_populates="underlying_record")
    market_data_profiles: Mapped[list["MarketDataProfile"]] = relationship(
        back_populates="underlying_record"
    )

    __table_args__ = (
        Index("ix_instruments_kind_status", "kind", "status"),
        Index("ix_instruments_series_root_kind", "series_root", "kind"),
    )

    @property
    def is_complete(self) -> bool:
        return (
            self.rate is not None
            and self.dividend_yield is not None
            and self.volatility is not None
        )


# Compatibility aliases — consumers migrate phase by phase; these are cheap
# and may stay (precedent: UnderlyingPricingDefault has always been a synonym).
Underlying = Instrument
UnderlyingPricingDefault = Instrument
```

Add `date` to the `datetime` import and `Date` to the sqlalchemy import at the top of models.py if not present (Date/date are already imported for `HedgeInstrument`). Then update **every** `ForeignKey("underlyings.id")` string in models.py to `ForeignKey("instruments.id")` — occurrences: `Position.underlying_id` (line ~501), `Product.underlying_id`, `MarketDataProfile.underlying_id` (line ~1406), `HedgeInstrument.underlying_id` (line ~1525), `HedgeMapEntry.underlying_id` (line ~1565), `HedgeBand.underlying_id` (line ~1598). Verify with `grep -n 'underlyings.id' backend/app/models.py` → zero hits.

Add to `HedgeMapEntry` (after `underlying_id`):

```python
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id"), index=True, nullable=True
    )
```

Add new tables after `FxRate`:

```python
class MarketQuote(Base):
    """Single observation store. All fetchers write here; resolution is
    latest(as_of <= valuation), id tie-break. Source is diagnostics, not
    priority — unification happens at write time."""

    __tablename__ = "market_quotes"
    __table_args__ = (
        Index("ix_market_quotes_instrument_as_of", "instrument_id", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    price_type: Mapped[str] = mapped_column(
        String(12), default="close", server_default="close", nullable=False
    )
    source: Mapped[str] = mapped_column(
        String(40), default="manual", server_default="manual", nullable=False
    )
    market_data_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_data_profiles.id"), nullable=True
    )
    meta: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AssumptionSet(Base):
    """Instrument-level r/q/vol, versioned + valuation-dated. Built, never
    imported (trade-keyed imports live in PricingParameterProfile)."""

    __tablename__ = "assumption_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    valuation_date: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(40), default="completed")
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    rows: Mapped[list["AssumptionRow"]] = relationship(
        back_populates="assumption_set", cascade="all, delete-orphan"
    )


class AssumptionRow(Base):
    __tablename__ = "assumption_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("assumption_sets.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(80), index=True)
    rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend_yield: Mapped[float | None] = mapped_column(Float, nullable=True)
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_payload: Mapped[dict | None] = mapped_column(JSON, default=dict, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    assumption_set: Mapped[AssumptionSet] = relationship(back_populates="rows")
```

Add `instrument_id` (nullable FK to instruments, indexed) to `PricingParameterRow` after `symbol` (line ~1257). Do NOT touch `spot` yet — Phase 2.

- [ ] **Step 4: Run** the new test file → PASS, then the whole suite: `.venv/bin/python -m pytest tests/ -q; echo "exit=$?"`. Expect failures ONLY from tests asserting `asset_class` is a column or `underlyings` table name (e.g. SQL strings in `tests/test_position_currency.py::test_migration_backfills_position_currency_from_product` is migration-scoped — leave; it runs against 0019/0020 schema and stays valid). Fix any model-level fallout (typically: code doing `Underlying.asset_class == ...` still works via synonym; raw `INSERT INTO underlyings` in non-migration tests must become `instruments` with `kind`). Iterate until exit=0.

- [ ] **Step 5: Commit** — `git add -A backend/app/models.py tests/test_instrument_models.py tests/ && git commit -m "feat(instruments): role-based instrument master + quote/assumption tables (models)"`

### Task 2: instruments service — resolvability, contract queries, validator

**Files:**
- Create: `backend/app/services/instruments.py`
- Modify: `backend/app/services/underlyings.py:249-266` (`active_market_data_underlyings`)
- Test: `tests/test_instruments_service.py` (create)

- [ ] **Step 1: Failing tests**

```python
# tests/test_instruments_service.py
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _mk(session, **kw):
    from app.models import Instrument
    row = Instrument(**{"status": "active", "kind": "index", **kw})
    session.add(row)
    session.flush()
    return row


def test_resolvable_includes_drafts_excludes_expired():
    """The silent draft-exclusion gotcha dies: resolvable = has AKShare
    mapping AND not expired/retired. Status 'draft' is IN."""
    from app import database
    from app.services.instruments import resolvable_market_data_instruments

    with database.SessionLocal() as session:
        _mk(session, symbol="000905.SH", status="active",
            akshare_symbol="000905", akshare_asset_class="index")
        _mk(session, symbol="LH2609.DCE", status="draft", kind="futures",
            akshare_symbol="LH2609", akshare_asset_class="futures")
        _mk(session, symbol="IC2503.CFFEX", status="expired", kind="futures",
            akshare_symbol="IC2503", akshare_asset_class="futures")
        _mk(session, symbol="NOMAP.SH", status="active", akshare_symbol=None)
        session.commit()
        symbols = {r.symbol for r in resolvable_market_data_instruments(session)}
        assert symbols == {"000905.SH", "LH2609.DCE"}


def test_listed_option_requires_strike_and_type():
    from app.services.instruments import validate_instrument_terms

    with pytest.raises(ValueError, match="strike"):
        validate_instrument_terms(kind="listed_option", strike=None, option_type="C")
    with pytest.raises(ValueError, match="option_type"):
        validate_instrument_terms(kind="listed_option", strike=4000.0, option_type=None)
    validate_instrument_terms(kind="futures", strike=None, option_type=None)  # ok


def test_list_instruments_filters_kind_and_parent():
    from app import database
    from app.services.instruments import list_instruments

    with database.SessionLocal() as session:
        idx = _mk(session, symbol="000905.SH")
        _mk(session, symbol="IC2606.CFFEX", kind="futures", parent_id=idx.id,
            series_root="IC")
        _mk(session, symbol="510500.SH", kind="etf")
        session.commit()
        futs = list_instruments(session, kind="futures")
        assert [r.symbol for r in futs] == ["IC2606.CFFEX"]
        children = list_instruments(session, parent_id=idx.id)
        assert [r.symbol for r in children] == ["IC2606.CFFEX"]
```

- [ ] **Step 2: Run → fails** (no module `instruments`).

- [ ] **Step 3: Implement `backend/app/services/instruments.py`:**

```python
"""Instrument-master service layer.

Wraps + extends app.services.underlyings (which keeps ensure_underlying /
sync semantics every auto-registering channel relies on). New code should
import from here.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Instrument
from .underlyings import (  # re-exports: canonical names going forward
    ensure_underlying as ensure_instrument,
    list_underlyings,
    sync_underlyings_from_positions as sync_instruments_from_positions,
)

__all__ = [
    "ensure_instrument",
    "sync_instruments_from_positions",
    "list_instruments",
    "resolvable_market_data_instruments",
    "validate_instrument_terms",
]

_UNRESOLVABLE_STATUSES = {"expired", "retired"}


def resolvable_market_data_instruments(session: Session) -> list[Instrument]:
    """Instruments eligible for market-data fetch: AKShare mapping present and
    not end-of-life. Drafts ARE included — synced-but-uncurated instruments
    must still get quotes (the old active-only filter silently starved them).
    """
    return (
        session.query(Instrument)
        .filter(
            Instrument.akshare_symbol.isnot(None),
            Instrument.akshare_asset_class.isnot(None),
            Instrument.status.notin_(_UNRESOLVABLE_STATUSES),
        )
        .order_by(Instrument.symbol.asc())
        .all()
    )


def validate_instrument_terms(
    *, kind: str, strike: float | None, option_type: str | None
) -> None:
    """Service-level guard (no DB CHECK): listed options need full terms."""
    if kind == "listed_option":
        if strike is None:
            raise ValueError("listed_option requires strike")
        if option_type is None:
            raise ValueError("listed_option requires option_type")


def list_instruments(
    session: Session,
    *,
    kind: str | None = None,
    status: str | None = None,
    parent_id: int | None = None,
    series_root: str | None = None,
    search: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[Instrument]:
    q = session.query(Instrument)
    if kind:
        q = q.filter(Instrument.kind == kind)
    if status:
        q = q.filter(Instrument.status == status)
    if parent_id is not None:
        q = q.filter(Instrument.parent_id == parent_id)
    if series_root:
        q = q.filter(Instrument.series_root == series_root)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(Instrument.symbol.ilike(like))
    return q.order_by(Instrument.symbol.asc()).offset(offset).limit(limit).all()
```

Then change `active_market_data_underlyings` (`underlyings.py:249`) to delegate:

```python
def active_market_data_underlyings(session: Session) -> list[Underlying]:
    """Deprecated name kept for callers; semantics are now 'resolvable'
    (drafts included). See instruments.resolvable_market_data_instruments."""
    from .instruments import resolvable_market_data_instruments

    rows = resolvable_market_data_instruments(session)
    if rows:
        return rows
    sync_underlyings_from_positions(session)
    return resolvable_market_data_instruments(session)
```

- [ ] **Step 4: Run** new file + full suite (exit=0). Any test pinning active-only bulk behavior must be updated to the new rule — update assertions to expect drafts included (this is the spec'd behavior change, cite spec "Resolvability rule").

- [ ] **Step 5: Commit** — `feat(instruments): service layer with draft-inclusive resolvability + terms validator`

---

## Phase 1 — Quote store and emitters

### Task 3: quote service

**Files:**
- Create: `backend/app/services/quotes.py`
- Test: `tests/test_quotes_service.py` (create)

- [ ] **Step 1: Failing tests**

```python
# tests/test_quotes_service.py
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


@pytest.fixture()
def instrument():
    from app import database
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = Instrument(symbol="000905.SH", kind="index", status="active")
        session.add(row)
        session.commit()
        yield row.id


def test_record_and_latest_quote(instrument):
    from app import database
    from app.services.quotes import latest_quote, record_quote

    with database.SessionLocal() as session:
        record_quote(session, instrument_id=instrument, price=6400.0,
                     as_of=datetime(2026, 6, 2), source="akshare")
        record_quote(session, instrument_id=instrument, price=6412.55,
                     as_of=datetime(2026, 6, 4), source="xlsx_import")
        session.commit()
        q = latest_quote(session, instrument, as_of=datetime(2026, 6, 4, 23))
        assert q.price == 6412.55
        # valuation-date cutoff: looking back before the second observation
        q2 = latest_quote(session, instrument, as_of=datetime(2026, 6, 3))
        assert q2.price == 6400.0


def test_latest_quote_tie_break_is_last_id(instrument):
    """Same as_of (e.g. conflicting xlsx rows): max id wins — last file row."""
    from app import database
    from app.services.quotes import latest_quote, record_quote

    as_of = datetime(2026, 6, 4)
    with database.SessionLocal() as session:
        record_quote(session, instrument_id=instrument, price=6410.0, as_of=as_of,
                     source="xlsx_import")
        record_quote(session, instrument_id=instrument, price=6412.55, as_of=as_of,
                     source="xlsx_import")
        session.commit()
        assert latest_quote(session, instrument, as_of=as_of).price == 6412.55


def test_latest_quote_none_when_empty(instrument):
    from app import database
    from app.services.quotes import latest_quote

    with database.SessionLocal() as session:
        assert latest_quote(session, instrument, as_of=datetime(2026, 6, 4)) is None
```

- [ ] **Step 2: Run → fails.**

- [ ] **Step 3: Implement `backend/app/services/quotes.py`:**

```python
"""Single observation store for instrument prices.

All fetchers (market-data page, contract loader, xlsx importer, pricer
fallback) write through record_quote; consumers resolve with latest_quote.
Source is diagnostics, never priority — unification happens at write time.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from sqlalchemy.orm import Session

from ..models import MarketQuote


def record_quote(
    session: Session,
    *,
    instrument_id: int,
    price: float,
    as_of: datetime,
    source: str,
    price_type: str = "close",
    market_data_profile_id: int | None = None,
    meta: dict | None = None,
) -> MarketQuote:
    row = MarketQuote(
        instrument_id=instrument_id,
        price=float(price),
        as_of=as_of,
        source=source,
        price_type=price_type,
        market_data_profile_id=market_data_profile_id,
        meta=meta or {},
    )
    session.add(row)
    session.flush()
    return row


def latest_quote(
    session: Session, instrument_id: int, *, as_of: datetime
) -> MarketQuote | None:
    """Deterministic: max(as_of <= valuation), tie-break max(id)."""
    return (
        session.query(MarketQuote)
        .filter(
            MarketQuote.instrument_id == instrument_id,
            MarketQuote.as_of <= as_of,
        )
        .order_by(MarketQuote.as_of.desc(), MarketQuote.id.desc())
        .first()
    )


def latest_quotes(
    session: Session, instrument_ids: Iterable[int], *, as_of: datetime
) -> dict[int, MarketQuote]:
    out: dict[int, MarketQuote] = {}
    for iid in set(instrument_ids):
        q = latest_quote(session, iid, as_of=as_of)
        if q is not None:
            out[iid] = q
    return out
```

- [ ] **Step 4: Run → PASS. Step 5: Commit** — `feat(quotes): single observation store with deterministic resolver`

### Task 4: market-data endpoints emit quotes; bulk uses resolvability rule

**Files:**
- Modify: `backend/app/main.py:3148-3243` (single + bulk akshare endpoints), `:2182-2212` (fetch-spot)
- Test: extend the existing market-data endpoint tests (find with `grep -rln "profiles/akshare" tests/`)

- [ ] **Step 1: Failing test** (add to the existing market-data test file; mirror its existing fetch stubbing — it monkeypatches `fetch_akshare_snapshot`):

```python
def test_akshare_profile_emits_market_quote(client, monkeypatch):
    # ...existing snapshot stub from neighbouring tests...
    resp = client.post("/api/market-data/profiles/akshare", json={
        "symbol": "000905.SH", "asset_class": "index",
        "start_date": "2026-06-01", "end_date": "2026-06-04",
    })
    assert resp.status_code == 200
    from app import database
    from app.models import Instrument, MarketQuote
    with database.SessionLocal() as session:
        quote = session.query(MarketQuote).one()
        inst = session.get(Instrument, quote.instrument_id)
        assert inst.symbol == "000905.SH"
        assert quote.source == "akshare"
        assert quote.market_data_profile_id == resp.json()["id"]
```

- [ ] **Step 2: Run → fails** (no quote row).

- [ ] **Step 3: Implement.** In `create_akshare_market_data_profile` (main.py:3148) after `session.flush()` (line ~3157) insert:

```python
        spot_value = (profile.data or {}).get("spot") if isinstance(profile.data, dict) else None
        if spot_value is not None:
            from .services.quotes import record_quote
            record_quote(
                session,
                instrument_id=underlying.id,
                price=float(spot_value),
                as_of=profile.valuation_date,
                source="akshare",
                market_data_profile_id=profile.id,
            )
```

(Confirm the headline-spot attribute by reading `_market_profile_from_snapshot` first — if spot lives in `profile.data["spot"]` keep as written; adjust the accessor once, reuse in all three endpoints.) Apply the same emission block in the bulk loop (after its `session.flush()`, line ~3219, using `underlying.id`) and in `fetch_underlying_spot` (main.py:2193-2202, using `row.id`). The bulk endpoint already iterates `active_market_data_underlyings`, which Task 2 re-pointed to the resolvable rule — no further change needed there; add a test asserting a draft instrument is included in bulk fetch.

- [ ] **Step 4: Run market-data tests + full suite → exit=0. Step 5: Commit** — `feat(quotes): akshare market-data endpoints emit quotes (drafts included in bulk)`

### Task 5: contract loader writes instruments + emits quotes; HedgeInstrument model retired

**Files:**
- Modify: `backend/app/services/hedging_loader.py` (`_upsert_catalog`, `_expire_missing`, `reconcile_map`), `backend/app/services/hedging_legs.py` (`_active_instruments`, `_effective_multiplier`, `propose`, `price`), `backend/app/services/domains/hedging.py` (`list_instruments`, `mark`, `unmark`, `get_map`, `purge_stale`), `backend/app/models.py` (delete `HedgeInstrument` class)
- Test: existing `tests/test_hedging_*` files pin loader/legs/map behavior — they are the RED driver

This is the largest rewiring task. Sequence:

- [ ] **Step 1:** Read `hedging_loader.py:19-103` (`_upsert_catalog`/`_expire_missing`/`reconcile_map`) and `services/domains/hedging.py` fully.
- [ ] **Step 2:** Write the failing test first (new file `tests/test_loader_writes_instruments.py`):

```python
def test_upsert_catalog_merges_into_existing_instrument(_db):
    """The LH2609 case: a contract row arriving for a symbol that already
    exists (position-sourced) MERGES terms onto it — one identity."""
    from app import database
    from app.models import Instrument
    from app.services.hedging_loader import _upsert_catalog
    from app.services.hedging_universe import ContractInfo  # frozen dataclass

    with database.SessionLocal() as session:
        existing = Instrument(symbol="LH2609.DCE", kind="futures", status="draft",
                              source="position")
        session.add(existing)
        session.flush()
        contracts = [ContractInfo(
            family="commodity_future", series_root="LH", exchange="DCE",
            contract_code="LH2609", instrument_type="future", option_type=None,
            strike=None, expiry=None, multiplier=16.0, last_price=13905.0,
            akshare_symbol="LH2609",
        )]
        seen = _upsert_catalog(session, contracts, underlying_id=existing.id)
        session.commit()
        rows = session.query(Instrument).filter(
            Instrument.symbol == "LH2609.DCE").all()
        assert len(rows) == 1                      # merged, not duplicated
        assert rows[0].multiplier == 16.0          # terms updated from feed
        assert rows[0].status == "active"          # feed terms are authoritative
        from app.models import MarketQuote
        q = session.query(MarketQuote).one()
        assert q.source == "hedge_load" and q.price == 13905.0
```

(Adjust the `ContractInfo` constructor to the real dataclass in `hedging_universe.py:14-30` — read it first; field names above match the `_upsert_catalog` call site at `hedging_loader.py:47`.)

- [ ] **Step 3:** Rewrite `_upsert_catalog` to upsert `Instrument` rows: canonical `symbol = f"{c.contract_code}.{c.exchange}"`; match by symbol (merge) else insert with `kind = "listed_option" if c.option_type else "futures"`, `status="active"`, `source="hedge_load"`, terms + `series_root` + `contract_code` + `loaded_at=utcnow()`, `parent_id=underlying_id` **only when the family spec's underlier IS that instrument** (index futures/options → the index row = `underlying_id` param; ETF options → the ETF row; commodity futures → `None`; commodity options → the matching futures row when present, found by `series_root` + same `expiry` month prefix on `contract_code`, else `None`). On `last_price is not None` → `record_quote(..., source="hedge_load", as_of=utcnow())` and stop writing `last_price`. Rewrite `_expire_missing` to set `status="expired"` on instruments of that family/series not in `seen`. `reconcile_map` logic unchanged but matches by `instrument_id` (backfilled in mark).
- [ ] **Step 4:** Re-point readers: in `hedging_legs.py` `_active_instruments` queries `Instrument` (`kind.in_(("futures","listed_option"))`, `status=="live"` → `=="active"`); everywhere `inst.last_price` is read (`propose` ATM at line 79, futures pricing) replace with a quote lookup passed in by the caller — add parameter `quotes: dict[int, float]` resolved via `latest_quotes(...)` at the call sites in `hedging_strategy`/endpoints. In `services/domains/hedging.py` re-key list/mark/unmark/map onto `Instrument` + `HedgeMapEntry.instrument_id`.
- [ ] **Step 5:** Delete the `HedgeInstrument` class from models.py; fix every remaining import (`grep -rn "HedgeInstrument" backend/app tests/`). Run the full suite; the existing hedging tests are the safety net — fix code (not tests) until green, EXCEPT tests that pin `last_price` column reads, which update to quote seeding.
- [ ] **Step 6: Commit** — `feat(instruments): contract loader writes instrument master + hedge_load quotes; HedgeInstrument retired`

### Task 6: spot consumption chain (pricer + risk) reads quotes

**Files:**
- Modify: `backend/app/services/position_pricer.py` (`_resolve_market_field` :771, the spot block in `_price_position` :513-633, `fetch_spot_from_akshare` :478), `backend/app/services/quantark.py` (`market_snapshot_for_position` :1222, `latest_market_input_for_position` :1262)
- Test: `tests/test_spot_chain.py` (create) + existing pricer/risk tests as the net

- [ ] **Step 1: Failing tests**

```python
def test_spot_resolves_from_quote_store(_db, seeded_position):
    """spot: override -> latest_quote -> fetch&record -> diagnostic."""
    from app import database
    from app.services.quotes import record_quote
    from app.services.quantark import market_snapshot_for_position
    # seed a quote; build a fallback env with a DIFFERENT spot
    with database.SessionLocal() as session:
        position = session.get(Position, seeded_position)
        record_quote(session, instrument_id=position.underlying_id,
                     price=6412.55, as_of=datetime(2026, 6, 4), source="akshare")
        session.commit()
        snap = market_snapshot_for_position(
            position, fallback_env(spot=999.0, valuation_date=datetime(2026, 6, 4)),
            session=session)
        assert snap.spot == 6412.55          # quote wins over fallback


def test_missing_quote_produces_explicit_diagnostic(...):
    # no quote rows, AKShare fetcher stubbed to fail ->
    # pricing_error contains "no market quote for"
```

(Write these against the real entry points after reading `quantark.py:1100-1230` — `market_snapshot_for_position` gains a `session` parameter threaded from `calculate_portfolio_risk`'s session; the PositionMarketInput read at `latest_market_input_for_position` keeps serving **r/q/vol only** until Phase 2 removes it.)

- [ ] **Step 2:** Implement: in `market_snapshot_for_position`, before the market-input read, resolve `latest_quote(session, position.underlying_id, as_of=fallback.valuation_date)`; quote price takes the `spot` slot; market-input/fallback fill the rest. In `position_pricer._price_position`: replace the spot branch of `_resolve_market_field` usage — order becomes `override → pricing-row spot is GONE (no change yet; Phase 2 drops the column) → quote → fetch_spot_from_akshare(...)` and on successful fallback fetch call `record_quote(..., source="pricer_fallback")` (fetch-once). When everything misses: `_failed(position, f"no market quote for {symbol} as of {valuation_date:%Y-%m-%d}", "missing_quote", ...)`. Add quote age (days) into the diagnostics dict.
- [ ] **Step 3:** Run full suite; pricer/risk tests that seeded spot via PositionMarketInput must now ALSO get it from there (still supported until Phase 2) — only tests seeding spot via pricing-parameter rows re-seed via `record_quote`. Iterate to exit=0.
- [ ] **Step 4: Commit** — `feat(quotes): spot chain reads quote store with fetch-and-record fallback + explicit missing-quote diagnostic`

---

## Phase 2 — Assumptions split, importer split-write, migration

### Task 7: assumptions service + build endpoint

**Files:**
- Create: `backend/app/services/assumptions.py`
- Modify: `backend/app/main.py:2285-2336` (replace `build-default` endpoint), `backend/app/services/pricing_profiles.py:335-470` (gut `build_default_pricing_profile`)
- Test: `tests/test_assumptions.py` (create)

- [ ] **Step 1: Failing tests**

```python
def test_build_assumptions_set_no_akshare(_db, monkeypatch):
    """Build never touches the network: no spot, no failed_akshare mode."""
    import app.services.assumptions as assumptions

    monkeypatch.setattr(
        "app.services.assumptions.fetch_akshare_snapshot",
        lambda *a, **k: pytest.fail("assumptions build must not fetch"),
        raising=False,
    )
    # seed: one open position on 000905.SH; instrument defaults r=0.023 q=0.018;
    # vol inherited from a trade-keyed pricing row (vol=0.221, trade T-0142)
    s = assumptions.build_assumptions_set(session, name="test")
    row = s.rows[0]
    assert (row.rate, row.dividend_yield, row.volatility) == (0.023, 0.018, 0.221)
    srcs = row.source_payload["manual_input_sources"]
    assert srcs["rate"] == "instrument_default"
    assert srcs["volatility"] == "inherited_pricing_parameter_row"
    assert row.source_payload["inherited_source_trade_id"] == "T-0142"


def test_build_assumptions_unfilled_raises(_db):
    with pytest.raises(ValueError) as exc:
        assumptions.build_assumptions_set(session, name="test")
    assert "unfilled" in str(exc.value)
```

- [ ] **Step 2:** Implement `assumptions.py`: port `build_default_pricing_profile`'s scope + resolution skeleton (`pricing_profiles.py:335-371` — `_open_position_underlyings`, defaults-then-inherit via `latest_manual_inputs_by_underlying`, the `unfilled` ValueError) but **delete the entire AKShare block (lines 373-409)** and write `AssumptionSet`/`AssumptionRow` instead of profile rows; per-field provenance `instrument_default | inherited_pricing_parameter_row` in `source_payload`. Endpoint: replace `POST /api/pricing-parameter-profiles/build-default` with `POST /api/assumptions/build` + `GET /api/assumptions/sets` + `GET /api/assumptions/sets/{id}` (schemas `AssumptionSetOut`/`AssumptionRowOut` in schemas.py mirroring `PricingParameterProfileOut` minus spot). Keep the audit event name `pricing_parameters.default_built` → rename `assumptions.built`.
- [ ] **Step 3:** Run; existing tests of build-default re-target to the new service (same fixtures, new asserts — no spot). Commit — `feat(assumptions): instrument-level assumption sets; build is AKShare-free`

### Task 8: importer split-write + r/q/vol chain + PositionMarketInput fold

**Files:**
- Modify: `backend/app/services/pricing_profiles.py` (xlsx import fn), `backend/app/services/position_pricer.py` (r/q/vol resolution + delete `import_market_inputs_from_xlsx` :379-456 + `latest_market_inputs_by_trade_id` :458), `backend/app/services/quantark.py` (drop market-input read), `backend/app/models.py` (delete `PositionMarketInput`, `MarketInputImportBatch`; drop `PricingParameterRow.spot`), `backend/app/main.py` (delete the market-inputs import endpoint; locate via `grep -n "market-inputs\|market_inputs" backend/app/main.py`)
- Test: `tests/test_import_split_write.py` (create)

- [ ] **Step 1: Failing tests**

```python
def test_import_splits_observations_from_assumptions(_db, tmp_path):
    """trade_id|symbol|spot|r|q|vol  ->  r/q/vol on trade-keyed rows,
    spot emitted as quotes with trade provenance."""
    path = _write_xlsx(tmp_path, rows=[
        {"trade_id": "T-0142", "symbol": "000905.SH", "spot": 6412.55,
         "rate": 0.023, "dividend_yield": 0.018, "volatility": 0.221},
        {"trade_id": "T-0143", "symbol": "000905.SH", "spot": 6410.00,
         "rate": 0.023, "dividend_yield": 0.018, "volatility": 0.225},
        {"trade_id": "T-0201", "symbol": "UNBOOKED.SH", "spot": 100.0,
         "rate": 0.02, "dividend_yield": 0.0, "volatility": 0.30},
    ])
    profile = import_pricing_parameter_profile_from_xlsx(session, xlsx_path=path)
    rows = profile.rows
    assert all(not hasattr(r, "spot") for r in rows)
    quotes = session.query(MarketQuote).all()
    assert len(quotes) == 3                      # every observation recorded
    assert profile.summary["quotes_emitted"] == 3
    assert profile.summary["spot_conflicts"] == [
        {"symbol": "000905.SH", "count": 2, "resolution": "last row wins"}]
    assert profile.summary["dormant_trade_ids"] == ["T-0201"]
    t201 = next(r for r in rows if r.source_trade_id == "T-0201")
    assert t201.instrument_id is not None        # ensure_instrument drafts it
```

```python
def test_rqv_chain_trade_params_then_assumptions(...):
    # position with exact trade row -> row's vol; position without -> assumption
    # set's vol; neither -> env fallback. Drive through the REAL pricer entry
    # (house lesson: stubs masked the headline bug in the currency project).
```

- [ ] **Step 2:** Implement importer split: in the xlsx import fn — for each parsed row, resolve `instrument_id` (`source_trade_id → Position.underlying_id`, else `ensure_instrument(symbol, source="pricing_profile", status="draft")`), write the row WITHOUT spot, and `record_quote(price=row_spot, as_of=profile.valuation_date, source="xlsx_import", price_type="mid", meta={"trade_id":..., "profile_id":...})` when spot present. Build the summary (applied/dormant/quotes_emitted/spot_conflicts). Then re-point the r/q/vol chain: `position_pricer._resolve_market_field` keeps `override → pricing-row → ` but the third slot becomes **assumption-row lookup** (latest set ≤ valuation, by instrument) instead of PositionMarketInput; quantark's `latest_market_input_for_position` is deleted and `market_snapshot_for_position` composes quote-spot + trade-row/assumption r/q/vol. Drop the models + import endpoint; migrate every reference (`grep -rn "PositionMarketInput\|market_inputs" backend/app tests/`).
- [ ] **Step 3:** Run full suite; tests seeding PositionMarketInput re-seed via pricing-parameter rows or assumption sets, preserving the asserted VALUES (equivalence!). Iterate to exit=0. Commit — `feat(pricing-params): trade-keyed split-write import; PositionMarketInput folded; r/q/vol chain on assumptions`

### Task 9: alembic migration 0024 + migration test

**Files:**
- Create: `backend/alembic/versions/0024_instrument_unification.py`
- Test: `tests/test_migration_0024.py` (create)

- [ ] **Step 1: Failing test** (pattern of `tests/test_position_currency.py::test_migration_backfills...` — upgrade to 0023, seed legacy rows with raw SQL, upgrade to 0024, assert):

```python
def test_0024_preserves_ids_merges_contracts_backfills_quotes(tmp_path, monkeypatch):
    # upgrade to 0023; seed:
    #   underlyings: (id=2,'LH2609.DCE'), (id=7,'000905.SH')
    #   positions: underlying_id=2
    #   hedge_instruments: ('DCE','LH2609', last_price=13905, loaded_at=...) -> MERGE
    #                      ('CFFEX','IC2606', last_price=6398) -> INSERT new id
    #   market_data_profiles: 000905 profile with data.spot=6412.55
    #   pricing_parameter_rows: trade T-0142, spot=6412.55, vol=0.221
    #   pricing_parameter_profiles: one source_type='default_underlying' with row
    #   position_market_inputs: T-0166 spot=13905 vol=0.314
    # upgrade to 0024; assert:
    #   instruments has id=2 with kind='futures' contract_code='LH2609' multiplier
    #   positions.underlying_id still == 2 and FK targets instruments
    #   market_quotes: 4 rows (profile spot + hedge last_price + pricing row spot
    #                  + market-input spot), honest as_of each
    #   pricing_parameter_rows has NO spot column; instrument_id backfilled
    #   assumption_sets: 1 (from the default_underlying profile), rows carried
    #   pricing_parameter_profiles: synthetic 'Migrated position market inputs'
    #   underlyings, hedge_instruments, position_market_inputs tables gone
```

Write it out fully (raw `sa.text` inserts exactly like the 0020 test at `tests/test_position_currency.py:50-92`).

- [ ] **Step 2:** Write 0024 with **migration-local Core table definitions** (house rule — never import app models). Order of operations (SQLite-safe):
  1. `ALTER TABLE underlyings RENAME TO instruments` (then `batch_alter_table("instruments")`: rename `asset_class`→`kind`; add contract_code/series_root/expiry/multiplier/strike/option_type/parent_id/loaded_at).
  2. `batch_alter_table` rebuilds for every FK-bearing table (`positions`, `products`, `market_data_profiles`, `hedge_map_entries`, `hedge_bands`) re-targeting `instruments.id` — values untouched.
  3. Create `market_quotes`, `assumption_sets`, `assumption_rows`; add `instrument_id` to `hedge_map_entries`, `pricing_parameter_rows`.
  4. Data: merge `hedge_instruments` (symbol = `contract_code || '.' || exchange`; UPDATE-on-collision else INSERT; status live→active; kind from `instrument_type`/`option_type`); emit quotes from `hedge_instruments.last_price` (as_of=loaded_at), `market_data_profiles` (`json_extract(data,'$.spot')`, as_of=valuation_date), `pricing_parameter_rows.spot` (as_of=profile valuation_date, source `legacy`), `position_market_inputs.spot`; convert `source_type='default_underlying'` profiles → assumption sets/rows; synthesize the migrated-market-inputs profile(s); backfill `instrument_id` columns by symbol.
  5. `batch_alter_table("pricing_parameter_rows")` drop `spot`; drop tables `hedge_instruments`, `position_market_inputs`, `market_input_import_batches`.
  `down_revision = "0023_hedge_bands_default_unique"` (verify the exact revision string inside the 0023 file before writing).
- [ ] **Step 3:** Run the migration test → PASS; full suite → exit=0.
- [ ] **Step 4: Commit** — `feat(migration): 0024 instrument unification — id-preserving rename, contract merge, quote backfill`

### Task 10: API sweep + backend verification + live upgrade

**Files:**
- Modify: `backend/app/main.py` — add `GET/PATCH /api/instruments`, `POST /api/instruments/sync-from-positions`, contracts/load aliases (re-route `POST /api/hedging/instruments/load` body to the same task fn; keep hedging paths working for the UI until Phase 3), `GET /api/market-data/quotes`, `POST /api/market-data/quotes`, `POST /api/market-data/quotes/refresh`; delete `/api/underlyings/*` routes + `create-spot-pricing-profile`; `backend/app/schemas.py` — `InstrumentOut`, `MarketQuoteOut`, `QuoteRefreshResultOut`
- Test: `tests/test_instruments_api.py` (create; FastAPI TestClient pattern from `tests/test_api.py`)

- [ ] **Step 1:** Failing tests: `GET /api/instruments?kind=futures` filters; `PATCH /api/instruments/{id}` updates status/mapping and 400s on `listed_option` without strike (validator); `POST /api/market-data/quotes/refresh` (AKShare stubbed) syncs-from-positions then fetches per resolvable instrument, returns `{synced_created, synced_existing, fetched, skipped: [symbols], failed: [{symbol, error}]}` and never aborts on one failure.
- [ ] **Step 2:** Implement; `quotes/refresh` is the server-side port of the superseded frontend plan: `sync_instruments_from_positions` → partition resolvable/unresolvable → loop `fetch_akshare_snapshot` + profile + `record_quote` per instrument inside try/except collecting failures.
- [ ] **Step 3:** Full backend suite → exit=0.
- [ ] **Step 4: Live DB upgrade** (the real book):

```bash
cp data/open_otc.sqlite3 data/open_otc.sqlite3.pre-instrument-unification-$(date +%Y%m%d).bak
.venv/bin/python -m alembic upgrade head
# repair the found bug:
sqlite3 data/open_otc.sqlite3 "UPDATE instruments SET akshare_asset_class='futures', kind='futures' WHERE symbol='LH2609.DCE';"
```

- [ ] **Step 5: Equivalence check against the live book** — run an out-of-band risk run for portfolios X and Dup (the `queue_portfolio_risk` + `execute_risk_run_task(task_id, run_id, session_factory)` script pattern with `PYTHONPATH=backend`; immune to the concurrent agent's uvicorn reloads) and diff totals/by_currency against run 25's persisted metrics. **Byte-identical or explain every digit before proceeding.**
- [ ] **Step 6: Commit** — `feat(api): instruments + quotes endpoints; live DB migrated (backup kept)`

---

## Phase 3 — UI

General notes: presentational/live split per house pattern (`X.tsx` + `X.live.tsx` + tests beside them); **vitest from `frontend/`**; nav lives in `frontend/src/main.tsx`.

### Task 11: Instruments page — Registry tab

**Files:**
- Create: `frontend/src/routes/Instruments.tsx`, `Instruments.live.tsx`, `Instruments.css`, `Instruments.test.tsx`
- Modify: `frontend/src/main.tsx` (nav entry "Instruments" replacing "Underlyings")

- [ ] Failing presentational tests first: renders instrument rows with kind/status/parent/roles columns; role badges read-only; `onSyncFromPositions` + `onLoadContracts` callbacks fire; kind↔akshare_asset_class mismatch renders a warning badge (the LH2609 catch); draft rows get a `wl-instruments__row--draft` class. Then implement: table columns `SYMBOL | KIND | PARENT | STATUS | ROLES | TERMS | AKSHARE | actions`; reuse the Underlyings page's existing edit-form fields (port from `Underlyings.tsx` — read it first) extended with terms + parent. Live wiring: `GET /api/instruments`, PATCH on edit, sync + contracts-load with TaskRun progress polling (port the polling pattern from `Hedging.live.tsx` instrument load). Commit per page-tab.

### Task 12: Allowed Hedges tab

- [ ] Port the Allowed Instruments module out of `Hedging.tsx`/`Hedging.live.tsx` (read both first; the marking UI exists there per the hedging-module project). Layout per approved wireframe: left rail = `GET /api/hedging/map` groups + open-position counts (extend the endpoint payload — small backend edit + test); right = map table (unmark, purge-stale) over candidate catalog (`GET /api/instruments?...` family-routed via the existing hedgeable families helper + `latest quote (age)` column from `GET /api/market-data/quotes?latest=1`). Tests: 0-allowed flag renders; mark/unmark callbacks; stale badge. Commit.

### Task 13: Market Data tab + FX

- [ ] Port the FX-rates table and fetch-form from `MarketData.tsx` (read first). New Quotes sub-tab: one row per instrument from `GET /api/market-data/quotes?latest=1` with age badge (`0d` green / `>3d` amber / `>5d` red); "Refresh quotes (all resolvable)" button → `POST /api/market-data/quotes/refresh` with the summary feedback string (`Synced N new · fetched X · skipped … · failed …`); manual-quote form → `POST /api/market-data/quotes`. Fetch-events sub-tab = existing profiles list (port). Tests: refresh button disabled while fetching, summary rendered, age badge thresholds. Commit.

### Task 14: Assumptions tab

- [ ] Move `UnderlyingDefaultsPanel.tsx` (read first) into the tab; add per-field provenance display (explicit default vs `inherited from T-… · profile #…` — data from the existing defaults serialization, which already carries `inherited`); unfilled rows highlighted; "Build assumptions" → `POST /api/assumptions/build` with the unfilled-400 surfaced inline; set selector via `GET /api/assumptions/sets`. **No import button.** Tests: unfilled highlight, build error rendering, no-import-button assertion. Commit.

### Task 15: Pricing Params page reshape + Position Detail block

- [ ] Reshape `PricingParameters.tsx`/`live` (read first): remove build-default console (`PricingBuildConsole.tsx`) and defaults panel (moved); keep import + profile library (`ProfileLibrary.tsx`); add the import-summary strip (applied / dormant / quotes emitted / spot conflicts from `profile.summary`) and the trade-keyed row table (trade id → matched position link → instrument, r/q/vol, `Q` marker, applied/dormant badge). Position Detail (in `Positions.tsx`): new "Pricing Params" `DetailItem` block showing resolved spot/r/q/vol + per-field provenance from a new `GET /api/portfolios/{pid}/positions/{id}/pricing-params` endpoint (small backend task inside this task: compose the resolution chain read-only and return `{field: {value, source}}` — test it). Commit.

### Task 16: page removal + nav + frontend verification

- [ ] Delete `Underlyings.*` and `MarketData.*` routes (+ their tests), slim `Hedging.*` to Strategy only, update `main.tsx` nav (Instruments · Pricing Params · …). Then: `cd frontend && npx vitest run; echo "exit=$?"` → exit=0 and `npx tsc --noEmit` → exit=0. Commit — `feat(ui): Instruments page (4 tabs) + Pricing Params reshape; legacy pages removed`

---

## Phase 4 — Agent surface, smoke, close-out

### Task 17: agent tools + workflow audit

- [ ] `grep -rn "underlyings\|hedge_instruments\|pricing-parameter-profiles/build-default\|market_inputs" backend/app/services/agents.py backend/app/services/deep_agent/ backend/app/skills/` — re-point every hit: tool bodies call `instruments`/`quotes`/`assumptions` services; tool descriptions updated (build-default tool → "build assumptions (r/q/vol only; spot comes from market quotes)"). Audit `backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md` references. NOTE (memory): editing skill bodies is safe; adding/removing SKILL.md files breaks the exact-set catalog tests — don't add/remove any. Reference docs: only if a new reference page is added does `VALID_REFERENCE_TYPES` in `test_reference_docs.py` need touching. Full backend suite → exit=0. Commit.

### Task 18: live smoke + close-out

- [ ] Start backend + frontend dev servers; browser smoke on the real book: Registry shows LH2609 with both role paths visible and the repaired mapping; contract load completes (LH commodity families now resolve); Market Data tab Refresh quotes fetches for every open-book instrument incl. drafts; Assumptions tab shows unfilled rows; build succeeds AKShare-free; Pricing Params import round-trip (use a real trade sheet); fresh risk runs for X and Dup complete; Risk page totals match Step 10.5's equivalence record; Hedging solve produces legs with quote-priced futures.
- [ ] Update memory `project_instrument_unification.md` (status → built; gotchas found during execution).
- [ ] Final commit + report.

---

## Self-review notes

- **Spec coverage:** data model→T1; migration→T9; instruments service/resolvability→T2; quote store→T3; emitters→T4,T5; spot chain→T6; assumptions→T7; importer split + PositionMarketInput fold + r/q/vol chain→T8; API delta→T10; UI 4 tabs→T11-14; Pricing Params page + Position Detail→T15; page removal→T16; agent tools→T17; LH repair + equivalence→T10/T18. Defaults-table rename: superseded by grounding adjustment #2. `price_type` for xlsx quotes: decided `mid` (T8).
- **Known-unread surfaces** (executor must read before editing, noted per task): `_market_profile_from_snapshot`, `hedging_universe.ContractInfo`, `services/domains/hedging.py`, the xlsx import fn in `pricing_profiles.py`, `Underlyings.tsx`, `MarketData.tsx`, `PricingParameters.tsx`, `Hedging.tsx`, `agents.py`.
- **Type consistency:** `record_quote`/`latest_quote` signatures fixed in T3 and used as such in T4-T6, T8-T10, T13.
