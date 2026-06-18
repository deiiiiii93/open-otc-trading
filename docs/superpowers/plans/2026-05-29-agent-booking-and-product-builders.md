# Direct-Booking Routing + Quant-Ark Product Builders — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the desk agent recognize a direct "book a `<product>`" intent (asking quote-first before delegating), and give it a deterministic `build_product` tool that turns LLM-extracted terms into quant-ark-validated products for every catalog family.

**Architecture:** Trader (LLM) extracts structured per-family terms → deterministic `build_product(family, terms)` tool backed by a per-family builder registry that synthesizes KO/KI/averaging observation schedules (shared `schedules.py`, extracted from `position_adapter`) and validates via `validate_quantark_build`. Orchestrator gains a booking-intent route with a one-shot quote-first clarification. The regex NL drafter is retired.

**Tech Stack:** Python 3.11, SQLAlchemy, Pydantic v2, LangChain `@tool`, deepagents, pytest. Quant-ark at `/Users/fuxinyao/quant-ark` (on `sys.path` via `quantark.ensure_quantark_path`).

**Spec:** `docs/superpowers/specs/2026-05-29-agent-booking-and-product-builders-design.md`

**Conventions in this repo (read before starting):**
- Tools are LangChain tools; tests invoke them via `tool.invoke({...})`.
- `validate_quantark_build(product_type, product_kwargs, market, engine_name, engine_kwargs=None) -> QuantArkResult(ok, data, error)` (`services/quantark.py:773`).
- Proven-good autocallable kwargs shape lives in `services/position_adapter.py` (`_snowball_barrier_config`, `_ki_barrier_config`, `_single_barrier_schedule`, `_ki_schedule`); mirror it.
- Schedule dict shape: `{"records":[{"observation_date":"YYYY-MM-DD","barrier":X,"return_rate":r?,"is_rate_annualized":bool?}], "aggregation_mode":"STOP_FIRST_HIT", "frequency":"MONTHLY"|"DAILY"|"CUSTOM"}`.
- Run a single test: `cd /Users/fuxinyao/open-otc-trading && python -m pytest tests/<file>::<test> -v` (the repo uses a `.venv`; `python` resolves to it).

**Ground-truth rule for builders:** every builder task's acceptance test asserts
`build_product(...).validation["ok"] is True` by actually building through quant-ark.
If exact kwarg names differ from what's shown, mirror the proven
`position_adapter` output until `validate_quantark_build` passes — the test is the spec.

---

## File Structure

**Create:**
- `backend/app/services/domains/schedules.py` — pure SSE-calendar + observation-schedule synthesis (extracted from `position_adapter`).
- `backend/app/services/domains/product_builders.py` — `BuildResult`, `build_product`, per-family builder registry.
- `backend/app/tools/products.py` — `build_product` `@tool` wrapper.
- `backend/app/skills/workflows/products/build-product/SKILL.md` — anchor skill.
- `backend/app/skills/references/products/build-contract.md` — per-family term schema reference.
- `tests/test_schedules.py`, `tests/test_product_builders.py`, `tests/test_tools_products.py`.

**Modify:**
- `backend/app/services/position_adapter.py` — import helpers from `schedules.py`.
- `backend/app/tools/__init__.py` — register `build_product_tool`.
- `backend/app/services/deep_agent/prompts/orchestrator.md` — booking route + quote-first contract + known-skills rows.
- `backend/app/services/deep_agent/prompts/trader.md` — swap tool + routing line.
- `backend/app/services/deep_agent/personas.py` — add `/skills/workflows/products/` to trader.
- `backend/app/skills/workflows/positions/book-position/SKILL.md` — build_product step + engine mapping.
- `backend/app/skills/workflows/rfq/draft-rfq/SKILL.md` — build_product step.
- `backend/app/services/quantark.py` — remove dead `parse_natural_language_rfq`.
- `backend/app/services/rfq.py`, `backend/app/services/domains/rfq.py`, `backend/app/tools/rfq.py`, `backend/app/main.py`, `backend/app/services/agents.py` — migrate `draft_rfq_from_natural_language`.
- Catalog/routing tests: `tests/test_skills_catalog_v2.py`, `tests/test_routing_contracts_phase3.py`, `tests/test_quant_services.py`, `tests/test_services_domains_rfq.py`, `tests/test_tools_rfq.py`.

---

## Phase 0 — Shared schedule synthesis module

### Task 1: Extract SSE-calendar + schedule helpers into `schedules.py`

**Files:**
- Create: `backend/app/services/domains/schedules.py`
- Test: `tests/test_schedules.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_schedules.py
from __future__ import annotations

from datetime import date

from app.services.domains import schedules


def test_add_months_rolls_year():
    assert schedules.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


def test_china_sse_business_days_excludes_weekends():
    days = schedules.china_sse_business_days(date(2026, 5, 30), date(2026, 6, 5))
    # 2026-05-30 is a Saturday, 05-31 Sunday -> excluded; weekdays included
    assert date(2026, 5, 30) not in days
    assert date(2026, 6, 1) in days


def test_monthly_observation_dates_respects_lockup_and_count():
    dates = schedules.monthly_observation_dates(
        start=date(2026, 1, 5), maturity_years=1.0, lockup_months=3
    )
    # months 3..12 inclusive -> 10 observations, all business days
    assert len(dates) == 10
    assert all(schedules.is_china_sse_business_day(d) for d in dates)
    assert dates == sorted(dates)


def test_build_ko_schedule_shape():
    sched = schedules.build_ko_schedule(
        dates=[date(2026, 4, 6), date(2026, 5, 6)],
        barriers=[101.0, 101.0],
        rates=[0.15, 0.15],
        annualized=True,
        frequency="MONTHLY",
    )
    assert sched["aggregation_mode"] == "STOP_FIRST_HIT"
    assert sched["frequency"] == "MONTHLY"
    assert sched["records"][0] == {
        "observation_date": "2026-04-06",
        "barrier": 101.0,
        "return_rate": 0.15,
        "is_rate_annualized": True,
    }


def test_daily_ki_dates_starts_day_after_start():
    dates = schedules.daily_ki_dates(start=date(2026, 1, 5), exercise=date(2026, 1, 9))
    assert date(2026, 1, 5) not in dates
    assert date(2026, 1, 6) in dates
    assert max(dates) <= date(2026, 1, 9)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_schedules.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.domains.schedules`.

- [ ] **Step 3: Implement `schedules.py`**

