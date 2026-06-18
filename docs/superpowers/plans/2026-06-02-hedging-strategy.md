# Hedging Module Part 2 — Strategy Engine, Agent & UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Part 1's allowed hedging map into a per-underlying hedging strategy engine (4 strategies, lexicographic hard→soft integer-lot MILP), exposed through the desk agent and a Hedging-page UI panel, booking tagged hedge legs into the hedged portfolio.

**Architecture:** A pure DB-free MILP solver (`scipy.optimize.milp`) at the core; thin services around it for greek aggregation (from the latest `RiskRun`), leg proposal/pricing, band config, and atomic booking; one domain facade (`domains/hedging_strategy.py`) that the agent tools, HTTP endpoints, and UI all call. Built in three shippable phases (engine → agent → UI) after a Phase 0 integration step.

**Tech Stack:** Python 3.11, SQLAlchemy 2 / Alembic, FastAPI, pytest; scipy 1.16 (`optimize.milp`/HiGHS) + numpy; React + TypeScript + Vite + Vitest.

**Spec:** `docs/superpowers/specs/2026-06-02-hedging-strategy-design.md`

---

## File structure

**New backend files**
- `backend/app/services/hedging_solver.py` — pure staged MILP solver.
- `backend/app/services/hedging_strategy_registry.py` — 4 strategies → ordered tiers.
- `backend/app/services/hedging_greeks.py` — per-underlying greek aggregation from latest `RiskRun`.
- `backend/app/services/hedging_legs.py` — candidate-leg proposal + per-contract greek pricing.
- `backend/app/services/domains/hedging_strategy.py` — orchestration facade (`solve_hedge`, `book_hedge`, band config).
- `backend/app/tools/hedging.py` — agent `@tool` wrappers.
- `backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md` — workflow.
- `backend/app/skills/references/hedging/strategy.md` — reference doc.
- `backend/alembic/versions/0022_hedge_bands.py` — `hedge_bands` migration.

**Modified backend**
- `backend/app/services/quantark.py` — add `spot` to `_risk_row`.
- `backend/app/models.py` — `HedgeBand` model.
- `backend/app/schemas.py` — hedging-strategy request/response schemas.
- `backend/app/tools/__init__.py` — register hedging tools in `QUANT_AGENT_TOOLS`.
- `backend/app/services/deep_agent/personas.py` — add `/skills/workflows/hedging/` to risk persona.
- `backend/app/main.py` — `/api/hedging/{hedgeable,bands,solve,book}` endpoints.

**Modified tests**
- `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`, `tests/test_workflow_skills_phase3.py` — add the `hedging` domain set + counts.

**New frontend**
- `frontend/src/routes/HedgeStrategy.tsx` (presentational) + `HedgeStrategy.live.tsx` (container) + `HedgeStrategy.css`, with tests; tab toggle added to `Hedging.tsx`/`main.tsx`.

---

## Phase 0 — Integration (merge Part 1, fix Alembic, new worktree)

### Task 0.1: Merge Part 1 into main and linearize Alembic history

**Files:**
- Rename: `backend/alembic/versions/0020_hedging_instrument_catalog.py` → `0021_hedging_instrument_catalog.py`
- Modify: that file's `revision` / `down_revision`

- [ ] **Step 1: Merge the Part 1 branch into main**

