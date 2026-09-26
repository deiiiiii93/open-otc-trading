# Arena — running and trusting a board

Launching, resuming, merging and purging runs, and the infrastructure failure modes that reach a leaderboard looking like model ability.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** Manifests, assertions and the scoring kernel: [`golden_workflows/CLAUDE.md`](../../golden_workflows/CLAUDE.md). Contestant routing and budgets: [`config/CLAUDE.md`](../../../../config/CLAUDE.md). Publishing: [`docs/arena/deploy/CLAUDE.md`](../../../../docs/arena/deploy/CLAUDE.md).

---

## Runs management (New Run / delete / merge)

The `/arena` Runs panel launches, deletes, and merges runs (endpoints in
`routers/arena.py`, store logic in `services/arena/store.py`):

- **New Run** launches multiple workflows × models with a **trials** count
  (`arena_run.trials`, migration **0045**, default 1). `task._execute` runs each pair
  `trials` times and folds the clean trials into one aggregate match via the shared
  `scoring.fold_trial_breakdowns` kernel — the SAME `n_trials` shape and CON scheme
  `store.merge_runs` produces. **Infra trials are skipped, not retried** (the async task
  layer already reruns whole failed runs); 0 clean trials → `invalid`. **`trials=1` is
  behavior-preserving** (single-trial aggregate = today's single match, derive-on-read
  card unchanged). Jury scores (when the jury runs) roll up onto the aggregate in
  `_record_pair` so the leaderboard's advisory subjective stats survive the wrap.
- **Delete** is **hard**: `store.delete_runs` drops the run + its matches (ORM cascade,
  via an ORM `update` so the session identity map stays in sync) and nulls dangling
  `agent_threads.arena_run_id`; the **router** then removes the transcript files and the
  `arena/<run_id>` artifact dir (store stays DB-pure). Missing ids are skipped.
- **Merge** stays non-destructive (reuses `store.merge_runs`).
- The frontend runs list polls while any run is **non-terminal** — poll only on
  `queued`/`running` (the backend emits `queued`, **not** the type union's `pending`);
  `completed`/`failed` are terminal.
- **`list_runs` must never build match dicts** (`_run_to_dict(include_matches=False)`).
  `run.matches` is a lazy relationship, so serializing it costs a query per run and
  then a `_match_to_dict` per match — each deriving an ability card, which **loads a
  workflow**. The router projects six scalar fields and discards the rest, so all of
  it was waste: measured **8.417 s to build 349 match dicts for the 87-run page and
  return 19 KB**, re-fetched every 4s by the polling Arena page (which requests
  `limit=200`, the router's clamp — so one request covers every run, not a page of
  20). `get_run` keeps `include_matches=True`; the drilldown is the one caller that
  needs them. `tests/test_arena_store.py` guards this by asserting that listing runs
  emits **no `arena_match` SQL at all** — expire the session first, or the identity
  map hides the lazy load the test exists to catch.

---

## Provenance: a board is one manifest on one app

Since 2026-09-25 (`provenance.py`, migration `0066`) every run carries
`arena_run.provenance` = `{stamped_at, app: {version, git_sha, git_dirty, label,
packages}, manifests: {workflow_id: {manifest_version, sha256, par_tool_calls,
par_calibrated}}}`, and every match `config.provenance` = what THAT match ran under.

- **Bump `manifest_version` on any scoring-relevant manifest edit.** The sha256
  (definition + fixtures + staged documents) catches an unbumped edit; the version
  is what a human reads on the board.
- **`--resume` refuses a run whose manifest changed** since it was stamped — start a
  new run. **`merge_runs` refuses** stamped matches with different manifest hashes.
  A pre-stamp run (`provenance` NULL) cannot be checked either way: NULL means
  UNKNOWN, never "same as today".
- **Cards derive on read, so the par is FROZEN in the stamp.** `_derive_card` prefers
  `config.provenance.par_tool_calls`; without it, editing a manifest's par re-cards
  every historical match. Pre-stamp matches still read today's par — freeze them
  before changing a par that published boards depend on.
- **The app label is captured ONCE per process** (`lru_cache`): the code in memory is
  fixed at import, so a commit landing mid-run must not relabel later matches.
- **Stamping never fails a run**: an unreadable manifest is recorded as
  `{"unavailable": reason}`, an explicit absence.

---

## A DB-wide tally must exclude MERGED runs

Any statistic computed by walking `arena_match` across **all** runs — a par
calibration, a per-check pass-rate tally, a call-count distribution — counts a merged
run's trials **a second time**, because `merge_runs` folds its sources' per-trial
breakdowns into the new run's `aggregate` rather than referencing them. Eight of this
DB's runs are merges, and they nest: **#58 contains #44, which is itself a merge of
[34, 35, 43]**, so those trials land in the tally three times.

- **Find them, never hardcode them:** `select distinct run_id from arena_match where
  json_extract(config,'$.merged_from') is not null`. Excluding exactly that set leaves
  every ORIGINAL run counted exactly once — including the sources of a merge, which is
  what you actually want. (`arena_run` carries no merge provenance; it lives on the
  MATCH `config`.)
- **A median hides the bug; `n` reveals it.** The 2026-08-26 `risk-limit-breach-day`
  par came out at exactly 26.0 both with and without the duplicates, because
  duplication is symmetric and a median is robust to it. The tell was the count — 36
  "fully-correct trials" from a workflow with only 12 real runs. **Sanity-check the
  sample size against the number of runs that could have produced it**, not just the
  statistic.
- A tally scoped to ONE run is unaffected (there is nothing to double), which is why
  the Run #58 per-check field audit was sound.

---

## Arena DB hygiene: three purge scopes, two ownership proofs

Golden workflows resolve books **by name**, so leftover rows don't just accumulate —
they make each successive match's name resolution harder than the last, which biases
scores **by position in the field**. Worse, the failure is SILENT: a model that resolves
the wrong same-named book gets a plausible, well-formed number and is graded wrong with
no error anywhere.

- **`_purge_seeded_portfolios`** reclaims FIXTURE rows: arena tag **AND** a name from
  **any registered workflow's** fixtures (`list_workflow_bundles()`). Scoping it to the
  current bundle only is the bug that let trader-rfq's "Arena Trader Desk" (3 positions,
  incl. NVDA) shadow high-board's "Desk Control Book" (5 positions).
