# High-Board Portfolio Review Day — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `high-board-portfolio-review-day` golden/arena workflow (the first `high_board`-persona workflow) plus the small harness extensions it needs.

**Architecture:** A markdown workflow definition + JSON fixtures drive a 6-step desk-oversight review (resolve book → create a desk-scoped board-review View → count Snowball exposure → inline composition summary → pull a seeded prior governance report → draft a governance report). Four harness changes connect the production-wired `high_board` persona to the golden-workflow harness: a persona-enum entry, a `tool_not_called` assertion type, a `reports` seed namespace, and arena-runner persona-mapping + seeded-`ReportJob` cleanup.

**Tech Stack:** Python 3.11, Pydantic v2, SQLAlchemy, pytest. Backend package `backend/app`. Tests run from repo root with `.venv/bin/python -m pytest`.

## Global Constraints

- Run tests from the **repo root** with `.venv/bin/python -m pytest` (anaconda `python` shadows the venv; use `PYTHONPATH=backend .venv/bin/python` for ad-hoc imports).
- **No Alembic migration** — `report_jobs` already exists; all changes are schema/loader/runner logic.
- **No new agent tools or skills** — the workflow cites only existing ones.
- Distinctive seeded report marker (verbatim): `report_type = "arena_high_board_governance"`.
- Seed rows **omit explicit `id`** (DB autoincrement); **no workflow assertion references a concrete seed id**.
- The golden-workflow loader rejects unknown `expected_skill` / `expected_tools` and any `step.replay` key missing from fixtures — keep these consistent.

---

### Task 1: `tool_not_called` assertion type

Adds a negative assertion (the positive-only DSL cannot forbid a tool call). General/reusable.

**Files:**
- Modify: `backend/app/golden_workflows/schema.py` (assertion union, ~lines 30–95)
- Modify: `backend/app/golden_workflows/assertions.py` (`evaluate_assertion`, ~lines 84–118)
- Test: `tests/test_golden_workflow_assertions.py` (create if absent)

**Interfaces:**
- Produces: assertion `{type: "tool_not_called", name: str}`; passes iff no observed tool call normalizes to `name`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_workflow_assertions.py
from app.golden_workflows.assertions import evaluate_assertion, AssertionContext
from app.golden_workflows.schema import parse_workflow  # noqa: F401 (ensures pkg import)


def _ctx(tool_calls):
    return AssertionContext(
        skills_routed=[], tool_calls=tool_calls, tool_results=[],
        artifacts=[], response_text="",
    )


class _A:  # lightweight assertion stand-in matching the discriminated-union shape
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_tool_not_called_passes_when_absent():
    a = _A(type="tool_not_called", name="create_report")
    ok, _ = evaluate_assertion(a, _ctx([{"name": "write_report_artifact"}]))
    assert ok is True


def test_tool_not_called_fails_when_present_normalized():
    a = _A(type="tool_not_called", name="create_report")
    # normalize_tool_name strips a trailing _tool suffix on the observed call
    ok, msg = evaluate_assertion(a, _ctx([{"name": "create_report_tool"}]))
    assert ok is False
    assert "create_report" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -v`
Expected: FAIL — `evaluate_assertion` returns `(False, "unknown assertion tool_not_called")` so `test_tool_not_called_passes_when_absent` fails.

- [ ] **Step 3: Add the schema model + union entry**

In `backend/app/golden_workflows/schema.py`, add the model after `_ToolResultPath` (before the `Assertion = Annotated[...]` union):

```python
class _ToolNotCalled(BaseModel):
    type: Literal["tool_not_called"]
    name: str
