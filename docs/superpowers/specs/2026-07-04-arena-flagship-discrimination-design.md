# Arena flagship discrimination overhaul — `risk-manager-control-day` v2

**Date:** 2026-07-04
**Status:** Approved design, pending implementation
**Scope:** `backend/app/golden_workflows/` (schema, assertions, flagship definition + fixtures), `backend/app/services/arena/` (scoring, runner, store, judge prompt), Arena frontend, tests, docs.

## 1. Problem

Empirical item analysis over the 42 scored arena matches of `risk-manager-control-day`
(runs #1–#9, `arena_match.score_breakdown` in the live DB) shows the objective score no
longer discriminates:

- Among **engaged** runs (objective ≥ 50, n=29), 15 of 31 checks pass at ≥ 93% — pure
  ceiling. The engaged pool means 83.7 (sd 12.1) with frontier models compressed at
  88–97.
- Nearly all items load on one factor (point-biserial r = +0.83…+0.94 vs total):
  "did the loop complete". Effective independent item count ≈ 8.
- One item is broken: `step3 | skill: read-risk-result` (10.3% pass, r = +0.10) — the
  known `skills_routed` dedup blind spot (SKILL.md read in step 1 is never re-read, so
  the check *cannot* pass). It measures a runtime artifact, not ability.
- Five of six session-level checks duplicate per-step twins (`task_returned_id` ×4,
  `artifact_exists`) — 6 of 32 points are double-counted.
- Frontier rank order is therefore decided by the LLM judge, which is high-variance
  (Sonnet 4.6: judge mean 24.8 vs objective 64).
- 13/42 matches score < 50 including pure-0 rows attributable to infra-blank routes
  (MiniMax, Qwen 3.7 Plus, Agnes, Hunyuan) — the leaderboard punishes gateway
  availability, not capability.

Validated orthogonal signal exists: replaying the (in-flight, uncommitted) backtest-dates
`tool_called` check against the 29 engaged transcripts yields 22/29 pass — and its
failures are top scorers (Opus 4.8 ×2, Sonnet 4.6). Report artifacts range 33 B–16.7 KB;
only ~half mention the backtest, ~13% quote a CVaR figure. Scenario-set over-execution
(extra sets beyond `market_crash`) occurs in ~30% of engaged runs.

## 2. Goal

Raise the objective ceiling and remove noise so the benchmark separates models across
four axes — **procedural** (run the loop), **adherence** (follow the exact instruction),
**grounding** (quote real numbers, not hallucinated ones), **synthesis** (the governance
report actually synthesizes the session) — while keeping the scoring engine pure
(no network, no LLM, flat +1 per check) and keeping infra failures out of capability
aggregates.

Non-goals: re-scoring historical runs (leaderboard is already run-scoped, commit
6535726); changing the other two workflow definitions (they gain the new assertion
*types* but their manifests are untouched this round); judge model or blend-weight
changes.

## 3. Workflow manifest v2 (7 steps → 9, denominator 32 → 39)

`backend/app/golden_workflows/definitions/risk-manager-control-day.md` — steps below
give the exact frontmatter deltas. Prose sections (`## Step N`) are renumbered and two
new sections written; narration must continue to match step order (loader enforces).

### Step 1 — Read stale risk *(modified)*

Broaden the staleness lexicon (34.5% pass today is judgment signal mixed with matcher
brittleness — "outdated", "24 hours old" currently fail):

```yaml
assertions:
  - type: response_contains
    any_of: ["stale", "out of date", "outdated", "24 hours", "yesterday", "not fresh", "no longer current"]
```

Skill check, tool expectation unchanged. **3 checks.**

### Step 2 — Refresh the risk *(unchanged)*

Profile-args assertion deliberately NOT added: all engaged runs already resolve
Control Profile → id 2 (zero variance — it would be another ceiling item). **3 checks.**

### Step 3 — Confirm the hotspot *(modified)*

- `expected_skill: null` — drop the dedup-blind skill check (see §4.1).
- Add numeric grounding: the response must quote the actual AAPL delta from the tool
  result, not a hallucinated figure.

```yaml
expected_skill: null
expected_tools:
  - name: get_latest_risk_run
assertions:
  - type: response_contains
    any_of: ["AAPL"]
  - type: response_quotes_tool_value
    tool: get_latest_risk_run
    path: "hotspot.delta"
    near: ["delta"]
```

**3 checks** (tool + AAPL + quote).

### Step 4 — Greeks landscape *(unchanged)* — **4 checks.**

### Step 5 — NEW: Grid comprehension

```yaml
- user: "From the landscape you just ran: what is portfolio gamma at a +10% spot shift, and what does delta become at a -20% shift?"
  expected_skill: null
  expected_tools: []
  outcome: >
    The agent answers from the retrieved landscape grid (re-fetching via
    get_greeks_landscape_run is acceptable but not required), quoting the
    actual gamma at +10% and delta at -20% from the computed run.
  assertions:
    - type: response_quotes_tool_value
      tool: get_greeks_landscape_run
      path: "landscape[spot_shift=0.1].gamma"
      scope: session
      near: ["gamma"]
    - type: response_quotes_tool_value
      tool: get_greeks_landscape_run
      path: "landscape[spot_shift=-0.2].delta"
      scope: session
      near: ["delta"]
    - type: tool_not_called
      name: run_greeks_landscape
  replay: step-5-grid-comprehension
```

`scope: session` because the landscape result lives in step 4's context (§4.3 defines
cumulative lookup). The `tool_not_called` closes the recomputation escape hatch:
without it a model could re-dispatch `run_greeks_landscape` in this step, read the
fresh result, and pass the grounding checks without demonstrating it can read data
it already has (re-fetching via `get_greeks_landscape_run` remains allowed — only
re-*dispatch* is forbidden). Tests reading returned data, not just dispatching.
**3 checks.**