```python
# backend/app/services/domains/schedules.py
"""Pure SSE-calendar and observation-schedule synthesis.

Single source of truth shared by the spreadsheet import adapter
(`position_adapter`) and the agent product builders (`product_builders`).
No DB, no quant-ark product objects — just dates and plain dicts.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from functools import lru_cache

from ...config import get_settings


def add_months(d: date, months: int) -> date:
    """Add `months` to a date, clamping the day to the target month's length."""
    base = d.month - 1 + months
    year = d.year + base // 12
    month = base % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@lru_cache(maxsize=1)
def china_sse_holidays() -> frozenset[date]:
    holiday_file = get_settings().quantark_path / "util/calendar/holidayfile/china_sse.csv"
    if not holiday_file.exists():
        return frozenset()
    holidays: set[date] = set()
    for line in holiday_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            holidays.add(date.fromisoformat(text))
        except ValueError:
            continue
    return frozenset(holidays)


def is_china_sse_business_day(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in china_sse_holidays()


def roll_to_business_day(day: date) -> date:
    """Roll forward to the next SSE business day (inclusive of `day`)."""
    current = day
    for _ in range(14):  # bounded: no 14-day holiday run on SSE
        if is_china_sse_business_day(current):
            return current
        current += timedelta(days=1)
    return current


def china_sse_business_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        if is_china_sse_business_day(current):
            days.append(current)
        current += timedelta(days=1)
    return days


def monthly_observation_dates(
    *, start: date, maturity_years: float, lockup_months: int, day_of_month: int | None = None
) -> list[date]:
    """Monthly KO observation dates from `start`+lockup through maturity.

    Observations at months lockup..total_months inclusive, each rolled forward
    to the next SSE business day. `day_of_month` defaults to the start day.
    """
    total_months = round(maturity_years * 12)
    anchor = start if day_of_month is None else start.replace(
        day=min(day_of_month, calendar.monthrange(start.year, start.month)[1])
    )
    dates: list[date] = []
    for m in range(lockup_months, total_months + 1):
        dates.append(roll_to_business_day(add_months(anchor, m)))
    return dates


def daily_ki_dates(*, start: date, exercise: date) -> list[date]:
    """Daily KI business-day observations from start+1 through exercise."""
    return china_sse_business_days(start + timedelta(days=1), exercise)


def build_ko_schedule(
    *, dates, barriers, rates, annualized: bool = False, frequency: str = "MONTHLY"
) -> dict:
    records = []
    for index, d in enumerate(dates):
        record: dict = {
            "observation_date": d.isoformat(),
            "barrier": barriers[index] if index < len(barriers) else barriers[-1],
        }
        if rates:
            record["return_rate"] = rates[index] if index < len(rates) else rates[-1]
            record["is_rate_annualized"] = annualized
        records.append(record)
    return {"records": records, "aggregation_mode": "STOP_FIRST_HIT", "frequency": frequency}


def build_ki_schedule(*, dates, barrier, frequency: str = "DAILY") -> dict:
    return {
        "records": [{"observation_date": d.isoformat(), "barrier": barrier} for d in dates],
        "aggregation_mode": "STOP_FIRST_HIT",
        "frequency": frequency,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_schedules.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/schedules.py tests/test_schedules.py
git commit -m "feat(schedules): shared SSE-calendar observation-schedule synthesis"
```

### Task 2: Point `position_adapter` at `schedules.py` (behavior-preserving)

**Files:**
- Modify: `backend/app/services/position_adapter.py` (helpers `_china_sse_business_day`, `_china_sse_holidays`, `_single_barrier_schedule`, `_ki_schedule`, and their callers)

- [ ] **Step 1: Run the existing adapter tests to capture the green baseline**

Run: `python -m pytest tests/ -k "position_adapter or import" -v`
Expected: PASS (record the count).

- [ ] **Step 2: Replace the private SSE helpers with imports**

In `position_adapter.py`, add near the top imports:

```python
from .schedules import (
    china_sse_holidays as _shared_sse_holidays,
    is_china_sse_business_day as _shared_is_business_day,
)
```

Then make the existing private helpers delegate (keep their names/signatures so the rest of the file is untouched):

```python
def _china_sse_holidays() -> frozenset[date]:
    return _shared_sse_holidays()


def _is_china_sse_business_day(day: date) -> bool:
    return _shared_is_business_day(day)
```

Leave `_single_barrier_schedule` / `_ki_schedule` as-is for now (import path uses explicit dates; not in scope to rewire their internals — only the calendar helpers are deduplicated).

- [ ] **Step 3: Run the adapter tests again**

Run: `python -m pytest tests/ -k "position_adapter or import" -v`
Expected: PASS (same count as Step 1).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/position_adapter.py
git commit -m "refactor(position_adapter): source SSE calendar from shared schedules module"
```

---

## Phase 1 — Builder registry core + autocallables

### Task 3: `BuildResult`, `build_product` dispatcher, engine map, snowball builder

**Files:**
- Create: `backend/app/services/domains/product_builders.py`
- Test: `tests/test_product_builders.py`

- [ ] **Step 1: Write the failing test (snowball is the reported bug)**

```python
# tests/test_product_builders.py
from __future__ import annotations

from app.services.domains.product_builders import build_product


def _snowball_terms(**over):
    terms = {
        "underlying": "000905.SH",
        "currency": "CNY",
        "initial_price": 100.0,
        "maturity_years": 1.0,
        "ko_barrier_pct": 101,
        "ki_barrier_pct": 70,
        "ko_rate": 0.15,
        "ko_frequency": "MONTHLY",
        "ki_convention": "DAILY",
        "lockup_months": 3,
        "trade_start_date": "2026-01-05",
    }
    terms.update(over)
    return terms


def test_snowball_builds_and_validates():
    result = build_product("SnowballOption", _snowball_terms())
    assert result.missing == []
    assert result.engine_name == "SnowballQuadEngine"
    bc = result.product_kwargs["barrier_config"]
    assert bc["ko_barrier"] == 101.0          # 101% of initial 100
    assert bc["ki_barrier"] == 70.0           # 70% of initial 100
    assert bc["ko_observation_schedule"]["records"]  # monthly KO synthesized
    assert bc["ki_observation_schedule"]["records"]  # daily KI synthesized
    assert result.validation["ok"] is True
    assert result.ok is True


def test_snowball_missing_lockup_and_start_are_reported_not_invented():
    terms = _snowball_terms()
    terms.pop("lockup_months")
    terms.pop("trade_start_date")
    result = build_product("SnowballOption", terms)
    assert "barrier_config.lockup_months" in result.missing
    assert "trade_start_date" in result.missing
    assert result.ok is False
    # No fabricated schedule when inputs are missing
    assert "ko_observation_schedule" not in result.product_kwargs.get("barrier_config", {})


def test_snowball_missing_coupon_reported():
    terms = _snowball_terms()
    terms.pop("ko_rate")
    result = build_product("SnowballOption", terms)
    assert "barrier_config.ko_rate" in result.missing
    assert result.ok is False


