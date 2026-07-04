# Golden Workflows — Authoring & Consumer Guide

A **golden workflow** is a markdown file (with YAML frontmatter) that describes
a realistic, multi-step agent session for a named persona (trader, risk_manager,
sales, quant). Each workflow ships with a sibling `*.fixtures.json` file that
carries the seed data the agent needs and scripted replay entries for deterministic
evaluation.

---

## 1. Where definitions live

```
backend/app/golden_workflows/
├── definitions/
│   ├── risk-manager-control-day.md           ← workflow definition (frontmatter + narration)
│   └── risk-manager-control-day.fixtures.json
├── assertions.py    ← assertion evaluator + seed-ref resolver
├── fixtures.py      ← FixtureBundle loader + apply_seed
├── registry.py      ← load_workflow_bundle / list_workflow_bundles
├── schema.py        ← Pydantic models (GoldenWorkflow, Step, Assertion types)
└── transcript.py    ← MatchTranscript / transcript_from_replay
```

The loader (`registry.py`) enforces: the markdown file stem equals the `id` field,
every `expected_skill` maps to a real SKILL.md in `app/skills/workflows/`, every
`expected_tools` entry maps to a real tool in `all_agent_tools()`, and every
`step.replay` key exists in the fixtures replay block.

---

## 2. Workflow format

### 2.1 Frontmatter (YAML)

```yaml
---
id: my-workflow-id          # kebab slug, must match filename stem
schema_version: 1
persona: risk_manager       # trader | risk_manager | sales | quant
title: "Human-readable title"
objective: >
  One-paragraph description of the scenario goal.
fixtures: my-workflow-id.fixtures.json   # sibling file, no path separators
tags: [flagship, risk]      # free list; "flagship" marks demo candidates
steps:
  - user: "What the user types"
    expected_skill: run-risk              # must match a SKILL.md name field
    expected_tools:
      - name: run_batch_pricing           # normalized: _tool suffix stripped
    outcome: >
      What the agent should produce for this step.
    assertions:
      - type: task_returned_id
        tool: run_batch_pricing
    rubric:
      - "Free-text rubric item for the LLM judge"
    replay: step-1-run-risk               # key in fixtures.json replay block
  # … more steps …
success:
  assertions:
    - type: skills_routed_sequence
      names: [run-risk, read-risk-result]
    - type: artifact_exists
      kind: report
  rubric:
    - "Overall rubric item for the LLM judge"
---
```

### 2.2 Narration body (Markdown)

Below the closing `---` comes one `## Step N — Title` section per step, in order:

```markdown
## Step 1 — Run fresh risk

The risk manager asks for a fresh calculation. The agent routes to `run-risk`,
calls `run_batch_pricing`, and returns a task id for tracking.
```

The loader parses these blocks into `GoldenWorkflow.narration` (a list of prose
strings). They serve as voice-over copy for the demo render.

---

## 3. Assertion DSL

All assertion types live in `schema.py` and are evaluated by `assertions.py`.

| Type | Required fields | Passes when |
|---|---|---|
| `skill_routed` | `name` | The named skill appears in the step's `skills_routed` list |
| `skills_routed_sequence` | `names` | The names appear as a **subsequence** (in order, gaps allowed) of session-level `skills_routed` |
| `tool_called` | `name`, `args?` | A tool call with that name exists; if `args` given, it must be a deep subset of the observed call args |
| `task_returned_id` | `tool` | The last successful result of that tool carries a non-empty `task_id` field in its `content` |
| `artifact_exists` | `kind` | At least one artifact with `kind == kind` is present |
| `response_contains` | `any_of` | The response text (lowercased) contains **at least one** of the strings |
| `tool_result_path` | `tool`, `path`, and exactly one of `equals`/`gte`/`lte`/`is_not_null` | The last successful result of that tool has the dot-path value passing the comparator |

`normalize_tool_name` strips a trailing `_tool` suffix from both expected and
observed tool names before comparison, so `run_batch_pricing_tool` and
`run_batch_pricing` are treated as identical.

---

## 4. Fixtures file (`*.fixtures.json`)

```jsonc
{
  "schema_version": 1,
  "seed": {
    "portfolios": [
      { "alias": "ctrl", "id": 1, "name": "Control Portfolio" }
    ],
    "pricing_profiles": [
      { "alias": "pp1", "id": 10, "name": "Daily", "valuation_date": "2026-06-20" }
    ],
    "positions": [
      { "alias": "p1", "portfolio": "ctrl",
        "underlying": "AAPL", "product_type": "EuropeanVanillaOption", "quantity": 100 }
    ],
    "risk_runs": [
      { "alias": "rr1", "portfolio": "ctrl",
        "status": "completed", "metrics": {} }
    ]
  },
  "replay": {
    "step-1-run-risk": {
      "ai": {
        "tool_calls": [
          { "id": "tc1", "name": "run_batch_pricing", "args": { "portfolio_id": 1 } }
        ]
      },
      "tool_results": [
        { "name": "run_batch_pricing", "tool_call_id": "tc1",
          "content": { "task_id": "task-abc" } }
      ],
      "skills_routed": ["run-risk"],
      "artifacts": [],
      "response_text": "I have queued a fresh risk calculation (task-abc)."
    }
  }
}
```

