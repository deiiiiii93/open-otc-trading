# LLM channels, models & routing — agent guidance

How a model entry is spelled, which reasoning efforts a route accepts, how much output it may emit, and the web console that rewrites this directory's YAML.

Part of [Open OTC Trading](../CLAUDE.md) — the root guide carries the repo-wide rules (migrations, test hermeticity, tool registration, HITL levels).

**See also.** The runtime that consumes these: [`backend/app/services/deep_agent/CLAUDE.md`](../backend/app/services/deep_agent/CLAUDE.md). Contestant identity on a board: [`backend/app/services/arena/CLAUDE.md`](../backend/app/services/arena/CLAUDE.md).

---

## Output budget: the Anthropic protocol had a hidden 4096 cap

`build_agent_model` now passes an explicit `max_tokens`
(`Settings.agent_max_output_tokens`, `OPEN_OTC_AGENT_MAX_OUTPUT_TOKENS`, default
**32768**) to `ChatAnthropic`. It previously passed none, so `langchain_anthropic`
applied `_FALLBACK_MAX_OUTPUT_TOKENS` = **4096** — the value it uses whenever it has
no profile for the model id, and it has none for **any** id routed through ZenMux,
`anthropic/claude-opus-4.8` included (the vendor prefix defeats its lookup). Every
Anthropic-protocol contestant in every board through #114 therefore ran capped,
while every OpenAI-protocol contestant ran at its provider default.

- **A truncated turn is invisible to every gate we have.** It emits a lone
  `reasoning` block — no text, no tool call — so the step produces nothing, yet the
  span is `status=success` because the HTTP call really did succeed.
  `_is_infra_blank` corroborates blankness with step **errors** and a truncation
  raises none, so the match is recorded `scored`. **`completion_tokens` exactly at
  the cap is the only tell, and it lives only in the trace DB's LLM spans** — not in
  the score, the diagnosis, or the transcript. This is one layer deeper than the 402
  deaths of Run #10: not a failed call, a successful one that ran out of room.
- **Truncation is not automatic invalidation, and a good score is not proof of its
  absence.** On run #114 `glm-5.3` truncated on 6–14% of calls per workflow (the
  OpenAI-protocol baseline: 0 of 349) yet still scored 100.0 and 90.9 on two of
  five, because the agent loop usually recovers next turn. It costs points only when
  the lost turn was scoring-critical — one severed `invalid_tool_call` on an
  artifact step took a whole synthesis axis to zero.
- **The `ChatOpenAI` branch deliberately sends no `max_tokens`.** Capping it would
  recreate the asymmetry in the other direction. The two protocols are kept level by
  giving one an explicit generous budget and the other none.
- **Probe, don't assume.** All nine Anthropic-protocol routes accept 32768 *and*
  65536; it is a setting rather than a constant so a future route with a smaller
  ceiling can be lowered without a code change.
- **`arena/channel.py` is DEAD CODE and a decoy.** Its `build_zenmux_chat` and the
  `_DEFAULT_CONFIG = {"temperature": 0, "max_tokens": 4096}` it reads are referenced
  only by `tests/test_arena_models.py`. Raising that constant changes nothing in
  production — it looks exactly like the cap you are hunting.
- **Comparability:** boards from run #115 on are not strictly like-for-like with
  #8–#114 for Anthropic-protocol models, which previously ran handicapped.
