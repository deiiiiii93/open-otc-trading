# Golden Workflows — Design Spec

**Date:** 2026-06-24
**Status:** Approved for planning (feature-flow)
**Scope:** All three phases in one feature cycle (user-directed; the phase
boundaries below keep it reviewable).

## 1. Problem

We need golden tests that **demonstrate the agent operating long, multi-step desk
workflows the way a human trader or risk manager does** — a whole "day" of chained
business actions, not isolated tool calls.

One shared asset — **golden workflow definitions** — feeds three consumers:

1. **Deterministic regression** — scripted-model golden transcripts (no LLM) that
   pin the expected skill/tool sequence and fail CI on drift.
2. **LLM-arena eval** — run real LLMs against each workflow, judge multi-step
   completion, and rank models arena-style on a leaderboard.
3. **Demo / explainability** — render each workflow to HTML + MP4 via hyperframes.

The workflow definition is the **single source of truth**; each consumer reads
different fields and must never fork it.

## 2. Key Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | Workflow = **markdown + YAML frontmatter** + sibling `*.fixtures.json` (basename only, same dir) | Mirrors the `SKILL.md` layer; diffable, reviewable. |
| D2 | Definitions live in **`backend/app/golden_workflows/`** (importable package) | Product assets consumed by arena + demo, not only tests. |
| D3 | Workflow sits **one layer above `SKILL.md`**; references skills by canonical name | Concrete regression checkpoints + objective eval signal. |
| D4 | Per-consumer fields are **orthogonal** (`replay`→regression, `rubric`→eval, prose→demo) | One file serves three masters without coupling. |
| D5 | **Prose `outcome` (demo) separate from typed `assertions[]` (machine)** | Prose is not reliably assertable; typed assertions are (§3.4). |
| D6 | Phase 2 runs candidates via **Zenmux** (OpenAI-compatible), **GPT-5.5 judge** | Reuses the key/helper feature-flow already depends on. |
| D7 | Phase 2 persists to a **DB table (alembic migration)** + **frontend Arena page** | Durable, browsable leaderboard. |
| D8 | Each arena match runs against an **injected per-match session factory + artifact root** (ephemeral DB) | Live tool calls mutate state; no cross-match contamination. |
| D9 | Phase 3 = **HTML + MP4 + TTS** via hyperframes CLI, **on-demand**; composition is pure/unit-tested | Heavy media deps stay out of CI. |
| D10 | **Mock-by-default tests**; live LLM/render opt-in (`ARENA_LIVE=1`, `DEMO_RENDER=1`) | Suite stays hermetic. |
| D11 | Arena **run-tools block until task completion** (tool wrapper awaits); regression replay supplies completed payloads | Lets a same-turn `get_*` read completed state deterministically. |

**Declined:** the reviewer's "split into 3 specs" — user directed all-phases in one
cycle. Mitigated by the phase boundaries (§10), shared versioned schemas, and
freezing Phase 1's format before Phases 2–3 build on it.

## 3. The Golden Workflow Format

### 3.1 Identifiers & normalization (`schema.py`)

- **Workflow `id`**: kebab slug `^[a-z0-9]+(-[a-z0-9]+)*$`, equal to the definition
  filename stem; no path separators. Duplicate ids → `DuplicateWorkflowError`.
- **`fixtures`**: a **basename only** (no `/`, no `..`), resolved in the same
  directory as the definition; otherwise `FixturePathError`.
- **`expected_skill`**: the skill's `name:` frontmatter value. The skill registry is
  built by **recursively** scanning `backend/app/skills/workflows/**/SKILL.md` and
  keying by frontmatter `name` (so nested dirs like `risk/run-risk/SKILL.md` are
  found); duplicate names across the tree → `SkillNameCollisionError`.
  `normalize_skill()` lowercases/trims; the registry test asserts the named skill
  exists.
- **Tool names**: the agent-registered tool name. `normalize_tool_name()` strips a
  single trailing `_tool`. **Registry rule:** if two registered tools normalize to
  the same name → `ToolNameCollisionError` at registry build (so matching is never
  ambiguous). Unknown name in a workflow → `UnknownToolError`.

### 3.2 Schema (Pydantic, `schema.py`) — fields, types, required/defaults

**`GoldenWorkflow` (frontmatter):**