- **`_purge_match_portfolios`** reclaims MODEL-CREATED rows on trace + baseline evidence
  (`collect_portfolio_ids_created` ∩ `id > pre-match baseline`), mirroring
  `_purge_match_rfqs`. Runs in a `finally`, because a leak here is **permanent**: the
  next baseline is taken above the leaked row, so its guard can never re-catch it.
- **`ARENA_PORTFOLIO_TAG` is NOT server-owned** — a model picks its own tags via
  `create_portfolio`, and Run #58 caught two model-created views that spontaneously
  tagged themselves `arena`. So the tag alone is never sufficient ownership proof for
  deletion; pair it with a known fixture name, or use trace+baseline evidence instead.
- **A KILLED match skips every `finally` purge** — and the leak is then permanent,
  because every later baseline sits above it. Run #115 (SIGKILLed 2026-08-19) left
  a "Board Review" view over id 9101 that shadowed every high-board match through
  run #140. **`_sweep_orphaned_match_portfolios`** runs before seeding and reclaims
  a portfolio when an ARENA thread's `create_portfolio` span minted that id at that
  row's `created_at` (±30 s): the timestamp replaces the lost baseline and spares a
  row that later reused the id. Portfolios only — RFQs, batches and scenario sets
  still rely on the `finally`; extend the same proof if one of them leaks.
- Both share `_delete_portfolios_with_dependents`, which sweeps dependents by
  introspecting mapped tables for `portfolio_id` / `position_id` in reverse
  FK-dependency order. **Ownership is the caller's job** — that helper re-checks nothing.