### Seed namespaces

| Namespace | Required keys | FK (alias reference) |
|---|---|---|
| `portfolios` | `alias`, `id`, `name` | — |
| `pricing_profiles` | `alias`, `id`, `name`, `valuation_date` | — |
| `positions` | `alias`, `portfolio`, `underlying`, `product_type`, `quantity` | `portfolio` → `portfolios` |
| `risk_runs` | `alias`, `portfolio` | `portfolio` → `portfolios` |

`market_data` is **not** yet modeled — market snapshots must be supplied
through the application's normal pricing-profile pathway if needed.

### `$seed` interpolation

Any assertion field value can reference a seed row as `$seed.<ns>.<alias>.<field>`.
The loader resolves these references against `FixtureBundle.seed_map` **before**
Pydantic validation, so typed fields (`equals: float`, `gte: float`) receive real
values.

Example: `equals: $seed.portfolios.ctrl.id` → `equals: 1` at load time.

### Replay entry shape

Each replay entry has:
- `ai.tool_calls`: list of `{id, name, args}` — the AI's tool calls this turn
- `tool_results`: list of `{name, tool_call_id, content, error?}` — results for each call
- `skills_routed`: list of skill names activated this turn
- `artifacts`: list of `{kind, path?, …}` artifact records
- `response_text`: the assistant's final text for this turn

`tool_call_id` in each result must match an `id` in `ai.tool_calls`; the loader
validates this and raises `WorkflowError` on a mismatch.

---

## 5. Three consumers

### 5.1 Deterministic regression (`tests/test_golden_workflow_regression.py`)

Uses `transcript_from_replay(loaded)` to build a `MatchTranscript` entirely from
the canned replay entries — no LLM, no network. Then runs the full assertion
engine (`objective_score`) against it.

**Run:**
```bash
python -m pytest tests/test_golden_workflow_regression.py -v
```

The regression test is the primary CI gate. A workflow cannot ship without its
replay entries passing the assertion manifest.

### 5.2 LLM arena (`app.services.arena` + `/api/arena/*` + the Arena page)

The arena evaluates live LLM responses against the same objective manifest. It
consists of five layers:

| Layer | Module | Role |
|---|---|---|
| Models / registry | `arena/models.py` | `ArenaModel` dataclass; `CANDIDATE_MODELS` list; `canonical_model_id()` lookup |
| Runner | `arena/runner.py` | `run_match()` — seeds a temp SQLite, drives steps, returns `MatchTranscript`; run_* tools wrapped to block until task completion |
| Judge | `arena/judge.py` | LLM-based qualitative scoring against per-step rubric + success rubric |
| Scoring | `arena/scoring.py` | `objective_score()` → `(score_0_100, passed, total)`; `total_score()` blends objective + judge |
| Store | `arena/store.py` | SQLAlchemy persistence (`ArenaRun`, `ArenaMatch`); `leaderboard()` |

**API endpoints** (registered in `app/routers/arena.py` and mounted in `app/main.py`):
```
POST /api/arena/runs                  → 202 {run_id, status}
GET  /api/arena/runs                  → {runs, total}
GET  /api/arena/runs/{id}             → {run, matches}
GET  /api/arena/matches/{id}/transcript
GET  /api/arena/leaderboard           → ranked model rows
GET  /api/arena/models                → registered model list
```

**Frontend:** `frontend/src/routes/Arena.live.tsx` renders the leaderboard and
lets users trigger new runs.

**Objective manifest for the flagship** (`risk-manager-control-day`): 32 points
(7 expected_skill points + 10 expected_tools + 9 step assertions + 6 success
assertions). The 9th step assertion is a `tool_called` check on the backtest step
verifying `run_backtest` carries the instructed date window (2026-03-24 →
2026-06-24) — see GH #6.

**Running live arena matches requires:**
```bash
ARENA_LIVE=1 ZENMUX_API_KEY=<key> python -m pytest tests/test_arena_runner.py -k live
```
Without `ARENA_LIVE=1` all live tests are **skipped, not failed**.

