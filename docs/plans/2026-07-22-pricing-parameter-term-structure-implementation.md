# Term-Structure Curves for Pricing Parameters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the desk author per-underlying term-structure curves for `r`/`q`/`vol` on the `Instrument` baseline, then materialize them — by linear interpolation at each open trade's time-to-maturity — into a normal flat `PricingParameterProfile` that the existing pricing path consumes unchanged.

**Architecture:** *Materialize-then-price.* Curves live as three nullable JSON columns on `instruments`. A new generate step walks open OTC positions, interpolates each curve at that trade's ACT/365 tenor (flat-scalar fallback when a param has no curve), and writes flat `r/q/vol` rows into a `PricingParameterProfile(source_type="curve")`. QuantArk still receives scalars — **zero pricing-code changes.** Interpolation is a pure, DB-free module; the DB read+write lives in the existing domain facade; REST and agent tools are thin wrappers; the frontend adds a curve editor + charts to the Instruments → Assumptions tab.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (Python, `.venv/bin/python`), React 19 / Vite / TypeScript / recharts 3.8.1 (frontend), pytest + vitest.

## Global Constraints

- **Backend tests:** `.venv/bin/python -m pytest` from repo root. **Frontend tests:** `cd frontend && npm test` (vitest); type-check `cd frontend && npx tsc --noEmit`.
- **Next migration is `0050`**, `down_revision = "0049_hedge_booking_claim"` (verified head). Migrations use **migration-local Core SQL / `sa.Table` on fresh `MetaData`** — never ORM models/services (house rule).
- **Pricing path is untouched.** No edits to `risk_engine.py`, `quantark.py`, or `build_assumptions_set`. QuantArk stays scalar-in.
- **Curve JSON shape everywhere:** `list[{"tenor": <label:str>, "value": <float>}] | None`. `None`/`[]` ⇒ "no curve → use flat scalar."
- **Tenor labels** are the fixed set in `TENOR_YEARS` (`1W,2W,1M,2M,3M,6M,9M,1Y,18M,2Y,3Y,5Y`). Unknown label ⇒ validation error (422/400). Volatility curve values must be `> 0`.
- **Trade tenor = ACT/365:** `(maturity_date − valuation_date).days / 365`, clamped to `≥ 0`.
- **A new agent tool MUST be added to `DEEP_AGENT_TOOL_NAMES`** (`services/agents.py`) AND registered in `QUANT_AGENT_TOOLS` (`tools/__init__.py`) — registered-but-not-allowlisted tools are silently dropped from every persona. A **HITL** write tool MUST also be added to all three structures in `services/deep_agent/hitl.py` (`INTERRUPT_TOOL_NAMES`, `_RISK_LEVEL_BY_TOOL`, `_LABEL_BY_TOOL`).
- **Frontend styling is token-only** (`var(--token)` from `src/tokens/`). No hardcoded hex, no `var(--x, #hex)` fallbacks. Read `frontend/CLAUDE.md` before UI work. Chart series colors: `--info`/`--pos`/`--warn`/`--neg`; grid/axis lines `--hairline`; axis/legend text `--ink-2`; tooltip `background: var(--paper)`, `border: 1px solid var(--hairline-2)`. **Do NOT copy `GreeksLandscape.tsx`'s hardcoded hex `COLORS` array** — use `Backtest.tsx` as the model.
- **Before the PR:** update `CHANGELOG.md` (`[Unreleased]`), add a `CLAUDE.md` subsystem section, update `README.md` if user-facing (Task 10).

---

## File Structure

**Create**
- `backend/alembic/versions/0050_instrument_term_structure_curves.py` — adds three nullable JSON columns to `instruments`.
- `backend/app/services/term_structure.py` — pure module: `TENOR_YEARS`, `tenor_to_years`, `validate_curve`, `interpolate_curve`. No DB, no imports from `app.services.*`.
- `tests/test_term_structure.py` — unit tests for the pure module.
- `tests/test_pricing_curve_generation.py` — tests for the compute + write facade.
- `tests/test_curve_api.py` — TestClient tests for the PUT-curves round-trip + the `from-curves` generate route.

**Modify**
- `backend/app/models.py` — add `rate_curve` / `dividend_yield_curve` / `volatility_curve` JSON columns to `Instrument` (after line 673).
- `backend/app/services/underlyings.py` — add `open_otc_positions(session) -> list[Position]`.
- `backend/app/services/domains/pricing_profiles.py` — add `GeneratedParamRow`, `generate_curve_param_rows`, `generate_profile_from_curves`.
- `backend/app/services/underlying_defaults.py` — extend `upsert_underlying_default` with the three curve params + validation.
- `backend/app/services/domains/assumptions.py` — extend `set_instrument_defaults` with the three curve params + validation.
- `backend/app/schemas.py` — add `CurvePoint`; extend `UnderlyingPricingDefaultOut` / `UnderlyingPricingDefaultUpdate`; add `GenerateFromCurvesRequest`.
- `backend/app/main.py` — extend `_serialize_default`; add `POST /api/pricing-parameter-profiles/from-curves`.
- `backend/app/tools/_shaping.py` — `shape_instrument_defaults` returns the three curves.
- `backend/app/tools/assumptions.py` — `SetInstrumentPricingDefaultsInput` + tool accept curves.
- `backend/app/tools/pricing_profiles.py` — add `generate_pricing_parameters_from_curves` tool.
- `backend/app/tools/__init__.py` — import + register the new tool in `QUANT_AGENT_TOOLS`.
- `backend/app/services/agents.py` — add the tool to `DEEP_AGENT_TOOL_NAMES`.
- `backend/app/services/deep_agent/hitl.py` — add the tool to the three HITL structures.
- `frontend/src/api/types.ts` (or wherever `UnderlyingPricingDefault` is typed) — add `CurvePoint` + curve fields.
- `frontend/src/routes/Instruments.live.tsx` — curve PUT + generate POST handlers, thread new props.
- `frontend/src/routes/Instruments.tsx` — pass the new props through.
- `frontend/src/routes/InstrumentsAssumptions.tsx` + `InstrumentsAssumptions.css` — curve editor, charts, Generate button.
- `frontend/src/routes/InstrumentsAssumptions.test.tsx` + `Instruments.live.test.tsx` — new vitest cases.
- `CHANGELOG.md`, `CLAUDE.md`, `README.md` — docs.

---

## Task 1: Migration `0050` + `Instrument` curve columns

**Files:**
- Modify: `backend/app/models.py:673` (add columns after `volatility`)
- Create: `backend/alembic/versions/0050_instrument_term_structure_curves.py`
- Test: `tests/test_pricing_curve_generation.py`

**Interfaces:**
- Produces: `Instrument.rate_curve`, `Instrument.dividend_yield_curve`, `Instrument.volatility_curve` — each `Mapped[list[dict] | None]` JSON column, nullable, default `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_pricing_curve_generation.py`:

```python
from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.services.underlyings import ensure_underlying


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_instrument_curve_columns_roundtrip(session: Session) -> None:
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.03}]
    inst.dividend_yield_curve = None
    inst.volatility_curve = [{"tenor": "6M", "value": 0.2}]
    session.flush()
    session.expire(inst)
    reloaded = ensure_underlying(session, "000300.SH")
    assert reloaded.rate_curve == [
        {"tenor": "3M", "value": 0.02},
        {"tenor": "1Y", "value": 0.03},
    ]
    assert reloaded.dividend_yield_curve is None
    assert reloaded.volatility_curve == [{"tenor": "6M", "value": 0.2}]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_pricing_curve_generation.py::test_instrument_curve_columns_roundtrip -v`
Expected: FAIL — `AttributeError: 'Instrument' object has no attribute 'rate_curve'` (or a mapper error).

- [ ] **Step 3: Add the model columns**

In `backend/app/models.py`, immediately after line 673 (`volatility: Mapped[float | None] = ...`), add:

```python
    # Term-structure curves (per underlying): list[{"tenor": <label>, "value":
    # <float>}] | None. None/[] means "no curve — use the flat scalar above".
    # Fed ONLY by generate_pricing_parameters_from_curves; pricing stays flat.
    rate_curve: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    dividend_yield_curve: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    volatility_curve: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
```