### Step 6 — Scenario test *(modified; was step 5)*

Add exact-args adherence (over-execution: ~30% of engaged runs ran 5–10 extra sets;
`_deep_subset` already enforces exact list length) and CVaR grounding:

```yaml
assertions:
  - type: task_returned_id
    tool: run_scenario_test
  - type: tool_result_path
    tool: get_scenario_test_run
    path: "results.var_cvar.cvar"
    lte: 0
  - type: tool_called
    name: run_scenario_test
    args_any_of:
      - predefined: ["market_crash"]
      - scenario_set: "market-crash"
    exclusive_keys: ["predefined", "custom", "scenario_set"]
  - type: response_quotes_tool_value
    tool: get_scenario_test_run
    path: "results.var_cvar.cvar"
    match: magnitude
    near: ["cvar", "expected shortfall", "loss"]
```

`args_any_of` (§4.5) because the tool legitimately accepts either calling convention
— live transcripts show both `predefined: ["market_crash"]` (majority) and
`scenario_set: "market-crash"`; punishing the literal-prompt form would be a false
negative. Over-execution is blocked from two sides: `_deep_subset`'s exact
list-length rule fails extra sets appended to `predefined`, and `exclusive_keys`
(§4.5) fails **mixed-carrier** calls — `run_scenario_test` accepts `predefined`,
`custom`, and `scenario_set` in one request, so without it a call like
`predefined: ["market_crash"], custom: [...]` would subset-match the first
alternative while still over-executing. The CVaR quote uses `match: magnitude` —
loss language ("a loss of $2.1M") legitimately drops the sign.

**7 checks** (skill + 2 tools + 4 assertions).

### Step 7 — Backtest *(was step 6; keeps the in-flight dates check)* — **5 checks.**

### Step 8 — NEW: Trap — nonexistent scenario set

```yaml
- user: "Also stress the book with the 'liquidity-crunch' scenario set using the Control Profile."
  expected_skill: null
  expected_tools:
    - name: list_scenario_library
  outcome: >
    No such scenario set exists. The agent checks the scenario library
    (list_scenario_library), reports that 'liquidity-crunch' is not available,
    and offers the nearest predefined alternatives WITHOUT launching a run or
    silently substituting a different set.
  assertions:
    - type: tool_not_called
      name: run_scenario_test
    - type: response_contains
      any_of: ["not found", "doesn't exist", "does not exist", "no such", "not available", "unavailable", "couldn't find", "cannot find", "not a predefined", "not in the library", "no scenario set"]
  replay: step-8-trap-missing-scenario-set
```

Pass = **verify**-then-report: the `list_scenario_library` tool expectation makes the
lookup mandatory — a model that guesses "not available" without checking earns the
phrasing point but not the verification point. Silent substitution or a raw failed
launch = fail on `tool_not_called`. Interleaved `list_scenario_library` calls don't
break the session subsequence check. **3 checks.**

### Step 9 — Governance report *(modified; was step 7)*

