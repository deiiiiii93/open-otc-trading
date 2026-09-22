# Limit incident text review (Jev site 4) — design

Date: 2026-09-21 · Status: draft for review · Parent spec:
`2026-09-21-jev-system-one-design.md` (cited below as **parent D*n***) ·
Build order: **after the System One branch merges** (see *Rollout*).

## Problem

A limit incident has two halves. The numeric half is fully served and deterministic:
`limits/evaluator.py` computes `utilization`, and `incidents.py` sets
`severity = evaluation.status` behind `_SEVERITY_RANK`. The parent spec keeps that
evaluator deterministic, and nothing here changes it.

The other half is text, and **nothing reads it**:

1. **`waiver_rationale` is validated only for being non-empty.** `incidents.waive()`
   accepts `"ok"`. A waiver silences a breach until `waiver_expires_at`, and
   `waive_limit_incident` is a `"write"` tool, so in AUTO mode an agent can write the
   rationale itself and the waiver executes unattended. The parent's tool guard asks
   *"did the user name this incident?"* — it never asks whether the stated reason is any
   good. A call can pass one and fail the other.
2. **A rationale makes claims about the book that nobody checks.** "The Dec position
   rolls off Friday", "stale vol mark", "limit is being resized" are each one read-only
   query away from being confirmed — but only once something has read the sentence and
   typed the claim.
3. **A comment thread has a state the incident row does not carry.** "The desk disputes
   the number" and "the desk is remediating" are both `status="acknowledged"`. The risk
   manager finds out which by opening each incident and reading.

All three are classification over text with a crisply statable predicate — the shape
Jev fits. None of them produces a number that enters the book.

## Evidence base — what exists, and what does not

- **There is no real corpus.** The live database holds **0** `limit_incidents` and
  therefore 0 waiver rationales and 0 comments (queried read-only, 2026-09-21). No
  predicate below has been tested against desk-written text.
- **There is a labelled model-written corpus — size not yet counted.** The
  `risk-limit-breach-day` golden workflow makes every contestant write a root-cause
  comment (step 3, `comment_limit_incident`), and its step 5 is a waiver probe in which a
  premature waiver is the prohibited action (graded session-wide). Every contestant that
  failed it called `waive_limit_incident` with a rationale it invented, and the scorer
  has already labelled that waiver wrong. Only smoke runs #96 / #97 are known to exist on
  this workflow, so the corpus may be a handful of rows. **Counting it is the plan's
  first task.**
- **Reading finished arena traces offline is not running Jev inside a match.** The
  parent's "OFF for arena runs" rule concerns a live match; an offline probe over the
  trace DB changes no board.
- Everything the parent measured about Jev applies unchanged: ~1.3 s median via ZenMux,
  non-deterministic (drift ≤ 0.10), and **policy must be written into the question**.

**Consequence for the design:** every predicate ships tagged `untested`, the feature is
display-only, and raw probabilities are stored so thresholds can be set later from the
desk's own waivers without re-asking the model.

## Decisions

User decisions (2026-09-21) are marked **[user]**.

- **D1 [user] Scope = three reads of incident text**: rationale grade, waiver claims
  (with deterministic checks), comment-thread state. The audit retrospective sweep is a
  separate, later spec — it depends on the guard's policy table, state builder and
  verdict store.
- **D2 [user] Verify the cheap claims only.** v1 ships checkers for
  `position_rolling_off`, `data_error` and `limit_under_review`. A claim without a
  checker renders `unverified`. `hedge_in_progress` is the most valuable claim to check
  and is deliberately deferred: it couples this feature to the hedging module.
- **D3 [user] Storage = a `limit_incident_reviews` table, one row per
  `(event_id, kind)`.** An incident can be waived, expire, reopen and be waived again;
  columns on `limit_incidents` would keep only the last grade. Writing into the event's
  `payload` was rejected: the timeline is the incident's append-only history, and a score
  attached an hour later is an opinion about an event, not part of it.
- **D4 [user] The rationale ladder and the six claim types are adopted as drafted**
  (*Predicates*, below).
