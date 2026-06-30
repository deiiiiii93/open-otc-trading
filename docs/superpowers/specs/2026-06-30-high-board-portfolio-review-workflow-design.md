# High-Board Portfolio Review Day — golden/arena workflow design

**Date:** 2026-06-30
**Status:** approved (brainstorming) — ready for implementation plan
**Author:** desk agent (pairing with operator)

## 1. Summary

A third golden workflow, joining `risk-manager-control-day` (risk_manager) and
`trader-rfq-booking-day` (trader). This one covers the **`high_board`** persona:
a desk-oversight review that inspects portfolio structure, curates a board-review
View, pulls a prior persisted report as governance evidence, and ends in a freshly
generated board governance report.

It is **read-heavy with one structural write**. The `high_board` persona owns only
two workflow domains — `portfolios` and `reporting` — so there is **no risk or
pricing** in this workflow (the persona cannot price or stress-test). The spine is
*inspect structure → curate a view → gather evidence → synthesize a report*. All
**six** high_board skills are exercised exactly once.

Workflow id: `high-board-portfolio-review-day`
Persona: `high_board`
Tags: `[flagship, high-board, oversight, reporting, desk-workflow]`

## 2. Why this persona / scenario

- `high_board` is already wired in **production** (`persona_domains.py`:
  `portfolios`, `reporting`) but has **zero** golden-workflow coverage. It is the
  only wired persona with no workflow, so it adds a genuinely new arena dimension
  (a new desk shape for models to compete on) without a persona-wiring cycle.
- The persona was designed for exactly this loop: `display-report`'s own
  description says *"when a high-board workflow needs persisted report evidence
  before a decision."*
- It is the cheap next candidate (vs `sales`/`quant`, which are not wired in
  `PERSONA_WORKFLOW_DOMAINS` at all and would each need a new persona cycle).

## 3. Harness wiring (the "new persona" cost)

`high_board` is wired in production but **not** in the golden-workflow harness.
Three small changes, no Alembic migration (`report_jobs` already exists):

1. **`backend/app/golden_workflows/schema.py:112`** — extend the persona `Literal`
   from `["trader", "risk_manager", "sales", "quant"]` to additionally include
   `"high_board"`.

2. **`backend/app/services/arena/runner.py` `_PERSONA_TO_CHARACTER`** — add
   `"high_board": "high_board"`. Today this map has no `high_board` entry, so the
   `_persona_to_character` default (`"trader"`) would silently give the candidate
   model the **wrong** system/persona context. This workflow surfaces and fixes
   that latent gap.

3. **New `reports` seed namespace** in `backend/app/golden_workflows/fixtures.py`.
   `ReportJob` is FK-light (id, report_type, status, request_payload,
   result_payload, artifact_paths, created_at), so the namespace mirrors the
   existing `risk_runs` one:
   - add `"reports": {"alias", "report_type"}` (plus a column allowlist for
     `status`/`result_payload`/`artifact_paths`/`request_payload`) to the seed
     specs;
   - add `"reports"` to `_INSERT_ORDER` (no FK parent needed — a JSON
     portfolio reference lives in the payload, not a column, so order is
     unconstrained; place it after `portfolios` for readability);
   - add an `apply_seed` branch inserting a `ReportJob` row and recording its id
     in the seed map for `$seed.reports.<alias>.id` interpolation.

   This is a **reusable harness asset**, exactly like the trader cycle's `rfq`
   namespace investment. It ships with its own unit test (mirroring the
   rfq-namespace test) rather than relying on the workflow's happy path alone.

## 4. The 6-step arc

Each step maps to one high_board skill and its real tools. Tool names below are
verified against `all_agent_tools()` (the loader rejects unknown skills/tools).

### Step 1 — Resolve the desk control book

- **user:** "Resolve the desk control book — is it a container or a view?"
- **skill:** `portfolio-membership`
- **tools:** `get_portfolio` (and/or `list_portfolios` to resolve a name)
- **outcome:** Agent resolves the seeded desk portfolio and reports it is a
  **container** with explicit membership.
- **assertions:** `skill_routed portfolio-membership`;
  `tool_result_path` `get_portfolio` `path=kind` `equals="container"`.

### Step 2 — Create the board-review View (structural write, HITL)