def test_unknown_family_is_reported():
    result = build_product("NotARealOption", {})
    assert result.ok is False
    assert "unsupported_family" in result.warnings[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_product_builders.py -v`
Expected: FAIL — `ModuleNotFoundError: product_builders`.

- [ ] **Step 3: Implement the core + snowball builder**

```python
# backend/app/services/domains/product_builders.py
"""Deterministic per-family quant-ark product builders.

The trader persona (LLM) fills a structured per-family `terms` dict; these
builders turn it into validated quant-ark product_kwargs, synthesizing
observation schedules where required. Economics that would have to be
invented (lockup, trade start, barrier levels, coupon) are reported in
`missing` — never fabricated.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from ...schemas import PricingEnvironmentSnapshot
from ..quantark import validate_quantark_build
from . import schedules

_ENGINE_BY_CLASS: dict[str, str] = {
    "EuropeanVanillaOption": "BlackScholesEngine",
    "AmericanOption": "AmericanOptionAnalyticalEngine",
    "CashOrNothingDigitalOption": "DigitalOptionAnalyticalEngine",
    "BarrierOption": "BarrierAnalyticalEngine",
    "OneTouchOption": "OneTouchAnalyticalEngine",
    "AsianOption": "AsianOptionAnalyticalEngine",
    "RangeAccrualOption": "RangeAccrualAnalyticalEngine",
    "SnowballOption": "SnowballQuadEngine",
    "KnockOutResetSnowballOption": "KOResetSnowballQuadEngine",
    "PhoenixOption": "PhoenixQuadEngine",
    "SingleSharkfinOption": "SingleSharkfinOptionAnalyticalEngine",
    "DoubleSharkfinOption": "DoubleSharkfinOptionAnalyticalEngine",
    "Futures": "DeltaOneEngine",
    "SpotInstrument": "DeltaOneEngine",
}


@dataclass
class _Out:
    product_kwargs: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BuildResult:
    ok: bool
    quantark_class: str
    engine_name: str
    product_kwargs: dict[str, Any]
    missing: list[str]
    warnings: list[str]
    validation: dict[str, Any] | None


# ----- shared helpers --------------------------------------------------------

def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _abs_barrier(terms: dict, abs_key: str, pct_key: str, initial: float) -> float | None:
    direct = _num(terms.get(abs_key))
    if direct is not None:
        return direct
    pct = _num(terms.get(pct_key))
    return None if pct is None else round(initial * pct / 100.0, 6)


def _start_date(terms: dict) -> date | None:
    raw = terms.get("trade_start_date")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw))
    except ValueError:
        return None


# ----- per-family builders ---------------------------------------------------

def _build_snowball(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    initial = _num(terms.get("initial_price")) or 100.0
    maturity = _num(terms.get("maturity_years"))
    out.product_kwargs = {
        "initial_price": initial,
        "strike": _num(terms.get("strike")) or initial,
        "maturity": maturity if maturity is not None else 1.0,
        "contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0,
        "payoff_config": {"include_principal": bool(terms.get("include_principal", False))},
        "accrual_config": {},
    }
    if maturity is None:
        out.missing.append("maturity_years")

    ko_barrier = _abs_barrier(terms, "ko_barrier", "ko_barrier_pct", initial)
    ki_barrier = _abs_barrier(terms, "ki_barrier", "ki_barrier_pct", initial)
    ko_rate = _num(terms.get("ko_rate"))
    lockup = terms.get("lockup_months")
    start = _start_date(terms)
    annualized = bool(terms.get("ko_rate_annualized", False))
    ki_convention = str(terms.get("ki_convention", "DAILY")).upper()

    if ko_barrier is None:
        out.missing.append("barrier_config.ko_barrier")
    if ko_rate is None:
        out.missing.append("barrier_config.ko_rate")
    if lockup is None:
        out.missing.append("barrier_config.lockup_months")
    if start is None:
        out.missing.append("trade_start_date")
    if ki_convention not in {"DAILY", "EUROPEAN", "NONE"}:
        out.warnings.append(f"unknown ki_convention {ki_convention}; defaulting DAILY")
        ki_convention = "DAILY"
    if ki_convention != "NONE" and ki_barrier is None:
        out.missing.append("barrier_config.ki_barrier")

    # Cannot synthesize schedules without start + lockup + maturity.
    if out.missing:
        bc: dict[str, Any] = {"ko_observation_type": "DISCRETE"}
        if ko_barrier is not None:
            bc["ko_barrier"] = ko_barrier
        if ko_rate is not None:
            bc["ko_rate"] = ko_rate
        if ki_barrier is not None and ki_convention != "NONE":
            bc["ki_barrier"] = ki_barrier
        out.product_kwargs["barrier_config"] = bc
        return out

    exercise = schedules.add_months(start, round(maturity * 12))
    ko_dates = schedules.monthly_observation_dates(
        start=start, maturity_years=maturity, lockup_months=int(lockup)
    )
    bc = {
        "ko_barrier": ko_barrier,
        "ko_rate": ko_rate,
        "ko_observation_type": "DISCRETE",
        "ko_observation_schedule": schedules.build_ko_schedule(
            dates=ko_dates,
            barriers=[ko_barrier] * len(ko_dates),
            rates=[ko_rate] * len(ko_dates),
            annualized=annualized,
            frequency="MONTHLY",
        ),
    }
    if ki_convention != "NONE":
        bc["ki_barrier"] = ki_barrier
        bc["ki_observation_type"] = "DISCRETE"
        bc["ki_continuous"] = False
        if ki_convention == "DAILY":
            ki_dates = schedules.daily_ki_dates(start=start, exercise=exercise)
            bc["ki_observation_schedule"] = schedules.build_ki_schedule(
                dates=ki_dates, barrier=ki_barrier, frequency="DAILY"
            )
        else:  # EUROPEAN
            bc["ki_observation_schedule"] = schedules.build_ki_schedule(
                dates=[exercise], barrier=ki_barrier, frequency="CUSTOM"
            )
    out.product_kwargs["barrier_config"] = bc
    out.product_kwargs["accrual_config"] = {
        "coupon_pay_type": "INSTANT",
        "is_annualized": annualized,
        "is_annualized_ko": annualized,
    }
    return out


_REGISTRY: dict[str, Callable[..., _Out]] = {
    "SnowballOption": _build_snowball,
}


def build_product(
    family: str, terms: dict[str, Any], *, market: PricingEnvironmentSnapshot | None = None
) -> BuildResult:
    engine_name = _ENGINE_BY_CLASS.get(family, "BlackScholesEngine")
    builder = _REGISTRY.get(family)
    if builder is None:
        return BuildResult(
            ok=False, quantark_class=family, engine_name=engine_name,
            product_kwargs={}, missing=[], warnings=[f"unsupported_family: {family}"],
            validation=None,
        )
    out = builder(terms, quantark_class=family)
    if out.missing:
        return BuildResult(
            ok=False, quantark_class=family, engine_name=engine_name,
            product_kwargs=out.product_kwargs, missing=out.missing,
            warnings=out.warnings, validation=None,
        )
    market = market or _default_market(terms)
    res = validate_quantark_build(family, dict(out.product_kwargs), market, engine_name)
    return BuildResult(
        ok=bool(res.ok), quantark_class=family, engine_name=engine_name,
        product_kwargs=out.product_kwargs, missing=[], warnings=out.warnings,
        validation={"ok": res.ok, "error": res.error},
    )


def _default_market(terms: dict[str, Any]) -> PricingEnvironmentSnapshot:
    defaults = PricingEnvironmentSnapshot()
    spot = _num(terms.get("initial_price")) or _num(terms.get("strike")) or defaults.spot
    return PricingEnvironmentSnapshot(
        spot=spot,
        volatility=defaults.volatility,
        rate=defaults.rate,
        dividend_yield=defaults.dividend_yield,
        asset_name=str(terms.get("underlying") or "UNKNOWN"),
        currency=str(terms.get("currency") or "CNY"),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_product_builders.py -v`
Expected: PASS (4 tests). If `test_snowball_builds_and_validates` fails on
`validation["ok"]`, compare `out.product_kwargs["barrier_config"]` against
`position_adapter._snowball_barrier_config` output and align keys (e.g.
`ko_observation_type`, accrual keys) until `validate_quantark_build` returns ok.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "feat(product-builders): registry core + validated snowball builder"
```

### Task 4: KO-Reset Snowball + Phoenix builders

**Files:**
- Modify: `backend/app/services/domains/product_builders.py`
- Test: `tests/test_product_builders.py`

- [ ] **Step 1: Add failing tests**

```python
# append to tests/test_product_builders.py
def test_ko_reset_snowball_builds():
    terms = _snowball_terms(reset_rate=0.02)
    result = build_product("KnockOutResetSnowballOption", terms)
    assert result.missing == []
    assert result.engine_name == "KOResetSnowballQuadEngine"
    assert result.validation["ok"] is True


def test_phoenix_builds():
    terms = _snowball_terms(coupon_barrier_pct=85, coupon_rate=0.01)
    result = build_product("PhoenixOption", terms)
    assert result.missing == []
    assert result.engine_name == "PhoenixQuadEngine"
    assert result.product_kwargs["coupon_config"]["coupon_rate"] == 0.01
    assert result.validation["ok"] is True


def test_phoenix_missing_coupon_reported():
    terms = _snowball_terms(coupon_barrier_pct=85)  # no coupon_rate
    result = build_product("PhoenixOption", terms)
    assert "coupon_config.coupon_rate" in result.missing
    assert result.ok is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_product_builders.py -k "ko_reset or phoenix" -v`
Expected: FAIL — builders not registered (`unsupported_family`).

- [ ] **Step 3: Implement both builders and register them**

```python
# in product_builders.py, after _build_snowball

def _build_ko_reset_snowball(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_snowball(terms, quantark_class=quantark_class)
    reset_rate = _num(terms.get("reset_rate"))
    if reset_rate is None:
        out.missing.append("reset_rate")
    else:
        out.product_kwargs["reset_rate"] = reset_rate
    return out


def _build_phoenix(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_snowball(terms, quantark_class=quantark_class)
    initial = _num(terms.get("initial_price")) or 100.0
    coupon_barrier = _abs_barrier(terms, "coupon_barrier", "coupon_barrier_pct", initial)
    coupon_rate = _num(terms.get("coupon_rate"))
    if coupon_barrier is None:
        out.missing.append("coupon_config.coupon_barrier")
    if coupon_rate is None:
        out.missing.append("coupon_config.coupon_rate")
    if coupon_barrier is not None and coupon_rate is not None:
        out.product_kwargs["coupon_config"] = {
            "coupon_barrier": coupon_barrier,
            "coupon_rate": coupon_rate,
            "coupon_pay_type": "INSTANT",
            "memory_coupon": bool(terms.get("memory_coupon", False)),
        }
    # Phoenix KO leg typically pays 0 coupon at KO (coupon comes from coupon_config).
    bc = out.product_kwargs.get("barrier_config")
    if isinstance(bc, dict) and "ko_rate" in bc and terms.get("ko_rate") is None:
        bc["ko_rate"] = 0.0
        if "barrier_config.ko_rate" in out.missing:
            out.missing.remove("barrier_config.ko_rate")
    return out


_REGISTRY.update({
    "KnockOutResetSnowballOption": _build_ko_reset_snowball,
    "PhoenixOption": _build_phoenix,
})
```

Note: `_build_phoenix` treats `ko_rate` as optional (defaults 0.0) because Phoenix
pays via `coupon_config`, not the KO leg. Keep `coupon_rate` required.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_product_builders.py -k "ko_reset or phoenix" -v`
Expected: PASS (3 tests). Align kwargs with `position_adapter._phoenix_*` if
`validate_quantark_build` rejects them.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "feat(product-builders): ko-reset snowball and phoenix builders"
```

---

## Phase 2 — Remaining families

### Task 5: Scalar-only families (vanilla, american, digital, barrier, one_touch, futures, spot)

**Files:**
- Modify: `backend/app/services/domains/product_builders.py`
- Test: `tests/test_product_builders.py`

- [ ] **Step 1: Add failing tests**

```python
# append to tests/test_product_builders.py
import pytest


@pytest.mark.parametrize(
    "family,terms,engine",
    [
        ("EuropeanVanillaOption",
         {"strike": 100.0, "option_type": "CALL", "maturity_years": 1.0},
         "BlackScholesEngine"),
        ("AmericanOption",
         {"strike": 100.0, "option_type": "PUT", "maturity_years": 1.0},
         "AmericanOptionAnalyticalEngine"),
        ("CashOrNothingDigitalOption",
         {"strike": 100.0, "cash_payoff": 10.0, "option_type": "CALL", "maturity_years": 1.0},
         "DigitalOptionAnalyticalEngine"),
        ("BarrierOption",
         {"strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
          "barrier": 75.0, "barrier_type": "DOWN_OUT"},
         "BarrierAnalyticalEngine"),
        ("OneTouchOption",
         {"barrier": 120.0, "cash_payoff": 10.0, "touch_type": "UP_TOUCH", "maturity_years": 1.0},
         "OneTouchAnalyticalEngine"),
        ("Futures", {"maturity_years": 1.0}, "DeltaOneEngine"),
        ("SpotInstrument", {}, "DeltaOneEngine"),
    ],
)
def test_scalar_families_build_and_validate(family, terms, engine):
    result = build_product(family, terms)
    assert result.engine_name == engine
    assert result.missing == [], result.missing
    assert result.validation["ok"] is True, result.validation


def test_vanilla_missing_strike_reported():
    result = build_product("EuropeanVanillaOption", {"option_type": "CALL", "maturity_years": 1.0})
    assert "strike" in result.missing
    assert result.ok is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_product_builders.py -k "scalar or vanilla_missing" -v`
Expected: FAIL — families unsupported.

- [ ] **Step 3: Implement scalar builders**

```python
# in product_builders.py, before _REGISTRY definitions are updated

def _maturity(terms: dict, out: _Out) -> float:
    m = _num(terms.get("maturity_years"))
    if m is None and "maturity_years" in _MATURITY_REQUIRED.get("_current", set()):
        pass
    return m if m is not None else 1.0


def _require(terms: dict, out: _Out, key: str, *, alias: str | None = None) -> Any:
    value = terms.get(key)
    if value is None:
        out.missing.append(alias or key)
    return value


def _common_option(terms: dict, out: _Out) -> dict:
    pk: dict[str, Any] = {"contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0}
    m = _num(terms.get("maturity_years"))
    if m is None:
        out.missing.append("maturity_years")
    else:
        pk["maturity"] = m
    return pk


def _build_vanilla(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    pk = _common_option(terms, out)
    strike = _num(_require(terms, out, "strike"))
    if strike is not None:
        pk["strike"] = strike
    pk["option_type"] = str(terms.get("option_type", "CALL")).upper()
    out.product_kwargs = pk
    return out


def _build_digital(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    cash = _num(_require(terms, out, "cash_payoff"))
    if cash is not None:
        out.product_kwargs["cash_payoff"] = cash
    return out


def _build_barrier(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    barrier = _num(_require(terms, out, "barrier"))
    if barrier is not None:
        out.product_kwargs["barrier"] = barrier
    out.product_kwargs["barrier_type"] = str(terms.get("barrier_type", "DOWN_OUT")).upper()
    out.product_kwargs["rebate"] = _num(terms.get("rebate")) or 0.0
    return out


def _build_one_touch(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    pk = _common_option(terms, out)
    barrier = _num(_require(terms, out, "barrier"))
    cash = _num(_require(terms, out, "cash_payoff"))
    if barrier is not None:
        pk["barrier"] = barrier
    if cash is not None:
        pk["cash_payoff"] = cash
    pk["touch_type"] = str(terms.get("touch_type", "UP_TOUCH")).upper()
    out.product_kwargs = pk
    return out


def _build_futures(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    pk: dict[str, Any] = {"contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0}
    m = _num(terms.get("maturity_years"))
    if m is not None:
        pk["maturity"] = m
    fwd = _num(terms.get("forward_price"))
    if fwd is not None:
        pk["forward_price"] = fwd
    out.product_kwargs = pk
    return out


def _build_spot(terms: dict, *, quantark_class: str) -> _Out:
    return _Out(product_kwargs={"contract_multiplier": _num(terms.get("contract_multiplier")) or 1.0})


_REGISTRY.update({
    "EuropeanVanillaOption": _build_vanilla,
    "AmericanOption": _build_vanilla,
    "CashOrNothingDigitalOption": _build_digital,
    "BarrierOption": _build_barrier,
    "OneTouchOption": _build_one_touch,
    "Futures": _build_futures,
    "SpotInstrument": _build_spot,
})
```

Remove the unused `_maturity` / `_MATURITY_REQUIRED` stub if not needed (it was a
false start — delete it; `_common_option` handles maturity).

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_product_builders.py -k "scalar or vanilla_missing" -v`
Expected: PASS (8 tests). Align option_type/barrier_type enum casing with quant-ark
if validation rejects (mirror `COMMON_TEMPLATES` defaults in `services/rfq.py:40`).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "feat(product-builders): scalar option, touch, futures, spot builders"
```

### Task 6: Schedule-bearing families (asian, single/double sharkfin)

**Files:**
- Modify: `backend/app/services/domains/product_builders.py`
- Test: `tests/test_product_builders.py`

- [ ] **Step 1: Add failing tests**

```python
# append to tests/test_product_builders.py
def test_asian_builds_with_synthesized_averaging_schedule():
    terms = {
        "strike": 100.0, "option_type": "CALL", "maturity_years": 1.0,
        "trade_start_date": "2026-01-05", "averaging_frequency": "MONTHLY",
    }
    result = build_product("AsianOption", terms)
    assert result.missing == []
    assert result.product_kwargs["observation_schedule"]["records"]
    assert result.validation["ok"] is True


def test_asian_missing_start_reported():
    result = build_product("AsianOption", {"strike": 100.0, "maturity_years": 1.0,
                                           "averaging_frequency": "MONTHLY"})
    assert "trade_start_date" in result.missing


def test_single_sharkfin_builds():
    terms = {"strike": 100.0, "barrier": 120.0, "option_type": "CALL",
             "maturity_years": 1.0, "participation_rate": 1.0}
    result = build_product("SingleSharkfinOption", terms)
    assert result.missing == []
    assert result.validation["ok"] is True


def test_double_sharkfin_builds():
    terms = {"strike": 100.0, "lower_barrier": 80.0, "upper_barrier": 120.0,
             "maturity_years": 1.0, "participation_rate": 1.0}
    result = build_product("DoubleSharkfinOption", terms)
    assert result.missing == []
    assert result.validation["ok"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_product_builders.py -k "asian or sharkfin" -v`
Expected: FAIL — families unsupported.

- [ ] **Step 3: Implement builders**

```python
# in product_builders.py

def _build_asian(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    start = _start_date(terms)
    maturity = _num(terms.get("maturity_years"))
    if start is None:
        out.missing.append("trade_start_date")
    if start is not None and maturity is not None:
        exercise = schedules.add_months(start, round(maturity * 12))
        freq = str(terms.get("averaging_frequency", "MONTHLY")).upper()
        if freq == "DAILY":
            avg_dates = schedules.daily_ki_dates(start=start, exercise=exercise)
        else:  # MONTHLY averaging
            avg_dates = schedules.monthly_observation_dates(
                start=start, maturity_years=maturity, lockup_months=1
            )
        out.product_kwargs["observation_schedule"] = {
            "records": [{"observation_date": d.isoformat()} for d in avg_dates],
            "aggregation_mode": "AVERAGE",
            "frequency": freq,
        }
    return out


def _build_single_sharkfin(terms: dict, *, quantark_class: str) -> _Out:
    out = _build_vanilla(terms, quantark_class=quantark_class)
    barrier = _num(_require(terms, out, "barrier"))
    if barrier is not None:
        out.product_kwargs["barrier"] = barrier
    out.product_kwargs["participation_rate"] = _num(terms.get("participation_rate")) or 1.0
    return out


def _build_double_sharkfin(terms: dict, *, quantark_class: str) -> _Out:
    out = _Out()
    pk = _common_option(terms, out)
    strike = _num(_require(terms, out, "strike"))
    if strike is not None:
        pk["strike"] = strike
    lower = _num(_require(terms, out, "lower_barrier"))
    upper = _num(_require(terms, out, "upper_barrier"))
    if lower is not None:
        pk["lower_barrier"] = lower
    if upper is not None:
        pk["upper_barrier"] = upper
    pk["participation_rate"] = _num(terms.get("participation_rate")) or 1.0
    out.product_kwargs = pk
    return out


_REGISTRY.update({
    "AsianOption": _build_asian,
    "SingleSharkfinOption": _build_single_sharkfin,
    "DoubleSharkfinOption": _build_double_sharkfin,
})
```

If quant-ark's Asian `observation_schedule`/`aggregation_mode` enum differs,
align with `services/quantark._build_observation_schedule` expectations until
validation passes.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_product_builders.py -k "asian or sharkfin" -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Run the whole builder suite + commit**

Run: `python -m pytest tests/test_product_builders.py tests/test_schedules.py -v`
Expected: PASS (all).

```bash
git add backend/app/services/domains/product_builders.py tests/test_product_builders.py
git commit -m "feat(product-builders): asian and sharkfin builders"
```

---

## Phase 3 — `build_product` tool

### Task 7: `build_product` @tool wrapper + registration

**Files:**
- Create: `backend/app/tools/products.py`
- Modify: `backend/app/tools/__init__.py`
- Test: `tests/test_tools_products.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tools_products.py
from __future__ import annotations

from app.tools.products import build_product_tool


def test_build_product_tool_returns_validated_snowball():
    result = build_product_tool.invoke({
        "family": "SnowballOption",
        "terms": {
            "underlying": "000905.SH", "currency": "CNY", "initial_price": 100.0,
            "maturity_years": 1.0, "ko_barrier_pct": 101, "ki_barrier_pct": 70,
            "ko_rate": 0.15, "ko_frequency": "MONTHLY", "ki_convention": "DAILY",
            "lockup_months": 3, "trade_start_date": "2026-01-05",
        },
    })
    assert result["ok"] is True
    assert result["engine_name"] == "SnowballQuadEngine"
    assert result["missing"] == []
    assert result["product_kwargs"]["barrier_config"]["ko_barrier"] == 101.0


def test_build_product_tool_reports_missing():
    result = build_product_tool.invoke({
        "family": "SnowballOption",
        "terms": {"underlying": "000905.SH", "maturity_years": 1.0,
                  "ko_barrier_pct": 101, "ki_barrier_pct": 70},
    })
    assert result["ok"] is False
    assert "trade_start_date" in result["missing"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_tools_products.py -v`
Expected: FAIL — `ModuleNotFoundError: app.tools.products`.

- [ ] **Step 3: Implement the tool**

```python
# backend/app/tools/products.py
"""@tool wrapper for deterministic product construction."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains.product_builders import build_product


class BuildProductInput(BaseModel):
    family: str = Field(description="QuantArk product class, e.g. 'SnowballOption'.")
    terms: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured per-family terms (levels, frequencies, tenor, dates).",
    )


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("build_product", args_schema=BuildProductInput)
def build_product_tool(family: str, terms: dict[str, Any]) -> dict[str, Any]:
    """Construct a quant-ark-validated product from structured terms.

    Synthesizes observation schedules where required. Reports any economics it
    will not invent (lockup, trade start, barrier levels, coupon) in `missing`
    instead of guessing. Does NOT persist anything.
    """
    result = build_product(family, dict(terms or {}))
    return asdict(result)
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_tools_products.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Register the tool in the agent tool list**

