# Trader RFQ→Booking Day — Flagship Arena Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring `trader-rfq-booking-day` to the flagship `risk-manager-control-day`
discrimination-benchmark standard — harvested-truth grounding, persisted-output correctness
binds, `par_tool_calls` golf-scoring, a write-free trap — so it is Model-Ability-Card gradable.

**Architecture:** Generalize the flagship-hardcoded determinism/harvest harness into a
per-workflow registry (shared infra, also serving sub-project 2). Add a trader-rfq determinism
entry that replays the **live `quote_rfq` path** on a pinned market snapshot to harvest one
grounding number. Rewrite the manifest so correctness binds to persisted tool outputs
(`tool_result_path` on `build_product`/`get_position_summaries`/valuations), with `record_answer`
structured checks layered on as presentation. Add a write-free build-validation trap. Regenerate
the golden replay to carry harvested values and earn full marks.

**Tech Stack:** Python 3.11, SQLAlchemy, pytest, QuantArk pricing. Arena scoring in
`backend/app/services/arena/`; golden workflows in `backend/app/golden_workflows/`. Run tests
with `.venv/bin/python -m pytest` from the repo root.

## Global Constraints

- Tests run from **repo root**: `.venv/bin/python -m pytest tests/…`. Backend import root is
  `backend/` (configured via pytest `pythonpath`).
- **Do not touch** `backend/app/services/arena/scoring.py` — the card/par kernel is already
  workflow-agnostic (`designed_par` reads `par_tool_calls`; axes derive from `_AXIS_BY_TYPE`).
- Assertion field names are **exact** (from `golden_workflows/schema.py`): `tool_called`
  supports `args`, `args_any_of`, `exclusive_keys`, `all_calls`, `max_calls`; `tool_result_path`
  needs **exactly one** comparator (`equals` | `gte` | `lte` | `is_not_null: true`);
  `answer_field_quotes` = `{field, value, rel_tol=0.02, match}`; `answer_field_equals` =
  `{field, equals|any_of}`; `response_quotes_tool_value` = `{tool, path, rel_tol, scope, match, near}`.
- `tool_result_path` list selectors use `[key=value]` syntax (e.g.
  `positions[underlying=MSFT].barrier`), same engine as the flagship
  `positions[underlying=AAPL].delta`.
