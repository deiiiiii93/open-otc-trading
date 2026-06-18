# Profile-consistent Greeks for Listed-Option Hedge Legs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Price listed-option hedge legs under the same `rate`/`dividend_yield`/`volatility` the underlying risk run used (its pricing parameter profile), and refuse — never silently default — when those params are unavailable.

**Architecture:** A new pure resolver collapses a profile's per-underlying rows to one `(rate, div, vol)`. `hedging_greeks.aggregate_by_underlying` reads the run's profile and attaches a complete `market` per underlying. `solve_hedge` forwards that market into `hedging_legs.price`, which prices option legs under it (futures/spot legs unchanged) or marks them unpriceable. Change is confined to the hedging module; the risk subsystem is untouched.

**Tech Stack:** Python, SQLAlchemy, FastAPI, pytest. Option Greeks via QuantArk `GreeksCalculator` (analytic Black-Scholes) through `risk_engine.compute_position_greeks`.

**Spec:** `docs/superpowers/specs/2026-06-03-hedge-leg-greeks-profile-consistency-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `backend/app/services/pricing_profiles.py` | Profile resolution helpers | **Add** `UnderlyingMarketParams` + `resolve_underlying_market_params` |
| `backend/app/services/hedging_greeks.py` | Per-underlying target aggregation from latest RiskRun | **Modify** `aggregate_by_underlying` to attach `market`/`params_ok`/`missing_params` + top-level `pricing_parameter_profile_id` |
| `backend/app/services/hedging_legs.py` | Per-contract leg Greeks | **Modify** `price()` signature + `_option_unit_greeks()` to consume a passed market |
| `backend/app/services/domains/hedging_strategy.py` | `solve_hedge` orchestration | **Modify** to build & forward `option_market` |
| `tests/test_pricing_profiles_resolution.py` | Pure resolver unit tests | **Create** |
| `tests/test_hedging_greeks.py` | Aggregation tests | **Modify** (add cases + `_profile`/`_run` helper) |
| `tests/test_hedging_legs.py` | `price()` tests | **Modify** (add option-leg cases) |
| `tests/test_hedging_solve_orchestration.py` | `solve_hedge` integration | **Modify** (profile the BS test + add refusal test) |

---

## Task 1: Pure resolver — `resolve_underlying_market_params`

**Files:**
- Modify: `backend/app/services/pricing_profiles.py` (add near `resolve_pricing_parameter_row_for_position`, ~line 105)
- Test: `tests/test_pricing_profiles_resolution.py` (create)

Reuses the existing module-level constant `MANUAL_INPUT_FIELDS = ("rate", "dividend_yield", "volatility")` (line 88) and helper `_normalize_pricing_match_key` (line 206). `dataclass` and `PricingParameterRow` are already imported.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pricing_profiles_resolution.py`:

```python
# tests/test_pricing_profiles_resolution.py
from app.models import PricingParameterRow
from app.services.pricing_profiles import (
    UnderlyingMarketParams,
    resolve_underlying_market_params,
)


def _row(symbol, **kw):
    # Transient (unpersisted) row; the resolver only reads attributes.
    return PricingParameterRow(symbol=symbol, **kw)


def test_resolve_agrees_when_rows_identical():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.01, volatility=0.2)
            for _ in range(3)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert res.ok
    assert (res.rate, res.dividend_yield, res.volatility) == (0.03, 0.01, 0.2)


def test_resolve_missing_field_is_listed():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.01, volatility=None)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert not res.ok
    assert res.missing_fields == ("volatility",)
    assert res.volatility is None


def test_resolve_conflicting_field_is_ambiguous():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2),
            _row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.35)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert not res.ok
    assert res.ambiguous_fields == ("volatility",)


def test_resolve_ignores_other_symbols():
    rows = [_row("000300.SH", rate=0.9, dividend_yield=0.9, volatility=0.9),
            _row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert res.ok and res.rate == 0.03


def test_resolve_absorbs_float_noise():
    rows = [_row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2),
            _row("000905.SH", rate=0.03, dividend_yield=0.0, volatility=0.2 + 1e-15)]
    res = resolve_underlying_market_params(rows, "000905.SH")
    assert res.ok and res.volatility is not None


def test_resolve_no_matching_rows_marks_all_missing():
    res = resolve_underlying_market_params([], "000905.SH")
    assert not res.ok
    assert set(res.missing_fields) == {"rate", "dividend_yield", "volatility"}
    assert isinstance(res, UnderlyingMarketParams)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_pricing_profiles_resolution.py -q`
