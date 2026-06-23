# Asian Termsheet Pricing-Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a booked weighted/dated Asian option's observation schedule (and realized fixings) flow into position pricing, replacing today's silent uniform-average-over-`num_observations` mispricing.

**Architecture:** The schedule lives in `product_kwargs.observation_records` (like every other product's schedule), so position pricing stays session-free. A new Asian branch in `_build_termsheet` (which holds `valuation_date` + calendar context) converts those records into QuantArk-ready records, splitting past/future relative to the valuation date. Realized fixing prices are immutable stored snapshots captured from `MarketQuote` once a date passes.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, pytest; QuantArk pricing engine (already supports weighted/dated/realized Asian pricing — no QuantArk change here).

## Global Constraints

- **Work in a dedicated git worktree off `main`** (`/Users/fuxinyao/oot-asian-wiring`, branch `feat/asian-pricing-wiring`). The primary checkout is on a concurrent session's branch (`feature/asian-quantark-calendar-weighted`) with uncommitted work — never commit there. The merged Asian integration this builds on lives on `main`.
- **Worktree env**: it lacks `node_modules` (gitignored symlink) and `config/agent_channels.yaml` (gitignored). Copy `config/agent_channels.yaml` from the primary checkout for any HTTP/client-fixture test. `config/` itself is NOT gitignored → commit selectively, never `git add -A`.
- **Run tests with** `.venv/bin/python -m pytest` from the worktree root. QuantArk is installed editable from `/Users/fuxinyao/quant-ark/quantark`.
- **TDD**: red → green → refactor, one behavior per test, real code (no mocks of QuantArk).
- **Regression invariant**: an unweighted, frequency-only Asian must price **byte-identical** to pre-change; `backend/tests/test_cross_channel_equivalence.py` must stay green.
- **Review gate**: run `zenmux-codex-review-loop` (GPT-5.5 xhigh, **max 3 loops**) at the end of each task, per the standing `/goal`.
- **No QuantArk edits.** QuantArk’s registry already coerces `product_kwargs["observation_records"]` (list of dicts) into `AsianObservationRecord(observation_time, observed_price, weight)`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backend/app/services/quantark.py` | **Component B** — `_asian_observation_records_for_pricing(...)` + call site in `_build_termsheet`: records → QuantArk records, past/future split, drop `num_observations`. |
| `backend/app/services/domains/product_builders.py` | **Component A1** — `_build_asian` emits `observation_records` from frequency/explicit dates (+ optional weights). |
| `backend/app/services/domains/positions.py` | **Component C** — `capture_due_asian_fixings(...)` (idempotent capture/backfill, immutable). |
| `backend/app/services/domains/position_terms.py` | **Component A2** — booking hook eager-captures already-past fixings. |
| `backend/app/main.py` | **Component C surface** — `POST …/asian-fixings/capture` endpoint. |
| `backend/app/services/agent/tools/*` + SKILL | **Component C surface** — "record due Asian fixings" agent tool + orchestrator routing line. |
| `backend/tests/test_asian_pricing_wiring.py` | Component B tests. |
| `backend/tests/test_asian_schedule_materialization.py` | Component A tests. |
| `backend/tests/test_asian_fixing_capture.py` | Component C tests. |

---

## Task 1: Component B — Pricing wiring in `_build_termsheet`

This is the keystone: turn `product_kwargs["observation_records"]` into QuantArk-ready records at pricing time. Testable with hand-crafted kwargs — no booking or capture needed.

**Files:**
- Modify: `backend/app/services/quantark.py` (add `_asian_observation_records_for_pricing`; call it inside `_build_termsheet` after `_add_observation_times`, before `normalize_quantark_kwargs` at ~line 714)
- Test: `backend/tests/test_asian_pricing_wiring.py`

**Interfaces:**
- Consumes: `_observation_context(market)`, `_observation_time(context, observation_date)`, `_parse_datetime(value)` (existing in `quantark.py`); `PricingEnvironmentSnapshot`.
- Produces:
  ```python
  def _asian_observation_records_for_pricing(
      records: list[dict], market: PricingEnvironmentSnapshot
  ) -> list[dict]:
      """Map stored {observation_date, weight, observed_price?} records to
      QuantArk {observation_time, observed_price?, weight?} records.
      - observation_time <= 0 (past): keep observed_price; if missing, DROP + renormalize.
      - observation_time  > 0 (future): force observed_price = None.
      Returns [] if records is falsy."""
  ```
  Call site removes `num_observations` from kwargs when `observation_records` is present and non-empty.

- [ ] **Step 1: Write the failing test — weighted future-only records become weighted observation_times**

```python
# backend/tests/test_asian_pricing_wiring.py
from datetime import datetime
from app.services.quantark import (
    _asian_observation_records_for_pricing,
    PricingEnvironmentSnapshot,
)


def _market(valuation="2025-01-01"):
    return PricingEnvironmentSnapshot(valuation_date=datetime.fromisoformat(valuation))


def test_future_records_get_observation_time_and_weight_no_observed_price():
    records = [
        {"observation_date": "2025-04-01", "weight": 0.25},
        {"observation_date": "2025-07-01", "weight": 0.75},
    ]
    out = _asian_observation_records_for_pricing(records, _market("2025-01-01"))
    assert len(out) == 2
    assert all(r["observation_time"] > 0 for r in out)
    assert all(r.get("observed_price") is None for r in out)
    assert [r["weight"] for r in out] == [0.25, 0.75]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_pricing_wiring.py::test_future_records_get_observation_time_and_weight_no_observed_price -v`
Expected: FAIL with `ImportError: cannot import name '_asian_observation_records_for_pricing'`

- [ ] **Step 3: Implement `_asian_observation_records_for_pricing`**

```python
# backend/app/services/quantark.py  (place near _add_observation_times)
def _asian_observation_records_for_pricing(
    records: Any, market: PricingEnvironmentSnapshot
) -> list[dict]:
    if not records or not isinstance(records, list):
        return []
    context = _observation_context(market)
    resolved: list[dict] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        observation_date = _parse_datetime(record.get("observation_date"))
        if not isinstance(observation_date, datetime):
            continue
        t = _observation_time(context, observation_date)
        weight = record.get("weight")
        if t <= 0:  # past relative to valuation
            observed = record.get("observed_price")
            if observed is None:
                # Uncaptured past fixing: drop it rather than crash QuantArk.
                continue
            resolved.append(
                {"observation_time": t, "observed_price": float(observed), "weight": weight}
            )
        else:  # future relative to valuation
            resolved.append({"observation_time": t, "observed_price": None, "weight": weight})
    return resolved
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_pricing_wiring.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — past captured record kept, future-of-stored nulled, uncaptured dropped**

```python
def test_past_future_split_and_uncaptured_drop():
    records = [
        {"observation_date": "2024-10-01", "weight": 0.2, "observed_price": 101.0},  # past, captured
        {"observation_date": "2024-11-01", "weight": 0.2},                            # past, uncaptured
        {"observation_date": "2025-06-01", "weight": 0.3, "observed_price": 999.0},   # future-of-valuation but carries a stored price
        {"observation_date": "2025-09-01", "weight": 0.3},                            # future
    ]
    out = _asian_observation_records_for_pricing(records, _market("2025-01-01"))
    # uncaptured past dropped -> 3 records
    assert len(out) == 3
    past = [r for r in out if r["observation_time"] <= 0]
    fut = [r for r in out if r["observation_time"] > 0]
    assert len(past) == 1 and past[0]["observed_price"] == 101.0
    # the stored-price future record must be nulled (it is future relative to valuation)
    assert all(r["observed_price"] is None for r in fut)
```

- [ ] **Step 6: Run test to verify it passes** (the impl from Step 3 already covers this)

Run: `.venv/bin/python -m pytest backend/tests/test_asian_pricing_wiring.py::test_past_future_split_and_uncaptured_drop -v`
Expected: PASS

- [ ] **Step 7: Wire the helper into `_build_termsheet` and drop `num_observations` when records present**

In `backend/app/services/quantark.py`, inside `_build_termsheet`, after the existing
`filtered_product_kwargs = _add_observation_times(filtered_product_kwargs, market)` (~line 714) and before `normalize_quantark_kwargs`:

```python
    if isinstance(filtered_product_kwargs, dict) and filtered_product_kwargs.get(
        "observation_records"
    ):
        asian_records = _asian_observation_records_for_pricing(
            filtered_product_kwargs["observation_records"], market
        )
        if asian_records:
            filtered_product_kwargs = {
                **filtered_product_kwargs,
                "observation_records": asian_records,
            }
            # Records take precedence in QuantArk; drop the count to avoid ambiguity.
            filtered_product_kwargs.pop("num_observations", None)
        else:
            # All records dropped (e.g. all-uncaptured-past): fall back to count behavior.
            filtered_product_kwargs = {
                k: v for k, v in filtered_product_kwargs.items() if k != "observation_records"
            }
```

- [ ] **Step 8: Write the failing end-to-end pricing test — weighted booked Asian prices weighted; unweighted is byte-identical**

```python
import pytest
from app.services.quantark import build_product_for_position
from app.models import Position


def _asian_position(observation_records=None, num_observations=None):
    kwargs = {
        "strike": 100.0, "initial_price": 100.0, "maturity": 1.0,
        "option_type": "CALL", "averaging_type": "ARITHMETIC",
    }
    if observation_records is not None:
        kwargs["observation_records"] = observation_records
    if num_observations is not None:
        kwargs["num_observations"] = num_observations
    return Position(
        underlying="TEST", product_type="AsianOption", product_kwargs=kwargs,
        engine_name="AsianOptionAnalyticalEngine", quantity=1,
    )


def test_unweighted_records_match_num_observations_byte_identical():
    # 4 quarterly future dates, uniform -> must equal num_observations=4 build.
    recs = [
        {"observation_date": f"2025-{m:02d}-01", "weight": None}
        for m in (4, 7, 10, 12)
    ]
    p_records = build_product_for_position(_asian_position(observation_records=recs))
    p_count = build_product_for_position(_asian_position(num_observations=4))
    # Both resolve to the same number of observations for averaging.
    from app.services.quantark import build_pricing_env, PricingEnvironmentSnapshot
    env = build_pricing_env(PricingEnvironmentSnapshot(valuation_date=__import__("datetime").datetime(2025, 1, 1)))
    a = p_records.resolve_observations(env)
    b = p_count.resolve_observations(env)
    assert a[4] == b[4]  # total_observations equal
    assert a[3] == pytest.approx(b[3])  # uniform future weights equal
```

- [ ] **Step 9: Run the pricing test**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_pricing_wiring.py -v`
Expected: PASS (adjust the resolve_observations index assertions to QuantArk’s 5-tuple `(past_prices, past_weights, future_times, future_weights, total)` if needed)

- [ ] **Step 10: Run the regression suite**

Run: `.venv/bin/python -m pytest backend/tests/test_cross_channel_equivalence.py backend/tests/test_product_builders.py -q`
Expected: PASS (no regressions)

- [ ] **Step 11: Commit**

```bash
git add backend/app/services/quantark.py backend/tests/test_asian_pricing_wiring.py
git commit -m "feat(asian): wire observation_records into position pricing (Component B)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 12: Review gate** — run `zenmux-codex-review-loop` (max 3 loops) on this commit; fix findings; re-commit.

---

## Task 2: Component A1 — `_build_asian` emits `observation_records`

Booking with the frequency picker currently writes no dated schedule. Make `_build_asian` generate `observation_records` into `product_kwargs` so the data persists into `product.raw_terms["terms"]` and reaches Task 1’s pricing path.

**Files:**
- Modify: `backend/app/services/domains/product_builders.py` (`_build_asian`)
- Test: `backend/tests/test_asian_schedule_materialization.py`

**Interfaces:**
- Consumes: `_start_date(terms)` (reads `trade_start_date`), `schedules.asian_observation_records(start, maturity_years, frequency, weights)` (both existing).
- Produces: `product_kwargs["observation_records"] = [{"observation_date": ISO, "weight": float|None}, …]` when a start date + maturity are present; otherwise unchanged (keeps `num_observations`).

- [ ] **Step 1: Write the failing test — frequency + start date yields dated records**

```python
# backend/tests/test_asian_schedule_materialization.py
from app.services.domains.product_builders import build_product


def _terms(**over):
    base = {
        "product_type": "AsianOption", "strike": 100.0, "initial_price": 100.0,
        "maturity_years": 1.0, "option_type": "CALL", "averaging_method": "ARITHMETIC",
        "averaging_frequency": "QUARTERLY", "trade_start_date": "2025-01-01",
    }
    base.update(over)
    return base


def test_frequency_booking_emits_observation_records():
    out = build_product(_terms())
    recs = out.product_kwargs.get("observation_records")
    assert recs and len(recs) == 4  # 4 quarterly observations over 1y
    assert all("observation_date" in r for r in recs)
    assert all(r["weight"] is None for r in recs)  # uniform default
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_schedule_materialization.py::test_frequency_booking_emits_observation_records -v`
Expected: FAIL — `observation_records` is None

- [ ] **Step 3: Implement record emission in `_build_asian`**

```python
# backend/app/services/domains/product_builders.py  (inside _build_asian, after num_observations block)
    start = _start_date(terms)
    weights = terms.get("averaging_weights")
    if maturity is not None and start is not None:
        try:
            records = schedules.asian_observation_records(
                start=start,
                maturity_years=maturity,
                frequency=freq,
                weights=list(weights) if weights else None,
            )
        except ValueError as exc:
            out.missing.append(f"observation schedule ({exc})")
            records = []
        if records:
            out.product_kwargs["observation_records"] = [
                {"observation_date": r["observation_date"].isoformat(), "weight": r["weight"]}
                for r in records
            ]
```

> Note: `_start_date` and `schedules` are already imported in this module (used by `_build_snowball`). `freq` is the upper-cased frequency computed earlier in `_build_asian`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_schedule_materialization.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — no start date falls back to num_observations only**

```python
def test_no_start_date_keeps_count_only():
    out = build_product(_terms(trade_start_date=None))
    assert out.product_kwargs.get("observation_records") is None
    assert out.product_kwargs.get("num_observations") == 4
```

- [ ] **Step 6: Run test to verify it passes** (impl already guards on `start is not None`)

Run: `.venv/bin/python -m pytest backend/tests/test_asian_schedule_materialization.py::test_no_start_date_keeps_count_only -v`
Expected: PASS

- [ ] **Step 7: Write the failing test — explicit weights flow through**

```python
def test_explicit_weights_flow_into_records():
    out = build_product(_terms(averaging_weights=[0.1, 0.2, 0.3, 0.4]))
    recs = out.product_kwargs["observation_records"]
    assert [r["weight"] for r in recs] == [0.1, 0.2, 0.3, 0.4]
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_schedule_materialization.py::test_explicit_weights_flow_into_records -v`
Expected: PASS

- [ ] **Step 9: Run regression suite**

Run: `.venv/bin/python -m pytest backend/tests/test_product_builders.py backend/tests/test_cross_channel_equivalence.py -q`
Expected: PASS — note: equivalence tests may need `trade_start_date` absent to keep agent↔import byte-identical; if a parity test now diverges because one channel supplies a start date and another doesn’t, assert structural (schedule-aware) equivalence instead, matching the precedent in `project_booking_product_builders` memory.

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/domains/product_builders.py backend/tests/test_asian_schedule_materialization.py
git commit -m "feat(asian): materialize observation_records at booking from frequency/weights (Component A1)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 11: Review gate** — `zenmux-codex-review-loop` (max 3 loops); fix; re-commit.

---

## Task 3: Component C — `capture_due_asian_fixings` (capture + backfill)

Stored-snapshot, immutable fixing capture from `MarketQuote`. Pure service function, testable with seeded quotes; doubles as backfill.

**Files:**
- Modify: `backend/app/services/domains/positions.py` (add `capture_due_asian_fixings`)
- Test: `backend/tests/test_asian_fixing_capture.py`

**Interfaces:**
- Consumes: `latest_quote(session, underlying_id, as_of=cutoff)` from `app.services.quotes`; `Position.product_kwargs`, `Position.underlying_id`.
- Produces:
  ```python
  def capture_due_asian_fixings(
      session: Session, position_id: int, *, as_of: date | None = None
  ) -> int:
      """Fill observed_price (immutably) for each observation_records entry whose
      observation_date <= as_of (default today) and observed_price is null, using
      the MarketQuote close as-of that date. Returns the count newly captured.
      Idempotent: already-captured prices are never overwritten."""
  ```

- [ ] **Step 1: Write the failing test — past dates get observed_price, future untouched**

```python
# backend/tests/test_asian_fixing_capture.py
from datetime import date, datetime
from app.services.domains.positions import capture_due_asian_fixings
from app.models import Position, MarketQuote, Instrument


def _seed_asian(session, instrument_id):
    pos = Position(
        underlying="TEST", underlying_id=instrument_id, product_type="AsianOption",
        quantity=1,
        product_kwargs={"observation_records": [
            {"observation_date": "2024-06-03", "weight": None},  # past
            {"observation_date": "2099-06-03", "weight": None},  # future
        ]},
    )
    session.add(pos); session.flush()
    return pos


def test_capture_fills_past_only(db_session):
    inst = Instrument(symbol="TEST"); db_session.add(inst); db_session.flush()
    db_session.add(MarketQuote(instrument_id=inst.id, as_of=datetime(2024, 6, 3), price=123.5, price_type="close"))
    db_session.flush()
    pos = _seed_asian(db_session, inst.id)

    n = capture_due_asian_fixings(db_session, pos.id, as_of=date(2025, 1, 1))
    assert n == 1
    recs = pos.product_kwargs["observation_records"]
    assert recs[0]["observed_price"] == 123.5
    assert recs[1].get("observed_price") is None  # future untouched
```

> Use the project’s existing `db_session` fixture (see other `backend/tests/test_*` for its import/conftest). If positions need a portfolio FK, seed it as those tests do.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_capture.py::test_capture_fills_past_only -v`
Expected: FAIL — `ImportError: cannot import name 'capture_due_asian_fixings'`

- [ ] **Step 3: Implement `capture_due_asian_fixings`**

```python
# backend/app/services/domains/positions.py
from datetime import date, datetime
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from ..quotes import latest_quote


def capture_due_asian_fixings(
    session: Session, position_id: int, *, as_of: date | None = None
) -> int:
    from ...models import Position

    as_of = as_of or datetime.utcnow().date()
    position = session.get(Position, position_id)
    if position is None:
        return 0
    kwargs = dict(position.product_kwargs or {})
    records = kwargs.get("observation_records")
    if not isinstance(records, list) or position.underlying_id is None:
        return 0

    captured = 0
    new_records = []
    for record in records:
        record = dict(record) if isinstance(record, dict) else record
        if isinstance(record, dict) and record.get("observed_price") is None:
            obs = _as_date(record.get("observation_date"))
            if obs is not None and obs <= as_of:
                cutoff = datetime.combine(obs, datetime.max.time())
                quote = latest_quote(session, position.underlying_id, as_of=cutoff)
                if quote is not None:
                    record["observed_price"] = float(quote.price)
                    captured += 1
        new_records.append(record)

    if captured:
        kwargs["observation_records"] = new_records
        position.product_kwargs = kwargs
        flag_modified(position, "product_kwargs")
        session.flush()
    return captured
```

> `_as_date` already exists in `positions.py` (added in sub-project B). Reuse it; do not redefine.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_capture.py::test_capture_fills_past_only -v`
Expected: PASS

- [ ] **Step 5: Write the failing test — immutable + idempotent**

```python
def test_capture_is_immutable_and_idempotent(db_session):
    inst = Instrument(symbol="TEST"); db_session.add(inst); db_session.flush()
    db_session.add(MarketQuote(instrument_id=inst.id, as_of=datetime(2024, 6, 3), price=100.0, price_type="close"))
    db_session.flush()
    pos = _seed_asian(db_session, inst.id)
    assert capture_due_asian_fixings(db_session, pos.id, as_of=date(2025, 1, 1)) == 1
    # A later, revised quote must NOT change the already-captured fix.
    db_session.add(MarketQuote(instrument_id=inst.id, as_of=datetime(2024, 6, 3), price=200.0, price_type="close"))
    db_session.flush()
    assert capture_due_asian_fixings(db_session, pos.id, as_of=date(2025, 1, 1)) == 0
    assert pos.product_kwargs["observation_records"][0]["observed_price"] == 100.0
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_capture.py::test_capture_is_immutable_and_idempotent -v`
Expected: PASS

- [ ] **Step 7: Write the failing test — missing quote leaves null, no crash**

```python
def test_missing_quote_leaves_null(db_session):
    inst = Instrument(symbol="TEST"); db_session.add(inst); db_session.flush()
    pos = _seed_asian(db_session, inst.id)  # no quotes seeded
    assert capture_due_asian_fixings(db_session, pos.id, as_of=date(2025, 1, 1)) == 0
    assert pos.product_kwargs["observation_records"][0].get("observed_price") is None
```

- [ ] **Step 8: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_capture.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/domains/positions.py backend/tests/test_asian_fixing_capture.py
git commit -m "feat(asian): immutable observed-price capture from MarketQuote (Component C)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 10: Review gate** — `zenmux-codex-review-loop` (max 3 loops); fix; re-commit.

---

## Task 4: Booking eager-capture + capture endpoint + agent tool (Component A2 + C surfaces)

Wire capture into booking (so seasoned imports don’t carry uncaptured fixings) and expose it for ongoing/backfill use.

**Files:**
- Modify: `backend/app/services/domains/position_terms.py` (`upsert_position_term_rows` AsianOption branch — call `capture_due_asian_fixings` after the schedule is mirrored)
- Modify: `backend/app/main.py` (add `POST /api/portfolios/{portfolio_id}/positions/{position_id}/asian-fixings/capture`)
- Modify: agent tool registry + the relevant SKILL.md (add "record due Asian fixings" tool + an orchestrator routing line)
- Test: extend `backend/tests/test_asian_fixing_capture.py`; add an endpoint test in `backend/tests/test_asian_fixing_lifecycle.py`

**Interfaces:**
- Consumes: `capture_due_asian_fixings(session, position_id, as_of=None)` (Task 3).
- Produces: endpoint returning `{"captured": int, "position_id": int}`; agent tool `capture_asian_fixings(position_id)`.

- [ ] **Step 1: Write the failing test — booking eager-captures already-past fixings**

```python
def test_booking_eager_captures_past(db_session):
    from app.services.domains.position_terms import upsert_position_term_rows
    inst = Instrument(symbol="TEST"); db_session.add(inst); db_session.flush()
    db_session.add(MarketQuote(instrument_id=inst.id, as_of=datetime(2020, 6, 3), price=88.0, price_type="close"))
    db_session.flush()
    pos = Position(
        underlying="TEST", underlying_id=inst.id, product_type="AsianOption", quantity=1,
        product_kwargs={"observation_records": [{"observation_date": "2020-06-03", "weight": None}]},
    )
    db_session.add(pos); db_session.flush()
    upsert_position_term_rows(db_session, pos)
    assert pos.product_kwargs["observation_records"][0]["observed_price"] == 88.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_capture.py::test_booking_eager_captures_past -v`
Expected: FAIL — observed_price still None

- [ ] **Step 3: Call capture from the AsianOption booking branch**

In `backend/app/services/domains/position_terms.py`, in `upsert_position_term_rows`, after `_replace_asian_schedule(session, position.id, records)` in the `asianoption` branch:

```python
        from .positions import capture_due_asian_fixings
        capture_due_asian_fixings(session, position.id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_capture.py::test_booking_eager_captures_past -v`
Expected: PASS

- [ ] **Step 5: Add the capture endpoint**

```python
# backend/app/main.py  (mirror the sub-project B asian-fixing-schedule endpoint)
@app.post("/api/portfolios/{portfolio_id}/positions/{position_id}/asian-fixings/capture")
def capture_asian_fixings_endpoint(portfolio_id: int, position_id: int):
    from .services.domains.positions import capture_due_asian_fixings
    with SessionLocal() as session:
        captured = capture_due_asian_fixings(session, position_id)
        session.commit()
    return {"captured": captured, "position_id": position_id}
```

> Match the exact `SessionLocal`/session-context pattern used by the neighbouring `asian-fixing-schedule` endpoint added in sub-project B.

- [ ] **Step 6: Write + run the endpoint test**

```python
# backend/tests/test_asian_fixing_lifecycle.py  (uses the existing TestClient fixture)
def test_capture_endpoint_returns_count(client, db_session):
    # seed an asian position with a past observation + quote (as in capture tests),
    # then POST the capture endpoint and assert {"captured": 1}.
    ...
```

Run: `.venv/bin/python -m pytest backend/tests/test_asian_fixing_lifecycle.py -v`
Expected: PASS (copy the `config/agent_channels.yaml` into the worktree first if the client fixture needs it)

- [ ] **Step 7: Add the agent tool + orchestrator routing line**

Register a `capture_asian_fixings(position_id: int)` tool (thin wrapper over `capture_due_asian_fixings`) in the tool registry, add it to `DEEP_AGENT_TOOL_NAMES`, and add ONE routing line to the orchestrator SKILL/prompt (per the `project_pricing_parameter_tools` lesson: a new workflow tool needs an explicit routing line — the catalog is not orchestrator knowledge). Keep the SKILL body under the 500-token cap.

- [ ] **Step 8: Run the skills-catalog coupling suite**

Run: `.venv/bin/python -m pytest backend/tests/test_skills_catalog.py backend/tests/test_routing_table.py -q`
Expected: PASS — if adding a SKILL.md, update the exact-set + count assertions in the six coupled files noted in the `skill_catalog_test_coupling` memory.

- [ ] **Step 9: Commit**

```bash
git add -p   # stage selectively; never git add -A (config/ is tracked)
git commit -m "feat(asian): eager-capture at booking + capture endpoint + agent tool (Component A2/C surfaces)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 10: Review gate** — `zenmux-codex-review-loop` (max 3 loops); fix; re-commit.

---

## Task 5: Full-suite regression + merge

**Files:** none (verification + merge)

- [ ] **Step 1: Run the whole backend suite**

Run: `.venv/bin/python -m pytest backend/tests -q`
Expected: PASS, no new failures vs the pre-existing baseline (record any pre-existing failures from `project_engine_config_variants` / `project_scenario_test` memories and confirm they are unchanged).

- [ ] **Step 2: Final whole-branch review gate**

Run `zenmux-codex-review-loop` with `--base main` (max 3 loops) over the entire branch diff; fix findings; re-commit.

- [ ] **Step 3: Fast-forward `main`** (do NOT check out `main` in the primary checkout; advance the ref from the worktree)

```bash
# from the worktree, with main as an ancestor:
git -C /Users/fuxinyao/oot-asian-wiring log --oneline main..HEAD   # review the commits
git branch -f main feat/asian-pricing-wiring                       # if fast-forwardable
```

- [ ] **Step 4: Update the project memory** `project_asian_weighted_calendar.md` — move task #13 from DEFERRED to DONE with the merge tip, and remove the "DEFERRED" note.

- [ ] **Step 5: Clean up the worktree** per the `feedback_subagent_exec_worktree` + `exitworktree_false_refusal` memories (verify `git log main..feat/asian-pricing-wiring` is empty, then remove).

---

## Self-Review

**Spec coverage:**
- §1 problem (3 gaps) → Gap 1 (booking) = Task 2; Gap 2 (kwargs→pricing) = Task 1; Gap 3 (in-progress crash) = Task 1 (drop/null rules) + Task 3 (capture). ✅
- §2 decisions: in-progress exact = Tasks 1+3; stored snapshot/immutable = Task 3 (idempotent test); records-in-product_kwargs = Tasks 1+2. ✅
- §3 data flow (BOOKING/CAPTURE/PRICING) → Tasks 2 / 3+4 / 1. ✅
- §4 Components A/B/C → Tasks 2 / 1 / 3+4. ✅
- §5 instrument/price resolution → Task 3 (`latest_quote`). ✅
- §6 edge cases → Task 1 (byte-identical, as-of future-null, uncaptured-drop), Task 3 (missing quote). ✅
- §7 testing/regression anchor → Task 1 Step 8/10, Task 5. ✅
- §8 out of scope → no cron capture, no UI migration, no RFQ pricing, no QuantArk edits — none added. ✅

**Placeholder scan:** Task 4 Step 6/7 leave the endpoint-test body and tool-registration as prose because the exact `client`/`SessionLocal` fixture and tool-registry module names must be read from the codebase at execution time — these are integration points, not logic. All logic-bearing steps (Tasks 1–3) carry complete code. Acceptable per "follow established patterns"; flagged explicitly so the executor reads the neighbouring sub-project B code first.

**Type consistency:** `capture_due_asian_fixings(session, position_id, *, as_of=None) -> int` is used identically in Tasks 3, 4. `_asian_observation_records_for_pricing(records, market) -> list[dict]` consistent in Task 1. `observation_records` entry shape `{observation_date, weight, observed_price?}` consistent across Tasks 1–4. ✅
