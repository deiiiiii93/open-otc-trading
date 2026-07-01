# Dynamic Subagents for the OTC Desk Agent — Design Spec

**Date:** 2026-07-01
**Status:** Design (approved for implementation planning)
**Author:** desk agent brainstorm (fuxinyao)

## 1. Summary

Adopt LangChain deep-agents' **dynamic subagents** — where the orchestrator writes a
QuickJS script that fans work out to persona subagents via a `task()` global — as a
governed capability of the OTC desk agent. Engine is **QuickJS `task()` across all
paths**. Value is scoped to **per-item *judgment* work** (narrative / investigation /
proposal), **not** numeric coverage (already solved by deterministic batch tools).

The spec delivers a reusable fan-out substrate plus three persisted daily workflows,
under a three-case trigger/consent taxonomy, with first-class observability.

## 2. Goals / Non-goals

**Goals**
- Enable QuickJS dynamic subagents behind a governed, auditable dispatch model.
- Ship three persisted daily workflows: morning risk-breach commentary,
  per-counterparty rollover reviews, per-underlying hedge proposals.
- Make every dispatch visible to the desk (web + IM) in real time.
- Keep the persistence thesis intact: persist *intent*, re-derive execution.

**Non-goals**
- **Not** for numeric/compute fan-out — `run_batch_pricing` / RiskRun / ScenarioTest
  already guarantee coverage deterministically; dynamic subagents adds nothing there.
- **Not** a Posture-B "promote an ad-hoc run into a persisted workflow" capture path —
  named as a future follow-up, not built here.
- **Not** exposing fan-out with booking/writing tools in the fanned-out persona.

## 2a. Scope of THIS pipeline run (vertical slice)

The full design above is the target end state. **This feature-flow run implements a
vertical slice only** — the pilot that proves the whole pattern end-to-end:

**In scope this run**
- Enable path + config gate for QuickJS dynamic subagents (`OPEN_OTC_AGENT_CODE_INTERPRETER`).
- Lower the *per-eval* `max_ptc_calls` backstop 64 → 24, with deterministic overflow
  handling (§6).
- **Whole-eval attribution gate** (§5a): the gate **rejects every `js_eval`** unless the run
  carries server-set authorization for the **seeded, allowlisted** pilot workflow — blocking
  emergent fan-out *and* arbitrary non-`task()` QuickJS in normal chat. Case-1/2 fan-out and
  any ad-hoc eval stay hard-blocked until the interactive card + frontend panel ship in a
  follow-up. Keeps the slice self-contained: the desk never gets invisible/ungoverned eval.
- **Capability-based booking/write exclusion** (§6) on the fanned-out persona.
- **Morning risk-breach commentary** — a **seeded, server-owned** Case-3 workflow
  (allowlisted id; `dynamic_subagents` honored only for such ids): scope
  (`run_batch_pricing`/RiskRun) → fan-out (`risk_manager`) → assemble.
- **Backend observability plumbing**: route `subagent` custom-stream events onto the web
  SSE stream (data path only; the dedicated panel is deferred, but the events are present
  and surfaceable in the existing transcript rendering).
- Tests: the §10 "vertical-slice acceptance" set only.

**Deferred to follow-up runs (and runtime-disabled until then)**
- **Case-1 and Case-2 fan-out** — the explicit `/dynamic` trigger, the interactive
  confirmation card, and the auto-mode envelope / width-enforce / async-escalation blend —
  all gated off by the §5a attribution gate until they ship *together with* their
  observability surfaces.
- Frontend fan-out panel + `js_eval` legible rendering.
- IM gateway `subagent`-event carriage.
- Per-counterparty rollover + per-underlying hedge workflows (and their scope tools, which
  may not yet exist).

## 3. Background & truth-check

Verified against LangChain's authoritative doc
(`docs.langchain.com/oss/javascript/deepagents/dynamic-subagents`) and the installed
Python packages.

- The documented feature is **JavaScript/TypeScript only** (`@langchain/quickjs`,
  `createDeepAgent`, `createCodeInterpreterMiddleware`). **No Python page or path.**