```

Then add `_ToolNotCalled` to the union members:

```python
Assertion = Annotated[
    Union[_SkillRouted, _SkillsRoutedSequence, _ToolCalled, _TaskReturnedId,
          _ArtifactExists, _ResponseContains, _ToolResultPath, _ToolNotCalled],
    Field(discriminator="type"),
]
```

- [ ] **Step 4: Add the evaluator branch**

In `backend/app/golden_workflows/assertions.py`, inside `evaluate_assertion`, add before the final `return False, f"unknown assertion {t}"`:

```python
    if t == "tool_not_called":
        from app.golden_workflows.schema import normalize_tool_name
        want = normalize_tool_name(a.name)
        called = any(normalize_tool_name(c.get("name", "")) == want for c in ctx.tool_calls)
        return (not called, f"tool {a.name} was called but must not be")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/schema.py backend/app/golden_workflows/assertions.py tests/test_golden_workflow_assertions.py
git commit -m "feat(golden): add tool_not_called negative assertion type"
```

---

### Task 2: `reports` seed namespace

Lets fixtures seed a persisted `ReportJob` for the display-report step.

**Files:**
- Modify: `backend/app/golden_workflows/fixtures.py` (`_NAMESPACES`, `_INSERT_ORDER`, column allowlist, `apply_seed`)
- Test: `tests/test_golden_workflow_fixtures_reports.py` (create)

**Interfaces:**
- Consumes: `apply_seed(bundle, session) -> dict[ns][alias] -> id` (existing).
- Produces: `reports` namespace — required keys `{alias, report_type}`; allowlisted columns `{report_type, status, request_payload, result_payload, artifact_paths}`; seeds a `models.ReportJob`; records inserted id in the returned map and `seed_map` for `$seed.reports.<alias>.id`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_golden_workflow_fixtures_reports.py
from app import database, models
from app.golden_workflows.fixtures import FixtureBundle, apply_seed


def _bundle():
    return FixtureBundle(
        schema_version=1,
        seed={
            "reports": [
                {"alias": "q3", "report_type": "arena_high_board_governance",
                 "status": "completed",
                 "result_payload": {"summary": "prior governance"},
                 "artifact_paths": {"markdown": "reports/q3.md"}},
            ]
        },
        replay={},
        seed_map={},
    )


def test_reports_namespace_inserts_reportjob_and_records_id():
    with database.SessionLocal() as session:
        ids = apply_seed(_bundle(), session)
        rid = ids["reports"]["q3"]
        row = session.get(models.ReportJob, rid)
        assert row is not None
        assert row.report_type == "arena_high_board_governance"
        assert row.status == "completed"
        assert row.result_payload == {"summary": "prior governance"}
        # cleanup so repeated test runs stay isolated
        session.delete(row)
        session.commit()
```