- **Truncation is now MEASURED and FLAGGED, never invalidated.** The instrument is
  `response_metadata` — `stop_reason: "max_tokens"` (Anthropic) or
  `finish_reason: "length"` (OpenAI); **both** are read, because a detector that
  knows only one goes blind the moment the other protocol's budget is pinned.
  `trace_harvest._llm_truncation` → `MatchStep.truncations` →
  `diagnose_heuristic` → `score_breakdown.truncation` → the leaderboard row,
  the match cell and the API.
  - **The score is NEVER adjusted.** Truncation costs points only when the lost
    turn was scoring-critical, which the harness cannot know — run #114 scored
    100.0 and 90.9 on two workflows that truncated. The flag is a caveat on the
    measurement, not a penalty, and marking such a match `invalid` would silently
    shrink historical boards (Run #20 loses four contestants).
  - **The flag is deliberately NOT appended to a step's `errors`.** That list is
    what `_is_infra_blank` reads; putting it there would BE the invalidation this
    design rejects.
  - **`truncation: null` means never measured, and is not `calls: 0`.** Every
    board through #114 ran capped and really did truncate; a confident zero for
    them would assert the opposite. Same `empty` vs `unavailable` discipline the
    report module enforces.
  - It is carried at the breakdown's **top level** because `fold_trial_breakdowns`
    does not lift `diagnosis` and every match is wrapped, single-trial included —
    the same trap that blanked the drilldown for 222 of 297 stored matches.
  - **`severed_tool_call` is the expensive case**: content blocks
    `['text', 'invalid_tool_call']` with `completion_tokens` exactly on the cap.
    The model HAD chosen its tool and the cap cut the JSON argument, so the call
    never ran — that is how one lost turn takes a whole axis with it.

---

## Gemini needs its thought signature echoed back, and ChatOpenAI drops it

Gemini 3 routes return an ENCRYPTED thought signature in `reasoning_details`
beside every tool call, and then **require it echoed back** on the assistant turn
that carries that call. Without it the request is refused:

```
400 invalid_params — Function call is missing a thought_signature in functionCall
parts. ... function call `default_api:task`, position 2.
```

`ChatOpenAI` cannot carry it, **by design**: its own docstring says it targets the
official OpenAI spec and that non-standard fields "(e.g. `reasoning_content`,
`reasoning_details`) are **not** extracted or preserved". So the signature is
dropped on the way in and absent on the way out, and the **second** model turn of
every tool loop dies. Turn one always succeeds, which is what makes the symptom so
misleading — arena run #134 failed on all six workflows with a healthy model.

- **It is not a model capability, and the tell is a control.** `gemini-3.7-flash`
  — four published boards on this exact route — fails **identically** once the
  field is stripped, and both models round-trip when it is preserved. So the
  enforcement is what changed, not the client. Past boards keep their numbers;
  they ran before enforcement. **A new gemini board could not have run at all.**
- **`_ThoughtSignatureChat` patches THREE seams**, because the field is lost at
  three places: `_create_chat_result` (non-streaming capture),
  `_convert_chunk_to_generation_chunk` (streaming capture) and
  `_get_request_payload` (re-attach). **The arena streams**, so the streaming
  capture is the seam that actually carries a board — and the easiest to leave out
  while a green non-streaming test says everything is fine.
- **Self-gating, so it is safe for the whole field.** Only a route that SENT
  `reasoning_details` gets it back, so a model that never emits one is untouched
  and no request grows a field its upstream did not originate. Verified live
  against `gpt-5.6-luna` and `deepseek-v4-pro`.
- Same shape as `DeepSeekReasoningChat` — when a gateway model needs a field
  LangChain does not model, that pair is the pattern.
- **The first probe was NOT MEASURED and looked green.** Echoing the raw assistant
  message back verbatim preserves the very field under test, so all six cases
  returned 200. A probe must strip what the client strips, and carry a known-good
  control, or it reports the harness's behaviour as the model's.
- The **anthropic-protocol route round-trips it natively** (ZenMux maps it to a
  `redacted_thinking` block) and was the tempting one-line fix. Rejected: changing
  gemini's protocol would confound every comparison against its own published
  boards.

## Reasoning effort: an unset knob is omitted, never sent as null

Effort is chosen in the composer (**Effort**, left of Mode) and per arena run
(`arena_run.reasoning_effort`, migration `0056`; `--reasoning-effort` on
`scripts/launch_arena_run.py`), and rides on the **model-selection dict** —
the one carrier already persisted into `AgentMessage.meta`, replayed by async
resume, and built by `arena_model_to_selection`.

- **Omitted-when-unset is load-bearing, not tidiness.**
  `_sync_agent_for_selection` / `_async_agent_for_selection` reuse the *prebuilt*
  orchestrator only when the resolved selection compares **equal** to
  `AgentService.default_model_selection`. A fourth key present on every turn — even
  as an explicit `None` — silently ends that reuse and rebuilds the graph per
  request. Conversely, a pinned selection is unequal to the default *by
  construction*, which correctly forces the per-turn build that applies the effort.
  `resolve_agent_model_selection` and `arena_model_to_selection` both omit it.
