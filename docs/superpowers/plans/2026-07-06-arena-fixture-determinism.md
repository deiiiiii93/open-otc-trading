# Arena Fixture Determinism Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the flagship arena workflow's producers (`run_batch_pricing`,
`run_greeks_landscape`, `run_scenario_test`, `run_backtest`) yield byte-identical
numbers across runs against a frozen seed, then harvest those numbers as canonical
truth and reconcile the replay transcript — so the ability-card reform (Spec B) can
score grounding against reproducible fixtures.

**Architecture:** Freeze a single `SEED_ACCOUNTING_DATE`. Add seed namespaces
(`market_quotes`, `spot_history`) to the golden-workflow fixture loader so the desk
resolves spot and backtest history from pinned data instead of live/default sources.
An **offline, clean-DB determinism test** drives all four producers twice and asserts
equality — it is the failing test that reveals which producers actually drift; we fix
only those. A harvester emits the canonical numbers; A5 rewrites the replay prose to
match.

**Tech Stack:** Python 3 / FastAPI / SQLAlchemy / pytest; QuantArk (deterministic
quant engine, cross-repo via `QUANTARK_PATH`); golden-workflow fixtures
(`app/golden_workflows/`).

## Global Constraints

- Tests run from `backend/` with `.venv/bin/python -m pytest` (repo convention).
- **Pinning is golden/arena-scoped only** — never change production desk
  wall-clock/live-market resolution. Isolation is a hard requirement (Spec A A3).
- Fixture truth values are **harvested from real tool payloads, never invented**
  (repo rule; Spec A A4).
- `SEED_ACCOUNTING_DATE = 2026-06-24` (matches the existing
  `pricing_profiles.valuation_date` and the flagship backtest end).
- Seed stale-run stays `created_at: 2026-06-22` (> 24h before the date) so step-1
  staleness is deterministic (Spec A A7).
- Do **not** add a `pricing_parameter_rows.spot` column — the resolver
  (`market_snapshot_for_position`) reads the quote store / fallback, not param rows
  (Spec A A2, Codex finding).
- The determinism test and harvester call the **producer services directly** (no
  LLM/agent) so the gate needs no API keys and stays offline.
- **Arena market data must be isolated from the shared desk store** (Codex
  critical). Seeded instruments/quotes/history are arena-owned: tagged
  `source="arena_seed"`, resolved only via arena positions' `underlying_id` (never
  by global symbol), purged per match, and **invisible to production quote
  resolution**. A pre-existing real `AAPL` must never read a seed 100.0 spot.
- **Comparison surface is canonical, not raw.** Producer equality compares only the
  numeric/tool-result subtrees with volatile metadata (`created_at`, `task_id`,
  `run_id`, any timestamp/uuid) stripped — freezing `valuation_date` does not freeze
  queued-run `created_at` (defaults to `utcnow`).
- **Grounding targets are underlying/alias-based, not `position_id`-based.** A clean
  determinism DB autoincrements position ids; harvest/truth key on
  `positions[underlying=AAPL]`, and Spec B's manifest path migrates from
  `[position_id=8]` to `[underlying=AAPL]` (the `_dig` `[key=value]` selector already
  supports it).

---

### Task 1: Freeze the accounting date + add the determinism test harness (failing)

Establish the frozen constant and a reusable seed→drive helper, then write the
offline determinism test. This test **is expected to fail or error initially** — its
failure is the audit that tells us which producers drift and where live fetches
happen. No producer code changes yet.

**Files:**
- Create: `backend/app/golden_workflows/determinism.py`
- Modify: `backend/app/golden_workflows/fixtures.py` (add `SEED_ACCOUNTING_DATE`)
- Test: `backend/tests/test_arena_fixture_determinism.py`

**Interfaces:**
- Produces:
  - `fixtures.SEED_ACCOUNTING_DATE: datetime` — the frozen valuation instant
    (`datetime(2026, 6, 24)`).
  - `determinism.seed_flagship(session) -> dict[str, dict[str, int]]` — loads the
    flagship `*.fixtures.json` via `load_fixtures` + `apply_seed`, returns the
    alias→id map.
  - `determinism.drive_producers(session, ids) -> dict[str, Any]` — calls the four
    producer services directly and returns a dict of the harvested payloads
    (`{"risk":..., "landscape":..., "scenario":..., "backtest":...}`).