```bash
cd /Users/fuxinyao/open-otc-trading
git checkout main && git pull
git merge --no-ff feature/hedging-instrument-management
```
Expect a clean merge of the new files; if `backend/app/models.py` / `backend/app/main.py` / `backend/app/schemas.py` conflict, keep **both** sides (Part 1 additions + main's currency-convention additions) — they are additive, non-overlapping blocks.

- [ ] **Step 2: Confirm the double-head, then re-chain**

```bash
cd backend && alembic heads
```
Expected: TWO heads — `0020_currency_convention` and `0020_hedging_instrument_catalog`.

Edit `backend/alembic/versions/0020_hedging_instrument_catalog.py`:
```python
revision = "0021_hedging_instrument_catalog"
down_revision = "0020_currency_convention"
```
Then rename the file:
```bash
git mv backend/alembic/versions/0020_hedging_instrument_catalog.py \
       backend/alembic/versions/0021_hedging_instrument_catalog.py
```

- [ ] **Step 3: Verify a single linear head + migrations apply**

```bash
cd backend && alembic heads        # expect ONE head: 0021_hedging_instrument_catalog
alembic upgrade head               # against a scratch DB; expect no error
cd .. && python -m pytest tests/test_hedging_migration.py -q
```
Expected: one head; upgrade succeeds; migration test passes.

- [ ] **Step 4: Run the full backend + frontend suites**

```bash
python -m pytest -q
cd frontend && npm test -- --run
```
Expected: green except the 6 known optional-langchain-dep failures (sandbox/quickjs/deepseek). Fix any real merge breakage before continuing.

- [ ] **Step 5: Commit the merge + re-chain**

```bash
git add -A
git commit -m "chore: merge hedging Part 1; linearize alembic (0021) after currency-convention"
```

### Task 0.2: Create the Part 2 worktree and commit the spec

**Files:**
- Move into place: `docs/superpowers/specs/2026-06-02-hedging-strategy-design.md`, `docs/superpowers/plans/2026-06-02-hedging-strategy.md`

- [ ] **Step 1: Create an isolated worktree off main**

```bash
cd /Users/fuxinyao/open-otc-trading
git worktree add -b feature/hedging-strategy ../open-otc-trading-strategy main
```

- [ ] **Step 2: Place the spec + plan on the branch and commit**

Copy this spec and plan into `../open-otc-trading-strategy/docs/superpowers/{specs,plans}/` (if not already tracked), then:
```bash
cd ../open-otc-trading-strategy
git add docs/superpowers/specs/2026-06-02-hedging-strategy-design.md \
        docs/superpowers/plans/2026-06-02-hedging-strategy.md
git commit -m "docs(hedging): Part 2 strategy engine spec + plan"
```
All remaining tasks run inside `../open-otc-trading-strategy`.

---

## Phase A — Engine (backend only)

> Run backend tests with `python -m pytest` from the worktree root (repo root on `sys.path`, per the Part-1 migration-test gotcha).

### Task A1: Add `spot` to the stored risk row

**Files:**
- Modify: `backend/app/services/quantark.py` (`_risk_row`, and its callers in `_calculate_position_risk`)
- Test: `tests/test_risk_row_spot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_risk_row_spot.py
from app.models import Portfolio, Position
from app.schemas import PricingEnvironmentSnapshot
from app.services.quantark import calculate_portfolio_risk


def test_risk_row_includes_spot_and_cash_greeks(session):
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add(pf)
    session.flush()
    pos = Position(
        portfolio_id=pf.id, underlying="000905.SH",
        product_type="EuropeanVanillaOption",
        product_kwargs={"strike": 100.0, "expiry": "2026-12-31", "option_type": "call",
                        "initial_price": 100.0},
        engine_name="BlackScholesEngine", quantity=1.0, entry_price=0.0, status="open",
    )
    session.add(pos)
    session.flush()
    from types import SimpleNamespace
    portfolio = SimpleNamespace(positions=[pos])
    market = PricingEnvironmentSnapshot(spot=100.0, volatility=0.2, rate=0.03)
    result = calculate_portfolio_risk(portfolio, market)
    row = result["positions"][0]
    assert "spot" in row and row["spot"] == 100.0
    # delta_cash / gamma_cash were already stored (RISK_GREEK_KEYS includes them)
    assert "delta_cash" in row and "gamma_cash" in row
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_risk_row_spot.py -v`
Expected: FAIL — `assert "spot" in row` (KeyError/AssertionError).

- [ ] **Step 3: Add the `spot` field**

In `backend/app/services/quantark.py`, give `_risk_row` a `spot` parameter and emit it. Change the signature line:
```python
def _risk_row(
    position: Position,
    price: float,
    market_value: float,
    gross_notional: float,
    pnl: float,
    delta_proxy: float,
    pricing_ok: bool,
    pricing_error: str | None,
    greeks: dict[str, float] | None = None,
    greeks_ok: bool = False,
    greeks_error: str | None = None,
    pricing_diagnostics: dict[str, Any] | None = None,
    currency: str | None = None,
    spot: float | None = None,
) -> dict[str, Any]:
```
Add to the `row` dict (next to `"currency": currency,`):
```python
        "spot": spot,
```
Then pass `spot=position_market.spot` at every `_risk_row(...)` call site inside `_calculate_position_risk` (the exclusion path, the pricing-failure path, and the success path) and inside `_calculate_deltaone_risk` if it builds rows. Each of those functions already has `position_market` in scope.

- [ ] **Step 4: Run the test + the existing risk suite**

Run: `python -m pytest tests/test_risk_row_spot.py tests/test_risk_engine.py tests/test_risk_currency.py -v`
Expected: PASS. If a golden/snapshot test asserts the exact row key-set, update it to include `spot`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/quantark.py tests/test_risk_row_spot.py
git commit -m "feat(risk): store per-position spot on risk rows (hedging aggregation input)"
```

### Task A2: `HedgeBand` model + migration `0022_hedge_bands`

**Files:**
- Modify: `backend/app/models.py`
- Create: `backend/alembic/versions/0022_hedge_bands.py`
- Test: `tests/test_hedge_bands_model.py`, `tests/test_hedge_bands_migration.py`

- [ ] **Step 1: Write the failing model test**

```python
# tests/test_hedge_bands_model.py
from app.models import HedgeBand, Underlying


def test_hedge_band_round_trips(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u)
    session.flush()
    band = HedgeBand(
        underlying_id=u.id, delta_cash_band=500000.0, gamma_cash_band=50000.0,
        vega_band=10000.0, currency="CNY",
    )
    session.add(band)
    session.flush()
    assert session.query(HedgeBand).one().delta_cash_band == 500000.0


def test_hedge_band_defaults_row_has_null_underlying(session):
    band = HedgeBand(
        underlying_id=None, delta_cash_band=300000.0, gamma_cash_band=30000.0,
        vega_band=8000.0, currency="CNY",
    )
    session.add(band)
    session.flush()
    assert session.query(HedgeBand).filter(HedgeBand.underlying_id.is_(None)).one()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedge_bands_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'HedgeBand'`.

- [ ] **Step 3: Add the model**

In `backend/app/models.py` (after the Part-1 `HedgeMapEntry` class):
```python
class HedgeBand(Base):
    __tablename__ = "hedge_bands"
    __table_args__ = (UniqueConstraint("underlying_id", name="uq_hedge_bands_underlying"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    underlying_id: Mapped[int | None] = mapped_column(
        ForeignKey("underlyings.id"), nullable=True, index=True
    )
    delta_cash_band: Mapped[float] = mapped_column(Float, nullable=False)
    gamma_cash_band: Mapped[float] = mapped_column(Float, nullable=False)
    vega_band: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="CNY", nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
```
(Confirm `UniqueConstraint`, `Float`, `ForeignKey`, `utcnow` are already imported at the top of `models.py`; they are used by existing models.)

- [ ] **Step 4: Run the model test**

Run: `python -m pytest tests/test_hedge_bands_model.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing migration test**

```python
# tests/test_hedge_bands_migration.py
import importlib.util
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load(name):
    path = Path("backend/alembic/versions") / name
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hedge_bands_upgrade_creates_table(tmp_path):
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path/'m.sqlite3'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE underlyings (id INTEGER PRIMARY KEY)"
        ))
        mod = _load("0022_hedge_bands.py")
        mod.op = Operations(MigrationContext.configure(conn))
        mod.upgrade()
        insp = sa.inspect(conn)
        assert "hedge_bands" in insp.get_table_names()
        cols = {c["name"] for c in insp.get_columns("hedge_bands")}
        assert {"delta_cash_band", "gamma_cash_band", "vega_band", "underlying_id"} <= cols
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedge_bands_migration.py -v`
Expected: FAIL — file `0022_hedge_bands.py` not found.

- [ ] **Step 7: Write the migration (migration-local Core tables, idempotent)**

```python
# backend/alembic/versions/0022_hedge_bands.py
"""hedge_bands table"""
from alembic import op
import sqlalchemy as sa

revision = "0022_hedge_bands"
down_revision = "0021_hedging_instrument_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "hedge_bands" in insp.get_table_names():
        return
    op.create_table(
        "hedge_bands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("underlying_id", sa.Integer(),
                  sa.ForeignKey("underlyings.id"), nullable=True),
        sa.Column("delta_cash_band", sa.Float(), nullable=False),
        sa.Column("gamma_cash_band", sa.Float(), nullable=False),
        sa.Column("vega_band", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("updated_by", sa.String(length=80), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("underlying_id", name="uq_hedge_bands_underlying"),
    )
    op.create_index("ix_hedge_bands_underlying_id", "hedge_bands", ["underlying_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "hedge_bands" not in insp.get_table_names():
        return
    op.drop_index("ix_hedge_bands_underlying_id", table_name="hedge_bands")
    op.drop_table("hedge_bands")
```

- [ ] **Step 8: Run both tests**

Run: `python -m pytest tests/test_hedge_bands_model.py tests/test_hedge_bands_migration.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0022_hedge_bands.py \
        tests/test_hedge_bands_model.py tests/test_hedge_bands_migration.py
git commit -m "feat(hedging): HedgeBand model + 0022 migration"
```

### Task A3: Band config resolution (defaults + override)

**Files:**
- Create: `backend/app/services/domains/hedging_strategy.py` (start the facade with band helpers)
- Test: `tests/test_hedge_band_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedge_band_config.py
from app.models import HedgeBand, Underlying
from app.services.domains import hedging_strategy as hs


def _underlying(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u); session.flush()
    return u


def test_resolve_falls_back_to_defaults(session):
    session.add(HedgeBand(underlying_id=None, delta_cash_band=300000.0,
                          gamma_cash_band=30000.0, vega_band=8000.0, currency="CNY"))
    u = _underlying(session)
    session.flush()
    bands = hs.resolve_bands(session, underlying_id=u.id)
    assert bands == {"delta": 300000.0, "gamma": 30000.0, "vega": 8000.0}


def test_resolve_prefers_underlying_override(session):
    u = _underlying(session)
    session.add(HedgeBand(underlying_id=None, delta_cash_band=300000.0,
                          gamma_cash_band=30000.0, vega_band=8000.0, currency="CNY"))
    session.add(HedgeBand(underlying_id=u.id, delta_cash_band=500000.0,
                          gamma_cash_band=50000.0, vega_band=10000.0, currency="CNY"))
    session.flush()
    bands = hs.resolve_bands(session, underlying_id=u.id)
    assert bands["delta"] == 500000.0


def test_set_bands_upserts(session):
    u = _underlying(session)
    hs.set_bands(session, underlying_id=u.id,
                 bands={"delta": 1.0, "gamma": 2.0, "vega": 3.0}, actor="desk_user")
    session.flush()
    hs.set_bands(session, underlying_id=u.id,
                 bands={"delta": 9.0, "gamma": 2.0, "vega": 3.0}, actor="desk_user")
    session.flush()
    assert session.query(HedgeBand).filter_by(underlying_id=u.id).one().delta_cash_band == 9.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedge_band_config.py -v`
Expected: FAIL — module/function missing.

- [ ] **Step 3: Implement band helpers in the facade**

```python
# backend/app/services/domains/hedging_strategy.py
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from ...models import HedgeBand

# Hard fallback if no defaults row exists yet.
_BUILTIN_DEFAULTS = {"delta": 500000.0, "gamma": 50000.0, "vega": 10000.0}


def resolve_bands(session: Session, *, underlying_id: int) -> dict[str, float]:
    row = (
        session.query(HedgeBand).filter(HedgeBand.underlying_id == underlying_id).one_or_none()
        or session.query(HedgeBand).filter(HedgeBand.underlying_id.is_(None)).one_or_none()
    )
    if row is None:
        return dict(_BUILTIN_DEFAULTS)
    return {"delta": row.delta_cash_band, "gamma": row.gamma_cash_band, "vega": row.vega_band}


def set_bands(
    session: Session, *, underlying_id: int | None,
    bands: dict[str, float], actor: str | None = None,
) -> HedgeBand:
    row = (
        session.query(HedgeBand)
        .filter(HedgeBand.underlying_id.is_(None) if underlying_id is None
                else HedgeBand.underlying_id == underlying_id)
        .one_or_none()
    )
    if row is None:
        row = HedgeBand(underlying_id=underlying_id, currency="CNY")
        session.add(row)
    row.delta_cash_band = float(bands["delta"])
    row.gamma_cash_band = float(bands["gamma"])
    row.vega_band = float(bands["vega"])
    row.updated_by = actor
    row.updated_at = datetime.utcnow()
    return row
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_hedge_band_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/hedging_strategy.py tests/test_hedge_band_config.py
git commit -m "feat(hedging): per-underlying band resolution + upsert"
```

### Task A4: Per-underlying greek aggregation from the latest RiskRun

**Files:**
- Create: `backend/app/services/hedging_greeks.py`
- Test: `tests/test_hedging_greeks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_greeks.py
from app.models import Portfolio, RiskRun
from app.services import hedging_greeks


def _run(session, pf_id, positions):
    run = RiskRun(portfolio_id=pf_id, status="completed",
                  metrics={"positions": positions})
    session.add(run); session.flush()
    return run


def test_aggregate_sums_cash_greeks_by_underlying(session):
    pf = Portfolio(name="pf", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 800000.0, "gamma_cash": 60000.0,
         "vega": 4000.0, "spot": 5600.0, "greeks_ok": True},
        {"underlying": "000905.SH", "delta_cash": 440000.0, "gamma_cash": 28000.0,
         "vega": 2500.0, "spot": 5600.0, "greeks_ok": True},
        {"underlying": "000300.SH", "delta_cash": 100000.0, "gamma_cash": 9000.0,
         "vega": 1000.0, "spot": 3900.0, "greeks_ok": True},
    ])
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["status"] == "ok"
    by = {u["underlying"]: u for u in out["underlyings"]}
    assert by["000905.SH"]["targets"] == {"delta": 1240000.0, "gamma": 88000.0, "vega": 6500.0}
    assert by["000905.SH"]["spot"] == 5600.0


def test_aggregate_reports_missing_run(session):
    pf = Portfolio(name="pf2", base_currency="CNY"); session.add(pf); session.flush()
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["status"] == "no_risk_run"


def test_aggregate_skips_rows_without_greeks(session):
    pf = Portfolio(name="pf3", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True},
        {"underlying": "000905.SH", "delta_cash": 9999.0, "gamma_cash": 9999.0,
         "vega": 9999.0, "spot": 5600.0, "greeks_ok": False},
    ])
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    by = {u["underlying"]: u for u in out["underlyings"]}
    assert by["000905.SH"]["targets"]["delta"] == 5.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_greeks.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the aggregator**

```python
# backend/app/services/hedging_greeks.py
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import RiskRun
from .domains import risk as risk_svc


def aggregate_by_underlying(session: Session, *, portfolio_id: int) -> dict[str, Any]:
    """Per-underlying {delta_cash, gamma_cash, vega} from the latest completed RiskRun.

    Returns {"status": "ok"|"no_risk_run", "risk_run_id", "created_at",
             "underlyings": [{"underlying", "targets": {...}, "spot"}]}.
    """
    run: RiskRun | None = risk_svc.get_latest_run(portfolio_id=portfolio_id, session=session)
    if run is None:
        return {"status": "no_risk_run", "portfolio_id": portfolio_id,
                "message": "No completed risk run for this portfolio. Run risk first."}
    rows = (run.metrics or {}).get("positions", [])
    acc: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not row.get("greeks_ok"):
            continue
        u = row.get("underlying") or "UNKNOWN"
        bucket = acc.setdefault(u, {"delta": 0.0, "gamma": 0.0, "vega": 0.0, "spot": None})
        bucket["delta"] += float(row.get("delta_cash", 0.0) or 0.0)
        bucket["gamma"] += float(row.get("gamma_cash", 0.0) or 0.0)
        bucket["vega"] += float(row.get("vega", 0.0) or 0.0)
        if bucket["spot"] is None and row.get("spot"):
            bucket["spot"] = float(row["spot"])
    underlyings = [
        {"underlying": u,
         "targets": {"delta": v["delta"], "gamma": v["gamma"], "vega": v["vega"]},
         "spot": v["spot"]}
        for u, v in sorted(acc.items())
    ]
    return {"status": "ok", "portfolio_id": portfolio_id, "risk_run_id": run.id,
            "created_at": run.created_at.isoformat(), "underlyings": underlyings}
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_hedging_greeks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_greeks.py tests/test_hedging_greeks.py
git commit -m "feat(hedging): aggregate per-underlying greeks from latest RiskRun"
```

### Task A5: Strategy registry

**Files:**
- Create: `backend/app/services/hedging_strategy_registry.py`
- Test: `tests/test_hedging_strategy_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_strategy_registry.py
import pytest

from app.services.hedging_strategy_registry import STRATEGIES, tiers_for


def test_four_strategies_registered():
    assert set(STRATEGIES) == {
        "delta_neutral", "delta_neutral_enhanced",
        "delta_gamma_neutral", "full_neutral",
    }


def test_delta_gamma_neutral_tiers():
    tiers = tiers_for("delta_gamma_neutral")
    assert tiers == [
        {"kind": "hard", "greeks": ["delta", "gamma"]},
        {"kind": "soft", "greeks": ["vega"]},
    ]


def test_full_neutral_is_all_hard():
    assert tiers_for("full_neutral") == [
        {"kind": "hard", "greeks": ["delta", "gamma", "vega"]},
    ]


def test_unknown_strategy_raises():
    with pytest.raises(KeyError):
        tiers_for("nope")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_strategy_registry.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the registry**

```python
# backend/app/services/hedging_strategy_registry.py
from __future__ import annotations

from copy import deepcopy

# strategy -> ordered tiers; each tier minimized lexicographically.
STRATEGIES: dict[str, list[dict]] = {
    "delta_neutral": [
        {"kind": "hard", "greeks": ["delta"]},
    ],
    "delta_neutral_enhanced": [
        {"kind": "hard", "greeks": ["delta"]},
        {"kind": "soft", "greeks": ["gamma", "vega"]},
    ],
    "delta_gamma_neutral": [
        {"kind": "hard", "greeks": ["delta", "gamma"]},
        {"kind": "soft", "greeks": ["vega"]},
    ],
    "full_neutral": [
        {"kind": "hard", "greeks": ["delta", "gamma", "vega"]},
    ],
}


def tiers_for(strategy: str) -> list[dict]:
    return deepcopy(STRATEGIES[strategy])
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_hedging_strategy_registry.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_strategy_registry.py tests/test_hedging_strategy_registry.py
git commit -m "feat(hedging): strategy registry (4 strategies -> lexicographic tiers)"
```

### Task A6: The staged MILP solver (centerpiece)

**Files:**
- Create: `backend/app/services/hedging_solver.py`
- Test: `tests/test_hedging_solver.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hedging_solver.py
from app.services.hedging_solver import Leg, solve
from app.services.hedging_strategy_registry import tiers_for

BANDS = {"delta": 50000.0, "gamma": 50000.0, "vega": 4000.0}


def test_delta_neutral_single_future():
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 1000000.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral"))
    assert res.status == "feasible"
    assert res.quantities["IC2406"] == -10
    assert abs(res.residual["delta"]) <= BANDS["delta"]


def test_delta_gamma_neutral_tight_bands_pin_exact_solution():
    # Tight bands force residual delta AND gamma to ~0, pinning the unique 2x2 fit.
    tight = {"delta": 1.0, "gamma": 1.0, "vega": 1.0}
    legs = [
        Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0),
        Leg(key="IO2406C", delta=20000.0, gamma=5000.0, vega=0.0),
    ]
    res = solve(targets={"delta": 1000000.0, "gamma": 100000.0, "vega": 0.0},
                legs=legs, bands=tight, tiers=tiers_for("delta_gamma_neutral"))
    assert res.status == "feasible"
    # gamma: 100000 + (-20)*5000 = 0 ; delta: 1000000 + (-6)*100000 + (-20)*20000 = 0
    assert res.quantities == {"IC2406": -6, "IO2406C": -20}
    assert abs(res.residual["delta"]) <= 1.0
    assert abs(res.residual["gamma"]) <= 1.0


def test_delta_gamma_neutral_loose_bands_minimize_lots():
    # With loose bands the solver meets the bands with the FEWEST total lots, not
    # an exact zero — gamma sits at its band edge (qo=-10), delta cleaned by qf=-8.
    legs = [
        Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0),
        Leg(key="IO2406C", delta=20000.0, gamma=5000.0, vega=0.0),
    ]
    res = solve(targets={"delta": 1000000.0, "gamma": 100000.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_gamma_neutral"))
    assert res.status == "feasible"
    assert res.quantities == {"IC2406": -8, "IO2406C": -10}
    assert abs(res.residual["delta"]) <= BANDS["delta"]
    assert abs(res.residual["gamma"]) <= BANDS["gamma"]


def test_gamma_hard_with_no_option_is_infeasible():
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 0.0, "gamma": 88000.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_gamma_neutral"))
    assert res.status == "infeasible"
    binding = {b["greek"] for b in res.binding}
    assert "gamma" in binding


def test_parsimony_prefers_smallest_total_lots():
    # Two identical futures; the solver must not split lots across both.
    legs = [
        Leg(key="A", delta=100000.0, gamma=0.0, vega=0.0),
        Leg(key="B", delta=100000.0, gamma=0.0, vega=0.0),
    ]
    res = solve(targets={"delta": 500000.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral"))
    assert res.status == "feasible"
    assert abs(res.quantities["A"]) + abs(res.quantities["B"]) == 5


def test_zero_target_yields_no_trade():
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 0.0, "gamma": 0.0, "vega": 0.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral"))
    assert res.quantities["IC2406"] == 0
    assert res.status == "feasible"


def test_soft_tier_violation_allowed_when_hard_met():
    # enhanced: delta hard (met by future), gamma/vega soft (future can't move them)
    legs = [Leg(key="IC2406", delta=100000.0, gamma=0.0, vega=0.0)]
    res = solve(targets={"delta": 300000.0, "gamma": 90000.0, "vega": 9000.0},
                legs=legs, bands=BANDS, tiers=tiers_for("delta_neutral_enhanced"))
    assert res.status == "feasible"          # hard (delta) satisfied
    assert res.quantities["IC2406"] == -3
    assert res.in_band["gamma"] is False     # soft band breached, but allowed
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_solver.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the solver**

```python
# backend/app/services/hedging_solver.py
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

GREEKS = ("delta", "gamma", "vega")


@dataclass
class Leg:
    key: str
    delta: float  # per-contract delta_cash
    gamma: float  # per-contract gamma_cash
    vega: float   # per-contract vega (raw)


@dataclass
class SolveResult:
    status: str                       # "feasible" | "infeasible"
    quantities: dict[str, int]
    residual: dict[str, float]
    in_band: dict[str, bool]
    binding: list[dict] = field(default_factory=list)


def solve(*, targets, legs, bands, tiers, q_max=1_000_000, tol=1e-6, eps=1e-6) -> SolveResult:
    n = len(legs)
    constrained: list[str] = []
    for tier in tiers:
        for g in tier["greeks"]:
            if g not in constrained:
                constrained.append(g)
    k = len(constrained)
    N = n + k + n  # q[0:n], e[n:n+k], a[n+k:]

    def ei(g: str) -> int:
        return n + constrained.index(g)

    def ai(i: int) -> int:
        return n + k + i

    A_rows: list[np.ndarray] = []
    lo: list[float] = []
    hi: list[float] = []
    # band-with-slack: Σ qᵢcᵢg − e_g ≤ B_g − T_g ;  −Σ qᵢcᵢg − e_g ≤ B_g + T_g
    for g in constrained:
        c = np.array([getattr(legs[i], g) for i in range(n)])
        Tg = float(targets.get(g, 0.0)); Bg = float(bands[g])
        r1 = np.zeros(N); r1[:n] = c; r1[ei(g)] = -1.0
        A_rows.append(r1); lo.append(-np.inf); hi.append(Bg - Tg)
        r2 = np.zeros(N); r2[:n] = -c; r2[ei(g)] = -1.0
        A_rows.append(r2); lo.append(-np.inf); hi.append(Bg + Tg)
    # |q|: a_i − q_i ≥ 0 ; a_i + q_i ≥ 0
    for i in range(n):
        u1 = np.zeros(N); u1[ai(i)] = 1.0; u1[i] = -1.0
        A_rows.append(u1); lo.append(0.0); hi.append(np.inf)
        u2 = np.zeros(N); u2[ai(i)] = 1.0; u2[i] = 1.0
        A_rows.append(u2); lo.append(0.0); hi.append(np.inf)

    base = [LinearConstraint(np.array(A_rows), np.array(lo), np.array(hi))] if A_rows else []
    lb = np.concatenate([np.full(n, -q_max), np.zeros(k), np.zeros(n)])
    ub = np.concatenate([np.full(n, q_max), np.full(k, np.inf), np.full(n, np.inf)])
    bounds = Bounds(lb, ub)
    integrality = np.concatenate([np.ones(n), np.zeros(k), np.zeros(n)])

    frozen: list[LinearConstraint] = []
    best_x: np.ndarray | None = None

    def _run(c_obj: np.ndarray):
        return milp(c=c_obj, constraints=base + frozen,
                    integrality=integrality, bounds=bounds)

    # lexicographic tiers (hard then soft), each frozen at its optimum
    for tier in tiers:
        c_obj = np.zeros(N)
        for g in tier["greeks"]:
            c_obj[ei(g)] = 1.0
        res = _run(c_obj)
        if not res.success:
            return SolveResult(status="infeasible", quantities={}, residual={},
                               in_band={}, binding=[{"greek": "_model", "shortfall": float("inf")}])
        best_x = res.x
        frozen.append(LinearConstraint(c_obj, -np.inf, float(res.fun) + eps))
    # parsimony: smallest total lots
    c_par = np.zeros(N)
    for i in range(n):
        c_par[ai(i)] = 1.0
    res = _run(c_par)
    if res.success:
        best_x = res.x

    x = best_x if best_x is not None else np.zeros(N)
    quantities = {legs[i].key: int(round(x[i])) for i in range(n)}
    residual: dict[str, float] = {}
    in_band: dict[str, bool] = {}
    for g in GREEKS:
        Rg = float(targets.get(g, 0.0)) + sum(quantities[legs[i].key] * getattr(legs[i], g)
                                              for i in range(n))
        residual[g] = Rg
        if g in constrained:
            in_band[g] = abs(Rg) <= float(bands[g]) + tol

    hard = [g for tier in tiers if tier["kind"] == "hard" for g in tier["greeks"]]
    binding = [{"greek": g, "shortfall": abs(residual[g]) - float(bands[g])}
               for g in hard if not in_band.get(g, True)]
    return SolveResult(status="infeasible" if binding else "feasible",
                       quantities=quantities, residual=residual,
                       in_band=in_band, binding=binding)
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_hedging_solver.py -v`
Expected: PASS (all six).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_solver.py tests/test_hedging_solver.py
git commit -m "feat(hedging): staged lexicographic MILP solver (scipy/HiGHS)"
```

### Task A7: Leg proposal + per-contract greek pricing

**Files:**
- Create: `backend/app/services/hedging_legs.py`
- Test: `tests/test_hedging_legs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_legs.py
from datetime import date

from app.models import HedgeInstrument, HedgeMapEntry, Underlying
from app.services import hedging_legs


def _underlying(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    session.add(u); session.flush()
    return u


def _mark(session, u, code, *, itype="future", option_type=None, strike=None,
          family="index_future", mult=200.0):
    inst = HedgeInstrument(
        underlying_id=u.id, family=family, series_root="IC", exchange="CFFEX",
        contract_code=code, instrument_type=itype, option_type=option_type,
        strike=strike, expiry=date(2026, 6, 21), multiplier=mult,
        last_price=5600.0, status="live",
    )
    session.add(inst)
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code=code, family=family,
        series_root="IC", instrument_type=itype, option_type=option_type,
        strike=strike, reconcile_status="active",
    ))
    session.flush()
    return inst


def test_propose_picks_future_for_delta_and_option_for_gamma(session):
    u = _underlying(session)
    _mark(session, u, "IC2406", itype="future")
    _mark(session, u, "IO2406C", itype="option", option_type="call", strike=5600.0,
          family="index_option", mult=100.0)
    legs = hedging_legs.propose(session, underlying_id=u.id,
                                strategy="delta_gamma_neutral")
    roles = {leg["role"] for leg in legs}
    assert "delta" in roles and "gamma_vega" in roles


def test_future_leg_greeks_are_closed_form(session):
    u = _underlying(session)
    inst = _mark(session, u, "IC2406", itype="future", mult=200.0)
    priced = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "delta"}],
                                spot=5600.0)
    leg = priced[0]
    assert leg["delta"] == 200.0 * 5600.0   # delta_cash per contract = mult * spot
    assert leg["gamma"] == 0.0 and leg["vega"] == 0.0


def test_only_active_map_instruments_are_proposed(session):
    u = _underlying(session)
    _mark(session, u, "IC2406", itype="future")
    # an instrument with NO map entry must not be proposed
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC9999", instrument_type="future", multiplier=200.0,
        expiry=date(2026, 9, 20), status="live",
    ))
    session.flush()
    legs = hedging_legs.propose(session, underlying_id=u.id, strategy="delta_neutral")
    assert all(leg["contract_code"] != "IC9999" for leg in legs)
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_legs.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement leg proposal + pricing**

```python
# backend/app/services/hedging_legs.py
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ..models import HedgeInstrument, HedgeMapEntry, Position
from ..schemas import PricingEnvironmentSnapshot
from .hedging_strategy_registry import tiers_for
from .risk_engine import compute_position_greeks

_OPTION_TYPES = {"option"}


def _active_instruments(session: Session, underlying_id: int) -> list[HedgeInstrument]:
    active_keys = {
        (e.exchange, e.contract_code)
        for e in session.query(HedgeMapEntry.exchange, HedgeMapEntry.contract_code)
        .filter(HedgeMapEntry.underlying_id == underlying_id,
                HedgeMapEntry.reconcile_status == "active")
    }
    if not active_keys:
        return []
    rows = (
        session.query(HedgeInstrument)
        .filter(HedgeInstrument.underlying_id == underlying_id,
                HedgeInstrument.status == "live")
        .all()
    )
    return [r for r in rows if (r.exchange, r.contract_code) in active_keys]


def _role_needs(strategy: str) -> set[str]:
    greeks = {g for tier in tiers_for(strategy) for g in tier["greeks"]}
    return greeks


def propose(session: Session, *, underlying_id: int, strategy: str) -> list[dict[str, Any]]:
    """Default leg set: a future/spot for delta, plus a near-ATM option when the
    strategy constrains gamma/vega. User can swap before solving."""
    insts = _active_instruments(session, underlying_id)
    needs = _role_needs(strategy)
    legs: list[dict[str, Any]] = []

    futures = [i for i in insts if i.instrument_type not in _OPTION_TYPES]
    if futures:
        nearest = min(futures, key=lambda i: (i.expiry or _MAX_DATE))
        legs.append(_leg_dict(nearest, role="delta"))

    if needs & {"gamma", "vega"}:
        options = [i for i in insts if i.instrument_type in _OPTION_TYPES]
        if options:
            atm = min(options, key=lambda i: abs((i.strike or 0.0) - (i.last_price or 0.0)))
            legs.append(_leg_dict(atm, role="gamma_vega"))
    return legs


def _leg_dict(inst: HedgeInstrument, *, role: str) -> dict[str, Any]:
    return {
        "instrument_id": inst.id, "role": role, "exchange": inst.exchange,
        "contract_code": inst.contract_code, "instrument_type": inst.instrument_type,
        "option_type": inst.option_type, "strike": inst.strike,
        "expiry": inst.expiry.isoformat() if inst.expiry else None,
        "multiplier": inst.multiplier, "family": inst.family,
    }


def price(session: Session, legs: list[dict[str, Any]], *, spot: float) -> list[dict[str, Any]]:
    """Per-contract greek contributions: futures/spot closed form; options via BS.

    Cash conventions match the book rows: delta_cash = δ·S, gamma_cash = γ·S²/100,
    vega raw; each scaled by the contract multiplier.
    """
    out: list[dict[str, Any]] = []
    for leg in legs:
        inst = session.get(HedgeInstrument, leg["instrument_id"])
        mult = float(inst.multiplier or 1.0)
        if inst.instrument_type not in _OPTION_TYPES:
            d, g, v = mult * spot, 0.0, 0.0
            ok, error = True, None
        else:
            per = _option_unit_greeks(inst, spot)
            ok, error = per["ok"], per.get("error")
            d = per["delta"] * mult * spot
            g = per["gamma"] * mult * spot * spot / 100.0
            v = per["vega"] * mult
        out.append({**_leg_dict(inst, role=leg.get("role", "delta")),
                    "key": f"{inst.exchange}:{inst.contract_code}",
                    "delta": d, "gamma": g, "vega": v,
                    "priced_ok": ok, "price_error": error})
    return out


def _option_unit_greeks(inst: HedgeInstrument, spot: float) -> dict[str, Any]:
    """Per-unit BS greeks for a listed option via a transient Position."""
    expiry = inst.expiry.isoformat() if inst.expiry else None
    product_kwargs = {
        "strike": float(inst.strike or 0.0),
        "expiry": expiry,
        "option_type": (inst.option_type or "call").lower(),
        "initial_price": float(spot),
    }
    pos = Position(
        underlying=inst.contract_code, product_type="EuropeanVanillaOption",
        product_kwargs=product_kwargs, engine_name="BlackScholesEngine",
        engine_kwargs={}, quantity=1.0, entry_price=0.0,
    )
    market = PricingEnvironmentSnapshot(spot=float(spot))
    return compute_position_greeks(pos, market)


from datetime import date as _date  # noqa: E402
_MAX_DATE = _date(9999, 12, 31)
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_hedging_legs.py -v`
Expected: PASS. (If `compute_position_greeks` needs a market with vol/rate to produce non-degenerate option greeks, the closed-form future test still passes; the option-greek path is exercised in Task A8's integration test with a fuller market.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_legs.py tests/test_hedging_legs.py
git commit -m "feat(hedging): leg proposal from active map + per-contract greek pricing"
```

### Task A8: Orchestration — `solve_hedge` in the facade

**Files:**
- Modify: `backend/app/services/domains/hedging_strategy.py`
- Test: `tests/test_hedging_solve_orchestration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_solve_orchestration.py
from datetime import date

from app.models import (HedgeInstrument, HedgeMapEntry, Portfolio, Position,
                        RiskRun, Underlying)
from app.services.domains import hedging_strategy as hs


def _setup(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    inst = HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC2406", instrument_type="future", multiplier=200.0,
        expiry=date(2026, 6, 21), last_price=5600.0, status="live",
    )
    session.add(inst)
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active",
    ))
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 0.0,
         "vega": 0.0, "spot": 5600.0, "greeks_ok": True},
    ]}))
    session.flush()
    return pf, u, inst


def test_solve_hedge_delta_neutral(session):
    pf, u, inst = _setup(session)
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_neutral")
    assert out["status"] == "feasible"
    # per-contract delta_cash = 200 * 5600 = 1,120,000 ; target 1,120,000 -> q = -1
    leg = out["legs"][0]
    assert leg["quantity"] == -1
    assert out["risk_run_id"]


def test_solve_hedge_reports_no_risk_run(session):
    pf = Portfolio(name="empty", base_currency="CNY"); session.add(pf); session.flush()
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_neutral")
    assert out["status"] == "no_risk_run"
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_solve_orchestration.py -v`
Expected: FAIL — `solve_hedge` missing.

- [ ] **Step 3: Implement `solve_hedge`**

Append to `backend/app/services/domains/hedging_strategy.py`:
```python
from ...models import Underlying
from .. import hedging_greeks, hedging_legs, hedging_solver
from ..hedging_strategy_registry import tiers_for


def _underlying_id(session: Session, symbol: str) -> int | None:
    row = session.query(Underlying.id).filter(Underlying.symbol == symbol).one_or_none()
    return row[0] if row else None


def solve_hedge(
    session: Session, *, portfolio_id: int, underlying: str, strategy: str,
    legs: list[dict[str, Any]] | None = None, bands: dict[str, float] | None = None,
) -> dict[str, Any]:
    agg = hedging_greeks.aggregate_by_underlying(session, portfolio_id=portfolio_id)
    if agg["status"] != "ok":
        return {"status": agg["status"], "message": agg.get("message")}
    target = next((u for u in agg["underlyings"] if u["underlying"] == underlying), None)
    if target is None:
        return {"status": "no_exposure",
                "message": f"No greek exposure to {underlying} in risk run {agg['risk_run_id']}."}
    spot = target["spot"]
    if spot is None:
        return {"status": "no_spot",
                "message": f"Risk run {agg['risk_run_id']} has no spot for {underlying}."}

    uid = _underlying_id(session, underlying)
    if legs is None:
        legs = hedging_legs.propose(session, underlying_id=uid, strategy=strategy)
    priced = hedging_legs.price(session, legs, spot=spot)
    usable = [p for p in priced if p["priced_ok"]]
    warnings = [{"contract_code": p["contract_code"], "error": p["price_error"]}
                for p in priced if not p["priced_ok"]]

    resolved_bands = bands or resolve_bands(session, underlying_id=uid)
    solver_legs = [hedging_solver.Leg(key=p["key"], delta=p["delta"],
                                      gamma=p["gamma"], vega=p["vega"]) for p in usable]
    result = hedging_solver.solve(
        targets=target["targets"], legs=solver_legs, bands=resolved_bands,
        tiers=tiers_for(strategy),
    )
    by_key = {p["key"]: p for p in usable}
    out_legs = [{**by_key[k], "quantity": q} for k, q in result.quantities.items()]
    return {
        "status": result.status, "portfolio_id": portfolio_id, "underlying": underlying,
        "strategy": strategy, "risk_run_id": agg["risk_run_id"], "spot": spot,
        "targets": target["targets"], "bands": resolved_bands,
        "legs": out_legs, "residual": result.residual, "in_band": result.in_band,
        "binding": result.binding, "warnings": warnings,
    }
```

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_hedging_solve_orchestration.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/hedging_strategy.py tests/test_hedging_solve_orchestration.py
git commit -m "feat(hedging): solve_hedge orchestration (greeks + legs + bands + solver)"
```

### Task A9: Atomic, tagged booking — `book_hedge`

**Files:**
- Modify: `backend/app/services/domains/hedging_strategy.py`
- Test: `tests/test_hedging_book.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_book.py
from app.models import Portfolio, Position
from app.services.domains import hedging_strategy as hs


def test_book_hedge_persists_tagged_legs(session):
    pf = Portfolio(name="book", base_currency="CNY")  # kind defaults to "container"
    session.add(pf); session.flush()
    legs = [{
        "contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
        "instrument_type": "future", "option_type": None, "strike": None,
        "multiplier": 200.0, "quantity": -3,
    }]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=42, strategy="delta_neutral", legs=legs,
                        spot=5600.0)
    session.flush()
    assert len(out["position_ids"]) == 1
    pos = session.get(Position, out["position_ids"][0])
    assert pos.source_payload["hedge"]["risk_run_id"] == 42
    assert pos.source_payload["hedge"]["is_hedge"] is True
    assert pos.source_trade_id == "HEDGE:42:1"
    assert pos.quantity == -3


def test_book_hedge_skips_zero_quantity_legs(session):
    pf = Portfolio(name="book2", base_currency="CNY")
    session.add(pf); session.flush()
    legs = [{"contract_code": "IC2406", "exchange": "CFFEX", "family": "index_future",
             "instrument_type": "future", "multiplier": 200.0, "quantity": 0}]
    out = hs.book_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                        risk_run_id=1, strategy="delta_neutral", legs=legs, spot=5600.0)
    assert out["position_ids"] == []
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_book.py -v`
Expected: FAIL — `book_hedge` missing.

- [ ] **Step 3: Implement `book_hedge`**

Append to `backend/app/services/domains/hedging_strategy.py`:
```python
from datetime import datetime as _dt

from .booking import BookingRequest, ProductBookingSpec, book_position

# QuantArk class per hedge family/instrument type.
_QUANTARK_CLASS = {"future": "Futures", "spot": "SpotInstrument", "option": "EuropeanVanillaOption"}
_PRODUCT_FAMILY = {"future": "futures", "spot": "spot", "option": "vanilla_option"}
_ENGINE = {"future": "DeltaOneEngine", "spot": "DeltaOneEngine", "option": "BlackScholesEngine"}


def _leg_terms(leg: dict[str, Any], spot: float) -> dict[str, Any]:
    # Keep terms to QuantArk-accepted kwargs; the contract identity (exchange/
    # contract_code) is preserved in source_payload.hedge, not in product terms
    # (DeltaOne constructors reject contract_code — Part 1 carried it as _otc_).
    itype = leg["instrument_type"]
    if itype in ("future", "spot"):
        return {"initial_price": float(spot),
                "contract_multiplier": float(leg.get("multiplier") or 1.0)}
    return {"initial_price": float(spot), "strike": float(leg.get("strike") or spot),
            "expiry": leg.get("expiry"),
            "option_type": (leg.get("option_type") or "call").lower(),
            "contract_multiplier": float(leg.get("multiplier") or 1.0)}


def book_hedge(
    session: Session, *, portfolio_id: int, underlying: str, risk_run_id: int,
    strategy: str, legs: list[dict[str, Any]], spot: float, actor: str = "desk_user",
) -> dict[str, Any]:
    """Atomically book each non-zero leg into the portfolio, tagged as a hedge."""
    position_ids: list[int] = []
    n = 0
    for leg in legs:
        qty = int(leg.get("quantity") or 0)
        if qty == 0:
            continue
        n += 1
        itype = leg["instrument_type"]
        role = "gamma_vega" if itype == "option" else "delta"
        spec = ProductBookingSpec(
            asset_class="equity", product_family=_PRODUCT_FAMILY[itype],
            quantark_class=_QUANTARK_CLASS[itype], underlying=underlying,
            currency="CNY", terms=_leg_terms(leg, spot),
        )
        request = BookingRequest(
            portfolio_id=portfolio_id, product=spec, quantity=float(qty),
            entry_price=0.0, status="open", engine_name=_ENGINE[itype],
            source_trade_id=f"HEDGE:{risk_run_id}:{n}", source="hedge", actor=actor,
            source_payload={"hedge": {
                "is_hedge": True, "risk_run_id": risk_run_id, "strategy": strategy,
                "leg_role": role, "hedged_underlying": underlying,
                "exchange": leg.get("exchange"), "contract_code": leg.get("contract_code"),
                "solved_at": _dt.utcnow().isoformat(),
            }},
        )
        position = book_position(session, request)
        position_ids.append(position.id)
    return {"status": "booked", "portfolio_id": portfolio_id, "underlying": underlying,
            "risk_run_id": risk_run_id, "position_ids": position_ids}
```
Note: the whole call runs inside one `Session`; the endpoint/tool layer owns commit, so a raised error rolls the unit of work back (atomic per underlying). Confirm `ProductBookingSpec` accepts `product_family`/`quantark_class`/`terms` (it subclasses `ProductSpec`; mirror the fields used by `tools/positions.py::book_position_tool`).

- [ ] **Step 4: Run the test**

Run: `python -m pytest tests/test_hedging_book.py -v`
Expected: PASS. If `ProductBookingSpec`'s field names differ, align them with `ProductSpec` (read `backend/app/services/domains/products.py`) and re-run.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/hedging_strategy.py tests/test_hedging_book.py
git commit -m "feat(hedging): atomic tagged hedge booking (book_hedge)"
```

### Task A10: HTTP endpoints + schemas

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/main.py`
- Test: `tests/test_hedging_strategy_api.py`

- [ ] **Step 1: Write the failing API test**

```python
# tests/test_hedging_strategy_api.py
from datetime import date

from app.models import (HedgeInstrument, HedgeMapEntry, Portfolio, RiskRun, Underlying)


def _seed(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC2406", instrument_type="future", multiplier=200.0,
        expiry=date(2026, 6, 21), last_price=5600.0, status="live"))
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active"))
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 0.0,
         "vega": 0.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.commit()
    return pf, u


