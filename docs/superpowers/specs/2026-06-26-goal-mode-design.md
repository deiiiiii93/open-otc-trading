# Goal mode: ledger-grounded acceptance over autonomous desk runs

**Date:** 2026-06-26
**Status:** Design complete — pre-implementation. Core decisions resolved (see §Decisions); the five
sub-decisions resolved via a GPT-5.5 (xhigh) review pass on 2026-06-26 (see §Resolved decisions).
**Companion to:** `2026-06-26-three-mode-execution-design.md` (Interactive / AUTO / YOLO). Goal mode
is the acceptance layer *above* the three modes; it inherits their gating and adds a contract.

## Problem

The three-mode design gives us a true headless **YOLO** mode: the model can no longer defer to a
human (no `propose_reply_options`, no prose-asking). That removes the brittle "autonomy
auto-continue" crutch and the Opus-4.8 drift it caused (re-running risk on the *Default* portfolio
with no profile instead of the named *Control* portfolio + Control Profile).

But removing the fake human leaves a gap: **nothing replaces the judgment the human used to
provide.** In YOLO a long autonomous run proceeds on best-effort and returns whatever it returns;
there is no in-band check that it actually achieved what was asked. The spec's own mitigation is a
prompt clause ("do not substitute defaults for named targets") — necessary but not sufficient,
because a prompt cannot *verify* the outcome.

The high-value desk scenario is **open-ended goals with no predefined workflow** — "get the AAPL
book delta-neutral by the close," "reduce snowball-book tail risk under a 20% crash to within
limit," "investigate yesterday's vega P&L spike and propose a fix." The *path* is unscriptable (an
LLM must find its way); the *acceptance* is usually a concrete, measurable end-state. Today these
run with no definition-of-done and no acceptance gate.

`deepagents>=0.6.7` ships `RubricMiddleware`: an in-loop LLM-as-judge that, when the agent would
finish, grades a transcript against a caller-supplied rubric and loops the agent back on
`needs_revision` until `satisfied` or `max_iterations`. A feasibility spike confirmed the loop works
on our real model/provider. It is the right *mechanism*, but its default grader judges the
**transcript** (a tail-30-message window) — which our compaction design explicitly forbids treating
as truth. Goal mode is the work of bending that mechanism to our ledger-grounded model.

## Goals

- A **goal mode** that layers on the three execution modes: declare a definition-of-done, run
  autonomously under the turn's mode, self-verify against the goal, loop or escalate.
- **Ledger-grounded acceptance:** the grader verifies criteria against durable ledger state
  (artifacts, persisted runs, findings), never against transcript narration.
- **Trustworthy in YOLO specifically:** make headless autonomous runs safe for consequential work
  by replacing the lost human judgment with a frozen, externally-ratified acceptance contract — the
  structural form of the spec's "named targets" mitigation.
- Reuse existing infrastructure: the three-mode gating, the capability-gate read/write seam, HITL
  interrupts, and `LedgerScopedCompactionMiddleware`.

## Non-goals

- **Not** a planner. Goal mode does not add decomposition/search; "the LLM finds its way" is the
  executor's own reasoning over the protected plan/findings ledger. Goal mode is the acceptance
  contract around it.
- **Not** a new execution mode. Autonomy/checkpoint behavior is inherited from
  Interactive/AUTO/YOLO, not redefined here (Decision 2).
- **Not** unbounded autonomy. v1 targets goals with a *bounded ledger footprint* (see §E ceiling);
  arbitrarily-long open-ended investigations are deferred until ledger offloading exists.
- **Not** a change to which HITL interrupts exist or how cost/run-python gating works — only the
  addition of an acceptance gate and a kickoff ratification.

## Decisions (resolved)

1. **Criteria authorship — framer drafts, user ratifies once, frozen.** A *framer* role (a distinct
   invocation from the executor) turns the NL goal into ledger-grounded, measurable criteria. The
   user confirms/edits **once at kickoff**; the criteria are then **immutable** for the run.
   Rationale: keeps the criteria-author and the grader independent of the executor; framing is the
   cheapest, highest-leverage human touch and forces the goal to become precise.