In `backend/app/tools/__init__.py`:
- Add import near the other `from .` imports:
  ```python
  from .products import build_product_tool
  ```
- Add to `QUANT_AGENT_TOOLS`, right after `get_rfq_catalog_tool,` (it is a pure DOMAIN_READ build helper, grouped with the other read tools):
  ```python
      build_product_tool,
  ```

- [ ] **Step 6: Verify the registry import is clean**

Run: `python -c "from app.tools import QUANT_AGENT_TOOLS; print(any(t.name=='build_product' for t in QUANT_AGENT_TOOLS))"`
Expected: `True`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/tools/products.py backend/app/tools/__init__.py tests/test_tools_products.py
git commit -m "feat(tools): add build_product tool to the agent catalog"
```

---

## Phase 4 — Skills

### Task 8: `build-product` anchor skill + `build-contract` reference

**Files:**
- Create: `backend/app/skills/workflows/products/build-product/SKILL.md`
- Create: `backend/app/skills/references/products/build-contract.md`
- Modify: `backend/app/services/deep_agent/personas.py`

- [ ] **Step 1: Write the reference doc**

Create `backend/app/skills/references/products/build-contract.md`:

```markdown
---
name: build-contract
description: Per-family structured term schema for build_product. Lists required vs synthesizable terms and the recommended engine per quant-ark product family.
reference_type: product
---