| Field | Type | Req? | Meaning |
|---|---|---|---|
| `id` | str slug | required | == filename stem |
| `schema_version` | int (==1) | required | format version |
| `persona` | enum(`trader`,`risk_manager`,`sales`,`quant`) | required | desk role |
| `title` | non-empty str | required | short human title |
| `objective` | non-empty str | required | session goal |
| `fixtures` | basename str | required | sibling fixtures filename |
| `tags` | list[str] | default `[]` | leaderboard slicing |
| `steps` | list[Step] | required, **min 1** | ordered turns |
| `success` | Success | required | end-of-session checks |

**`Step`:**

| Field | Type | Req? | Meaning |
|---|---|---|---|
| `user` | non-empty str | required | human message that turn |
| `expected_skill` | str | required | canonical skill, checked **per-step** (§3.5) |
| `expected_tools` | list[ToolExpectation] | default `[]` | tool milestones (ordered subsequence within the step) |
| `outcome` | non-empty str (prose) | required | human description (demo only) |
| `assertions` | list[Assertion] | default `[]` | per-step machine checks |
| `rubric` | list[str] | default `[]` | judge points |
| `replay` | str ref | required | key into fixtures `replay` map |

**`ToolExpectation` = `{name: str, args: dict | null}`** — a **top-level `args` of
`null` or omitted is a wildcard** (tool called with any args). A non-null `args` dict
is a deep partial match (§3.5); a `null` **nested inside** an args dict asserts the
actual value is JSON `null`. (Same rule for `tool_called.args`.)
**`Success` = `{assertions: list[Assertion], rubric: list[str]}`**.

Narration body: one `## Step N — <beat>` heading per step, **1-based, contiguous,
exactly one per step**; mismatch → `NarrationMismatchError`. Prose read only by demo.

### 3.3 Loader & registry (`registry.py`)

- `load_workflow(path) -> GoldenWorkflow`: parse `---` YAML frontmatter + body, load
  sibling fixtures, validate, attach narration by index.
- `list_workflows()` / `get_workflow(id)`: discover definitions at the exact path
  **`backend/app/golden_workflows/definitions/*.md`**, with each `*.fixtures.json`
  as a sibling in that same directory.
- **Validation errors** (each a distinct `WorkflowError` subclass): duplicate id;
  id≠filename; fixtures not a sibling basename; invalid/missing YAML frontmatter;
  empty `steps`; a `Step.replay` ref absent from fixtures (`MissingReplayError`);
  narration block count ≠ step count / misnumbered; unknown persona; unknown tool;
  tool-name collision; missing skill dir; malformed `Assertion`.
- **Unused fixtures `replay` key**: not an error — emit `warnings.warn(msg,
  UnusedReplayWarning)` (custom class) so tests assert via `pytest.warns`.

### 3.4 Assertion DSL (shared by regression + arena objective scoring)

`Assertion` is a tagged union (`type` discriminator). Per-step assertions evaluate
against the **step context**; `success.assertions` against the **accumulated session
context** (§3.6 / §6.1 define both):

| `type` | fields | passes when |
|---|---|---|
| `skill_routed` | `name` | normalized skill in context `skills_routed` |
| `skills_routed_sequence` | `names: list[str]` | normalized names appear as an ordered subsequence of session `skills_routed` |
| `tool_called` | `name`, `args?` | a tool call matches name + arg-subset (§3.5) |
| `task_returned_id` | `tool` | a `tool_results[]` entry from `tool` has a non-empty `task_id` |
| `artifact_exists` | `kind` | an `artifacts[]` entry has that `kind` |
| `response_contains` | `any_of: list[str]` | `response_text` (case-insensitive) contains ≥1 |
| `tool_result_path` | `tool`, `path`, exactly one of `equals`/`gte`/`lte`/`is_not_null` | value at `path` of the tool result satisfies the comparator |

`tool_result_path` rules: uses the **last** `tool_results[]` entry for `tool`; `path`
is dotted, array elements via integer segments (`legs.0.delta`); keys containing
dots are unsupported (documented). Exactly one comparator (validated). `gte`/`lte`
require numeric actual+expected; missing path / type mismatch / errored tool result
→ **fail** with a path-qualified message. Each assertion yields `(passed, message)`.

### 3.5 Matching semantics