- **There is NO universal ladder, and models.dev is NOT trustworthy for it —
  MEASURE.** A live probe (`scripts/smoke_reasoning_efforts.py`, 214 calls,
  2026-08-14) found models.dev wrong for **every route that mattered**, and mostly
  **too narrow**, which is the dangerous direction: it claims `low/medium/high` for
  deepseek-v4-pro (really all 7), **toggle-only with no levels** for qwen3.7-plus and
  mimo-v2.5-pro (really all 7), **non-reasoning** for kimi-k2.7-code (really 6
  levels, and it *refuses* `none`), and `none…max` for the GPT-5.6 family which
  actually **rejects `minimal`**. Gating on it would have blocked working levels on
  **13 of 22** probed models.
  - **The gate is only as strong as the evidence:** `measured` → reject an
    off-ladder level; `models.dev` (unmeasured) or unknown → **allow**, the provider
    decides. That is safe because the gateway refuses an unsupported level itself
    with a precise message (`"Unsupported value: 'minimal' is not supported with
    ..."`), so it is a reliable backstop — the "silently accepted and ignored" fear
    that justified local gating turned out not to hold for the reject case.
  - Re-measure with `smoke_reasoning_efforts.py --all --out p.json`, then
    `refresh_model_reasoning.py --merge-probe p.json`. The merge **refuses to mark a
    model measured while any level was inconclusive** (402/429/5xx/transport), since
    an incomplete accepted-set would become a hard rejection. Classify on the HTTP
    STATUS, not the error prose: `tencent/hy3` returns a bare 400 with an EMPTY body
    for `max`.
  - Notable measured facts: `openai/chat-latest` accepts **only `medium`**;
    grok-4.5/4.6 reject `none` and `max`; gemini-2.5-pro rejects `none`
    ("does not support thinking_budget 0"). `minimal` is rejected by every OpenAI
    model but accepted by most others.
  - Data: `config/model_reasoning.json`, refreshed by
    `scripts/refresh_model_reasoning.py` (`--check` fails when stale). Read through
    `services/deep_agent/reasoning_capabilities.py`. models.dev is the registry
    **OpenCode** uses and remains the fallback + the only source of the `toggle`
    flag; its `interleaved: {field: "reasoning_content"}` is literally the DeepSeek
    quirk `DeepSeekReasoningChat` hand-implements.
  - **Vendored, never fetched at runtime.** Same rule as the exact `quantark` pin
    and the harvested arena fixtures: third-party truth that governs behaviour
    arrives as a reviewable diff (with the upstream sha256 recorded), not as a
    silent change. A missing or corrupt snapshot degrades to permissive, never to a
    crash.
  - **The ladder is per ROUTE, not per model.** models.dev lists
    `deepseek-v4-pro` as `high,max` on the direct DeepSeek API but
    `low,medium,high` via ZenMux — so lookups key on `(channel, model_id)`.
  - **Unknown is PERMISSIVE, never "unsupported".** models.dev lags releases
    (`x-ai/grok-4.6` has no ZenMux entry), and stale data must not block a working
    model. Unknown models fall back to `VALID_REASONING_EFFORTS`, the outer bound.
  - `tests/test_reasoning_capabilities.py` guards that the outer bound is a
    superset of every effort in the snapshot — if upstream adds a token we do not
    know, that token is legal for some model yet rejected for every unknown one.
    Tests assert **structure and invariants, never a live model's current ladder**
    (that is a moving target); use `reload_snapshot(data)` to pin a fixture.
  - ZenMux's own API cannot answer this: `GET /api/v1/models` carries only
    `capabilities.reasoning: true|false`. And do NOT gate on the desk registry's
    `reasoning` tag — hand-maintained, only 13 of 32 models carry it, and
    `openai/gpt-5.5` (a reasoning model) does not.
  - Validation exists because an unrecognised value is *forwarded and ignored* by
    the provider, which reads as "effort had no effect" rather than "effort was
    never applied".
  - **`effort_rejection(registry, channel, provider, model, effort)` is the SINGLE
    seam** for "can this route carry this effort", used by
    `resolve_agent_model_selection`, `queue_arena_run` AND both UI ladder builders.
    `queue_arena_run` once had its own narrower check, so a board passed launch
    validation and then died per-match.
  - **The two protocols carry effort through DIFFERENT fields — neither blocks it.**
    OpenAI-compatible: top-level `reasoning_effort`. Anthropic: **`output_config.effort`**,
    a named level (`low/medium/high/xhigh/max`), which is what langchain's
    `ChatAnthropic.effort` shorthand writes; the older `thinking.budget_tokens`
    budget is being retired (`budget_tokens` is gone on Opus 4.7 — use
    `{"type": "adaptive"}` plus `output_config.effort`). We send `output_config`
    directly rather than the `effort=` shorthand, because that shorthand is typed
    to Claude's ladder while the gateway accepts `none`/`minimal` for the
    **non-Claude** models routed over this protocol.
  - **"Anthropic protocol" never meant "no effort" — that was a wrong assumption
    that denied effort to 7 of 8 models routed that way**, four of which
    (`glm-5.2`, `minimax-m3`, `qwen3.7-max`, `longcat-2.0`) are not Claude at all.
    Measured ladders: opus-4.8 and sonnet-5 `low..max`; **sonnet-4.6 rejects
    `xhigh`** ("This model does not support effort level 'xhigh'. Supported levels:
    high, low, max, medium."); glm-5.2 / minimax-m3 / longcat-2.0 accept all seven;
    qwen3.7-max `low..max`. **`claude-haiku-4.5` is the ONE genuine no-effort model
    on either protocol** — it rejects every level, because it does not reason.
    Probe it with `smoke_reasoning_efforts.py --protocol anthropic`.
  - **Both send paths must drop an unsupported level, not just hide it.** The
    composer's reset and the arena picker's reset are DISPLAY only; the selection
    state survives a model switch, so the chat controller and the arena launch
    payload each re-filter against the model's ladder before sending. Without that,
    picking `high` then switching to glm-5.2 still sent `high` and 422'd mid-turn.
  - **`OPEN_OTC_MODEL_REASONING_EFFORT` is filtered per model too**, and its value
    is spell-checked. It has no request boundary to validate at, and before that it
    (a) forwarded a typo verbatim and (b) sent `high` to toggle-only and
    non-reasoning models — so a sweep silently included models that never varied,
    and both failures read as "effort had no effect on this model" rather than
    "effort was never applied". A filtered model is **skipped with a WARNING**, not
    silently: raising would break a process-wide sweep for the models that can take
    it.
- **Arena effort is PER MODEL, not per run** (`arena_run.reasoning_efforts`, a
  `{model_slug: effort}` JSON map — migration `0057` replaced `0056`'s single
  column). A scalar could not express a pinned mixed board at all: with GLM-5.2 on
  `high/max`, five contestants toggle-only and most on `low/medium/high`, the
  intersection across a real field is usually EMPTY. A model absent from the map
  runs at its vendor default. `queue_arena_run` validates each entry against **its
  own** model at **launch** (and rejects a key naming a model not in the run, which
  would otherwise silently leave the board unpinned);
  `resolve_agent_model_selection` would otherwise reject the offending model
  per-match, after other pairs had already cost money.
- **`merge_runs` folds by `(workflow_id, model_id, reasoning_effort)`** — effort is
  part of the contestant key, so it is part of the fold key. A cross-effort merge
  therefore produces one merged row **per arm** rather than the 400 it used to
  return: the fold that refusal protected against (one row averaging two regimes'
  EFF/CON) is structurally impossible now, and refusing would make merge stricter
  than launch, which happily puts both arms in one run. Merged runs record
  per-model effort **lists**, and each merged match carries its arm's effort in
  both the column and `config`.
- **Both pickers filter to what the model accepts**, so the UI can never offer a
  level the server rejects: the composer from `AgentModelOption.reasoning_efforts`,
  and the arena New Run panel with one picker per selected model
  (`reasoningEffortsFor`), hidden entirely for a model with no ladder.
- **A new field on `agent_model_config` MUST also be declared on the response
  model.** `/api/agent/models` is served with `response_model=AgentModelConfigOut`,
  and pydantic **silently drops** any key `AgentModelOption` does not name — the
  builder emitted `reasoning_efforts` and its unit test passed while the API served
  none of it, so the composer offered every level for every model. Assert such
  fields at the **HTTP** layer (`test_api.py`), where the response model applies.
  Same failure mode as the golden-workflow `scope: session` key.
- **`"none"` is a request, `None` is an absence.** `"none"` asks the model to skip
  reasoning and IS sent; collapsing it to unset would turn "no thinking" into
  "vendor default thinking".
- **Anthropic protocol REFUSES an effort (422), never drops it.** `ChatAnthropic`
  has no `reasoning_effort` — it budgets thinking in tokens — so accepting one
  would report a setting that never took effect.
- **Precedence is explicit-arg → `OPEN_OTC_MODEL_REASONING_EFFORT` → vendor
  default**, matching the gateway bridge's documented ladder.
- **Two efforts are two operating regimes, never one averaged row.** Effort moves
  tool-call count (**measured: ~22% fewer calls at `high` than `low`**, runs
  #107/#108), so it moves EFF and CON — the same defect class as folding a model
  whose weights changed behind a stable id. That is why effort is in the
  contestant key rather than beside it.
- `run_match_fn` / `_run_and_score_once` receive the effort kwarg **only when
  pinned** — those are injected seams (≈20 test fakes plus
  `scripts/launch_arena_run.py` supply their own), so an unpinned run must issue
  the exact call it always did.

---

## Model maintenance UI

A web console to add/edit/delete LLM **channels and models** — and set the registry
default — instead of hand-editing `config/agent_channels.yaml`. The YAML stays the single
source of truth; the UI mutates it and hot-reloads the live registry.

**Package:** `backend/app/services/deep_agent/channel_registry_writer.py` (the writer),
`channel_registry.py` (`reload()` now reads under `_LOCK`; new `commit_registry()` seam),
`model_factory.py::agent_registry_config` (maintenance serializer), `routers/agent_channels.py`
(`build_agent_channels_router`, flag-gated CRUD under `/api/agent`). Schemas:
`AgentRegistryOut` / `ChannelWriteIn` / `ModelWriteIn` / `DefaultWriteIn` (`schemas.py`).
Frontend: `frontend/src/routes/ModelMaintenance.{tsx,live.tsx,css}` (the **Model Maintenance**
nav page). New dep: `ruamel.yaml`.

### Write model (validate-then-commit, corrupt-save-proof)

Every mutation runs the FULL read-modify-write under `channel_registry._LOCK` via
`_mutate`: load the YAML round-trip (`ruamel`, comment/key-order preserving) → apply one
change + guards → dump to a temp file → validate with the existing
`channel_registry.load_from_path` → only on success `os.replace` onto the live file **and**
swap `_REGISTRY` under the same lock, then the router calls
`agent_service.rebuild_default_model()`. A bad candidate never reaches `os.replace`
(→ HTTP 422, live file byte-unchanged); guard violations → 409.

### Gotchas

- **Holding `_LOCK` across the load (not just the commit) is the lost-update fix.** Two
  concurrent writes that each only locked the commit would both read the same snapshot and
  the second `os.replace` would clobber the first. `reload()` was also changed to read the
  file under `_LOCK` for the same reason.
- **Health-independent default integrity.** `load_from_path`'s `_resolve_default` *skips*
  validating the default when its channel is unhealthy (missing `api_key_env` var), so the
  writer has its **own** raw-level check (`_assert_default_integrity`) that blocks
  deleting/renaming the default even when unhealthy — else a later reload (once the key
  returns) would fail with a dangling default.
- **Model routes need `{model_id:path}`.** Model ids contain slashes
  (`anthropic/claude-sonnet-4.6`), so a plain `{model_id}` segment can't match; the frontend
  sends the id **raw** (channel names are URL-encoded).
- **Secrets never touch the YAML.** The UI edits only the `api_key_env` *name*; health is
  derived from whether that env var is set. Adding a brand-new provider still needs a manual
  `.env` edit + restart.
- **Does NOT sync arena `CANDIDATE_MODELS`.** `services/arena/models.py::CANDIDATE_MODELS`
  is a separate hardcoded list; adding a model here does not make it an arena contestant.
- **Writes gated by `OPEN_OTC_FEATURE_MODEL_WRITE_API`** (default on; three config sites like
  every other flag). This is a default-on, unauthenticated surface consistent with the rest
  of the no-auth backend — set it `false` on any non-localhost bind. The UI edits only the
  live root `config/agent_channels.yaml`; the tracked `.example.yml` is left to humans.
- **Tests are hermetic against `AGENT_CHANNELS_FILE` leaks** — the writer/router/serializer
  test fixtures source the config from `channel_registry._REPO_ROOT`, not the env-overridable
  `_yaml_path()` (another test in the suite repoints that env var).

---

## The three axes of a model entry (schema, 2026-08-25)

A model in `config/agent_channels.yaml` is described by three INDEPENDENT fields.
Read them as WHAT / WHOSE METAL / HOW WE TALK TO IT:

```yaml
- id: qwen/qwen3.7-max      # WHAT  — bare model id, no `:upstream` suffix
  provider: alibaba         # WHOSE — the ZenMux upstream serving it
  protocol: anthropic       # HOW   — openai_chat | anthropic | openai_responses
```

The gateway is addressed as **`<id>:<provider>`**, composed by the loader and
exposed as `ModelDescriptor.wire_id`. **Anything that talks to a provider, or keys
route-scoped data, must use `wire_id`, never `.id`** — the measured effort ladders
in `config/model_reasoning.json` are keyed per ROUTE, so a pinned model read by its
bare id silently resolves to its unpinned twin's ladder.

- **`provider` used to mean the gateway routing label** (`anthropic`/`openai`) with
  the upstream riding in the id as a `:suffix`, so one row read
  `qwen/qwen3.7-max:alibaba` / `openai` / `anthropic` — three vendor-shaped words
  meaning three different things. That label was **dispatch-dead** (`build_agent_model`
  has always routed on the protocol; `glm-5.2` with `provider: openai` took the exact
  same `ChatAnthropic` branch as `claude-opus-4.8`) and **redundant as a lookup key**
  (`_build_channel` already forbids duplicate ids per channel), so the field was freed
  to name the upstream — which is a real part of the route.
- **`find_model` matches on the id ALONE and ignores `provider`.** That is what lets
  13k+ persisted `AgentMessage.meta` selections — replayed verbatim by async resume —
  keep resolving after the meaning changed. Both id spellings resolve; a selection
  naming an upstream the registry no longer declares resolves to the configured one
  with a warning, because replaying an old thread on `:alibaba` is the worse outcome.
- **Legacy YAML still loads,** translated in place. The discriminator is the id's
  `:suffix`, **never the `provider` value** — `anthropic` is both a legacy gateway
  label and a real upstream, so only the row's shape distinguishes the schemas.
  A legacy row with no suffix AND no protocol is left **unpinned** rather than
  composing `<id>:openai`: minting a pin nobody chose is worse than the lottery.
- **`protocol` is REQUIRED on a zenmux row** and no longer defaults to `provider` —
  that default only worked while `provider` was the gateway label; now it would ask
  to speak a protocol called "deepseek". Values were renamed from
  `openai`/`responses` because a bare `openai` hid WHICH of OpenAI's two APIs was
  meant, and that is the distinction the malformed-tool-call investigation turned
  on. Old spellings alias (`canonical_protocol`).
- **The `channel.type` guard on the DeepSeek SDK branch is load-bearing.** The zenmux
  row `deepseek/deepseek-v4-flash` now also carries `provider: deepseek`; without the
  guard it would be built with `ChatDeepSeek` against the ZenMux base URL — a client
  for the wrong API, silently. `provider` selects an SDK only on an
  `openai_compatible` channel, where there is one upstream and nothing to pin.
- **Upstreams are shape-checked, never allowlisted** (`_UPSTREAM_RE`). ZenMux has no
  enumeration endpoint (302/404), so an allowlist could only reject real providers.
- **Two upstream arms of one model are legal** — `_build_channel` dedupes on the
  dispatch id — and `find_model` then **refuses a bare id** as ambiguous rather than
  picking one. Silently choosing would BE the lottery the pin exists to prevent.
- **Declare the upstream ONCE.** `arena_model_to_selection` derives it from the pin
  inside `zenmux_name` instead of reading `ArenaModel.provider`, because two
  declarations drift: `qwen3.8-27b` sat `protocol: anthropic` in the live YAML for
  five weeks while its own comment — and the tracked template — said it had no pin.
- **`dispatch_id` must be declared at all three layers or it vanishes**:
  `AgentRegistryModelOut` (pydantic drops unnamed keys), the router projection, and
  `AgentRegistryModel` in `types.ts`. Same trap as `/api/agent/models`'
  `reasoning_efforts`.