- We rely on the **Python port** `langchain_quickjs` v0.3.2 + Python `deepagents`, which
  the repo already depends on and already wires. Same semantics (verified from source +
  a runtime probe); the port is *richer* where we need it (per-subagent tools/model/
  middleware) and *adds* the safety caps the JS doc omits (`max_ptc_calls`, 32-concurrent
  limit, timeout). This is a dependency/maturity risk (see §12).

## 4. Key findings (grounding)

1. **Already wired, flag-gated.** `deep_agent/orchestrator.py:166-172` constructs
   `CodeInterpreterMiddleware(ptc=["task"], max_ptc_calls=64, timeout=5.0)`; gated by
   `settings.agent_code_interpreter_enabled` (`config.py:83`, env
   `OPEN_OTC_AGENT_CODE_INTERPRETER`, currently `"false"`). Enabling is config, not a
   refactor.
2. **`timeout=5.0` is safe (empirically probed).** The QuickJS timeout bounds JS-engine
   execution slices, **not** wall-clock spent awaiting a bridged async host call. Probed
   directly against `quickjs_rs`: an 8s host await under a 5s timeout returned cleanly.
   So multi-minute subagent dispatches are fine at the default.
3. **Mid-fan-out HITL is non-resumable (from source).** `deepagents .../subagents.py:
   668-694`: `task()` runs a subagent via a plain nested `subagent.invoke(...)` with **no
   checkpointer on the subagent config**. A LangGraph `interrupt()` inside a `Promise.all`
   has no independent checkpoint → resuming re-executes the whole `js_eval` and
   **re-dispatches every subagent** (non-idempotent). Confirmed by the library docs:
   *"`interruptOn` approval workflows on the parent agent are not enforced per dispatch.
   Gate the `eval` tool itself."* **Consequence: all approval is pre-eval; booking must
   never be in the fan-out path.**
4. **Coverage is already deterministic.** Batch tools iterate in Python (QuantArk), not
   in the LLM, so the "screened 75 of 500" failure cannot occur for numeric work.

## 5. Trigger / consent taxonomy (the spine)

Fan-out is initiated by one of three paths, distinguished by **who initiated it**, which
decides consent and whether the pre-eval gate fires.

| Case | Trigger | Consent | Pre-eval gate |
|---|---|---|---|
| **1. User explicitly asks** | Deterministic (slash form / explicit-intent phrase) | Implicit (user opted in) | **Skipped** |
| **2. Orchestrator judges the request fits** | Emergent (model decides) | **HITL confirm, pre-eval** | **Required** |
| **3. DeskWorkflow tagged `dynamic_subagents`** | Declared in workflow `meta` | Given at authoring/approval | **Skipped** |

### Case 1 — user explicitly asks
- **Trigger:** a `/dynamic` slash form **and** an explicit-intent phrase match routed
  deterministically — *not* left to the model, so Case 1 cannot silently collapse into
  Case 2. (The soft `"workflow"` keyword from the LangChain doc is insufficient on its
  own.)
- **Consent:** pre-authorized; gate skipped. Fan-out panel shown (§8).

### Case 2 — orchestrator judges the request fits
Emergent → **pre-eval HITL confirmation** (mid-fan-out interrupts are non-resumable, so
the confirmation fires *before* the `js_eval` runs, where resume is clean).
- **Interactive:** live confirmation card — "plan to fan out N `{persona}` subagents over
  these {items}, est. ~{cost/time} — approve/reject." Approve → run; reject → the model
  falls back to a narrower/sequential path.
- **Auto (blend):**
  - **(c) Envelope:** the goal-mode contract / launch params declare a fan-out envelope
    (max width, allowed personas, budget) up front.
  - **(a) Enforcement:** a **width/envelope** gate auto-approves within the envelope and
    records a logged decision. Note: fan-out *width* (dispatch count) is knowable at eval
    time, but per-dispatch *cost* is **not** estimable a-priori (the `task()` description
    is natural language; the expensive tool is chosen *inside* the subagent). So
    enforcement is width- and budget-envelope-based, optionally augmented by a post-hoc
    running-cost accumulator that escalates once cumulative spend crosses the envelope —
    not by the per-tool `LongRunningCostHITLMiddleware` a-priori estimator, which has no
    dispatch args to estimate from.
  - **(b) Escalation:** async escalation (IM card / notification via the gateway `done`
    path) fires **only** when the envelope is breached; the run waits for a reply.