- **D5 Display-only.** A review changes no incident status, blocks no waiver, reorders
  no queue, and is not an input to the tool guard. Same posture as parent D2 / D3, for
  the same reason: no predicate here has evidence.
- **D6 The review is built from the EVENT, never from the incident's mutable columns.**
  The `waived` event's payload already carries `rationale` and `waiver_expires_at`, and
  its `created_at` is the waive time. A re-waive overwrites
  `limit_incidents.waiver_rationale`; the event it replaced is still scoreable, and a
  late retry scores the text that was actually written.
- **D7 Claims are one `noul` each, not one `choice`.** A real rationale often makes two
  claims ("stale mark, and the Dec position rolls off"). Under `choice` the mass splits
  and reads as low confidence; under `noul` both fire and each gets its own check. Crisp
  `noul` predicates are also where the parent's probes separated cleanly.
- **D8 One Jev request per `waived` event (8 questions), one per scored `commented`
  event (1 question)** — parent D12.
- **D9 "Due" is a pure database fact: a scoreable event with no review row, or a row
  with `status="unscored"`.** The post-commit enqueue is only the fast path. A call site
  that forgets to enqueue delays a review until the next sweep; it cannot lose one. No
  `pending` bookkeeping exists to go stale.
- **D10 A thread review is due only for an incident's LATEST `commented` event.** The
  thread state is a function of all comments so far, so older comment events are never
  backfilled. A new comment does not delete the previous thread review — it is history.
- **D11 `claim_check` is `supported` | `no_evidence` | `unverified` — never
  "contradicted".** A checker that finds nothing has shown absence of evidence in this
  database, not that the claim is false (the review may be happening by email). This is
  the parent's step-8 rule — *a disputed print is not a confirmed erroneous one* —
  applied to our own output.
- **D12 Checks are recomputed; Jev answers are not.** Checkers are deterministic and
  cheap, and the book moves: `limit_under_review` can turn `supported` the day after the
  waiver when the draft version appears. Each sweep recomputes the checks of reviews
  whose incident is still `waived`, stamping `checked_at`. The Jev probabilities on the
  row are never refreshed.
- **D13 Reviews are invisible to the agent.** `get_limit_incident_tool` and
  `list_limit_incidents_tool` do not carry them. A model that can see its rationale's
  grade will write to the grader, and any arena board over the limits tools would shift.
  Their hand-built key projections omit new fields by default; a test pins the omission.
- **D14 Arena threads are never scored — and "cannot tell" means skip.** The event's
  `thread_id` is checked against `AgentThread.source == "arena"` at enqueue and in the
  sweep. The memory queue's `_is_arena_thread` has the same test but is **fail-open**
  (an exception ⇒ "not arena" ⇒ proceed), which is right for extraction and wrong here.
  The plan lifts the lookup into a shared helper that returns `True | False | None` and
  lets each caller choose; this caller treats `None` as skip. D9 makes that free: a
  skipped event is still due, so the next sweep retries it. The master switch already
  defaults OFF for arena launches (parent D6); this covers a desk that turned it on and
  then ran a board, whose fixture incidents are purged afterwards.
- **D15 Feature switch `OPEN_OTC_LIMIT_REVIEW`, default `true`, under the master
  switch** — parent D15's opt-out-under-an-opt-in. Live iff `OPEN_OTC_SYSTEM_ONE` **and**
  `OPEN_OTC_LIMIT_REVIEW`. Either off ⇒ inert: no call, no row (parent D17).
- **D16 Author identity stays out of `state`.** `actor`, `persona` and `mode` are on the
  event and the UI shows them, but Jev grades the text, not the author. An agent-written
  level-0 waiver in AUTO mode is the row a risk manager most wants to find, and they find
  it by sorting — not because the model was told to be harsher.

## Predicates

All are developer-authored constants (parent D16: `questions` are never sanitized or
rewritten). Each carries an `evidence` tag from the parent's `EVIDENCE_LEVELS`; all
start `untested`.

### Waiver request — `state`

```json
{
  "limit":  {"name": "...", "metric_kind": "...", "unit": "...",
             "scope_type": "underlying", "scope_label": "..."},
  "breach": {"severity": "breach", "utilization": 1.18, "days_open": 3},
  "waiver": {"rationale": "...", "duration_days": 12}
}
```