- **`expected_skill` is per-step**: the named skill must appear in that step's
  `skills_routed`. (The natural consequence is a global ordered subsequence, but the
  unit of scoring is the per-step hit.)
- **Tool order within a step**: `expected_tools` must appear as an ordered
  subsequence of that step's tool calls; extra calls allowed.
- **Duplicate calls**: each `ToolExpectation` consumes the first not-yet-matched call
  of that name (left-to-right).
- **Arg subset (deep partial)**: applies only when `args` is a non-null dict (a
  top-level `null`/omitted `args` is a wildcard, §3.2). Dicts — every expected key
  present and recursively matches; lists — equal length, element-wise deep; scalars
  — exact equality, **no type coercion** (`1`≠`"1"`, `1`≠`1.0`); a nested `null`
  requires actual `null`. Missing key → fail with a path-qualified message.
- **`$seed` interpolation**: in `ToolExpectation.args` and assertion fields, a string
  `"$seed.<ns>.<alias>.<field>"` is resolved by the loader to the seeded value before
  matching. Unresolved `$seed.*` at load → `UnresolvedSeedRefError`.

### 3.6 Fixtures & replay JSON schema (`*.fixtures.json`)

```jsonc
{
  "schema_version": 1,
  "seed": {                         // applied before the run via FixtureLoader
    "portfolios": [ { "alias": "control", "id": 6, "name": "..." } ],
    "positions":  [ { "alias": "p1", "portfolio": "control", "underlying": "AAPL", ... } ],
    "pricing_profiles": [ { "alias": "prof", "id": 3, ... } ],
    "market_data": [ ... ],
    "risk_runs":  [ { "alias": "stale", "portfolio": "control",
                      "as_of": "2026-06-20T00:00:00Z" } ]   // older than valuation date
  },
  "replay": {                       // keyed by Step.replay
    "<ref>": {
      "ai": { "content": "string",
              "tool_calls": [ { "id": "call_1", "name": "run_batch_pricing",
                                "args": { ... } } ] },
      "tool_results": [ { "tool_call_id": "call_1", "name": "run_batch_pricing",
                          "content": { ... } } ],
      "skills_routed": ["run-risk"],   // explicit routed-skill events for this turn
      "artifacts": [ ],                // e.g. [{"kind":"report","path":"..."}]
      "response_text": "string"        // the turn's final assistant text
    }
  }
}
```

- **Seed namespaces** (closed set; unknown namespace → `UnknownSeedNamespaceError`):
  `portfolios` (req: `alias`, `id`, `name`), `positions` (req: `alias`, `portfolio`
  [FK alias], `underlying`; plus product fields), `pricing_profiles` (req: `alias`,
  `id`), `market_data` (req: `alias`, `underlying`, `spot`, `as_of`), `risk_runs`
  (req: `alias`, `portfolio` [FK alias], `as_of`). Every row carries a unique `alias`
  within its namespace (duplicate → `DuplicateAliasError`); rows with stable identity
  (`portfolios`, `pricing_profiles`) also carry an **explicit `id`** so references
  like `portfolio_id:6` are deterministic (explicit-id conflict with an existing row
  → `SeedIdConflictError`). FK fields name another row's `alias`; an unresolved FK →
  `UnresolvedAliasError`.
- **`$seed` resolution lifecycle**: `FixtureLoader` inserts rows via existing
  factories/services (not raw SQL), honoring explicit ids, **then** builds the
  resolution map from the **inserted rows**. `$seed.<ns>.<alias>.<field>` is resolved
  by **exact string replacement preserving the JSON type** of the source field
  (so a numeric id stays numeric — required because matching forbids coercion). An
  unresolved `$seed.*` → `UnresolvedSeedRefError`.
- **Replay**: `skills_routed`, `artifacts`, `response_text`, and normalized
  `tool_results` are **authored explicitly** so the regression transcript carries the
  exact fields the assertion engine needs (no derivation from `expected_skill` —
  avoiding tautology; see §5). `valid` ids: every `tool_results[].tool_call_id`
  matches an `ai.tool_calls[].id`.

## 4. Flagship Workflow — Risk Manager "Control Day"

`persona: risk_manager`. Seven turns mapping to real skills in
`backend/app/skills/workflows/risk/`, chaining **5 distinct skills across 7 turns,
with 4 task-returning async steps (2, 4, 5, 6)**. Hotspot underlying is **`AAPL`**.

