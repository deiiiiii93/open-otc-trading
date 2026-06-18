# Per-Underlying Default Pricing Profile Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `build_default_pricing_profile` emit one `PricingParameterRow` per live underlying (not per position/trade-id), so trade-id-less positions get priced, and retire the lossy `latest_pricing_rows_by_trade_id` round-trip.

**Architecture:** A default profile's economics are entirely per-underlying (spot + rate/div/vol). Stop fanning rows out per position; emit one symbol-keyed row per underlying with `source_trade_id=""`. The existing resolver already falls back from trade-id to "unique complete row for the underlying," so positions (with or without a trade id) resolve by symbol. The XLSX-import path is untouched and stays per-trade.

**Tech Stack:** Python, SQLAlchemy ORM, FastAPI, pytest. Spec: `docs/superpowers/specs/2026-06-03-per-underlying-default-pricing-profile-design.md`.

---

## File Structure

- **Modify** `backend/app/services/pricing_profiles.py`
  - Replace `latest_pricing_rows_by_trade_id` (dict, lossy) with `pricing_rows_for_profile` (list).
  - Rewrite the per-position loop in `build_default_pricing_profile` to a per-underlying emit; drop `skipped_positions`.
- **Modify** `backend/app/services/risk_engine.py` — switch consumer to `pricing_rows_for_profile`.
- **Modify** `backend/app/services/position_pricer.py` — switch consumer to `pricing_rows_for_profile`.
- **Modify** `tests/test_position_import_pricing.py` — rename/rework the lookup test to the list API.
- **Modify** `tests/test_underlying_defaults.py` — rewrite builder tests to per-underlying shape; add the trade-id-less regression test.

