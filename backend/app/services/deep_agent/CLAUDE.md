# Deep agent runtime — agent guidance

The LangGraph agent stack: what it audits, what it may never paraphrase away, what it remembers across sessions, how it fans out, and what it refuses to read.

Part of [Open OTC Trading](../../../../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** Model routing, effort ladders and output budgets: [`config/CLAUDE.md`](../../../../config/CLAUDE.md). Measuring agent behaviour: [`golden_workflows/CLAUDE.md`](../../golden_workflows/CLAUDE.md).

---

## Audit trail (dangerous-action log)

An always-on, append-only record of every write-class action an LLM agent takes —
distinct from `/tracing` (which records every transcript detail, disableable).
Captures bookings, portfolio/RFQ writes, deletes, memory writes, async dispatches,
and file/artifact writes, **including actions taken in headless YOLO mode** — YOLO
only empties the HITL interrupt map, it never bypasses middleware, which is where
capture lives.

**Package:** `backend/app/services/deep_agent/write_actions.py` (shared write-class
taxonomy off `__capability_group__`, also consumed by `fanout_readonly.py`),
`audit_redaction.py` (secret-key masking + content elision to sha256/len/head),
`services/audit_trail.py` (recorder), `deep_agent/audit_trail_middleware.py`
(`AuditTrailMiddleware`). Model: `AgentActionAudit` (`models.py`). Migration `0043`.
Read-only API: `backend/app/routers/audit.py` (`/api/audit`). Frontend:
`frontend/src/routes/Audit.{tsx,live.tsx,css}` (the **Audit** nav page).

### Capture points

`AuditTrailMiddleware` sits at the `wrap_tool_call` seam, just inside
`ToolErrorBoundaryMiddleware`, in **all three** agent stacks — orchestrator
(`_agent_middleware`), per-persona (`all_personas`), and `build_async_agent`.
`tests/test_audit_registration.py` asserts middleware presence in all three so a new
stack can't silently skip it.

- **Fail-closed phase-1.** An `attempted` row must commit *before* the tool executes
  (bounded retry 0.1/0.3/0.9s); if it can't, the write is refused with a ToolMessage
  rather than executed unaudited. Phase-2 (`ok`/`denied`/`error`/`interrupted`) is a
  best-effort outcome update by in-memory PK — on failure the row honestly stays
  `attempted` rather than lying about the outcome.
- **HITL chains.** `hitl_proposal` → `hitl_decision` → `execution` rows are linked by
  a server-minted `audit_ref` (UUID), minted unconditionally inside
  `_source_meta_for_action` so it survives async resume paths. Decision rows are
  recorded at the resume boundary **before** the graph invocation, in their own
  transaction, so a failed approved-write can't erase the human approval.
- **Redaction.** Secret-key regex masking
  (`token|password|secret|api[_-]?key|credential|authorization`) plus content-body
  elision (`write_file`/`edit_file`/`run_python`/`execute` bodies →
  `{sha256, byte_len, head}`) before any row is persisted.

### Gotchas

- List sort key is `occurred_at`, not `id` — insertion order only approximates
  chronological order for organic same-process writes; a backfilled or
  out-of-order-committed row breaks that assumption.
- The Audit page pager reuses the shared `TableToolbar` / `DataTablePage`
  primitives (rows-range label + rows-per-page select + prev/next) — same control
  as Positions/Portfolios/Reports/Tasks, not a bespoke "Load more" button.
- `fail_closed_refusals.unpersisted` (surfaced in `/api/audit/summary`) is an
  in-memory counter — it resets per process.
- The System One tool guard stores its verdicts beside this trail, in
  `agent_tool_guard_verdicts`, joined by `(thread_id, tool_call_id)` — see
  [`services/system_one/CLAUDE.md`](../system_one/CLAUDE.md). Its resume
  determinism depends on every resume path stamping `mode` + `thread_id`.

---

## Ground-truth artifacts and compaction

Long-agent compaction may shorten reasoning, but it must never paraphrase away a
deterministic tool or DB result. `GroundTruthArtifactMiddleware`
(`services/deep_agent/ground_truth.py`) classifies evidence from server-owned
`ToolGroup` metadata and captures every such result, including small results, through
`ContentAddressedFilesystemBackend.capture_tool_result` before compaction. The CAS
stores exact bytes; `session_artifacts` stores the id, SHA-256, input hash, tool call,
and `generated_at` / `observed_at` / `data_as_of`. If capture fails, the raw message is
kept and compaction refuses to evict it.

`services/deep_agent/compaction.py` gives the summariser artifact-reference capsules,
not raw prices/Greeks/rows, then appends a server-rendered exact manifest. Never treat
the narrative summary as executable evidence. Recover canonical content through the
workflow-scoped `list_artifacts` → `inspect_artifact` → targeted `read_artifact`
sequence (`artifact_access.py`, `tools/artifacts.py`). This is deliberate progressive
disclosure: no embeddings, vector search, semantic chunking, or RAG ranking. Do not add
one to this evidence path. `read_artifact` reuses the source artifact reference (it does
not mint an artifact-of-an-artifact), so a later compaction removes the disclosed body
back to the original id/hash.

Exact `kind='tool_result'` artifacts are load-bearing CAS entries even when a later
artifact supersedes them; the normal GC sweep never evicts their bytes.

Hedging adds an action-time gate. `OPEN_OTC_HEDGE_RISK_MAX_AGE_SECONDS` defaults to
900. `book_hedge`'s HITL payload must show the source artifact id, artifact generation
time, Greeks `valuation_as_of`, `risk_generated_at`, and `expires_at`; execution also
revalidates the latest risk run, portfolio fingerprint, exact proposal fields, and
solver legs. `stale_hedge_proposal` means refresh risk and re-solve, never retry the
old approval.

### A/B evidence standard for agent-behaviour features

Do not claim that a new agent strategy outperforms its predecessor from regression tests
alone. Add a paired benchmark with the old production path as arm A and the candidate as
arm B. Both arms must receive the same input hash and model/configuration; predeclare the
scorer and pass criteria; retain per-case raw results; report regressions and costs as well
as wins; and avoid an LLM-only judge when deterministic state or trace checks exist.

The compaction reference implementation is
`services/deep_agent/compaction_benchmark.py`, exposed by
`scripts/compaction_ab_benchmark.py`. Its offline controlled-model mode is the cheap PR
gate for structural guarantees. A live-model probe may supplement that gate for prose or
planning quality, but may not replace the deterministic proof. Extend its case set when a
later compaction or hedge-evidence change adds a new claimed advantage or failure mode.

The same runner's `--live` mode is the standard model-behaviour supplement. Use the same
channel/provider/model, temperature, limits, and input hash for both arms; alternate arm
order; give each arm its production recovery path; persist full prompts/responses plus
call and evidence timestamps; and grade with deterministic domain rules. Run from a clean
`uv sync --locked` environment. The evidence must include the `uv.lock` hash, complete
installed-distribution fingerprint, and a passing installed-versus-locked version check;
dependency drift invalidates the benchmark. Never treat a live provider response alone
as proof without the paired raw artifact and scorer output.

---

## Long-term memory

A DeerFlow-inspired cross-session memory layer for the deep agent. It distills
durable facts from closed sessions and injects the relevant ones into later
conversations, so the desk "remembers" preferences, per-book context, and
corrections across threads.

**Package:** `backend/app/services/deep_agent/memory/` — `config`, `normalize`,
`safety`, `scope`, `store`, `extractor`, `runs`, `inject`, `queue`, `middleware`,
`runtime`, `window`. REST API: `backend/app/routers/memory.py` (`/api/memory`).
Frontend console: `frontend/src/routes/Memory.{tsx,live.tsx,css}` (the **Memory**
nav page). Migrations: `0038` (evolve `memory_entries` into typed columns) + `0039`.

### Scopes & lifecycle

Four scopes, resolved via a constant-`desk` identity seam (no real multi-user yet):

- `user:desk` — desk-wide operator facts and preferences.
- `book:{portfolio_id}` — per-portfolio context (`scope_id` is the **stringified
  portfolio integer id**, e.g. `"1"`).
- `domain:global` — shared knowledge, staged **propose → approve**: only `approved`
  facts inject; new domain facts land as `proposed` and must be approved first.
- `correction:desk` — facts learned from user corrections (`source_error=True`),
  with their own injection sub-budget.

### Store invariants (`store.py`)

- `MemoryStore.create` forces domain facts to `proposed`; everything else is
  `active`. **`api`-created non-domain facts are auto-pinned.**
- `pinned` is an eviction-protection flag (human/approved facts survive cap
  eviction and the extractor) — it is **not** an edit gate.
- `archived` is read-only: `update` / `set_status` / `set_pinned` raise
  `MemoryConflictError` (→ HTTP 409). `archive()` is idempotent.
- Hygiene: normalized exact-match dedup, confidence floor `0.7`, caps `100`/scope
  and `20` for corrections, content-safety denylist (see `config.DEFAULT_DENYLIST`).
  No embeddings.

### Writes are off the hot path

The agent turn only enqueues **in-memory** — no synchronous SQLite write. Durability
comes from a **reconciliation sweep** over `AgentSession` close status (session
close + a correction fast-path on `after_model`). `apply_diff` and the run-success
cursor commit in **one transaction** (idempotent re-run). Memory is a *diff*
(add/remove), not an append.

### Extractor model resolution

The extraction LLM is chosen by **registry tag**, two-tier so a missing tag
degrades to a cheap model rather than the expensive agent default
(`resolve_extractor_selection`): `extractor_model` (dedicated tag — tag exactly
**one** model with `extractor` in `agent_channels.yaml`) → `extractor_fallback_tag`
(`fast`) → registry default. Pinned extractor in this repo:
`deepseek/deepseek-v4-flash` on the **zenmux** channel.

### Configuration

| Env var | Effect |
|---|---|
| `OPEN_OTC_MEMORY` | `on` (default) / `off` — master capture switch. Even when `off`, existing facts stay editable via the API/console. |
| `OPEN_OTC_MEMORY_RECONCILE_SINCE` | ISO-8601 instant. The sweep only **discovers** sessions closed at/after it. Set when first enabling memory on an existing DB so it doesn't mass-extract the whole backlog. Malformed → fails open (no cutoff) with a warning. |
| `OPEN_OTC_MEMORY_KEEP_ALIVE` | `on` (default) / `off` — System One keep-alive score (display-only). Needs `OPEN_OTC_SYSTEM_ONE=true` and `OPEN_OTC_MEMORY` on. |

Defaults live in `MemoryConfig` (`config.py`): floor `0.7`, caps `100`/`20`,
injection budgets `2000`/`1000` tokens.

### Gotchas

- **Seeding via the API** mirrors all store policy server-side: POST a `domain`
  fact and it still lands `proposed`; POST a `book`/`user` fact and it lands
  `active` + `pinned`. Store only **durable** facts — verify volatile-looking
  values (live position counts, "latest run #N") against the DB first; they go
  stale.
- The reconciliation sweep keys off `AgentSession.closed_at`; sessions with a NULL
  `closed_at` are never swept (so historical threads may need manual seeding).
- **Memory page table layout:** the shared `Table` primitive renders each row as
  an *independent* CSS grid, so only `fr` and fixed lengths align across rows —
  `max-content`/`auto` tracks resolve per-row and break column alignment. Columns
  that must not clip (status, conf, the action buttons) use fixed widths; the rest
  use `minmax(0, fr)`. Row-action buttons are height-constrained to `--row-height`.

---

## Dynamic subagents (governed QuickJS fan-out)

An opt-in execution substrate for **recurring desk workflows that fan out per work
item** — e.g. one read-only commentary per breached position. The orchestrator writes a
QuickJS script that calls the deepagents `task()` global (via `CodeInterpreterMiddleware`,
`subagents=True` — **not** `ptc=["task"]`, which raises at model-call time) to dispatch one
persona subagent per item, then reconciles the results deterministically. **Gated off by
default** (`OPEN_OTC_AGENT_CODE_INTERPRETER=false`).

**Package:** `backend/app/services/deep_agent/` — `dynamic_subagents.py` (allowlist +
attribution helpers + `reconcile_fanout_coverage`), `eval_gate.py`
(`EvalAttributionGateMiddleware`), `fanout_readonly.py` (`FanoutReadOnlyMiddleware`). Tool:
`backend/app/tools/assemble_breach_report.py`; scope: `services/risk_limits.py`. Seed
workflow `morning-risk-breach-commentary`; migrations `0040` (seed) + `0041` (finalize
prompt).

### Governance (all server-owned — the model can never self-authorize)

- **Eval gate.** Enabling the code interpreter exposes a general `eval` tool;
  `EvalAttributionGateMiddleware` rejects **every** `eval` unless `configurable` carries
  server-stamped Case-3 attribution (`fanout_attribution_extra`) for an **allowlisted**
  (`DYNAMIC_SUBAGENTS_ALLOWLIST`) workflow persisted with `source='seed'`. Attribution is
  threaded via `main.py::_desk_workflow_drive_factory` →
  `stream_and_persist(desk_workflow_slug/source/launch_args)` — never from model/tool input.
- **Read-only fan-out.** `FanoutReadOnlyMiddleware` blocks writes inside fanned-out
  subagents (`ls_agent_type=='subagent'` + Case-3 attr). Classification is **by capability
  group** (`__capability_group__`): block `DOMAIN_WRITE`/`PAGE_ACTION`/`ASYNC_DISPATCH` +
  deepagents FS/shell writes (`write_file`/`edit_file`/`execute`) +
  `run_python(writes_artifacts=True)`; **allow everything else**. This is
  **allow-by-default on purpose** — deny-by-default against the HITL write-map blocks the
  reads the investigator needs (incl. ungated tools like `get_position_summaries`). Why
  writes matter: resume/retry re-runs the whole `eval` and **re-dispatches every subagent**,
  so a write would repeat non-idempotently.
- **Server-authoritative coverage.** `assemble_breach_report` re-derives scope server-side
  from the launch `portfolio_id` (`configurable['fanout_launch_args']`, not the model arg)
  via `enumerate_limit_breaches`, then `reconcile_fanout_coverage` guarantees exactly one
  terminal record per scoped item (uncovered → `failed`). The model can't shrink coverage
  by omitting ids.

### Gotchas

- **A tool the model must call has to be in `DEEP_AGENT_TOOL_NAMES`** (the allowlist
  `select_deep_agent_tools()` filters `QUANT_AGENT_TOOLS` by), not merely registered in
  `QUANT_AGENT_TOOLS` — otherwise it is silently dropped from every persona's toolset.
  `assemble_breach_report` hit exactly this: registered but not allowlisted, so the model
  finalized via `write_report_artifact` instead. **When a model *never* calls a specific
  tool across models and prompts, suspect availability before capability.**
- **Stronger models loop more:** `deepseek-v4-pro` blows past the default
  `agent_recursion_limit` of 100 on the fan-out — raise it (≥300) for pro-tier runs.
- **Subagent stream events don't surface as SSE frames:** deepagents `task()` runs each
  subagent via a nested `subagent.invoke()`, so its internal events never reach the parent
  `astream_events` — inside-fan-out observability is a library limitation (follow-up).
- **The `metrics['limit_breaches']` producer is not built yet:** `enumerate_limit_breaches`
  returns `[]` (honest-empty) until a risk producer populates that key; real runs surface
  no breaches until then.
- Live smokes on the **direct** DeepSeek channel: `api.deepseek.com` exposes both
  `deepseek-v4-flash` and `deepseek-v4-pro` (the registry only declares flash).

---

## A binary `read_file` is a 400 the history can never recover from

deepagents' `read_file` returns any file it deems binary as a langchain v1 media
content block — `{"type": "file", "base64": …, "mime_type": …}`. `langchain_openai`
translates that **correctly** into the documented OpenAI wire shape
(`file.file_data`); verified against the exact `ToolMessage` recorded in the trace,
so the client is not at fault. The **gateways** are. Measured 2026-08-29 on ZenMux
with a real confirmation PDF, sending the identical part inside a **tool** message:
`glm-5.3-flash` accepts it; `gemini-3.7-flash`, `gpt-5.6-luna` and
`deepseek-v4-flash-vision` all 400, each in its own dialect (`required oneof field
'data'` / `Missing required parameter: 'input[N].output[0].text'` / `file must have
a file_id or file_data`). The same PDF in a **user** message is fine on all but
deepseek — so the defect is specifically *a binary returned from a tool*, which is
the only shape `read_file` can produce.

- **It is unrecoverable, which is what makes it different from a flaky call.** The
  rejected message stays in the history, so every later turn re-sends it and draws
  the same 400. On the first `confirmation-desk-day` board,
  `deepseek-v4-flash-vision` read one PDF during step 4 and then made **zero tool
  calls for steps 5–9**. It reads exactly like a model that gave up; it is a model
  that was never asked again.
- **Blast radius is set by WHICH history got poisoned.** `gemini-3.7-flash` hit the
  identical defect and survived, because its reads happened inside `task()`
  subagents whose checkpoint namespaces are discarded. Counting error spans by
  chain name: gemini had `trader` 3, `general-purpose` 1, **zero**
  `otc_desk_orchestrator`; deepseek had **six** `otc_desk_orchestrator`, which is
  terminal. **The span name tells you whether a run is wounded or dead.**
- **Invisible to every gate we have.** Nothing truncates, so the truncation flag
  reads a clean zero. The `read_file` call itself is `status=success`, so
  `_is_infra_blank` — which corroborates blankness with step *errors* — sees a
  healthy step and the match is recorded `scored`. Only the provider span carries
  the 400. **When a model stops calling tools, check whether it was still being
  asked** before concluding it declined to act.
- **There is a FOURTH agent stack, and "all three stacks" never covered it.**
  `create_deep_agent` auto-adds a `general-purpose` subagent whose middleware it
  builds internally — `[TodoList, Filesystem, summarization, PatchToolCalls]` —
  so **nothing passed as `middleware=` reaches it**, while it inherits the
  parent's full toolset. It was therefore running unguarded *and* **unaudited**,
  contradicting the audit trail's always-on contract; on this board it issued
  three `read_file` calls, one of them a PDF. Fixed by claiming the name
  (`orchestrator._general_purpose_subagent`), which is deepagents' documented
  override. **Behaviour-preserving by construction, and only if you OMIT `tools`
  and `interrupt_on`**: deepagents resolves a caller spec with
  `spec.get("interrupt_on", interrupt_on)` and
  `spec.get("tools") if "tools" in spec else tools`, and PREPENDS the same base
  middleware stack — so omitting both inherits exactly what the auto-added agent
  got, including the filesystem-permission interrupt merge. Declaring
  `interrupt_on` there would silently narrow write gating on a subagent that can
  book. **When you add a middleware "to every stack", check the subagents the
  framework adds for you, not just the ones you construct.**
- **The guard is `BinaryReadGuardMiddleware`** (`deep_agent/binary_read_guard.py`),
  at the `wrap_tool_call` seam beside audit and booking capture — the only seam
  that sees a subagent's tool calls — and registered in all three hand-built
  stacks plus the general-purpose override
  (`tests/test_binary_read_guard.py` pins that, mirroring `test_audit_registration.py`).
  It replaces a media block with text naming the tool to use instead
  (`parse_trade_confirmation` for confirmations, the artifact tools otherwise).
- **Uniform, deliberately not per-route.** Letting the one tolerant gateway through
  would hand that contestant an advantage conferred by its gateway rather than its
  ability — the confound the arena exists to remove. It also costs nothing: this
  desk never reads documents by pushing bytes into the prompt, and image parts
  (what `parse_trade_confirmation` actually sends) are accepted on every route
  measured, deepseek included.
- **The harness created the hazard.** The workflow names
  `/artifacts/uploads/confirmations/`, and `read_file` is available and reads it.
  Two of four contestants took that reasonable path and were punished; two never
  tried. **A behavioural spread caused by a harness hazard is not a capability
  signal** — same rule as the dangling-artifact fixture and the unaddressable
  upload paths.
- **It inverted a board.** Pre-fix `deepseek-v4-flash-vision` scored 36.4 and
  placed last; post-fix, same model, same workflow, same effort, it scored
  **100.0** in 23 calls and placed first. **The `LC_AUTOGENERATED` filename
  warning from langchain's block translator is the tell**, and it appears in the
  run log at the moment the binary enters the history.


---

## deepagents 0.7 / langchain-core 1.6 (app 0.2.0, 2026-09-25)

The upgrade is all-or-nothing — the packages cross-pin — and it changed things no
test would notice. What to know before touching the stacks:

- **deepagents builds no `TodoListMiddleware` any more.** Every stack of ours appends
  it explicitly (orchestrator, personas, general-purpose, async); `write_todos`, the
  SSE todo panel and `desk_context._IGNORED_TOOLS` depend on it. On 0.6 the same code
  raises "duplicate middleware instances" — it only runs on 0.7.
- **`SkillsMiddleware._get_backend` is gone** (backend factories are rejected); read
  `self._backend`. `EnvelopeSkillsMiddleware` crashed every persona until fixed.
- **A built-in `delete` file tool** reaches every agent. It is in `FS_WRITE_TOOLS`
  (audit + Case-3 fan-out block); `ScopedArtifactsBackend` does not override it, so
  filesystem permissions are its only path guard.
- **No default prompts.** 0.7 emits no base agent prompt, no filesystem prompt and no
  `task` system prompt, and cut the `task` tool description 6,573 → 1,072 chars. We
  ADOPTED this rather than re-vendoring the old text: goal was the new harness, and
  runs are version-stamped, so the change is visible rather than silent. The persona
  list still reaches the model through the `task` tool's `{available_agents}`.
  Measure the delivered prompt before claiming parity — see the capture recipe in
  the 2026-09-25 session: fake chat model, `build_orchestrator`, read turn-1 system
  message.
- **A video `read_file` returns a `Command`**, not a `ToolMessage`, carrying image
  frames in a synthetic `HumanMessage`. `BinaryReadGuardMiddleware._guard_command`
  replaces it; a guard that only inspects `ToolMessage` misses it.
- **`SummarizationMiddleware.__init__` calls `model.with_retry()`**, so a test that
  builds `_agent_middleware(model=None)` dies at construction. Pass
  `GenericFakeChatModel(messages=iter([]))`.
- **ChatOpenAI auto-switches to the Responses API for bare `gpt-6*` model names with
  tools.** Our ids carry the vendor prefix (`openai/gpt-6-luna:openai`), so nothing
  switches today; a bare id would silently bypass `_ThoughtSignatureChat`'s
  chat-completions seams. The payload-based switch rules are unchanged from 1.2.

## Mode-assembled prompts (headless never asks)

A sentence that presumes a user who can answer belongs to a MODE, not to the
prompt. Three places assemble by mode:

- **Per-turn text** — `mode_prompts.py`: the execution-mode block and the context
  brief's "nothing in view" lines, keyed `interactive` / `auto` / `yolo`.
  `render_context_brief(context, mode=)` and `_orchestrator_user_prompt(mode=)`;
  legacy callers passing only `yolo_mode` get AUTO, never headless.
- **Orchestrator system prompt** — `<!-- MODE_SECTION:<name> -->` markers in
  `prompts/orchestrator.md`, filled by `assemble_mode_sections` from
  `prompts/modes/<name>.interactive.md` | `.headless.md`. A marker without its
  file raises. The Skills page edits `orchestrator.md` raw, so it shows the markers,
  not the section text.
- **Persona policies** — `personas._resolve_policy_fragments` swaps
  `reply-options-policy` for `headless-policy` and drops
  `_INTERACTIVE_ONLY_FRAGMENTS` (`cost-preview-policy`, `clarification-policy`).

Only the wording about ASKING varies by mode. Tool authorization is the HITL
interrupt map's job; the headless execution block inherits AUTO's wording
verbatim, and `test_mode_prompts.py` pins that. Adding a new "ask the user" sentence
anywhere the orchestrator or a persona reads it? Give it a headless variant —
`test_mode_prompts.py` fails if the headless clarification section says ASK.

## A subagent-returned state key needs a reducer (2026-09-26)

deepagents' `task` tool returns **every** key of the subagent's final state to the
parent as a `Command` update, except `_EXCLUDED_STATE_KEYS` and private
attributes. Two `task()` calls in one orchestrator step therefore write the same
key twice. A plain key is a `LastValue` channel, which takes one write per step,
so LangGraph raises `InvalidUpdateError` and the turn dies after both subagents
have finished. `desk_context` did exactly this (run #143 thread 1132) until it got
`Annotated[..., merge_scope]`. **A new middleware `state_schema` key that a
persona can carry needs a reducer, or must be private.**
`tests/test_parallel_task_state_merge.py` drives the real orchestrator through a
two-task fan-out.

## Compaction must never split an AI/tool pair (2026-09-26)

`LedgerScopedCompactionMiddleware._determine_cutoff_index` overrides deepagents'
pair-safe cutoff with the end of its own compactable batch, which stops at
`max_messages` or at a protected message. That end can fall right after an
`AIMessage` with `tool_calls`, which strands its results on the kept side. Most
routes accept an orphan `ToolMessage` silently. DeepSeek's upstream returns
`400 Messages with role 'tool' must be a response to a preceding message with
'tool_calls'`, and because the summary persists, every later turn in the thread
fails the same way. `_pair_safe_cutoff` only ever moves the cutoff **back**
(moving forward would summarize the protected result that stopped the batch).
**Any new cutoff logic must pass through it.** It is pinned by
`test_long_agent_compaction.py::test_cutoff_*`.