### Case 3 — DeskWorkflow tagged
- **Trigger:** `meta` flag `dynamic_subagents: true`, alongside existing `mode` / `persona`
  fields.
- **Consent:** declared at authoring; pre-authorized; gate skipped. Panel shown.

## 5a. The eval gate: an immutable, server-owned trust boundary

Enabling `CodeInterpreterMiddleware` exposes a **general QuickJS `js_eval` tool** to the
orchestrator — not just `task()` fan-out. So the boundary this slice enforces is on the
**eval tool itself**, not merely on dispatches that call `task()`:

- **Whole-eval gate.** For this slice the pre-eval gate **rejects _every_ `js_eval`** unless
  the run carries server-set authorization for the approved pilot workflow. This blocks both
  emergent fan-out *and* arbitrary non-`task()` QuickJS in normal chat (which would otherwise
  be ungoverned new runtime behavior — resource/output manipulation). Only after
  authorization passes may an eval run; the eval is then additionally expected to match the
  fan-out orchestration shape.
- **Attribution is server-set, immutable context.** Authorization is stamped into the run's
  LangGraph `configurable` (e.g. `fanout_attribution = case3_workflow` plus the concrete
  `workflow_id`) **only** by deterministic server code — the DeskWorkflow runner. The
  orchestrator model, its JS `code`, and tool arguments cannot write it. Missing / ambiguous
  / model-supplied ⇒ treated as emergent ⇒ **hard-blocked**.
- **Case-3 authorization ≠ script metadata.** `meta.dynamic_subagents: true` alone does NOT
  grant fan-out. The flag is honored **only for server-owned, seeded/admin-approved workflow
  ids on a slice allowlist**; user-, API-, or model-authored workflow scripts that set it are
  **rejected at save time and ignored at run time**. The gate verifies
  `workflow_id ∈ approved_dynamic_allowlist` before stamping `case3_workflow`. This closes
  the self-*authorization* path at the persistence layer, not just the self-*labeling* path
  inside `js_eval`.
- **No self-labeling:** authorization is never derived from the eval `code`, the `task()`
  description, workflow-script contents, or any other model-influenced field.
- **Tests (blocking):** (a) the seeded pilot workflow (allowlisted id) is allowed; (b) a
  spontaneous orchestrator `js_eval` — with or without `task()` — in normal chat is blocked;
  (c) a user/model-authored workflow that sets `dynamic_subagents: true` is rejected at save
  and receives no attribution; (d) a crafted payload attempting to set the attribution key or
  a non-allowlisted `workflow_id` does not bypass the gate.

## 6. Execution pipeline & invariants

Every fan-out follows a fixed shape:

```
scope   →  deterministic tool call (run_batch_pricing / RiskRun / …) returns the
           COMPLETE item list. Coverage of *which* items is guaranteed by the tool.
fan-out →  for each item, task({subagentType, description, responseSchema}) — parallel,
           context-isolated. Only the per-item JUDGMENT is fanned out.
assemble→  deterministic tool/step collects the typed results into the artifact/report.
```

**Governing principle: keep enumeration in tools, fan out only judgment.** The LLM never
decides *which* items to cover (eliminating the "drops item 3 of 5" risk at the scoping
layer); it only narrates the items the scope tool handed it.

**Hard invariants**
- **Capability-based booking/write exclusion.** Re-dispatch on resume/retry is
  non-idempotent, so a fanned-out persona must be structurally incapable of writes — not
  merely missing a named tool. Reuse the existing capability layer (`capability_gate.py` /
  `ToolGroup`): tools declare read/write + idempotency metadata; fan-out construction
  applies a **read-only capability profile** and asserts, **recursively/transitively**, that
  no reachable tool can book, mutate workflow/DB state, send messages, or create artifacts.
  **Deny-by-default:** an unclassified tool is treated as write-capable and excluded.