Not changed (verified): `backend/app/tools/_shaping.py` (its `skipped_positions` handling is optional and must stay for backward compatibility with old profiles), `resolve_pricing_parameter_row_for_position`, `resolve_underlying_market_params`, the hedging/domains tests (fixture-based, don't call the builder).

---

## Task 1: Replace `latest_pricing_rows_by_trade_id` with `pricing_rows_for_profile`

This swap is behavior-preserving for existing tests: for XLSX profiles every row already has a unique trade id, so `list(profile.rows)` equals `list({trade_id: row}.values())`.

**Files:**
- Modify: `backend/app/services/pricing_profiles.py:497-510`
- Modify: `backend/app/services/risk_engine.py:13-19, 74-78`
- Modify: `backend/app/services/position_pricer.py:31-34, 156-160, 529-532`
- Test: `tests/test_position_import_pricing.py:41, 690-704`

- [ ] **Step 1: Rewrite the lookup test to the list API (failing)**

In `tests/test_position_import_pricing.py`, change the import on line 41 from `latest_pricing_rows_by_trade_id,` to `pricing_rows_for_profile,`. Then replace the whole `test_latest_pricing_rows_by_trade_id_uses_profile_rows` function (lines 690-704) with:

```python
def test_pricing_rows_for_profile_returns_all_rows(tmp_path: Path):
    market_path = tmp_path / "market.xlsx"
    write_market_workbook(market_path, ["T-VANILLA", "T-OTHER"], spot=101.0)
    session = configure_test_db(tmp_path)
    profile = import_pricing_parameter_profile_from_xlsx(
        session,
        xlsx_path=market_path,
        name="Lookup Profile",
        valuation_date=datetime(2026, 4, 30),
    )
    session.commit()

    rows = pricing_rows_for_profile(session, profile_id=profile.id)

    assert isinstance(rows, list)
    assert {row.source_trade_id for row in rows} == {"T-VANILLA", "T-OTHER"}
    assert all(row.profile_id == profile.id for row in rows)
    assert all(row.spot == 101.0 for row in rows)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_position_import_pricing.py::test_pricing_rows_for_profile_returns_all_rows -q`
Expected: FAIL — `ImportError: cannot import name 'pricing_rows_for_profile'`.

- [ ] **Step 3: Add `pricing_rows_for_profile`, remove `latest_pricing_rows_by_trade_id`**

In `backend/app/services/pricing_profiles.py`, replace the function at lines 497-510:

```python
def latest_pricing_rows_by_trade_id(
    session: Session,
    *,
    profile_id: int,
) -> dict[str, PricingParameterRow]:
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

with:

```python
def pricing_rows_for_profile(
    session: Session,
    *,
    profile_id: int,
) -> list[PricingParameterRow]:
    profile = (
        session.query(PricingParameterProfile)
        .options(selectinload(PricingParameterProfile.rows))
        .filter(PricingParameterProfile.id == profile_id)
        .one_or_none()
    )
    if profile is None:
        raise ValueError(f"Pricing parameter profile not found: {profile_id}")
    return list(profile.rows)
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `python -m pytest tests/test_position_import_pricing.py::test_pricing_rows_for_profile_returns_all_rows -q`
Expected: PASS.

- [ ] **Step 5: Switch `risk_engine.py` to the list API**

In `backend/app/services/risk_engine.py`, in the import block (lines 13-19) change `latest_pricing_rows_by_trade_id,` to `pricing_rows_for_profile,`. Then replace lines 74-78:

```python
    pricing_rows_by_trade = latest_pricing_rows_by_trade_id(
        session,
        profile_id=pricing_parameter_profile_id,
    )
    pricing_rows = list(pricing_rows_by_trade.values())
```

with:

```python
    pricing_rows = pricing_rows_for_profile(
        session,
        profile_id=pricing_parameter_profile_id,
    )
```

- [ ] **Step 6: Switch `position_pricer.py` to the list API**

In `backend/app/services/position_pricer.py`, in the import block (lines 31-34) change `latest_pricing_rows_by_trade_id,` to `pricing_rows_for_profile,`. Then replace lines 156-160:

```python
    pricing_rows = (
        latest_pricing_rows_by_trade_id(session, profile_id=pricing_parameter_profile_id)
        if pricing_parameter_profile_id is not None
        else {}
    )
```

with:

```python
    pricing_rows = (
        pricing_rows_for_profile(session, profile_id=pricing_parameter_profile_id)
        if pricing_parameter_profile_id is not None
        else []
    )
```

Then replace the call at lines 529-532:

```python
    pricing_row_resolution = resolve_pricing_parameter_row_for_position(
        list(pricing_rows.values()),
        position,
    )
```

with:

```python
    pricing_row_resolution = resolve_pricing_parameter_row_for_position(
        pricing_rows,
        position,
    )
```

- [ ] **Step 7: Run the affected suites to verify green**

Run: `python -m pytest tests/test_position_import_pricing.py tests/test_underlying_defaults.py -q`
Expected: PASS (no behavior change yet for the builder; the consumer swap is equivalent for XLSX profiles).

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/pricing_profiles.py backend/app/services/risk_engine.py backend/app/services/position_pricer.py tests/test_position_import_pricing.py
git commit -m "refactor(pricing): pricing_rows_for_profile returns all rows (drop lossy trade-id dict)"
```

---

## Task 2: Make `build_default_pricing_profile` per-underlying

**Files:**
- Modify: `backend/app/services/pricing_profiles.py:422-494`
- Test: `tests/test_underlying_defaults.py` (new test + rewrites at lines 19, 305, 338, 415-455, 473-493, 738-781)

- [ ] **Step 1: Add the trade-id-less regression test (failing)**

In `tests/test_underlying_defaults.py`, extend the import on line 19 from:

```python
from app.services.pricing_profiles import _open_position_underlyings, build_default_pricing_profile
```

to:

```python
from app.services.pricing_profiles import (
    _open_position_underlyings,
    build_default_pricing_profile,
    resolve_pricing_parameter_row_for_position,
)
```

Then add this test (place it after `test_build_happy_path`):

```python
def test_build_default_prices_trade_id_less_position(session: Session) -> None:
    # A position booked without a source_trade_id (the #109 case) must still be
    # priceable: the per-underlying default row resolves by underlying.
    position = _make_position(session, underlying="000300.SH", source_trade_id=None)
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=3842.15),
    ):
        profile = build_default_pricing_profile(session)
        session.commit()

    rows = [row for row in profile.rows if row.symbol == "000300.SH"]
    assert len(rows) == 1
    assert rows[0].source_trade_id == ""
    assert "skipped_positions" not in profile.summary

    resolution = resolve_pricing_parameter_row_for_position(list(profile.rows), position)
    assert resolution.ok is True
    assert resolution.match_type == "underlying"
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `python -m pytest tests/test_underlying_defaults.py::test_build_default_prices_trade_id_less_position -q`
Expected: FAIL — today the builder skips the trade-id-less position (`skipped_positions` present, zero `000300.SH` rows), so `len(rows) == 1` fails.

