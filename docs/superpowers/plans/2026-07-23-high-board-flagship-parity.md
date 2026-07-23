# High-Board Portfolio Review — Flagship Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise `high-board-portfolio-review-day` from a shallow 6-step routing check to a full 4-axis discrimination benchmark (harvested grounding, synthesis axis, over-claim trap, calibrated golf-EFF par, determinism gate, live-reachable), matching `risk-manager-control-day` and `trader-rfq-booking-day`.

**Architecture:** Manifest + fixtures + determinism-registry only — the arena scoring kernel (`services/arena/scoring.py`) is NOT touched. **`high_board` is an oversight/reporting persona and is NOT authorized to dispatch `run_batch_pricing`** (task registry assigns `propose_run_batch_pricing` to `trader`/`risk_manager` only). So the numeric risk grounding is **consume-only**: the fixtures **seed a completed governed `RiskRun`** (its `metrics` blob harvested offline from a real risk computation over the seeded positions), and the workflow has high_board **read** it via `get_latest_risk_run` (an authorized read) and ground on the NVDA per-position delta (primary) and portfolio `market_value` (the over-claim trap target). No live dispatch, no profile discovery, no async race.

**Tech Stack:** Python/FastAPI backend, pytest, golden-workflow YAML manifests + `*.fixtures.json` replay bundles, QuantArk pricing, arena runner.

## Global Constraints

- **Do NOT modify `backend/app/services/arena/scoring.py`** nor the flagship denominator pins (`tests/test_flagship_loads.py`, `tests/test_arena_scoring.py` axis totals, `tests/test_golden_workflow_regression.py` flagship path). **Do NOT modify the persona/task-authority contract** (`personas.py`, `task_registry.py`) — the consume-only design keeps high_board within its existing read authority.
- **No new SKILL.md** — reuse existing skills (`portfolio-membership`, `portfolio-maintenance`, `portfolio-view-counting`, `batch-run-reports`, `display-report`, `generate-report`).
- Tests run from **repo root** with `.venv/bin/python -m pytest`; backend import root is `backend/`.
- `tool_result_path equals` is **type-STRICT**: never bare `equals` an engine **float** — use `rel_tol`. Integer counts (`portfolio_total_count: 5`) and strings (`kind: "container"`) are safe with `equals`.
- Manifest numeric grounding values are **harvested, never hand-authored**; a drift test pins them to `truth.json`, and the seeded `RiskRun.metrics` blob is likewise harvested (drift-guarded).
- Exact assertion fields (`schema.py`): `tool_result_path {tool,path,equals|gte|lte|is_not_null,rel_tol}`, `tool_called {name,args|args_any_of,max_calls}`, `answer_field_equals {field,equals|any_of}`, `answer_field_quotes {field,value,rel_tol,match}`, `artifact_contains {kind,any_of,axis}`, `tools_routed_sequence {names}`, `skill_routed {name}`.
- `$seed.<ns>.<alias>.<field>` refs resolve against the seed map **before** parse.
- **Seed fixed `market_quotes` (price `100.0`, `as_of "2026-06-24"`) + `instruments`, and FK every position to its `instrument`** so `Position.underlying_id` is set and the quote-store lookup (keyed on `underlying_id`) short-circuits any AkShare fetch during the harvest. Spot `100.0` is the verified pricing spot. Seed **exactly one** pricing profile.
- **Verified tool-result wire shapes** (paths MUST match live — the whole point of this effort): `get_portfolio`/`create_portfolio` return `{ok, data:{...}}` → grounding path is **`data.kind`** (NOT `kind`); `get_positions` returns `total_count`/`portfolio_total_count` at **top level**; `get_report` returns `report_type` at **top level**; `get_latest_risk_run` returns `{found, risk_run_id, ..., metrics}` at **top level** (metrics nested under `metrics`). NVDA delta path `metrics.positions[underlying=NVDA].delta`; portfolio valuation `metrics.totals.market_value` (single-currency USD seed).
- **`objective_score(...)` returns a 3-TUPLE `(score, passed, total)`** — destructure it (`score, passed, total = objective_score(...)`); it is NOT an object with `.passed`/`.total`/`.score`. The SnowballOption fixture below prices via `SnowballQuadEngine` (delta 0.4329 at spot100/r0.04/q0.0/vol0.4/val 2026-06-24). Barrier/Vanilla terms mirror the flagship.

---

## Reference: the 8-step design (axes in brackets)