2. **Autonomy follows the thread mode.** Goal mode does not impose a checkpoint policy. The
   *approval/checkpoint* behavior for writes is whatever Interactive / AUTO / YOLO already dictate.
   Two clarifications: (a) the contract's `domain_write_policy: "forbidden"` *additionally* withholds
   `DOMAIN_WRITE` capability entirely (orthogonal to mode); `"allowed_by_mode"` grants it and lets
   mode govern approval. (b) Ratification is mandatory for `allowed_by_mode` contracts; a `forbidden`
   (read-only/advisory) contract may be auto-ratified. Goal mode adds only the framing gate, the
   frozen criteria, the grader loop, and escalation.

## The design

### A. Layering over the three modes

The grader loop is orthogonal to autonomy; the mode supplies gating:

| Mode | Writes during the run | Mid-run surfacing | Goal-mode shape |
|------|-----------------------|-------------------|-----------------|
| **Interactive** | HITL-interrupt, user approves each | yes | ratify → autonomous reads/plan → each write approved → grader gate |
| **AUTO** | auto-cleared | yes (may ask/propose) | ratify → autonomous incl. writes → grader gate; agent may voluntarily surface |
| **YOLO** | auto-cleared | **no (headless)** | ratify *at kickoff* → fully silent autonomous → grader gate → out-of-band escalation |

Ratification is a **launch-time** gate, so it is compatible with YOLO (the human touch is before the
headless run begins, not during it).

### B. Goal lifecycle

```
frame → ratify+freeze → execute (mode-gated) → grade → {satisfied | revise | escalate}
```

1. **Frame.** A framer invocation reads the NL goal and emits a `FramerResponseV1` (§C) — either a
   `GoalContractV1` or a `needs_clarification` (questions surfaced, no run started). The framer is
   prompted to commit to checkable end-states, not prose.
2. **Ratify + freeze.** The contract is surfaced to the user for one confirm/edit. On confirm it is
   frozen into thread state as the immutable `rubric`, the `goal_contract`, and `contract_hash` (§C).
   A `forbidden` (read-only/advisory) contract MAY be auto-ratified (low stakes); an `allowed_by_mode`
   contract (any goal that writes) MUST be ratified.
3. **Execute.** The orchestrator runs the goal under the turn's mode. The executor maintains the
   durable `plan` artifact and accumulates `finding` artifacts (both ledger-protected).
4. **Grade.** `RubricMiddleware` fires at the executor's natural finish, grading the **ledger**
   (§D), not the transcript.
5. **Terminate.** `satisfied` → return. `needs_revision` → inject per-criterion feedback, loop
   (bounded by `max_iterations`); strategy changes append a `plan` revision (§F). `max_iterations_reached`
   / `failed` / `grader_error` / `context_ceiling` → escalate (§F).

### C. Criteria contract — final v1 (the trust hinge)

Vague criteria silently re-rot the gate, so the framer emits a structured `FramerResponseV1`, not
free prose. All criteria are required (no weights, no partial credit), and every check is backed by
a ledger artifact or an allowlisted `DOMAIN_READ` tool.