- [ ] **Step 1: Add the frozen constant**

In `backend/app/golden_workflows/fixtures.py`, near the top imports (after the
existing `from datetime import datetime, timezone`):

```python
# Single frozen valuation instant for the golden/arena path. Every time- or
# market-dependent producer on this path resolves as-of this date so harvested
# fixture truth is reproducible (Spec A). NOT used by the production desk.
SEED_ACCOUNTING_DATE = datetime(2026, 6, 24)
```

- [ ] **Step 2: Write the seed/drive helper skeleton**

Create `backend/app/golden_workflows/determinism.py`:

```python
"""Deterministic seed + producer drive for the flagship arena workflow.

Calls producer SERVICES directly (no LLM) so the determinism gate runs offline.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.golden_workflows.fixtures import (
    SEED_ACCOUNTING_DATE,
    apply_seed,
    load_fixtures,
)
from app.golden_workflows.registry import flagship_fixtures_path


def seed_flagship(session) -> dict[str, dict[str, int]]:
    bundle = load_fixtures(flagship_fixtures_path())
    return apply_seed(bundle, session)


def drive_producers(session, ids: dict[str, dict[str, int]]) -> dict[str, Any]:
    portfolio_id = ids["portfolios"]["control"]
    profile_id = ids["pricing_profiles"]["prof"]
    out: dict[str, Any] = {}
    out["risk"] = _canonical(_drive_risk(session, portfolio_id, profile_id))
    out["landscape"] = _canonical(_drive_landscape(session, portfolio_id, profile_id))
    out["scenario"] = _canonical(_drive_scenario(session, portfolio_id, profile_id))
    out["backtest"] = _strict_backtest(_drive_backtest(session, portfolio_id))
    return out


# Volatile keys stripped before equality — freezing valuation_date does NOT freeze
# queued-run created_at (defaults to utcnow), task ids, or run ids (Codex [high]).
_VOLATILE_KEYS = {"created_at", "updated_at", "task_id", "run_id", "id",
                  "queued_at", "completed_at", "as_of"}


def _canonical(payload: Any) -> Any:
    """Recursively drop volatile metadata so equality compares only the numeric /
    tool-result subtree."""
    if isinstance(payload, dict):
        return {k: _canonical(v) for k, v in payload.items()
                if k not in _VOLATILE_KEYS}
    if isinstance(payload, list):
        return [_canonical(v) for v in payload]
    return payload


def _strict_backtest(payload: dict) -> dict:
    """Fail loudly on a hollow backtest — domains/backtest.py CATCHES per-underlying
    market-data prep failures and returns an empty 'completed' result, which would
    let the offline gate certify a backtest with all positions excluded (Codex
    [high]). Reject anything not fully completed with zero exclusions."""
    if payload.get("status") != "completed":
        raise AssertionError(f"backtest not completed: {payload.get('status')!r}")
    excluded = payload.get("excluded_positions") or []
    if excluded:
        raise AssertionError(f"backtest excluded positions (live-fetch masked?): {excluded}")
    return _canonical(payload)
```

Leave `_drive_risk` / `_drive_landscape` / `_drive_scenario` / `_drive_backtest` and
`flagship_fixtures_path` to Step 3 — implement them by reading the real producer
service signatures (`app/services/batch_pricing.py`, the greeks-landscape runner,
`app/services/scenario_test_runner.py`, `app/services/backtest_runner.py`) and
calling the same synchronous service each async tool wraps. Each returns the JSON
payload the corresponding `get_*_run` tool would surface. Confirm the exact key names
for backtest status / `excluded_positions` while reading `domains/backtest.py` and
adjust `_strict_backtest` to the real shape.

- [ ] **Step 3: Wire the four drive helpers + `flagship_fixtures_path`**

Read each producer service and implement the four `_drive_*` functions to call it
directly with `valuation`/`profile` pinned, plus add `flagship_fixtures_path()` to
`registry.py` returning the flagship `.fixtures.json` `Path`. Record the exact
service entrypoints used in a module docstring comment (this is the audit record).

- [ ] **Step 4: Write the offline determinism test (expected to fail)**

Create `backend/tests/test_arena_fixture_determinism.py`:

```python
import pytest
from app.golden_workflows.determinism import seed_flagship, drive_producers


@pytest.mark.offline
def test_producers_are_reproducible(offline_session_factory, block_network):
    """Drive the flagship producers twice from a clean DB, offline; the payloads
    must be byte-identical. `block_network` patches the market-data provider to
    raise so any live fetch fails the gate rather than leaking live data."""
    with offline_session_factory() as s1:
        ids1 = seed_flagship(s1)
        first = drive_producers(s1, ids1)
    with offline_session_factory() as s2:
        ids2 = seed_flagship(s2)
        second = drive_producers(s2, ids2)
    assert first == second, "producers drifted across identical seeds"
```

- [ ] **Step 5: Add the `offline_session_factory` + `block_network` fixtures**

In `backend/tests/conftest.py` (or a new `tests/fixtures_determinism.py` imported
there), add `offline_session_factory` (fresh in-memory/temp-file SQLite with schema
created, one clean DB per call) and `block_network` (monkeypatch the AkShare/quote
fetch entrypoints used by `ensure_spot_history` and any quote refresh to raise
`RuntimeError("network disabled in determinism gate")`). Read
`app/services/backtest_market_history.py` to find the exact fetch function name to
patch.

- [ ] **Step 6: Run the test — capture which producers drift/fetch**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -v`
Expected: FAIL or ERROR. **Record in the commit body** exactly how it fails per
producer (equality mismatch vs `RuntimeError` from `block_network`) — this is the
audit output that scopes Tasks 3–4 below. A producer that already passes needs no
fix.

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/determinism.py backend/app/golden_workflows/fixtures.py backend/app/golden_workflows/registry.py backend/tests/test_arena_fixture_determinism.py backend/tests/conftest.py
git commit -m "test(arena): offline determinism gate for flagship producers (failing audit)"
```

---

### Task 2: Add `market_quotes` + `spot_history` seed namespaces

Give the fixture loader the ability to seed pinned market data. This is the seam the
fix tasks depend on. Wire schema + loader + FK validation, matching the existing
namespace pattern in `fixtures.py`.

**Files:**
- Modify: `backend/app/golden_workflows/fixtures.py:36-47` (`_NAMESPACES`, `_FK`,
  `_INSERT_ORDER`) and the `apply_seed` dispatch (`:179-274`)
- Test: `backend/tests/test_golden_workflow_fixtures.py`

**Interfaces:**
- Consumes: `models.MarketQuote` (`instrument_id`, `as_of`, `price`, `price_type`,
  `source`), the instruments table, and the backtest history model discovered in
  Task 1 Step 5.
- Produces: two new seed namespaces usable in `*.fixtures.json`:
  - `market_quotes`: `{alias, instrument, as_of, price}` (FK `instrument →
    instruments`)
  - `spot_history`: `{alias, instrument, as_of, price}` (bulk daily series)
  Positions gain an optional `underlying` → instrument link so
  `_quote_spot_for_position` can resolve `underlying_id`.

- [ ] **Step 1: Write the failing loader test**

```python
def test_market_quotes_namespace_seeds_quote(tmp_path):
    bundle = load_fixtures(_write_fixture(tmp_path, {
        "instruments": [{"alias": "aapl", "symbol": "AAPL"}],
        "market_quotes": [{"alias": "q1", "instrument": "aapl",
                           "as_of": "2026-06-24", "price": 100.0}],
    }))
    with _session() as s:
        ids = apply_seed(bundle, s)
        assert ids["market_quotes"]["q1"] > 0
        q = s.get(models.MarketQuote, ids["market_quotes"]["q1"])
        assert q.price == 100.0
```

- [ ] **Step 2: Run it — verify it fails**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py::test_market_quotes_namespace_seeds_quote -v`
Expected: FAIL — `UnknownSeedNamespaceError: instruments` (or `market_quotes`).

- [ ] **Step 3: Register the namespaces**

In `fixtures.py`, extend `_NAMESPACES`:

```python
    "instruments": {"alias", "symbol"},
    "market_quotes": {"alias", "instrument", "as_of", "price"},
    "spot_history": {"alias", "instrument", "as_of", "price"},
```

extend `_FK`:

```python
    "market_quotes": {"instrument": "instruments"},
    "spot_history": {"instrument": "instruments"},