- **`max_ptc_calls` lowered 64 → 24 as a *per-eval* backstop**, with explicit overflow
  semantics so it never silently breaks the coverage guarantee. `max_ptc_calls` bounds one
  `js_eval`, not the whole workflow. After the deterministic scope step, a declared workflow
  that finds more items than the per-eval budget **chunks them into sequential batched
  evals** (each within the 24-call budget and the 32-concurrent bridge cap), so *all* scoped
  items are still covered. If chunking cannot proceed (e.g. an emergent run hits the
  backstop), the run surfaces a **visible partial/fail status** rather than silently dropping
  items or emitting only a `PTCCallBudgetExceeded` model error.
- **Per-item terminal state + failure isolation.** Every scoped item produces **exactly
  one** record keyed by its item id — either `success` (typed commentary) or an explicit
  `failed` (subagent timeout / `SubagentError`). Assembly **surfaces failed item ids
  prominently** (or fails the workflow) — never an apparently-complete report with a breach
  silently missing. Retries target **only** `failed` ids (never re-running successful items,
  which would be non-idempotent). Rides the existing `SubagentComplete` / `SubagentError`
  stream events.
- `timeout=5.0` unchanged (empirically safe).
- Eval/fan-out runs only in the governed, allowlisted Case-3 path this slice; never as an
  ungoverned interactive default.

## 7. The three workflows (concrete)

Each is a persisted DeskWorkflow tagged `dynamic_subagents: true` (Case 3), following §6.

1. **Morning risk-breach commentary** (pilot / reference implementation)
   - scope: `run_batch_pricing` / RiskRun → breached positions.
   - fan-out: `risk_manager` per breach → investigate (greeks history, underlying move,
     scenario if warranted), draft commentary. Schema `{position_id, severity,
     commentary}`.
   - assemble: morning report artifact.
2. **Per-counterparty rollover reviews**
   - scope: tool → counterparties with trades expiring in the window.
   - fan-out: persona per counterparty → assess rollover options, draft outreach.
     Schema `{counterparty_id, recommendation, draft}`.
   - assemble: rollover review report.
3. **Per-underlying hedge proposals**
   - scope: tool → over-hedged / mis-hedged underlyings.
   - fan-out: `risk_manager` per underlying → propose an adjustment and explain (no
     booking). Schema `{underlying, proposed_adjustment, rationale}`.
   - assemble: hedge-proposal report for human review.

All three are read/investigate/propose — **none books** — so the §6 booking-exclusion
invariant costs nothing.

## 8. Observability (first-class)

Because Case 2 is emergent, *seeing* the dispatch is a requirement, not a nicety.

- **Backend:** route the `subagent` custom-stream events (`_subagent.py`:
  `SubagentStart/Complete/Error`, `type:"subagent"`, grouped by `eval_id`) through the web
  SSE **and** the IM gateway envelopes. *Nothing consumes them today.*
- **Frontend:** a live **fan-out panel** keyed by `eval_id` — per-dispatch persona
  (`subagent_type`), `label`, live status (start→complete/error), `duration_ms`.
  *Frontend has zero code for this today.*
- Render the `js_eval` tool call as a legible "orchestrating N sub-tasks" affordance, not a
  raw JavaScript dump.

> **Slice note:** §8 describes the end-state observability. This run ships only the
> **backend SSE data path**; the frontend panel and IM carriage are deferred. Because
> emergent (Case-2) fan-out is the case that *needs* the live panel, it is hard-blocked by
> the §5a gate until the panel ships — so no fan-out runs this slice without at least the
> declared-workflow consent (§2a) and the event data reaching the stream.

## 9. Persistence integration

- Case 3 workflows persist the **intent** (scope→fan-out→assemble); the item list is
  re-derived each run against that day's data — same model as today's sequential `step()`.
- The unit of persistence is the *procedure*, never the execution trace.
- Posture B (capture a good Case-1/Case-2 ad-hoc run into a persisted Case-3 workflow via
  the Workflow Builder) is a **future follow-up**, out of scope here.

