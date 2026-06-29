# Trader RFQ-to-Booking Golden Workflow — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a second golden workflow (`trader-rfq-booking-day`, trader persona) that drives a client RFQ from intake through quote, approval, build, booking, verification and book-impact pricing — plus the harness hardening that this first live-RFQ-creating workflow exposes.

**Architecture:** The workflow ships as one markdown definition + one `*.fixtures.json` in `backend/app/golden_workflows/definitions/`, cited skills/tools all pre-existing. Two harness changes support it: an `rfq` seed namespace in `fixtures.py` (forward-looking), and post-match cleanup of live-created arena RFQs by `rfq_id`s harvested from the match trace.

**Tech Stack:** Python 3.11, SQLAlchemy ORM (`app.models`), Pydantic v2, pytest. The golden-workflows package (`app.golden_workflows`) and arena services (`app.services.arena`). Live validation uses the real desk orchestrator via Zenmux.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-06-29-trader-rfq-booking-workflow-design.md` — this plan implements it.
- Name-based fixtures: no `$seed` ids; assertions use names / `response_contains`. Arena ownership markers (`Portfolio.tags=["arena"]`, `PricingParameterProfile.summary["arena_owned"]=True`) are applied by `run_match` AT RUNTIME after `apply_seed` (`runner.py:329-334`) — do NOT put them in the fixtures JSON (`apply_seed` ignores them; the flagship fixtures carry none).
- Registry reference checks (already enforced): every `expected_skill` must map to a real `SKILL.md` (`name:` field), every `expected_tools` entry to a real tool in `all_agent_tools()` (the `_tool` suffix is stripped on both sides), every `step.replay` key to a `replay` entry in the fixtures.
- QuantArk product convention: real class names (`BarrierOption`, `EuropeanVanillaOption`), native `product_kwargs` (`maturity`, NOT `maturity_years`, on seeded positions). Per `app/skills/references/products/build-contract.md`.
- Env (live steps only): run in the PRIMARY checkout on `main` (quant-ark on the venv `.pth`; `config/agent_channels.yaml` is gitignored). Always `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python` — anaconda `python` shadows the venv.
- Test runner from this worktree: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/...`.
- Reward end-state, never mandate a rejection (spec §6). A model that builds correctly first try must score full marks.

---

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `backend/app/golden_workflows/fixtures.py` | Add `rfqs` seed namespace + optional `positions.rfq` FK | 1 |
| `tests/test_golden_fixtures_rfq.py` (new) | Unit tests for the `rfqs` namespace | 1 |
| `backend/app/services/arena/trace_harvest.py` | Add `collect_rfq_ids_touched(thread_id, store=None)` | 2 |
| `backend/app/services/arena/runner.py` | `_purge_arena_rfqs` + wire into `run_match` | 2 |
| `tests/test_arena_rfq_cleanup.py` (new) | Unit tests for harvest + purge | 2 |
| `docs/superpowers/findings/2026-06-29-barrier-build-probe.md` (new) | Probe findings: clean DOWN_IN terms, engine, Layer-2 value | 3 |
| `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md` (new) | The 8-step workflow definition + objective manifest | 4 |
| `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json` (new) | Seed + replay | 4 |
| `tests/test_trader_rfq_workflow.py` (new) | Registry load + regression replay | 4, 5 |

---

## Task 1: `rfq` seed namespace in `fixtures.py`

**Files:**
- Modify: `backend/app/golden_workflows/fixtures.py` (`_NAMESPACES`, `_FK`, `_INSERT_ORDER`, FK-validation loop ~`fixtures.py:112-119`, `apply_seed` ~`fixtures.py:162-240`)
- Test: `tests/test_golden_fixtures_rfq.py` (new)

**Interfaces:**
- Consumes: `app.models.RFQ` (cols: `client_name`, `channel`, `status`, `request_payload`, `quote_payload`, `approved_response`, `id`); `RfqStatus` enum.
- Produces: a seedable `rfqs` namespace (required keys `{alias, status}`); an optional `positions.rfq` alias → `Position.rfq_id`. `apply_seed` returns `ids["rfqs"][alias] -> rfq_id` as for other namespaces.

- [ ] **Step 1: Write the failing test**

Create `tests/test_golden_fixtures_rfq.py`:

```python
from app.golden_workflows.fixtures import FixtureBundle, apply_seed, load_fixtures
from app.golden_workflows.schema import WorkflowError
from app import models


def _seed_only(seed: dict) -> FixtureBundle:
    return FixtureBundle(seed=seed, replay={})


def test_rfq_namespace_seeds_row(db_session):
    bundle = _seed_only({
        "rfqs": [{"alias": "r1", "status": "submitted", "client_name": "ARENA Client"}],
    })
    ids = apply_seed(bundle, db_session)
    rid = ids["rfqs"]["r1"]
    row = db_session.get(models.RFQ, rid)
    assert row.status == "submitted"
    assert row.client_name == "ARENA Client"


def test_position_links_seeded_rfq(db_session):
    bundle = _seed_only({
        "portfolios": [{"alias": "p", "name": "T1 Portfolio"}],
        "rfqs": [{"alias": "r1", "status": "submitted"}],
        "positions": [{
            "alias": "pos1", "portfolio": "p", "rfq": "r1",
            "underlying": "MSFT", "product_type": "EuropeanVanillaOption", "quantity": 1,
        }],
    })
    ids = apply_seed(bundle, db_session)
    pos = db_session.get(models.Position, ids["positions"]["pos1"])
    assert pos.rfq_id == ids["rfqs"]["r1"]


def test_position_without_rfq_still_seeds(db_session):
    # The positions.rfq FK is OPTIONAL: a position with no rfq must still validate+seed.
    bundle = _seed_only({
        "portfolios": [{"alias": "p", "name": "T2 Portfolio"}],
        "positions": [{
            "alias": "pos1", "portfolio": "p",
            "underlying": "MSFT", "product_type": "EuropeanVanillaOption", "quantity": 1,
        }],
    })
    ids = apply_seed(bundle, db_session)
    pos = db_session.get(models.Position, ids["positions"]["pos1"])
    assert pos.rfq_id is None
```

> If `db_session` is not an existing fixture, mirror the session fixture already used by the golden-workflows tests — check `tests/test_match_transcript.py` / `tests/conftest.py` for the in-memory session fixture name and reuse it verbatim.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_golden_fixtures_rfq.py -v`
Expected: FAIL — `UnknownSeedNamespaceError: rfqs` (namespace not registered).

- [ ] **Step 3: Register the namespace, FK, insert order, and column allowlist**

In `backend/app/golden_workflows/fixtures.py`, add `rfqs` to `_NAMESPACES`:

```python
    "rfqs": {"alias", "status"},
```

Add the optional FK edge (positions may reference an rfq):

```python
_FK: dict[str, dict[str, str]] = {
    "positions": {"portfolio": "portfolios", "rfq": "rfqs"},
    "pricing_parameter_rows": {"profile": "pricing_profiles"},
    "risk_runs": {"portfolio": "portfolios"},
}
```

Put `rfqs` before `positions` in `_INSERT_ORDER`:

```python
_INSERT_ORDER = [
    "portfolios", "pricing_profiles", "pricing_parameter_rows", "rfqs", "positions", "risk_runs",
]
```

Add an RFQ column allowlist near `_RISK_RUN_COLS`:

```python
# Column allowlist for the rfqs seed namespace (beyond "alias"/"status").
_RFQ_COLS: frozenset[str] = frozenset({
    "client_name", "channel", "status", "request_payload",
    "quote_payload", "approved_response",
})
```

- [ ] **Step 4: Make the FK validator skip absent optional fields**

The FK-validation loop (`fixtures.py:112-119`) currently requires every FK field to resolve. Make it skip a field that a row omits (required-ness is still enforced by `_NAMESPACES`). Replace the inner loop body:

```python
    for ns, fks in _FK.items():
        for row in seed.get(ns, []):
            for fld, target_ns in fks.items():
                if fld not in row:
                    continue  # optional FK (e.g. positions.rfq) — absent is fine
                ref = row.get(fld)
                if ref not in aliases.get(target_ns, set()):
                    raise UnresolvedAliasError(
                        f"{ns}.{row.get('alias')}.{fld} -> {target_ns}.{ref}"
                    )
```

- [ ] **Step 5: Add the `apply_seed` branch for `rfqs` and resolve `positions.rfq`**

In `apply_seed`, add an `rfqs` branch (place it with the other `elif`s, before the `else`):

```python
            elif ns == "rfqs":
                extra = {
                    k: v for k, v in row.items()
                    if k != "alias" and k in _RFQ_COLS
                }
                if "id" in row:
                    extra["id"] = row["id"]
                obj = models.RFQ(**extra)
```

Update the existing `positions` branch to resolve the optional `rfq` alias:

```python
            elif ns == "positions":
                portfolio_id = _parent_id("portfolios", row["portfolio"])
                extra = {
                    k: v for k, v in row.items()
                    if k not in ("alias", "portfolio", "rfq")
                }
                if "rfq" in row:
                    extra["rfq_id"] = _parent_id("rfqs", row["rfq"])
                obj = models.Position(portfolio_id=portfolio_id, **extra)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_golden_fixtures_rfq.py -v`
Expected: PASS (3 tests).

- [ ] **Step 7: Run the golden/fixtures regression to confirm no break**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests -k "golden or fixtures" -q`
Expected: PASS (existing fixtures tests still green; the optional-FK change is backward-compatible).

- [ ] **Step 8: Commit**

```bash
git add backend/app/golden_workflows/fixtures.py tests/test_golden_fixtures_rfq.py
git commit -m "feat(golden): add rfq seed namespace + optional positions.rfq FK"
```

---

## Task 2: Harvest created RFQ ids + purge them after each match

**Files:**
- Modify: `backend/app/services/arena/trace_harvest.py` (add `collect_rfq_ids_touched`)
- Modify: `backend/app/services/arena/runner.py` (add `_purge_arena_rfqs`; call after `harvest` in `run_match` ~`runner.py:357`)
- Test: `tests/test_arena_rfq_cleanup.py` (new)