- **user:** "Create a board-review view: all live Snowballs across the desk."
- **skill:** `portfolio-maintenance`
- **tools:** `create_portfolio` (`kind="view"`, `filter_rule`)
- **filter_rule:** `{op: "eq", field: "product_type", value: "Snowball"}`
- **outcome:** A rule-driven View portfolio is created. `create_portfolio` is
  HITL-gated; under arena `yolo_mode=True` it auto-confirms (consistent with the
  trader workflow's no-HITL booking path).
- **assertions:** `tool_called create_portfolio` with `args` subset
  `{kind: "view"}`; `task_returned_id` / id present for the new portfolio.

### Step 3 — Count exposures in the view

- **user:** "How many positions land in that view?"
- **skill:** `portfolio-view-counting`
- **tools:** `get_positions` (`portfolio_id` = the new view; returns `total_count`)
- **outcome:** The view resolves membership from its rule on query; the agent
  reports the count of Snowball positions.
- **assertions:** `skill_routed portfolio-view-counting`;
  `tool_result_path` `get_positions` `path=total_count` `gte=1`.

### Step 4 — Inline batch summary of the view

- **user:** "Give me an inline batch summary of the view — don't persist it."
- **skill:** `batch-run-reports`
- **tools:** `run_report_batch`
- **outcome:** An inline batch summary is produced from the view snapshot, with
  **no** persisted report artifact.
- **assertions:** `skill_routed batch-run-reports`; `response_contains` an inline
  summary token; **no** `artifact_exists kind=report` claimed for this step.

### Step 5 — Pull prior persisted report as evidence

- **user:** "Pull last quarter's board governance report for context."
- **skill:** `display-report`
- **tools:** `list_reports` + `get_report`
- **outcome:** The agent finds and summarizes the **seeded** persisted report.
- **assertions:** `tool_called get_report` with `args` subset
  `{report_id: $seed.reports.q3.id}` (or `tool_result_path get_report path=id
  equals=$seed.reports.q3.id`).

### Step 6 — Generate the board governance report

- **user:** "Draft the board governance report."
- **skill:** `generate-report`
- **tools:** `create_report` (+ `write_report_artifact`)
- **outcome:** A board governance report artifact is produced as a thread asset.
- **assertions:** `artifact_exists kind=report`.

### Success block

```yaml
success:
  assertions:
    - type: skills_routed_sequence
      names: [portfolio-membership, portfolio-maintenance, portfolio-view-counting,
              batch-run-reports, display-report, generate-report]
    - type: artifact_exists
      kind: report
    - type: response_contains
      any_of: ["governance", "board"]
  rubric:
    - "Curated the board-review view from a rule, not by hand-picking positions."
    - "Grounded the final report in the counted exposures and the prior report evidence."
    - "Did not invent risk/PnL numbers (no pricing in this persona)."
```

## 5. Fixtures (`high-board-portfolio-review-day.fixtures.json`)

`seed`:

- **portfolios**: one `container` desk book — `{alias: "desk", name: "Desk Control Book"}`.
- **positions**: 4–5 rows under `desk`, a **mix** so the step-2 view is a true
  filtered subset, not a copy — e.g. 2 with `product_type: "Snowball"` and 2–3
  non-Snowball (vanilla/barrier). This makes `total_count` for the view (Step 3)
  strictly less than the container's `portfolio_total_count`.
- **reports** (new namespace): one `completed` `ReportJob` —
  `{alias: "q3", report_type: "portfolio_governance", status: "completed",
  result_payload: {summary: "…"}, artifact_paths: {markdown: "…"}}` — for Step 5
  to find.

`replay`: six entries keyed `step-1-membership` … `step-6-generate`, each carrying
the canned `ai.tool_calls`, `tool_results`, `skills_routed`, `artifacts`, and
`response_text` so the deterministic regression test passes with no LLM and no
network.

## 6. Point budget & testing

- **Estimated objective points: ~27–29** (6 skill points + ~8 tool points + ~8
  step assertions + ~6 success assertions). Comparable to the 31/32-pt flagships;
  not padded to match — each skill gets one clean beat.
- **Tests:**
  - `tests/test_golden_workflow_regression.py` — replay drives the full assertion
    engine to a passing objective score.
  - `tests/test_golden_workflow_registry.py` — workflow loads; all skills/tools
    resolve.
  - New `reports`-namespace unit test in the fixtures test module (mirrors the
    rfq-namespace test): seeds a `reports` row and asserts a `ReportJob` is
    inserted and its id is reachable via `$seed.reports.<alias>.id`.
  - `tests/test_high_board_loads.py` (new) — pins workflow id, step count (6),
    tag list, and objective point total to prevent silent drift.
  - Arena-runner coverage: `_persona_to_character("high_board") == "high_board"`.

## 7. Out of scope (YAGNI)

- No risk or pricing steps — not in the `high_board` persona domains.
- No hyperframes demo render (the workflow is demo-shaped; rendering is a later,
  separate cycle).
- No new agent tools or skills — the workflow cites only existing ones.
- No `sales`/`quant` persona wiring (each is its own future cycle).
- No live arena leaderboard run — that is a later, separately triggered action.
- No Alembic migration — `report_jobs` already exists; all changes are
  schema/loader/runner logic.

## 8. Decisions made (flag to revisit)

- **View rule = "all live Snowballs"** (`product_type == "Snowball"`). Concrete and
  resolves to a clear subset of the seeded book. Could instead be a status/tag rule.
- **6 steps, ~28 points** rather than padding to 31+. Keeps each of the six skills
  to one clean beat.