- **`_purge_match_scenario_sets`** reclaims MODEL-CREATED **scenario sets**, the
  third namespace — a DIRECTORY, not a table, so the baseline is the set of names
  present pre-match (`scenario_set_name_baseline`) and the trace evidence is
  `collect_scenario_set_names_saved` (spans of `save_scenario_set` /
  `generate_scenario_set` — **not** `run_scenario_test`, which merely *names* a set
  to execute). Also in the `finally`, same permanence argument.
  - **A reserved-name guard cannot see a near-miss name.** `_purge_seeded_trap_sets`
    and `_assert_trap_sets_absent` key on the EXACT `trap_absent_sets` entry, and
    both passed cleanly for five weeks while `stagflation-shock-2011-compact` and
    `-x10` sat in the library beside them. **A model asked for a set that does not
    exist does not stop — it invents one**, which is precisely how the near-miss
    name gets minted. The trap step is the one place guaranteed to provoke this.
  - Cost, measured: the leaked 45-scenario set turned the flagship's step-8 trap
    into a 141 KB retrieval problem (Run #109 `high`: 273 calls / 91 errors there,
    81 of them brute-forcing artifact keys). A leaked on-disk `market-crash` drifted
    1 scenario → a 5-point grid on 2026-07-09, moving CVaR −7759 → −12175 — which
    is why the flagship pins the `market_crash` **predefined built-in** and
    `exclusive_keys` blocks the on-disk set. **Never let a manifest depend on a
    mutable on-disk set.**
  - The purge **spares a pre-existing set the model OVERWROTE** (the name is in the
    baseline). Deletion cannot restore the original and would destroy desk work —
    so overwrite drift stays a manifest-design problem, not a cleanup one.
  - Reuse `scenario_catalog._safe_name` when mapping a harvested name to its file;
    restating the regex would let the purge silently miss what the writer created.

---

## Long arena runs wedge silently — watch the trace clock, not the process

Measured twice (runs #115 and #117, 2026-08-19/20, ~90 min and ~40 min lost). The
run process stays alive at **0% CPU** holding one ESTABLISHED socket to the local
proxy; the run status stays `running`; nothing raises and nothing exits.

- **`timeout=None` is passed to `ChatAnthropic` deliberately**, and the OpenAI path
  has a stream-chunk timeout that DOES fire (`langchain_openai.stream_chunk_timeout
  fired` appears in the log) — and the process hung anyway. The hang is **below**
  the layer that owns a timeout, so adding one on the other protocol would not
  catch it either.
- **The only reliable signal is the trace clock.** Poll
  `max(start_time)` on `trace_runs`; quiet for >20 min with the process alive means
  wedged. Score and status are useless here — silence looks exactly like work.
- **Recovery is SIGKILL then `--resume <run>`**, which re-runs every non-`scored`
  arm and deletes its stale rows first. Same recipe as the run #8 proxy wedge.
- **Killing the process does NOT stop the run — set `cancel_requested` instead.**
  The `task_runs` row outlives the process, so any worker can pick the run up and
  resume it: run #121 was killed at the launcher, its row stayed `running`, and a
  `uvicorn --reload` dev server's worker resumed it at `.env`'s recursion limit
  (100) rather than the launcher's (300). `arena/task.py::_execute` now honours
  `cancel_requested` at the **arm boundary** — the arm in flight finishes and is
  recorded, then the loop stops and marks the run `failed` (`cancelled after N of
  M units`), never `completed`. A match cannot be interrupted mid-flight, so
  expect up to one arm of delay. **A run left non-terminal is what invites the
  silent resume**, so always leave it terminal.
- **A resume must RE-SUPPLY every process-level setting.** `reasoning_effort` is
  persisted on the run, but an output budget
  (`OPEN_OTC_AGENT_MAX_OUTPUT_TOKENS` / `..._OPENAI_...`) is env-only with no
  `arena_run` column, so a resume that omits it silently finishes the run at a
  DIFFERENT budget than it started with. Nothing in the stored data would reveal
  that — for a budget study it silently contaminates the independent variable.

---

## A per-check tally is only valid on a board where every arm is HEALTHY

The scoring-validity instrument (walk `objective.steps[].checks[]` +
`objective.success[]`, key by `label`, look for a spread) assumes every
contestant was actually asked every question. **A contaminated arm manufactures
false discrimination**: it fails checks the whole healthy field passes, turning
saturated checks into apparent 3/4 discriminators. On the first
`confirmation-desk-day` board the tally read **8 of 33 checks dead**; repairing
the one infra-killed arm moved it to **25 of 33** — the workflow was three times
more saturated than the tally claimed. This compounds the merged-run rule
(exclude merges, because they double-count) with a second precondition: **check
arm health before computing the tally**, and recompute it after any arm is
re-run.

---

## Malformed tool calls: a 200 that dispatches nothing

A provider can return **HTTP 200** with a well-formed message whose tool calls carry
an **empty `id` and empty `name`**. deepagents' `task()` guards on exactly that, so
the call never runs; the agent re-issues it, loops to the recursion limit, and the
transcript ends up blank. Measured on run #122 (2026-08-24): `deepseek-v4-flash` at
effort `max` over chat-completions emitted **95 tool calls, 100% malformed**,
executed **zero**, and scored **7.7**. The same model, same effort, same workflow on
the **OpenAI Responses API** emitted 132 calls with **zero** malformed and scored
**91.1**.

- **It is invisible to every other gate.** Nothing raises, so no step records an
  error, so `_is_infra_blank` (which corroborates blankness with step *errors*)
  cannot see it and the match is recorded `scored`. Nothing truncates, so the
  truncation flag reads a clean zero. And **7.7 is not zero** — it is the
  prohibition floor, which inaction earns by satisfying every `tool_not_called`
  check, so the score looks like a real if terrible result. **When a match is blank
  with no errors, check the malformed count before concluding the model declined to
  act.**
- **Same three absence rules as truncation**, and for the same reasons: never
  appended to a step's `errors` (that list feeds the infra-blank gate — putting it
  there would BE the invalidation this design rejects); `truncation`-style
  `null` = never measured, which is NOT `calls: 0`; and carried at the breakdown's
  **top level**, because `fold_trial_breakdowns` does not lift `diagnosis`.
