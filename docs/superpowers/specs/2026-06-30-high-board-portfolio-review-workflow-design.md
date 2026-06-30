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
two workflow domains — `portfolios` and `reporting` — so it has **no risk or
pricing skills of its own** and never routes to a risk/pricing skill. (Note: the
`batch-run-reports` reporting tool, `run_report_batch`, computes a summary risk
total *internally* to populate a governance report — this is authorized desk
reporting, not a persona-boundary crossing; see §4 Step 4.) The spine is
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

4. **New `tool_not_called` assertion type** in
   `backend/app/golden_workflows/schema.py` + `assertions.py`. The existing DSL is
   **positive-only** (`tool_called` deep-subset, `skills_routed_sequence`
   subsequence), so it cannot forbid a tool call. `high_board` holds the **full
   tool list** (`personas.py`: all personas share it; only state-mutating tools are
   runtime-gated), so `create_report` *is* callable and Step 6 must be able to
   assert it was **not** called. New type: `{type: tool_not_called, name: <tool>}`,
   passing iff no observed tool call normalizes to `name`. Small, general, reusable
   by the other workflows. Mirrors the `tool_called` evaluator with negation; uses
   the same `normalize_tool_name`. (Adds one assertion point to the manifest.)

5. **§3.5 — Seeded-ReportJob cleanup in the arena runner**
   (`backend/app/services/arena/runner.py`). The existing `_purge_seeded_portfolios`
   resets portfolios + pricing profiles before reseeding, and the trader cycle added
   RFQ purge — but `ReportJob` has no cleanup, so each live match would leave its
   seeded governance report behind, polluting `list_reports` and making Step 5's
   evidence selection nondeterministic. **Two-path, ownership-scoped cleanup**
   (mirrors how the RFQ purge separates "this match's rows" from recovery):
   - **Normal path (ownership-precise):** the match captures
     `seed_ids = apply_seed(...)` and, in its `finally`, purges **only its own**
     seeded report ids — `delete(ReportJob).where(id.in_(seed_ids["reports"]))`. This
     can never touch another match's or a user's row.
     - **Recovery path (crash fallback):** *before* reseeding, delete any leftover
     rows carrying the distinctive marker `report_type == "arena_high_board_governance"`
     (optionally further narrowed by an arena ownership token stamped in
     `request_payload`, e.g. `{"arena_seed_token": "<run_id>"}` — a JSON field, no
     schema change). Such a leftover can only be a prior **crashed** match's orphan,
     because matches **run sequentially** — the arena serialises matches on the shared
     async-checkpointer SQLite ("Known constraint — sequential matches"), so no
     concurrent match can own a live marker row when this runs. The marker is
     arena-private (no production/user report uses it), so the recovery delete is
     safe under that invariant.
   Step 6 writes a *thread artifact* (`write_report_artifact`), not a `ReportJob`, and
   `create_report` is asserted un-called, so the **only** `ReportJob` a match touches
   is its own seeded marker row. Regression tests: (a) seeding twice leaves exactly
   one marker report and no stale rows visible to `list_reports`; (b) a simulated
   prior-crash orphan (marker row with no live match) is reclaimed by the recovery
   path on the next match.

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