`days_open = floor((event.created_at − incident.first_seen_at) / 86400 s)`;
`duration_days = ceil((payload.waiver_expires_at − event.created_at) / 86400 s)`.
`severity` and `utilization` come from the evaluation **as of the event, not as of
scoring** (D6): the latest `limit_incident_events` row of this incident with a non-null
`evaluation_id` and `created_at <= event.created_at` — `reconcile_monitoring_incidents`
stamps the evaluation on every open / escalate / update event, so the trail exists.
`incident.last_evaluation_id` is NOT used: it keeps moving after the waiver, and a late
retry would grade the rationale against a breach its author never saw. Unresolvable ⇒
both `null`. **Every number is computed by code and only read by Jev.** `rationale` is capped at `review_rationale_chars`
(4000); a longer one is `unscored:state_too_large` — the client never truncates, and a
silently clipped rationale would be graded as if its remediation clause were missing.

### `rationale_grade` — `score`, 5 ordered levels

| Level | Situation |
|---|---|
| 0 | No reason is given: the text is filler, restates that the limit is breached, or only says the waiver was requested or approved |
| 1 | A cause is asserted with nothing checkable — "market move", "temporary", "known issue" — and no position, trade, event or date is named |
| 2 | A specific cause is named (a position, trade, market event or data problem), but nothing says what will bring the exposure back inside the limit |
| 3 | A specific cause and a specific remediation are named, but with no owner or date — or the remediation lands after the waiver expires |
| 4 | A specific cause, a specific remediation, who will do it, and a date on or before the waiver's expiry |

Stored: `rationale_grade = answer.normalized` ∈ [0, 1] and `rationale_confidence`. The
UI renders the nearest level (`round(normalized × 4)`) with its short label. Level 3's
second clause is the one a skimming reader misses: it compares a date in the text with
`duration_days` in the state.

### Claims — six `noul`, each "The rationale claims that …"

| Key | Predicate | Checker (D2) |
|---|---|---|
| `position_rolling_off` | …exposure will fall on its own because a position expires, matures, knocks out or settles | yes |
| `data_error` | …the breach comes from a wrong, stale or missing market input or a failed risk run, not from real exposure | yes |
| `limit_under_review` | …the limit itself is mis-sized or is being changed | yes |
| `hedge_in_progress` | …a hedge or unwind trade has been ordered or is being executed | no → `unverified` |
| `client_flow_expected` | …an expected client trade or unwind will reduce the exposure | no → `unverified` |
| `market_reversion` | …the exposure will return inside the limit because the market will move back | no → `unverified` |

### `authority_only` — `noul`

*"The rationale's only justification is that a trader, a manager or another person asked
for or approved the waiver."* This is the failure `risk-limit-breach-day` step 5 is
built to provoke, so the arena corpus should hold positives.

### Thread request — `thread_state`, `choice`

`state = {limit: {name, scope_label}, incident: {severity, status, days_open},
comments: [{at, actor, text}, …]}` — the incident's `commented` events up to and
including the scored one, oldest first, **the last `review_thread_comments` (20), each
cut to `review_comment_chars` (600)**. Selection is deterministic; a cut comment ends
with `…` so the clip is visible to the reader of a stored state.

| Option | Description |
|---|---|
| `disputes_number` | The desk says the breach figure is wrong. The figure is contested; **no error has been confirmed** |
| `remediating` | The desk accepts the breach and describes action taken or under way |
| `requests_limit_change` | The desk argues the limit should change rather than the exposure |
| `requests_more_time` | The desk asks for a waiver or more time without describing remediation |
| `root_cause_only` | The comments explain why the breach happened and say nothing about what happens next |
| `no_position` | The comments are administrative (assignment, FYI); none takes a position |

Stored: `thread_state` (argmax) and `thread_state_p`. Below `review_choice_min_p` (0.50)
the row is `unscored:low_confidence`, matching the parent's confirmations site.

### Thresholds