- **The score is NEVER adjusted.** Whether the lost calls were scoring-critical is
  not something the harness can know, and sweeping such matches to `invalid` would
  silently shrink any board containing one.
- **`arg_keys` is stored, argument bodies are not** — it identifies WHICH call shape
  the route mangles. Run #122's were 84-of-95 `task()` persona delegations, i.e. the
  biggest-payload call, echoing the output-budget rule that the largest emission dies
  first.
- **Suspect the ROUTE before the model.** Five other contestants on the same gateway
  in the same window emitted 1,283 tool calls with zero malformed. This is the same
  defect class that forced `protocol: anthropic` onto glm-5.2 and minimax-m3; the
  difference is that it is now measured instead of discovered by accident.
- **ROOT CAUSE (2026-08-25): a ZenMux UPSTREAM-PROVIDER lottery, not a protocol and
  not an effort.** ZenMux serves one model id from several upstreams (five prices are
  listed for `deepseek/deepseek-v4-flash`) and picks per request. Pin one with a
  `:provider` suffix. Measured on the streaming wire, one `task` call each:
  `deepseek/deepseek-v4-flash:deepseek` sends `id`/`name` as **`null`** in
  continuation deltas (correct); **`:alibaba` sends them as empty strings**.
  langchain merges a present-but-empty string over the real identifiers from the
  first delta, while `arguments` fragments concatenate fine — intact args, hollow
  ids. That is the whole defect.
  - **The response body names NO provider**, so an unpinned id is a lottery you
    cannot even attribute after the fact, and there is no provider-enumeration
    endpoint (302/404). **Pin every contestant's provider or the contestant is not
    reproducible.** This is not a hypothetical: run #112 scored 93.6 unpinned on
    2026-08-17 and runs #121/#122/#125 scored 7.7 from 2026-08-21, with no code
    change anywhere — the lottery simply started landing on `:alibaba`.
  - **Effort is irrelevant** — the empty strings appear at `none` through `max`, and
    run #125 reproduced the failure at the vendor default. An earlier reading of this
    as an "effort × protocol interaction" was wrong.
  - **A DISPATCH id contains `:`.** `_parse_model_selection`'s `split(":", 2)`
    is load-bearing; a plain `split(":")` rejects every pinned id. The effort
    snapshot keys on `(channel, model_id)`, so a pinned route is a DIFFERENT key
    from its unpinned twin and needs its own probe — read it through
    `ModelDescriptor.wire_id`, never `.id`.
  - **A pin changes the regime under a stable slug.** Arena matches recorded before
    the pin ran on either upstream; they are not comparable to pinned ones. The
    pinned DeepSeek route therefore ranks as its OWN contestant
    (`deepseek-v4-flash-ds`), because its old slug holds both 93.6 and 7.7.
  - **EVERY zenmux model declares its upstream as of 2026-08-25** — in
    `agent_channels.yaml`, the tracked `.example.yml`, and `CANDIDATE_MODELS`.
    **Policy: pin the model owner's own infrastructure.** Discover the options by
    scraping `zenmux.ai/<model>` for `<id>:<provider>`; there is no enumeration
    endpoint, and `owned_by` is NOT the slug for google (`google-vertex`), z-ai
    (`bigmodel`), bytedance (`volcengine`), tencent (`tencent-cloud`) or meituan
    (`longcat`). Exposure was worst where nobody looked: `deepseek-v4-pro` and
    `glm-5.2` had SIX upstreams each. The upstream is the YAML's `provider:`
    field — see *The three axes of a model entry* below; the `:suffix` spelling
    it replaced still loads.
  - **Renaming an id can dangle the `default:`** — the tracked template pointed at
    `anthropic/claude-sonnet-4.6` and had to be repointed with it.
  - **The effort snapshot keys on `(channel, model_id)`, so every pin was a new
    key** and would have silently gone permissive for the whole field.
    `effort_support` now falls back to the unpinned entry when a pinned one has not
    been probed — measured evidence about the same model beats "unknown" — while a
    probed pinned entry still wins.