> Note: confirm the `FixtureBundle` constructor field names by reading the top of `backend/app/golden_workflows/fixtures.py`; if it is a dataclass with different kwargs, adapt the `_bundle()` construction (the load path is `load_fixtures(path)` — you may instead write a tiny JSON fixture to a tmp file and call `load_fixtures`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures_reports.py -v`
Expected: FAIL — `load_fixtures`/validation raises `UnknownSeedNamespaceError` for `reports`, or `apply_seed` raises `unhandled namespace 'reports'`.

- [ ] **Step 3: Register the namespace + insert order + column allowlist**

In `backend/app/golden_workflows/fixtures.py`, add to `_NAMESPACES`:

```python
    "reports": {"alias", "report_type"},
```

Add `"reports"` to `_INSERT_ORDER` (no FK parent — place after `portfolios`):

```python
_INSERT_ORDER = [
    "portfolios", "reports", "pricing_profiles", "pricing_parameter_rows", "rfqs", "positions", "risk_runs",
]
```

Add a column allowlist near `_RFQ_COLS`:

```python
# Column allowlist for the reports seed namespace (beyond the always-excluded "alias").
_REPORT_COLS: frozenset[str] = frozenset({
    "report_type", "status", "request_payload", "result_payload", "artifact_paths",
})
```

- [ ] **Step 4: Add the `apply_seed` branch**

In `apply_seed`, add a branch (before the final `else: raise`):

```python
            elif ns == "reports":
                extra = {
                    k: v for k, v in row.items()
                    if k != "alias" and k in _REPORT_COLS
                }
                obj = models.ReportJob(**extra)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_fixtures_reports.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/golden_workflows/fixtures.py tests/test_golden_workflow_fixtures_reports.py
git commit -m "feat(golden): add reports seed namespace for ReportJob fixtures"
```

---

### Task 3: Accept `high_board` persona in the schema

**Files:**
- Modify: `backend/app/golden_workflows/schema.py` (`GoldenWorkflow.persona`, ~line 112)
- Test: `tests/test_golden_workflow_assertions.py` (extend) or a new tiny schema test

**Interfaces:**
- Produces: `GoldenWorkflow.persona` accepts `"high_board"`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_golden_workflow_assertions.py
from app.golden_workflows.schema import parse_workflow


def test_high_board_is_a_valid_persona():
    wf = parse_workflow({
        "id": "x", "schema_version": 1, "persona": "high_board",
        "title": "t", "objective": "o", "fixtures": "x.fixtures.json",
        "steps": [], "success": {"assertions": [], "rubric": []},
    })
    assert wf.persona == "high_board"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py::test_high_board_is_a_valid_persona -v`
Expected: FAIL — Pydantic `ValidationError` (`high_board` not in the Literal).

- [ ] **Step 3: Extend the persona Literal**

In `backend/app/golden_workflows/schema.py`:

```python
    persona: Literal["trader", "risk_manager", "sales", "quant", "high_board"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_assertions.py::test_high_board_is_a_valid_persona -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/golden_workflows/schema.py tests/test_golden_workflow_assertions.py
git commit -m "feat(golden): accept high_board persona in workflow schema"
```

---

### Task 4: Arena runner — persona mapping + seeded-ReportJob cleanup

**Files:**
- Modify: `backend/app/services/arena/runner.py` (`_PERSONA_TO_CHARACTER` ~line 30; seed/cleanup region ~lines 360–417)
- Test: `tests/test_arena_runner_high_board.py` (create)

**Interfaces:**
- Consumes: `apply_seed(...) -> seed_ids` (existing); `database.SessionLocal`; `models.ReportJob`.
- Produces: `_persona_to_character("high_board") == "high_board"`; module constant `ARENA_REPORT_MARKER = "arena_high_board_governance"`; `_purge_seeded_reports(session)` deletes marker rows; match purges its own seeded report ids in `finally`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_arena_runner_high_board.py
from app import database, models
from app.services.arena import runner


def test_persona_maps_high_board_to_high_board():
    assert runner._persona_to_character("high_board") == "high_board"


def test_purge_seeded_reports_removes_marker_rows_only():
    with database.SessionLocal() as session:
        keep = models.ReportJob(report_type="portfolio_governance", status="completed")
        marker = models.ReportJob(report_type=runner.ARENA_REPORT_MARKER, status="completed")
        session.add_all([keep, marker])
        session.commit()
        keep_id, marker_id = keep.id, marker.id

    with database.SessionLocal() as session:
        runner._purge_seeded_reports(session)
        session.commit()

    with database.SessionLocal() as session:
        assert session.get(models.ReportJob, marker_id) is None
        assert session.get(models.ReportJob, keep_id) is not None
        # cleanup
        session.delete(session.get(models.ReportJob, keep_id))
        session.commit()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_arena_runner_high_board.py -v`
Expected: FAIL — `_persona_to_character` returns `"trader"` (no `high_board` key) and `runner.ARENA_REPORT_MARKER` / `_purge_seeded_reports` do not exist (`AttributeError`).

- [ ] **Step 3: Add persona mapping + marker constant + purge helper**

In `backend/app/services/arena/runner.py`, extend `_PERSONA_TO_CHARACTER`:

```python
    "trader": "trader",
    "risk_manager": "risk_manager",
    "high_board": "high_board",
    "sales": "trader",
    "quant": "trader",
```

Add a module constant near the other arena markers (e.g. next to `ARENA_PORTFOLIO_TAG`):

```python
ARENA_REPORT_MARKER = "arena_high_board_governance"
```

Add the purge helper near `_purge_seeded_portfolios`:

```python
def _purge_seeded_reports(session) -> None:
    """Recovery purge: delete any ReportJob carrying the arena-private marker
    report_type. Safe under the sequential-matches invariant — a leftover marker
    row can only be a prior crashed match's orphan, never a live concurrent one.
    No production/user report uses this report_type."""
    from sqlalchemy import delete

    from app import models
    session.execute(
        delete(models.ReportJob).where(
            models.ReportJob.report_type == ARENA_REPORT_MARKER
        )
    )
```

- [ ] **Step 4: Wire the two cleanup paths into `run_match`**

In the seed block (where `_purge_seeded_portfolios(session, loaded.fixtures)` and `seed_ids = apply_seed(...)` are called), add the recovery purge before reseeding and capture the seeded report ids:

```python
        _purge_seeded_portfolios(session, loaded.fixtures)
        _purge_seeded_reports(session)              # recovery: reclaim prior crash orphans
        seed_ids = apply_seed(loaded.fixtures, session)
        seeded_report_ids = list(seed_ids.get("reports", {}).values())
```

> `seeded_report_ids` is defined in the first `with database.SessionLocal()` block. Hoist it so it is in scope at the `finally` (e.g. initialize `seeded_report_ids = []` just above that `with`).

In the match `finally` (next to `_purge_match_rfqs(...)`), add the ownership-precise purge:

```python
    finally:
        _purge_match_rfqs(thread_id, rfq_id_baseline)
        if seeded_report_ids:
            from sqlalchemy import delete
            with database.SessionLocal() as session:
                session.execute(
                    delete(models.ReportJob).where(models.ReportJob.id.in_(seeded_report_ids))
                )
                session.commit()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_arena_runner_high_board.py -v`
Expected: PASS (both tests).

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/arena/runner.py tests/test_arena_runner_high_board.py
git commit -m "feat(arena): map high_board persona + cleanup seeded ReportJob rows"
```

---

### Task 5: Workflow definition + fixtures (loads + regression-passes)

**Files:**
- Create: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md`
- Create: `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json`
- Test: `tests/test_golden_workflow_registry.py` (already loads all defs) + `tests/test_golden_workflow_regression.py` (already runs all defs)

**Interfaces:**
- Consumes: Tasks 1–3 (the `tool_not_called` type, `reports` namespace, `high_board` persona).
- Produces: workflow id `high-board-portfolio-review-day`, 6 steps, tags `[flagship, high-board, oversight, reporting, desk-workflow]`.

- [ ] **Step 1: Write the definition file**

Create `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md`:

````markdown
---
id: high-board-portfolio-review-day
schema_version: 1
persona: high_board
title: "High-Board Portfolio Review Day"
objective: >
  A board overseer reviews the desk: resolves the control book, curates a
  desk-scoped board-review View, counts the Snowball exposure, takes an inline
  composition summary, pulls the prior persisted governance report as evidence,
  and drafts a fresh board governance report.
fixtures: high-board-portfolio-review-day.fixtures.json
tags: [flagship, high-board, oversight, reporting, desk-workflow]

steps:
  - user: "Resolve the desk control book — is it a container or a view?"
    expected_skill: portfolio-membership
    expected_tools:
      - name: get_portfolio
    outcome: >
      The agent resolves the seeded desk book and reports it is a Container with
      explicit membership.
    assertions:
      - type: skill_routed
        name: portfolio-membership
      - type: tool_result_path
        tool: get_portfolio
        path: kind
        equals: container
    replay: step-1-membership

  - user: "Create a board-review view over the desk control book."
    expected_skill: portfolio-maintenance
    expected_tools:
      - name: create_portfolio
    outcome: >
      A View portfolio is created, scoped to the desk container via
      source_portfolio_ids.
    assertions:
      - type: tool_called
        name: create_portfolio
        args:
          kind: view
      - type: tool_result_path
        tool: create_portfolio
        path: kind
        equals: view
    replay: step-2-create-view

  - user: "How many Snowballs are in that board-review view?"
    expected_skill: portfolio-view-counting
    expected_tools:
      - name: get_positions
    outcome: >
      The agent counts the Snowball subset of the view and reports it against the
      view's full membership.
    assertions:
      - type: skill_routed
        name: portfolio-view-counting
      - type: tool_called
        name: get_positions
        args:
          product_type: Snowball
      - type: tool_result_path
        tool: get_positions
        path: total_count
        gte: 1
      - type: tool_result_path
        tool: get_positions
        path: portfolio_total_count
        equals: 5
    replay: step-3-count

  - user: "Give me an inline batch composition summary of the view — don't persist it."
    expected_skill: batch-run-reports
    expected_tools:
      - name: run_report_batch
    outcome: >
      An inline composition summary (counts / product-type breakdown) is produced
      with no persisted artifact.
    assertions:
      - type: skill_routed
        name: batch-run-reports
      - type: response_contains
        any_of: ["composition", "positions", "breakdown"]
    replay: step-4-batch

  - user: "Pull last quarter's board governance report for context."
    expected_skill: display-report
    expected_tools:
      - name: list_reports
      - name: get_report
    outcome: >
      The agent finds and summarizes the seeded prior governance report.
    assertions:
      - type: skill_routed
        name: display-report
      - type: tool_called
        name: list_reports
      - type: tool_called
        name: get_report
      - type: tool_result_path
        tool: get_report
        path: report_type
        equals: arena_high_board_governance
    replay: step-5-display

  - user: "Draft the board governance report."
    expected_skill: generate-report
    expected_tools:
      - name: write_report_artifact
    outcome: >
      A board governance report artifact is produced as a thread asset via
      write_report_artifact (not create_report).
    assertions:
      - type: tool_called
        name: write_report_artifact
      - type: artifact_exists
        kind: text
      - type: tool_not_called
        name: create_report
    replay: step-6-generate

success:
  assertions:
    - type: skills_routed_sequence
      names: [portfolio-membership, portfolio-maintenance, portfolio-view-counting, batch-run-reports, display-report, generate-report]
    - type: tool_result_path
      tool: get_positions
      path: portfolio_total_count
      equals: 5
    - type: artifact_exists
      kind: text
    - type: tool_not_called
      name: create_report
    - type: response_contains
      any_of: ["governance", "board"]
  rubric:
    - "Curated the board-review view by scoping it to the desk book, not by hand-picking positions."
    - "Grounded the final report in governed evidence: the structural counts and the prior persisted governance report."
    - "Did not present the live batch risk total as a precise governed valuation."
---

## Step 1 — Resolve the desk control book

The overseer asks which book the desk control sits in. The agent routes to
`portfolio-membership`, calls `get_portfolio`, and reports it is a Container.

## Step 2 — Create the board-review view

The overseer asks for a board-review view. The agent routes to
`portfolio-maintenance` and calls `create_portfolio` with `kind=view` sourced from
the desk container.

## Step 3 — Count the Snowball exposure

The overseer asks how many Snowballs are in the view. The agent routes to
`portfolio-view-counting` and calls `get_positions` with a `Snowball` filter,
reporting the subset against the view's full membership.

## Step 4 — Inline composition summary

The overseer asks for an inline composition summary. The agent routes to
`batch-run-reports`, calls `run_report_batch`, and returns counts/breakdown with no
persisted artifact.

## Step 5 — Pull prior governance report

The overseer asks for the prior governance report. The agent routes to
`display-report`, calls `list_reports` then `get_report`, and summarizes the seeded
report.

## Step 6 — Generate the board governance report

The overseer asks for a fresh board governance report. The agent routes to
`generate-report` and calls `write_report_artifact`, producing a thread artifact.
````

- [ ] **Step 2: Write the fixtures file**

Create `backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json`:

```json
{
  "schema_version": 1,
  "seed": {
    "portfolios": [
      { "alias": "desk", "name": "Desk Control Book" },
      { "alias": "other", "name": "Other Desk Book" }
    ],
    "positions": [
      { "alias": "d1", "portfolio": "desk", "underlying": "AAPL", "product_type": "Snowball", "quantity": 100 },
      { "alias": "d2", "portfolio": "desk", "underlying": "MSFT", "product_type": "Snowball", "quantity": 50 },
      { "alias": "d3", "portfolio": "desk", "underlying": "AAPL", "product_type": "EuropeanVanillaOption", "quantity": 10 },
      { "alias": "d4", "portfolio": "desk", "underlying": "TSLA", "product_type": "BarrierOption", "quantity": 20 },
      { "alias": "d5", "portfolio": "desk", "underlying": "NVDA", "product_type": "EuropeanVanillaOption", "quantity": 30 },
      { "alias": "o1", "portfolio": "other", "underlying": "AAPL", "product_type": "Snowball", "quantity": 5 }
    ],
    "reports": [
      { "alias": "q3", "report_type": "arena_high_board_governance", "status": "completed",
        "result_payload": { "summary": "Prior-quarter board governance review." },
        "artifact_paths": { "markdown": "reports/q3-governance.md" } }
    ]
  },
  "replay": {
    "step-1-membership": {
      "ai": { "tool_calls": [ { "id": "tc1", "name": "get_portfolio", "args": { "portfolio_id": 1 } } ] },
      "tool_results": [ { "name": "get_portfolio", "tool_call_id": "tc1",
        "content": { "id": 1, "name": "Desk Control Book", "kind": "container" } } ],
      "skills_routed": ["portfolio-membership"],
      "artifacts": [],
      "response_text": "The Desk Control Book (id 1) is a Container with explicit membership."
    },
    "step-2-create-view": {
      "ai": { "tool_calls": [ { "id": "tc2", "name": "create_portfolio",
        "args": { "name": "Board Review View", "kind": "view", "source_portfolio_ids": [1] } } ] },
      "tool_results": [ { "name": "create_portfolio", "tool_call_id": "tc2",
        "content": { "id": 2, "name": "Board Review View", "kind": "view" } } ],
      "skills_routed": ["portfolio-maintenance"],
      "artifacts": [],
      "response_text": "Created the board-review view (id 2) sourced from the desk book."
    },
    "step-3-count": {
      "ai": { "tool_calls": [ { "id": "tc3", "name": "get_positions",
        "args": { "portfolio_id": 2, "product_type": "Snowball" } } ] },
      "tool_results": [ { "name": "get_positions", "tool_call_id": "tc3",
        "content": { "positions": [ { "underlying": "AAPL" }, { "underlying": "MSFT" } ],
          "total_count": 2, "portfolio_total_count": 5 } } ],
      "skills_routed": ["portfolio-view-counting"],
      "artifacts": [],
      "response_text": "2 Snowballs out of 5 positions in the board-review view."
    },
    "step-4-batch": {
      "ai": { "tool_calls": [ { "id": "tc4", "name": "run_report_batch",
        "args": { "title": "Board Review Composition", "report_type": "composition",
          "portfolio": { "positions": [], "market": {} } } } ] },
      "tool_results": [ { "name": "run_report_batch", "tool_call_id": "tc4",
        "content": { "summary": { "position_count": 5, "by_product_type": { "Snowball": 2 } },
          "risk_summary": {} } } ],
      "skills_routed": ["batch-run-reports"],
      "artifacts": [],
      "response_text": "Composition summary: 5 positions; breakdown shows 2 Snowballs."
    },
    "step-5-display": {
      "ai": { "tool_calls": [
        { "id": "tc5a", "name": "list_reports", "args": {} },
        { "id": "tc5b", "name": "get_report", "args": { "report_id": 10 } } ] },
      "tool_results": [
        { "name": "list_reports", "tool_call_id": "tc5a",
          "content": { "reports": [ { "id": 10, "report_type": "arena_high_board_governance", "status": "completed" } ], "total": 1 } },
        { "name": "get_report", "tool_call_id": "tc5b",
          "content": { "id": 10, "report_type": "arena_high_board_governance", "status": "completed",
            "result_payload": { "summary": "Prior-quarter board governance review." } } } ],
      "skills_routed": ["display-report"],
      "artifacts": [],
      "response_text": "Pulled the prior governance report (arena_high_board_governance) for context."
    },
    "step-6-generate": {
      "ai": { "tool_calls": [ { "id": "tc6", "name": "write_report_artifact",
        "args": { "title": "Board Governance Report", "format": "markdown", "body_markdown": "# Board Governance Report\n..." } } ] },
      "tool_results": [ { "name": "write_report_artifact", "tool_call_id": "tc6",
        "content": { "path": "artifacts/board-governance.md", "kind": "text" } } ],
      "skills_routed": ["generate-report"],
      "artifacts": [ { "kind": "text", "path": "artifacts/board-governance.md" } ],
      "response_text": "Drafted the board governance report for the board."
    }
  }
}
```

- [ ] **Step 3: Run the registry + regression suites**

Run: `.venv/bin/python -m pytest tests/test_golden_workflow_registry.py tests/test_golden_workflow_regression.py -v`
Expected: PASS — the new workflow loads (skills/tools/replay keys resolve) and its replay transcript passes the full assertion manifest. If a skill-catalog exact-set/count test elsewhere fails, that is expected drift from adding a definition — update those pinned sets (see the skill-catalog coupling note) but do NOT touch unrelated assertions.

- [ ] **Step 4: Commit**

```bash
git add backend/app/golden_workflows/definitions/high-board-portfolio-review-day.md backend/app/golden_workflows/definitions/high-board-portfolio-review-day.fixtures.json
git commit -m "feat(golden): add high-board-portfolio-review-day workflow + fixtures"
```

---

### Task 6: Manifest-pin test (drift guard)

**Files:**
- Test: `tests/test_high_board_loads.py` (create)

**Interfaces:**
- Consumes: `get_workflow_bundle("high-board-portfolio-review-day")`, `objective_score` / manifest counting helpers used by the existing flagship-load test.

- [ ] **Step 1: Write the test**

```python
# tests/test_high_board_loads.py
from app.golden_workflows.registry import get_workflow_bundle


def test_high_board_definition_pins():
    wf = get_workflow_bundle("high-board-portfolio-review-day").workflow
    assert wf.id == "high-board-portfolio-review-day"
    assert wf.persona == "high_board"
    assert len(wf.steps) == 6
    assert wf.tags == ["flagship", "high-board", "oversight", "reporting", "desk-workflow"]
    skills = [s.expected_skill for s in wf.steps]
    assert skills == [
        "portfolio-membership", "portfolio-maintenance", "portfolio-view-counting",
        "batch-run-reports", "display-report", "generate-report",
    ]
```

> Mirror the objective-point-total pin from `tests/test_flagship_loads.py` if that pattern exists there (copy its counting helper and assert the total this workflow produces). If no such helper exists, the step/skill/tag pins above are sufficient.

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_high_board_loads.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_high_board_loads.py
git commit -m "test(golden): pin high-board-portfolio-review-day id/steps/tags/skills"
```

---

### Task 7: Adversarial negative replay cases (guards bite)

Prove the scope discriminator and `tool_not_called` reject the leaking shapes.

**Files:**
- Test: `tests/test_high_board_guards.py` (create)

**Interfaces:**
- Consumes: `get_workflow_bundle(...)`, `transcript_from_replay`, `objective_score` (the same helpers `tests/test_golden_workflow_regression.py` uses — copy its imports/usage).

- [ ] **Step 1: Write the test**

```python
# tests/test_high_board_guards.py
import copy

from app.golden_workflows.registry import get_workflow_bundle
from app.golden_workflows.transcript import transcript_from_replay
from app.golden_workflows.assertions import objective_score


def _loaded():
    return get_workflow_bundle("high-board-portfolio-review-day")


def test_baseline_replay_passes():
    loaded = _loaded()
    tx = transcript_from_replay(loaded)
    score, passed, total = objective_score(loaded.workflow, tx)
    assert passed == total  # all objective assertions pass on the honest replay


def test_leaking_view_fails_scope_discriminator():
    loaded = _loaded()
    # Simulate a leaking hybrid view: the view's full membership resolves to 6,
    # not the seeded desk count of 5.
    loaded = copy.deepcopy(loaded)
    loaded.fixtures.replay["step-3-count"]["tool_results"][0]["content"]["portfolio_total_count"] = 6
    tx = transcript_from_replay(loaded)
    _, passed, total = objective_score(loaded.workflow, tx)
    assert passed < total  # portfolio_total_count == 5 assertion now fails


def test_calling_create_report_fails_tool_not_called():
    loaded = _loaded()
    loaded = copy.deepcopy(loaded)
    # Step 6 also calls create_report before write_report_artifact.
    loaded.fixtures.replay["step-6-generate"]["ai"]["tool_calls"].insert(
        0, {"id": "tc6x", "name": "create_report", "args": {"report_type": "portfolio_governance"}}
    )
    loaded.fixtures.replay["step-6-generate"]["tool_results"].insert(
        0, {"name": "create_report", "tool_call_id": "tc6x", "content": {"id": 77}}
    )
    tx = transcript_from_replay(loaded)
    _, passed, total = objective_score(loaded.workflow, tx)
    assert passed < total  # tool_not_called create_report now fails
```

> Confirm `objective_score`'s return shape and `transcript_from_replay`'s argument by reading `tests/test_golden_workflow_regression.py`; adapt the deepcopy mutation if `loaded.fixtures.replay` is immutable (rebuild the bundle from a mutated dict instead).

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_high_board_guards.py -v`
Expected: PASS — baseline passes; both leaking shapes are rejected.

- [ ] **Step 3: Commit**

```bash
git add tests/test_high_board_guards.py
git commit -m "test(golden): adversarial replay cases for high-board scope + create_report guards"
```

---

### Task 8: Full suite + skill-catalog drift reconciliation

**Files:**
- Modify: whichever pinned skill-catalog/count tests break from adding one definition (see the skill-catalog coupling note — typically exact-set/count assertions).

- [ ] **Step 1: Run the golden + arena suites**

Run:
```bash
.venv/bin/python -m pytest tests/test_golden_workflow_*.py tests/test_high_board_*.py \
  tests/test_arena_runner_high_board.py tests/test_flagship_loads.py -v
```
Expected: PASS. If a pinned exact-set or count test elsewhere fails solely because one workflow definition was added, update that pinned set to include `high-board-portfolio-review-day`. Do not alter unrelated assertions.

- [ ] **Step 2: Commit any reconciliation**

```bash
git add -A
git commit -m "test(golden): reconcile pinned catalogs for high-board workflow"
```

## Self-Review

- **Spec coverage:** §3 harness changes → Tasks 1 (tool_not_called), 2 (reports namespace), 3 (persona), 4 (runner persona map + §3.5 cleanup). §4 six-step arc + §5 fixtures → Task 5. §6 tests → Tasks 2/4/6/7/8. §6b accepted limitations → no task (documented trade-offs). All covered.
- **Type consistency:** `_purge_seeded_reports`, `ARENA_REPORT_MARKER`, `seeded_report_ids` used consistently in Task 4. Assertion `type: "tool_not_called"` / field `name` consistent across schema (Task 1), evaluator (Task 1), definition (Task 5), and guard test (Task 7). `report_type` marker string identical (`arena_high_board_governance`) in fixtures, runner, and assertions.
- **Placeholder scan:** no TBD/TODO; every code step shows real content. The two "confirm the constructor/return shape by reading X" notes are verification guards, not placeholders — the code is provided and only needs a shape check.