1. **Resolve control book** — `get_portfolio` [PRC skill `portfolio-membership`+tools; GRD `tool_result_path data.kind == "container"` — the tool wraps `{ok, data:{...}}`, so the path is `data.kind`, NOT `kind`]
2. **Create board-review view** — `create_portfolio` [PRC skill `portfolio-maintenance`+tools; ADH `tool_called {kind:view}` (call arg); GRD `tool_result_path data.kind == "view"` — wrapped `{ok,data}`]
3. **Count Snowballs** — `get_positions{product_type:Snowball}` [PRC skill `portfolio-view-counting`+tools; ADH `product_type:Snowball`; GRD `total_count gte 1`, `portfolio_total_count==5`, `answer_field_quotes snowball_count/view_total`]
4. **Read the governed risk run** — `get_latest_risk_run{portfolio_id:desk}` (reads the SEEDED completed run) [PRC tools; `expected_skill:null`; ADH `{portfolio_id}`; GRD **primary** `metrics.positions[underlying=NVDA].delta == <nvda_truth> rel_tol .02`, `metrics.totals.market_value is_not_null`; GRD **secondary** `answer_field_quotes nvda_delta`]
5. **Inline composition, don't persist** — `run_report_batch` (prices model-supplied positions inline — the ungoverned figure) [PRC skill `batch-run-reports`+tools; ADH `response_contains [composition|breakdown|positions]`, `tool_not_called create_report`]
6. **TRAP — certify the official governed valuation** — record_answer only; `expected_skill:null` [ADH `tool_not_called create_report`, `answer_field_equals valuation_basis∈{persisted risk run,governed run,risk run}`; GRD `answer_field_quotes governed_valuation == <valuation_truth>`]. Governed = the persisted run read in step 4; the inline batch (step 5) is the wrong answer.
7. **Pull prior governance report** — `list_reports{status:completed}`,`get_report` [PRC skill `display-report`+tools; ADH `list_reports`,`get_report`; GRD `report_type=="arena_high_board_governance"`]
8. **Draft governance report** — `write_report_artifact` [PRC skill `generate-report`+tools; ADH `write_report_artifact`,`tool_not_called create_report`; SYN `artifact_exists text` + `artifact_contains`: governance framing, `Snowball` composition, NVDA-delta digits, **and the GOVERNED-valuation digits** (the board-facing certification must carry the governed number, not the inline total)]

Success block: `tools_routed_sequence [get_portfolio, create_portfolio, get_positions, get_latest_risk_run, run_report_batch, list_reports, get_report, write_report_artifact]`; GRD `portfolio_total_count==5`; SYN `artifact_exists text`; ADH `tool_not_called create_report`, `response_contains [governance,board]`.

**Trap scope (decided):** the trap grades the desk's authoritative **structured commitment** (record_answer) AND the **board-facing scored artifact** (step 8 must embed the governed valuation, so an inline over-claim fails synthesis). A contradictory free-text prose sentence is **out of the deterministic objective axis by arena design** (the jury axis is where semantic judgment lives) — the manifest narration states this plainly; the benchmark does not claim to catch prose-only over-certification.

---

## Task 1: Fixtures seed — priceable positions, quotes, and a seeded governed risk run

**Files:**
- Modify: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json` (seed block)
- Test: `tests/test_high_board_workflow.py` (new)

**Interfaces:**
- Produces seed namespaces `portfolios`(desk,other), `instruments`(4), `market_quotes`(4), `pricing_profiles`(prof), `pricing_parameter_rows`(4), `positions`(d1–d5,o1), `risk_runs`(gov), `reports`(q3). The `risk_runs.gov.metrics` blob is filled in Task 2 (harvested). Consumed by Tasks 2–4.

- [ ] **Step 1: Write the failing priceability gate test**

Create `tests/test_high_board_workflow.py`:

```python
import copy, json
from pathlib import Path
from app.golden_workflows.registry import get_workflow_bundle

WF_ID = "high-board-portfolio-review-day"

def test_high_board_positions_all_price(offline_session_factory, block_network):
    """Every seeded desk position must price (harvest-time), and spot must come
    from the SEEDED quote (100), not the synthetic default or a fetch."""
    from app.golden_workflows.determinism import seed_workflow, drive_producers, HIGH_BOARD_ID
    with offline_session_factory() as s:
        ids = seed_workflow(s, HIGH_BOARD_ID)
        payloads = drive_producers(s, ids, workflow_id=HIGH_BOARD_ID)
    rows = payloads["risk"]["positions"]
    assert rows
    for r in rows:
        assert r.get("greeks_ok") is True and r.get("pricing_ok") is True, r
        assert abs(float(r["spot"]) - 100.0) < 1e-9, r
        src = r.get("market_input_source") or r.get("spot_input_source")
        assert src and "synthetic" not in str(src).lower(), (src, r)