## How to use

The `build_product(family, terms)` tool constructs a quant-ark-validated
product from structured `terms`. Extract terms from the user's request; do NOT
invent economics. If `build_product` returns `missing`, ask the user for exactly
those fields in one message, then call again.

Barrier levels accept either an absolute key (`ko_barrier`) or a percent-of-
initial key (`ko_barrier_pct`, e.g. `101` for 101%). Initial price defaults 100.

## Autocallables (require schedule synthesis)

`SnowballOption` (engine `SnowballQuadEngine`),
`KnockOutResetSnowballOption` (`KOResetSnowballQuadEngine`),
`PhoenixOption` (`PhoenixQuadEngine`).

Required: `maturity_years`, `ko_barrier`/`ko_barrier_pct`, `ki_barrier`/
`ki_barrier_pct` (unless `ki_convention: NONE`), `ko_rate` (coupon; Phoenix uses
`coupon_rate` + `coupon_barrier` instead and `ko_rate` defaults 0),
`lockup_months`, `trade_start_date`.
Synthesized: monthly KO schedule (from start+lockup on the SSE calendar), daily
or European KI schedule. Optional: `initial_price` (default 100), `strike`
(default = initial), `ki_convention` (DAILY|EUROPEAN|NONE), `ko_rate_annualized`.
KO-Reset adds required `reset_rate`. Phoenix adds required `coupon_rate`,
`coupon_barrier`/`coupon_barrier_pct`.