```ts
type FramerResponseV1 =
  | { type: "contract"; contract: GoalContractV1 }
  | { type: "needs_clarification"; summary: string; questions: string[] };
// needs_clarification means the goal is not executable yet; no executor run starts from it.

type GoalContractV1 = {
  schema_version: "goal_contract.v1";
  goal_text: string;
  summary: string;
  // "forbidden" → the run must not receive DOMAIN_WRITE; ratification may be auto-skipped.
  // "allowed_by_mode" → kickoff ratification mandatory; writes follow the thread mode.
  domain_write_policy: "forbidden" | "allowed_by_mode";
  criteria: GoalCriterionV1[];          // min 1, max 10
};

type GoalCriterionV1 = {
  id: string;                            // "C1", "C2", … stable within the contract
  text: string;                          // human-readable acceptance statement
  required: true;
  check: ArtifactExistsCheck | LedgerPredicateCheck | MeasurableCheck;
};

type ArtifactExistsCheck = {
  type: "artifact_exists";
  kind: "plan" | "finding" | "report" | "persisted_run";
  selector?: FieldPredicate[];           // all must match artifact metadata/top-level fields
  min_count?: number;                    // default 1
};

type LedgerPredicateCheck = {
  type: "ledger_predicate";
  tool: string;                          // must be in GOAL_GRADER_READ, DOMAIN_READ only
  args: object;                          // frozen explicit args; no implicit defaults for named targets
  expect: FieldPredicate[];              // all must pass against returned JSON
};

type MeasurableCheck = {
  type: "measurable";
  tool: string;                          // GOAL_GRADER_READ, DOMAIN_READ only
  args: object;
  metric_path: string;                   // dot-path into returned JSON
  transform?: "identity" | "abs";        // default identity
  op: "<" | "<=" | ">" | ">=" | "==" | "!=";
  threshold: number;
  units?: string;
};

type FieldPredicate = {
  path: string;                          // dot-path into returned JSON or artifact fields
  op: "exists" | "not_exists" | "eq" | "neq" | "lt" | "lte" | "gt" | "gte" | "in" | "contains";
  value?: string | number | boolean | string[] | number[];
};
```

**Validation (enforced before freeze):**

- A contract with `domain_write_policy: "allowed_by_mode"` must contain at least one
  `ledger_predicate` or `measurable` criterion verifying the target end-state — it may **not** rely
  on `artifact_exists` alone (artifact existence is gameable; an end-state predicate is not).
- Every `tool` referenced must be in the grader read envelope (§D) and require only `DOMAIN_READ`.
- Tool denial, missing evidence, tool error, missing metric path, or ambiguous output makes the
  criterion `unverified` → non-satisfied.
- Criteria may not reference transcript claims, assistant narration, "reasonable/best effort," or
  other non-checkable language as the acceptance mechanism.