- **user:** "Create a board-review view over the desk control book."
- **skill:** `portfolio-maintenance`
- **tools:** `create_portfolio` (`kind="view"`, `source_portfolio_ids`)
- **source_portfolio_ids:** the resolved id of the seeded desk container — the
  agent discovers it at runtime (Step 1's `get_portfolio`/`list_portfolios`), so the
  **assertion does not pin a concrete id** (see the assertion note below).
- **Why sources, not a filter_rule:** the rule DSL's `ALLOWED_FIELDS`
  (`portfolio_rule.py`) has no portfolio field, and the membership resolver
  (`portfolio_membership.py`) applies a `filter_rule` **globally across every
  container in the DB** (union with sources, not intersection). Since the live
  arena driver seeds into the **main** desk DB, a `product_type` filter_rule view
  would leak Snowballs from other real containers — nondeterministic scoring. A
  source-scoped view resolves to exactly the seeded desk positions **by
  construction**, so resolved ids ⊆ desk book is guaranteed. The `product_type`
  slice moves to Step 3 (count time), which is the natural home for it.
- **outcome:** A source-scoped View portfolio is created. `create_portfolio` is
  HITL-gated; under arena `yolo_mode=True` it auto-confirms (consistent with the
  trader workflow's no-HITL booking path).
- **assertions (scope enforced by an id-independent discriminator, not by id-matching):**
  - `tool_called create_portfolio` with `args` deep-subset `{kind: "view"}` — confirms a
    View (not a Container) was created. The args **must not** pin a concrete
    `source_portfolio_ids` value: the live arena seeds fixtures with **fresh
    autoincrement ids per match** (`runner.py`), so the desk id differs every run and
    a pinned `$seed.portfolios.desk.id` would fail live. (The shipped risk-manager
    workflow likewise references no concrete seed ids in assertions.)
  - `task_returned_id` / id present for the new portfolio.
  - **Scope is enforced at Step 3** by the `portfolio_total_count == 5` count
    discriminator, which is **id-independent** (it checks resolved membership *size*,
    not ids) and holds in both replay and live: only a desk-sourced view yields 5
    (empty/unsourced → 0, global `filter_rule` → 3, hybrid → 6).

### Step 3 — Count the Snowball exposure in the view

- **user:** "How many Snowballs are in that board-review view?"
- **skill:** `portfolio-view-counting`
- **tools:** `get_positions` (`portfolio_id` = the new view, `product_type="Snowball"`;
  returns `total_count` and `portfolio_total_count`)
- **outcome:** The view resolves membership from its source (the desk book) on
  query; the agent applies the `Snowball` product-type filter at count time and
  reports the Snowball count vs. the view's total. This is exactly what
  `portfolio-view-counting` exists for ("asks for Snowball count, product-type
  count").
- **assertions:** `skill_routed portfolio-view-counting`;
  `tool_called get_positions` with `args` subset `{product_type: "Snowball"}`;
  `tool_result_path` `get_positions` `path=total_count` `gte=1` (Snowball subset);
  **scope-enforcing assertion** — `tool_result_path` `get_positions`
  `path=portfolio_total_count` `equals=5` (the seeded desk position count). This is
  a deterministic discriminator: `portfolio_total_count` is the view's full
  membership (pre-`product_type` filter, `positions.py:1155 =
  len(status_filtered)`). With the seeded non-desk Snowball tripwire (§5), only the
  correctly source-scoped view yields 5 — a hybrid `source+filter_rule` view yields
  6 and a pure global `filter_rule` view yields 3, both failing, in **both** the
  isolated replay DB and live. The number is pinned to the fixture's seeded desk
  position count.

### Step 4 — Inline batch summary of the view

- **user:** "Give me an inline batch summary of the view — don't persist it."
- **skill:** `batch-run-reports`
- **tools:** `run_report_batch`
- **outcome:** An inline **composition** summary is produced from the view snapshot
  (position/product-type counts and exposure breakdown), with **no** persisted
  report artifact. `run_report_batch` also computes a risk total internally, but —
  because high_board has no governed market source (§5) — that figure is treated as
  **indicative/unpriced, not a governed valuation** and is *not* the step's headline.
  The governed evidence the workflow relies on is the structural counts (Steps 3–4)
  and the prior persisted report (Step 5), neither of which depends on live market
  data.
- **assertions:** `skill_routed batch-run-reports`; `response_contains` a
  composition-summary token (e.g. a count/breakdown phrase); **no**
  `artifact_exists kind=report` for this step.

### Step 5 — Pull prior persisted report as evidence

- **user:** "Pull last quarter's board governance report for context."
- **skill:** `display-report`
- **tools:** `list_reports` + `get_report`
- **outcome:** The agent lists reports, finds the seeded governance report, and
  summarizes it.
- **assertions (id-independent AND seed-specific):** `skill_routed display-report`;
  `tool_called list_reports`; `tool_called get_report`; `tool_result_path`
  `get_report` `path=report_type` `equals="arena_high_board_governance"`. The seeded
  report carries a **distinctive arena-scoped `report_type`**
  (`arena_high_board_governance`) that no production report uses — so the assertion
  is both id-independent (survives autoincrement) **and** specific (a stale, foreign,
  or concurrently-created `portfolio_governance` report in the shared live DB cannot
  satisfy it). `report_type` is a core `ReportJob` column surfaced by
  `shape_report_row`, so `get_report` exposes it directly. This marker is also what
  the §3.5 cleanup purges by.

### Step 6 — Generate the board governance report

- **user:** "Draft the board governance report."
- **skill:** `generate-report`
- **tools:** `write_report_artifact` (**not** `create_report`)
- **Why not `create_report`:** the live `generate-report` SKILL explicitly forbids
  it ("Do not call `create_report` for custom thread reports; it queues a legacy
  portfolio/risk report job") and mandates `write_report_artifact`, which produces
  the thread artifact. Using `create_report` would queue an unintended legacy
  report job (restricted report types) and could fail the live step while replay
  still passes.
- **outcome:** A board governance report artifact is produced as a thread asset via
  `write_report_artifact`.
- **assertions:**
  - `tool_called write_report_artifact`.
  - `artifact_exists kind=text` — `write_report_artifact` emits artifact kind by
    payload (`text` for markdown/html, `binary` for docx); the risk-manager
    workflow asserts `kind: text` for the same tool. (**Not** `kind=report`, which
    the tool never emits.)
  - `tool_not_called create_report` (new type, §3.4) — `create_report` is in the
    high_board tool surface and queues a legacy `ReportJob` side effect; the
    subsequence matcher would let a `create_report`→`write_report_artifact` turn
    pass the positive checks, so the negative assertion is required to actually
    enforce the generate-report SKILL's prohibition.

### Success block

```yaml
success:
  assertions:
    - type: skills_routed_sequence
      names: [portfolio-membership, portfolio-maintenance, portfolio-view-counting,
              batch-run-reports, display-report, generate-report]
    - type: tool_result_path
      tool: get_positions
      path: portfolio_total_count
      equals: 5   # pinned to seeded desk position count — fails any non-desk-scoped view
    - type: artifact_exists
      kind: text
    - type: tool_not_called
      name: create_report
    - type: response_contains
      any_of: ["governance", "board"]
  rubric:
    - "Curated the board-review view by scoping it to the desk book, not by hand-picking positions."
    - "Grounded the final report in GOVERNED evidence: the structural position/exposure counts (Steps 3–4) and the prior persisted governance report (Step 5)."
    - "Did NOT present the live batch risk/PnL total as a precise governed valuation; any such figure is framed as indicative/unpriced or omitted (high_board has no governed market source)."
```

## 5. Fixtures (`high-board-portfolio-review-day.fixtures.json`)

`seed`:

- **portfolios**: one `container` desk book — `{alias: "desk", name: "Desk Control Book"}`.
  The Step-2 view is created **live** (source-scoped to `desk`), not seeded.
- **portfolios** (second container): one `container` `{alias: "other", name: "Other Desk Book"}`
  holding **one** `Snowball` position **not** in `desk`. This is the leak-tripwire
  (next bullet).
- **positions**: 5 rows under `desk` — a **mix**: 2 `product_type: "Snowball"` + 3
  non-Snowball (vanilla/barrier) — **plus 1 `Snowball` under `other`** (6 positions
  total across the DB). The correctly source-scoped view (source=`[desk]`) resolves
  to exactly the 5 desk positions → `portfolio_total_count == 5`. The Step-3
  `Snowball` count is a strict subset (`total_count == 2`). The lone non-desk
  Snowball makes the Step-3 `portfolio_total_count == 5` assertion a **complete
  discriminator**, deterministic even in the isolated replay DB:
  - correct source view → **5** ✓
  - `source=[desk] + filter_rule(Snowball)` hybrid → 5 desk ∪ all-Snowballs = **6** ✗
  - pure `filter_rule(Snowball)` view → all 3 Snowballs = **3** ✗

  So no leaking-view shape can satisfy the assertion. The seeded `other` container
  is purged by the arena's portfolio cleanup like any seeded portfolio.
- **No `pricing_profiles` fixture.** `run_report_batch` takes a
  `PortfolioSnapshotInput` with an **embedded** `market`; there is no
  pricing-profile→market resolution on the high_board tool surface (the persona has
  no `market-data` domain), so a seeded profile would not feed the tool. Honest
  consequence: in the **live** Step-4 path the agent assembles the snapshot's market
  from thread context — likely minimal/default — so the batch's risk total is a
  **structural demonstration, not a grounded desk valuation**. The deterministic
  **replay** supplies a canned `run_report_batch` result with representative
  numbers. The governance-report rubric (§4) therefore only requires the figures to
  *trace to the tool output*, and the report copy must not overstate their
  precision. (If grounded live numbers are ever needed, add an allowed step that
  resolves a seeded market into the snapshot — out of scope here, §7.)
- **reports** (new namespace): one `completed` `ReportJob` —
  `{alias: "q3", report_type: "arena_high_board_governance", status: "completed",
  result_payload: {summary: "…"}, artifact_paths: {markdown: "…"}}` — for Step 5
  to find. The **distinctive arena-scoped `report_type`** is the stable per-workflow
  marker: it makes Step 5's evidence assertion id-independent **and** seed-specific
  (no production/foreign governance report collides), and it is the key the §3.5
  cleanup purges by — robust even if a crashed match never captured the inserted id.
- **No pinned ids.** All seed rows **omit explicit `id`** and let the DB
  autoincrement (matching risk-manager), so the same fixture seeds clash-free on
  every live match. `apply_seed` still records `alias → inserted id` in `seed_map`
  (used by the runner's cleanup and available for `$seed` interpolation), but **no
  workflow assertion references a concrete seed id** — scope/evidence are enforced
  id-independently (count discriminator; `report_type`), per §F9.

`replay`: six entries keyed `step-1-membership` … `step-6-generate`, each carrying
the canned `ai.tool_calls`, `tool_results`, `skills_routed`, `artifacts`, and
`response_text` so the deterministic regression test passes with no LLM and no
network.

## 6. Point budget & testing

- **Estimated objective points: ~29–31** (6 skill points + ~8 tool points + ~9
  step assertions incl. the scope discriminator + ~7 success assertions incl.
  `tool_not_called`). On par with the 31/32-pt flagships, earned through the
  scope/negative guards rather than padding.
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
  - `tool_not_called` assertion unit test (new) — passes when the tool is absent,
    fails when a matching (normalized) call is present.
  - **Adversarial replay cases** (regression): two crafted transcripts that the
    manifest must **reject** — (a) Step 2 creates a `source=[desk] + filter_rule`
    hybrid view (Step-3 `portfolio_total_count` resolves to 6 → scope assertion
    fails); (b) Step 6 calls `create_report` then `write_report_artifact`
    (`tool_not_called create_report` fails). These prove the guards bite.

## 6b. Accepted limitations (adversarial-review residuals, at the harness boundary)

Two residual concerns from adversarial review are **consciously accepted** because
closing them fully requires harness-wide changes that affect every golden workflow,
not just this one — i.e. a separate harness cycle, not this workflow's scope:

1. **Count discriminator is fixture-cardinality-based, not a tenant invariant.**
   `portfolio_total_count == 5` could *in principle* be satisfied coincidentally by
   unrelated live-DB state of the same size. A bulletproof guard would assert the
   created view's `source_portfolio_ids`/membership equals the seeded desk source —
   but that needs **late seed-id resolution in the assertion engine** (resolving
   `$seed` against `apply_seed` *output*, not the pre-seed JSON), which the harness
   does not support (§F9) and which the **shipped** risk-manager/trader workflows
   also lack. The count discriminator is the strongest *id-independent* guard
   available today and is consistent with the existing arena's shared-DB posture.
   The arena seeds a fresh fixture set and `_purge_seeded_portfolios` resets prior
   seeds, bounding the coincidence risk. **Decision needed:** ship with this
   limitation, or fund the late-seed-id harness feature first.

2. **ReportJob cleanup leans on the sequential-matches invariant + a convention
   marker**, not an enforced ownership column (`ReportJob` has none). The dual-path
   cleanup (id-precise normal purge + marker/token recovery) is safe **given** the
   documented sequential execution; a hard ownership column or process-wide arena
   lock would be needed for concurrent matches — which the arch does not currently
   run. Accepted as consistent with the RFQ-cleanup precedent.

Both are recorded so the implementer (and reviewers) treat them as known, bounded
trade-offs rather than oversights.

## 7. Out of scope (YAGNI)

- No risk or pricing steps — not in the `high_board` persona domains.
- No late seed-id resolution in the assertion engine, and no `ReportJob` ownership
  column / arena lock — both are harness-wide changes (see §6b); this cycle accepts
  the documented limitations instead.
- No hyperframes demo render (the workflow is demo-shaped; rendering is a later,
  separate cycle).
- No new agent tools or skills — the workflow cites only existing ones.
- No `sales`/`quant` persona wiring (each is its own future cycle).
- No live arena leaderboard run — that is a later, separately triggered action.
- No Alembic migration — `report_jobs` already exists; all changes are
  schema/loader/runner logic.
- No seeded-market resolution for `run_report_batch` — the live Step-4 risk total
  is a structural demonstration, not a grounded valuation (see §5).
- The new `tool_not_called` assertion type (§3.4) is the **only** DSL extension;
  no broader assertion-engine rework (e.g. exact-arg-set or arg-absent matching).
  `filter_rule`-absence on the view is enforced by the count discriminator (§5),
  not by an arg-absent assertion.

## 8. Decisions made (flag to revisit)

- **Board-review view is source-scoped to the desk** (`source_portfolio_ids: [desk]`),
  not rule-scoped. Forced by the resolver: a `filter_rule` view scans all containers
  globally (leakage in the shared live DB) and the DSL can't express a portfolio
  scope. The `Snowball` slice moves to Step-3 count time. Revisit only if a
  portfolio-scoping rule field is ever added to `ALLOWED_FIELDS`.
- **`batch-run-reports` computes risk internally** — accepted as authorized desk
  reporting, not a persona-boundary crossing. The "no risk/pricing" framing is
  scoped to *skills*, not to tool internals.
- **Live batch risk total is indicative, not governed evidence.** Because
  high_board has no governed market source, Step 4's risk figure is demoted to a
  non-headline indicative number; the report's governed claims rest on the
  structural counts (Steps 3–4) and the seeded prior report (Step 5). The judge
  rubric fails a report that presents the live risk total as a precise governed
  valuation. (Adversarial-review residual: rather than add a market-resolution step
  — out of scope, no `market-data` domain for this persona — the workflow makes its
  governed evidence base independent of live market data.)
- **6 steps, ~28 points** rather than padding to 31+. Keeps each of the six skills
  to one clean beat.