## Scalar families

`EuropeanVanillaOption`/`AmericanOption`: `strike`, `option_type`,
`maturity_years`.
`CashOrNothingDigitalOption`: + `cash_payoff`.
`BarrierOption`: + `barrier`, `barrier_type` (default DOWN_OUT), `rebate`.
`OneTouchOption`: `barrier`, `cash_payoff`, `touch_type`, `maturity_years`.
`Futures`: `maturity_years`, optional `forward_price`. `SpotInstrument`: none.

## Schedule families

`AsianOption`: `strike`, `maturity_years`, `trade_start_date`,
`averaging_frequency` (MONTHLY|DAILY) → synthesized averaging schedule.
`SingleSharkfinOption`: `strike`, `barrier`, `participation_rate`,
`maturity_years`. `DoubleSharkfinOption`: `strike`, `lower_barrier`,
`upper_barrier`, `participation_rate`, `maturity_years`.
```

- [ ] **Step 2: Write the anchor skill**

Create `backend/app/skills/workflows/products/build-product/SKILL.md`:

```markdown
---
name: build-product
description: Construct a quant-ark-validated product from natural-language terms before booking or quoting. Use when a user states product economics (family, underlying, tenor, barrier levels, observation frequencies) that must become a concrete product, when book-position or draft-rfq needs validated product_kwargs, or when an RFQ/booking flow needs schedule-bearing terms the engine will accept.
domain: products
workflow_type: action
allowed_envelopes:
  - desk_workflow
may_escalate_to:
  - desk_workflow
required_context:
  - request_text
optional_context:
  - product_family
  - trade_effective_date
write_actions: false
confirmation_required: false
success_criteria:
  - validated product_kwargs and recommended engine are returned
  - missing economics are reported without being invented
---

## When to use

- A user states product economics that must become a concrete, priceable product.
- `book-position` or `draft-rfq` needs validated `product_kwargs` and an engine.
- An RFQ or booking flow needs schedule-bearing terms the quant-ark engine accepts.

## Required inputs

Use the user's request text plus any explicit family, underlying, tenor, barrier
levels, observation frequencies, and dates. Read
`/skills/references/products/build-contract.md` for the per-family term schema.

## Procedure

1. Identify the quant-ark family. Call `get_rfq_catalog` if the family is unclear.
2. Extract the structured `terms` for that family per `build-contract.md`. Do not
   invent economics.
3. Call `build_product(family=<class>, terms=<extracted>)`.
4. If `missing` is non-empty, ask for exactly those fields in one message, then
   call `build_product` again.
5. On `ok`, hand the returned `product_kwargs` and `engine_name` to the booking
   or RFQ step.

## Stop conditions

Do not guess lockup, trade start, barrier levels, or coupon. Report them as
missing and ask. Do not book or persist from this skill.

## Output shape

Return built-or-blocked first, then family, engine, the validated product_kwargs
summary, and any missing terms.

## References

- `/skills/references/products/build-contract.md`

## Example

User: Build a 1Y CSI 500 Snowball, KO 101% monthly, KI 70% daily.
Assistant: Extract SnowballOption terms, call build_product; if it reports
trade_start_date / lockup_months / ko_rate missing, ask for those, then return
the validated product and engine.
```

- [ ] **Step 3: Add the products group to the trader catalog**

In `backend/app/services/deep_agent/personas.py`, in `trader_spec`, add
`"/skills/workflows/products/",` to the `skills=[...]` list (after
`"/skills/workflows/positions/",`).

- [ ] **Step 4: Verify the skill lints clean and is discoverable**

Run:
```bash
python -c "
from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT
p = SKILLS_ROOT / 'workflows/products/build-product/SKILL.md'
ws = lint_skill_file(p, mode='ci', root=SKILLS_ROOT)
print('errors:', [w for w in ws if w.severity=='error'])
print('name:', parse_skill_file(p).frontmatter['name'])
"
```
Expected: `errors: []` and `name: build-product`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/skills/workflows/products backend/app/skills/references/products/build-contract.md backend/app/services/deep_agent/personas.py
git commit -m "feat(skills): add build-product anchor, build-contract reference, trader catalog"
```