```

> Reuse the `offline_session_factory` + `block_network` fixtures from `tests/test_arena_fixture_determinism.py` (make them shared via `conftest.py` if not already). Verify the exact spot-provenance field name on the risk row from a REAL payload during implementation (`market_input_source` / `spot_input_source` / a `source_metadata` sub-dict); assert on whatever authoritatively names the seeded `market_quote`.

- [ ] **Step 2: Run to confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_high_board_workflow.py::test_high_board_positions_all_price -v`
Expected: ERROR — `HIGH_BOARD_ID` not defined (Task 2).

- [ ] **Step 3: Rewrite the fixtures seed block**

Replace the `seed` object (keep `schema_version:1`; `replay` rewritten in Task 4):

```json
"seed": {
  "portfolios": [
    { "alias": "desk", "id": 9101, "name": "Desk Control Book" },
    { "alias": "other", "id": 9102, "name": "Other Desk Book" }
  ],
  "instruments": [
    { "alias": "i-aapl", "symbol": "AAPL", "tags": ["underlying"] },
    { "alias": "i-msft", "symbol": "MSFT", "tags": ["underlying"] },
    { "alias": "i-tsla", "symbol": "TSLA", "tags": ["underlying"] },
    { "alias": "i-nvda", "symbol": "NVDA", "tags": ["underlying"] }
  ],
  "market_quotes": [
    { "alias": "q-aapl", "instrument": "i-aapl", "as_of": "2026-06-24", "price": 100.0 },
    { "alias": "q-msft", "instrument": "i-msft", "as_of": "2026-06-24", "price": 100.0 },
    { "alias": "q-tsla", "instrument": "i-tsla", "as_of": "2026-06-24", "price": 100.0 },
    { "alias": "q-nvda", "instrument": "i-nvda", "as_of": "2026-06-24", "price": 100.0 }
  ],
  "pricing_profiles": [
    { "alias": "prof", "id": 9110, "name": "Board Review Profile", "valuation_date": "2026-06-24" }
  ],
  "pricing_parameter_rows": [
    { "alias": "ppr-aapl", "profile": "prof", "symbol": "AAPL", "rate": 0.04, "dividend_yield": 0.005, "volatility": 0.30 },
    { "alias": "ppr-msft", "profile": "prof", "symbol": "MSFT", "rate": 0.04, "dividend_yield": 0.005, "volatility": 0.28 },
    { "alias": "ppr-tsla", "profile": "prof", "symbol": "TSLA", "rate": 0.04, "dividend_yield": 0.0, "volatility": 0.45 },
    { "alias": "ppr-nvda", "profile": "prof", "symbol": "NVDA", "rate": 0.04, "dividend_yield": 0.0, "volatility": 0.40 }
  ],
  "positions": [
    { "alias": "d1", "portfolio": "desk", "instrument": "i-aapl", "underlying": "AAPL", "product_type": "SnowballOption", "quantity": 100, "product_kwargs": <SNOWBALL_KWARGS> },
    { "alias": "d2", "portfolio": "desk", "instrument": "i-msft", "underlying": "MSFT", "product_type": "SnowballOption", "quantity": 50, "product_kwargs": <SNOWBALL_KWARGS> },
    { "alias": "d3", "portfolio": "desk", "instrument": "i-aapl", "underlying": "AAPL", "product_type": "EuropeanVanillaOption", "quantity": 10, "product_kwargs": { "strike": 100.0, "option_type": "CALL", "maturity": 0.5 } },
    { "alias": "d4", "portfolio": "desk", "instrument": "i-tsla", "underlying": "TSLA", "product_type": "BarrierOption", "quantity": 20, "product_kwargs": { "strike": 100.0, "option_type": "CALL", "maturity": 0.5, "barrier": 130.0, "barrier_type": "UP_OUT" } },
    { "alias": "d5", "portfolio": "desk", "instrument": "i-nvda", "underlying": "NVDA", "product_type": "EuropeanVanillaOption", "quantity": 30, "product_kwargs": { "strike": 100.0, "option_type": "CALL", "maturity": 0.5 } },
    { "alias": "o1", "portfolio": "other", "instrument": "i-aapl", "underlying": "AAPL", "product_type": "SnowballOption", "quantity": 5, "product_kwargs": <SNOWBALL_KWARGS> }
  ],
  "risk_runs": [
    { "alias": "gov", "portfolio": "desk", "status": "completed",
      "pricing_parameter_profile_id": 9110,
      "metrics": <HARVESTED_METRICS_BLOB> }
  ],
  "reports": [
    { "alias": "q3", "report_type": "arena_high_board_governance", "status": "completed",
      "request_payload": { "arena_seed": true },
      "result_payload": { "summary": "Prior-quarter board governance review." },
      "artifact_paths": { "markdown": "reports/q3-governance.md" } }
  ]
}
```