**Interfaces:**
- Consumes: `TraceStore` spans for a thread (same source `transcript_from_trace` reads); `app.models.RFQ`.
- Produces: `collect_rfq_ids_touched(thread_id: int, store=None) -> set[int]` — the rfq ids appearing in this thread's RFQ-tool span outputs (touched, NOT necessarily created). `_purge_arena_rfqs(session, rfq_ids: set[int]) -> None` — ORM-deletes those RFQs (cascading `rfq_quote_versions` + `approvals`). `run_match` snapshots `max(rfqs.id)` before driving and only deletes harvested ids **above** that baseline (creation proof) — `quote_rfq`/`submit_rfq_for_approval` act on existing rows and `create_or_update_rfq_draft` can update, so a harvested id alone is not proof of creation.

- [ ] **Step 1: Probe the rfq tool-output shape (record the id path)**

Before writing the collector, confirm where `rfq_id` lives in the harvested tool output. Run a tiny probe in the PRIMARY checkout:

```bash
cd /Users/fuxinyao/open-otc-trading
PYTHONPATH=backend .venv/bin/python -c "
from app.tools.rfq import create_or_update_rfq_draft_tool
import inspect
print(inspect.getsource(create_or_update_rfq_draft_tool))
" | grep -iE "return|rfq_id|id|RFQOut|model_dump" | head
```

Record in the test below the key under which the id appears (expected: a top-level `rfq_id` or `id`). The collector must read whatever key the tool actually returns — adjust the `_extract_rfq_id` helper in Step 3 to match.

- [ ] **Step 2: Write the failing test**

Create `tests/test_arena_rfq_cleanup.py`:

```python
from app.services.arena.trace_harvest import collect_rfq_ids_touched
from app.services.arena import runner
from app import models


def test_collect_rfq_ids_touched_from_spans(monkeypatch):
    # A fake store returning one tool span whose output carries rfq_id=42.
    spans = [{
        "run_type": "tool",
        "name": "create_or_update_rfq_draft",
        "outputs": {"output": {"name": "create_or_update_rfq_draft",
                                "tool_call_id": "c1", "rfq_id": 42}},
    }]

    class _Store:
        def spans_for_thread(self, thread_id):  # match the real TraceStore method name
            return spans

    ids = collect_rfq_ids_touched(thread_id=1, store=_Store())
    assert ids == {42}


def test_purge_arena_rfqs_cascades(db_session):
    rfq = models.RFQ(status="submitted", client_name="ARENA")
    db_session.add(rfq)
    db_session.flush()
    db_session.add(models.RFQQuoteVersion(rfq_id=rfq.id, version=1))
    db_session.commit()
    rid = rfq.id

    runner._purge_arena_rfqs(db_session, {rid})
    db_session.commit()

    assert db_session.get(models.RFQ, rid) is None
    assert db_session.query(models.RFQQuoteVersion).filter_by(rfq_id=rid).count() == 0
```

> Confirm the real `TraceStore` reader method name (the test stub must match what `collect_rfq_ids_touched` calls) by checking how `transcript_from_trace` loads spans in `trace_harvest.py`; reuse that exact method. Confirm `RFQQuoteVersion`'s required columns (e.g. `version`, and any non-null `payload`) and fill them so the row inserts.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_arena_rfq_cleanup.py -v`
Expected: FAIL — `ImportError: cannot import name 'collect_rfq_ids_touched'`.

- [ ] **Step 4: Implement `collect_rfq_ids_touched` in `trace_harvest.py`**

```python
# RFQ tools whose outputs carry an rfq id. These TOUCH an rfq (create OR update,
# quote, submit) — touched != created, so run_match filters by an id baseline.
_RFQ_TOOLS = {"create_or_update_rfq_draft", "quote_rfq", "submit_rfq_for_approval"}


def _extract_rfq_id(content: Any) -> int | None:
    if isinstance(content, dict):
        for key in ("rfq_id", "id"):
            v = content.get(key)
            if isinstance(v, int):
                return v
    return None


def collect_rfq_ids_touched(thread_id: int, store=None) -> set[int]:
    """Return the rfq ids appearing in this thread's RFQ-tool span outputs.

    These are ids the agent TOUCHED (created or merely quoted/submitted/updated).
    The caller (run_match) intersects this with an "id > pre-match baseline" guard
    to delete only RFQs CREATED during the match — never a pre-existing real or
    seeded RFQ the agent referenced. Needed because RFQ has no
    portfolio_id/position_id column and direct book_position leaves
    Position.rfq_id null, so the portfolio-scoped purge cannot reach them.
    """
    store = store or _default_store()
    out: set[int] = set()
    for sp in store.spans_for_thread(thread_id):
        if sp.get("run_type") != "tool" or sp.get("name") not in _RFQ_TOOLS:
            continue
        content, _name, _tcid = _parse_tool_output(sp.get("outputs"))
        rid = _extract_rfq_id(content)
        if rid is not None:
            out.add(rid)
    return out
```