**Live driver (wired):** `run_match` drives the **real desk orchestrator**. Each
match seeds the workflow's fixtures into the main DB, creates an arena-tagged
`AgentThread` (`source="arena"`, `arena_run_id`), and drives each workflow step
through `AgentService.stream_and_persist` bound to the candidate Zenmux model
(`yolo_mode=True`, `confirmed_cost_preview=True`). The `MatchTranscript` is then
reconstructed from the persisted trace spans by
`trace_harvest.transcript_from_trace`. `skills_routed` is read as **ground
truth** from `read_file` loads of `/skills/workflows/<domain>/<name>/SKILL.md`.
The `drive=`/`harvest=` seams stay injectable for unit tests.

**Known constraint — sequential matches:** matches run one at a time because the
async checkpointer SQLite serialises writes. Each match seeds a fresh fixture set
(unique IDs), so sequential matches never contaminate one another.

**Background tasks (settled between steps):** `run_batch_pricing` and similar
queue a `TaskRun` that a process-global thread pool runs asynchronously. After
each workflow step, `run_match` calls a `settle()` waiter that blocks until every
`TaskRun` queued since the match started (excluding the arena run itself) is
terminal, so a later step (e.g. `get_latest_risk_run`) reads the freshly computed
result rather than stale state. The wait is bounded (`TASK_SETTLE_MAX_ATTEMPTS`),
so a stuck task degrades to stale data instead of hanging the match. `settle=` is
injectable (tests pass a no-op).

### 5.3 Demo generation (`app.services.demo.composition` + `scripts/generate_demo.py`)

Converts a `LoadedWorkflow` + `MatchTranscript` into a `CompositionBundle`
(section plan + narrator scripts) and optionally drives the hyperframes render
pipeline to produce HTML + MP4.

**Deterministic composition (CI-safe):**
```bash
python scripts/generate_demo.py \
    --workflow-id risk-manager-control-day \
    --source regression \
    --output-dir artifacts/demos/my-run
```

Writes `section_plan.json` and `narrator_scripts.json` to the output directory.
No LLM call, no network, no media tools required.

**Full render (requires `DEMO_RENDER=1`):**
```bash
DEMO_RENDER=1 python scripts/generate_demo.py \
    --workflow-id risk-manager-control-day \
    --source regression
```

Requires Node.js ≥ 18, `npx hyperframes` on PATH, a TTS provider (e.g.
`OPENAI_API_KEY` for OpenAI TTS), and `ffmpeg`.

**Using an arena transcript as source:**
```bash
python scripts/generate_demo.py \
    --workflow-id risk-manager-control-day \
    --source arena \
    --run-id 42 \
    --model gpt-5.5-turbo \
    --transcript-path /path/to/transcript.json
```

---

## 6. Authoring a new workflow

1. **Draft the `.md` file** in `definitions/` with a kebab `id` matching the
   filename stem. Start with the YAML frontmatter (see §2.1), then add one
   `## Step N — …` narration block per step.

2. **Create the `*.fixtures.json` file** with `schema_version: 1`, a `seed`
   block covering all objects the agent needs (portfolios, positions, profiles,
   risk runs), and an empty `replay` block to start.

3. **Verify skills and tools exist.** The loader refuses unknown `expected_skill`
   or `expected_tools[].name` values. Run:
   ```bash
   python -m pytest tests/test_golden_workflow_registry.py -v
   ```

4. **Fill in replay entries** by running the agent manually and capturing its
   turns, or by constructing synthetic responses that match your assertions.
   Each entry key must match the `replay:` field in the corresponding step.

5. **Write the manifest/exact-content tests** (add to
   `tests/test_flagship_loads.py` or a new test file) to pin the workflow id,
   step count, tag list, and objective point total. This prevents silent drift.

6. **Run the full golden-workflow suite:**
   ```bash
   python -m pytest tests/test_golden_workflow_*.py tests/test_flagship_loads.py \
       tests/test_match_transcript.py tests/test_arena_*.py \
       tests/test_demo_composition.py tests/test_generate_demo_smoke.py -v
   ```
   All 203 tests must pass before merge.

---

## 7. Known constraints and carry-forwards

| Constraint | Detail |
|---|---|
| Sequential arena matches | Shared async-checkpointer SQLite serialises writes; fresh fixtures per match avoid contamination |
| Live driver wired | `run_match` drives the real orchestrator (`stream_and_persist`) + harvests the transcript from the trace; `drive=`/`harvest=` injectable for tests |
| Background tasks not executed | Queued `TaskRun`s aren't run by a worker in-process; workflows assert queued action + `task_id`, not completed results |
| `market_data` namespace | Not yet modeled in fixtures; use application-level pricing profiles instead |
| `ARENA_LIVE=1` gate | Live arena tests skip without this env var + `ZENMUX_API_KEY` |
| `DEMO_RENDER=1` gate | Hyperframes render pipeline skipped without this env var |