def test_hedgeable_endpoint(client, session):
    pf, u = _seed(session)
    r = client.get(f"/api/hedging/hedgeable?portfolio_id={pf.id}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["underlyings"][0]["underlying"] == "000905.SH"


def test_solve_endpoint(client, session):
    pf, u = _seed(session)
    r = client.post("/api/hedging/solve", json={
        "portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "delta_neutral"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "feasible"
    assert body["legs"][0]["quantity"] == -1


def test_book_endpoint(client, session):
    pf, u = _seed(session)
    r = client.post("/api/hedging/book", json={
        "portfolio_id": pf.id, "underlying": "000905.SH", "risk_run_id": 1,
        "strategy": "delta_neutral", "spot": 5600.0,
        "legs": [{"contract_code": "IC2406", "exchange": "CFFEX",
                  "family": "index_future", "instrument_type": "future",
                  "multiplier": 200.0, "quantity": -1}]})
    assert r.status_code == 200
    assert len(r.json()["position_ids"]) == 1


def test_bands_get_and_put(client, session):
    pf, u = _seed(session)
    put = client.put(f"/api/hedging/bands/{u.id}", json={
        "delta": 500000.0, "gamma": 50000.0, "vega": 10000.0})
    assert put.status_code == 200
    got = client.get(f"/api/hedging/bands?underlying_id={u.id}")
    assert got.json()["delta"] == 500000.0
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_strategy_api.py -v`
Expected: FAIL — 404 (endpoints not registered).

- [ ] **Step 3: Add schemas**

In `backend/app/schemas.py` (near the Part-1 hedging schemas):
```python
class HedgeBandsOut(BaseModel):
    delta: float
    gamma: float
    vega: float


class HedgeBandsIn(BaseModel):
    delta: float
    gamma: float
    vega: float


class HedgeSolveRequest(BaseModel):
    portfolio_id: int
    underlying: str
    strategy: str
    legs: list[dict[str, Any]] | None = None
    bands: dict[str, float] | None = None


class HedgeBookRequest(BaseModel):
    portfolio_id: int
    underlying: str
    risk_run_id: int
    strategy: str
    spot: float
    legs: list[dict[str, Any]]
```
(`Any` is already imported in `schemas.py`.)

- [ ] **Step 4: Add endpoints**

In `backend/app/main.py`, inside `create_app` next to the Part-1 `/api/hedging/*` routes:
```python
    @app.get("/api/hedging/hedgeable")
    def hedging_hedgeable(portfolio_id: int, session: Session = Depends(get_db)):
        from .services import hedging_greeks
        return hedging_greeks.aggregate_by_underlying(session, portfolio_id=portfolio_id)

    @app.get("/api/hedging/bands", response_model=HedgeBandsOut)
    def hedging_get_bands(underlying_id: int, session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        return hs.resolve_bands(session, underlying_id=underlying_id)

    @app.put("/api/hedging/bands/{underlying_id}", response_model=HedgeBandsOut)
    def hedging_put_bands(underlying_id: int, payload: HedgeBandsIn,
                          session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        hs.set_bands(session, underlying_id=underlying_id,
                     bands=payload.model_dump(), actor="desk_user")
        session.commit()
        return hs.resolve_bands(session, underlying_id=underlying_id)

    @app.post("/api/hedging/solve")
    def hedging_solve(payload: HedgeSolveRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        return hs.solve_hedge(session, portfolio_id=payload.portfolio_id,
                              underlying=payload.underlying, strategy=payload.strategy,
                              legs=payload.legs, bands=payload.bands)

    @app.post("/api/hedging/book")
    def hedging_book(payload: HedgeBookRequest, session: Session = Depends(get_db)):
        from .services.domains import hedging_strategy as hs
        try:
            out = hs.book_hedge(session, portfolio_id=payload.portfolio_id,
                                underlying=payload.underlying, risk_run_id=payload.risk_run_id,
                                strategy=payload.strategy, legs=payload.legs, spot=payload.spot)
        except Exception:
            session.rollback()
            raise
        session.commit()
        return out
```
Add `HedgeBandsOut, HedgeBandsIn, HedgeSolveRequest, HedgeBookRequest` to the `from .schemas import (...)` block at the top of `main.py`.

- [ ] **Step 5: Run the API tests + full suite**

Run: `python -m pytest tests/test_hedging_strategy_api.py -v && python -m pytest -q`
Expected: PASS (known optional-dep failures aside).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_hedging_strategy_api.py
git commit -m "feat(hedging): /api/hedging solve|book|bands|hedgeable endpoints"
```

---

## Phase B — Agent (tools + workflow)

### Task B1: Hedging agent tools

**Files:**
- Create: `backend/app/tools/hedging.py`
- Modify: `backend/app/tools/__init__.py`
- Test: `tests/test_hedging_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hedging_tools.py
from datetime import date

from app.models import (HedgeInstrument, HedgeMapEntry, Portfolio, RiskRun, Underlying)
from app.tools.hedging import (get_hedgeable_underlyings_tool, propose_hedge_tool)


def _seed(session):
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC2406", instrument_type="future", multiplier=200.0,
        expiry=date(2026, 6, 21), last_price=5600.0, status="live"))
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active"))
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 0.0,
         "vega": 0.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.commit()
    return pf


def test_get_hedgeable_tool(session):
    pf = _seed(session)
    out = get_hedgeable_underlyings_tool.invoke({"portfolio_id": pf.id})
    assert out["status"] == "ok"


def test_propose_hedge_tool(session):
    pf = _seed(session)
    out = propose_hedge_tool.invoke(
        {"portfolio_id": pf.id, "underlying": "000905.SH", "strategy": "delta_neutral"})
    assert out["status"] == "feasible"
    assert out["legs"][0]["quantity"] == -1
```
(`tests/test_hedging_tools.py` must be added to `_GATE_TEST_FILES` in `tests/conftest.py` only if the tools should exercise the real capability gate; for these read-tool shape tests, the autouse bypass is fine — do not add it.)

- [ ] **Step 2: Run it to confirm it fails**

Run: `python -m pytest tests/test_hedging_tools.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement the tools**

```python
# backend/app/tools/hedging.py
"""@tool wrappers for the hedging strategy domain (thin adapters over
services/domains/hedging_strategy)."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import database
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services import hedging_greeks
from app.services.domains import hedging_strategy as hs


class HedgeableInput(BaseModel):
    portfolio_id: int


class ProposeHedgeInput(BaseModel):
    portfolio_id: int
    underlying: str
    strategy: str = Field(description="delta_neutral|delta_neutral_enhanced|delta_gamma_neutral|full_neutral")
    legs: list[dict[str, Any]] | None = None
    bands: dict[str, float] | None = None


class BookHedgeInput(BaseModel):
    portfolio_id: int
    underlying: str
    risk_run_id: int
    strategy: str
    spot: float
    legs: list[dict[str, Any]]


class BandsInput(BaseModel):
    underlying_id: int


class SetBandsInput(BaseModel):
    underlying_id: int
    bands: dict[str, float]


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_hedgeable_underlyings", args_schema=HedgeableInput)
def get_hedgeable_underlyings_tool(portfolio_id: int) -> dict[str, Any]:
    """Per-underlying greek exposure + staleness from the latest completed risk run."""
    with database.SessionLocal() as session:
        return hedging_greeks.aggregate_by_underlying(session, portfolio_id=portfolio_id)


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("propose_hedge", args_schema=ProposeHedgeInput)
def propose_hedge_tool(portfolio_id: int, underlying: str, strategy: str,
                       legs: list[dict[str, Any]] | None = None,
                       bands: dict[str, float] | None = None) -> dict[str, Any]:
    """Propose + size a hedge (staged MILP). No persistence; safe to call repeatedly."""
    with database.SessionLocal() as session:
        return hs.solve_hedge(session, portfolio_id=portfolio_id, underlying=underlying,
                              strategy=strategy, legs=legs, bands=bands)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_hedge", args_schema=BookHedgeInput)
def book_hedge_tool(portfolio_id: int, underlying: str, risk_run_id: int,
                    strategy: str, spot: float, legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Atomically book the sized hedge legs into the portfolio, tagged + linked to the run."""
    with database.SessionLocal() as session:
        out = hs.book_hedge(session, portfolio_id=portfolio_id, underlying=underlying,
                            risk_run_id=risk_run_id, strategy=strategy, legs=legs, spot=spot)
        session.commit()
        return out


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_hedge_bands", args_schema=BandsInput)
def get_hedge_bands_tool(underlying_id: int) -> dict[str, Any]:
    """Resolved hedge band widths (per-underlying override else defaults)."""
    with database.SessionLocal() as session:
        return hs.resolve_bands(session, underlying_id=underlying_id)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("set_hedge_bands", args_schema=SetBandsInput)
def set_hedge_bands_tool(underlying_id: int, bands: dict[str, float]) -> dict[str, Any]:
    """Persist a per-underlying band override."""
    with database.SessionLocal() as session:
        hs.set_bands(session, underlying_id=underlying_id, bands=bands, actor="desk_user")
        session.commit()
        return hs.resolve_bands(session, underlying_id=underlying_id)
```

- [ ] **Step 4: Register the tools**

In `backend/app/tools/__init__.py` add the import and extend `QUANT_AGENT_TOOLS`:
```python
from .hedging import (
    book_hedge_tool,
    get_hedge_bands_tool,
    get_hedgeable_underlyings_tool,
    propose_hedge_tool,
    set_hedge_bands_tool,
)
```
Add to the `QUANT_AGENT_TOOLS` list (near the read tools, with the writes among the persisted-action group):
```python
    get_hedgeable_underlyings_tool,
    propose_hedge_tool,
    get_hedge_bands_tool,
    # writes:
    book_hedge_tool,
    set_hedge_bands_tool,
```

- [ ] **Step 5: Run the tool tests + the tool catalog test**

Run: `python -m pytest tests/test_hedging_tools.py -v && python -m pytest tests/ -q -k "tools"`
Expected: PASS. If a test asserts the exact `QUANT_AGENT_TOOLS` length/membership, update it to include the 5 new tools.

- [ ] **Step 6: Commit**

```bash
git add backend/app/tools/hedging.py backend/app/tools/__init__.py tests/test_hedging_tools.py
git commit -m "feat(hedging): agent tools (hedgeable/propose/book/bands)"
```

### Task B2: Workflow SKILL.md + reference + persona + catalog tests

**Files:**
- Create: `backend/app/skills/workflows/hedging/hedge-portfolio/SKILL.md`
- Create: `backend/app/skills/references/hedging/strategy.md`
- Modify: `backend/app/services/deep_agent/personas.py`
- Modify: `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`, `tests/test_workflow_skills_phase3.py`

- [ ] **Step 1: Update the catalog tests first (red)**

In `tests/test_skills_catalog_v2.py`:
- add `"/skills/workflows/hedging/"` to the `risk_sources` expected list (after `"/skills/workflows/risk/"`).
- add to the `expected` dict in `test_all_workflow_domains_have_expected_skills`:
```python
        "/workflows/hedging/": {"hedge-portfolio"},
```
- bump the risk-persona total-catalog count assertion (find `test_risk_total_workflow_catalog` / the `len(catalog) == N` for risk) by the number of *new-to-risk* workflows (hedge-portfolio = +1).

Apply the analogous edits in `tests/test_skills_catalog.py` and `tests/test_workflow_skills_phase3.py` (search each for the risk persona source list and any total/count assertions).

- [ ] **Step 2: Run to confirm red**

Run: `python -m pytest tests/test_skills_catalog_v2.py tests/test_skills_catalog.py tests/test_workflow_skills_phase3.py -v`
Expected: FAIL — the `hedging` workflow doesn't exist yet and the persona doesn't list it.

- [ ] **Step 3: Wire the persona**

In `backend/app/services/deep_agent/personas.py`, find `risk_spec`'s `skills` list and add `"/skills/workflows/hedging/"` after `"/skills/workflows/risk/"`.

- [ ] **Step 4: Write the workflow SKILL.md**

```markdown
---
name: hedge-portfolio
description: Size and book a per-underlying greek hedge for a portfolio using the four hedging strategies (delta-neutral, delta-neutral-enhanced, delta-gamma-neutral, full-neutral). Use when a desk wants to neutralize delta/gamma/vega within bands against the latest risk run and book the hedge.
domain: hedging
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_async
required_context:
  - portfolio_id
optional_context:
  - underlying
  - strategy
  - legs
  - bands
write_actions: true
confirmation_required: true
success_criteria:
  - sized legs with residual greeks and feasibility are returned before booking
  - infeasible hard bands are reported with the binding greek, never booked silently
  - booked hedge legs are tagged with the source risk_run_id
---

## When to use

- A desk wants to reduce a portfolio's per-underlying greeks into bands and book the hedge.
- Follows a completed risk run (the source of the greek targets).

## Required inputs

`portfolio_id`; optionally `underlying`, `strategy`
(`delta_neutral|delta_neutral_enhanced|delta_gamma_neutral|full_neutral`),
explicit `legs`, and band `bands` overrides. Read
`/skills/references/hedging/strategy.md` for strategy + band semantics.

## Procedure

1. Call `get_hedgeable_underlyings(portfolio_id)`. If `status` is `no_risk_run`,
   tell the desk to run risk first (do not fabricate exposure). Surface the run's
   age; if stale, ask whether to re-run risk before hedging.
2. Pick the `underlying` and `strategy` (confirm with the desk if unspecified).
3. Call `propose_hedge(portfolio_id, underlying, strategy)`. Present the proposed
   legs (tagged by greek role), the resolved bands, the sized quantities, and the
   residual greeks. Offer to swap legs (re-call with explicit `legs`) or override
   `bands` (confirm defaults vs override).
4. If `status` is `infeasible`, report the binding greek(s) + shortfall and
   suggest adding an option leg (for gamma/vega) or widening the band. Do not book.
5. On a feasible proposal, compose a confirmation summary (portfolio, underlying,
   strategy, legs+quantities, residual greeks).
6. After confirmation, call `book_hedge(portfolio_id, underlying, risk_run_id,
   strategy, spot, legs)` using the `risk_run_id` and `spot` from the proposal.
7. Return booked position ids and the residual greeks achieved.

## Stop conditions

Never book an infeasible hard-band solution or guess greek targets without a
completed risk run. Ask for a risk run or a band/leg change instead.

## Output shape

Return feasible/infeasible first, then strategy, per-leg quantities, residual
greeks, binding greeks (if any), and booked position ids (after booking).

## References

- `/skills/references/hedging/strategy.md`

## Example

User: Delta-gamma hedge portfolio 6 on CSI 500.
Assistant: get_hedgeable_underlyings(6) → propose_hedge(6, "000905.SH",
"delta_gamma_neutral") → review legs/residuals → confirm → book_hedge(...).
```

- [ ] **Step 5: Write the reference doc**

```markdown
# Hedging strategies & bands

Per **underlying**, size integer lots of hedge instruments to push greeks into
bands, lexicographically (hard tier first, then soft tier, then smallest total lots).

| Strategy | Hard (must hold) | Soft (minimize violation) |
|---|---|---|
| delta_neutral | delta_cash | — |
| delta_neutral_enhanced | delta_cash | gamma_cash, vega |
| delta_gamma_neutral | delta_cash, gamma_cash | vega |
| full_neutral | delta_cash, gamma_cash, vega | — |

- delta_cash = δ·S, gamma_cash = γ·S²/100, vega is raw model units.
- Bands are per-underlying absolute cash widths (one width per greek; a greek is
  hard or soft depending on the strategy). Defaults apply unless overridden.
- Futures/spot move delta only; options are required to move gamma/vega. A
  gamma/vega-hard strategy with no option leg is infeasible.
- Greek targets come from the latest completed risk run; if none exists, run risk
  first. Hedge legs book into the same portfolio, tagged with the risk_run_id.
```

- [ ] **Step 6: Run the catalog tests + skill lint**

Run: `python -m pytest tests/test_skills_catalog_v2.py tests/test_skills_catalog.py tests/test_workflow_skills_phase3.py -v`
Expected: PASS. If a skill-lint test validates frontmatter keys, ensure `hedge-portfolio` matches the schema used by `book-position` (it does).

- [ ] **Step 7: Commit**

```bash
git add backend/app/skills/workflows/hedging backend/app/skills/references/hedging \
        backend/app/services/deep_agent/personas.py \
        tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_workflow_skills_phase3.py
git commit -m "feat(hedging): hedge-portfolio workflow + reference + risk persona wiring"
```

---

## Phase C — UI (Hedge-Strategy tab on the Hedging page)

> Frontend tests: `cd frontend && npm test -- --run`.

### Task C1: Types, API client, presentational panel

**Files:**
- Modify: `frontend/src/types.ts`
- Create: `frontend/src/routes/HedgeStrategy.tsx`, `frontend/src/routes/HedgeStrategy.css`
- Test: `frontend/src/routes/HedgeStrategy.test.tsx`

- [ ] **Step 1: Write the failing presentational test**

```tsx
// frontend/src/routes/HedgeStrategy.test.tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { describe, it, expect, vi } from 'vitest'
import { HedgeStrategy } from './HedgeStrategy'
import type { HedgeProposal } from '../types'

const proposal: HedgeProposal = {
  status: 'feasible', portfolio_id: 1, underlying: '000905.SH',
  strategy: 'delta_neutral', risk_run_id: 7, spot: 5600,
  targets: { delta: 1120000, gamma: 0, vega: 0 },
  bands: { delta: 500000, gamma: 50000, vega: 10000 },
  legs: [{ contract_code: 'IC2406', exchange: 'CFFEX', instrument_type: 'future',
           role: 'delta', multiplier: 200, quantity: -1, key: 'CFFEX:IC2406',
           delta: 1120000, gamma: 0, vega: 0, priced_ok: true, price_error: null,
           option_type: null, strike: null, family: 'index_future' }],
  residual: { delta: 0, gamma: 0, vega: 0 },
  in_band: { delta: true }, binding: [], warnings: [],
}

it('renders sized legs and a Book button when feasible', () => {
  render(<HedgeStrategy
    strategy="delta_neutral" onStrategyChange={() => {}}
    proposal={proposal} onSolve={() => {}} onBook={() => {}}
    loading={false} />)
  expect(screen.getByText('IC2406')).toBeInTheDocument()
  expect(screen.getByText('-1')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /book/i })).toBeEnabled()
})

it('disables Book and shows the binding greek when infeasible', () => {
  const infeasible = { ...proposal, status: 'infeasible' as const,
    in_band: { delta: true, gamma: false },
    binding: [{ greek: 'gamma', shortfall: 38000 }] }
  render(<HedgeStrategy strategy="delta_gamma_neutral" onStrategyChange={() => {}}
    proposal={infeasible} onSolve={() => {}} onBook={() => {}} loading={false} />)
  expect(screen.getByText(/gamma/i)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /book/i })).toBeDisabled()
})

it('calls onSolve when Solve is clicked', () => {
  const onSolve = vi.fn()
  render(<HedgeStrategy strategy="delta_neutral" onStrategyChange={() => {}}
    proposal={null} onSolve={onSolve} onBook={() => {}} loading={false} />)
  fireEvent.click(screen.getByRole('button', { name: /solve/i }))
  expect(onSolve).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd frontend && npm test -- --run HedgeStrategy`
Expected: FAIL — cannot find `./HedgeStrategy`.

- [ ] **Step 3: Add types**

In `frontend/src/types.ts`:
```ts
export type HedgeStrategyName =
  | 'delta_neutral' | 'delta_neutral_enhanced' | 'delta_gamma_neutral' | 'full_neutral'

export interface HedgeLeg {
  key: string
  contract_code: string
  exchange: string
  instrument_type: string
  option_type: string | null
  strike: number | null
  expiry?: string | null
  multiplier: number
  family: string
  role: string
  delta: number
  gamma: number
  vega: number
  quantity: number
  priced_ok: boolean
  price_error: string | null
}

export interface HedgeProposal {
  status: 'feasible' | 'infeasible' | 'no_risk_run' | 'no_exposure' | 'no_spot'
  portfolio_id: number
  underlying: string
  strategy: string
  risk_run_id?: number
  spot?: number
  targets?: { delta: number; gamma: number; vega: number }
  bands?: { delta: number; gamma: number; vega: number }
  legs?: HedgeLeg[]
  residual?: { delta: number; gamma: number; vega: number }
  in_band?: Record<string, boolean>
  binding?: { greek: string; shortfall: number }[]
  warnings?: { contract_code: string; error: string }[]
  message?: string
}
```

- [ ] **Step 4: Write the presentational component**

```tsx
// frontend/src/routes/HedgeStrategy.tsx
import './HedgeStrategy.css'
import type { HedgeProposal, HedgeStrategyName } from '../types'

const STRATEGIES: HedgeStrategyName[] = [
  'delta_neutral', 'delta_neutral_enhanced', 'delta_gamma_neutral', 'full_neutral',
]

export function HedgeStrategy(props: {
  strategy: HedgeStrategyName
  onStrategyChange: (s: HedgeStrategyName) => void
  proposal: HedgeProposal | null
  onSolve: () => void
  onBook: () => void
  loading: boolean
}) {
  const { strategy, onStrategyChange, proposal, onSolve, onBook, loading } = props
  const feasible = proposal?.status === 'feasible'
  return (
    <div className="hedge-strategy">
      <div className="hedge-strategy__bar">
        <select value={strategy} aria-label="strategy"
          onChange={(e) => onStrategyChange(e.target.value as HedgeStrategyName)}>
          {STRATEGIES.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <button onClick={onSolve} disabled={loading}>Solve</button>
      </div>

      {proposal?.status === 'no_risk_run' && (
        <p className="hedge-strategy__warn">No completed risk run — run risk first.</p>
      )}

      {proposal?.legs && (
        <table className="hedge-strategy__legs">
          <thead><tr><th>Contract</th><th>Role</th><th>Qty</th></tr></thead>
          <tbody>
            {proposal.legs.map((leg) => (
              <tr key={leg.key}>
                <td>{leg.contract_code}</td><td>{leg.role}</td><td>{leg.quantity}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {proposal?.residual && (
        <div className="hedge-strategy__residual">
          {(['delta', 'gamma', 'vega'] as const).map((g) => (
            <span key={g} className={proposal.in_band?.[g] === false ? 'out' : 'in'}>
              {g}: {Math.round(proposal.residual![g])}
            </span>
          ))}
        </div>
      )}

      {proposal?.status === 'infeasible' && (
        <p className="hedge-strategy__infeasible">
          Infeasible — binding: {proposal.binding?.map((b) => b.greek).join(', ')}
        </p>
      )}

      <button onClick={onBook} disabled={!feasible || loading}>Book hedge</button>
    </div>
  )
}
```

- [ ] **Step 5: Add minimal CSS**

```css
/* frontend/src/routes/HedgeStrategy.css */
.hedge-strategy__bar { display: flex; gap: 8px; align-items: center; }
.hedge-strategy__residual span.out { color: #c0392b; font-weight: 600; }
.hedge-strategy__residual span.in { color: #27ae60; }
.hedge-strategy__infeasible { color: #c0392b; }
.hedge-strategy__warn { color: #b9770e; }
.hedge-strategy__legs { width: 100%; border-collapse: collapse; }
```

- [ ] **Step 6: Run the test**

Run: `cd frontend && npm test -- --run HedgeStrategy`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/HedgeStrategy.tsx frontend/src/routes/HedgeStrategy.css frontend/src/routes/HedgeStrategy.test.tsx
git commit -m "feat(ui): hedge-strategy presentational panel"
```

### Task C2: Live container + tab wiring

**Files:**
- Create: `frontend/src/routes/HedgeStrategy.live.tsx`
- Modify: `frontend/src/routes/Hedging.tsx` (or `main.tsx`) to add the tab toggle
- Test: `frontend/src/routes/HedgeStrategy.live.test.tsx`

- [ ] **Step 1: Write the failing live test**

```tsx
// frontend/src/routes/HedgeStrategy.live.test.tsx
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { HedgeStrategyLive } from './HedgeStrategy.live'

beforeEach(() => {
  vi.restoreAllMocks()
})

it('solves and renders quantities from the API', async () => {
  const proposal = {
    status: 'feasible', portfolio_id: 1, underlying: '000905.SH',
    strategy: 'delta_neutral', risk_run_id: 7, spot: 5600,
    targets: { delta: 1120000, gamma: 0, vega: 0 },
    bands: { delta: 500000, gamma: 50000, vega: 10000 },
    legs: [{ contract_code: 'IC2406', exchange: 'CFFEX', instrument_type: 'future',
             role: 'delta', multiplier: 200, quantity: -1, key: 'CFFEX:IC2406',
             delta: 1120000, gamma: 0, vega: 0, priced_ok: true, price_error: null,
             option_type: null, strike: null, family: 'index_future' }],
    residual: { delta: 0, gamma: 0, vega: 0 }, in_band: { delta: true },
    binding: [], warnings: [],
  }
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true, json: async () => proposal,
  } as Response)

  render(<HedgeStrategyLive portfolioId={1} underlying="000905.SH" />)
  fireEvent.click(screen.getByRole('button', { name: /solve/i }))
  await waitFor(() => expect(screen.getByText('IC2406')).toBeInTheDocument())
  expect(screen.getByText('-1')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd frontend && npm test -- --run HedgeStrategy.live`
Expected: FAIL — cannot find the live module.

- [ ] **Step 3: Implement the container**

```tsx
// frontend/src/routes/HedgeStrategy.live.tsx
import { useState } from 'react'
import { api } from '../api/client'
import { HedgeStrategy } from './HedgeStrategy'
import type { HedgeProposal, HedgeStrategyName } from '../types'

export function HedgeStrategyLive(props: { portfolioId: number; underlying: string }) {
  const { portfolioId, underlying } = props
  const [strategy, setStrategy] = useState<HedgeStrategyName>('delta_neutral')
  const [proposal, setProposal] = useState<HedgeProposal | null>(null)
  const [loading, setLoading] = useState(false)

  async function solve() {
    setLoading(true)
    try {
      const res = await api<HedgeProposal>('/api/hedging/solve', {
        method: 'POST',
        body: JSON.stringify({ portfolio_id: portfolioId, underlying, strategy }),
      })
      setProposal(res)
    } finally {
      setLoading(false)
    }
  }

  async function book() {
    if (!proposal || proposal.status !== 'feasible' || !proposal.legs) return
    setLoading(true)
    try {
      await api('/api/hedging/book', {
        method: 'POST',
        body: JSON.stringify({
          portfolio_id: portfolioId, underlying, risk_run_id: proposal.risk_run_id,
          strategy, spot: proposal.spot, legs: proposal.legs,
        }),
      })
    } finally {
      setLoading(false)
    }
  }

  return (
    <HedgeStrategy strategy={strategy} onStrategyChange={setStrategy}
      proposal={proposal} onSolve={solve} onBook={book} loading={loading} />
  )
}
```
(Confirm the `api()` helper's signature/return matches `Hedging.live.tsx` from Part 1; mirror it exactly. If `api()` returns parsed JSON, the above is correct; if it returns a `Response`, add `.json()`.)

- [ ] **Step 4: Add the tab toggle**

After Phase 0, Part 1's `Hedging.tsx`/`Hedging.live.tsx` exist in this worktree. Read the live container to find its current portfolio + selected-underlying state, then add a tab switch around the existing instruments view. Adapt this pattern (names will differ — wire to the real state):

```tsx
// near the top of the Hedging live container's component body
const [tab, setTab] = useState<'instruments' | 'strategy'>('instruments')

// in the returned JSX, above the existing instruments view:
<div className="hedging-tabs" role="tablist">
  <button role="tab" aria-selected={tab === 'instruments'}
    onClick={() => setTab('instruments')}>Allowed Instruments</button>
  <button role="tab" aria-selected={tab === 'strategy'}
    onClick={() => setTab('strategy')}>Hedge Strategy</button>
</div>

{tab === 'instruments' ? (
  /* ...existing Part 1 instruments view... */
) : (
  selectedUnderlyingId != null && selectedPortfolioId != null
    ? <HedgeStrategyLive portfolioId={selectedPortfolioId} underlying={selectedSymbol} />
    : <p>Select a portfolio and underlying to hedge.</p>
)}
```
`selectedPortfolioId`/`selectedSymbol` come from the page's existing portfolio selection and the selected rail row. Import `HedgeStrategyLive` from `./HedgeStrategy.live` and add a `.hedging-tabs` rule to `Hedging.css`.

- [ ] **Step 5: Run the live test + the full frontend suite + build**

Run: `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build`
Expected: tests PASS, `tsc` exit 0, build succeeds.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/HedgeStrategy.live.tsx frontend/src/routes/HedgeStrategy.live.test.tsx frontend/src/routes/Hedging.tsx
git commit -m "feat(ui): hedge-strategy live container + Hedging-page tab"
```

---

## Final verification (after all phases)

- [ ] `python -m pytest -q` — green except the 6 known optional-langchain-dep failures.
- [ ] `cd frontend && npm test -- --run && npx tsc --noEmit && npm run build` — green.
- [ ] `cd backend && alembic upgrade head` on a scratch DB — single head, no error.
- [ ] Dispatch a final code-review over the whole branch (subagent-driven-development's final reviewer), then `superpowers:finishing-a-development-branch`.

## Notes for the implementer

- **Run backend tests with `python -m pytest`** from the worktree root (migration tests need the repo root on `sys.path`).
- **`PYTHONPATH` gotcha:** `python -c` may import the app from the *main* checkout via the venv `.pth`; always run via `pytest` or set `PYTHONPATH=<worktree>/backend`.
- The solver is the load-bearing piece — keep it pure and DB-free; never add a DB import to `hedging_solver.py`.
- Characterization tests must use **non-default input values** (the vacuous-test trap: a test passes because the asserted value equals a fallback).
- Booking atomicity is owned by the session unit-of-work: the endpoint/tool commits once after `book_hedge` returns; a raised exception rolls back all legs.