- **A single-shot probe does NOT reproduce it** — deepseek is 4/4 clean at `max` with
  one tool bound and a short prompt. It appears under real agentic load (~15k prompt
  tokens, many tools). Only a real match convicts, the same lesson longcat-2.0 taught.

- **An UNPARSEABLE call is a second, louder variant (run #139, 2026-09-24).** The
  xiaomi upstream returned `finish_reason: tool_calls` with one call whose id and
  name were `null` and whose args were `<parameter=...>` markup. langchain parks
  that in `invalid_tool_calls` — `tool_calls` stays EMPTY, so the detector above
  read zero — and then REPLAYED it on every later request, which every gateway
  rejects (400 "`id` is null"). Unlike the empty-id loop this one raises, but only
  as step errors deep in the match, which was recorded `scored` at 41.3. Fixed at
  the client (`model_factory._neutralize_invalid_tool_calls`, all four client
  classes) and counted by the detector as `reason: "unparseable"`. **A replay
  failure on one route reproduces on every route** — gpt-6-luna 400s on the same
  history — so suspect the HARNESS when the rejected turn is one the model never
  had executed.

---

## A contestant is `(model_id, reasoning_effort, max_output_tokens)`

One board can rank the same model at several efforts (migration **0058**) and at
several output budgets (migration **0059**). Each widening made the previous
unique key let the new arm collide with the first, so these are identity changes,
not UI ones.

**Budget earns its place by measurement, not analogy:** runs #118 (4096) and #119
(32768) produced an artifact in **0/8** vs **7/8** trials and differ by **16.4
mean objective** — on the budget alone. Everything the effort key does, the
budget key does identically: `''`/`0` sentinels rather than NULL, per-model arm
LISTS, omitted-when-unset on the selection dict, part of the `merge_runs` fold
key, part of `--resume`'s todo set and stale-row cleanup, part of the transcript
directory, and part of the React `rowKey`.

- **Arms are the CROSS PRODUCT of the two axes.** `task.arms_for` is the single
  definition, read by the execution loop, the progress total AND the launch-time
  count — so a run cannot execute a different number of contestants than it
  counted. A model at two efforts and two budgets is **four** contestants;
  counting models leaves the progress bar permanently short, reading as stuck.
- **A resume re-supplies the budget FROM THE RUN.** This is the hole 0059 closed:
  the budget was env-only (`OPEN_OTC_AGENT_MAX_OUTPUT_TOKENS`) with no column, so
  a resume that omitted the env var silently finished the run at a DIFFERENT
  budget than it started with — and nothing in the stored data would reveal it.
- **A pinned budget applies to BOTH wire protocols.** An anthropic-only budget arm
  would leave every OpenAI-protocol contestant at its provider default under both
  arms and report a null result as if the budget had been varied.
- **Migration 0058 had to be taught about being superseded.** `0001_initial`
  materialises today's ORM, so a fresh DB arrives at revision 1 already carrying
  0059's 5-column key; 0058 looked for its own 4-column *name*, found it absent,
  and added it alongside — and the 4-column key **forbids the second budget arm**.
  It now no-ops when a constraint already extends its columns, matched on
  **COLUMNS not names** (the pre-0058 table's constraint is unnamed, so a
  name-based test reads it as "superseded" and skips the real conversion).
  `test_migration_fresh_chain` cannot catch this class — adding a redundant
  constraint succeeds — so the guards are 0058's and 0059's idempotency tests.
- **SQLite reflection only recovers a constraint NAME from a single-line
  `CONSTRAINT <name> UNIQUE (...)`.** A test fixture that splits it across two
  lines reflects the constraint as unnamed, so `drop_constraint` by name matches
  nothing and the rebuild silently leaves the old key behind — the fixture passes
  while proving nothing. Both 0058's and 0059's fixtures declare it on one line,
  matching what alembic emits and what the live DB contains.

- **`ArenaMatch.reasoning_effort` is `''` for unpinned, never NULL.** SQL treats
  NULLs as DISTINCT in a UNIQUE constraint, so a nullable column would silently
  stop protecting unpinned pairs at the DB level while the Python-level upsert
  still dedups — a backstop that looks present and is not. `None` is used at every
  dict/API boundary; `''` only in the column.
- **`arena_run.reasoning_efforts` values are LISTS of arms** (`{slug: [null,
  "high"]}`); `null` is the explicit unpinned arm, and a model absent from the map
  runs once at its vendor default. Legacy scalar values are read as one-element
  lists by `task.effort_levels_for` and normalised at `store._run_to_dict` —
  derive on read, never migrate the JSON. A duplicate level within one model's
  list is **rejected at launch**: it would mint two contestants sharing one key.
- **0058's backfill reads each row's OWN `config.reasoning_effort`**, never a blind
  `''`. Flattening historical rows would assert that a `high` board and a `low`
  board were the same regime, and `merge_runs` — which now groups on the column —
  would fold them. (On this DB all 347 historical rows were unpinned, so the
  backfill was precautionary; it becomes load-bearing the first pinned board.)
- **Anything keyed on `(workflow, model)` is now arm-blind and wrong.** Four sites
  needed the third key: `store.record_match`'s upsert (the second arm silently
  clobbered the first), `_save_transcript`'s directory (both arms wrote one
  `transcript.json` — the same clobber the per-trial copies fixed), and
  `launch_arena_run.py --resume`'s todo set **and its stale-row cleanup** (a pair
  read as done when one arm scored, then the run marked `completed` — run #104's
  failure shape; and the cleanup would delete the other arm's row). **`scorecard.py`'s
  `model_id → transcript path` map is still arm-blind** and picks one arm
  arbitrarily — known gap, not part of scoring.