Expected: FAIL — `ImportError: cannot import name 'resolve_underlying_market_params'`.

- [ ] **Step 3: Implement the resolver**

In `backend/app/services/pricing_profiles.py`, immediately above `def resolve_pricing_parameter_row_for_position(` (line ~105), insert:

```python
@dataclass(frozen=True)
class UnderlyingMarketParams:
    """Single per-underlying (rate, dividend_yield, volatility) collapsed from a
    profile's rows. ``ok`` only when nothing is missing or ambiguous."""

    rate: float | None
    dividend_yield: float | None
    volatility: float | None
    missing_fields: tuple[str, ...] = ()
    ambiguous_fields: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_fields and not self.ambiguous_fields


def resolve_underlying_market_params(
    rows: list[PricingParameterRow], symbol: str
) -> UnderlyingMarketParams:
    """Collapse a profile's rows for ``symbol`` to one (rate, div, vol), field-wise.

    For each field, the distinct non-null values across the underlying's rows are
    quantized (1e-12) to absorb float noise: exactly one -> use it; none -> missing;
    more than one -> ambiguous. Both missing and ambiguous make ``ok`` False so the
    caller refuses rather than guessing.
    """
    key = _normalize_pricing_match_key(symbol)
    matched = [row for row in rows if _normalize_pricing_match_key(row.symbol) == key]
    values: dict[str, float | None] = {}
    missing: list[str] = []
    ambiguous: list[str] = []
    for field in MANUAL_INPUT_FIELDS:
        distinct = {
            round(float(getattr(row, field)), 12)
            for row in matched
            if getattr(row, field) is not None
        }
        if len(distinct) == 1:
            values[field] = distinct.pop()
        elif not distinct:
            values[field] = None
            missing.append(field)
        else:
            values[field] = None
            ambiguous.append(field)
    return UnderlyingMarketParams(
        rate=values["rate"],
        dividend_yield=values["dividend_yield"],
        volatility=values["volatility"],
        missing_fields=tuple(missing),
        ambiguous_fields=tuple(ambiguous),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_pricing_profiles_resolution.py -q`
Expected: PASS (6 passed).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/pricing_profiles.py tests/test_pricing_profiles_resolution.py
git commit -m "feat(hedging): per-underlying market-param resolver from pricing profile

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Aggregation attaches the per-underlying market

**Files:**
- Modify: `backend/app/services/hedging_greeks.py:1-47` (imports + `aggregate_by_underlying`)
- Test: `tests/test_hedging_greeks.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_hedging_greeks.py`, replace the import block and `_run` helper (lines 1-14) with:

```python
# tests/test_hedging_greeks.py
from datetime import datetime, timedelta

from app.models import (Portfolio, PricingParameterProfile, PricingParameterRow,
                        RiskRun)
from app.services import hedging_greeks


def _profile(session, symbol, *, rate=None, dividend_yield=None, volatility=None,
             rows=2):
    p = PricingParameterProfile(name="prof", valuation_date=datetime.utcnow(),
                                source_type="default_underlying", status="completed",
                                summary={})
    session.add(p); session.flush()
    for i in range(rows):
        session.add(PricingParameterRow(
            profile_id=p.id, source_trade_id=f"t{i}", symbol=symbol, spot=5600.0,
            rate=rate, dividend_yield=dividend_yield, volatility=volatility))
    session.flush()
    return p


def _run(session, pf_id, positions, created_at=None, profile_id=None):
    run = RiskRun(portfolio_id=pf_id, status="completed",
                  metrics={"positions": positions},
                  pricing_parameter_profile_id=profile_id)
    if created_at is not None:
        run.created_at = created_at
    session.add(run); session.flush()
    return run
```

Then append these tests to the end of the file:

```python
def test_aggregate_attaches_profile_market(session):
    pf = Portfolio(name="pf_prof", base_currency="CNY"); session.add(pf); session.flush()
    p = _profile(session, "000905.SH", rate=0.025, dividend_yield=0.01, volatility=0.22)
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}], profile_id=p.id)
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["pricing_parameter_profile_id"] == p.id
    u = {x["underlying"]: x for x in out["underlyings"]}["000905.SH"]
    assert u["params_ok"] is True
    assert u["market"] == {"spot": 5600.0, "rate": 0.025,
                           "dividend_yield": 0.01, "volatility": 0.22}


def test_aggregate_profileless_run_marks_params_missing(session):
    pf = Portfolio(name="pf_noprof", base_currency="CNY"); session.add(pf); session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}])
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    assert out["pricing_parameter_profile_id"] is None
    u = {x["underlying"]: x for x in out["underlyings"]}["000905.SH"]
    assert u["params_ok"] is False
    assert u["market"] is None
    assert u["missing_params"]  # non-empty reason


def test_aggregate_conflicting_vol_refuses(session):
    pf = Portfolio(name="pf_amb", base_currency="CNY"); session.add(pf); session.flush()
    p = PricingParameterProfile(name="amb", valuation_date=datetime.utcnow(),
                                source_type="xlsx", status="completed", summary={})
    session.add(p); session.flush()
    session.add(PricingParameterRow(profile_id=p.id, source_trade_id="a",
        symbol="000905.SH", spot=5600.0, rate=0.03, dividend_yield=0.0, volatility=0.20))
    session.add(PricingParameterRow(profile_id=p.id, source_trade_id="b",
        symbol="000905.SH", spot=5600.0, rate=0.03, dividend_yield=0.0, volatility=0.35))
    session.flush()
    _run(session, pf.id, [
        {"underlying": "000905.SH", "delta_cash": 5.0, "gamma_cash": 1.0, "vega": 1.0,
         "spot": 5600.0, "greeks_ok": True}], profile_id=p.id)
    out = hedging_greeks.aggregate_by_underlying(session, portfolio_id=pf.id)
    u = {x["underlying"]: x for x in out["underlyings"]}["000905.SH"]
    assert u["params_ok"] is False
    assert any("volatility" in m for m in u["missing_params"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hedging_greeks.py -q`
Expected: FAIL — new tests `KeyError: 'pricing_parameter_profile_id'` / missing `params_ok`. (The pre-existing tests still pass.)

- [ ] **Step 3: Implement the aggregation enrichment**

In `backend/app/services/hedging_greeks.py`, update the imports (lines 9-10) from:

```python
from ..models import RiskRun
from .domains import risk as risk_svc
```

to:

```python
from ..models import PricingParameterRow, RiskRun
from .domains import risk as risk_svc
from .pricing_profiles import resolve_underlying_market_params
```

Then replace the body from `rows = (run.metrics or {}).get("positions", [])` (line 26) through the `return {...}` (lines 45-47) with:

```python
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

    profile_id = run.pricing_parameter_profile_id
    profile_rows = (
        session.query(PricingParameterRow)
        .filter(PricingParameterRow.profile_id == profile_id)
        .all()
        if profile_id is not None
        else []
    )

    underlyings = []
    for u, v in sorted(acc.items()):
        market, params_ok, missing_params = _resolve_market(
            u, v["spot"], profile_id, profile_rows)
        underlyings.append({
            "underlying": u,
            "targets": {"delta": v["delta"], "gamma": v["gamma"], "vega": v["vega"]},
            "spot": v["spot"],
            "market": market,
            "params_ok": params_ok,
            "missing_params": missing_params,
        })
    stale = bool(run.created_at and run.created_at.date() < datetime.utcnow().date())
    return {"status": "ok", "portfolio_id": portfolio_id, "risk_run_id": run.id,
            "pricing_parameter_profile_id": profile_id,
            "created_at": run.created_at.isoformat(), "stale": stale,
            "underlyings": underlyings}


def _resolve_market(
    underlying: str, spot: Any, profile_id: int | None,
    profile_rows: list[PricingParameterRow],
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    """Per-underlying option-pricing market from the run's profile, or a refusal.

    Returns (market | None, params_ok, missing_params). ``market`` carries the full
    spot+r+q+vol only when the profile supplies an unambiguous (rate, div, vol);
    otherwise the option legs for this underlying will refuse to price.
    """
    if profile_id is None:
        return None, False, ["risk run not priced under a pricing parameter profile"]
    params = resolve_underlying_market_params(profile_rows, underlying)
    if not params.ok:
        reasons = [f"{f} (missing)" for f in params.missing_fields]
        reasons += [f"{f} (ambiguous)" for f in params.ambiguous_fields]
        return None, False, reasons
    market = {
        "spot": float(spot) if spot is not None else None,
        "rate": params.rate,
        "dividend_yield": params.dividend_yield,
        "volatility": params.volatility,
    }
    return market, True, []
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hedging_greeks.py -q`
Expected: PASS (all, including the pre-existing 5 cases).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_greeks.py tests/test_hedging_greeks.py
git commit -m "feat(hedging): attach profile market + params status per underlying

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `price()` consumes the passed market for option legs