| Step | Human asks | `expected_skill` | `expected_tools` | key `assertions` |
|---|---|---|---|---|
| 1 Orient | "Where's my risk on portfolio 6 this morning?" | `read-risk-result` | `get_latest_risk_run{portfolio_id:6}` | `response_contains:["stale","out of date"]` |
| 2 Refresh | "It's stale — rerun it." | `run-risk` | `run_batch_pricing{portfolio_id:6,method:"summary"}` | `task_returned_id:run_batch_pricing` |
| 3 Read | "Done? What's the picture?" | `read-risk-result` | `get_latest_risk_run{portfolio_id:6}` | `tool_result_path:get_latest_risk_run path:"hotspot.underlying" equals:"AAPL"` |
| 4 Investigate | "Dig into AAPL." | `run-greeks-landscape` | `run_greeks_landscape`, `get_greeks_landscape_run` | `task_returned_id:run_greeks_landscape` |
| 5 Stress | "How bad if it gaps down 15%?" | `run-scenario-test` | `run_scenario_test`, `get_scenario_test_run` | `task_returned_id:run_scenario_test`, `tool_result_path:get_scenario_test_run path:"pnl" lte:0` |
| 6 Validate | "Backtest the hedge before I commit." | `run-backtest` | `run_backtest`, `get_backtest_run` | `task_returned_id:run_backtest` |
| 7 Report | "Write it up for the book." | `create-risk-report` | `create_report` | `artifact_exists:report` |

### Pinned flagship facts (in `risk-manager-control-day.fixtures.json`)

Deterministic by construction — every value an assertion checks is a **canned
tool_result**:
- Portfolio `control` has explicit `id:6`, 3 positions; **`AAPL`** is the hotspot
  (its vega is the dominant book vega in the canned step-3 result).
- A pre-seeded `stale` risk run with `as_of` older than the seeded valuation date by
  > the stale threshold (1 calendar day), so step 1's `response_contains:["stale"]`
  holds via the canned summary.
- Step 5's canned `get_scenario_test_run.pnl` is negative (the −15% shock).
- Step 7's canned `create_report` result includes an `artifacts[]` of kind `report`.

### `success`

- **`assertions`**: `skills_routed_sequence: [read-risk-result, run-risk,
  read-risk-result, run-greeks-landscape, run-scenario-test, run-backtest,
  create-risk-report]`; `task_returned_id` for `run_batch_pricing`,
  `run_greeks_landscape`, `run_scenario_test`, `run_backtest` (**4**);
  `artifact_exists:report`.
- **`rubric`** (judge): (a) recognized the stale run unprompted; (b) identified
  `AAPL` as the hotspot; (c) kept `AAPL` as the subject through steps 4–6; (d) the
  report references the scenario P&L and backtest attribution.

## 5. Phase 1 — Deterministic Regression (the proof)

`backend/tests/test_golden_workflow_format.py`:

1. `get_workflow("risk-manager-control-day")` parses; 7 steps; every field +
   narration block present.
2. Build `_ScriptedGraph` from `replay` entries (reusing `tests/_scripted_graph.py`
   helpers `_ai`, `_task_call`, `_interrupt`). Seed the test DB via `FixtureLoader`.
3. For each `user` turn, assemble the step context from the replay entry
   (`tool_calls`, `tool_results`, `skills_routed`, `artifacts`, `response_text`,
   `task_ids` extracted from results) and evaluate `expected_tools` + per-step
   `assertions`. Assert all pass.
4. Evaluate `success.assertions` against the accumulated session context.

**Regression boundary (explicit & non-tautological):** `_ScriptedGraph` replaces the
orchestrator, so this test proves **format sufficiency + the assertion/matching
engine**, not live routing. `skills_routed` is **authored in the replay fixture
independently of `expected_skill`** (a human writes both; the test fails if they
disagree), so it is a real check of the engine, not a tautology. Live routing is
covered by the existing agent integration tests and Phase 2 arena runs.
`extract_skills_routed(transcript)` and `extract_assertion_context(transcript_step)`
are shared utilities used by both regression and arena, so observation is identical.

## 6. Phase 2 — LLM-Arena Eval

### 6.1 Shared `MatchTranscript` schema (versioned) — carries all assertion fields