### Task 9: Update `book-position` and `draft-rfq` skills to use `build_product`

**Files:**
- Modify: `backend/app/skills/workflows/positions/book-position/SKILL.md`
- Modify: `backend/app/skills/workflows/rfq/draft-rfq/SKILL.md`

- [ ] **Step 1: Update `book-position` Procedure + engine mapping**

Replace the `## Procedure` section of `book-position/SKILL.md` with:

```markdown
## Procedure

1. If the product terms are natural-language, first run `build-product` to get
   validated `product_kwargs` and the recommended `engine_name`. Autocallables
   must use their quad engine (SnowballOption → `SnowballQuadEngine`,
   KnockOutResetSnowballOption → `KOResetSnowballQuadEngine`, PhoenixOption →
   `PhoenixQuadEngine`); never book an autocallable with `BlackScholesEngine`.
2. Validate the product family is supported and required terms are present.
3. Compose a confirmation summary with portfolio, product, quantity, entry price,
   and engine.
4. After confirmation, call `book_position(portfolio_id=<id>, product=<spec>,
   quantity=<qty>, entry_price=<optional>, engine_name=<recommended>)`.
5. Return the booked position id, product id, and product summary.
```

- [ ] **Step 2: Update `draft-rfq` Procedure**

Replace step 1 of the `## Procedure` section in `draft-rfq/SKILL.md`:

```markdown
1. Call `build_product(family=<class>, terms=<extracted>)` to obtain validated
   product_kwargs (use `build-product` for term extraction guidance). If it
   reports missing terms, ask for them before continuing.
2. Call `validate_rfq_terms(terms=<draft terms>)`.
3. If hard validation errors exist, stop and return them.
4. Call `create_or_update_rfq_draft(draft=<validated terms>, rfq_id=<optional>)`.
```

- [ ] **Step 3: Verify both skills still lint clean**

Run:
```bash
python -c "
from app.services.deep_agent.skill_lint import lint_skill_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT
for rel in ['workflows/positions/book-position/SKILL.md','workflows/rfq/draft-rfq/SKILL.md']:
    ws = lint_skill_file(SKILLS_ROOT/rel, mode='ci', root=SKILLS_ROOT)
    print(rel, [w for w in ws if w.severity=='error'])
"
```
Expected: `[]` for both.

- [ ] **Step 4: Commit**

```bash
git add backend/app/skills/workflows/positions/book-position/SKILL.md backend/app/skills/workflows/rfq/draft-rfq/SKILL.md
git commit -m "docs(skills): route book-position and draft-rfq through build_product"
```

---

## Phase 5 — Routing + quote-first (Issue #1)

### Task 10: Orchestrator booking-intent route + quote-first contract; trader tool swap

**Files:**
- Modify: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Modify: `backend/app/services/deep_agent/prompts/trader.md`
- Test: `tests/test_routing_contracts_phase3.py`

- [ ] **Step 1: Write the failing routing-contract test**

```python
# append to tests/test_routing_contracts_phase3.py
from pathlib import Path

import app.services.deep_agent.orchestrator as orch


def _orchestrator_prompt() -> str:
    return (Path(orch.__file__).parent / "prompts" / "orchestrator.md").read_text(encoding="utf-8")


def test_orchestrator_has_direct_booking_quote_first_contract() -> None:
    prompt = _orchestrator_prompt()
    assert "book-position" in prompt
    assert "build-product" in prompt
    # quote-first clarification language is present
    assert "quote" in prompt.lower()
    booking_idx = prompt.find("Book a product")
    assert booking_idx != -1, "missing direct-booking routing row"
    # The direct-booking row routes to the trader, not high_board
    assert "trader" in prompt[booking_idx:booking_idx + 200]


def test_trader_prompt_uses_build_product_not_regex_drafter() -> None:
    from pathlib import Path
    import app.services.deep_agent.orchestrator as orch
    trader = (Path(orch.__file__).parent / "prompts" / "trader.md").read_text(encoding="utf-8")
    assert "build_product" in trader
    assert "draft_rfq_from_natural_language" not in trader
```