- **Progress totals count ARMS, not models** (`queue_arena_run` and `_execute`). The
  old `workflows × models × trials` product under-counts once any model carries two
  arms, leaving a finished run's progress bar permanently short — it reads as stuck.
- **`get_leaderboard` has NO `response_model`** — it hand-builds an explicit key
  projection, so a field the store gains is served only if named there. Different
  mechanism from `/api/agent/models`' pydantic drop, identical failure: the store
  unit test passes while the API serves nothing. Assert new board fields in
  `test_arena_api.py`, at the HTTP layer. `RunSummary` is the opposite case — it is
  *constructed explicitly*, so a shape change there fails loudly with a
  `ValidationError` rather than silently.
- **The board's `rowKey` must include the effort.** Two arms of one model are
  duplicate React keys otherwise.
- **`fold_trial_breakdowns` does NOT lift `diagnosis`.** It lifts `objective`,
  `objective_score`/`stdev`, `total_score` and `subjective_mode`; `diagnosis`
  stays inside each entry of `aggregate`. Any reader that goes straight to
  `score_breakdown.diagnosis` therefore sees nothing for a wrapped match — which
  silently blanked the drilldown panel and the match-cell snippet for **222 of
  297 stored matches**. So **"`trials=1` is behavior-preserving" holds for the
  ability card, not for the diagnosis**: with `n_trials: 1` there is also no trial
  tab (tabs need `length > 1`), so the data is unreachable rather than merely
  moved. Read it through `displayDiagnosis` — one trial ⇒ that trial's, more than
  one ⇒ none, because averaging counts across trials would report a run that never
  happened (the spread is what CON measures); the Average tab lists each trial's
  counts instead.
- **Every surface that shows a contestant must show its effort**, or two arms are
  indistinguishable: same model, workflow, status and radar. The leaderboard's
  model column had a `Badge`; the run drilldown's match cells did not, so run #109
  showed Grok 4.6 twice (OVR 75 and 74) with no way to tell `low` from `high`.
  A pinned arm gets the badge; an unpinned one gets none — `null` is an absence,
  not a level.