## 10. Testing strategy

### Vertical-slice acceptance tests (this run — blocking)
- **Coverage (golden, assert-on-outcomes):** every breach the scope tool returned has
  exactly one commentary with a valid severity — *not* "these tool calls fired in this
  order." Scope is deterministic; only the narrative varies.
- **Overflow (§6):** scope returns more than `max_ptc_calls` breaches → all still covered
  across sequential chunks, with a visible partial/complete status.
- **Attribution gate (§5a):** declared Case-3 workflow allowed; emergent/undeclared
  `task()` fan-out hard-blocked; a payload attempting to self-label attribution cannot
  bypass.
- **Capability booking-exclusion (§6):** a fanned-out persona's *reachable* toolset is
  read-only under the transitive check; indirect write wrappers, artifact/message creation,
  and booking aliases are all excluded (deny-by-default on unclassified tools).
- **Per-item failure isolation (§6):** a single subagent error/timeout inside a multi-item
  chunk yields an explicit `failed` record for that item id (surfaced), `success` for the
  rest, and a retry that targets only the failed id.
- **Observability data path:** `subagent` start/complete/error events reach the web SSE
  stream, grouped by `eval_id`.

### Follow-up tests (deferred surfaces — out of scope this run)
- Interactive Case-2 confirmation card; Case-2 **auto-mode** blend (within-envelope
  auto-approve + log; envelope-breach escalation); Case-1 `/dynamic` trigger.
- Frontend fan-out panel rendering; IM gateway `subagent`-event carriage.

Deferred governance surfaces are **runtime-disabled** (via the §5a attribution gate) until
their follow-up tests are in scope — so the slice cannot ship an untested governance path.

## 11. Components / files (implementation surface)

- `deep_agent/orchestrator.py` — enable path already present; wire the pre-eval gate;
  lower `max_ptc_calls`.
- New pre-eval **eval gate** middleware (§5a): **rejects every `js_eval`** unless the run
  carries immutable `configurable` authorization for an allowlisted Case-3 workflow — blocks
  emergent fan-out and arbitrary non-`task()` eval alike.
- `deep_agent/personas.py` / `capability_gate.py` — fanned-out persona gets a **read-only
  capability profile** enforced transitively (§6). (Per-persona auto-mode cost/width gate is
  deferred.)
- `config.py` / `.env` — flip `OPEN_OTC_AGENT_CODE_INTERPRETER`.
- DeskWorkflow model / runner / script validation — **server-owned `dynamic_subagents`
  allowlist** (reject the flag from user/model-authored saves); the runner stamps immutable
  attribution into `configurable`; the seeded pilot workflow script.
- Case-1 trigger — `/dynamic` slash form + explicit-intent routing.
- `stream_collector.py` / `agents.py` — route `subagent` custom events to web SSE.
- Gateway (`services/gateway/*`) — carry `subagent` events / fan-out summary on the
  envelopes.
- Frontend — fan-out panel; `js_eval` legible rendering.
- Tests — golden fixtures (outcome assertions), gate/invariant/observability unit tests.

## 12. Risks & dependencies

- **Python port of a JS-documented feature.** `langchain_quickjs` v0.3.2 is not the
  documented surface; maturity/support/version-parity is not guaranteed. Pin the version;
  add a smoke test that fails loudly if the `task()` bridge or `subagent` event shape
  changes on upgrade.
- **Emergent-dispatch trust** — mitigated by the Case-2 pre-eval gate + observability panel.
- **Re-dispatch non-idempotency** — mitigated by the booking-exclusion invariant; any
  future write-capable fan-out needs idempotency keys before it ships.
- **Golden nondeterminism** — mitigated by asserting on outcomes/coverage, not traces.

## 13. Open questions / future follow-ups

- Posture B capture-and-promote path (future).
- Exact envelope schema for goal-mode contracts (width/persona/budget) — settle in the
  implementation plan.
- Whether the Case-1 slash trigger reuses the existing DeskWorkflow slash auto-pilot
  surface or is a distinct entry point — settle in the plan.