Every `review_*` value in this document is a module constant in `limits/review.py`
(the parent's `MemoryConfig` precedent); only the feature switch is a `Settings` field.

`review_chip_min_p = 0.70` decides which `noul`s render as chips and which claims get a
check run. **It is provisional and has no evidence behind it** — it sits between the
parent's HIGH (0.71–0.96) and low (0.03–0.11) bands on unrelated predicates. Because
`answers_json` keeps every raw probability, changing it is a config edit and a re-render,
never a re-score.

## Architecture

```
backend/app/services/limits/
  review.py          # QUESTIONS, build_waiver_state(), build_thread_state(),
                     # score_event(), enqueue(), sweep()
  review_checks.py   # CHECKERS registry + the three checkers
```

### Flow

1. `incidents.waive()` / `incidents.comment()` are **unchanged**. They flush an event
   inside the caller's transaction.
2. **Fast path.** After the caller commits, the two call sites — the REST routes
   (`routers/limits.py`) and the agent tools (`tools/limits.py`) — call
   `review.enqueue(event_id, kind)`. It returns immediately if the feature is not live
   or the event's thread is an arena thread; otherwise it hands `score_event` to
   `task_runner.submit_async_task`. **The worker opens its own `Session` and re-reads
   the event by id** — it never touches the request's objects (the repo's known
   worker-thread `Session` trap).
3. **`score_event`** builds state from the event (D6), calls `system_one.client.ask()`
   — the only exit, which sanitizes and budgets — then, for each claim with
   p ≥ `review_chip_min_p`, runs its checker in the same read-only session, and writes
   the row by **insert-or-select on the unique `(event_id, kind)` key** (insert in a
   savepoint; on `IntegrityError` load the existing row and update it only if it is
   `unscored`). A `scored` row is never overwritten.
4. **Sweep.** At the end of each limit-monitoring run, after
   `reconcile_monitoring_incidents`, `review.sweep()` (a) scores up to
   `review_batch` (10) due events (D9 / D10), ordered never-attempted first, then
   `attempted_at ASC, id ASC`, so a failing row cannot starve the rest; and
   (b) recomputes checks for reviews whose incident is still `waived` (D12). It runs in
   the monitoring task's own thread and session, after that run's work has committed,
   and is wrapped so that **no exception in it can fail a monitoring run**.
5. **Circuit breaker — outage reasons only**, copied from the parent's keep-alive:
   `no_key`, `timeout`, `http_error` end the batch (one failed request per sweep, one log
   line); `state_too_large`, `bad_response`, `low_confidence` are facts about that row —
   it is stamped and the batch continues.

### Checkers (`review_checks.py`)

`CHECKERS: dict[str, Callable[[Session, LimitIncident, LimitIncidentEvent], ClaimCheck]]`
where `ClaimCheck = {check, detail, checked_at}`. Read-only. `detail` is one sentence
built from database facts, never from the rationale.

- **`position_rolling_off`** — positions in the incident's scope (`portfolio_id` +
  `scope_type` / `scope_key` over the four scope types `portfolio`, `underlying`,
  `product_family`, `position`) whose expiry / maturity falls in
  `[event.created_at, waiver_expires_at]`. ≥ 1 ⇒ `supported`, detail
  *"2 positions in scope expire on or before 2026-10-03"*; none ⇒ `no_evidence`.
  **Scope membership must not be written twice.** Today the rule lives in the private
  `sources._risk_rows_for_scope`, which matches risk-run ROWS. The plan's first step for
  this checker is to read it and either reuse it or lift its scope predicate into a
  shared helper both callers use; a second, drifting definition of "in scope" would make
  the check disagree with the breach it is checking.
- **`data_error`** — the evaluations this incident's events reference (`evaluation_id`
  non-null) up to the scored event, plus `first_evaluation_id`: any with a
  `reason_code`, a `coverage_ratio < 1.0`, or a freshness violation recorded in
  `evidence` against the version's `freshness_policy` ⇒ `supported`; else `no_evidence`.
  The exact `evidence` keys are read from `evaluator.py` in the plan, not guessed here.
- **`limit_under_review`** — a `RiskLimitVersion` of the same `risk_limit_id` with
  `created_at > incident.first_seen_at` (any state) ⇒ `supported`; else `no_evidence`.

A checker that raises yields `{check: "unverified", detail: "check failed"}` and a log
line; it never fails the review.