```jsonc
{
  "schema_version": 1,
  "run_id": 12, "workflow_id": "...", "model_id": "openai/gpt-5.5",
  "started_at": "...", "finished_at": "...",
  "steps": [ { "index": 1, "user": "...",
               "messages": [ ... ],
               "tool_calls": [ { "name":"...", "args":{...}, "id":"...",
                                 "result":{...}, "error": null } ],
               "tool_results": [ { "name":"...", "tool_call_id":"...",
                                   "content": {...}, "error": null } ],
               "skills_routed": ["read-risk-result"],
               "artifacts": [ {"kind":"report","path":"..."} ],
               "task_ids": ["task_..."],
               "response_text": "final assistant text for the turn",
               "errors": [] } ]
}
```
Normative extraction (so regression replay and live arena yield identical context):
- `response_text` = last assistant text of the turn.
- `tool_results[]` = each tool message normalized to `{name, tool_call_id, content,
  error}` (non-JSON content wrapped as `{"raw": "..."}`).
- `task_ids[]` = the value at the canonical envelope key **`content.task_id`** of
  each tool result (string; missing/null/errored → not collected). `task_returned_id`
  reads the same key.
- `artifacts[]` = artifacts emitted by tools that turn (kind + path).
- `skills_routed[]` (**live source of truth**): populated from the orchestrator's
  skill/subagent-selection events — the runner subscribes to the existing
  routing/skill-injection signal (the same `astream_events`/signal-sink path the
  agent already emits when a workflow skill is selected) and records each selected
  skill `name`, normalized and de-duplicated **per turn, order-preserving**. In
  regression these events are authored in the replay fixture; the extraction util is
  identical, so the two paths agree.
- Errored tools set `error` and contribute no `task_id`/`artifact`.

Stored at `artifacts/arena/<run_id>/<model_slug>/<workflow_id>/transcript.json`.

### 6.2 Runner (`backend/app/services/arena/runner.py`)

**Conversation contract (what the model sees):** the candidate model is given the
**same system/persona context the production agent uses for `persona`**, then the
workflow's `steps[].user` messages one at a time. It **never sees** `expected_skill`,
`expected_tools`, `assertions`, `rubric`, `outcome`, or `objective` — those are the
hidden answer key. **Full conversation history is retained across all 7 turns** (it's
one session). Per user step the agent may take multiple tool/assistant turns up to a
**budget of 12 model turns**; exceeding it ends the step and records a
`budget_exceeded` error for that step (the match continues).

Run loop (normative):
```
seed ephemeral DB; history = [system(persona)]
for step in workflow.steps:
    history += user(step.user)
    run orchestrator on history until it yields no tool call or hits the 12-turn budget
    capture MatchTranscript.step(index, user, messages, tool_calls, tool_results,
        skills_routed, artifacts, task_ids, response_text, errors)
persist transcript; score (objective + judge)
```

For each `(workflow, model)`:
- Build the deep-agent orchestrator backed by the candidate model via an
  OpenAI-compatible chat client (`base_url` = Zenmux, `api_key=$ZENMUX_API_KEY`),
  wired as a `ModelDescriptor`/channel so the existing builder is reused.
- **Isolation (D8):** the runner injects a **per-match session factory** (bound to an
  ephemeral SQLite DB seeded by `FixtureLoader`) and a **per-match artifact root**
  into the app context the tools use — no reliance on process-global state. Tasks run
  via an **in-process synchronous executor** bound to that same session factory, so
  workers never touch the shared dev/test DB. Matches run under a bounded pool
  (default 4). The ephemeral DB + scratch artifacts are removed in a `finally`;
  artifacts referenced by the transcript are first **copied into the persisted match
  namespace** and the transcript paths rewritten, so Phase 3 can consume them.
- **Blocking run-tools (D11):** in arena mode the `run_*` task tools **await
  completion** (per-task timeout default 120s) before returning, so a same-turn
  `get_*` reads completed state. Timeout → the tool returns an error result and the
  match is marked `failed`.
- **Per-match config (persisted in `arena_match.config`):** `temperature` (default 0
  for comparability), `max_tokens`, `tool_choice=auto`, request `timeout`, `retry`
  (2, exp backoff), orchestrator/prompt version hash.
- A model/tool error or timeout → match `failed` with the error; **the run continues**
  for other matches.