- [ ] **Step 3: Rewrite the builder loop to per-underlying**

In `backend/app/services/pricing_profiles.py`, replace the block at lines 422-494 (from `skipped_positions: list[dict[str, Any]] = []` through `return profile`):

```python
    skipped_positions: list[dict[str, Any]] = []
    row_count = 0
    open_positions = (
        session.query(Position)
        .filter(Position.underlying.isnot(None))
        .filter(Position.status != "closed")
        .all()
    )
    for position in open_positions:
        underlying = (position.underlying or "").strip()
        if underlying not in fetched:
            continue
        payload = position.source_payload or {}
        if isinstance(payload, dict) and payload.get("trade_state") == "敲出":
            continue
        trade_id = (position.source_trade_id or "").strip()
        if not trade_id:
            skipped_positions.append(
                {
                    "position_id": position.id,
                    "reason": "missing_source_trade_id",
                }
            )
            continue
        store = existing[underlying]
        manual_inputs = resolved_inputs[underlying]
        inherited = inherited_inputs.get(underlying) or {}
        manual_field_sources = {
            field: (
                "underlying_default"
                if getattr(store, field) is not None
                else "latest_pricing_parameter_profile"
            )
            for field in MANUAL_INPUT_FIELDS
        }
        spot = fetched[underlying]["spot"]
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id=trade_id,
                symbol=underlying,
                spot=spot,
                rate=manual_inputs["rate"],
                dividend_yield=manual_inputs["dividend_yield"],
                volatility=manual_inputs["volatility"],
                source_row=None,
                source_payload=make_json_safe(
                    {
                        "source": "default_underlying",
                        "underlying_default_id": store.id,
                        "akshare_symbol": fetched[underlying]["akshare_symbol"],
                        "manual_input_sources": manual_field_sources,
                        "inherited_pricing_parameter_profile_id": inherited.get(
                            "pricing_parameter_profile_id"
                        ),
                        "inherited_pricing_parameter_row_id": inherited.get(
                            "pricing_parameter_row_id"
                        ),
                    }
                ),
            )
        )
        row_count += 1

    profile.summary = {
        "row_count": row_count,
        "underlyings": [fetched[underlying] for underlying in underlyings],
        "valuation_date": effective_valuation.isoformat(),
        "adjust": adjust,
        "skipped_positions": skipped_positions,
    }
    session.flush()
    return profile
```

with:

```python
    row_count = 0
    for underlying in underlyings:
        if underlying not in fetched:
            continue
        store = existing[underlying]
        manual_inputs = resolved_inputs[underlying]
        inherited = inherited_inputs.get(underlying) or {}
        manual_field_sources = {
            field: (
                "underlying_default"
                if getattr(store, field) is not None
                else "latest_pricing_parameter_profile"
            )
            for field in MANUAL_INPUT_FIELDS
        }
        session.add(
            PricingParameterRow(
                profile_id=profile.id,
                source_trade_id="",
                symbol=underlying,
                spot=fetched[underlying]["spot"],
                rate=manual_inputs["rate"],
                dividend_yield=manual_inputs["dividend_yield"],
                volatility=manual_inputs["volatility"],
                source_row=None,
                source_payload=make_json_safe(
                    {
                        "source": "default_underlying",
                        "underlying_default_id": store.id,
                        "akshare_symbol": fetched[underlying]["akshare_symbol"],
                        "manual_input_sources": manual_field_sources,
                        "inherited_pricing_parameter_profile_id": inherited.get(
                            "pricing_parameter_profile_id"
                        ),
                        "inherited_pricing_parameter_row_id": inherited.get(
                            "pricing_parameter_row_id"
                        ),
                    }
                ),
            )
        )
        row_count += 1

    profile.summary = {
        "row_count": row_count,
        "underlyings": [fetched[underlying] for underlying in underlyings],
        "valuation_date": effective_valuation.isoformat(),
        "adjust": adjust,
    }
    session.flush()
    return profile
```