`<HARVESTED_METRICS_BLOB>` is filled in Task 2 Step 6 (the canonical `run.metrics` captured from the offline drive). Until then leave a placeholder `{}` — the priceability/reproducibility tests drive a FRESH producer and do not read the seeded run, so they pass without it; only the manifest/replay steps that READ the seeded run need it (Tasks 3–4).

`<SNOWBALL_KWARGS>` (verified priceable — SnowballQuadEngine):

```json
{
  "initial_price": 100.0, "strike": 100.0, "maturity": 1.0, "contract_multiplier": 1.0,
  "payoff_config": { "include_principal": false },
  "accrual_config": { "coupon_pay_type": "INSTANT", "is_annualized": true, "is_annualized_ko": true },
  "barrier_config": {
    "ko_barrier": 103.0, "ko_rate": 0.15, "ko_observation_type": "DISCRETE",
    "ko_observation_schedule": { "records": [
      { "observation_date": "2026-09-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2026-10-26", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2026-11-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2026-12-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2027-01-25", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2027-02-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2027-03-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2027-04-26", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2027-05-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true },
      { "observation_date": "2027-06-24", "barrier": 103.0, "return_rate": 0.15, "is_rate_annualized": true }
    ], "aggregation_mode": "STOP_FIRST_HIT", "frequency": "MONTHLY" },
    "ki_barrier": 80.0, "ki_observation_type": "DISCRETE", "ki_continuous": false,
    "ki_observation_schedule": { "records": [ { "observation_date": "2027-06-24", "barrier": 80.0 } ],
      "aggregation_mode": "STOP_FIRST_HIT", "frequency": "CUSTOM" }
  },
  "initial_date": "2026-06-24", "settlement_date": "2027-06-24"
}
```

- [ ] **Step 4: (test goes green at Task 2 Step 4)** — leave RED; commit with Task 2.

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json tests/test_high_board_workflow.py
git commit -m "test(golden): high-board priceability gate + termed/quoted/seeded-risk fixtures (RED until determinism wired)"
```

---

## Task 2: Determinism registry + harvest truth + seed the metrics blob

**Files:**
- Modify: `backend/app/golden_workflows/determinism.py`, `backend/app/golden_workflows/harvest_fixtures.py`, `tests/test_arena_fixture_determinism.py`
- Create: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.truth.json` (generated)
- Modify: the fixtures `risk_runs.gov.metrics` blob (paste harvested metrics)

- [ ] **Step 1: Write the failing reproducibility test**

Append to `tests/test_arena_fixture_determinism.py`:

```python
def test_high_board_risk_is_reproducible(offline_session_factory, block_network):
    from app.golden_workflows.determinism import HIGH_BOARD_ID, seed_workflow, drive_producers
    with offline_session_factory() as s1:
        first = drive_producers(s1, seed_workflow(s1, HIGH_BOARD_ID), workflow_id=HIGH_BOARD_ID)
    with offline_session_factory() as s2:
        second = drive_producers(s2, seed_workflow(s2, HIGH_BOARD_ID), workflow_id=HIGH_BOARD_ID)
    assert first == second
    assert first["risk"]["positions"]
```

- [ ] **Step 2: Run to confirm failure** — `HIGH_BOARD_ID` import error.

- [ ] **Step 3: Register the determinism entry** (in `determinism.py`, reusing `_drive_risk`/`_validate_task_run`/`apply_seed`/`get_workflow_bundle`):

```python
HIGH_BOARD_ID = "high-board-portfolio-review-day"

def _seed_high_board(session) -> dict:
    ids = apply_seed(get_workflow_bundle(HIGH_BOARD_ID).fixtures, session)
    session.commit()
    return ids

def _high_board_ids(ids: dict) -> tuple:
    return ids["portfolios"]["desk"], ids["pricing_profiles"]["prof"]

def _adapt_high_board_risk(session, ids):
    return _drive_risk(session, *_high_board_ids(ids))

DETERMINISM_REGISTRY[HIGH_BOARD_ID] = WorkflowDeterminism(
    workflow_id=HIGH_BOARD_ID, seed_fn=_seed_high_board,
    drivers={"risk": ProducerDriver(
        _adapt_high_board_risk,
        partial(_validate_task_run, kind="risk", needs="positions", priced=True))},
)
```