- **A THIRD way a field vanishes between store and screen: the TypeScript type.**
  `/api/arena/runs/{id}` had always sent `reasoning_effort` per match, but
  `ArenaMatchSummary` did not declare it (only `ArenaLeaderboardRow` did), so the
  UI could not consume what was already on the wire. This is the same failure as
  `/api/agent/models`' pydantic drop and `get_leaderboard`'s hand-built key
  projection, at a different layer — and the loudest of the three, since `tsc`
  reports it the moment anything reads the field. When a value is in the DB and
  in the response but not on screen, check the client type before the server.

## Cost metering (2026-09-25)

Each match's `score_breakdown.usage` holds token totals and a **list-price** USD
estimate (`cost.py`), priced at score time against `config/model_pricing.json`
and stamped with that snapshot's `fetched_at`/`sha256`. A price refresh never
re-costs a finished match. The rules:

- `usage: null` = NOT METERED (before 2026-09-25, or a replay) — never "free".
- `usd: null` with `usd_low < usd_high` = the list is ambiguous for that model
  (deepseek-v4.1-flash lists 0.075 and 0.15 with no condition). Report the range;
  do not pick one.
- `unpriced_calls > 0` = a model missing from the snapshot; the dollar figures
  are then a floor. Refresh the snapshot (`scripts/refresh_model_pricing.py`).
- `input` includes cached tokens and `output` includes reasoning, as the
  providers report them. Cache reads dominate agentic matches (~86% of a luna
  risk-manager match) — pricing them at the prompt rate would overstate cost ~5×.
- The BILLED amount is per generation id (`steps[].usage[].generation_id` in the
  transcript). `scripts/arena_cost_report.py --run N --billed` sums it with
  `ZENMUX_MGT_KEY`. Only the `openai_chat` client (`_ThoughtSignatureChat`)
  records ids so far; Responses-API and Anthropic-protocol contestants show
  `generation_ids: 0`.

## One turn can be two root traces (escalation retry)

`transcript_from_trace` maps TURNS, not roots, onto steps. A denied tool call
raises `CapabilityDeniedError` and ends the first pass as an `error` root. When
the envelope table has a widening, `_apply_runtime_signals` re-drives the same
prompt as a second root. `_group_roots_into_turns` merges a root into the
previous turn only if that turn's last root errored AND the prompt is identical.
Before 2026-09-25 roots were mapped 1:1, which shifted every step after an
escalation (run #141 match 633: 64.1 → 97.4 on rescore). Thirteen historical
arena threads carry the pattern. To fix a stored row without re-running it,
use `scripts/rescore_arena_match.py --match ID --thread TID --reason …`
(`--dry-run` first).

## Reports are a purge namespace too (2026-09-25)

`report_jobs` has NO portfolio column (the portfolio is in `request_payload`), so
`_delete_portfolios_with_dependents` cannot reach a report, and
`_purge_seeded_reports` only knows `ARENA_REPORT_MARKER`. A contestant's
`create_report` (`report_job_id`) or `generate_report` (`report_id`) row is
handled by `_purge_match_reports(thread_id, report_id_baseline)` in the
`finally`, and by `_sweep_orphaned_match_reports()` before seeding. Five rows
had leaked (ids 1–4 from risk-manager-control, 8 from high-board). Because ids
are reused, report 8 posed as the fresh 9101 book's prior board report.
Adding a tool that inserts `ReportJob`? Add it to `_REPORT_CREATE_TOOLS`.

## Per-step event loops vs cached HTTP clients (2026-09-26)

`runner._drive_step` runs every step in its own `asyncio.run`. The LLM libraries
cache one `httpx.AsyncClient` per base URL **per process** (langchain-openai
`_cached_async_httpx_client`, langchain-anthropic `_get_default_async_httpx_client`).
Behind a proxy, which is how this desk reaches ZenMux, the next loop can draw the
last loop's idle connection and die with `RuntimeError: Event loop is closed`. It
dies on its first model call, so the step is blank. `_reset_async_http_pools()`
clears both caches before each step. Those are private names, and
`tests/test_arena_http_pool_reset.py` pins them, so a library rename fails a test
instead of silently bringing the failure back. That test only reproduces the
failure with proxy env vars set; a bare client quietly drops the dead connection.
**Any new code path that calls `asyncio.run` more than once per process and
makes LLM calls needs the same reset.** The phrase is also in
`_PROVIDER_ERROR_RE`, so a turn it kills is gated as infra.