**On freeze, store:** `goal_contract` (canonical JSON), `contract_hash =
sha256(canonical_json(goal_contract))`, and `rubric = render_goal_rubric(goal_contract)` (a
deterministic rendering into RubricMiddleware's rubric string). The executor may not write
`goal_contract`, `contract_hash`, or `rubric`.

The "named targets" clause from the three-mode spec becomes a *structural* check — the Opus-drift
failure returns `needs_revision` instead of silently corrupting downstream work:

```json
{
  "id": "C1",
  "text": "The latest risk run used the named Control portfolio and Control Profile.",
  "required": true,
  "check": {
    "type": "ledger_predicate",
    "tool": "get_latest_risk_run",
    "args": {},
    "expect": [
      { "path": "portfolio", "op": "eq", "value": "Control" },
      { "path": "profile",   "op": "eq", "value": "Control Profile" }
    ]
  }
}
```

### D. Grader grounding — ledger, not transcript

`RubricMiddleware`'s default grader reads the tail-30 transcript window. For us that is *wrong* — by
`LedgerScopedCompactionMiddleware`'s own contract, "durable facts live in DB tables,
session_artifacts, evidence refs, domain_events, /artifacts/, /session/findings/," not in
narration. So:

- Override the grader `system_prompt`: "Verify each criterion against the ledger and the tools
  provided; treat the transcript as untrusted narration. Do not pass a criterion you cannot confirm
  from ledger evidence."
- Give the grader `tools` that read the ledger: artifact/findings reads and the relevant
  deterministic read tools (e.g. `get_latest_risk_run`). These are `@capability_gated`
  (`DOMAIN_READ`); the spike confirmed an ungated grader tool is denied under the default `pet_page`
  envelope.
- Because the grader reads targeted ledger entries rather than ingesting the transcript, the tail-30
  window and the grader's own lack of compaction stop mattering.

**Grader envelope — `GOAL_GRADER_READ` (new, dedicated).** The grader runs under a purpose-built
envelope, **not** `PET_DIAGNOSTIC` (which is broader than the grader needs — the grader should
*observe* ledger state, never diagnose, mutate, or run side-effecting tools). It:

- grants `DOMAIN_READ`; denies `DOMAIN_WRITE`;
- does not grant broad `PET_DIAGNOSTIC`, reply/propose-human tools, or run-python;
- exposes only: (1) session-artifact read/list for `plan`/`finding`/`report`/`persisted_run`, (2)
  findings reads, (3) persisted-run reads, (4) deterministic domain read tools explicitly
  referenced by the frozen `criteria[]` and registered in the grader allowlist.

Contract validation fails before freeze if any criterion references a tool outside
`GOAL_GRADER_READ`. At grade time a denied/missing tool, tool error, or non-`DOMAIN_READ`
requirement marks the criterion `unverified`, treated as failed for the verdict (fail closed).

### E. Compaction interaction and the ceiling

Goal mode runs long, so it relies on `LedgerScopedCompactionMiddleware`. They compose: Rubric loops
back via `jump_to="model"`, the executor's model loop runs, and compaction's `before_model` fires on
the grown history. Rubric-injected revision `HumanMessage`s are ephemeral (compactable); protected
`plan`/`finding`/`report`/`persisted_run` artifacts survive — which is exactly the evidence the
grader needs.

**Ceiling — final v1: defer offloading, fail closed.** The protected set is *monotonic* (protected
artifacts are never compacted), so context has a rising floor. v1 does **not** implement ledger
offloading — reference-replacing protected artifacts is a separate storage/compaction-correctness
problem too risky to rush into the acceptance path. v1 scopes to **bounded-footprint goals** and
fails closed instead of summarizing away grader evidence.

Before every executor model call and every grader call, compute whether the required context fits
(system/developer prompts + frozen `rubric` + `goal_contract` + compacted non-protected history +
**all protected ledger artifacts** + tool scaffolding). If it cannot fit the model window or exceeds
the configured budget `GOAL_PROTECTED_CONTEXT_BUDGET` (v1 default: 0.6 × model `max_input_tokens`),
stop immediately and escalate with `terminal_reason: "context_ceiling"` (§F) — do **not** keep
looping to `max_iterations` (v1 default `GOAL_MAX_ITERATIONS = 3`).

The `/goal` ratification card states that v1 supports bounded-ledger goals; broad investigations
that would accumulate unbounded findings must be narrowed, split into multiple goals, or deferred
until reference-based offloading exists.

### F. Escalation on terminal-unsatisfied — final v1

Terminal non-satisfied outcomes are `max_iterations_reached`, `failed`, `grader_error`, and
`context_ceiling`. The **orchestrator** (not RubricMiddleware, which owns only the verdict) handles
escalation: it writes a persisted `GoalRunStateV1` to thread/session metadata and appends a
structured `goal.escalated` event.

```ts
type GoalRunStateV1 = {
  schema_version: "goal_run_state.v1";
  goal_run_id: string;
  contract_hash?: string;                // set on freeze; absent while awaiting_ratification
  mode: "interactive" | "auto" | "yolo";
  status: "awaiting_ratification" | "running" | "satisfied" | "stuck_needs_human" | "cancelled";
  terminal_reason?: "max_iterations_reached" | "failed" | "grader_error" | "context_ceiling";
  last_verdict?: "satisfied" | "needs_revision" | "failed" | "grader_error";
  failing_criteria: Array<{
    id: string; text: string;
    status: "failed" | "unverified";
    reason: string; evidence_refs: string[];
  }>;
  partial_ledger_refs: {
    plan_artifact_ids: string[];           // all plan revisions, latest last
    finding_artifact_ids: string[];
    report_artifact_ids: string[];
    persisted_run_ids: string[];
  };
  updated_at: string;
};
```

**By mode:**

- **Interactive / AUTO:** `status: "stuck_needs_human"` + a normal in-thread goal-status card
  summarizing failing criteria and partial ledger.
- **YOLO:** `status: "stuck_needs_human"` + the structured goal-status card/event **only** — no
  `propose_reply_options`, no model-authored in-band question, no auto-resume.

If the grader gives no criterion-level detail, populate `failing_criteria` with every criterion not
positively recorded as satisfied, marked `unverified` with `reason: terminal_reason`.

**Resume:** requires an explicit user action from the stuck card; keeps the same frozen
`goal_contract`/`contract_hash`/`rubric` (changing criteria means cancel + new `/goal`); runs in the
mode chosen at resume time (default presented: Interactive); injects the last failing-criteria
feedback as compactable revision feedback and continues from the protected ledger artifacts.

### Revision-loop planning policy (patch vs re-plan)

`needs_revision` feedback **may** trigger re-planning, but only by **appending a new protected
`plan` revision** — there is no separate re-plan trigger in v1, and prior plans are never edited or
deleted (auditability under compaction). RubricMiddleware remains feedback-only; the executor
decides:

1. Read the latest protected `plan` + relevant `finding` artifacts.
2. If satisfying the failing criteria needs a changed strategy/target/tool-sequence or any further
   `DOMAIN_WRITE` → append a new `plan` revision **before** acting.
3. If the gap is only missing evidence or a missing deliverable artifact → patch without a revision.
4. Never mutate/delete prior plan artifacts; never mutate `goal_contract`/`contract_hash`/`rubric`.

```ts
type PlanRevisionV1 = {
  kind: "plan";
  plan_id: string; revision: number;
  supersedes_plan_artifact_id?: string;
  reason: "initial" | "rubric_feedback" | "human_resume";
  addresses_criteria: string[];
  summary: string;
  steps: Array<{ id: string; description: string; expected_ledger_evidence?: string[] }>;
};
```

Domain *side effects* remain governed by the thread's execution mode for approval — except that a
`forbidden` contract has no `DOMAIN_WRITE` capability at all, so a plan revision can never escalate
it into writes.

### G. Activation: the `/goal` command

Goal mode is activated **explicitly** by a `/goal <description>` slash command in the desk composer
— never auto-detected, never a persistent toggle. This keeps it a deliberate, per-session act and
keeps the everyday chat path untouched.

There is **no existing slash-command infrastructure** (verified: nothing in `ChatComposer.tsx`,
`useAgentChatController.ts`, `agents.py`, or `main.py`), so this is net-new. v1 scope is the single
`/goal` command, parsed at the boundary:

1. **Composer parse.** `ChatComposer` recognizes a leading `/goal ` token, strips it, and routes the
   remainder as `goal_text` to a dedicated start-goal request (not a normal chat message). A bare
   `/goal` with no text shows an inline hint instead of sending.
2. **Orthogonal to mode.** `/goal` activates the *acceptance layer*; the Interactive/AUTO/YOLO
   segmented control still governs autonomy (Decision 2). A user picks the mode, then types `/goal …`.
3. **Triggers framing, not execution.** `/goal` runs the framer. If the framer returns
   `needs_clarification`, the questions are surfaced and **no** rubric/goal run is created — the user
   resubmits a refined `/goal`. If it returns a `contract`, it is shown as a ratify card; a
   `forbidden` contract may flow straight to execution (auto-ratified), an `allowed_by_mode` contract
   executes only after the user confirms/edits (§B).

A minimal command registry can come later; v1 hard-codes the one prefix.

### H. Goal run lifecycle & activation gate

Goal mode is **thread-scoped**: a thread holds at most one *active goal pointer*
(`active_goal_run_id`) plus the persisted `GoalRunStateV1` (§F). The pointer is set when the
`awaiting_ratification` state is created and stays set through `running` and `stuck_needs_human`; it
is cleared only on `satisfied`/`cancelled`. The rubric is attached to invocation state (and thus
`RubricMiddleware` is active) **only** while the pointed-to run's status is `running`. The state
machine:

```
(/goal) → awaiting_ratification → running → { satisfied | stuck_needs_human | cancelled }
                       │                         │              │
              (auto-ratify forbidden)      (resume) ───────────┘ (resume from stuck)
```

- `awaiting_ratification`: contract framed, not yet frozen (no `contract_hash`); `active_goal_run_id`
  is set here. A `forbidden` contract MAY be auto-ratified — if so it freezes and transitions
  straight to `running`; otherwise it awaits user ratification like any contract.
- `running`: contract frozen; the executor is live. **This is the only state in which
  `RubricMiddleware` is active.**
- `satisfied` / `cancelled`: terminal. The orchestrator clears `active_goal_run_id`; the frozen
  contract/state are retained for audit.
- `stuck_needs_human`: terminal-pending; the pointer stays set until the user resumes (→ `running`)
  or cancels (→ `cancelled`).

**Activation gate (corrects the naive "rubric present" check):** `RubricMiddleware` must key off an
**active `running` goal run**, not the mere presence of a `goal_contract`/`rubric` in state — else a
`satisfied`/`stuck`/`cancelled` goal would keep re-grading every subsequent ordinary turn on the
thread. Concretely, the orchestrator only attaches the rubric to the invocation state while
`active_goal_run_id` resolves to a `running` (or just-resumed) `GoalRunStateV1`; otherwise the
middleware is a no-op (`before_agent`/`after_agent` short-circuit on absent `rubric`).

## Data flow

```
User types `/goal <description>`  (composer strips prefix → start-goal request { goal_text, mode })
  → framer invocation → FramerResponseV1
       needs_clarification → surface questions; no run created  (user resubmits)
       contract           → GoalRunState{ awaiting_ratification }
       (active_goal_run_id set at awaiting_ratification)
  → ratify (forbidden MAY auto-ratify; allowed_by_mode requires user confirm/edit)
       → freeze { rubric, goal_contract, contract_hash }; state → running
  → build_orchestrator(yolo_mode=, allow_reply_options=)         (mode-derived, unchanged)
       middleware += RubricMiddleware(                            (attached only while running)
           model, system_prompt=LEDGER_GRADER_PROMPT,
           tools=GOAL_GRADER_READ tools, max_iterations=GOAL_MAX_ITERATIONS,
           on_evaluation=record_evaluation)
  → executor runs (plan + findings ledger; mode-gated writes; plan revisions on re-strategy)
  → RubricMiddleware.after_agent → grader (reads ledger under GOAL_GRADER_READ)
       satisfied      → state → satisfied; clear active_goal_run_id
       needs_revision → inject per-criterion feedback → loop
       terminal-unsat → write GoalRunState{ stuck_needs_human } + goal.escalated event
                        (in-band card for Interactive/AUTO; structured card only for YOLO)
```

## Resolved decisions

All five open items were resolved in a GPT-5.5 (xhigh) design-review pass on 2026-06-26 and folded
into the design above. Summary log:

1. **Criteria schema** → §C. Structured `FramerResponseV1` / `GoalContractV1` with required
   all-or-nothing `artifact_exists` / `ledger_predicate` / `measurable` checks; `allowed_by_mode`
   contracts must carry ≥1 end-state predicate; frozen with `contract_hash`.
2. **Grader envelope** → §D. New dedicated `GOAL_GRADER_READ` (DOMAIN_READ only, fail closed); not
   `PET_DIAGNOSTIC`.
3. **Escalation mechanism** → §F. Persisted `GoalRunStateV1` (`stuck_needs_human`) + `goal.escalated`
   event + structured goal-status card; explicit-action resume keeping the frozen contract.
4. **Patch vs re-plan** → §F. `needs_revision` may re-plan, but only by appending a protected `plan`
   revision (`PlanRevisionV1`); prior plans immutable; RubricMiddleware stays feedback-only.
5. **Ledger offloading** → §E. Deferred; v1 = bounded-footprint goals, fail closed via
   `context_ceiling` escalation.

No open decisions remain for v1 scope. (Reference-based ledger offloading for unbounded goals is
explicitly out of v1.)

## Error handling / edge cases

- **Mutated rubric mid-run.** Forbidden: `RubricMiddleware._reset_for_new_rubric` restarts grading
  whenever the rubric string changes, so a mutable rubric both moves the bar and resets the run.
  Criteria are frozen post-ratification; the executor cannot write `rubric`.
- **`grader_error` / `failed`.** Treated as terminal-unsatisfied → escalate (§F), never silently
  return as done.
- **Read-only goal in Interactive.** No write interrupts fire; the run is effectively autonomous to
  the grader gate. Acceptable.
- **Empty/over-broad criteria from the framer.** Ratification is the catch; an unratified
  consequential goal does not execute.
- **Protected-set growth aborts a run.** If context cannot fit before `satisfied`, escalate as
  `stuck_needs_human` with `terminal_reason: "context_ceiling"` (do not loop to `max_iterations`
  burning tokens).
- **`/goal` while a goal is already active.** Reject with an inline hint (one goal per thread at a
  time): the active goal must be `satisfied` or `cancelled` first; if `stuck_needs_human`, the user
  must resume or cancel it before starting another `/goal`. A bare `/goal` (no text) shows usage and
  sends nothing.

## Testing

- **Mode layering:** grader loop active under all three modes; writes gated per mode
  (Interactive interrupts, AUTO/YOLO auto-clear); YOLO cannot surface in-band.
- **Freeze:** executor cannot mutate `rubric`; a contract edit only happens at ratification.
- **Ledger grounding:** grader passes a criterion only with ledger evidence; a transcript that
  *claims* success without the artifact yields `needs_revision`.
- **Named-target regression:** a run that drifts to the Default portfolio fails the
  `ledger_predicate(portfolio==Control)` criterion (the Opus-drift case).
- **Grader envelope:** ledger-read tools succeed under the grader envelope; `DOMAIN_WRITE` denied.
- **Escalation:** terminal-unsatisfied in YOLO produces a `stuck_needs_human` `GoalRunStateV1` with
  failing criteria + partial ledger; nothing is reported as done.
- **Activation gate:** after a goal reaches `satisfied`/`cancelled`, a subsequent ordinary turn on
  the same thread does **not** re-trigger the grader (rubric attached only while `running`).
- **Compaction compose:** revision messages compact; protected artifacts survive across loop-backs.

## Migration / compatibility

- No DB migration required for v1: the frozen `rubric` + `goal_contract` + `contract_hash`, the
  `active_goal_run_id` pointer, and `GoalRunStateV1` all ride in thread/`AgentMessage.meta` JSON
  alongside `mode` (§F is the structured shape; no new table).
- Goal mode is opt-in per thread; the rubric is attached to invocation state only while the active
  goal run is `running` (§H), so `RubricMiddleware` is a no-op
  (`before_agent`/`after_agent` short-circuit on absent `rubric`) for every ordinary desk turn —
  those remain byte-for-byte unchanged.

## Files touched (estimate)

- Backend: `deep_agent/orchestrator.py` (attach/activate `RubricMiddleware` only while
  `active_goal_run_id` resolves to a `running` goal run, with the rubric on invocation state),
  a new `deep_agent/goal_mode.py` (framer, `GoalContract`/`GoalCriterion`, ledger grader prompt +
  tool set, escalation), `services/agents.py` (kickoff framing + ratification + freeze; thread
  `goal_contract` + `active_goal_run_id` + `GoalRunStateV1`), `deep_agent/capability_gate.py` /
  `envelopes.py` (the `GOAL_GRADER_READ` envelope, §D), `schemas.py` / `main.py` (start-goal request,
  ratify endpoint, resume/cancel endpoints).
- Frontend: net-new `/goal` command parse in `ChatComposer.tsx` (strip prefix → start-goal
  request; bare-`/goal` hint), a ratify/edit criteria card, `types.ts`,
  `useAgentChatController.ts` start-goal wiring.
- Tests across the above.
```