(If `_orchestrator_prompt` already exists in this test module, reuse it instead
of redefining.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_routing_contracts_phase3.py -k "direct_booking or build_product" -v`
Expected: FAIL — strings absent.

- [ ] **Step 3: Edit `orchestrator.md`**

In the `## Routing (after clarification is clean)` list, add a bullet after the
RFQ/trader line:

```markdown
- Direct booking of a NEW product from stated terms ("book a snowball …", "book me a …", "book this trade") → **booking intent**: run the Quote-first booking rule below, then route to `trader`. This is NOT the same as booking an already-approved RFQ ("book RFQ-42", "book this RFQ") which stays on the RFQ lifecycle → `high_board`.
```

Add a new subsection after the `## Clarification protocol` section:

```markdown
## Quote-first booking rule

When the user expresses a direct booking intent for a new product from terms,
do NOT delegate immediately. First ask ONE defaulted question:

  > "Do you want me to price/quote this first, or book it at the stated terms? (quote / book as-is)"

- If the user wants a quote first → delegate to `trader` with `draft-rfq` (then
  `quote-rfq`), naming `build-product` for term construction.
- If the user wants to book as-is → delegate to `trader` with `book-position`,
  which uses `build-product` to construct validated terms before the HITL
  `book_position` confirmation.

Skip the question only if the user already stated the choice ("just book it",
"quote it first") in the same request.
```

Add two rows to the `### Known single-persona skills` table:

```markdown
| Book a product directly into a portfolio from terms     | trader        | book-position                    |
| Construct/validate a quant-ark product from terms        | trader        | build-product                    |
```

- [ ] **Step 4: Edit `trader.md`**

In the `## Tools you use` list, replace the line:
`- `draft_rfq_from_natural_language` — extract RFQ terms and missing fields from client text.`
with:
`- `build_product` — construct a quant-ark-validated product (with synthesized observation schedules) from structured terms; reports missing economics instead of inventing them.`

In `## Routing from skills`, add:
`For booking or RFQ construction, read `/skills/workflows/products/build-product/SKILL.md` and `/skills/references/products/build-contract.md` before calling `build_product`.`

- [ ] **Step 5: Run routing tests to verify pass**

Run: `python -m pytest tests/test_routing_contracts_phase3.py -v`
Expected: PASS (including the prior tests in the file).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/prompts/orchestrator.md backend/app/services/deep_agent/prompts/trader.md tests/test_routing_contracts_phase3.py
git commit -m "feat(routing): direct-booking intent with quote-first clarification"
```

---

## Phase 6 — Migration + catalog test updates

### Task 11: Update skill-catalog tests for the new `products` group

**Files:**
- Modify: `tests/test_skills_catalog_v2.py`

- [ ] **Step 1: Run the catalog tests to see the failures**

Run: `python -m pytest tests/test_skills_catalog_v2.py -v`
Expected: FAIL — `test_persona_sources_are_workflow_only`,
`test_all_workflow_domains_have_expected_skills`, and
`test_trader_total_workflow_catalog` now mismatch.

- [ ] **Step 2: Update the trader source list**

In `test_persona_sources_are_workflow_only`, add `"/skills/workflows/products/",`
to the trader expected-sources list (the block at lines ~42-49 in the current
file). Leave the risk_manager and high_board lists unchanged.

- [ ] **Step 3: Update the per-domain expected-skills dict**

In `test_all_workflow_domains_have_expected_skills`, add an entry:
```python
        "/workflows/products/": {"build-product"},
```

- [ ] **Step 4: Update the trader total count**

In `test_trader_total_workflow_catalog`, change `== 19` to `== 20` (both the
assertion and the message string).

- [ ] **Step 5: Run to verify pass**

Run: `python -m pytest tests/test_skills_catalog_v2.py tests/test_skills_catalog.py tests/test_workflow_skills_phase3.py -v`
Expected: PASS. (`/skills/products/`-empty assertions remain valid: the new
group is `/skills/workflows/products/`; the contract doc is under
`/skills/references/products/`.)

- [ ] **Step 6: Commit**

```bash
git add tests/test_skills_catalog_v2.py
git commit -m "test(skills-catalog): account for products workflow group"
```

### Task 12: Remove dead `parse_natural_language_rfq`

**Files:**
- Modify: `backend/app/services/quantark.py`
- Modify: `tests/test_quant_services.py`

- [ ] **Step 1: Confirm it is unused outside its own module + test**

Run: `grep -rn "parse_natural_language_rfq" backend tests --include="*.py" | grep -v __pycache__`
Expected: only `services/quantark.py` (definition) and `tests/test_quant_services.py`.

- [ ] **Step 2: Delete the function and its test**

- Remove `def parse_natural_language_rfq(...)` (currently `services/quantark.py:1321-1400`).
- Remove the corresponding test in `tests/test_quant_services.py` (search for
  `parse_natural_language_rfq`).

- [ ] **Step 3: Run to verify nothing else breaks**

Run: `python -m pytest tests/test_quant_services.py -v`
Expected: PASS (the removed test no longer collected).

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/quantark.py tests/test_quant_services.py
git commit -m "chore(quantark): remove dead parse_natural_language_rfq"
```

### Task 13: Migrate / retire `draft_rfq_from_natural_language`

**Files:**
- Modify: `backend/app/main.py`, `backend/app/services/agents.py`, `backend/app/tools/rfq.py`, `backend/app/tools/__init__.py`, `backend/app/services/rfq.py`, `backend/app/services/domains/rfq.py`
- Modify: `tests/test_tools_rfq.py`, `tests/test_services_domains_rfq.py`

- [ ] **Step 1: Map every caller**

Run: `grep -rn "draft_rfq_from_natural_language\|draft_from_natural_language" backend tests --include="*.py" | grep -v __pycache__`
Record each call site (API route in `main.py`, `agents.py`, tool wrapper, service, domain facade, tests).

- [ ] **Step 2: Decide per call site (decision rule)**

- **Agent tool surface** (`tools/rfq.py`, `tools/__init__.py`, `trader.md`):
  remove `draft_rfq_from_natural_language_tool` from `QUANT_AGENT_TOOLS` and its
  import; the agent now uses `build_product`. (Already removed from `trader.md`
  in Task 10.)
- **API route in `main.py`:** if a frontend consumes it, keep the HTTP endpoint
  but re-point its body to call `build_product` + `validate_rfq_terms`; otherwise
  delete the route. Verify the frontend:
  Run: `grep -rn "draft_rfq_from_natural_language\|draft-rfq-from" frontend/src --include="*.ts" --include="*.tsx"`
  - If no frontend hit → delete the route and its handler.
  - If a frontend hit exists → keep the route, re-point internals, leave the
    response shape unchanged.
- **`services/rfq.py` / `services/domains/rfq.py`:** keep
  `draft_from_natural_language` only if the API route still needs it; otherwise
  remove it and the now-unused regex helpers (`_template_for_text`,
  `_unknown_for_text`, `_number_after`, `_tenor_years`, `_underlying_from_text`,
  `_quantity_from_text`, `_bounds_for_unknown`) and the `RFQDraftFromNLOut`
  plumbing if unreferenced.

- [ ] **Step 3: Update the affected tests**

- `tests/test_tools_rfq.py`: remove `test_draft_rfq_from_natural_language_tool`
  and the tool import.
- `tests/test_services_domains_rfq.py`: remove or repoint the
  `draft_from_natural_language` test per the Step-2 decision.

- [ ] **Step 4: Run the full RFQ + tools + agents suites**

Run: `python -m pytest tests/test_tools_rfq.py tests/test_services_domains_rfq.py tests/ -k "rfq or agents" -v`
Expected: PASS. Fix any import errors from the removals.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(rfq): retire regex draft_rfq_from_natural_language in favor of build_product"
```

### Task 14: Full-suite regression + end-to-end booking check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest tests/ -q`
Expected: PASS (no regressions). Investigate and fix any failure before
proceeding — do not leave the suite red.

- [ ] **Step 2: End-to-end booking smoke (the original bug)**

Run:
```bash
python -c "
from app.services.domains.product_builders import build_product
terms = {'underlying':'000905.SH','currency':'CNY','initial_price':100.0,
 'maturity_years':1.0,'ko_barrier_pct':101,'ki_barrier_pct':70,'ko_rate':0.15,
 'ko_frequency':'MONTHLY','ki_convention':'DAILY','lockup_months':3,
 'trade_start_date':'2026-01-05'}
r = build_product('SnowballOption', terms)
print('ok', r.ok, 'engine', r.engine_name, 'validation', r.validation)
assert r.ok and r.validation['ok']
print('KO obs:', len(r.product_kwargs['barrier_config']['ko_observation_schedule']['records']))
print('KI obs:', len(r.product_kwargs['barrier_config']['ki_observation_schedule']['records']))
"
```
Expected: `ok True engine SnowballQuadEngine validation {'ok': True, ...}` with
10 KO observations and a populated daily KI schedule — i.e. the exact request
that previously failed now builds.

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A && git commit -m "test: full-suite regression for booking + product builders" || echo "nothing to commit"
```

---

## Self-Review

**Spec coverage:**
- Issue #1 routing + quote-first → Task 10. ✓
- `build_product` tool + registry, all families → Tasks 3–7. ✓
- Shared `schedules.py` extracted from `position_adapter` → Tasks 1–2. ✓
- `build-product` skill + `build-contract` reference + book-position/draft-rfq updates → Tasks 8–9. ✓
- Missing-term policy (lockup/start/levels/coupon not invented) → Tasks 3, 4, 6 tests. ✓
- Engine mapping (fix BlackScholes default for snowball) → `_ENGINE_BY_CLASS` (Task 3) + book-position skill (Task 9). ✓
- Retire regex drafter + dead parser → Tasks 12–13. ✓
- Skill-catalog test coupling → Task 11. ✓

**Placeholder scan:** No "TBD"/"handle edge cases"/"similar to Task N". Migration
Task 13 uses an explicit decision rule with grep-verified branches rather than a
placeholder (the frontend-consumer fact must be discovered at execution time).

**Type consistency:** `BuildResult(ok, quantark_class, engine_name, product_kwargs,
missing, warnings, validation)` is defined once (Task 3) and consumed identically
by the tool (`asdict`, Task 7) and tests. `build_product(family, terms, *, market)`
signature is stable across tasks. `_Out`, `_REGISTRY`, `_ENGINE_BY_CLASS`,
`schedules.*` names match across Tasks 1, 3–7.

**Known execution-time iteration point:** builder kwargs are validated against the
real quant-ark engine; if exact key names differ, mirror the proven
`position_adapter` output (called out in each builder task's Step 4).
```