### 6.3 Judge (`backend/app/services/arena/judge.py`)

- Input: `MatchTranscript` + per-step `rubric` + `success.rubric`.
- Calls **`openai/gpt-5.5`** via Zenmux, `reasoning=high`, `temperature=0`,
  structured output `{ "rubric_scores":[{"point","score":0..100,"rationale"}],
  "overall_notes" }`. Retries=2 on parse/validation failure or timeout (120s) with
  backoff. On exhaustion → `judged_score=null`, `judge_missing=true`.
- **Objective score** (no LLM): via the §3.4/§3.5 engine over the transcript.

### 6.4 Scoring & leaderboard (reproducible)

- **Objective points** (numerator/denominator, no double counting): one point each
  for — every `Step.expected_skill` (per-step hit; **skill routing is never also
  listed as a step `assertion`**, so it is counted once), every `ToolExpectation`
  across steps, every per-step `Assertion`, and every `success.assertions` entry.
  `objective = 100 * passed / total`. *Flagship manifest* (pinned by a test):
  7 `expected_skill` + 10 `ToolExpectation` (1,1,1,2,2,2,1) + 8 step assertions
  (1,1,1,1,2,1,1) + 6 `success.assertions` (1 sequence + 4 task ids + 1 artifact)
  = **31 points**.
- All scores are floats in **[0,100]**, rounded to 1 decimal at the storage boundary.
- `judged_score` = mean of `rubric_scores[].score`.
- `total_score` = `w_obj*objective + w_judge*judged`, weights from `arena_run.weights`
  (default `{obj:0.5,judge:0.5}`). If `judge_missing`, `total_score = objective`,
  match flagged.
- **Run status aggregation:** a run is `completed` when every match reaches a terminal
  state (`scored` or `failed`) through normal execution; it is `failed` only on an
  infrastructure error that aborts the whole run. Partial match failures (incl.
  judge-only) leave the run `completed`.
- **Leaderboard** = for the **latest `completed` run** only, mean `total_score` per
  `model_id` over its **non-failed** matches (`status=scored`); ties broken by mean
  `objective` then `model_id`. Filters `?run_id=` and `?tag=` (workflows carrying the
  tag). Models with zero scored matches are omitted. **Empty cases** (no completed
  run / tag matches nothing) → `200 {rows: []}`.

### 6.5 Model registry (source of truth, `services/arena/models.py`)

A config list of candidate models, each `{zenmux_name, display_name, slug
(path-safe), default_config}`. `GET /api/arena/models` exposes it for the frontend.
The API validates `model_ids` against this registry (`slug` or `zenmux_name`).

### 6.6 Persistence (alembic migration, next sequential revision)

Migration uses **migration-local Core tables** (repo convention — no ORM/service
imports). Round-trip (upgrade/downgrade) test required.

- **`arena_run`**: `id` PK, `created_at` ts, `status`
  enum(`queued`,`running`,`completed`,`failed`), `workflow_ids` JSON, `model_ids`
  JSON, `weights` JSON, `error` text null.
- **`arena_match`**: `id` PK, `run_id` FK→arena_run, `workflow_id` str, `model_id`
  str, `status` enum(`pending`,`running`,`scored`,`failed`), `objective_score` float
  null, `judged_score` float null, `total_score` float null, `judge_missing` bool
  default false, `config` JSON, `transcript_path` str null, `error` text null,
  `created_at` ts. **Unique(`run_id`,`workflow_id`,`model_id`)**; indexes on
  (`run_id`), (`model_id`).

ORM models + `services/arena/store.py` read/write above the tables.

### 6.7 API (`backend/app/routers/arena.py`)

| Method/path | Request | Response | Codes |
|---|---|---|---|
| `POST /api/arena/runs` | `{workflow_ids[], model_ids[], weights?}` | `{run_id, status}` | 202 / 422 |
| `GET /api/arena/runs` | `?limit&offset` | `{runs:[RunSummary], total}` | 200 |
| `GET /api/arena/runs/{id}` | — | `{run, matches:[MatchSummary]}` | 200 / 404 |
| `GET /api/arena/matches/{id}/transcript` | — | transcript JSON | 200 / 404 (missing path) |
| `GET /api/arena/leaderboard` | `?run_id&tag` | `{rows:[{model_id, avg_total, avg_objective, matches}]}` | 200 |
| `GET /api/arena/models` | — | `{models:[{slug, zenmux_name, display_name}]}` | 200 |