- The golden replay bundle format per step key is `{ai:{content,tool_calls}, tool_results:[{tool_call_id,name,content}], skills_routed:[], artifacts:[], response_text}` — assertions score against `tool_results` + `response_text`, **not** live tool args.
- Harvested truth values are **read from real payloads, never invented** (re-run the harvester;
  don't hand-edit `*.truth.json`).
- Constant IDs: `TRADER_RFQ_ID = "trader-rfq-booking-day"`, `FLAGSHIP_ID = "risk-manager-control-day"`.
- Seeded trader-rfq aliases (from `trader-rfq-booking-day.fixtures.json`): portfolio `desk`
  ("Arena Trader Desk"), pricing profile `prof` ("Arena Trader Profile", `valuation_date`
  `2026-06-29`), MSFT pricing params `rate=0.04, dividend_yield=0.01, volatility=0.28`.
- **Quote grounding uses `quote_mode="price"`, harvesting `quote_payload.achieved_price`** — the
  option's model price (the premium). In solve mode `solved_value` defaults to a solved **strike**
  (`RFQUnknownSpecIn.field_path = "strike"`), which is an input here, not a groundable output. Since
  every RFQ term is specified, pricing the structure *is* the quote. `_fixed_price_quote_payload`
  (price mode) emits `achieved_price` = `target_value`; the `quote_rfq` **tool** exposes it via
  `shape_rfq(rfq)["quote_payload"]`, so the assertion path is `quote_payload.achieved_price`.
- **Net-delta grounding (step 8) reads `get_latest_risk_run`**, path
  `metrics.positions[underlying=MSFT].delta` — `get_latest_position_valuations` (`shape_valuation_results`)
  does **not** expose per-position delta; `run_batch_pricing` produces a RiskRun whose metrics do
  (the flagship's proven `positions[underlying].delta` surface).
- **build_product barrier bind path is `product_kwargs.barrier_type`** (top-level in the tool
  output; `_build_barrier` sets `out.product_kwargs["barrier_type"]`), not `product_spec.terms.*`.
- **Stateful writes (build/book/price) can't bind literal seeded ids** — ids are dynamic per run and
  the golden replay is canned. Enforce integrity via `max_calls: 1` (blocks duplicate/​repeat
  execution) + result **terms** binds (underlying + barrier_type + strike, so a wrong-direction or
  wrong-product booking fails) + `task_returned_id`. Document this as the enforceable-within-engine
  control; per-arg id binding is a known engine limitation shared with the flagship.
- End every commit message with the repo's Co-Authored-By trailer.

---

### Task 1: Generalize the determinism harness into a per-workflow registry

Refactor `determinism.py` so the flagship is one entry in a registry and new workflows plug in,
**behaviour-preserving for the flagship** (existing flagship determinism/harvest tests stay green).

**Files:**
- Modify: `backend/app/golden_workflows/determinism.py`
- Test: `tests/test_arena_fixture_determinism.py` (existing flagship tests must still pass unchanged)

**Interfaces:**
- Consumes: existing `seed_flagship(session) -> ids`, `drive_producers(session, ids)`,
  `_require_complete`, `_require_priced`, `_no_async_dispatch`, `_drive_risk/_landscape/_scenario/_backtest`.
- Produces:
  - `@dataclass(frozen=True) ProducerDriver(fn, validate)` where
    `fn: Callable[[Session, dict], tuple[run, dict]]` and
    `validate: Callable[[Any, dict], dict]` returns the canonical payload or raises
    (so each producer brings its own completion predicate — the flagship's task-run
    validator, the RFQ quote's status/price validator, etc.).
  - `@dataclass(frozen=True) WorkflowDeterminism(workflow_id, seed_fn, drivers: dict[str, ProducerDriver])`.
  - `DETERMINISM_REGISTRY: dict[str, WorkflowDeterminism]` (flagship registered).
  - `seed_workflow(session, workflow_id) -> ids` and
    `drive_producers(session, ids, *, workflow_id=FLAGSHIP_ID) -> dict[str, Any]`
    (default keeps every existing caller working).
  - **`_drive_risk/_landscape/_scenario/_backtest` keep their existing
    `(session, portfolio_id, profile_id)` signatures** (the offline-guard test calls
    `_drive_backtest` with three args directly); the registry wraps them in
    `(session, ids)` adapters.

- [ ] **Step 1: Write the failing test** — registry drives the flagship identically to the legacy path.

Add to `tests/test_arena_fixture_determinism.py`:

```python
def test_registry_flagship_matches_legacy_drive(offline_session_factory, block_network):
    from app.golden_workflows.determinism import (
        DETERMINISM_REGISTRY, FLAGSHIP_ID, seed_workflow, drive_producers,
    )
    assert FLAGSHIP_ID in DETERMINISM_REGISTRY
    with offline_session_factory() as s:
        via_registry = drive_producers(s, seed_workflow(s, FLAGSHIP_ID),
                                       workflow_id=FLAGSHIP_ID)
    assert set(via_registry) == {"risk", "landscape", "scenario", "backtest"}
    assert via_registry["risk"]["positions"]  # non-empty priced payload
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py::test_registry_flagship_matches_legacy_drive -v`
Expected: FAIL — `ImportError: cannot import name 'DETERMINISM_REGISTRY'`.

- [ ] **Step 3: Implement the registry (behaviour-preserving)**

In `determinism.py`, add near the top imports:

```python
from dataclasses import dataclass
from functools import partial
from typing import Callable
```

Replace the existing `drive_producers` with the registry machinery. **Keep `_drive_risk`,
`_drive_landscape`, `_drive_scenario`, `_drive_backtest`, `_require_complete`, `_require_priced`,
`_no_async_dispatch`, `seed_flagship`, `seed_backtest_history` EXACTLY as they are** — the `_drive_*`
functions keep their `(session, portfolio_id, profile_id)` signatures so the offline-guard test's
direct three-arg call still works:

```python
@dataclass(frozen=True)
class ProducerDriver:
    # fn(session, ids) -> (run, payload); validate(run, payload) -> canonical payload (or raises)
    fn: Callable[["Session", dict], tuple]
    validate: Callable[[object, dict], dict]


@dataclass(frozen=True)
class WorkflowDeterminism:
    workflow_id: str
    seed_fn: Callable[["Session"], dict]
    drivers: dict  # name -> ProducerDriver


def _flagship_ids(ids: dict) -> tuple:
    return ids["portfolios"]["control"], ids["pricing_profiles"]["prof"]


# (session, ids) adapters over the unchanged 3-arg _drive_* functions.
def _adapt_risk(session, ids):
    return _drive_risk(session, *_flagship_ids(ids))

def _adapt_landscape(session, ids):
    return _drive_landscape(session, *_flagship_ids(ids))

def _adapt_scenario(session, ids):
    return _drive_scenario(session, *_flagship_ids(ids))

def _adapt_backtest(session, ids):
    return _drive_backtest(session, *_flagship_ids(ids))


def _validate_task_run(run, payload, *, kind, needs, priced=False):
    if priced:
        payload = _require_priced(payload)
    return _require_complete(run, payload, kind=kind, needs=needs)


_FLAGSHIP_DETERMINISM = WorkflowDeterminism(
    workflow_id=FLAGSHIP_ID,
    seed_fn=seed_flagship,
    drivers={
        "risk": ProducerDriver(_adapt_risk,
            partial(_validate_task_run, kind="risk", needs="positions", priced=True)),
        "landscape": ProducerDriver(_adapt_landscape,
            partial(_validate_task_run, kind="landscape", needs="portfolio")),
        "scenario": ProducerDriver(_adapt_scenario,
            partial(_validate_task_run, kind="scenario", needs="var_cvar")),
        "backtest": ProducerDriver(_adapt_backtest,
            partial(_validate_task_run, kind="backtest", needs="by_underlying")),
    },
)

DETERMINISM_REGISTRY: dict = {FLAGSHIP_ID: _FLAGSHIP_DETERMINISM}


def seed_workflow(session, workflow_id: str) -> dict:
    return DETERMINISM_REGISTRY[workflow_id].seed_fn(session)


def drive_producers(session, ids: dict, *, workflow_id: str = FLAGSHIP_ID) -> dict:
    """Drive a workflow's producers synchronously; each driver's own validator
    gates its payload before it is trusted. Default workflow_id keeps every
    existing caller (harvester, flagship determinism tests) working unchanged."""
    wd = DETERMINISM_REGISTRY[workflow_id]
    out: dict = {}
    with _no_async_dispatch():
        for key, drv in wd.drivers.items():
            run, payload = drv.fn(session, ids)
            out[key] = drv.validate(run, payload)
    return out
```

The `partial(_validate_task_run, ...)` calls reproduce the exact old behaviour
(`_require_priced` then `_require_complete`), so the flagship path is byte-identical.

- [ ] **Step 4: Run the full flagship determinism suite to verify green**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -v`
Expected: PASS — all existing flagship tests (`test_producers_are_reproducible`,
`test_harvest_matches_payloads_and_is_idempotent`, …) plus the new registry test.

- [ ] **Step 5: Verify the harvester still works (it calls `drive_producers(session, seed_flagship(session))`)**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -k harvest -v`
Expected: PASS (default `workflow_id=FLAGSHIP_ID` preserves the old call).

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/determinism.py tests/test_arena_fixture_determinism.py
git commit -m "refactor(arena): per-workflow determinism registry (flagship-preserving)"
```

---

### Task 2: Trader-RFQ determinism entry + harvest `trader-rfq-booking-day.truth.json`

Add the trader-rfq registry entry that replays the **live `quote_rfq` path** on a pinned market
snapshot, harvest the single grounding number, and prove it is coupled to the live path.

**Files:**
- Modify: `backend/app/golden_workflows/determinism.py` (add `_seed_trader_rfq`, `_drive_quote_rfq`, register)
- Modify: `backend/app/golden_workflows/harvest_fixtures.py` (generalize to per-workflow)
- Create: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.truth.json` (harvested output)
- Test: `tests/test_arena_fixture_determinism.py`

**Interfaces:**
- Consumes: Task 1's `WorkflowDeterminism`, `ProducerDriver`, `DETERMINISM_REGISTRY`,
  `seed_workflow`, `drive_producers`; `app.golden_workflows.fixtures.apply_seed`;
  `app.golden_workflows.registry.get_workflow_bundle`; `app.services.rfq` (`create_or_update_rfq_draft`
  / `quote_rfq` service functions); `app.schemas.PricingEnvironmentSnapshot`.
- Produces: `TRADER_RFQ_ID` constant; `DETERMINISM_REGISTRY[TRADER_RFQ_ID]`; a generalized
  `harvest_for(session, workflow_id) -> dict` and `write_truth_file(workflow_id)` in the harvester.

- [ ] **Step 1: Write the failing determinism + parity test**

Add to `tests/test_arena_fixture_determinism.py`:

```python
def test_trader_rfq_quote_is_reproducible(offline_session_factory, block_network):
    from app.golden_workflows.determinism import (
        TRADER_RFQ_ID, seed_workflow, drive_producers,
    )
    with offline_session_factory() as s1:
        first = drive_producers(s1, seed_workflow(s1, TRADER_RFQ_ID), workflow_id=TRADER_RFQ_ID)
    with offline_session_factory() as s2:
        second = drive_producers(s2, seed_workflow(s2, TRADER_RFQ_ID), workflow_id=TRADER_RFQ_ID)
    assert first == second, "trader-rfq quote drifted across identical seeds"
    assert isinstance(first["quote"]["achieved_price"], (int, float))


def test_trader_rfq_quote_tracks_spot(offline_session_factory, block_network):
    """Parity: the harvested number is coupled to the live quote inputs, not hand-built.
    Changing the pinned spot must change the harvested achieved_price."""
    from app.golden_workflows.determinism import (
        TRADER_RFQ_ID, seed_workflow, _drive_quote_rfq,
    )
    with offline_session_factory() as s:
        ids = seed_workflow(s, TRADER_RFQ_ID)
        _, base = _drive_quote_rfq(s, ids)
    with offline_session_factory() as s:
        ids = seed_workflow(s, TRADER_RFQ_ID)
        _, bumped = _drive_quote_rfq(s, ids, spot=120.0)
    assert base["achieved_price"] != bumped["achieved_price"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -k trader_rfq -v`
Expected: FAIL — `ImportError: cannot import name 'TRADER_RFQ_ID'`.

- [ ] **Step 3: Implement the trader-rfq seed + quote driver**

In `determinism.py` add:

```python
TRADER_RFQ_ID = "trader-rfq-booking-day"

# Pinned market for the MSFT down-and-in barrier put quote. Spot at strike (ATM=100)
# per the workflow prompt; rate/div/vol mirror the seeded Arena Trader Profile MSFT row
# so the harvested price equals what a live "quote using the Arena Trader Profile" price
# on an ATM draft produces.
_TRADER_RFQ_SPOT = 100.0
_MSFT_RATE, _MSFT_DIV, _MSFT_VOL = 0.04, 0.01, 0.28


def _seed_trader_rfq(session) -> dict:
    ids = apply_seed(get_workflow_bundle(TRADER_RFQ_ID).fixtures, session)
    session.commit()
    return ids


def _drive_quote_rfq(session, ids, *, spot: float = _TRADER_RFQ_SPOT):
    """Replay the LIVE quote_rfq PRICE path on a deterministic MSFT down-in barrier
    put draft; return (rfq, {'achieved_price', 'engine'}). Price mode (not solve —
    solve returns a solved *strike*, an input here). The market snapshot is pinned,
    so the price is byte-deterministic offline (no live fetch)."""
    from app.services import rfq as rfq_svc
    from app.schemas import RFQRequestDraft

    draft = RFQRequestDraft.model_validate({
        "client_name": "ARENA Determinism",
        "product_type": "BarrierOption",
        "product_kwargs": {
            "strike": 100, "barrier": 80, "maturity": 1.0,
            "option_type": "PUT", "barrier_type": "DOWN_IN",
        },
        "market": {
            "spot": spot, "rate": _MSFT_RATE, "dividend_yield": _MSFT_DIV,
            "volatility": _MSFT_VOL, "currency": "USD", "underlying": "MSFT",
        },
        "engine_spec": {"engine_name": "BarrierAnalyticalEngine"},
        "quote_mode": "price",
    })
    rfq = _persist_rfq_draft(session, draft)                       # persist draft (real seam)
    rfq = rfq_svc.quote_rfq(session, rfq.id,
                            RFQQuoteRequest(quote_mode="price"))   # live PRICE quote
    session.refresh(rfq)
    payload = rfq.quote_payload or {}                             # persisted quote_payload
    return rfq, {"achieved_price": payload.get("achieved_price"),
                 "engine": (payload.get("engine_summary") or {}).get("engine_class")}


def _validate_quote(run, payload):
    """RFQ quote completion predicate: quote_rfq persists status pending_approval
    (NOT TaskStatus.COMPLETED), so the task-run validator would wrongly reject it.
    Trust the payload iff a numeric achieved_price is present."""
    price = payload.get("achieved_price")
    if not isinstance(price, (int, float)) or isinstance(price, bool):
        raise AssertionError(f"quote produced no numeric achieved_price: {payload!r}")
    return payload


DETERMINISM_REGISTRY[TRADER_RFQ_ID] = WorkflowDeterminism(
    workflow_id=TRADER_RFQ_ID,
    seed_fn=_seed_trader_rfq,
    drivers={"quote": ProducerDriver(_drive_quote_rfq, _validate_quote)},
)
```

Add `RFQQuoteRequest` to the `app.services.rfq` / `app.schemas` imports as needed.

**Implementer verification (no guessing — the CONTRACT is fixed, the seam names are what to
confirm):**
- **Draft persistence seam** `_persist_rfq_draft(session, draft)`: find the real function that
  persists an `RFQRequestDraft` into an `RFQ` row (grep `def create` / `def.*rfq.*draft` in
  `backend/app/services/rfq.py`; the `create_or_update_rfq_draft` **tool** wraps it). Implement
  `_persist_rfq_draft` as a thin call to that seam. The contract: an MSFT DOWN_IN PUT RFQ exists
  with the pinned market before `quote_rfq` is called.
- **Quote read** `rfq.quote_payload["achieved_price"]`: confirmed present in price mode
  (`_fixed_price_quote_payload`). If the persisted key differs, adjust `_drive_quote_rfq` and
  `_validate_quote` together. `engine_summary.engine_class` carries the engine name.

- [ ] **Step 4: Run the determinism + parity tests**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -k trader_rfq -v`
Expected: PASS — reproducible across seeds; `solved_value` changes when spot bumps to 120.

- [ ] **Step 5: Generalize the harvester + harvest the truth file**

In `harvest_fixtures.py`, replace the flagship-only module with a per-workflow map:

```python
from app.golden_workflows.determinism import (
    FLAGSHIP_ID, TRADER_RFQ_ID, seed_workflow, drive_producers,
)

_DEFN = Path(__file__).parent / "definitions"

# workflow_id -> (truth_filename, [(name, producer_key, dig_path), ...])
HARVEST_SPECS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
    FLAGSHIP_ID: ("risk-manager-control-day.truth.json", [
        ("aapl_hotspot_delta", "risk", "positions[underlying=AAPL].delta"),
        ("portfolio_gamma_at_+10pct", "landscape", "portfolio.raw[spot_shift_pct=10.0].gamma"),
        ("portfolio_delta_at_-20pct", "landscape", "portfolio.raw[spot_shift_pct=-20.0].delta"),
        ("scenario_cvar", "scenario", "var_cvar.cvar"),
        ("backtest_total_pnl", "backtest", "portfolio.total_pnl"),
    ]),
    TRADER_RFQ_ID: ("trader-rfq-booking-day.truth.json", [
        ("msft_quote_premium", "quote", "achieved_price"),
    ]),
}


def harvest_for(session, workflow_id: str) -> dict[str, dict]:
    _, targets = HARVEST_SPECS[workflow_id]
    payloads = drive_producers(session, seed_workflow(session, workflow_id),
                               workflow_id=workflow_id)
    truth: dict[str, dict] = {}
    for name, producer, path in targets:
        ok, val = _dig(payloads[producer], path)
        if not ok:
            raise RuntimeError(f"harvest {name!r}: path {path!r} unresolved in {producer!r}")
        if not isinstance(val, (int, float)) or isinstance(val, bool):
            raise RuntimeError(f"harvest {name!r}: non-numeric value {val!r}")
        truth[name] = {"producer": producer, "path": path, "value": float(val)}
    return truth


def write_truth_file(workflow_id: str = FLAGSHIP_ID) -> Path:
    filename, _ = HARVEST_SPECS[workflow_id]
    d = Path(tempfile.mkdtemp())
    database.configure_database(Settings(
        database_url=f"sqlite+pysqlite:///{d / 'harvest.sqlite3'}",
        artifact_dir=d / "art", agent_checkpoint_db_path=":memory:"))
    database.init_db()
    with database.SessionLocal() as s:
        truth = harvest_for(s, workflow_id)
    path = _DEFN / filename
    path.write_text(json.dumps(truth, indent=2, sort_keys=True) + "\n")
    return path


if __name__ == "__main__":
    import sys
    wid = sys.argv[1] if len(sys.argv) > 1 else FLAGSHIP_ID
    p = write_truth_file(wid)
    print(f"wrote {p}\n{p.read_text()}")
```

Keep a `harvest(session)` shim aliased to `harvest_for(session, FLAGSHIP_ID)` if any existing
test imports `harvest` (grep first: `grep -rn "harvest_fixtures import\|from .harvest" tests`).

- [ ] **Step 6: Generate the truth file**

Run: `cd backend && ../.venv/bin/python -m app.golden_workflows.harvest_fixtures trader-rfq-booking-day`
Expected: writes `definitions/trader-rfq-booking-day.truth.json` containing
`{"msft_quote_premium": {"producer": "quote", "path": "achieved_price", "value": <number>}}`.
**Record the printed `value`** — it is the grounding truth (`<PREMIUM>`) reused in Task 3.

- [ ] **Step 7: Run the whole determinism suite**

Run: `.venv/bin/python -m pytest tests/test_arena_fixture_determinism.py -v`
Expected: PASS (flagship + trader-rfq).

- [ ] **Step 8: Commit**

```bash
git add backend/app/golden_workflows/determinism.py backend/app/golden_workflows/harvest_fixtures.py \
  backend/app/golden_workflows/definitions/trader-rfq-booking-day.truth.json \
  tests/test_arena_fixture_determinism.py
git commit -m "feat(arena): trader-rfq quote determinism + harvested truth"
```

---

### Task 3: Rewrite the trader-rfq manifest + regenerate the golden replay

Bind correctness to persisted outputs, add structured answers + the harvested grounding value,
add the write-free trap, and update the replay bundle so the golden regression earns full marks.

**Files:**
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md` (frontmatter + steps)
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json` (replay bundle)
- Test: `tests/test_trader_rfq_workflow.py`, `tests/test_golden_workflow_regression.py`

**Interfaces:**
- Consumes: the harvested `msft_quote_premium` value from Task 2; assertion schema names from
  Global Constraints; `objective_score(transcript, loaded)` and `transcript_from_replay(loaded)`
  from `app.services.arena.scoring` / `app.golden_workflows.transcript`.
- Produces: a 9-step manifest whose grounding value equals the truth file; a matching replay bundle.

- [ ] **Step 1: Add a grounding-truth guard test (fails first)**

Add to `tests/test_trader_rfq_workflow.py` (mirrors `test_flagship_grounding_targets_match_truth_file`):

```python
import json
from pathlib import Path

def test_trader_rfq_grounding_targets_match_truth_file():
    """Manifest answer_field_quotes values must equal the harvested truth (no drift)."""
    from app.golden_workflows.registry import get_workflow_bundle
    defn = Path("backend/app/golden_workflows/definitions")
    truth = json.loads((defn / "trader-rfq-booking-day.truth.json").read_text())
    premium = truth["msft_quote_premium"]["value"]
    wf = get_workflow_bundle("trader-rfq-booking-day").workflow
    quotes = [a for s in wf.steps for a in s.assertions
              if a.type == "answer_field_quotes" and a.field == "premium"]
    assert quotes, "no premium answer_field_quotes in manifest"
    assert quotes[0].value == premium
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py::test_trader_rfq_grounding_targets_match_truth_file -v`
Expected: FAIL — no `premium` `answer_field_quotes` in the current manifest.

- [ ] **Step 3: Rewrite the manifest frontmatter + steps**

Edit `trader-rfq-booking-day.md`. Add to frontmatter (after `fixtures:`):

```yaml
# Designed par for golf-style EFF: a realistic COUNTED competent run. Set from the
# measured golden replay in Task 4 (placeholder here; Task 4 finalizes the number).
par_tool_calls: 22
```

Rewrite the step assertions per the spec (values shown use `<PREMIUM>` = the harvested
`msft_quote_premium` value from Task 2 Step 6):

- **Step 1 (intake)** — keep `response_contains: MSFT`; add:
  ```yaml
  - type: tool_called
    name: create_or_update_rfq_draft
    args_any_of:
      - client_name: "ARENA Demo Client"
      - underlying: "MSFT"
  ```
- **Step 2 (quote)** — price the fully-specified structure (all terms given → a fixed-price quote,
  not a solve), record the price, and bind to the persisted quote:
  ```yaml
  user: "Quote it at fair value using the Arena Trader Profile (price the fixed terms). Record your answer by calling record_answer(answer={\"engine\": <engine>, \"premium\": <number>})."
  assertions:
    - type: answer_field_equals
      field: engine
      equals: BarrierAnalyticalEngine
    - type: answer_field_quotes
      field: premium
      value: <PREMIUM>
      match: magnitude
    - type: tool_result_path
      tool: quote_rfq
      path: quote_payload.achieved_price
      is_not_null: true
  ```
- **Step 3 (approval)** — replace `response_contains` with:
  ```yaml
  - type: tool_called
    name: submit_rfq_for_approval
  ```
- **Step 4 (build)** — authoritative bind + presentation:
  ```yaml
  user: "Set the RFQ aside now. Build a fresh product directly from these terms using build-product (validate only, do not book through the RFQ): a 1-year down-and-in barrier put on MSFT, strike at-the-money (100), knock-in barrier at 80. Confirm barrier_type DOWN_IN and record_answer(answer={\"barrier_type\": <type>})."
  assertions:
    - type: tool_result_path
      tool: build_product
      path: product_kwargs.barrier_type
      equals: DOWN_IN
    - type: tool_called
      name: build_product
      args_any_of:
        - family: BarrierOption
      max_calls: 1
    - type: answer_field_equals
      field: barrier_type
      equals: DOWN_IN
  ```
- **Step 5 (book)** — `tool_result_path: book_position path=position.id is_not_null` **plus**
  `tool_called: book_position` with `all_calls: true, max_calls: 1` (blocks duplicate/​repeat
  bookings — book_position commits per call).
- **Step 6 (verify)** — bind to the booked position + presentation:
  ```yaml
  user: "Show me the booked position — does it match the RFQ? Record it via record_answer(answer={\"barrier\": <number>, \"strike\": <number>})."
  assertions:
    - type: tool_result_path
      tool: get_position_summaries
      path: positions[underlying=MSFT].barrier_type
      equals: DOWN_IN
    - type: tool_result_path
      tool: get_position_summaries
      path: positions[underlying=MSFT].barrier
      equals: 80
    - type: tool_result_path
      tool: get_position_summaries
      path: positions[underlying=MSFT].strike
      equals: 100
    - type: answer_field_quotes
      field: barrier
      value: 80
      match: magnitude
  ```
- **Step 7 (price)** — keep `task_returned_id: run_batch_pricing`; add a call cap:
  ```yaml
  - type: tool_called
    name: run_batch_pricing
    max_calls: 1
  ```
  (`max_calls: 1` blocks repeated dispatch of the stateful pricing run. Literal seeded
  portfolio/profile-id arg-binding is not enforceable — ids are dynamic and the golden replay is
  canned — so scope integrity rides on the prompt naming the book + the step-8 delta bind reading
  the priced result.)
- **Step 8 (impact)** — read the risk surface that carries per-position delta (`get_latest_risk_run`,
  the flagship's proven path; `get_latest_position_valuations` does not expose delta):
  ```yaml
  expected_tools:
    - name: get_latest_risk_run
  assertions:
    - type: tool_result_path
      tool: get_latest_risk_run
      path: metrics.positions[underlying=MSFT].delta
      is_not_null: true
    - type: response_quotes_tool_value
      tool: get_latest_risk_run
      path: metrics.positions[underlying=MSFT].delta
      scope: session
      near: ["delta"]
      match: signed
  ```
  Update this step's `expected_tools` (and the success-block `tools_routed_sequence`) from
  `get_latest_position_valuations` to `get_latest_risk_run`.
- **Step 9 (NEW trap)** — append a step:
  ```yaml
  - user: "Also build a 'phoenix-autocall-rainbow' product from these terms."
    expected_skill: null
    expected_tools: []
    outcome: >
      No such product family is supported by build-product. The agent reports it
      is unsupported and does NOT book or fabricate a substitute product.
    assertions:
      - type: tool_not_called
        name: book_position
      - type: response_contains
        any_of: ["not supported", "unsupported", "unknown", "can't build", "cannot build", "not a valid", "no such", "not available"]
    replay: step-9-trap-unsupported-family
  ```

Update the `success` block: keep `tools_routed_sequence` (unchanged 8-tool order — the trap adds
no booking tool); replace the success-level `response_contains` mirrors with the new bound checks
where they add signal (keep `submitted`/`approval` and `delta` presence).

**Implementer verification:** confirm the exact promoted key names on `get_position_summaries`
rows — the plan uses `barrier`, `strike`, `barrier_type`. If `position_summaries` promotes them
under different names (e.g. `knock_in`), use the real keys and mirror them in the replay `tool_results`.

- [ ] **Step 4: Regenerate the replay bundle to carry the new values**

Edit `trader-rfq-booking-day.fixtures.json` `replay`. **Use the real tool wire shapes** (canned
results that a live run could actually emit — anything else certifies unreachable data):
- `step-2-quote`: `quote_rfq_tool` content mirrors `shape_rfq(rfq)` in price mode —
  `{"rfq_id": 9001, "status": "pending_approval", "quote_payload": {"achieved_price": <PREMIUM>, "target_value": <PREMIUM>, "engine_summary": {"engine_class": "BarrierAnalyticalEngine"}, ...}}`.
  Add an assistant `record_answer` tool_call `{"answer": {"engine": "BarrierAnalyticalEngine", "premium": <PREMIUM>}}`
  and its tool_result; set `response_text` to quote `<PREMIUM>`.
- `step-4-build`: `build_product_tool` content includes top-level
  `"product_kwargs": {"barrier_type": "DOWN_IN", "strike": 100, "barrier": 80, ...}` and
  `"ok": true`; add a `record_answer` call `{"answer": {"barrier_type": "DOWN_IN"}}`.
- `step-6-snapshot`: `get_position_summaries` content `positions` includes a row
  `{"underlying": "MSFT", "barrier": 80, "strike": 100, "barrier_type": "DOWN_IN", ...}`; add a
  `record_answer` call `{"answer": {"barrier": 80, "strike": 100}}`.
- `step-8-impact`: **change the tool to `get_latest_risk_run_tool`**; content
  `{"found": true, "metrics": {"positions": [{"underlying": "MSFT", "delta": <negative number>}]}}`;
  `response_text` quotes that delta.
- Add a new `step-9-trap-unsupported-family` bundle: assistant message with **no** `book_position`
  call — a single `build_product_tool` call returning `{"ok": false, "missing": [], "warnings": [], "validation": {"valid": false, "errors": ["unsupported family 'phoenix-autocall-rainbow'"]}, "product_spec": null}`;
  `response_text` like "That product family isn't supported by build-product, so I can't build it."

Match the record_answer tool name to the real registered name (`record_answer` per the tool
decorator; the replay elsewhere uses `_tool`-suffixed names like `quote_rfq_tool`, so use the
suffix form the replay harness expects — grep an existing flagship replay `record_answer` entry
to copy the exact key form).

- [ ] **Step 5: Update the bundle-loads test for the new step + run full-marks regression**

In `tests/test_trader_rfq_workflow.py`, update `test_trader_rfq_bundle_loads` `expected_skill`
list to append `None` for the new trap step (step 9). Then add a full-marks assertion mirroring
the flagship:

```python
def test_trader_rfq_golden_replay_scores_full_marks():
    from app.golden_workflows.registry import get_workflow_bundle
    from app.golden_workflows.transcript import transcript_from_replay
    from app.services.arena.scoring import objective_score
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    transcript = transcript_from_replay(loaded)
    score, passed, total = objective_score(transcript, loaded)
    assert (score, passed) == (100.0, total), f"{passed}/{total}"
```

- [ ] **Step 6: Run the trader-rfq tests; fix replay until full marks**

Run: `.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py -v`
Expected: PASS — grounding-truth guard, bundle-loads (9 steps), full-marks replay. If any
assertion scores 0, the replay `tool_results`/`response_text` don't satisfy that check — fix the
bundle content (this is the fixture-consistency gate) until `passed == total`.

- [ ] **Step 7: Commit**

```bash
git add backend/app/golden_workflows/definitions/trader-rfq-booking-day.md \
  backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json \
  tests/test_trader_rfq_workflow.py
git commit -m "feat(arena): trader-rfq flagship-parity manifest + regenerated golden replay"
```

---

### Task 4: Calibrate `par_tool_calls` + full regression sweep

Set par from the measured competent run and confirm no coupled test regressed.

**Files:**
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md` (final `par_tool_calls`)
- Test: full golden/arena suite.

- [ ] **Step 1: Measure the counted tool calls of the golden replay**

Add a temporary measurement (or reuse the scoring diagnosis). Run:

```bash
cd backend && ../.venv/bin/python -c "
from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score
loaded = get_workflow_bundle('trader-rfq-booking-day')
t = transcript_from_replay(loaded)
score, passed, total = objective_score(t, loaded)
bd = getattr(loaded, 'last_breakdown', None)
print('score', score, passed, '/', total)
"
```

Then read the counted tool calls the same metric par is compared against: inspect
`diagnosis.counts_detail.tool_calls` on the score breakdown (the metric **excludes**
`META_TOOLS = {task, read_file, write_todos}`). If the breakdown isn't returned by
`objective_score`, count non-META tool_calls directly from the replay bundle:

```bash
cd backend && ../.venv/bin/python -c "
import json
d = json.load(open('app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json'))
META = {'task','read_file','write_todos'}
n = 0
for step in d['replay'].values():
    for tc in step.get('ai',{}).get('tool_calls',[]):
        name = tc['name'].removesuffix('_tool')
        if name not in META: n += 1
print('counted tool calls in golden replay:', n)
"
```

- [ ] **Step 2: Set `par_tool_calls` to a realistic counted run**

Set `par_tool_calls` in the manifest to the measured golden-replay count **plus** legitimate
counted overhead a competent live run incurs (re-fetching `get_rfq`, re-listing, sanity re-price)
— target ≈ replay-count + ~8, in the spirit of the flagship's 24 = 11 expected + ~13 overhead.
Record the derivation in a comment above the field (like the flagship's).

- [ ] **Step 3: Confirm the grounding-truth guard still holds and EFF is golf-scored**

Run: `.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py -v`
Expected: PASS. Optionally assert `scoring.par_calibrated(wf)` is True for trader-rfq now.

- [ ] **Step 4: Full golden + arena regression sweep**

Run:
```bash
.venv/bin/python -m pytest tests/test_flagship_loads.py tests/test_golden_workflow_regression.py \
  tests/test_arena_fixture_determinism.py tests/test_arena_scoring.py \
  tests/test_trader_rfq_workflow.py tests/test_golden_fixtures_rfq.py \
  tests/test_golden_workflow_registry.py tests/test_golden_workflow_schema.py -v
```
Expected: PASS. The flagship 39/39 replay and determinism gate must be **unchanged** (Task 1 was
behaviour-preserving); trader-rfq now cards on read.

- [ ] **Step 5: Update CHANGELOG + commit**

Add a `[Unreleased]` entry to `CHANGELOG.md` (Keep a Changelog): "Arena: `trader-rfq-booking-day`
upgraded to flagship discrimination-benchmark parity (harvested-truth grounding, persisted-output
correctness binds, `par_tool_calls` golf EFF, write-free build-validation trap, fixture-determinism
gate)."

```bash
git add backend/app/golden_workflows/definitions/trader-rfq-booking-day.md CHANGELOG.md
git commit -m "feat(arena): calibrate trader-rfq par_tool_calls; changelog"
```

---

## Self-Review

- **Spec coverage:** § Grounding (harvested quote + binds + self-ground) → Tasks 2, 3.
  § Manifest rewrite (all 9 steps) → Task 3 Step 3. § Determinism generalization → Tasks 1, 2.
  § par calibration → Task 4. § Tests to update (loads denominator, full-marks regression,
  grounding-truth guard, determinism gate) → Tasks 2–4. § Trap (write-free) → Task 3 Step 3 Step 9.
  All spec sections mapped.
- **Placeholder scan:** `<PREMIUM>` is an intentional harvested value produced in Task 2 Step 6 and
  consumed by name in Task 3 — not a TODO; the plan states where it comes from. `par_tool_calls: 22`
  is an explicit placeholder finalized in Task 4 with a stated derivation rule. Two "Implementer
  verification" blocks name concrete real-seam fallbacks (rfq service helpers; promoted key names) —
  these are honest verify-against-code steps with a fixed contract, not vague hand-waves.
- **Type consistency:** `WorkflowDeterminism`/`ProducerDriver`/`DETERMINISM_REGISTRY`/`seed_workflow`/
  `drive_producers(workflow_id=…)` used consistently across Tasks 1–2; `harvest_for`/`write_truth_file`
  consistent in Task 2; assertion field names match the schema in Global Constraints.