(The `Position` import stays — it is still used as a type hint at lines 161 and 232.)

- [ ] **Step 4: Run the new test to verify it passes**

Run: `python -m pytest tests/test_underlying_defaults.py::test_build_default_prices_trade_id_less_position -q`
Expected: PASS.

- [ ] **Step 5: Rewrite `test_build_happy_path` to per-underlying shape**

In `tests/test_underlying_defaults.py`, replace the assertions block of `test_build_happy_path` (lines 446-455, from `assert profile.source_type` through the `underlyings ==` assertion):

```python
    assert profile.source_type == "default_underlying"
    assert len(profile.rows) == 3
    by_trade = {row.source_trade_id: row for row in profile.rows}
    assert by_trade["TRD-1"].spot == pytest.approx(3842.15)
    assert by_trade["TRD-1"].rate == pytest.approx(0.025)
    assert by_trade["TRD-1"].dividend_yield == pytest.approx(0.02)
    assert by_trade["TRD-1"].volatility == pytest.approx(0.185)
    assert by_trade["TRD-3"].spot == pytest.approx(6184.72)
    underlyings = {item["underlying"] for item in profile.summary["underlyings"]}
    assert underlyings == {"000300.SH", "000852.SH"}
```

with:

```python
    assert profile.source_type == "default_underlying"
    assert len(profile.rows) == 2
    by_symbol = {row.symbol: row for row in profile.rows}
    assert by_symbol["000300.SH"].spot == pytest.approx(3842.15)
    assert by_symbol["000300.SH"].rate == pytest.approx(0.025)
    assert by_symbol["000300.SH"].dividend_yield == pytest.approx(0.02)
    assert by_symbol["000300.SH"].volatility == pytest.approx(0.185)
    assert by_symbol["000300.SH"].source_trade_id == ""
    assert by_symbol["000852.SH"].spot == pytest.approx(6184.72)
    underlyings = {item["underlying"] for item in profile.summary["underlyings"]}
    assert underlyings == {"000300.SH", "000852.SH"}
```

- [ ] **Step 6: Invert `test_build_skips_positions_without_trade_id`**

Replace the whole function (lines 473-493):

```python
def test_build_skips_positions_without_trade_id(session: Session) -> None:
    _make_position(session, underlying="000300.SH", source_trade_id=None)
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=3842.15),
    ):
        profile = build_default_pricing_profile(session)
        session.commit()
    assert len(profile.rows) == 1
    skipped = profile.summary.get("skipped_positions") or []
    assert len(skipped) == 1
    assert skipped[0]["reason"] == "missing_source_trade_id"
```

with:

```python
def test_build_emits_one_row_per_underlying_regardless_of_trade_id(session: Session) -> None:
    _make_position(session, underlying="000300.SH", source_trade_id=None)
    _make_position(session, underlying="000300.SH", source_trade_id="TRD-2")
    upsert_underlying_default(
        session,
        underlying="000300.SH",
        rate=0.025,
        dividend_yield=0.02,
        volatility=0.185,
    )
    session.commit()
    with patch(
        "app.services.pricing_profiles.fetch_akshare_snapshot",
        return_value=_fake_snapshot("000300", spot=3842.15),
    ):
        profile = build_default_pricing_profile(session)
        session.commit()
    assert len(profile.rows) == 1
    assert profile.rows[0].symbol == "000300.SH"
    assert profile.rows[0].source_trade_id == ""
    assert "skipped_positions" not in profile.summary
```