A run is queued as an async task (batch-pricing mechanism); status
`queued→running→completed|failed`. Unknown workflow/model id or empty lists → 422.
`run_arena` agent tool is out of scope this cycle.

### 6.8 Frontend (`frontend/src/pages/Arena.tsx`)

Leaderboard table (model × `avg_total`, `avg_objective`, match count) + run picker +
run detail (workflow × model grid with scores, drill-down fetching
`/matches/{id}/transcript`). Models populated from `/api/arena/models`. Wire into
`lib/routing.ts` + `main.tsx` at `/arena` (repo's explicit-routing pattern) + nav
entry. Runs polled like existing async tasks; no streaming.

### 6.9 Test strategy

- Runner unit-tested with a **mock chat model** (scripted tool calls) + the real
  isolation/blocking/polling code → deterministic, no network.
- Judge tested with **faked Zenmux HTTP responses** (valid, malformed-JSON, timeout,
  retry-exhausted) so the real parser/retry path runs.
- Store + router against the test DB; migration round-trip test.
- Live multi-model runs opt-in behind `ARENA_LIVE=1` + `ZENMUX_API_KEY`; skipped in CI.

## 7. Phase 3 — Hyperframes Demo

### 7.1 Composition builder (`backend/app/services/demo/composition.py`)

- `build_composition(workflow, transcript) -> CompositionBundle` is **pure** (returns
  data, no IO): `section_plan` (one section per step: narration prose, `user` line,
  and the step's tool calls/outcome as on-screen events) + `narrator_scripts`
  (per-section narration text for TTS).
- `write_composition(bundle, out_dir)` persists the bundle (separate IO function).
- Default `out_dir`: `artifacts/demos/<workflow_id>/<source>/` where `source` =
  `regression` or `<run_id>-<model_slug>` (so models/runs never overwrite); a
  `--output-dir` override is honored.
- Unit test: `build_composition` for the flagship returns 7 ordered sections, each
  carrying its narration block + step events. No render.

### 7.2 Render script (`scripts/generate_demo.py`, on-demand, not CI)

- Behind `DEMO_RENDER=1`. Drives the hyperframes CLI: TTS narrator scripts → render
  HTML → encode MP4. Documents prereqs (Node/hyperframes CLI, TTS provider); writes
  `composition.html`, narration audio, `demo.mp4` under the `out_dir` above.
- Smoke unit test runs `build_composition` for the flagship with the render mocked.

## 8. Failure Handling

- **Loader/registry**: each malformed-definition case (§3.3) raises a specific
  `WorkflowError` subclass naming the workflow id and offending field.
- **Skill/tool drift / collision**: registry test fails loudly (§3.1, D3).
- **Arena match error/timeout**: match `failed` with error; run continues; failed
  matches excluded from the leaderboard; run still `completed` (§6.4).
- **Judge malformed/timeout**: bounded retry; on exhaustion objective-only,
  `judge_missing=true`.
- **Isolation cleanup**: ephemeral DB + scratch artifacts removed in `finally` even on
  failure; transcript + its referenced artifacts copied to the persisted namespace
  first, so Phase 3 references stay valid.
- **Demo render failure**: script exits non-zero naming the failing stage; the
  deterministic composition bundle is still written for inspection.
- **No network / no keys in CI**: all live paths skipped; mock paths cover logic.

## 9. Out of Scope

- Trader / Sales-RFQ / Quant personas and additional workflows (parallel track; this
  cycle ships the Risk Manager flagship only).
- `run_arena` agent tool; streaming arena progress in the UI; serving/hosting MP4s
  beyond writing to `artifacts/`.

## 10. Build Order (phase boundaries)

1. **P1**: schema + loader + registry + Assertion/matching engine + `$seed`
   interpolation + flagship definition + fixtures + regression proof + flagship
   point-count test. **Freeze the format here.**
2. **P2**: `MatchTranscript` + extraction utils + runner (isolation/blocking) + judge
   + scoring + model registry + migration + store + router + Arena page + tests.
3. **P3**: pure composition builder + writer + render script + tests.

Each phase's tests are green before the next begins. Validate in an isolated git
worktree (a concurrent session may share this checkout).