Legacy-tool trap (already stated in prose: `create-risk-report` skill forbids the
legacy `create_report` job) plus synthesis-coverage checks (today only ~half of report
bodies mention the backtest; ~13% quote CVaR):

```yaml
assertions:
  - type: artifact_exists
    kind: text
  - type: tool_not_called
    name: create_report
  - type: artifact_contains
    kind: text
    any_of: ["AAPL"]
  - type: artifact_contains
    kind: text
    any_of: ["backtest", "back-test", "historical replay"]
  - type: artifact_contains
    kind: text
    any_of: ["cvar", "expected shortfall"]
```

The CVaR entry deliberately excludes bare `"VaR"`: matching is case-insensitive
substring, so `"VaR"` would award the point to VaR-only (or even "variance"-only)
reports — hiding exactly the missing-CVaR evidence this check exists to expose
(`"cvar"` also covers `"CVaR"` case-insensitively).

**7 checks** (skill + tool + 5 assertions).

### Session success *(modified)*

- Keep `tools_routed_sequence` unchanged — steps 5/8 add no signature tools, so the
  7-tool sequence still encodes the designed order (skip/reorder still fails).
- **Delete** the 5 duplicate checks (`task_returned_id` ×4, `artifact_exists`) —
  identical pass patterns to their per-step twins; pure denominator dilution.

**1 check.**

### Point budget

| Unit | Checks | Axis composition |
|---|---|---|
| Step 1 | 3 | 2 procedural, 1 adherence |
| Step 2 | 3 | 3 procedural |
| Step 3 | 3 | 1 procedural, 1 adherence, 1 grounding |
| Step 4 | 4 | 4 procedural |
| Step 5 (new) | 3 | 2 grounding, 1 adherence |
| Step 6 | 7 | 4 procedural, 1 adherence, 2 grounding |
| Step 7 | 5 | 4 procedural, 1 adherence |
| Step 8 (new) | 3 | 1 procedural, 2 adherence |
| Step 9 | 7 | 2 procedural, 1 adherence, 4 synthesis |
| Session | 1 | 1 procedural |
| **Total** | **39** | 22 procedural / 8 adherence / 5 grounding / 4 synthesis |

Update the `scoring.py` docstring ("32 for the flagship") and every exact-count test
(§9).

## 4. Schema + assertion engine changes (shared, all workflows)

### 4.1 Nullable `expected_skill`

`schema.py::Step.expected_skill: str | None` (default stays required-present in YAML —
`null` must be explicit). In `scoring.py::_evaluate_objective`, when `expected_skill is
None` **no skill check is emitted** (the step contributes only tools + assertions).
**`registry.py` must change too**: the loader unconditionally calls
`normalize_skill(step.expected_skill)` and validates it against `skill_names()` for
every step — with `null` steps that crashes at bundle load, before any scoring runs.
Skip skill-existence validation when `expected_skill is None`, and add a loader test
with an explicit `null` step so this cannot regress.
Rationale: `skills_routed` only records a skill when its SKILL.md is read; the runtime
never re-reads an already-loaded file, so repeat-skill steps structurally cannot pass.
This mirrors the session-level `skills_routed_sequence` → `tools_routed_sequence`
migration already made for the same reason.

### 4.2 New assertion: `artifact_contains`

```python
class _ArtifactContains(BaseModel):
    type: Literal["artifact_contains"]
    kind: str
    any_of: list[str] = Field(min_length=1)
```

Evaluator: over `ctx.artifacts` with matching `kind`, extract the body as
`str(a.get("content") or a.get("text") or "")`; pass iff **any** artifact body contains
**any** `any_of` entry, case-insensitively. Failure detail names the kind and the
missed terms. (Artifact bodies are already captured in transcripts — verified across
75 artifacts in the stored runs.)

### 4.3 New assertion: `response_quotes_tool_value`

```python
class _ResponseQuotesToolValue(BaseModel):
    type: Literal["response_quotes_tool_value"]
    tool: str
    path: str
    rel_tol: float = 0.02          # 0 < rel_tol < 1
    scope: Literal["step", "session"] = "step"
    match: Literal["signed", "magnitude"] = "signed"
    near: list[str] | None = None  # label anchors; None = whole response
```

Semantics:

1. Resolve the **last** successful result of `tool` in the evaluation context and dig
   `path` (extended `_dig`, §4.4). Target must be numeric (bool excluded); a missing
   path or non-numeric target fails with a clear detail message.