**Files:**
- Modify: `backend/app/services/hedging_legs.py:83-126` (`price` + `_option_unit_greeks`)
- Test: `tests/test_hedging_legs.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_hedging_legs.py`, add to the imports at the top (after line 4):

```python
from app.schemas import PricingEnvironmentSnapshot
```

Append these tests to the end of the file:

```python
def _option(session, u):
    return _mark(session, u, "IO2406C", itype="option", option_type="call",
                 strike=5600.0, family="index_option", mult=100.0)


def test_option_leg_honors_passed_volatility(session):
    u = _underlying(session)
    inst = _option(session, u)
    args = [{"instrument_id": inst.id, "role": "gamma_vega"}]
    low = hedging_legs.price(session, args, spot=5600.0,
        option_market=PricingEnvironmentSnapshot(spot=5600.0, rate=0.03,
            dividend_yield=0.0, volatility=0.20))[0]
    high = hedging_legs.price(session, args, spot=5600.0,
        option_market=PricingEnvironmentSnapshot(spot=5600.0, rate=0.03,
            dividend_yield=0.0, volatility=0.35))[0]
    assert low["priced_ok"] is True and high["priced_ok"] is True
    # Gamma is strongly vol-dependent (~1/sigma); the passed vol really feeds BS.
    assert low["gamma"] != high["gamma"]


def test_option_leg_refused_when_market_missing(session):
    u = _underlying(session)
    inst = _option(session, u)
    leg = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "gamma_vega"}],
        spot=5600.0, option_market=None, option_market_error="no profile params")[0]
    assert leg["priced_ok"] is False
    assert leg["price_error"] == "no profile params"
    assert leg["delta"] == 0.0 and leg["gamma"] == 0.0 and leg["vega"] == 0.0


def test_future_leg_unaffected_by_missing_option_market(session):
    u = _underlying(session)
    inst = _mark(session, u, "IC2406", itype="future", mult=200.0)
    leg = hedging_legs.price(session, [{"instrument_id": inst.id, "role": "delta"}],
        spot=5600.0, option_market=None)[0]
    assert leg["priced_ok"] is True
    assert leg["delta"] == 200.0 * 5600.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hedging_legs.py -q`
Expected: FAIL — `price()` got an unexpected keyword argument `option_market`.

- [ ] **Step 3: Implement the new `price` and `_option_unit_greeks`**

In `backend/app/services/hedging_legs.py`, replace `price` (lines 83-106) with:

```python
def price(session: Session, legs: list[dict[str, Any]], *, spot: float,
          option_market: PricingEnvironmentSnapshot | None = None,
          option_market_error: str | None = None) -> list[dict[str, Any]]:
    """Per-contract greek contributions: futures/spot closed form; options via BS.

    Option legs price under ``option_market`` (spot+r+q+vol from the run's pricing
    profile). When it is ``None`` the option leg refuses (``priced_ok=False`` with
    ``option_market_error``) rather than falling back to flat defaults. Cash
    conventions match the book rows: delta_cash = δ·S, gamma_cash = γ·S²/100, vega
    raw; each scaled by the contract multiplier.
    """
    out: list[dict[str, Any]] = []
    for leg in legs:
        inst = session.get(HedgeInstrument, leg["instrument_id"])
        mult = float(_effective_multiplier(inst) or 1.0)
        if inst.instrument_type not in _OPTION_TYPES:
            d, g, v = mult * spot, 0.0, 0.0
            ok, error = True, None
        elif option_market is None:
            d = g = v = 0.0
            ok = False
            error = option_market_error or "pricing parameters unavailable for option leg"
        else:
            s = float(option_market.spot)
            per = _option_unit_greeks(inst, option_market)
            ok, error = per["ok"], per.get("error")
            d = per["delta"] * mult * s
            g = per["gamma"] * mult * s * s / 100.0
            v = per["vega"] * mult
        out.append({**_leg_dict(inst, role=leg.get("role", "delta")),
                    "key": f"{inst.exchange}:{inst.contract_code}",
                    "delta": d, "gamma": g, "vega": v,
                    "priced_ok": ok, "price_error": error})
    return out
```

Then replace `_option_unit_greeks` (lines 109-126) with:

```python
def _option_unit_greeks(
    inst: HedgeInstrument, market: PricingEnvironmentSnapshot
) -> dict[str, Any]:
    """Per-unit BS greeks for a listed option via a transient Position, priced under
    the supplied ``market`` (spot + rate + dividend_yield + volatility)."""
    # EuropeanVanillaOption takes 'maturity' (years to expiry), 'strike',
    # and uppercase 'option_type' (CALL/PUT). It does NOT accept 'expiry',
    # 'initial_price', or lowercase option_type.
    maturity = _years_to_expiry(inst.expiry)
    product_kwargs = {
        "strike": float(inst.strike or 0.0),
        "maturity": maturity,
        "option_type": (inst.option_type or "call").upper(),
    }
    pos = Position(
        underlying=inst.contract_code, product_type="EuropeanVanillaOption",
        product_kwargs=product_kwargs, engine_name="BlackScholesEngine",
        engine_kwargs={}, quantity=1.0, entry_price=0.0,
    )
    return compute_position_greeks(pos, market)
```

(The `PricingEnvironmentSnapshot` import at `hedging_legs.py:10` stays — it is now used for the type annotations.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hedging_legs.py -q`
Expected: PASS (pre-existing + 3 new).

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_legs.py tests/test_hedging_legs.py
git commit -m "feat(hedging): price option legs under passed profile market; refuse if absent

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: `solve_hedge` builds and forwards the option market

**Files:**
- Modify: `backend/app/services/domains/hedging_strategy.py:1-11` (import) and `:53-92` (`solve_hedge`)
- Test: `tests/test_hedging_solve_orchestration.py`

- [ ] **Step 1: Update the existing BS test + add the refusal test**

In `tests/test_hedging_solve_orchestration.py`, update the import block (lines 3-6) to add the profile models:

```python
from app.models import (HedgeInstrument, HedgeMapEntry, Portfolio, Position,
                        PricingParameterProfile, PricingParameterRow, RiskRun,
                        Underlying)
from app.services.domains import hedging_strategy as hs
```

In `test_solve_hedge_prices_option_leg_via_black_scholes`, replace the single
`session.add(RiskRun(...))` block (lines 74-76) with a profile + a run that
references it, and add a profile assertion at the end:

```python
    prof = PricingParameterProfile(name="p", valuation_date=date.today(),
                                   source_type="default_underlying",
                                   status="completed", summary={})
    session.add(prof); session.flush()
    session.add(PricingParameterRow(profile_id=prof.id, source_trade_id="x",
        symbol="000905.SH", spot=5600.0, rate=0.03, dividend_yield=0.0,
        volatility=0.22))
    session.add(RiskRun(portfolio_id=pf.id, status="completed",
        pricing_parameter_profile_id=prof.id, metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 90000.0,
         "vega": 5000.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.flush()
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_gamma_neutral")
    assert out["status"] in ("feasible", "infeasible")
    assert out["pricing_parameter_profile_id"] == prof.id
    opt = next(l for l in out["legs"] if l["instrument_type"] == "option")
    assert opt["priced_ok"] is True
    assert opt["gamma"] != 0.0   # Black-Scholes produced a real, non-degenerate gamma
```

Note: `date` is already imported at the top of the file (line 2).

Append a new refusal test to the end of the file:

```python
def test_solve_hedge_refuses_option_leg_without_profile(session):
    # Same instruments as the BS test, but the run has NO pricing parameter
    # profile -> option legs must refuse (excluded + warned), never default-priced.
    u = Underlying(symbol="000905.SH", asset_class="index", currency="CNY")
    pf = Portfolio(name="pf-noprof", base_currency="CNY")
    session.add_all([u, pf]); session.flush()
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_future", series_root="IC", exchange="CFFEX",
        contract_code="IC2412", instrument_type="future", multiplier=200.0,
        expiry=date(2026, 12, 18), last_price=5600.0, status="live"))
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IC2412",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active"))
    session.add(HedgeInstrument(
        underlying_id=u.id, family="index_option", series_root="IO", exchange="CFFEX",
        contract_code="IO2412C5600", instrument_type="option", option_type="call",
        strike=5600.0, multiplier=100.0, expiry=date(2026, 12, 18),
        last_price=5600.0, status="live"))
    session.add(HedgeMapEntry(
        underlying_id=u.id, exchange="CFFEX", contract_code="IO2412C5600",
        family="index_option", series_root="IO", instrument_type="option",
        option_type="call", strike=5600.0, reconcile_status="active"))
    session.add(RiskRun(portfolio_id=pf.id, status="completed", metrics={"positions": [
        {"underlying": "000905.SH", "delta_cash": 1120000.0, "gamma_cash": 90000.0,
         "vega": 5000.0, "spot": 5600.0, "greeks_ok": True}]}))
    session.flush()
    out = hs.solve_hedge(session, portfolio_id=pf.id, underlying="000905.SH",
                         strategy="delta_gamma_neutral")
    # Refused option leg is not among the solved legs, and is surfaced as a warning.
    assert [l for l in out["legs"] if l["instrument_type"] == "option"] == []
    assert any("parameter" in (w["error"] or "").lower() for w in out["warnings"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_hedging_solve_orchestration.py -q`
Expected: FAIL — `KeyError: 'pricing_parameter_profile_id'` in the BS test, and the
refusal test fails because option legs are still default-priced (option present in
`out["legs"]`).

- [ ] **Step 3: Implement `solve_hedge` wiring**

In `backend/app/services/domains/hedging_strategy.py`, add the schema import after line 9:

```python
from ...models import HedgeBand, Underlying
from ...schemas import PricingEnvironmentSnapshot
from .. import hedging_greeks, hedging_legs, hedging_solver
```

Then, inside `solve_hedge`, replace the block from `uid = _underlying_id(...)` through
the `priced = hedging_legs.price(session, legs, spot=spot)` line (lines 69-72) with:

```python
    uid = _underlying_id(session, underlying)
    if legs is None:
        legs = hedging_legs.propose(session, underlying_id=uid, strategy=strategy)

    market = target.get("market")
    if target.get("params_ok") and market is not None:
        option_market = PricingEnvironmentSnapshot(
            spot=float(market["spot"]), rate=float(market["rate"]),
            dividend_yield=float(market["dividend_yield"]),
            volatility=float(market["volatility"]))
        option_market_error = None
    else:
        option_market = None
        missing = ", ".join(target.get("missing_params") or []) or \
            "rate/dividend_yield/volatility"
        option_market_error = (
            f"option leg not priced: pricing parameters unavailable for {underlying} "
            f"from the risk run's profile ({missing})")

    priced = hedging_legs.price(session, legs, spot=spot,
                                option_market=option_market,
                                option_market_error=option_market_error)
```

Finally, add `pricing_parameter_profile_id` to the return dict (lines 86-92) — change
the `"strategy": strategy, "risk_run_id": agg["risk_run_id"], "spot": spot,` line to:

```python
        "strategy": strategy, "risk_run_id": agg["risk_run_id"],
        "pricing_parameter_profile_id": agg.get("pricing_parameter_profile_id"),
        "spot": spot,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_hedging_solve_orchestration.py -q`
Expected: PASS (all, including the updated BS test and new refusal test).

- [ ] **Step 5: Run the full hedging suite to catch regressions**

Run: `python -m pytest tests/ -q -k "hedging or pricing_profile"`
Expected: PASS. (Confirms no other hedging test relied on default-vol option pricing.)

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/domains/hedging_strategy.py tests/test_hedging_solve_orchestration.py
git commit -m "feat(hedging): solve_hedge prices option legs under the run's profile market

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage**
- Resolver + `UnderlyingMarketParams` + field-wise collapse → Task 1.
- `aggregate_by_underlying` enrichment (`market`/`params_ok`/`missing_params`, top-level `pricing_parameter_profile_id`, profile-less refusal) → Task 2.
- `price()` new signature + `_option_unit_greeks(inst, market)` → Task 3.
- `solve_hedge` builds/forwards `option_market`, returns profile id, refusal warnings → Task 4.
- Error table (missing / ambiguous / profile-less, futures unaffected) → covered by tests in Tasks 1, 3, 4.
- Accepted simplifications (flat vol, ETF-inherits-index) → no code; documented in spec.
- All four named test files touched.

**Placeholder scan:** None — every step shows complete code and exact commands.

**Type consistency:** `UnderlyingMarketParams(rate, dividend_yield, volatility, missing_fields, ambiguous_fields)` + `.ok` used consistently. `resolve_underlying_market_params(rows, symbol)` signature matches all callers. `price(session, legs, *, spot, option_market, option_market_error)` matches the call in `solve_hedge`. `_option_unit_greeks(inst, market)` matches its single caller. `market` dict keys (`spot/rate/dividend_yield/volatility`) are written in Task 2 and read identically in Task 4. `aggregate_by_underlying` return keys (`params_ok`, `market`, `missing_params`, `pricing_parameter_profile_id`) are produced in Task 2 and consumed in Task 4.

**Note on test command:** run pytest from the repo root — `pyproject.toml` sets `testpaths = ["tests"]` and `pythonpath = ["backend"]`, so `app...` imports resolve automatically (no `cd backend`, no `PYTHONPATH` needed).