```

and `_INSERT_ORDER` (instruments before anything referencing them; quotes/history
last):

```python
_INSERT_ORDER = [
    "instruments", "portfolios", "reports", "pricing_profiles",
    "pricing_parameter_rows", "rfqs", "positions", "risk_runs",
    "market_quotes", "spot_history",
]
```

- [ ] **Step 4: Implement the `apply_seed` branches**

In `apply_seed`, add branches (mirroring the existing ones) that construct
`models.Instrument`, `models.MarketQuote`, and the backtest-history model. Parse
`as_of` ISO strings to `datetime` exactly as the `risk_runs.created_at` branch does
(`:257-263`). Resolve the `instrument` FK via `_parent_id("instruments", ...)`. Read
`models.Instrument` and the history model for their required columns; set
`price_type="close"`, `source="seed"` on quotes.

- [ ] **Step 5: Link seeded positions to their instrument**

Extend the `positions` branch so a position row carrying `underlying` also sets
`underlying_id` when a matching seeded instrument alias exists (add an optional
`underlying_instrument` alias field to the positions namespace, FK to `instruments`).
This is what lets `_quote_spot_for_position` find the seeded quote. Add a test
asserting a seeded position resolves `underlying_id`.

- [ ] **Step 6: Run the tests — verify they pass**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/fixtures.py backend/tests/test_golden_workflow_fixtures.py
git commit -m "feat(arena): market_quotes + spot_history seed namespaces"
```

---

### Task 3: Arena market-data isolation + pin spot for risk/landscape/scenario

**Isolation first (Codex critical), then pinning.** Seeded market data must never be
readable by the production desk. Then wire the three non-backtest producers to the
seed. If the audit (Task 1 Step 6) showed a producer already deterministic (risk via
`batch_pricing.py:174-186` profile valuation_date), **add a regression assertion but
no code change** — do not "fix" deterministic code.

**Files:**
- Modify: `backend/app/services/quotes.py` (`latest_quote` — exclude arena-seed rows
  from production resolution)
- Modify: `backend/app/services/arena/runner.py` (extend the per-match purge to
  arena-owned instruments/quotes/history)
- Modify (only as the audit requires): `backend/app/services/batch_pricing.py`,
  the greeks-landscape runner, `backend/app/services/scenario_test_runner.py`
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json`
- Test: `backend/tests/test_arena_fixture_determinism.py`,
  `backend/tests/test_arena_runner.py`

**Interfaces:**
- Consumes: Task 2 seed namespaces; `SEED_ACCOUNTING_DATE`;
  `market_snapshot_for_position` (`quantark.py:1415`), `_quote_spot_for_position` →
  `latest_quote` (`quotes.py`), `batch_pricing` profile-valuation threading
  (`:174-186`), the arena purge in `runner.py`.

- [ ] **Step 1: Write the isolation regression test FIRST (failing)**

```python
def test_seed_quote_never_leaks_to_real_instrument(offline_session_factory):
    """A pre-existing REAL AAPL instrument+quote must not be shadowed by, and must
    not read, an arena_seed 100.0 quote."""
    with offline_session_factory() as s:
        real = models.Instrument(symbol="AAPL"); s.add(real); s.flush()
        s.add(models.MarketQuote(instrument_id=real.id, as_of=SEED_ACCOUNTING_DATE,
                                 price=187.5, source="akshare")); s.commit()
        seed_flagship(s)  # seeds arena_seed AAPL @ 100.0 as a SEPARATE instrument
        from app.services.quotes import latest_quote
        q = latest_quote(s, real.id, as_of=SEED_ACCOUNTING_DATE)
        assert q.price == 187.5, "production resolution read an arena_seed quote"
```

- [ ] **Step 2: Run it — verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py::test_seed_quote_never_leaks_to_real_instrument -v`
Expected: FAIL (arena instrument collides on unique symbol, or resolution reads seed).

- [ ] **Step 3: Isolate arena market data**