- [ ] **Step 7: Fix the row lookups in `test_build_inherits_manual_inputs_from_latest_profile_until_human_edit`**

This test fetches the default-profile row by trade id; switch it to symbol. On line 305 replace:

```python
    inherited_row = next(r for r in inherited_profile.rows if r.source_trade_id == "TRD-1")
```

with:

```python
    inherited_row = next(r for r in inherited_profile.rows if r.symbol == "000300.SH")
```

On line 338 replace:

```python
    edited_row = next(r for r in edited_profile.rows if r.source_trade_id == "TRD-1")
```

with:

```python
    edited_row = next(r for r in edited_profile.rows if r.symbol == "000300.SH")
```

(The `manual_input_sources` assertions below each lookup are unchanged — that payload is still emitted per underlying.)

- [ ] **Step 8: Fix the default-side lookup in `test_default_underlying_profile_rows_match_equivalent_xlsx`**

On line 777 replace:

```python
    default_row = next(r for r in default_profile.rows if r.source_trade_id == "TRD-1")
```

with:

```python
    default_row = next(r for r in default_profile.rows if r.symbol == "000300.SH")
```

(Leave the `xlsx_row` lookup on line 778 keyed by `TRD-1` — the XLSX profile is still per-trade. The comparison over `spot/rate/dividend_yield/volatility/symbol` holds.)

- [ ] **Step 9: Run the full builder test file to verify green**

Run: `python -m pytest tests/test_underlying_defaults.py -q`
Expected: PASS (all builder tests, including the new regression test and the inverted/renamed test).

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/pricing_profiles.py tests/test_underlying_defaults.py
git commit -m "feat(pricing): per-underlying default profile (price trade-id-less positions)"
```

---

## Task 3: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the pricing/risk/hedging-adjacent suites**

Run: `python -m pytest tests/test_underlying_defaults.py tests/test_position_import_pricing.py tests/test_tools_pricing_profiles.py tests/test_services_domains_pricing_profiles.py tests/test_hedging_greeks.py tests/test_hedging_solve_orchestration.py -q`
Expected: PASS. (Confirms the fixture-based hedging/domains and `_shaping` tool tests are unaffected, and the XLSX import/pricing path is unchanged.)

- [ ] **Step 2: Run the broader backend suite**

Run: `python -m pytest -q`
Expected: PASS, or only pre-existing/AKShare-network-dependent failures unrelated to this change. If a failure references `latest_pricing_rows_by_trade_id`, `skipped_positions`, `source_trade_id` on a default profile, or per-trade default rows, fix it the same way (symbol-keyed lookup) and re-run.

- [ ] **Step 3: Commit (only if Step 2 required a fix)**

```bash
git add -A
git commit -m "test: align remaining tests with per-underlying default profile"
```

---

## Self-Review

**Spec coverage:**
- Per-underlying builder emit + drop `skipped_positions` → Task 2, Step 3. ✓
- `source_trade_id=""` (no migration) → Task 2, Step 3 (asserted in Steps 1/6). ✓
- Replace `latest_pricing_rows_by_trade_id` with list-returning `pricing_rows_for_profile` + both consumers → Task 1. ✓
- Resolver unchanged; trade-id-less position resolves by underlying → Task 2, Step 1 regression test asserts `match_type == "underlying"`. ✓
- XLSX path untouched / stays per-trade → Task 1 Step 1 (two distinct trade rows), Task 2 Step 8 (xlsx_row still keyed by TRD-1). ✓
- Test rewrites: happy_path, skips→invert, inherits, equivalent_xlsx, lookup-test → Tasks 1-2. ✓
- `_shaping.py` intentionally unchanged (backward compat) → File Structure note; verified in Task 3 Step 1. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type/name consistency:** `pricing_rows_for_profile(session, *, profile_id) -> list[PricingParameterRow]` is defined in Task 1 Step 3 and used identically in risk_engine (Step 5), position_pricer (Step 6), and the test (Step 1). `resolve_pricing_parameter_row_for_position` takes a list in both consumers and the new test. `source_trade_id == ""` is the invariant asserted across Task 2 Steps 1/5/6. ✓