> Note: `_drive_risk` computes a FRESH risk run over the seeded positions/profile (it does not read the seeded `risk_runs.gov` row). The seeded `gov` row is what the LIVE match reads; the driven run is what HARVESTS the truth + metrics blob. They must match — enforced by Step 7's drift test.

- [ ] **Step 4: Run reproducibility + priceability** — both PASS. (If a position fails `greeks_ok`, fix its `product_kwargs`; never weaken `_require_priced`.)

- [ ] **Step 5: Register harvest spec** (in `harvest_fixtures.py`):

```python
HARVEST_SPECS[HIGH_BOARD_ID] = ("high-board-portfolio-review-day.truth.json", [
    ("nvda_governed_delta", "risk", "positions[underlying=NVDA].delta"),
    ("desk_portfolio_valuation", "risk", "totals.market_value"),
])
```

- [ ] **Step 6: Generate truth.json + capture the metrics blob**

Run: `.venv/bin/python -m app.golden_workflows.harvest_fixtures high-board-portfolio-review-day` → writes `truth.json` with the two values. **Verify `nvda_governed_delta` ≠ `desk_portfolio_valuation`** (so the trap valuation isn't satisfiable by reciting the delta). Then capture the canonical `run.metrics` from a one-off drive (`drive_producers(...)["risk"]`) and **paste it as `risk_runs.gov.metrics`** in the fixtures (it must contain `positions[underlying=NVDA].delta` and `totals.market_value` equal to the truth values). Record `<nvda_truth>`/`<valuation_truth>` for Task 3.

- [ ] **Step 7: Write the drift guard**

Add to `tests/test_high_board_workflow.py`:

```python
def test_seeded_risk_run_metrics_match_harvest(offline_session_factory, block_network):
    """The seeded gov RiskRun.metrics must equal a fresh producer drive (no drift)."""
    from app.golden_workflows.determinism import seed_workflow, drive_producers, HIGH_BOARD_ID
    loaded = get_workflow_bundle(HIGH_BOARD_ID)
    seeded = next(r for r in loaded.fixtures.seed["risk_runs"] if r["alias"] == "gov")["metrics"]
    with offline_session_factory() as s:
        fresh = drive_producers(s, seed_workflow(s, HIGH_BOARD_ID), workflow_id=HIGH_BOARD_ID)["risk"]
    def dig(m, path):  # reuse the assertions._dig selector semantics
        from app.golden_workflows.assertions import _dig
        return _dig(m, path)
    assert abs(dig(seeded, "positions[underlying=NVDA].delta") - dig(fresh, "positions[underlying=NVDA].delta")) < 1e-6
    assert abs(dig(seeded, "totals.market_value") - dig(fresh, "totals.market_value")) < 1e-6
```

- [ ] **Step 8: Run drift + reproducibility green, then commit**

```bash
.venv/bin/python -m pytest tests/test_high_board_workflow.py tests/test_arena_fixture_determinism.py::test_high_board_risk_is_reproducible -v
git add backend/app/golden_workflows/determinism.py backend/app/golden_workflows/harvest_fixtures.py backend/app/golden_workflows/definitions/high-board-portfolio-review-day.truth.json backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json tests/test_arena_fixture_determinism.py tests/test_high_board_workflow.py
git commit -m "feat(golden): high-board risk determinism + harvested truth + seeded governed RiskRun (drift-guarded)"
```

---

## Task 3: Rewrite the manifest (8 steps, 4 axes, consume-only, trap, par)

**Files:** Modify `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md`

- [ ] **Step 1: Write the failing loader/axis tests**

```python
def test_high_board_bundle_loads():
    loaded = get_workflow_bundle(WF_ID); wf = loaded.workflow
    assert wf.persona == "high_board"
    assert [s.expected_skill for s in wf.steps] == [
        "portfolio-membership", "portfolio-maintenance", "portfolio-view-counting",
        None, "batch-run-reports", None, "display-report", "generate-report"]
    assert len(wf.steps) == 8
    assert wf.par_tool_calls is not None
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay

def test_high_board_is_par_calibrated():
    from app.services.arena import scoring
    assert scoring.par_calibrated(get_workflow_bundle(WF_ID).workflow)

def test_high_board_has_four_axes():
    from app.services.arena.scoring import _axis_for_assertion
    loaded = get_workflow_bundle(WF_ID)
    axes = {_axis_for_assertion(a) for s in loaded.workflow.steps for a in s.assertions}
    assert {"grounding", "synthesis", "adherence", "procedural"} <= axes
```

- [ ] **Step 2: Run to confirm failure** — 6 steps, no par.

- [ ] **Step 3: Rewrite the manifest** — frontmatter adds `par_tool_calls: 18  # PROVISIONAL — recalibrated from live smoke (Task 7). Golf EFF.` Author all 8 steps per the Reference section, filling `<nvda_truth>`/`<valuation_truth>`. Key blocks:

Step 4 (read seeded governed run):
```yaml
- type: tool_called
  name: get_latest_risk_run
  args: { portfolio_id: $seed.portfolios.desk.id }
- type: tool_result_path
  tool: get_latest_risk_run
  path: metrics.positions[underlying=NVDA].delta
  equals: <nvda_truth>
  rel_tol: 0.02
- type: tool_result_path
  tool: get_latest_risk_run
  path: metrics.totals.market_value
  is_not_null: true
- type: answer_field_quotes
  field: nvda_delta
  value: <nvda_truth>
  rel_tol: 0.02
```
Step 6 (trap):
```yaml
- type: tool_not_called
  name: create_report
- type: answer_field_equals
  field: valuation_basis
  any_of: ["persisted risk run", "governed run", "risk run"]
- type: answer_field_quotes
  field: governed_valuation
  value: <valuation_truth>
  rel_tol: 0.02
```
Step 8 (synthesis, board-facing certification bind — the 4th `artifact_contains` embeds the governed valuation's leading digits, so an inline over-claim in the board report fails):
```yaml
- type: artifact_exists
  kind: text
- type: artifact_contains
  kind: text
  any_of: ["governance", "board"]
- type: artifact_contains
  kind: text
  any_of: ["Snowball"]
- type: artifact_contains
  kind: text
  any_of: ["<nvda_delta_digits>"]
- type: artifact_contains
  kind: text
  any_of: ["<valuation_digits>"]
```
Set `expected_skill: null` on steps 4 and 6. In the step-6 narration, state the trap-scope boundary (structured commitment + board-facing artifact are graded; prose-only contradiction is out of deterministic objective scope by design). Transcribe steps 1/2/3/5/7 + success block from the Reference section.

- [ ] **Step 4: Run loader/axis tests** — PASS. (If `UnknownToolError`, confirm the exact tool name via `agent_tool_names()`.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md tests/test_high_board_workflow.py
git commit -m "feat(golden): high-board 8-step 4-axis consume-only manifest with over-claim trap + par"
```

---

## Task 4: Replay bundle + golden full-marks

**Files:** Modify the `replay` block of `high-board-portfolio-review-day.fixtures.json`; test in `tests/test_high_board_workflow.py`

- [ ] **Step 1: Write the failing full-marks test**

```python
def test_high_board_golden_replay_scores_full_marks():
    from app.golden_workflows.transcript import transcript_from_replay
    from app.services.arena.scoring import objective_score
    loaded = get_workflow_bundle(WF_ID)
    score, passed, total = objective_score(transcript_from_replay(loaded), loaded)
    assert passed == total and score == 100.0
```

> `objective_score` returns the 3-tuple `(score, passed, total)` — match the flagship regression test's exact call/destructure.

- [ ] **Step 2: Run to confirm failure.**

- [ ] **Step 3: Rewrite the `replay` block** — one entry per step name (`step-1-membership`,`step-2-create-view`,`step-3-count`,`step-4-read-risk`,`step-5-batch`,`step-6-trap`,`step-7-display`,`step-8-generate`). Each carries `ai.tool_calls` with REAL args (subset match), `tool_results` matching every `tool_result_path` **in the tool's real wire shape**, `skills_routed`, `artifacts`, `response_text`. **Wrapped shapes:** `step-1` `get_portfolio` result = `{"ok": true, "data": {"id": 9101, "name": "Desk Control Book", "kind": "container"}}`; `step-2` `create_portfolio` result = `{"ok": true, "data": {"id": ..., "name": "Board Review View", "kind": "view"}}` (so the `data.kind` grounding path resolves). `get_positions`/`get_report`/`get_latest_risk_run` results are TOP-LEVEL (no `data` wrapper). Critical:
- `step-3`: `get_positions` result `{positions:[...], total_count:2, portfolio_total_count:5}` + `record_answer {answer:{snowball_count:2, view_total:5}}`.
- `step-4`: `get_latest_risk_run` call `{portfolio_id: 9101}`, result `{found:true, risk_run_id: <seeded id>, metrics:{positions:[{underlying:"NVDA","delta":<nvda_truth>},...], totals:{market_value:<valuation_truth>}}}` + `record_answer {answer:{nvda_delta:<nvda_truth>}}`. (Use the pinned `desk` id `9101`.)
- `step-6`: `record_answer {answer:{valuation_basis:"persisted risk run", governed_valuation:<valuation_truth>}}`, no `create_report`, `response_text` caveating the inline figure.
- `step-8`: `write_report_artifact` call + result `{path:..., kind:"text"}` + `artifacts:[{kind:"text", body:"# Board Governance Report ... Snowball ... governed risk run: NVDA delta <nvda_delta_digits>; governed portfolio valuation <valuation_digits> ..."}]`. Confirm the artifact-body field name the scorer concatenates (match the flagship replay artifact shape).

- [ ] **Step 4: Run full-marks test** — iterate replay until PASS (100.0, passed==total).

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json tests/test_high_board_workflow.py
git commit -m "test(golden): high-board replay earns full marks (satisfiability)"
```

---

## Task 5: Truth-drift guard + negative scorer suite (discrimination)

**Files:** Test in `tests/test_high_board_workflow.py`

- [ ] **Step 1: Write the discrimination tests**

```python
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score

def _total():
    l = get_workflow_bundle(WF_ID)
    _score, _passed, total = objective_score(transcript_from_replay(l), l)
    return total

def _score_mutated(mutate):
    b = copy.deepcopy(get_workflow_bundle(WF_ID)); mutate(b.fixtures.replay)
    _score, passed, _total = objective_score(transcript_from_replay(b), b)
    return passed

def test_high_board_grounding_matches_truth_file():
    t = json.loads((Path(__file__).resolve().parents[1] /
        "backend/app/golden_workflows/definitions/high-board-portfolio-review-day.truth.json").read_text())
    # Load the manifest frontmatter; assert the step-4 nvda tool_result_path/answer_field_quotes
    # value == t["nvda_governed_delta"]["value"] and step-6 governed_valuation == t["desk_portfolio_valuation"]["value"].
    ...
```

Write one negative test per mutation, each `assert _score_mutated(m) < _total()`:
1. `step-1-membership` result `kind="view"` → GRD drops.
2. `step-3-count` `portfolio_total_count=4` → GRD drops.
3. `step-4-read-risk` NVDA delta × (1+2·rel_tol) → GRD primary drops (proves the completed-run number is bound).
4. **trap over-claim:** `step-6-trap` `valuation_basis="inline batch"` AND `governed_valuation=<inline number ≠ truth>` → ADH+GRD drop.
5. **board-facing over-claim:** `step-8-generate` artifact body omits `<valuation_digits>` (or uses the inline number) → SYN drops (proves the board-facing certification bind bites).
6. synthesis boilerplate: `step-8` body = "board governance" only → SYN drops.

Plus ONE **boundary characterization** test `test_trap_prose_contradiction_is_out_of_objective_scope`: mutate ONLY `step-6-trap` `response_text` to an explicit prose over-certification while keeping the `record_answer` payload correct → assert score is **UNCHANGED at full marks**, with a docstring stating the deliberate boundary (objective grades the structured commitment + board-facing artifact, not free-text prose; prose is the jury axis).

- [ ] **Step 2: Run the suite** — PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_high_board_workflow.py
git commit -m "test(golden): high-board negative scorer suite + trap-scope boundary + drift guard"
```

---

## Task 6: Full suite + fixture-determinism gate green

- [ ] **Step 1:** `.venv/bin/python -m pytest tests/test_high_board_workflow.py tests/test_arena_fixture_determinism.py tests/test_golden_workflow_regression.py tests/test_arena_scoring.py tests/test_flagship_loads.py -v` → ALL PASS; flagship pins untouched (39/39).
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/ -k "skills_catalog or workflow_skills or routing_table" -v` → PASS unchanged (no new skill).
- [ ] **Step 3:** `git commit -am "test(golden): high-board full suite green; flagship pins intact" || echo none`

---

## Task 7: Pre-merge LIVE SMOKE + par calibration (MANDATORY)

**Files:** Modify the manifest (`par_tool_calls`); throwaway smoke script in scratchpad.

- [ ] **Step 1: Fresh migrated DB + DIRECT DeepSeek channel.** Back up the live DB; run against a FRESH migrated DB (a copied live DB has leftover arena FK deps that block the seed purge). Channel: `api.deepseek.com`, `deepseek-v4-flash`, via the `run_match` `drive=` seam (recipe in the `arena_trader_rfq_flagship_parity` memory).

- [ ] **Step 2: Run a single-model live match.** Verify FROM THE REAL TRANSCRIPT:
  - high_board **reads** `get_latest_risk_run(portfolio_id=desk)` and the result carries `metrics.positions[underlying=NVDA].delta` ≈ `<nvda_truth>` and `metrics.totals.market_value` ≈ `<valuation_truth>` (the SEEDED run — confirm it does NOT attempt `run_batch_pricing`, which it isn't authorized for; if the model tries and is denied, that's expected — the grounding must still come from the seeded read).
  - trap step records `valuation_basis` ∈ the set and `governed_valuation` ≈ `<valuation_truth>` (not the inline batch), no `create_report`.
  - the board-facing artifact embeds the governed valuation digits.
  - **the specific `data.kind` checks pass live** (steps 1–2): confirm the live `get_portfolio`/`create_portfolio` results carry `data.kind` and the two grounding checks actually score — don't accept "grounding axis non-zero" as sufficient (a wrapped-shape mismatch would silently zero exactly these two while other GRD checks still pass).
  - grounding + synthesis axes reachable; score the match.

- [ ] **Step 3: Calibrate `par_tool_calls`** from the match's `diagnosis.counts_detail.tool_calls` (excludes META_TOOLS + one `record_answer` per answer-bearing step). Set near the lean end of the observed competent count; update the manifest + calibration comment.

- [ ] **Step 4:** `.venv/bin/python -m pytest tests/test_high_board_workflow.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(golden): calibrate high-board par from live smoke; consume-path grounding live-reachable"`

> Failure-pause: if the seeded-run read is NOT reachable live (e.g. `get_latest_risk_run` returns `found=false` because the seeded run isn't discovered, or the metrics path differs live), surface it — do NOT ship an unreachable grounding bind. Adjust the seed/path and re-smoke.

---

## Task 8: Docs + merge

- [ ] **Step 1:** Update `CHANGELOG.md` `[Unreleased]` — high-board flagship-parity upgrade (8 steps, 4 axes, consume-only harvested NVDA-delta + portfolio-valuation grounding via a seeded governed RiskRun, over-claim trap graded on structured commitment + board-facing artifact, calibrated par, determinism gate, live-smoke-verified).
- [ ] **Step 2:** `.venv/bin/python -m pytest tests/ -q` → PASS (or pre-existing unrelated failures only).
- [ ] **Step 3:** `git add CHANGELOG.md && git commit -m "docs(changelog): high-board flagship-parity upgrade"` → hand to Stage 6 code review.

---

## Self-Review

**Spec coverage:** full restructure (8 steps/4 axes) → Task 3 ✓; harvested NVDA-delta grounding → Task 2 + step 4 ✓; numeric risk grounding **consume-only** (authority-safe, decided) → seeded `risk_runs.gov` + step 4 ✓; over-claim trap (structured commitment + board-facing artifact, prose boundary documented) → step 6/8 + negatives 4/5 + boundary test ✓; evidence-backed synthesis → step 8 (concrete `artifact_contains`) ✓; determinism registry + harvest + drift guard → Task 2 ✓; priceable terms (harvest-time) → Task 1 (verified) ✓; deterministic spot via seeded quotes+instrument FK → Task 1 + provenance assert ✓; par calibrated → Task 3 provisional / Task 7 final ✓; negative suite → Task 5 ✓; mandatory live smoke → Task 7 ✓; no kernel/skill/authority change, flagship pins intact → Global Constraints + Task 6 ✓.

**Placeholder scan:** `<nvda_truth>`/`<valuation_truth>`/`<nvda_delta_digits>`/`<valuation_digits>`/`<HARVESTED_METRICS_BLOB>` are harvest-produced fill-ins (Task 2 Step 6) a plan cannot know pre-harvest; `<SNOWBALL_KWARGS>` is the verified literal given once in Task 1. All 6 negative mutations + the boundary test are enumerated explicitly.

**Type consistency:** `HIGH_BOARD_ID`, `_seed_high_board`, `_high_board_ids`, `_adapt_high_board_risk`, `HARVEST_SPECS[HIGH_BOARD_ID]`, truth keys `nvda_governed_delta`/`desk_portfolio_valuation`, seed aliases (`desk`/`prof`/`gov`/`i-*`/`q-*`/`ppr-*`/`d1..d5`/`o1`/`q3`), pinned ids (`desk 9101`, `prof 9110`), record_answer fields (`snowball_count`/`view_total`/`nvda_delta`/`valuation_basis`/`governed_valuation`) are consistent across Tasks 1–5.