Three changes, then re-run Step 1 to green:
  1. **Scoped symbol, no collision:** arena instruments seed with a namespaced symbol
     (e.g. `"AAPL"` → stored `"AAPL#arena"`) OR the `instruments` seed branch upserts
     by `(symbol, source='arena_seed')`. Arena positions link by `underlying_id`
     (Task 2 Step 5), so the display `underlying` string stays `"AAPL"` while the row
     is a distinct arena-owned instrument. Confirm `Instrument` has a `source`/origin
     column or add one via the seed metadata; if none exists, use the namespaced
     symbol approach (no migration).
  2. **Production resolution ignores seed rows:** `latest_quote` filters out
     `MarketQuote.source == "arena_seed"` by default (add a param the arena drive
     passes to opt back in). This guarantees the real-AAPL test passes even if a
     stray seed quote shares an instrument.
  3. **Purge:** extend `runner.py`'s per-match cleanup to delete arena-owned
     instruments/quotes/spot_history for the match, mirroring the existing
     RFQ/portfolio purge. Add a reseed test: seeding twice does not raise and leaves
     no accumulation.

- [ ] **Step 4: Seed the pinned quotes in the flagship fixture**

Add to `risk-manager-control-day.fixtures.json` `seed` (all quotes/history carry the
arena-seed origin per Step 3):

```json
"instruments": [
  {"alias": "aapl", "symbol": "AAPL"},
  {"alias": "tsla", "symbol": "TSLA"},
  {"alias": "nvda", "symbol": "NVDA"}
],
"market_quotes": [
  {"alias": "q-aapl", "instrument": "aapl", "as_of": "2026-06-24", "price": 100.0},
  {"alias": "q-tsla", "instrument": "tsla", "as_of": "2026-06-24", "price": 100.0},
  {"alias": "q-nvda", "instrument": "nvda", "as_of": "2026-06-24", "price": 100.0}
]
```

and add `"underlying_instrument": "aapl"` (etc.) to each position row so
`underlying_id` resolves.

- [ ] **Step 5: Add the seeded-source assertion (A2 proof)**

Extend the determinism test: after `drive_producers`, assert the AAPL risk row's
`spot == 100.0` AND that it came from the seeded quote (drive once with the quote
seeded, once with it removed so the fallback default differs) — proving the seeded
quote is the *source used*, not merely present.

- [ ] **Step 6: Apply the minimal per-producer fix the audit requires**

For each producer the audit flagged: thread `SEED_ACCOUNTING_DATE` / profile
valuation as the `valuation_date` into its risk-engine call, and confirm it does not
hit `risk_engine.py:434`'s `utcnow()` no-profile path. If the greeks landscape runner
ignores the profile and defaults r/q/vol, pass the Control Profile (or
`SEED_ACCOUNTING_DATE`) explicitly on the golden path only. Show the exact edited
lines in the commit.

- [ ] **Step 7: Run the determinism + isolation tests**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py tests/test_arena_runner.py -v`
Expected: risk/landscape/scenario equality + isolation + reseed pass; backtest may
still fail (Task 4). If backtest still fails, mark it xfail with a Task-4 comment.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json backend/tests
git commit -m "feat(arena): isolate arena market data + pin spot/valuation for risk/landscape/scenario"
```

---

### Task 4: Freeze the backtest's historical inputs (Codex [high])

Make `run_backtest` reproducible: seed the spot-history series it consumes over the
flagship window and ensure no live fetch fires on the golden path.

**Files:**
- Modify: `backend/app/services/backtest_market_history.py` (guard/seed seam),
  `backend/app/services/domains/backtest.py`
- Modify: `risk-manager-control-day.fixtures.json` (`spot_history` rows)
- Test: `backend/tests/test_arena_fixture_determinism.py`

**Interfaces:**
- Consumes: `ensure_spot_history` (the live-fetch entrypoint found in Task 1 Step 5),
  the `spot_history` seed namespace (Task 2), `SEED_ACCOUNTING_DATE`.

- [ ] **Step 1: Seed the history series**

Add `spot_history` rows to the flagship fixture covering trading days
`2026-03-24 → 2026-06-24` per underlying. Generate a deterministic synthetic series
(e.g. a fixed price path) so `ensure_spot_history` finds complete stored data and
skips fetching. Keep the series small but gap-free over the 63 trading days the
existing backtest reports.

- [ ] **Step 2: Confirm the offline guard trips without the seed (strict)**

With `block_network` active and the `spot_history` seed **removed**, driving the
backtest must FAIL — either `ensure_spot_history` raises `RuntimeError("network
disabled…")` and it propagates, or (because `domains/backtest.py` swallows the
prep failure into an empty "completed" result) `_strict_backtest` raises
`AssertionError` on the resulting `excluded_positions` / non-completed status. Assert
with `pytest.raises((RuntimeError, AssertionError))`. This is the guard against
certifying a hollow backtest (Codex [high]).