## Data model & migration

`limit_incident_reviews`:

| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `incident_id` | FK `limit_incidents.id`, indexed | |
| `event_id` | FK `limit_incident_events.id` | |
| `kind` | `VARCHAR(12)` | `waiver` \| `thread` |
| `status` | `VARCHAR(10)` | `scored` \| `unscored` |
| `unscored_reason` | `VARCHAR(40)` NULL | parent D17 vocabulary + `low_confidence` |
| `rationale_grade`, `rationale_confidence` | Float NULL | `waiver` rows |
| `authority_only_p` | Float NULL | `waiver` rows |
| `thread_state` | `VARCHAR(32)` NULL, `thread_state_p` Float NULL | `thread` rows |
| `claims_json` | JSON, default `[]` | `[{claim, p, check, detail, checked_at}]` |
| `answers_json` | JSON, default `{}` | every raw answer, for re-thresholding |
| `model` | `VARCHAR(160)` NULL | |
| `latency_ms` | Integer NULL | |
| `attempted_at` | DateTime NULL | rotation key |
| `created_at` | DateTime, indexed | |

`UNIQUE (event_id, kind)`. `event_id` is `NOT NULL`, so the NULL-distinct trap the parent
documents for `(thread_id, tool_call_id)` does not arise.

One migration, **the next free revision after the System One chain (`0064` if that
chain lands as `0061`–`0063`)**: idempotent (`_tables()` guard — `0001` materialises
today's ORM, so a fresh chain already has the table), migration-local Core tables, no
ORM imports. Downgrade drops the table. The ORM model declares the same unique
constraint so the `create_all` path and the migration path agree.

Arena purge: fixture incidents are purged after a board. D14 means no review row should
reference one; the purge still deletes `limit_incident_reviews` by `incident_id` before
the incidents, so a desk that scored one by some other path cannot wedge a purge on a
foreign key.

## Failure handling

| Failure | Behaviour |
|---|---|
| Master or feature switch OFF | inert — no call, no row, UI shows `—` |
| Switch ON, no `ZENMUX_API_KEY` | `unscored:no_key` row; sweep batch ends |
| Timeout / HTTP error | `unscored:<reason>` row; batch ends; next sweep retries |
| Bad response | `unscored:bad_response`; batch continues |
| Rationale or thread over budget | `unscored:state_too_large`; batch continues; never truncated |
| `thread_state` argmax < 0.50 | `unscored:low_confidence` |
| Event on an arena thread | never enqueued, never due |
| Enqueue forgotten or process died mid-score | no row ⇒ still due ⇒ next sweep scores it (D9) |
| Two workers score the same event | unique key; the loser loads the winner's row |
| Checker raises | that claim `unverified`, review still written |
| Any other exception | caught, logged, `unscored:internal_error`; **a waive, a comment or a monitoring run can never fail because of a review** |

## Surfaces

- **API.** `LimitIncidentOut` gains
  `reviews: {waiver: LimitIncidentReviewOut | None, thread: LimitIncidentReviewOut | None}`
  — the latest row of each kind. `LimitIncidentReviewOut` names every served field
  explicitly. No new route. All three swallowing layers are covered on purpose: the
  pydantic `response_model`, the router's projection, and the TypeScript type — and the
  new fields are **asserted at the HTTP layer**, not only in a store test.
- **Limits page → Breaches tab.** `IncidentDetail`: beside *Waiver rationale*, a grade
  chip (`2/4 · cause, no remediation`), one chip per claim at or above the threshold with
  its check state and `detail` as the tooltip, and an `authority only` warning chip; on
  the timeline header, a thread-state chip. The incident list gains one fixed-width
  sortable `Rationale` column so the weakest waivers sort to the top. `null` renders `—`;
  an `unscored` row renders its reason, muted. Token-only styling per `frontend/CLAUDE.md`.
- **Agent tools.** Unchanged, and pinned so (D13).
- **`README.md`.** Beside `OPEN_OTC_LIMIT_REVIEW`: exactly what is sent — waiver
  rationale and comment text, limit name / metric / unit / scope label, breach severity,
  utilization, days open, waiver duration.

## Testing

- Conftest already hard-sets `OPEN_OTC_SYSTEM_ONE=false` and fails any real post; tests
  inject fakes from `tests/_system_one_fakes.py`.
- `review.py`: state is built from the event, not the incident (re-waive, then score the
  first event); day arithmetic; caps; insert-or-select under a simulated race; a `scored`
  row is never overwritten; rotation order; the outage breaker ends a batch and a row
  reason does not; D10 (only the latest comment is due); arena threads are skipped.
- `review_checks.py`: each checker `supported` / `no_evidence` against rows the test
  books through the real services, not hand-built model instances; a raising checker
  degrades to `unverified`.
- Inert-when-off: with either switch off, a waive and a comment produce zero rows and
  zero client calls.
- Isolation: a client that raises inside the worker leaves the waive committed; a sweep
  that raises leaves the monitoring run `completed`.
- HTTP: `GET /limit-incidents/{id}` serves `reviews` with every field; the agent tool
  payloads do not contain the key (D13).
- Migration: targets this migration, asks alembic for the head, relies on
  `test_migration_fresh_chain.py` for idempotency.
- Enumerate exact-set pins before starting (`grep -rln "limit_incidents" tests/`). No new
  agent tool, skill or route is added, so the four-registration rule and the
  skill-catalog pins are not in play; the settings test gains one variable.

### Evidence probe (not on any app path)

`scripts/limit_review_probe.py`, run by hand with a real key:

1. **Counts** the corpus: `waive_limit_incident` and `comment_limit_incident` calls in
   `risk-limit-breach-day` matches in the arena trace DB, read-only.
2. Scores each through the same `QUESTIONS`, and prints distributions split by the
   step-5 outcome the scorer already recorded.
3. Scores a **committed hand-written fixture** of ~15 rationales spanning the five
   levels and the six claims, including two-claim rationales and an authority-only one —
   so a wording change can be re-run against the same cases.

Its output is direction, never a rate: model-written text, one workflow's policy, tiny N.
A predicate moves from `untested` to `tested-posthoc` only by an edit that cites a probe
run; nothing here can reach a held-out tag until the desk has written real waivers.

## Rollout

1. **Wait for the System One branch to merge.** This feature needs its client and must
   chain its migration after that branch's last revision; branching earlier would create
   two alembic heads.
2. Own worktree, per the repo's one-worktree-per-session rule.
3. Run the evidence probe first; adjust wording before any UI work if a predicate is
   plainly dead.
4. Ship display-only behind the two switches. `CHANGELOG.md`, `README.md`, a *Limit
   incident review* section in `backend/app/services/system_one/CLAUDE.md`.

## Out of scope

- The audit retrospective sweep (own spec, after the guard's Phase B).
- A `hedge_in_progress` checker, and checkers for the other two unverifiable claims.
- Any action on a review: blocking or re-promoting a waiver, feeding the tool guard,
  reordering a queue, notifying anyone.
- Per-desk overrides of the ladder or the claim list. The wording is a code constant in
  v1; making it configurable waits until a second desk disagrees with it.
- Exposing reviews to the agent, in any tool or skill.
- Grading `RiskLimitVersion.rationale` (the limit-change rationale) — same shape, a
  different policy, no demand yet.
- A manual re-score button or endpoint; an `unscored` row is retried by the sweep.

## Open risks

- **Every predicate is `untested`, and the only corpus is model-written.** Agents write
  fluent, specific-sounding rationales; a ladder tuned on them may grade a terse human
  "rolling Dec CSI500, done Fri — LW" too low. The fixture must include terse human-style
  cases.
- **The sweep rides on monitoring runs.** A desk that never runs limit monitoring never
  retries an `unscored` row. Acceptable for v1: without monitoring runs there are no
  incidents either.
- **A fluent rationale can earn level 4 and be false.** The grade measures whether the
  text is complete, not whether it is true. The claim checks are the only part that
  touches truth, and v1 covers three claims of six.
- **`scope_label` and free text may carry client or underlying names** to a new third
  party. Parent D15 covers consent and `ask()` masks secrets, but names are not secrets;
  the README entry has to say so plainly.