> Use the SAME store accessor and span-reader method that `transcript_from_trace` uses (replace `_default_store()` / `spans_for_thread` with the real names found in Step 1's neighbouring code). `_parse_tool_output` already exists in this module.

- [ ] **Step 5: Implement `_purge_arena_rfqs` in `runner.py`**

```python
def _purge_arena_rfqs(session, rfq_ids) -> None:
    """ORM-delete the given RFQ rows so quote_versions/approvals cascade
    (both relationships are cascade='all, delete-orphan')."""
    if not rfq_ids:
        return
    from app import models

    for rfq in session.query(models.RFQ).filter(models.RFQ.id.in_(list(rfq_ids))):
        session.delete(rfq)
    session.commit()
```

- [ ] **Step 6: Capture the rfq baseline before driving, and wire filtered cleanup after harvest**

In `run_match`, BEFORE the step-driving loop (`for wf_step in workflow.steps:` ~line 353), snapshot the current max rfq id so we can prove which RFQs are new:

```python
    # High-water mark for RFQs so post-match cleanup only deletes rows CREATED
    # during this match (a harvested id <= baseline was merely touched, e.g. a
    # pre-existing RFQ the agent quoted — never delete it).
    from sqlalchemy import func
    with database.SessionLocal() as session:
        rfq_id_baseline = session.query(func.max(models.RFQ.id)).scalar() or 0
```

After `transcript = harvest(thread_id, workflow, model)` (~line 357) and before the artifact-copy loop, add:

```python
    # Delete only RFQs created during this match: harvested (touched) ids that are
    # also above the pre-match baseline. See collect_rfq_ids_touched / _purge_arena_rfqs.
    touched = collect_rfq_ids_touched(thread_id)
    created = {rid for rid in touched if rid > rfq_id_baseline}
    if created:
        with database.SessionLocal() as session:
            _purge_arena_rfqs(session, created)
```

Ensure `models` is imported in `run_match`'s scope (it is imported lazily elsewhere in this module — add `from app import models` at the top of `run_match` if not already in scope). Add the harvest import at the top with the other arena imports:

```python
from app.services.arena.trace_harvest import collect_rfq_ids_touched, transcript_from_trace
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_arena_rfq_cleanup.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Run the arena regression to confirm no break**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests -k "arena" -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/arena/trace_harvest.py backend/app/services/arena/runner.py tests/test_arena_rfq_cleanup.py
git commit -m "feat(arena): purge live-created RFQs via trace-harvested rfq_ids after each match"
```

---

## Task 3: Probe `build_product` for the barrier put (resolve spec §6 Layer 2)

**Files:**
- Create: `docs/superpowers/findings/2026-06-29-barrier-build-probe.md` (findings the workflow task consumes)

**Interfaces:**
- Produces: the exact `terms` dict for a clean `BarrierOption` DOWN_IN build (`ok==true`), its `engine_name`, the DOWN_OUT-default behaviour when `barrier_type` is omitted, and a Layer-2 barrier value that yields `ok==false, missing==[]` — or a documented conclusion that none exists (→ Layer 2 dropped, Layer 1 stands).

- [ ] **Step 1: Run the build probe (PRIMARY checkout)**

```bash
cd /Users/fuxinyao/open-otc-trading
PYTHONPATH=backend .venv/bin/python - <<'PY'
from app.tools.products import build_product_tool
def call(**terms):
    r = build_product_tool.invoke({"family": "BarrierOption", "terms": terms})
    print(terms, "=>", {"ok": r.get("ok"), "missing": r.get("missing"),
                        "engine": r.get("engine_name"),
                        "barrier_type": (r.get("product_spec") or {}).get("terms", {}).get("barrier_type")})
base = dict(initial_price=100, strike=100, barrier=80, maturity_years=1, option_type="PUT")
call(**base, barrier_type="DOWN_IN")     # expect ok=True, DOWN_IN
call(**base)                             # expect ok=True, default DOWN_OUT (Layer 1 trap)
call(**{**base, "barrier": 120}, barrier_type="DOWN_IN")  # down barrier ABOVE spot — Layer 2 candidate
call(**{**base, "barrier": 100}, barrier_type="DOWN_IN")  # barrier == spot — Layer 2 candidate
PY
```

> The exact tool-call shape (`build_product_tool.invoke({...})` vs positional) must match the tool's signature — adjust to whatever the other golden-workflow probes used (grep scratchpad `fixture_validate`/`price_probe` from the flagship for the invocation idiom).

- [ ] **Step 2: Record findings**

Create `docs/superpowers/findings/2026-06-29-barrier-build-probe.md` capturing, verbatim from Step 1's output:
- The clean DOWN_IN `terms` dict and its `engine_name`.
- Confirmation that omitting `barrier_type` yields `ok==true` with DOWN_OUT (Layer 1).
- The first probed barrier value (if any) giving `ok==false, missing==[]` — the Layer-2 value. If both candidates either succeed or return a non-empty `missing`, write **"Layer 2 dropped — no clean validation rejection; Layer 1 stands alone."**

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/findings/2026-06-29-barrier-build-probe.md
git commit -m "docs(findings): barrier build probe — DOWN_IN terms, engine, Layer-2 value"
```

---

## Task 4: Write the workflow definition + fixtures

**Files:**
- Create: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md`
- Create: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json`
- Test: `tests/test_trader_rfq_workflow.py` (new)

**Interfaces:**
- Consumes: Task 3 findings (clean DOWN_IN terms, engine, Layer-2 value); the `rfqs` seed namespace (Task 1) is available but NOT used by this workflow (RFQ created live in step 1).
- Produces: a registry-loadable bundle `get_workflow_bundle("trader-rfq-booking-day")`.

- [ ] **Step 1: Write the failing registry test**

Create `tests/test_trader_rfq_workflow.py`:

```python
from app.golden_workflows.registry import get_workflow_bundle


def test_trader_rfq_bundle_loads():
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    wf = loaded.workflow
    assert wf.persona == "trader"
    assert [s.expected_skill for s in wf.steps] == [
        "intake-request", "quote-rfq", "submit-for-approval", "build-product",
        "book-position", "position-snapshot", "price-portfolio", "position-snapshot",
    ]
    # Every replay key referenced by a step exists in the fixtures.
    for s in wf.steps:
        assert s.replay in loaded.fixtures.replay
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py -v`
Expected: FAIL — `WorkflowError`/file-not-found (definition missing).

- [ ] **Step 3: Write the workflow definition**

Create `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md`. Use the clean DOWN_IN terms/engine from Task 3 in the step-4 outcome text. The objective manifest total is **provisional (33)** — Task 6 pins it against a live transcript.

```markdown
---
id: trader-rfq-booking-day
schema_version: 1
persona: trader
title: "Trader RFQ-to-Booking Day"
objective: >
  A trader takes a client RFQ for a 1-year down-and-in barrier put from intake
  through to a booked, verified position and reports its impact on the desk book:
  capture the request, quote it, route the quote for approval, build the
  QuantArk product, book it into the control portfolio, verify the booked terms
  against the RFQ, price the book with the new position, and report the net delta
  impact.
fixtures: trader-rfq-booking-day.fixtures.json
tags: [flagship, trader, rfq, booking, desk-workflow]

steps:
  - user: "A client wants a 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in at 80%. Capture it as an RFQ for the Arena Trader Desk."
    expected_skill: intake-request
    expected_tools:
      - name: create_or_update_rfq_draft
    outcome: >
      The agent captures the request as an RFQ draft and returns its id.
    assertions:
      - type: response_contains
        any_of: ["MSFT"]
    replay: step-1-intake

  - user: "Quote it using the Arena Trader Profile."
    expected_skill: quote-rfq
    expected_tools:
      - name: solve_rfq
      - name: quote_rfq
    outcome: >
      The agent solves the draft and persists a quote, reporting the solved value
      and engine.
    assertions:
      - type: response_contains
        any_of: ["quote", "solved", "engine"]
    replay: step-2-quote

  - user: "Route the quote for approval."
    expected_skill: submit-for-approval
    expected_tools:
      - name: submit_rfq_for_approval
    outcome: >
      The agent submits the quoted RFQ for governance approval.
    assertions:
      - type: response_contains
        any_of: ["submitted", "approval"]
    replay: step-3-submit

  - user: "Risk has the quote. Build the product so we can book it — 1-year down-and-in barrier put on MSFT, strike at-the-money, knock-in at 80%."
    expected_skill: build-product
    expected_tools:
      - name: fetch_market_snapshot
      - name: build_product
    outcome: >
      The agent builds a validated BarrierOption with barrier_type DOWN_IN.
    assertions:
      - type: response_contains
        any_of: ["down-and-in", "DOWN_IN", "down and in"]
    replay: step-4-build

  - user: "Approved — book it into the Arena Trader Desk portfolio."
    expected_skill: book-position
    expected_tools:
      - name: book_position
    outcome: >
      The agent books the validated product as a position and returns the id.
    assertions:
      - type: task_returned_id
        tool: book_position
    replay: step-5-book

  - user: "Show me the booked position — does it match the RFQ?"
    expected_skill: position-snapshot
    expected_tools:
      - name: get_position_summaries
    outcome: >
      The agent reads the booked position and confirms the down-and-in barrier
      at 80% matches the RFQ.
    assertions:
      - type: response_contains
        any_of: ["80", "down-and-in", "DOWN_IN"]
    replay: step-6-snapshot

  - user: "Now price the Arena Trader Desk book with this position in it."
    expected_skill: price-portfolio
    expected_tools:
      - name: run_batch_pricing
    outcome: >
      The agent queues a batch-pricing run over the portfolio and returns the id.
    assertions:
      - type: task_returned_id
        tool: run_batch_pricing
    replay: step-7-price

  - user: "What's the net delta impact of the new trade on the book?"
    expected_skill: position-snapshot
    expected_tools:
      - name: get_latest_position_valuations
    outcome: >
      The agent reads the fresh valuations and reports the new position's delta
      contribution to the book.
    assertions:
      - type: response_contains
        any_of: ["delta"]
    replay: step-8-impact

success: end
```

> Verify `success: end` matches the flagship's terminal marker (`risk-manager-control-day.md` uses `success: end`) — copy whatever value/format it uses. If the schema names step assertions differently (e.g. `task_returned_id` field is `tool` vs `tool_name`), copy the exact field names from the flagship definition.

- [ ] **Step 4: Write the fixtures (seed + first-pass replay)**

Create `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json`. Seed the control portfolio, profile, parameter rows (one per underlying — MSFT for the new trade, AAPL+NVDA for the existing book), and the existing book. Spot defaults to 100 (positions not instrument-linked), so ATM strikes give non-zero Greeks.

```json
{
  "schema_version": 1,
  "seed": {
    "portfolios": [
      {"alias": "desk", "name": "Arena Trader Desk"}
    ],
    "pricing_profiles": [
      {"alias": "prof", "name": "Arena Trader Profile", "valuation_date": "2026-06-29"}
    ],
    "pricing_parameter_rows": [
      {"alias": "pr_msft", "profile": "prof", "symbol": "MSFT", "rate": 0.04, "dividend_yield": 0.01, "volatility": 0.28},
      {"alias": "pr_aapl", "profile": "prof", "symbol": "AAPL", "rate": 0.04, "dividend_yield": 0.005, "volatility": 0.30},
      {"alias": "pr_nvda", "profile": "prof", "symbol": "NVDA", "rate": 0.04, "dividend_yield": 0.0, "volatility": 0.45}
    ],
    "positions": [
      {"alias": "ex_aapl", "portfolio": "desk", "underlying": "AAPL", "product_type": "EuropeanVanillaOption", "quantity": 200,
       "product_kwargs": {"strike": 100, "maturity": 1.0, "option_type": "CALL"}},
      {"alias": "ex_nvda", "portfolio": "desk", "underlying": "NVDA", "product_type": "BarrierOption", "quantity": 100,
       "product_kwargs": {"strike": 100, "barrier": 80, "maturity": 1.0, "option_type": "PUT", "barrier_type": "DOWN_IN"}}
    ]
  },
  "replay": {
    "step-1-intake": {
      "ai": {"content": "Captured the request as an RFQ draft.", "tool_calls": [{"id": "c1", "name": "create_or_update_rfq_draft_tool", "args": {}}]},
      "tool_results": [{"tool_call_id": "c1", "name": "create_or_update_rfq_draft_tool", "content": {"rfq_id": 9001, "status": "draft"}}],
      "skills_routed": ["intake-request"], "artifacts": [],
      "response_text": "Captured the MSFT down-and-in barrier put as RFQ 9001 (draft)."},
    "step-2-quote": {
      "ai": {"content": "Solving and quoting the draft.", "tool_calls": [{"id": "c2a", "name": "solve_rfq_tool", "args": {}}, {"id": "c2b", "name": "quote_rfq_tool", "args": {}}]},
      "tool_results": [
        {"tool_call_id": "c2a", "name": "solve_rfq_tool", "content": {"rfq_id": 9001, "solved_value": 3.21, "engine": "BarrierAnalyticalEngine"}},
        {"tool_call_id": "c2b", "name": "quote_rfq_tool", "content": {"rfq_id": 9001, "quote_id": 1, "solved_value": 3.21, "engine": "BarrierAnalyticalEngine", "status": "quoted"}}],
      "skills_routed": ["quote-rfq"], "artifacts": [],
      "response_text": "Quoted RFQ 9001 at 3.21 via the BarrierAnalyticalEngine (quoted)."},
    "step-3-submit": {
      "ai": {"content": "Submitting the quoted RFQ for approval.", "tool_calls": [{"id": "c3", "name": "submit_rfq_for_approval_tool", "args": {}}]},
      "tool_results": [{"tool_call_id": "c3", "name": "submit_rfq_for_approval_tool", "content": {"rfq_id": 9001, "prior_state": "quoted", "new_state": "submitted"}}],
      "skills_routed": ["submit-for-approval"], "artifacts": [],
      "response_text": "Submitted RFQ 9001 for approval (quoted -> submitted)."},
    "step-4-build": {
      "ai": {"content": "Fetching spot and building the product.", "tool_calls": [{"id": "c4a", "name": "fetch_market_snapshot_tool", "args": {}}, {"id": "c4b", "name": "build_product_tool", "args": {}}]},
      "tool_results": [
        {"tool_call_id": "c4a", "name": "fetch_market_snapshot_tool", "content": {"underlying": "MSFT", "spot": 100.0}},
        {"tool_call_id": "c4b", "name": "build_product_tool", "content": {"ok": true, "engine_name": "BarrierAnalyticalEngine", "product_spec": {"product_family": "BarrierOption", "terms": {"strike": 100, "barrier": 80, "maturity_years": 1, "option_type": "PUT", "barrier_type": "DOWN_IN"}}}}],
      "skills_routed": ["build-product"], "artifacts": [],
      "response_text": "Built a down-and-in BarrierOption (DOWN_IN, KI 80%) on MSFT."},
    "step-5-book": {
      "ai": {"content": "Booking the validated product.", "tool_calls": [{"id": "c5", "name": "book_position_tool", "args": {}}]},
      "tool_results": [{"tool_call_id": "c5", "name": "book_position_tool", "content": {"position_id": 5001, "portfolio": "Arena Trader Desk", "family": "BarrierOption"}}],
      "skills_routed": ["book-position"], "artifacts": [],
      "response_text": "Booked position 5001 into Arena Trader Desk."},
    "step-6-snapshot": {
      "ai": {"content": "Reading the booked position.", "tool_calls": [{"id": "c6", "name": "get_position_summaries_tool", "args": {}}]},
      "tool_results": [{"tool_call_id": "c6", "name": "get_position_summaries_tool", "content": {"positions": [{"id": 5001, "underlying": "MSFT", "barrier_type": "DOWN_IN", "barrier": 80}]}}],
      "skills_routed": ["position-snapshot"], "artifacts": [],
      "response_text": "Booked position matches the RFQ: down-and-in barrier at 80 on MSFT."},
    "step-7-price": {
      "ai": {"content": "Queuing batch pricing over the book.", "tool_calls": [{"id": "c7", "name": "run_batch_pricing_tool", "args": {}}]},
      "tool_results": [{"tool_call_id": "c7", "name": "run_batch_pricing_tool", "content": {"task_id": 7001, "status": "queued"}}],
      "skills_routed": ["price-portfolio"], "artifacts": [],
      "response_text": "Queued batch pricing 7001 over Arena Trader Desk."},
    "step-8-impact": {
      "ai": {"content": "Reading fresh valuations.", "tool_calls": [{"id": "c8", "name": "get_latest_position_valuations_tool", "args": {}}]},
      "tool_results": [{"tool_call_id": "c8", "name": "get_latest_position_valuations_tool", "content": {"positions": [{"id": 5001, "greeks": {"delta": -45.2}}]}}],
      "skills_routed": ["position-snapshot"], "artifacts": [],
      "response_text": "The new MSFT put adds about -45 delta to the book."}
  }
}
```

> Replay shape mirrors the flagship exactly: each `ai` has `content` + `tool_calls[{id,name,args}]`; each `tool_results` entry has `tool_call_id` + `name` (FULL name WITH `_tool` suffix, matching its tool_call) + `content`. The `content`/`skills_routed` values are a FIRST PASS — Task 6 reconciles them to live tool-output shapes (the flagship needed this). The seed `product_kwargs` for existing positions must use native QuantArk kwargs (`maturity`, not `maturity_years`); confirm against the flagship's `risk-manager-control-day.fixtures.json` positions for the exact key names the live batch path accepts.

- [ ] **Step 5: Run the registry test to verify it passes**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py -v`
Expected: PASS — bundle loads, all `expected_skill`/`expected_tools`/`replay` references resolve.

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/definitions/trader-rfq-booking-day.md backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json tests/test_trader_rfq_workflow.py
git commit -m "feat(golden): trader-rfq-booking-day workflow definition + fixtures"
```

---

## Task 5: Deterministic regression replay green

**Files:**
- Modify: `tests/test_trader_rfq_workflow.py` (add a regression-replay assertion)

**Interfaces:**
- Consumes: the bundle from Task 4 and the existing scripted-graph replay harness (the same one `risk-manager-control-day` uses for deterministic regression).

- [ ] **Step 1: Confirm the scoring API against the flagship test**

Read `tests/test_arena_scoring.py` to confirm the call convention for `objective_score`. The API is:
- `from app.golden_workflows.transcript import transcript_from_replay` → `transcript_from_replay(loaded) -> MatchTranscript`
- `from app.services.arena.scoring import objective_score` → `objective_score(transcript, loaded) -> (score_0_100, passed, total)`

- [ ] **Step 2: Write the regression test for the trader workflow**

Add to `tests/test_trader_rfq_workflow.py`:

```python
from app.golden_workflows.transcript import transcript_from_replay
from app.services.arena.scoring import objective_score


def test_trader_rfq_regression_replay_scores_full():
    loaded = get_workflow_bundle("trader-rfq-booking-day")
    transcript = transcript_from_replay(loaded)
    score, passed, total = objective_score(transcript, loaded)
    # The clean replay path should satisfy every objective check.
    assert passed == total, f"{passed}/{total} objective checks passed"
```

- [ ] **Step 3: Run it; fix replay/assertion mismatches until green**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests/test_trader_rfq_workflow.py -v`
Expected: PASS. If an assertion fails, the replay `content` shape doesn't match what the assertion reads — align the replay fixture to the assertion (NOT the reverse; assertions encode intended behaviour). Keep `response_text` containing the `response_contains` tokens (`MSFT`, `submitted`, `DOWN_IN`/`80`, `delta`).

- [ ] **Step 4: Run the full golden/arena subset**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests -k "golden or arena or fixtures or trader" -q`
Expected: PASS. If a count/exact-set assertion over workflow bundles surfaces (none found at plan time), update it to include `trader-rfq-booking-day`.

- [ ] **Step 5: Commit**

```bash
git add tests/test_trader_rfq_workflow.py
git commit -m "test(golden): trader-rfq regression replay scores full"
```

---

## Task 6: Live end-to-end validation + pin objective manifest + RFQ-cleanup proof

**Files:**
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.md` (pin manifest total; reconcile assertions to live shapes)
- Modify: `backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json` (reconcile replay to live shapes)

> Run in the PRIMARY checkout on `main` (quant-ark + gitignored config present). The flagship moved 6.5 → 77.4 through exactly this reconciliation — expect 1–3 iterations.

- [ ] **Step 1: Run one live match against a reference model**

Write this driver to the scratchpad and run it from the PRIMARY checkout. It drives the real desk orchestrator (`run_match`), harvests from the trace, and prints the objective breakdown:

```python
# scratchpad/live_trader_run.py
import json
from pathlib import Path
from app.golden_workflows.registry import get_workflow_bundle
from app.services.arena.models import get_model
from app.services.arena.runner import run_match
from app.services.arena.scoring import objective_score, objective_breakdown

loaded = get_workflow_bundle("trader-rfq-booking-day")
model = get_model("claude-sonnet-4-6")
transcript = run_match(loaded, model, artifact_root=Path("/tmp/arena_trader"))
score, passed, total = objective_score(transcript, loaded)
print(f"score={score:.1f} passed={passed}/{total}")
print(json.dumps(objective_breakdown(transcript, loaded), indent=2, default=str))
```

```bash
cd /Users/fuxinyao/open-otc-trading
PYTHONPATH=backend .venv/bin/python scratchpad/live_trader_run.py
```

- [ ] **Step 2: Reconcile assertions + replay to live tool shapes**

For each failing objective assertion, inspect the LIVE tool output in the harvested transcript and align: the assertion to the real response/tool-result shape, AND the replay fixture to the same shape (so Task 5's regression stays green). Common live-shape gaps (from the flagship): a tool returns `{metrics:{...}}` not a top-level field; `response_contains` tokens must appear in the live response text.

- [ ] **Step 3: Pin the objective manifest total**

Once a correct live run scores all objective points, replace the provisional `33` with the measured `objective_max` in the definition's manifest. Confirm residual failures are genuine MODEL VARIANCE (e.g. a model folding step-8's impact-read into step-7), NOT fixture gaps — do NOT over-tune so one model hits 100%.

- [ ] **Step 4: Prove RFQ cleanup across two consecutive matches**

```bash
cd /Users/fuxinyao/open-otc-trading
PYTHONPATH=backend .venv/bin/python - <<'PY'
from app import database, models
def count_arena_rfqs():
    with database.SessionLocal() as s:
        return s.query(models.RFQ).filter(models.RFQ.client_name.like("%ARENA%")).count()
# run two matches (reuse the Step-1 driver), asserting no growth
print("arena rfqs after 2 matches:", count_arena_rfqs())
PY
```

Expected: stable count (cleanup deletes each match's live RFQs). If RFQs accumulate, either the `collect_rfq_ids_touched` id-extraction path (Task 2 Step 1) didn't match the live tool output (fix `_extract_rfq_id`), or the baseline filter excluded a genuinely-new id (check the snapshot timing).

- [ ] **Step 5: Run the golden/arena regression once more, then commit**

Run: `PYTHONPATH=backend /Users/fuxinyao/open-otc-trading/.venv/bin/python -m pytest tests -k "golden or arena or fixtures or trader" -q`
Expected: PASS.

```bash
git add backend/app/golden_workflows/definitions/trader-rfq-booking-day.md backend/app/golden_workflows/definitions/trader-rfq-booking-day.fixtures.json
git commit -m "fix(golden): pin trader-rfq objective manifest + reconcile assertions to live shapes"
```

---

## Self-Review notes (spec coverage)

- Spec §4 (8 steps) → Task 4 definition. §5 (autonomy: yolo/cost-preview already in driver; build never cards because terms complete) → encoded in step user-turns (names all terms) + Task 6 validates no stall. §6 (two-layer discriminator) → Task 3 probe + Task 4 step-4. §7 (manifest) → Task 4 provisional, Task 6 pinned. §8 (fixtures) → Task 4 seed. §8.5.1 (RFQ cleanup via trace harvest) → Task 2. §8.5.2 (rfq seed namespace + tests) → Task 1. §9 (live-shape assertions, settle, env) → Task 6 + Global Constraints. §11 open items → Task 3 (Layer 2), Task 6 (manifest pin, live shapes), Task 4 (tickers chosen: MSFT/AAPL/NVDA), Task 2 Step 1 (rfq_id shape).
- Layer-2 dependency: if Task 3 finds no clean `ok==false, missing==[]` value, Layer 2 is dropped (documented in findings); Task 4/6 proceed with Layer 1 only — no plan change needed.