- [ ] **Step 3: Make futures end-date resolution deterministic**

If the audit showed futures-chain resolution uses wall-clock (Codex finding), pin its
effective end date to `SEED_ACCOUNTING_DATE` on the golden path. Read
`domains/backtest.py` for the resolution point; show the edited lines. If the flagship
book (AAPL/TSLA/NVDA options, no futures hedge) never triggers futures resolution,
document that and skip — but assert it in the test.

- [ ] **Step 4: Run the full determinism test — all four match, offline**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -v`
Expected: PASS — all four producers byte-identical across two clean-DB offline runs.
Remove any Task 3 xfail.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/backtest_market_history.py backend/app/services/domains/backtest.py backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json backend/tests/test_arena_fixture_determinism.py
git commit -m "feat(arena): seed backtest history so run_backtest is offline-reproducible"
```

---

### Task 5: Harvest canonical truth values

Emit the reproducible numbers into a committed artifact Spec B will consume as
`response_quotes_value` targets.

**Files:**
- Create: `backend/app/golden_workflows/harvest_fixtures.py`
- Create: `backend/app/golden_workflows/definitions/risk-manager-control-day.truth.json`
- Test: `backend/tests/test_arena_fixture_determinism.py`

**Interfaces:**
- Consumes: `determinism.seed_flagship`, `determinism.drive_producers`.
- Produces: `harvest_fixtures.harvest() -> dict` writing `*.truth.json` with the
  canonical grounding targets keyed by **underlying/alias-based** paths (not
  `position_id`, which is not stable in a clean DB — Codex [medium]):
  `metrics.positions[underlying=AAPL].delta`,
  `results.portfolio.raw[spot_shift_pct=10.0].gamma`,
  `results.portfolio.raw[spot_shift_pct=-20.0].delta`, `results.var_cvar.cvar`, and
  the backtest headline P&L. (Spec B's manifest migrates `[position_id=8]` →
  `[underlying=AAPL]` accordingly.)

- [ ] **Step 1: Write the harvester**

`harvest()` seeds the frozen state, drives the producers, and `_dig`s each target
path out of the real payloads (reuse `assertions._dig`, whose `[key=value]` selector
resolves `[underlying=AAPL]`), writing them to `*.truth.json`. Values come **only**
from the payloads. If any target path fails to resolve from the live payload,
`harvest()` raises — a target that cannot be grounded is a bug, not an empty value.

- [ ] **Step 2: Assert harvest == determinism payloads**

Test: `harvest()` output equals the values dug from `drive_producers`, and re-running
`harvest()` produces an identical file (idempotent, A6).

- [ ] **Step 3: Generate and commit the truth file**

Run: `.venv/bin/python -m app.golden_workflows.harvest_fixtures`
Then verify the file, and run the test.

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -v`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/app/golden_workflows/harvest_fixtures.py backend/app/golden_workflows/definitions/risk-manager-control-day.truth.json backend/tests/test_arena_fixture_determinism.py
git commit -m "feat(arena): harvest canonical grounding truth values from frozen seed"
```

---

### Task 6: Reconcile the replay transcript with harvested truth (A5)

Rewrite the internally-inconsistent replay prose/report numbers so the canned
golden-replay transcript quotes the same values as the payload paths and the harvested
truth. Keep the golden-replay regression at full marks.

**Files:**
- Modify: `backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json`
  (the `replay` block: step-3 prose, step-4 landscape response, step-7 report artifact)
- Test: `backend/tests/test_golden_workflow_regression.py`

**Interfaces:**
- Consumes: `*.truth.json` (Task 5) — the numbers to write into the prose.

- [ ] **Step 1: Identify every inconsistent number**

Diff the replay prose/report against `*.truth.json`. The known offenders:
step-3 response says AAPL delta `573.35`(payload) but report/README say `-148,000`;
step-4 response says delta `-248,500` while the grid has `860.47`; report says
`gamma@+10% -9,600` vs grid `16.403`; `delta@-20% -310,000` vs grid `391.19`.

- [ ] **Step 2: Rewrite prose to the harvested numbers**