(`JSON` and `mapped_column` are already imported in `models.py`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_pricing_curve_generation.py::test_instrument_curve_columns_roundtrip -v`
Expected: PASS (in-memory test DBs are built with `Base.metadata.create_all`, so the model columns suffice for tests).

- [ ] **Step 5: Write the migration**

Create `backend/alembic/versions/0050_instrument_term_structure_curves.py` (mirrors the `sa.JSON()` add-column pattern of `0042_instrument_tags.py`, nullable, no backfill):

```python
"""Add term-structure curve columns to instruments.

Adds Instrument.rate_curve / dividend_yield_curve / volatility_curve — each a
nullable JSON list[{"tenor": <label>, "value": <float>}]. None means "no curve;
use the flat scalar". No backfill.

HOUSE RULE: migration-local Core SQL only — no ORM models/services.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0050_instrument_term_structure_curves"
down_revision = "0049_hedge_booking_claim"
branch_labels = None
depends_on = None

_COLUMNS = ("rate_curve", "dividend_yield_curve", "volatility_curve")


def upgrade() -> None:
    for column in _COLUMNS:
        op.add_column("instruments", sa.Column(column, sa.JSON(), nullable=True))


def downgrade() -> None:
    for column in reversed(_COLUMNS):
        op.drop_column("instruments", column)
```

- [ ] **Step 6: Verify the migration applies to a throwaway DB**

Run: `OPEN_OTC_DATABASE_URL="sqlite+pysqlite:///$PWD/scratch_mig.db" .venv/bin/python -m alembic -c backend/alembic.ini upgrade head && rm -f scratch_mig.db`
Expected: exits 0 (upgrades a fresh DB clean through `0050`). If `alembic.ini` lives elsewhere or reads a fixed URL, instead run the repo's standard `.venv/bin/python -m alembic upgrade head` against the live DB (`data/open_otc.sqlite3`) — the columns are additive + nullable, so this is a safe forward-only step.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models.py backend/alembic/versions/0050_instrument_term_structure_curves.py tests/test_pricing_curve_generation.py
git commit -m "feat(pricing): add term-structure curve columns to instruments (migration 0050)"
```

---

## Task 2: Pure interpolation module `term_structure.py`

**Files:**
- Create: `backend/app/services/term_structure.py`
- Test: `tests/test_term_structure.py`

**Interfaces:**
- Produces:
  - `TENOR_YEARS: dict[str, float]`
  - `tenor_to_years(label: str) -> float` — raises `ValueError` on unknown label.
  - `validate_curve(points, *, require_positive: bool = False) -> list[dict] | None` — returns a cleaned `[{"tenor","value"}]` (known labels, deduped, finite floats; `>0` when `require_positive`) or `None` for `None`; raises `ValueError` on any violation.
  - `interpolate_curve(points: list[dict] | None, target_years: float) -> float | None` — `None`/`[]` → `None`; single point → its value; flat-extrapolate the ends; linear between bracketing points. Assumes labels are valid (write path validates).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_term_structure.py`:

```python
from __future__ import annotations

import pytest

from app.services.term_structure import (
    TENOR_YEARS,
    interpolate_curve,
    tenor_to_years,
    validate_curve,
)


def test_tenor_years_map_has_expected_labels() -> None:
    assert set(TENOR_YEARS) == {
        "1W", "2W", "1M", "2M", "3M", "6M", "9M", "1Y", "18M", "2Y", "3Y", "5Y",
    }
    assert TENOR_YEARS["1Y"] == pytest.approx(1.0)
    assert TENOR_YEARS["6M"] == pytest.approx(0.5)
    # Strictly increasing in label order used by the UI axis.
    years = [TENOR_YEARS[k] for k in
             ("1W", "1M", "3M", "6M", "1Y", "18M", "2Y", "5Y")]
    assert years == sorted(years)


def test_tenor_to_years_rejects_unknown() -> None:
    assert tenor_to_years("3M") == pytest.approx(0.25)
    with pytest.raises(ValueError):
        tenor_to_years("4M")


def test_interpolate_none_and_empty_return_none() -> None:
    assert interpolate_curve(None, 0.5) is None
    assert interpolate_curve([], 0.5) is None


def test_interpolate_single_point_is_constant() -> None:
    curve = [{"tenor": "6M", "value": 0.2}]
    assert interpolate_curve(curve, 0.1) == pytest.approx(0.2)
    assert interpolate_curve(curve, 5.0) == pytest.approx(0.2)


def test_interpolate_flat_extrapolation_both_ends() -> None:
    curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    assert interpolate_curve(curve, 0.0) == pytest.approx(0.02)   # below first
    assert interpolate_curve(curve, 3.0) == pytest.approx(0.05)   # above last


def test_interpolate_linear_between() -> None:
    # 3M=0.25y -> 0.02, 1Y=1.0y -> 0.05. Midpoint at 0.625y -> 0.035.
    curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    assert interpolate_curve(curve, 0.625) == pytest.approx(0.035)


def test_interpolate_is_order_independent() -> None:
    curve = [{"tenor": "1Y", "value": 0.05}, {"tenor": "3M", "value": 0.02}]
    assert interpolate_curve(curve, 0.625) == pytest.approx(0.035)


def test_validate_curve_dedup_and_labels() -> None:
    cleaned = validate_curve([{"tenor": "3M", "value": 0.02}])
    assert cleaned == [{"tenor": "3M", "value": 0.02}]
    assert validate_curve(None) is None
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "4M", "value": 0.02}])         # unknown label
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "3M", "value": 0.02},
                        {"tenor": "3M", "value": 0.03}])          # duplicate
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "3M", "value": "x"}])          # non-numeric
    with pytest.raises(ValueError):
        validate_curve([{"tenor": "3M", "value": 0.0}], require_positive=True)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_term_structure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.term_structure'`.

- [ ] **Step 3: Write the module**

Create `backend/app/services/term_structure.py`:

```python
"""Pure term-structure tenor labels + linear interpolation.

Dependency-free by design: no DB, no imports from app.services.*. Labels are
entry/display only — every calculation runs on the year-fraction axis.
"""
from __future__ import annotations

import math
from typing import Any

# Standard tenor labels -> year-fractions. Extend here (single source) if the
# desk needs 1D / 4M / 7Y / 10Y; keep values strictly increasing per label
# ordering used by the UI axis.
TENOR_YEARS: dict[str, float] = {
    "1W": 7 / 365,
    "2W": 14 / 365,
    "1M": 1 / 12,
    "2M": 2 / 12,
    "3M": 3 / 12,
    "6M": 6 / 12,
    "9M": 9 / 12,
    "1Y": 1.0,
    "18M": 18 / 12,
    "2Y": 2.0,
    "3Y": 3.0,
    "5Y": 5.0,
}


def tenor_to_years(label: str) -> float:
    """Year-fraction for a tenor label; ValueError on unknown label."""
    key = str(label).strip()
    if key not in TENOR_YEARS:
        raise ValueError(f"unknown tenor label: {label!r}")
    return TENOR_YEARS[key]


def validate_curve(
    points: Any, *, require_positive: bool = False
) -> list[dict] | None:
    """Return a cleaned [{"tenor","value"}] list, or None for None.

    Enforces: known labels, no duplicate labels, finite float values, and
    (when require_positive) value > 0. Raises ValueError on any violation.
    """
    if points is None:
        return None
    if not isinstance(points, list):
        raise ValueError("curve must be a list of {tenor, value} points")
    seen: set[str] = set()
    cleaned: list[dict] = []
    for point in points:
        if not isinstance(point, dict):
            raise ValueError("curve point must be an object with tenor and value")
        label = str(point.get("tenor", "")).strip()
        if label not in TENOR_YEARS:
            raise ValueError(f"unknown tenor label: {label!r}")
        if label in seen:
            raise ValueError(f"duplicate tenor label: {label!r}")
        seen.add(label)
        try:
            value = float(point.get("value"))
        except (TypeError, ValueError):
            raise ValueError(f"non-numeric value for tenor {label!r}") from None
        if not math.isfinite(value):
            raise ValueError(f"non-finite value for tenor {label!r}")
        if require_positive and value <= 0:
            raise ValueError(f"value for tenor {label!r} must be > 0")
        cleaned.append({"tenor": label, "value": value})
    return cleaned


def interpolate_curve(points: list[dict] | None, target_years: float) -> float | None:
    """Linear interpolation on the year axis with flat extrapolation.

    None / [] -> None (no curve). Single point -> constant. Below the first
    knot -> first value; above the last -> last value; else linear between the
    bracketing knots. Points are assumed to carry valid labels (write path
    validates via validate_curve).
    """
    if not points:
        return None
    knots = sorted(
        (TENOR_YEARS[str(p["tenor"]).strip()], float(p["value"])) for p in points
    )
    if len(knots) == 1:
        return knots[0][1]
    if target_years <= knots[0][0]:
        return knots[0][1]
    if target_years >= knots[-1][0]:
        return knots[-1][1]
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if x0 <= target_years <= x1:
            if x1 == x0:
                return y0
            weight = (target_years - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return knots[-1][1]  # unreachable; defensive
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_term_structure.py -v`
Expected: PASS (all cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/term_structure.py tests/test_term_structure.py
git commit -m "feat(pricing): pure term-structure tenor map + linear interpolation"
```

---

## Task 3: Open-position scope + curve→flat compute

**Files:**
- Modify: `backend/app/services/underlyings.py:269` (add `open_otc_positions` after `open_position_underlying_symbols`)
- Modify: `backend/app/services/domains/pricing_profiles.py` (add `GeneratedParamRow`, `generate_curve_param_rows`)
- Test: `tests/test_pricing_curve_generation.py`

**Interfaces:**
- Consumes: `interpolate_curve` (Task 2); `Instrument.*_curve` columns (Task 1); `position_requires_pricing_params` (`services/pricing_profiles.py:327`); `compatibility_terms_for_position` (`services/domains/products.py:327`) → `["product_kwargs"]["maturity_date"]`.
- Produces:
  - `open_otc_positions(session) -> list[Position]` — open, `position_kind=="otc"`, not knocked-out, `product` eager-loaded.
  - `GeneratedParamRow` dataclass: `symbol: str, source_trade_id: str, instrument_id: int, rate: float, dividend_yield: float, volatility: float, source_payload: dict`.
  - `generate_curve_param_rows(session, *, valuation_date: datetime | None = None) -> list[GeneratedParamRow]` — raises `ValueError("no open positions in scope")` when nothing scopes/produces a row; raises `ValueError({"unfilled_trades": [...]})` when any scoped trade can't be fully resolved.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pricing_curve_generation.py`:

```python
from datetime import datetime

from app.models import Portfolio, Position
from app.services.domains.pricing_profiles import generate_curve_param_rows


def _seed_open_option(
    session: Session,
    *,
    underlying: str,
    maturity: str,
    source_trade_id: str = "TRD-1",
) -> Position:
    # Product-less on purpose: with product_id=None, open_otc_positions loads
    # no product, so compatibility_terms_for_position takes its fallback branch
    # and reads maturity_date straight off position.product_kwargs — independent
    # of the Product model's normalized-column schema. (Real booked positions
    # carry a product; that branch reads the product's terms, the proven
    # termsheet seam.)
    portfolio = session.query(Portfolio).first() or Portfolio(name="Test")
    if portfolio.id is None:
        session.add(portfolio)
        session.flush()
    pos = Position(
        portfolio_id=portfolio.id,
        product_id=None,
        underlying=underlying,
        product_type="VanillaCall",
        product_kwargs={"underlying": underlying, "maturity_date": maturity},
        quantity=1.0,
        source_trade_id=source_trade_id,
        status="open",
        position_kind="otc",
        engine_name="quantark.vanilla",
        engine_kwargs={},
        source_payload={},
        mapping_status="supported",
    )
    session.add(pos)
    session.flush()
    return pos


def test_generate_curve_rows_interpolates_at_trade_tenor(session: Session) -> None:
    # valuation 2026-01-01, maturity 2026-07-02 -> ~0.5y ACT/365.
    _seed_open_option(session, underlying="000300.SH", maturity="2026-07-02")
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    inst.dividend_yield = 0.01           # flat-scalar fallback (no curve)
    inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
    session.flush()

    rows = generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
    assert len(rows) == 1
    row = rows[0]
    assert row.symbol == "000300.SH"
    assert row.source_trade_id == "TRD-1"
    # 182 days / 365 = 0.4986y; between 3M(0.25) and 1Y(1.0):
    #   0.02 + (0.4986-0.25)/(0.75) * 0.03 = 0.02995...
    assert row.rate == pytest.approx(0.02995, abs=1e-4)
    assert row.dividend_yield == pytest.approx(0.01)       # flat scalar
    assert row.volatility == pytest.approx(0.22)           # single-point curve
    assert row.source_payload["interp"]["rate"]["source"] == "curve"
    assert row.source_payload["interp"]["dividend_yield"]["source"] == "flat_scalar"
    assert row.source_payload["tenor_years"] == pytest.approx(0.4986, abs=1e-3)


def test_generate_curve_rows_skips_delta_one(session: Session) -> None:
    pos = _seed_open_option(session, underlying="IF2609.CFE", maturity="2026-09-01")
    pos.engine_name = "DeltaOneEngine"
    inst = ensure_underlying(session, "IF2609.CFE", source="manual", status="active")
    inst.rate = 0.02
    inst.dividend_yield = 0.0
    inst.volatility = 0.2
    session.flush()
    with pytest.raises(ValueError, match="no open positions in scope"):
        generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))


def test_generate_curve_rows_unfilled_when_no_curve_no_scalar(session: Session) -> None:
    _seed_open_option(session, underlying="000905.SH", maturity="2026-07-02",
                      source_trade_id="U-1")
    ensure_underlying(session, "000905.SH", source="manual", status="active")  # all None
    session.flush()
    with pytest.raises(ValueError) as exc_info:
        generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
    payload = exc_info.value.args[0]
    assert isinstance(payload, dict)
    assert payload["unfilled_trades"][0]["source_trade_id"] == "U-1"
    assert set(payload["unfilled_trades"][0]["missing_params"]) == {
        "rate", "dividend_yield", "volatility"
    }


def test_generate_curve_rows_no_open_positions_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="no open positions in scope"):
        generate_curve_param_rows(session, valuation_date=datetime(2026, 1, 1))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pricing_curve_generation.py -k generate_curve_rows -v`
Expected: FAIL — `ImportError: cannot import name 'generate_curve_param_rows'`.

- [ ] **Step 3: Add `open_otc_positions`**

In `backend/app/services/underlyings.py`, immediately after `open_position_underlying_symbols` (ends line 269), add (`is_knocked_out` and `Position` are already imported in this module; add `selectinload`):

```python
def open_otc_positions(session: Session) -> list[Position]:
    """Open OTC positions in the same scope as open_position_underlying_symbols,
    but returning the Position rows (product eager-loaded for term extraction)."""
    from sqlalchemy.orm import selectinload

    rows = (
        session.query(Position)
        .options(selectinload(Position.product))
        .filter(Position.underlying.isnot(None))
        .filter(Position.status == "open")
        .filter(Position.position_kind == "otc")
        .all()
    )
    live: list[Position] = []
    for pos in rows:
        payload = pos.source_payload if isinstance(pos.source_payload, dict) else {}
        state = payload.get("trade_state")
        if is_knocked_out(state or ""):
            continue
        live.append(pos)
    return live
```

- [ ] **Step 4: Add the compute to the domain facade**

In `backend/app/services/domains/pricing_profiles.py`, extend the imports near the top:

```python
from dataclasses import dataclass
from datetime import date, datetime
```

Add `Instrument` to the `from app.models import (...)` block, and add these imports after the existing `from app.services...` imports:

```python
from app.services.domains.products import compatibility_terms_for_position
from app.services.pricing_profiles import position_requires_pricing_params
from app.services.term_structure import interpolate_curve
from app.services.underlyings import ensure_underlying, open_otc_positions
```

Add the dataclass + compute function (place them after `PARAM_FIELDS = (...)`, before `create_profile`):

```python
_PARAM_CURVE_ATTR = {
    "rate": "rate_curve",
    "dividend_yield": "dividend_yield_curve",
    "volatility": "volatility_curve",
}


@dataclass(frozen=True)
class GeneratedParamRow:
    symbol: str
    source_trade_id: str
    instrument_id: int
    rate: float
    dividend_yield: float
    volatility: float
    source_payload: dict[str, Any]


def _coerce_maturity(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def generate_curve_param_rows(
    session: Session, *, valuation_date: datetime | None = None
) -> list[GeneratedParamRow]:
    """Interpolate each open OTC trade's r/q/vol from its underlying's curves.

    Per param: interpolate the curve at the trade's ACT/365 tenor; when there is
    no curve, fall back to the flat Instrument scalar. A trade with an
    unresolvable maturity, or any param with neither curve nor scalar, is
    collected as "unfilled". Nothing is silently dropped.

    Raises ValueError("no open positions in scope") when the scope is empty (or
    every position is delta-one), and ValueError({"unfilled_trades": [...]})
    when any scoped trade cannot be fully resolved.
    """
    effective = valuation_date or datetime.utcnow()
    val_date = effective.date()
    positions = [p for p in open_otc_positions(session)
                 if position_requires_pricing_params(p)]
    if not positions:
        raise ValueError("no open positions in scope")

    instruments: dict[str, Instrument] = {
        row.symbol: row for row in session.query(Instrument).all()
    }
    for pos in positions:
        if pos.underlying not in instruments:
            instruments[pos.underlying] = ensure_underlying(
                session, pos.underlying, source="pricing_profile", status="draft"
            )
    session.flush()

    rows: list[GeneratedParamRow] = []
    unfilled: list[dict] = []
    for pos in positions:
        instrument = instruments[pos.underlying]
        terms = compatibility_terms_for_position(pos)
        maturity = _coerce_maturity((terms.get("product_kwargs") or {}).get("maturity_date"))
        if maturity is None:
            unfilled.append({"source_trade_id": pos.source_trade_id,
                             "position_id": pos.id, "reason": "no_maturity_date"})
            continue
        tenor_years = max(0.0, (maturity - val_date).days / 365.0)

        values: dict[str, float] = {}
        interp: dict[str, dict] = {}
        missing: list[str] = []
        for param, curve_attr in _PARAM_CURVE_ATTR.items():
            curve = getattr(instrument, curve_attr, None)
            curve_value = interpolate_curve(curve, tenor_years)
            if curve_value is not None:
                values[param] = curve_value
                interp[param] = {"source": "curve", "value": curve_value, "curve": curve}
                continue
            flat = getattr(instrument, param, None)
            if flat is not None:
                values[param] = flat
                interp[param] = {"source": "flat_scalar", "value": flat}
            else:
                missing.append(param)
        if missing:
            unfilled.append({"source_trade_id": pos.source_trade_id,
                             "position_id": pos.id, "reason": "missing_params",
                             "missing_params": missing})
            continue

        rows.append(GeneratedParamRow(
            symbol=pos.underlying,
            source_trade_id=pos.source_trade_id or "",
            instrument_id=instrument.id,
            rate=values["rate"],
            dividend_yield=values["dividend_yield"],
            volatility=values["volatility"],
            source_payload={"generated_from": "instrument_curves",
                            "instrument_id": instrument.id,
                            "tenor_years": tenor_years, "interp": interp},
        ))

    if unfilled:
        raise ValueError({"unfilled_trades": unfilled})
    if not rows:
        raise ValueError("no open positions in scope")
    return rows
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pricing_curve_generation.py -k generate_curve_rows -v`
Expected: PASS (all four).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/underlyings.py backend/app/services/domains/pricing_profiles.py tests/test_pricing_curve_generation.py
git commit -m "feat(pricing): interpolate open-trade r/q/vol from instrument curves"
```

---

## Task 4: Write facade `generate_profile_from_curves`

**Files:**
- Modify: `backend/app/services/domains/pricing_profiles.py` (add `generate_profile_from_curves`; extend `__all__`)
- Test: `tests/test_pricing_curve_generation.py`

**Interfaces:**
- Consumes: `generate_curve_param_rows` (Task 3); `PricingParameterProfile` / `PricingParameterRow`; `record_audit`; `_reload_profile`; `_clean`; `DomainWriteError` (all in-module).
- Produces: `generate_profile_from_curves(*, name: str | None = None, valuation_date: datetime | None = None, actor: str = "agent", session: Session | None = None) -> PricingParameterProfile` — writes a `source_type="curve"` profile + one row per resolved trade with per-row provenance; audits `pricing_parameter_profile.generated_from_curves`; translates the compute's `ValueError`s into `DomainWriteError("unfilled_trades", {...})` / `DomainWriteError("no_open_positions")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_pricing_curve_generation.py`:

```python
from app.models import PricingParameterProfile, PricingParameterRow
from app.services.domains._errors import DomainWriteError
from app.services.domains.pricing_profiles import generate_profile_from_curves


def test_generate_profile_from_curves_writes_flat_profile(session: Session) -> None:
    _seed_open_option(session, underlying="000300.SH", maturity="2026-07-02")
    inst = ensure_underlying(session, "000300.SH", source="manual", status="active")
    inst.rate_curve = [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}]
    inst.dividend_yield = 0.01
    inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
    session.flush()

    profile = generate_profile_from_curves(
        name="Curve Set", valuation_date=datetime(2026, 1, 1), session=session
    )
    assert profile.source_type == "curve"
    assert profile.name == "Curve Set"
    assert profile.status == "completed"
    assert len(profile.rows) == 1
    row = profile.rows[0]
    assert row.symbol == "000300.SH"
    assert row.source_trade_id == "TRD-1"
    assert row.volatility == pytest.approx(0.22)
    assert row.source_payload["generated_from"] == "instrument_curves"
    # Persisted and reloadable.
    stored = (
        session.query(PricingParameterProfile)
        .filter(PricingParameterProfile.id == profile.id)
        .one()
    )
    assert stored.source_type == "curve"


def test_generate_profile_from_curves_unfilled_raises_domain_error(session: Session) -> None:
    _seed_open_option(session, underlying="000905.SH", maturity="2026-07-02",
                      source_trade_id="U-9")
    ensure_underlying(session, "000905.SH", source="manual", status="active")
    session.flush()
    with pytest.raises(DomainWriteError) as exc_info:
        generate_profile_from_curves(valuation_date=datetime(2026, 1, 1), session=session)
    assert exc_info.value.error == "unfilled_trades"
    assert exc_info.value.detail["unfilled_trades"][0]["source_trade_id"] == "U-9"


def test_generate_profile_from_curves_no_positions_raises_domain_error(session: Session) -> None:
    with pytest.raises(DomainWriteError) as exc_info:
        generate_profile_from_curves(valuation_date=datetime(2026, 1, 1), session=session)
    assert exc_info.value.error == "no_open_positions"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_pricing_curve_generation.py -k generate_profile_from_curves -v`
Expected: FAIL — `ImportError: cannot import name 'generate_profile_from_curves'`.

- [ ] **Step 3: Add the write facade**

In `backend/app/services/domains/pricing_profiles.py`, add after `create_profile` (reuses in-module `_session_scope`, `_clean`, `record_audit`, `_reload_profile`, `DomainWriteError`):

```python
def generate_profile_from_curves(
    *,
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PricingParameterProfile:
    """Materialize open-trade r/q/vol from instrument curves into a flat
    ``source_type="curve"`` pricing profile. Reversible (delete the profile)."""
    effective = valuation_date or datetime.utcnow()
    with _session_scope(session) as sess:
        try:
            rows = generate_curve_param_rows(sess, valuation_date=effective)
        except ValueError as exc:
            arg = exc.args[0] if exc.args else "generate failed"
            if isinstance(arg, dict) and "unfilled_trades" in arg:
                raise DomainWriteError(
                    "unfilled_trades", {"unfilled_trades": list(arg["unfilled_trades"])}
                ) from exc
            if arg == "no open positions in scope":
                raise DomainWriteError("no_open_positions") from exc
            raise
        profile = PricingParameterProfile(
            name=_clean(name) or f"Curve Pricing Parameters {effective:%Y-%m-%d}",
            valuation_date=effective,
            source_type="curve",
            source_path=None,
            status="completed",
            summary={"row_count": len(rows), "generated_from": "instrument_curves",
                     "created_by": actor},
        )
        sess.add(profile)
        sess.flush()
        for generated in rows:
            sess.add(
                PricingParameterRow(
                    profile_id=profile.id,
                    source_trade_id=generated.source_trade_id,
                    symbol=generated.symbol,
                    instrument_id=generated.instrument_id,
                    rate=generated.rate,
                    dividend_yield=generated.dividend_yield,
                    volatility=generated.volatility,
                    source_payload=generated.source_payload,
                )
            )
        record_audit(
            sess,
            event_type="pricing_parameter_profile.generated_from_curves",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"row_count": len(rows)},
        )
        sess.commit()
        return _reload_profile(sess, profile.id)
```

Add `"generate_profile_from_curves"`, `"generate_curve_param_rows"`, and `"GeneratedParamRow"` to the module's `__all__` list (line 90).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_pricing_curve_generation.py -v`
Expected: PASS (whole file).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/pricing_profiles.py tests/test_pricing_curve_generation.py
git commit -m "feat(pricing): write facade generating a source_type=curve profile"
```

---

## Task 5: REST — curve read/write on underlying defaults

**Files:**
- Modify: `backend/app/schemas.py:1530` (add `CurvePoint`; extend `UnderlyingPricingDefaultOut` + `UnderlyingPricingDefaultUpdate`)
- Modify: `backend/app/services/underlying_defaults.py:19` (extend `upsert_underlying_default`)
- Modify: `backend/app/main.py:2354` (extend `_serialize_default`)
- Test: `tests/test_curve_api.py`, `tests/test_underlying_defaults.py`

**Interfaces:**
- Consumes: `validate_curve` (Task 2); `update_underlying` (applies arbitrary columns via `setattr`, already generic).
- Produces: `PUT /api/underlying-pricing-defaults/{underlying}` accepts + returns `rate_curve`/`dividend_yield_curve`/`volatility_curve` (each `list[CurvePoint] | None`); invalid curve ⇒ HTTP 400.

- [ ] **Step 1: Write the failing test**

Create `tests/test_curve_api.py`:

```python
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings


@pytest.fixture()
def api_client(tmp_path) -> TestClient:
    from app.main import create_app

    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/api.db",
        artifact_dir=tmp_path / "artifacts",
    )
    return TestClient(create_app(settings))


def _seed_underlying(symbol: str) -> None:
    from app.database import SessionLocal
    from app.services.underlyings import ensure_underlying

    with SessionLocal() as s:
        ensure_underlying(s, symbol, source="manual", status="active")
        s.commit()


def test_put_curve_roundtrip(api_client: TestClient) -> None:
    _seed_underlying("000300.SH")
    body = {
        "rate_curve": [{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}],
        "volatility_curve": [{"tenor": "6M", "value": 0.22}],
    }
    resp = api_client.put("/api/underlying-pricing-defaults/000300.SH", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["rate_curve"] == [
        {"tenor": "3M", "value": 0.02},
        {"tenor": "1Y", "value": 0.05},
    ]
    assert data["volatility_curve"] == [{"tenor": "6M", "value": 0.22}]
    assert data["dividend_yield_curve"] is None
    # Round-trips through the list endpoint too.
    listed = api_client.get("/api/underlying-pricing-defaults").json()
    row = next(r for r in listed if r["underlying"] == "000300.SH")
    assert row["rate_curve"][0]["tenor"] == "3M"


def test_put_curve_rejects_unknown_label(api_client: TestClient) -> None:
    _seed_underlying("000300.SH")
    resp = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"rate_curve": [{"tenor": "4M", "value": 0.02}]},
    )
    assert resp.status_code == 400


def test_put_curve_rejects_nonpositive_vol(api_client: TestClient) -> None:
    _seed_underlying("000300.SH")
    resp = api_client.put(
        "/api/underlying-pricing-defaults/000300.SH",
        json={"volatility_curve": [{"tenor": "6M", "value": 0.0}]},
    )
    assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_curve_api.py -k put_curve -v`
Expected: FAIL — the PUT returns 200 but the response has no `rate_curve` key (KeyError in the assertion), and the unknown-label case returns 200 instead of 400.

- [ ] **Step 3: Add the schema**

In `backend/app/schemas.py`, add `CurvePoint` immediately before `class UnderlyingPricingDefaultOut` (line 1530):

```python
class CurvePoint(BaseModel):
    tenor: str
    value: float
```

Extend `UnderlyingPricingDefaultOut` (add after `volatility` at line 1534):

```python
    rate_curve: list[CurvePoint] | None = None
    dividend_yield_curve: list[CurvePoint] | None = None
    volatility_curve: list[CurvePoint] | None = None
```

Extend `UnderlyingPricingDefaultUpdate` (add after `volatility` at line 1550):

```python
    rate_curve: list[CurvePoint] | None = None
    dividend_yield_curve: list[CurvePoint] | None = None
    volatility_curve: list[CurvePoint] | None = None
```

- [ ] **Step 4: Extend the service write**

In `backend/app/services/underlying_defaults.py`, add the import and extend `upsert_underlying_default`:

```python
from app.services.term_structure import validate_curve
```

Replace the `upsert_underlying_default` signature + body (lines 19-40) with:

```python
def upsert_underlying_default(
    session: Session,
    *,
    underlying: str,
    rate=...,
    dividend_yield=...,
    volatility=...,
    notes=...,
    rate_curve=...,
    dividend_yield_curve=...,
    volatility_curve=...,
) -> UnderlyingPricingDefault:
    cleaned = (underlying or "").strip()
    if not cleaned:
        raise ValueError("underlying must not be empty")
    fields = {}
    if rate is not ...:
        fields["rate"] = rate
    if dividend_yield is not ...:
        fields["dividend_yield"] = dividend_yield
    if volatility is not ...:
        fields["volatility"] = volatility
    if notes is not ...:
        fields["notes"] = notes
    for column, value in (
        ("rate_curve", rate_curve),
        ("dividend_yield_curve", dividend_yield_curve),
        ("volatility_curve", volatility_curve),
    ):
        if value is not ...:
            fields[column] = validate_curve(
                value, require_positive=(column == "volatility_curve")
            )
    return update_underlying(session, cleaned, fields)
```

(`validate_curve` raises `ValueError` on a bad curve; the route already maps `ValueError` → HTTP 400.)

- [ ] **Step 5: Extend `_serialize_default`**

In `backend/app/main.py`, in `_serialize_default` (line 2365), add these keyword arguments to the `UnderlyingPricingDefaultOut(...)` constructor (after `volatility=...`):

```python
            rate_curve=row.rate_curve,
            dividend_yield_curve=row.dividend_yield_curve,
            volatility_curve=row.volatility_curve,
```

The PUT route (`main.py:2402`) already does `payload.model_dump(exclude_unset=True)` → `upsert_underlying_default(**body)`, so provided curve fields flow through automatically; absent fields keep their `...` sentinel and are untouched.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_curve_api.py -k put_curve tests/test_underlying_defaults.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/services/underlying_defaults.py backend/app/main.py tests/test_curve_api.py
git commit -m "feat(pricing): read/write term-structure curves on underlying defaults API"
```

---

## Task 6: REST — generate route `POST /from-curves`

**Files:**
- Modify: `backend/app/schemas.py` (add `GenerateFromCurvesRequest`)
- Modify: `backend/app/main.py:2510` (add the route beside the import route)
- Test: `tests/test_curve_api.py`

**Interfaces:**
- Consumes: `generate_profile_from_curves` (Task 4); `DomainWriteError`; `PricingParameterProfileOut`.
- Produces: `POST /api/pricing-parameter-profiles/from-curves` — body `{name?, valuation_date?}` → `PricingParameterProfileOut`; `unfilled_trades` ⇒ 400 with the list; `no_open_positions` ⇒ 400.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_curve_api.py`:

```python
def _seed_open_option_for_api(symbol: str, maturity: str, trade_id: str) -> None:
    from app.database import SessionLocal
    from app.models import Portfolio, Position
    from app.services.underlyings import ensure_underlying

    with SessionLocal() as s:
        portfolio = s.query(Portfolio).first() or Portfolio(name="Test")
        if portfolio.id is None:
            s.add(portfolio)
            s.flush()
        # Product-less (see _seed_open_option in test_pricing_curve_generation.py).
        s.add(Position(
            portfolio_id=portfolio.id, product_id=None, underlying=symbol,
            product_type="VanillaCall",
            product_kwargs={"underlying": symbol, "maturity_date": maturity},
            quantity=1.0, source_trade_id=trade_id, status="open",
            position_kind="otc", engine_name="quantark.vanilla", engine_kwargs={},
            source_payload={}, mapping_status="supported",
        ))
        inst = ensure_underlying(s, symbol, source="manual", status="active")
        inst.rate = 0.02
        inst.dividend_yield = 0.01
        inst.volatility_curve = [{"tenor": "6M", "value": 0.22}]
        s.commit()


def test_generate_from_curves_happy_path(api_client: TestClient) -> None:
    _seed_open_option_for_api("000300.SH", "2026-07-02", "T-API")
    resp = api_client.post(
        "/api/pricing-parameter-profiles/from-curves",
        json={"name": "Curve Run", "valuation_date": "2026-01-01T00:00:00"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["source_type"] == "curve"
    assert body["rows"][0]["symbol"] == "000300.SH"
    assert body["rows"][0]["volatility"] == pytest.approx(0.22)


def test_generate_from_curves_no_positions_400(api_client: TestClient) -> None:
    resp = api_client.post("/api/pricing-parameter-profiles/from-curves", json={})
    assert resp.status_code == 400
    assert "no open positions" in str(resp.json()["detail"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_curve_api.py -k generate_from_curves -v`
Expected: FAIL — 404 (route not defined yet).

- [ ] **Step 3: Add the request schema**

In `backend/app/schemas.py`, after `BuildAssumptionsRequest` (line 1563), add:

```python
class GenerateFromCurvesRequest(BaseModel):
    name: str | None = None
    valuation_date: datetime | None = None
```

- [ ] **Step 4: Add the route**

In `backend/app/main.py`, add imports near the other domain imports at the top of the file:

```python
from .services.domains.pricing_profiles import generate_profile_from_curves
from .services.domains._errors import DomainWriteError
from .schemas import GenerateFromCurvesRequest
```

(If `GenerateFromCurvesRequest` / `DomainWriteError` are imported via an existing aggregate import, add them there instead — match the file's import style.)

Add the route immediately after the import endpoint (after line 2550), inside `create_app`:

```python
    @app.post(
        "/api/pricing-parameter-profiles/from-curves",
        response_model=PricingParameterProfileOut,
    )
    def generate_pricing_parameters_from_curves_endpoint(
        payload: GenerateFromCurvesRequest,
        session: Session = Depends(get_db),
    ):
        try:
            profile = generate_profile_from_curves(
                name=payload.name,
                valuation_date=payload.valuation_date,
                actor="desk_user",
                session=session,
            )
        except DomainWriteError as exc:
            if exc.error == "unfilled_trades":
                raise HTTPException(
                    status_code=400,
                    detail={
                        "detail": "trades missing curve/scalar inputs",
                        "unfilled_trades": (exc.detail or {}).get("unfilled_trades", []),
                    },
                ) from exc
            if exc.error == "no_open_positions":
                raise HTTPException(
                    status_code=400, detail="no open positions in scope"
                ) from exc
            raise HTTPException(status_code=400, detail=exc.error) from exc
        return profile
```

(The domain facade commits and records the `pricing_parameter_profile.generated_from_curves` audit itself, so the route just returns the reloaded profile.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_curve_api.py -v`
Expected: PASS (whole file).

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_curve_api.py
git commit -m "feat(pricing): POST /pricing-parameter-profiles/from-curves generate route"
```

---

## Task 7: Agent tools + registration + HITL

**Files:**
- Modify: `backend/app/tools/_shaping.py:498` (`shape_instrument_defaults` returns curves)
- Modify: `backend/app/services/domains/assumptions.py:95` (`set_instrument_defaults` accepts + validates curves)
- Modify: `backend/app/tools/assumptions.py:45` (`SetInstrumentPricingDefaultsInput` + tool accept curves)
- Modify: `backend/app/tools/pricing_profiles.py` (add generate tool)
- Modify: `backend/app/tools/__init__.py:67,143` (import + register in `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/agents.py:373` (add to `DEEP_AGENT_TOOL_NAMES`)
- Modify: `backend/app/services/deep_agent/hitl.py:23,63,113` (three HITL structures)
- Test: `tests/test_tools_assumptions.py`, `tests/test_tools_pricing_profiles.py`, `tests/test_hitl.py`

**Interfaces:**
- Consumes: `generate_profile_from_curves` (Task 4); `validate_curve` (Task 2); `_profile_with_rows`, `parse_valuation_date`, `domain_write_error_response` (in-module).
- Produces:
  - `get_instrument_pricing_defaults` / `set_instrument_pricing_defaults` round-trip the three curve fields.
  - `generate_pricing_parameters_from_curves` tool (DOMAIN_WRITE, HITL "write") → `{"ok": True, "data": <profile-with-rows>}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tools_pricing_profiles.py` (match the file's existing fixture/import style — the assertions are the contract):

```python
def test_generate_pricing_parameters_from_curves_tool(session_with_open_curve_trade):
    """The tool materializes a source_type='curve' profile from instrument curves."""
    from app.tools.pricing_profiles import generate_pricing_parameters_from_curves_tool

    result = generate_pricing_parameters_from_curves_tool.func(
        name="Curve Run", valuation_date="2026-01-01T00:00:00"
    )
    assert result["ok"] is True
    assert result["data"]["source_type"] == "curve"
    assert result["data"]["rows"][0]["symbol"] == "000300.SH"


def test_generate_pricing_parameters_from_curves_tool_no_positions(empty_session):
    from app.tools.pricing_profiles import generate_pricing_parameters_from_curves_tool

    result = generate_pricing_parameters_from_curves_tool.func()
    assert result["ok"] is False
    assert result["error"] == "no_open_positions"
```

Append to `tests/test_tools_assumptions.py`:

```python
def test_set_and_get_instrument_curves(session_with_underlying):
    from app.tools.assumptions import (
        get_instrument_pricing_defaults_tool,
        set_instrument_pricing_defaults_tool,
    )

    set_result = set_instrument_pricing_defaults_tool.func(
        symbol="000300.SH",
        rate_curve=[{"tenor": "3M", "value": 0.02}, {"tenor": "1Y", "value": 0.05}],
    )
    assert set_result["ok"] is True
    assert set_result["data"]["rate_curve"][0]["tenor"] == "3M"

    got = get_instrument_pricing_defaults_tool.func(symbols=["000300.SH"])
    assert got["data"][0]["rate_curve"][1]["value"] == 0.05


def test_set_instrument_curve_rejects_unknown_label(session_with_underlying):
    from app.tools.assumptions import set_instrument_pricing_defaults_tool

    result = set_instrument_pricing_defaults_tool.func(
        symbol="000300.SH", volatility_curve=[{"tenor": "4M", "value": 0.2}]
    )
    assert result["ok"] is False
    assert result["error"] == "invalid_curve"
```

Append to `tests/test_hitl.py`:

```python
def test_generate_from_curves_is_hitl_write():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    name = "generate_pricing_parameters_from_curves"
    assert name in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL[name] == "write"
    assert name in _LABEL_BY_TOOL
```

(If `session_with_open_curve_trade` / `empty_session` / `session_with_underlying` fixtures don't already exist in those test modules, add small local fixtures mirroring the `_seed_open_option` / `ensure_underlying` seeding from `tests/test_pricing_curve_generation.py` and `tests/test_curve_api.py`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_pricing_profiles.py -k from_curves tests/test_tools_assumptions.py -k curve tests/test_hitl.py -k from_curves -v`
Expected: FAIL — tool/attribute not found; curve keys absent from shaped output; HITL name missing.

- [ ] **Step 3: Shape curves in the agent output**

In `backend/app/tools/_shaping.py`, in `shape_instrument_defaults` (line 498), add to the returned dict (after `"volatility"`):

```python
        "rate_curve": instrument.rate_curve,
        "dividend_yield_curve": instrument.dividend_yield_curve,
        "volatility_curve": instrument.volatility_curve,
```

- [ ] **Step 4: Accept curves in the domain setter**

In `backend/app/services/domains/assumptions.py`, add the import:

```python
from app.services.term_structure import validate_curve
```

Extend `set_instrument_defaults` (line 95). Add three keyword params to the signature (after `clear`):

```python
    rate_curve: list[dict] | None = None,
    dividend_yield_curve: list[dict] | None = None,
    volatility_curve: list[dict] | None = None,
```

Inside the function, after the existing scalar `invalid` check (before the `with _session_scope(...)` block), validate any provided curve (a curve param of `None` means "leave unchanged"; pass `[]` to clear):

```python
    curve_updates: dict[str, list[dict] | None] = {}
    for column, value, positive in (
        ("rate_curve", rate_curve, False),
        ("dividend_yield_curve", dividend_yield_curve, False),
        ("volatility_curve", volatility_curve, True),
    ):
        if value is not None:
            try:
                curve_updates[column] = validate_curve(value, require_positive=positive)
            except ValueError as exc:
                raise DomainWriteError("invalid_curve", {"field": column, "reason": str(exc)})
    if not provided and not clear_fields and not curve_updates:
        raise DomainWriteError("no_fields")
```

Then remove the earlier standalone `if not provided and not clear_fields: raise DomainWriteError("no_fields")` check (line 128-129) — it is now replaced by the combined check above. Inside the `with _session_scope(...)` block, after the existing `for field in clear_fields:` loop, apply the curves:

```python
        for column, value in curve_updates.items():
            setattr(instrument, column, value)
```

And extend the audit `payload` to include `"curves": sorted(curve_updates)`.

- [ ] **Step 5: Pass curves through the assumptions tool**

In `backend/app/tools/assumptions.py`, extend `SetInstrumentPricingDefaultsInput` (line 45) with:

```python
    rate_curve: list[dict] | None = Field(
        default=None,
        description="Term-structure rate curve: [{tenor: '3M', value: 0.02}]. "
        "Labels from {1W,2W,1M,2M,3M,6M,9M,1Y,18M,2Y,3Y,5Y}. None=leave "
        "unchanged; [] clears. Fed only to generate_pricing_parameters_from_curves.",
    )
    dividend_yield_curve: list[dict] | None = Field(default=None, description="See rate_curve.")
    volatility_curve: list[dict] | None = Field(
        default=None, description="See rate_curve; values must be > 0."
    )
```

Extend `set_instrument_pricing_defaults_tool` (line 103) — add the three params to the signature and pass them to the service call:

```python
def set_instrument_pricing_defaults_tool(
    symbol: str,
    rate: float | None = None,
    dividend_yield: float | None = None,
    volatility: float | None = None,
    clear: list[str] | None = None,
    rate_curve: list[dict] | None = None,
    dividend_yield_curve: list[dict] | None = None,
    volatility_curve: list[dict] | None = None,
) -> dict[str, Any]:
    """Set/clear an instrument's baseline r/q/vol AND term-structure curves; run
    build_assumption_set or generate_pricing_parameters_from_curves afterwards to
    materialize. HITL — requires confirmation."""
    try:
        instrument = assumptions_svc.set_instrument_defaults(
            symbol=symbol,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            clear=clear or [],
            rate_curve=rate_curve,
            dividend_yield_curve=dividend_yield_curve,
            volatility_curve=volatility_curve,
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": shape_instrument_defaults(instrument)}
```

- [ ] **Step 6: Add the generate tool**

In `backend/app/tools/pricing_profiles.py`, extend the domain import to include the new function:

```python
from app.services.domains import pricing_profiles as pricing_profiles_svc
```

(already imported — `generate_profile_from_curves` is reached via `pricing_profiles_svc.generate_profile_from_curves`.)

Add the input schema (after `DeletePricingParameterProfileInput`, line 82):

```python
class GeneratePricingParametersFromCurvesInput(BaseModel):
    name: str | None = Field(
        default=None, description="Defaults to 'Curve Pricing Parameters <date>'."
    )
    valuation_date: str | None = Field(
        default=None, description="ISO datetime; defaults to now."
    )
```

Add the tool (after `delete_pricing_parameter_profile_tool`, line 197):

```python
@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool(
    "generate_pricing_parameters_from_curves",
    args_schema=GeneratePricingParametersFromCurvesInput,
)
def generate_pricing_parameters_from_curves_tool(
    name: str | None = None,
    valuation_date: str | None = None,
) -> dict[str, Any]:
    """Interpolate every open trade's r/q/vol from its underlying's
    term-structure curves (flat-scalar fallback) into a new flat pricing profile
    (source_type='curve'); pass the returned id to run_batch_pricing. On
    unfilled_trades, set those instruments' curves or scalars and retry.
    HITL — requires confirmation."""
    try:
        profile = pricing_profiles_svc.generate_profile_from_curves(
            name=name, valuation_date=parse_valuation_date(valuation_date)
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile)}
```

Add `"generate_pricing_parameters_from_curves_tool"` to the module's `__all__` (line 200).

- [ ] **Step 7: Register the tool**

In `backend/app/tools/__init__.py`, add to the `from .pricing_profiles import (...)` block (line 67):

```python
    generate_pricing_parameters_from_curves_tool,
```

and add it to `QUANT_AGENT_TOOLS` in the "Pricing parameter writes" section (after `create_pricing_parameter_profile_tool`, line 222):

```python
    generate_pricing_parameters_from_curves_tool,
```

- [ ] **Step 8: Allowlist the tool**

In `backend/app/services/agents.py`, add to `DEEP_AGENT_TOOL_NAMES` in the "Pricing parameter writes" block (after `"create_pricing_parameter_profile"`, line 417):

```python
        "generate_pricing_parameters_from_curves",
```

- [ ] **Step 9: Wire HITL**

In `backend/app/services/deep_agent/hitl.py`:
- Add to `INTERRUPT_TOOL_NAMES` (after `"create_pricing_parameter_profile"`, line 52): `"generate_pricing_parameters_from_curves",`
- Add to `_RISK_LEVEL_BY_TOOL` (after line 100): `"generate_pricing_parameters_from_curves": "write",`
- Add to `_LABEL_BY_TOOL` (after line 142): `"generate_pricing_parameters_from_curves": "Generate pricing params from curves",`

- [ ] **Step 10: Run tests + the tool-registration guard**

Run: `.venv/bin/python -m pytest tests/test_tools_pricing_profiles.py tests/test_tools_assumptions.py tests/test_hitl.py -v`
Expected: PASS.

Run the registration guard (`select_deep_agent_tools` raises if the allowlist references a tool absent from `QUANT_AGENT_TOOLS`):
Run: `.venv/bin/python -c "from app.services.agents import select_deep_agent_tools; select_deep_agent_tools(); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 11: Commit**

```bash
git add backend/app/tools/_shaping.py backend/app/services/domains/assumptions.py backend/app/tools/assumptions.py backend/app/tools/pricing_profiles.py backend/app/tools/__init__.py backend/app/services/agents.py backend/app/services/deep_agent/hitl.py tests/test_tools_pricing_profiles.py tests/test_tools_assumptions.py tests/test_hitl.py
git commit -m "feat(pricing): agent tools for curves + curve-generate (HITL, allowlisted)"
```

- [ ] **Step 12: Full backend suite**

Run: `.venv/bin/python -m pytest`
Expected: PASS (no regressions).

---

## Task 8: Frontend — data layer (types + fetch/PUT/POST + prop threading)

**Files:**
- Modify: the file that types `UnderlyingPricingDefault` (per the api-client usage `api<UnderlyingPricingDefault[]>('/api/underlying-pricing-defaults')`; likely `frontend/src/api/types.ts` — locate with `grep -rn "UnderlyingPricingDefault" frontend/src`)
- Modify: `frontend/src/routes/Instruments.live.tsx` (state + handlers, ~lines 62-82, 264-363, 661-672)
- Modify: `frontend/src/routes/Instruments.tsx` (prop passthrough, ~lines 1109-1126)
- Test: `frontend/src/routes/Instruments.live.test.tsx`

**Interfaces:**
- Consumes: `api<T>(path, init?)` from `../api/client`.
- Produces:
  - TS `CurvePoint = { tenor: string; value: number }`; `UnderlyingPricingDefault` gains `rate_curve?: CurvePoint[] | null` etc.
  - `onCurveUpsert(underlying, curves)` handler → `PUT /api/underlying-pricing-defaults/{underlying}` with body `{rate_curve, dividend_yield_curve, volatility_curve}`.
  - `onGenerateFromCurves()` handler → `POST /api/pricing-parameter-profiles/from-curves`, returning the created profile or surfacing `unfilled_trades`.

- [ ] **Step 1: Write the failing test**

In `frontend/src/routes/Instruments.live.test.tsx`, add a fetch-mock branch + assertion mirroring the existing pattern (a curve PUT fires and the defaults reload):

```tsx
it('[curve-put] saving a curve PUTs to the defaults endpoint', async () => {
  let curvePutBody: string | undefined;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = requestUrl(input);
    if (url === '/api/underlying-pricing-defaults' && !init?.method) {
      return response([
        { underlying: '000300.SH', rate: 0.02, dividend_yield: 0.01,
          volatility: 0.2, is_complete: true, has_open_position: true,
          rate_curve: null, dividend_yield_curve: null, volatility_curve: null,
          created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00' },
      ]);
    }
    if (url.startsWith('/api/underlying-pricing-defaults/') && init?.method === 'PUT') {
      curvePutBody = init.body as string;
      return response({ underlying: '000300.SH' });
    }
    // ... existing branches for /api/instruments, /api/hedging/map, /api/assumptions/sets ...
    return response([]);
  });
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  // render, activate assumptions tab, trigger a curve save, then:
  await waitFor(() => expect(curvePutBody).toBeDefined());
  expect(JSON.parse(curvePutBody!)).toHaveProperty('rate_curve');
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- Instruments.live`
Expected: FAIL (no curve save path yet; `curvePutBody` stays undefined).

- [ ] **Step 3: Extend the TS types**

In the `UnderlyingPricingDefault` type definition, add:

```tsx
export type CurvePoint = { tenor: string; value: number };

// inside the UnderlyingPricingDefault interface/type:
  rate_curve?: CurvePoint[] | null;
  dividend_yield_curve?: CurvePoint[] | null;
  volatility_curve?: CurvePoint[] | null;
```

- [ ] **Step 4: Add the handlers in `Instruments.live.tsx`**

Add a curve-save handler mirroring `onAssumptionUpsert` (lines 346-363):

```tsx
const onCurveUpsert = async (
  underlying: string,
  curves: {
    rate_curve: CurvePoint[] | null;
    dividend_yield_curve: CurvePoint[] | null;
    volatility_curve: CurvePoint[] | null;
  },
) => {
  if (!assumptionUnderlyingRoleSymbolSet.has(underlying.trim().toLowerCase())) {
    setAssumptionBuildFeedback(`Skipped ${underlying}: not an UNDERLYING role instrument`);
    return;
  }
  try {
    await api(`/api/underlying-pricing-defaults/${encodeURIComponent(underlying)}`, {
      method: 'PUT',
      body: JSON.stringify(curves),
    });
    if (!cancelledRef.current) await loadAssumptionDefaults();
  } catch (err) {
    console.error('Failed to upsert curves', err);
  }
};

const onGenerateFromCurves = async (name?: string) => {
  try {
    const profile = await api<{ id: number; name: string }>(
      '/api/pricing-parameter-profiles/from-curves',
      { method: 'POST', body: JSON.stringify({ name: name ?? null }) },
    );
    if (!cancelledRef.current) {
      setAssumptionBuildFeedback(`Generated pricing profile "${profile.name}" (#${profile.id})`);
    }
  } catch (err) {
    // api() throws Error(responseText); the 400 body carries {detail:{unfilled_trades}}.
    if (!cancelledRef.current) setAssumptionBuildFeedback(String(err));
  }
};
```

Thread both into the `<Instruments ... />` render (lines 661-672):

```tsx
onCurveUpsert={(u, c) => { void onCurveUpsert(u, c); }}
onGenerateFromCurves={(name) => { void onGenerateFromCurves(name); }}
```

- [ ] **Step 5: Pass through in `Instruments.tsx`**

In the `<InstrumentsAssumptions ... />` render (lines ~1109-1126), forward the two new props: `onCurveUpsert={onCurveUpsert}` and `onGenerateFromCurves={onGenerateFromCurves}`. Add them to the `Instruments` component's prop type alongside `onAssumptionUpsert`.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd frontend && npm test -- Instruments.live`
Expected: PASS.

- [ ] **Step 7: Type-check + commit**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

```bash
git add frontend/src/api frontend/src/routes/Instruments.live.tsx frontend/src/routes/Instruments.tsx frontend/src/routes/Instruments.live.test.tsx
git commit -m "feat(pricing): frontend data layer for curve save + generate"
```

---

## Task 9: Frontend — curve editor + charts + Generate button

**Files:**
- Modify: `frontend/src/routes/InstrumentsAssumptions.tsx`
- Modify: `frontend/src/routes/InstrumentsAssumptions.css`
- Test: `frontend/src/routes/InstrumentsAssumptions.test.tsx`

**Interfaces:**
- Consumes: `onCurveUpsert`, `onGenerateFromCurves` props (Task 8); `Select` (`../components/Select`); `NumberInput` (`../components/NumberInput`); recharts.
- Produces: a per-underlying curve editor (add/edit/remove `{tenor-label, value}` rows for r/q/vol), three token-styled `LineChart`s, and a "Generate pricing parameters from curves" button.

**Design decision (chart layout):** render **three separate small `LineChart`s** — one each for r, q, vol — rather than one combined chart. Rationale: r/q sit near 0–5% while vol sits near 15–40%; a shared y-axis would flatten the rate/dividend curves into a straight line. Each chart auto-scales its own y-axis (`domain={['auto', 'auto']}`), x-axis is tenor ordered by year-fraction.

- [ ] **Step 1: Write the failing test**

In `frontend/src/routes/InstrumentsAssumptions.test.tsx`, add a curve-edit assertion mirroring the existing `[defaults-edit]` test (query by `aria-label`/role, assert the `onCurveUpsert` mock args):

```tsx
it('[curve-edit] adding a rate curve point submits {rate_curve}', () => {
  const onCurveUpsert = vi.fn();
  render(
    <InstrumentsAssumptions
      {...defaultBaseProps}
      defaults={[completeDefault]}
      onCurveUpsert={onCurveUpsert}
      onGenerateFromCurves={vi.fn()}
    />,
  );
  // open the curve editor for the row, choose a tenor + value, save:
  fireEvent.click(screen.getByRole('button', { name: /edit curves/i }));
  fireEvent.change(screen.getByLabelText(/rate curve tenor/i), { target: { value: '3M' } });
  fireEvent.change(screen.getByLabelText(/rate curve value/i), { target: { value: '0.02' } });
  fireEvent.click(screen.getByRole('button', { name: /add point/i }));
  fireEvent.click(screen.getByRole('button', { name: /save curves/i }));
  expect(onCurveUpsert).toHaveBeenCalledWith(
    '000300.SH',
    expect.objectContaining({
      rate_curve: [{ tenor: '3M', value: 0.02 }],
    }),
  );
});

it('[curve-generate] the generate button calls onGenerateFromCurves', () => {
  const onGenerate = vi.fn();
  render(
    <InstrumentsAssumptions
      {...defaultBaseProps}
      defaults={[completeDefault]}
      onCurveUpsert={vi.fn()}
      onGenerateFromCurves={onGenerate}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: /generate pricing parameters from curves/i }));
  expect(onGenerate).toHaveBeenCalled();
});
```

Add the two new props to `defaultBaseProps` (both `vi.fn()`).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- InstrumentsAssumptions`
Expected: FAIL (no curve editor / generate button rendered).

- [ ] **Step 3: Extend the component props**

In `InstrumentsAssumptions.tsx`, extend `InstrumentsAssumptionsProps` (lines 54-86):

```tsx
  /** Save curve edits → PUT /api/underlying-pricing-defaults/{underlying} */
  onCurveUpsert: (
    underlying: string,
    curves: {
      rate_curve: CurvePoint[] | null;
      dividend_yield_curve: CurvePoint[] | null;
      volatility_curve: CurvePoint[] | null;
    },
  ) => void;
  /** Materialize curves into a pricing profile → POST …/from-curves */
  onGenerateFromCurves: (name?: string) => void;
```

- [ ] **Step 4: Build the curve editor + charts**

Add a `CurveEditor` sub-component (co-located, mirroring `DefaultsGrid`'s string-draft edit pattern). Key points:
- One editor block per param (rate / dividend_yield / volatility). Each holds a list of `{tenor, value}` draft rows.
- Tenor picker uses `Select` (`variant="inline"`, `searchable`) with `options` built from the label set, each with `aria-label={`${param} curve tenor`}` on the trigger; value input uses `NumberInput` with `step="any"` and `aria-label={`${param} curve value`}`.
- "Add point" appends a `{tenor, value}` from the draft; "remove" drops one; "Save curves" calls `onCurveUpsert(underlying, { rate_curve, dividend_yield_curve, volatility_curve })` (empty list → `null`).
- Sort points by `TENOR_YEARS` order (import a frontend copy of the label→years map, or a fixed label-order array) for both display and the chart x-axis.

Add the three charts using the **`Backtest.tsx` token-only recharts idiom** (per underlying, one chart per param):

```tsx
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';

function CurveChart({ label, color, points }: {
  label: string; color: string; points: CurvePoint[];
}) {
  const data = [...points].sort(
    (a, b) => TENOR_ORDER.indexOf(a.tenor) - TENOR_ORDER.indexOf(b.tenor),
  );
  return (
    <ResponsiveContainer width="100%" height={160}>
      <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: 8 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--hairline)" />
        <XAxis dataKey="tenor" tick={{ fontSize: 10, fill: 'var(--ink-2)' }}
          tickLine={false} axisLine={{ stroke: 'var(--hairline)' }} />
        <YAxis domain={['auto', 'auto']} width={56}
          tick={{ fontSize: 10, fill: 'var(--ink-2)' }}
          tickLine={false} axisLine={{ stroke: 'var(--hairline)' }} />
        <Tooltip contentStyle={{
          background: 'var(--paper)', border: '1px solid var(--hairline-2)',
          fontSize: 'var(--type-small-size)', color: 'var(--ink)' }} />
        <Line type="monotone" dataKey="value" name={label}
          stroke={color} dot strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

Assign each param an explicit token color: rate → `var(--info)`, dividend_yield → `var(--pos)`, volatility → `var(--warn)`. **No hex.** Define `TENOR_ORDER` as the fixed label array `['1W','2W','1M','2M','3M','6M','9M','1Y','18M','2Y','3Y','5Y']` (must match backend `TENOR_YEARS` key order).

Add the Generate button at the tab level:

```tsx
<Button variant="primary"
  onClick={() => onGenerateFromCurves()}>
  Generate pricing parameters from curves
</Button>
```

- [ ] **Step 5: Style with tokens**

In `InstrumentsAssumptions.css`, add `wl-`-prefixed BEM classes for the editor layout (grid rows, chart grid). Use ONLY `var(--…)` tokens — spacing `--gap-*`, borders `--hairline`/`--hairline-2`, text `--ink`/`--ink-2`, surfaces `--paper`/`--paper-2`. No hardcoded values.

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npm test -- InstrumentsAssumptions`
Expected: PASS.

- [ ] **Step 7: Type-check + full frontend suite**

Run: `cd frontend && npx tsc --noEmit && npm test`
Expected: no type errors; all vitest green.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/routes/InstrumentsAssumptions.tsx frontend/src/routes/InstrumentsAssumptions.css frontend/src/routes/InstrumentsAssumptions.test.tsx
git commit -m "feat(pricing): curve editor + charts + generate button on Assumptions tab"
```

---

## Task 10: Docs & rollout

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `CLAUDE.md`
- Modify: `README.md` (if judged user-facing)

- [ ] **Step 1: CHANGELOG**

Under `## [Unreleased]` in `CHANGELOG.md`, add:

```markdown
### Added
- **Term-structure curves for pricing parameters.** Author per-underlying
  `r`/`q`/`vol` curves on the Instrument baseline (Instruments → Assumptions tab,
  or via `set_instrument_pricing_defaults`); a new *generate* step interpolates
  each open trade's flat `r/q/vol` at its ACT/365 tenor into a
  `PricingParameterProfile(source_type="curve")`. Pricing stays flat/unchanged.
  New: migration `0050`, `POST /api/pricing-parameter-profiles/from-curves`,
  agent tool `generate_pricing_parameters_from_curves` (HITL), curve fields on
  the underlying-pricing-defaults API.
```

- [ ] **Step 2: CLAUDE.md subsystem section**

Add a new `## Term-structure curves for pricing parameters` section documenting: storage on `Instrument` (three nullable JSON columns, migration `0050`); the *materialize-then-price* boundary (`generate_profile_from_curves` → `source_type="curve"` profile → unchanged pricing path); the pure `term_structure.py` module; the curve-feeds-**only**-generate boundary (`build_assumptions_set` untouched); missing-curve → flat-scalar fallback; ACT/365 tenor; and the `DEEP_AGENT_TOOL_NAMES` + `hitl.py` (three structures) registration gotcha for the new tool.

- [ ] **Step 3: README (if user-facing)**

If the desk-user docs describe the Assumptions tab or pricing parameters, add a short paragraph on curve authoring + generate. Otherwise skip and note the decision in the commit message.

- [ ] **Step 4: Final full-suite check**

Run: `.venv/bin/python -m pytest && cd frontend && npx tsc --noEmit && npm test`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md CLAUDE.md README.md
git commit -m "docs(pricing): document term-structure curves subsystem"
```

---

## Self-review notes (for the implementer)

- **Interpolation is only ever run to produce flat scalars.** If you find yourself passing a curve into `risk_engine`, `quantark`, or `build_assumptions_set`, stop — that's out of scope (§12 of the design). Curves feed `generate_curve_param_rows` and nothing else.
- **`source_type="curve"` is load-bearing.** It's how a generated profile is distinguished from `agent`/`xlsx`/`default_underlying_archived` profiles. Don't reuse `create_profile` (which forces `"agent"`).
- **The new tool needs four registrations**, not one: `QUANT_AGENT_TOOLS` (tools/__init__.py), `DEEP_AGENT_TOOL_NAMES` (agents.py), and the three `hitl.py` structures. The `select_deep_agent_tools()` smoke in Task 7 Step 10 is the guard that you got the first two right; the `test_hitl.py` case guards the third.
- **`validate_curve` is the single validation seam** — reused by the REST PUT (`upsert_underlying_default`) and the agent setter (`set_instrument_defaults`). Volatility curves require `> 0`; rate/dividend allow any finite value (rates can be negative).
- **Unfilled contract mirrors `build_assumptions_set`**: a `ValueError({"unfilled_trades": [...]})` from the compute becomes `DomainWriteError("unfilled_trades", …)` at the facade and a 400 with the list at the route — the same shape operators already see for assumption builds.