2. Determine the scan region. With `near` set, only numeric tokens starting within
   **160 characters after the start of any anchor occurrence** (case-insensitive
   substring) are considered; with `near: None`, the whole `response_text`. Anchors
   bind the number to the metric being asked about: without them, a multi-value
   question can be satisfied by **swapped** answers (step 5: "gamma is −310,000 and
   delta is −9,600" would contain both target tokens and pass both assertions).
   Every grounding assertion in this manifest sets `near`.
3. Scan the region for numeric tokens: regex over
   `-?\d[\d,]*(?:\.\d+)?\s*(k|m|mm|bn|b)?%?` (case-insensitive suffix), normalizing
   commas, expanding suffixes (k→1e3, m/mm→1e6, bn/b→1e9), and additionally trying
   `token/100` for `%`-suffixed tokens.
4. Matching is **sign-sensitive by default** (`match: "signed"`): pass iff any token
   satisfies `|token − target| ≤ rel_tol × |target|`, sign included — quoting
   `+148,000` against a delta of `−148,000` fails, because rewarding an inverted risk
   direction is a scoring false positive. `match: "magnitude"` compares
   `|abs(token) − abs(target)| ≤ rel_tol × |abs(target)|` and is reserved for
   loss-language metrics where dropping the sign is idiomatic ("a loss of $2.1M" for
   cvar = −2,100,000) — only the step-6 CVaR quote uses it. When `target == 0`, use
   absolute tolerance `rel_tol` instead. Consequence for signed checks: a response
   writing "negative delta of 220,000" (sign as word, unsigned token) fails — accepted
   strictness for a risk benchmark; replay fixtures must quote signed figures.

**Scope** (evaluated in `scoring.py`, not the engine): `step` uses the step's own
context (default, unchanged behavior). `session` uses a **cumulative** context — tool
results from steps 0…i merged, `response_text` still the *current* step only. Scoring
builds the cumulative tool-result list incrementally per step; `evaluate_assertion`
just receives whichever `AssertionContext` scoring passes.

Live-run note: targets are dug from the *actual* tool results in the transcript, so the
assertion self-grounds against whatever the live QuantArk run produced — no fixture
values are baked into the manifest.

Known limitation (accepted): a response that only paraphrases without any numeric token
within tolerance fails even if directionally right — that is the point of the check.

### 4.4 `_dig` list selector

Extend `assertions.py::_dig` so a path segment may be `name[key=value]`: after
resolving `name`, if the current node is a list, select the first element whose
`key` equals `value` (numeric-aware comparison: parse `value` as int/float when
possible, else string). Examples: `landscape[spot_shift=0.1].gamma`,
`landscape[spot_shift=-0.2].delta`. Bare integer segments keep their existing
index-into-list meaning. `tool_result_path` gains this for free.

### 4.5 `tool_called.args_any_of`

`_ToolCalled` gains two fields:

- `args_any_of: list[dict] | None = None` — mutually exclusive with `args`
  (validator: at most one of the two may be set; `args_any_of` must be non-empty when
  present). Pass iff any candidate dict subset-matches some call of `name` (reuse
  `match_tool` per candidate). Needed where a tool has more than one legitimate
  calling convention for the same semantic instruction (step 6:
  `predefined: ["market_crash"]` vs `scenario_set: "market-crash"`).
- `exclusive_keys: list[str] | None = None` — closes the subset-matching bypass for
  multi-carrier tools: for the call/candidate pair that matched, every
  `exclusive_keys` entry **not** present in the matched candidate (or in `args`) must
  be *absent* from the call, where absent means missing, `None`, `[]`, or `""`
  (models legitimately pass `custom: []`). A mixed-carrier call
  (`predefined: [...], custom: [...]`) therefore fails even though it subset-matches
  one alternative. Composable with plain `args` too.

### 4.6 Axis tagging

Pure derivation, no manifest field. In `scoring.py`:

```python
AXIS_BY_KIND_TYPE = {
    # check kind "skill" and "tool"                  -> "procedural"
    # skills_routed_sequence, tools_routed_sequence,
    # task_returned_id                               -> "procedural"
    # tool_called, tool_not_called, response_contains-> "adherence"
    # tool_result_path, response_quotes_tool_value   -> "grounding"
    # artifact_exists, artifact_contains             -> "synthesis"
}
```

Each check dict in the breakdown gains `"axis"`, and `objective_breakdown()` gains
`"axes": {axis: {"passed": n, "total": n}}`. The aggregate score stays flat +1/check —
axes are reporting, not weighting.

## 5. Fixtures (`risk-manager-control-day.fixtures.json`)

Two new hand-authored replay entries (replay entries are plain JSON:
`{ai, tool_results, skills_routed, artifacts, response_text}`):

- **`step-5-grid-comprehension`** — `ai` has **no tool calls** (answers from step-4
  data); `response_text` quotes gamma −9,600 at +10% and delta −310,000 at −20%,
  consistent with the existing step-4 landscape fixture (`landscape[spot_shift=0.1]
  .gamma = -9600`, `landscape[spot_shift=-0.2].delta = -310000`). `skills_routed: []`.
- **`step-8-trap-missing-scenario-set`** — `ai` calls `list_scenario_library`;
  `tool_results` returns the predefined library (must NOT include "liquidity-crunch");
  `response_text` states the set is not available and offers `market_crash` /
  `severe_downturn` as alternatives. `skills_routed: []`.

Also update existing replays so the golden transcript earns **39/39** (regression test
enforces this): `step-3` `response_text` quotes delta −148,000 (signed, matching
`hotspot.delta`); `step-6` (scenario) replay's `ai` call args use
`predefined: ["market_crash"]` (satisfying one `args_any_of` alternative) and its
`response_text` quotes CVaR −2,100,000 (magnitude match). All grounded figures in
replay responses are written **with their sign** (signed matching, §4.3).

## 6. Judge: anchored rubric

Replace the 4 flat rubric points with 6 anchored points (manifest `success.rubric`;
text flows through `judge.py::_collect_rubric_points` unchanged):

1. "Staleness judgment: 100 = flags yesterday's run as stale before acting and
   recommends a refresh; 50 = mentions the timestamp but draws no conclusion;
   0 = treats the stale result as current."
2. "Numeric grounding: 100 = quoted delta/gamma/CVaR figures match the tool results;
   50 = numbers partially match or are rounded beyond recognition; 0 = numbers absent
   or fabricated."
3. "Instruction adherence: 100 = exact backtest window and exactly the market-crash
   set; 50 = one substitution; 0 = both substituted or scope invented."
4. "Trap handling: 100 = verifies 'liquidity-crunch' does not exist and says so;
   50 = hesitates or asks without checking; 0 = silently substitutes or launches a
   different set."
5. "Report synthesis: 100 = the artifact covers hotspot, landscape, scenario loss and
   backtest with figures; 50 = covers some analyses; 0 = thin or missing artifact."
6. "Process: 100 = all four async tasks return ids in the designed order; 50 = minor
   reordering; 0 = steps skipped."

`judge.py`: add one line to the system message — "Each rubric point defines score
anchors; pick the score matching the closest anchor and use the full 0–100 range."
No structural changes (prompt shape, parser, retries untouched).

## 7. Infra-invalid handling (arena task / store / API)

- **Detection lives at the task boundary, not in the runner.** `run_match` returns a
  `MatchTranscript`; persistence, scoring, and the judge run later in the arena
  **task** (`task.py`) — that is where the check goes, immediately after the
  transcript is obtained and **before** scoring/judge. A match is infra-blank iff
  **both**: (a) every step has `not step.tool_calls and not
  step.response_text.strip()`, and (b) at least one step recorded a transport/provider
  error (`step.errors` non-empty). Then `status='invalid'`, `error='infra_blank'`,
  scores left NULL, judge skipped. **No auto-retry.** The error-evidence requirement
  (b) keeps genuine model failures scoreable: an all-blank transcript with *no*
  recorded errors is a model that silently did nothing and stays a real scored 0 —
  invalidity must be corroborated by transport evidence, never inferred from
  blankness alone. Invalid matches stay visible (per-model `invalid_count` on the
  leaderboard), so degraded routes are surfaced rather than silently dropped.
- **Store/aggregation** (`store.py` + `/api/arena` endpoints): leaderboard means and
  trial counts consider only `status='scored'` matches; expose `invalid_count` per
  (run, model). No migration needed — `arena_match.status` is a free string column.
- **Evidence visibility:** the run-detail `MatchSummary` response gains
  `error: str | null` so the corroborating reason (`infra_blank`) is auditable via
  the supported API path, and the Arena UI renders that reason on invalid match
  cells — exclusions must be explainable, not just visible as a count.
- **Scoring path is untouched** — invalid matches never reach `objective_score`.

## 8. Frontend (Arena page)

`frontend/src/routes/Arena.live.tsx` / `Arena.css`, per `frontend/CLAUDE.md`
(token-only styling, shared Table primitive):

- Leaderboard rows: `invalid_count` surfaced as a muted chip (e.g. "2 infra") next to
  the trial count; invalid matches excluded from displayed means (server already
  excludes — the UI just renders what the API returns).
- Match list: `invalid` status badge (distinct token color from `error`/`failed`).
- Match breakdown view: a compact 4-cell axis strip (procedural / adherence /
  grounding / synthesis, `passed/total` each) above the existing per-check table,
  fed by `score_breakdown.objective.axes`.

## 9. Tests

| File | Change |
|---|---|
| `test_golden_workflow_schema.py` | nullable `expected_skill`; `artifact_contains` validation (non-empty `any_of`); `response_quotes_tool_value` validation (`0 < rel_tol < 1`, scope/match literals, `near` non-empty when present); `tool_called` `args`/`args_any_of` mutual exclusion + non-empty; `_dig` selector syntax errors |
| `test_golden_workflow_registry.py` | bundle with an explicit `expected_skill: null` step loads (no skill-existence validation crash) |
| `test_golden_workflow_assertions.py` | evaluator cases: comma/suffix/percent token normalization, signed vs magnitude matching (inverted-sign token fails signed, passes magnitude), `near` anchoring (swapped gamma/delta answers fail; token outside the 160-char window fails; anchor case-insensitive), `rel_tol` boundary, target-0 abs-tol, missing path, non-numeric target; `args_any_of` (each alternative matches; over-long `predefined` list fails both); `exclusive_keys` (mixed-carrier call fails despite subset match; `custom: []`/`None`/missing counts as absent; composes with plain `args`); `artifact_contains` case-insensitivity + kind filtering; `_dig` `[key=value]` incl. negative floats |
| `test_arena_scoring.py` | null-skill emits no check; `scope: session` cumulative lookup (result in an earlier step); axis subtotals sum to passed/total; new denominator 39 |
| `test_flagship_loads.py` | 9 steps, replay refs present, point-count table |
| `test_golden_workflow_regression.py` | golden replay earns **39/39** (forces fixture consistency in §5) |
| `test_arena_runner.py` / arena task tests | infra-blank (all-blank + step errors) → `invalid`, scores NULL, judge never invoked; all-blank *without* errors stays `scored` 0; text-but-no-tools stays `scored` |
| `test_arena_store.py` / `test_arena_api.py` | invalid excluded from aggregates; `invalid_count` in responses |
| `frontend Arena.live.test.tsx` | invalid badge, infra chip, axis strip rendering |

Suite-hygiene reminders: run backend tests from repo root with `.venv/bin/python -m
pytest`; the primary `.env` leaks into `Settings()` for some suites (validate in a
no-`.env` worktree if full-suite runs misbehave).

## 10. Docs & changelog

- `CHANGELOG.md` `[Unreleased]`: flagship v2 (9 steps / 39 points), two new assertion
  types, `args_any_of`, nullable `expected_skill`, `_dig` selectors, axis subtotals,
  `invalid` match status, Arena UI badges/axis strip.
- `CLAUDE.md` (Golden Workflows / arena notes): denominator now 39; `invalid` status
  semantics; `response_quotes_tool_value` signed-vs-magnitude matching;
  `expected_skill: null` for repeat-skill steps.
- `README.md`: Arena page bullet (invalid handling + axis breakdown) — user-facing.

## 11. Sequencing & compatibility

1. The current working tree already contains uncommitted related changes (backtest
   `tool_called` check, `tools_routed_sequence` migration, `tool_not_called` support).
   **Commit or fold those in first** — this design builds directly on them.
2. Implementation order: schema/engine (§4) → manifest+fixtures (§3, §5) → scoring
   axes (§4.5) → runner/store/API (§7) → judge rubric (§6) → frontend (§8) → docs.
3. Historical runs are not re-scored; the run-scoped leaderboard keeps old boards
   internally consistent. Cross-run comparisons across the v1/v2 boundary are invalid
   by design — the run page already scopes to a single run.
4. Other workflow manifests are untouched; they load unchanged under the extended
   schema (all new fields optional / new types additive).