Edit the `response_text` of `step-3-read-fresh-risk`, `step-4-greeks-landscape`, and
the `artifacts[0].content` + `response_text` of `step-7-create-report` so every quoted
figure matches `*.truth.json` (AAPL delta, gamma@+10%, delta@-20%, CVaR magnitude,
backtest P&L). Keep the required keyword tokens (`AAPL`, `stale`, `cvar`, `backtest`).

- [ ] **Step 3: Run the golden-replay regression — full marks**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_regression.py -v`
Expected: PASS — replay still earns 39/39 (self-grounding checks now agree with the
reconciled prose; no manifest scoring change in this spec).

- [ ] **Step 4: Run the full arena + golden test set**

Run: `.venv/bin/python -m pytest tests/test_arena_scoring.py tests/test_golden_workflow_regression.py tests/test_golden_workflow_assertions.py tests/test_flagship_loads.py tests/test_arena_fixture_determinism.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/definitions/risk-manager-control-day.fixtures.json
git commit -m "fix(arena): reconcile replay transcript numbers with harvested truth"
```

---

### Task 7: Docs

**Files:**
- Modify: `CHANGELOG.md` (under `[Unreleased]`)
- Modify: `CLAUDE.md` (Golden workflows section — the determinism gotcha)

- [ ] **Step 1: CHANGELOG entry**

Add under `[Unreleased]` → an `Added`/`Changed` bullet describing the frozen
`SEED_ACCOUNTING_DATE`, the `market_quotes`/`spot_history` seed namespaces, the
offline determinism gate, and the harvested `*.truth.json` (noting Spec B will consume
it).

- [ ] **Step 2: CLAUDE.md gotcha**

Add to the Golden-workflows section: producers on the golden/arena path resolve spot
from seeded `MarketQuote` and backtest history from seeded `spot_history`; the
determinism gate runs clean-DB + offline; re-harvest via `harvest_fixtures` after any
QuantArk numeric change rather than hand-editing truth values.

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md CLAUDE.md
git commit -m "docs(arena): document fixture determinism gate + seed namespaces"
```

---

## Self-Review

**Spec coverage:**
- A1 (`SEED_ACCOUNTING_DATE`) → Task 1 Step 1. ✓
- A2 (spot via real resolver / seeded `MarketQuote`, no param-row column) → Tasks 2–3. ✓
- A2b (backtest historical inputs frozen) → Task 4. ✓
- A3 (golden-scoped injection, no production change) → Global Constraints + Task 3
  Step 3 (golden-path-only edits). ✓
- A4 (harvest from real payloads) → Task 5. ✓
- A5 (reconcile replay) → Task 6. ✓
- A6 (clean-DB offline determinism gate) → Task 1 + Task 4 Step 2. ✓
- A7 (staleness stays honest) → Global Constraints (seed `created_at` unchanged). ✓
- Seeded-source proof → Task 3 Step 5. ✓

**Codex plan-review fixes applied:**
- [critical] Arena market-data isolation (leak to shared store) → Global Constraint +
  Task 3 Steps 1–3 (scoped/tagged instruments, `latest_quote` excludes `arena_seed`,
  per-match purge, real-AAPL + reseed regression tests). ✓
- [high] Swallowed backtest guard → `_strict_backtest` in `drive_producers` +
  Task 4 Step 2 (`pytest.raises((RuntimeError, AssertionError))`). ✓
- [high] Volatile `created_at` in equality → `_canonical` strips `_VOLATILE_KEYS`
  before comparison (Global Constraint + Task 1). ✓
- [medium] Unstable `position_id=8` → underlying/alias-based targets
  (`[underlying=AAPL]`) + harvest raises on unresolved path (Global Constraint +
  Task 5). ✓

**Placeholder scan:** Steps that defer detail (Task 1 Step 3/5, Task 3 Step 6, Task 4
Step 3) are **audit-gated by design** — the failing determinism test dictates the
exact seam, and each such step names the real file/function to read and the exact fix
pattern. This is deliberate for a determinism-hardening task, not a hidden TODO.

**Type consistency:** `SEED_ACCOUNTING_DATE`, `seed_flagship`, `drive_producers`,
`_canonical`, `_strict_backtest`, `flagship_fixtures_path`, `harvest`, the
`market_quotes`/`spot_history`/`instruments` namespaces, the `arena_seed` source tag,
and the `*.truth.json` underlying-based path names are used consistently across
Tasks 1–7.
