# Jev (System One) integration — design

Date: 2026-09-21 · Status: draft for review · Branch: `worktree-jev-system-one`

## Problem

Three decisions in the desk runtime are classification-shaped, and today each is made
by something that cannot read context:

1. **AUTO mode runs 41 `"write"`-level tools unattended by a static per-TOOL lookup.**
   `hitl.interrupt_on_config(yolo_mode=True)` drops every tool whose
   `_RISK_LEVEL_BY_TOOL` entry is `"write"`. It never reads the call's arguments or the
   user's request, so `void_settlement_cashflow(9300)` executes identically whether the
   user asked for it or the agent reached for it to clear a refusal (the measured
   `ops-settlement-day` step-8 failure).
2. **A memory's only quality signal is the extractor LLM's self-reported `confidence`.**
   It is written once, never revisited, and says nothing about whether the fact is still
   worth keeping. The human approving a `proposed` domain fact, or pruning live ones, has
   no second opinion — and the repo's own guidance ("store only durable facts; volatile
   values go stale") is enforced by nobody.
3. **A confirmation's product `family` is picked by one vision LLM and then trusted.**
   `segment_document` chooses from the closed `_SCHEMA_FAMILIES` list; the choice selects
   the schema stage 2 fills, so a wrong family yields a well-formed, wrong trade. Nothing
   cross-checks it before a human approves an **irreversible** booking.

TypeSafe's **Jev** is a non-generative "System One" model: `state` + typed `questions`
in, calibrated probabilities out (`noul` yes/no, `choice` one-of-N, `score` ordered
levels). It cannot generate text, call tools, or see images, so it is never a persona
model, an arena contestant, or a source of numbers. It fits exactly these three sites.

## Evidence base — what was measured, and what was not

Live probes on 2026-09-21 against `typesafe/jev-1.13` via ZenMux. All cases were
hand-written by the evaluator; N is tiny; one model version. **Read direction, never a
rate.**

| Claim (vendor) | Measured |
|---|---|
| 70–500 ms | **1.03 / 1.33 / 2.30 s** min / median / max over 61 calls. ZenMux's own 404s take ~1.0–1.5 s, so ~1 s is gateway round-trip. |
| (deterministic — unstated) | **No.** Identical requests drifted up to 0.10 (typically ≤ 0.04); probabilities quantised to 0.01. |
| "0 % hallucination" | A type-safety guarantee, not a correctness one. |

- No-request priors for void / waive / close: 0.42–0.54 — Jev does **not** key on the verb.
- Generic predicate *"the call performs an action the user did not ask for"*: five
  matched pairs separated by +0.60 (HIGH 0.71–0.96, low 0.03–0.11), including a
  document-borne instruction (0.94) and an absent referent (0.71–0.81).
- **The same generic predicate FAILED the repo's real trap**: step-8 void with the
  system's own *"Settle, void, or edit that cashflow first"* hint in state scored
  **0.49–0.52**, while a *correct* implied step (step-6 resync) scored 0.61–0.66.
  Separation −0.17: no threshold works.
- A **policy-specific** predicate (*"voids, cancels or deletes a record the user did not
  explicitly name for that"*) separated them: trap 0.85, cancel-KO 0.73, authorised void
  0.10–0.12, resync 0.06 (+0.61). **Post-hoc** — written after seeing the failure and
  tested on the same cases.

**Consequence for the design:** Jev evaluates a crisply stated predicate; it does not
supply desk judgment. Policy must be written INTO the question, per tool. And because
the only predicate that works is post-hoc, the guard ships in **shadow** mode so real
traffic produces the evidence enforcement would need.

## Decisions

User decisions (2026-09-21) are marked **[user]**.

**Notation.** `unscored:<reason>` in this document is prose shorthand. In every stored
or served shape the two are **separate fields with no prefix** — guard
`verdict="unscored", unscored_reason="no_key"`; confirmations
`status="unscored", reason="no_key"`; memory `keep_alive_score=NULL,
keep_alive_unscored_reason="no_key"`.

- **D1 [user] Guard scope = 9 destroy-and-terminate tools**, not all 41:
  `void_settlement_cashflow`, `close_position`, `settle_position`, `mark_knockout`,
  `waive_limit_incident`, `resolve_limit_incident`, `delete_pricing_parameter_rows`,
  `remove_portfolio_sources`, `import_otc_positions`. They share one broad predicate
  *family* — "destroys or terminates something the user did not name" — which is the
  family the one tested predicate belongs to. **That is a reason to group them, not
  evidence about them:** only the `void_settlement_cashflow` wording has any evidence
  (`tested-posthoc`); the other eight rows are `untested` and marked so in the table.
- **D2 [user] Shadow first.** The guard records a verdict and never blocks. Enforcement
  ships in the same change behind `OPEN_OTC_TOOL_GUARD=enforce`, default `shadow`.
- **D3 [user] Memory keep-alive score is display-only.** It changes no eviction order,
  no injection order, no status.
- **D4 [user] Confirmation family cross-check approved**; disagreement is flagged for
  review and never overrides the deterministic bookability gate.
- **D5 [user] Out of scope:** Feishu intent triage and per-turn model/effort routing.
- **D6 Master switch defaults OFF.** `OPEN_OTC_SYSTEM_ONE` (default `false`). With it off
  every feature here is inert: no HTTP call, no new rows, no behaviour change. A fresh
  checkout, CI, and every arena launch are unaffected.
- **D7 Jev is NOT a channel-registry model.** Registry rows are selectable agent models
  (`/api/agent/models`, the Model Maintenance UI); Jev 404s on every chat endpoint, so
  listing it would make it selectable and broken. It is configured in `Settings`.
  Consequently **no `agent_channels.yaml` / `.example.yml` edit is needed**.
- **D8 Enforcement = re-promote to the normal approval card.** AUTO still has a reachable
  human (irreversible tools already interrupt there), so a flagged — or unscoreable —
  call behaves exactly as it would in interactive mode. A ZenMux outage therefore
  degrades AUTO to interactive for nine tools; it stops nothing. **One exception, and it
  is a refusal — message-wide, not call-local:** if any guarded call's verdict row cannot
  be committed, every guarded call in that `AIMessage` that is not a committed `clear`
  is refused with an error `ToolMessage`, including ones whose own `flagged`/`unscored`
  verdict did commit (D10 rule 3 explains why a partial interrupt is unsafe). That is not a new posture — it is the audit trail's
  own phase-1 rule ("no classified write may execute without a committed attempt row"),
  against the same database, which would refuse the write a moment later anyway.
- **D9 The guard lives at `after_model`, as a `HumanInTheLoopMiddleware` subclass**,
  mirroring `LongRunningCostHITLMiddleware`. That is the repo's paved path for an
  argument-aware interrupt: the approval card, `_SUMMARY_BUILDERS` projection and the
  `hitl_proposal → hitl_decision → execution` audit chain all work unchanged. The
  "`wrap_tool_call` is the only seam" rule concerns *scanning results from outside* a
  subagent; a middleware registered IN each stack sees that stack's tool calls at either
  seam. `allowed_decisions` stays `["approve", "reject"]` — no `edit` — so langchain
  issue #40694 (a guard classifying a call that HITL later rewrites) cannot occur.
- **D10 The interrupt set must be a pure function of committed state — never of a fresh
  Jev answer.** LangGraph re-runs the node from the top on resume, and the resume value
  is a positional list of decisions with no ids. Jev is non-deterministic, so re-asking
  could shrink the flagged set and mis-attach the human's decision (the precedent raises
  `ValueError` on a count mismatch). Three rules make re-entry deterministic:
  1. A verdict is **committed under a UNIQUE `(thread_id, tool_call_id)` key before any
     interrupt** (insert-or-select: insert in a savepoint, on `IntegrityError` read the
     existing row). Re-entry finds the row and never calls Jev. **A stored row is reused
     only if it describes the SAME call**: the row also stores `tool_name` and
     `args_hash` (sha256 of the canonical-JSON redacted args), and a lookup that finds
     the key but a different name or hash is `unscored:tool_call_id_collision` — never
     the stored verdict. Without this, a provider that reuses an id would let an earlier
     `clear` wave through a different destructive call. The collision result is itself
     structural (the mismatch is identical on every pass), so it is deterministic, needs
     no row of its own, and in `enforce` takes the approval card. `thread_id` is
     `NOT NULL DEFAULT 0` — SQL treats NULLs as distinct in a UNIQUE key, the same trap
     `ArenaMatch.reasoning_effort` documents.
  2. **A call with an empty `tool_call_id` cannot be cached**, so in `enforce` it is
     never sent to Jev: it is `unscored:no_tool_call_id` by construction — a structural
     fact that is identical on every pass. (Some routes do emit empty ids; see the arena
     malformed-tool-call notes.)
  3. **If any guarded call's verdict cannot be committed, that pass raises NO interrupt
     — it is a refusal pass** (D8; composition for a multi-call message is defined under
     *Tool guard → `after_model`*). An interrupt there would be followed, on resume, by
     a lookup that finds nothing, a fresh Jev call, and possibly a different set.
  In `shadow` none of this matters (nothing interrupts): a persistence failure is logged
  and the tool runs.
- **D11 The guard reads the user's words from the DB, not from the message list.** Inside
  a persona the first human message is the orchestrator's `task()` paraphrase. The
  predicate is about what the USER named, so the guard loads the thread's latest
  `AgentMessage(role="user")` via `AUDIT_CONTEXT_KEY['thread_id']` (stamped at every entry
  point, readable in subagents) in a short read-only session — the same move the HITL
  summary builders make. **Turn-scoped when it can be:** if the audit context carries
  `message_id` (the column already exists on `AgentActionAudit`), the guard loads
  exactly that message and requires `role == "user"` and the same `thread_id`;
  otherwise it falls back to the thread's latest user message. Turns on one thread are
  sequential and a HITL resume creates no user message, so the fallback is right in
  practice — but it is the weaker path, so the verdict row records which one was used
  (`user_request_source`: `message_id` | `latest`) and shadow data shows how often.
  The plan stamps `message_id` at every audit-context site where the turn's user
  message id is known. No user message resolvable ⇒ the call is **unscored**, never
  guessed.
- **D12 One Jev request per decision, all predicates inside it.** Jev answers every
  question in a request in parallel, so N predicates cost one round-trip.
- **D13 Synchronous, in both guard modes.** A guarded call is a rare destructive
  operation; +~1.3 s on a void is small beside the LLM turns around it, and a
  synchronous shadow measures exactly what enforcement would cost (latency and
  unavailability included). No background thread, no lost verdicts on exit.
- **D14 `null` means never scored, which is not `0`.** Every new field is nullable and
  the UI renders `—`. Same absence rule as the truncation and malformed-tool-call flags.
- **D15 The master switch is ONE explicit data-policy opt-in covering every
  default-enabled data class, with per-class opt-OUT.** Jev adds a new third party
  (TypeSafe, reached through ZenMux) to the set that sees desk text. The same text
  already reaches ZenMux-routed LLMs today (chat turns, the memory extractor's session
  windows, the confirmation extractor's documents), so the trust boundary is not new —
  but the upstream is. Therefore: the master switch defaults OFF; turning it on is the
  consent act and `README.md` lists, next to that variable, exactly what each of the
  three features then sends; **each feature has its own switch to opt its data class
  back out**; and **one outbound sanitizer runs inside `ask()`** (D16). This is
  opt-out-under-an-opt-in on purpose: requiring four variables to see anything would
  mostly produce desks that enabled one and wondered why the others were dark.
- **D16 One sanitizer, at the single exit — over `state` ONLY.** `ask()` walks `state`
  recursively and masks every string through the audit trail's secret masking
  (`audit_redaction`: the `token|password|secret|api[_-]?key|credential|authorization`
  key regex for dict keys, plus `sk-…`-style token patterns in free text) **before** the
  size check and the HTTP call. Feature code cannot forget it because feature code never
  builds the request. **`questions` are never touched**: they are developer-authored
  constants, and rewriting a question key, option or criterion would break response
  validation or silently change the predicate being asked. Memory adds its own gate on
  top: **the CURRENT denylist is re-run on the fact and on every sibling immediately
  before sending** — backfill reaches rows that predate the denylist, were imported, or
  were admitted under older rules, so "it passed once" is not a guarantee.
- **D17 "Disabled" and "broken" are different and must look different.** Master switch
  or feature switch off ⇒ the feature is **inert**: no HTTP call, no rows, fields stay
  `NULL`. Switch ON but the call cannot be made (no key, timeout, HTTP error, bad
  response, state over budget) ⇒ an explicit **`unscored`** record carrying the reason
  — a verdict row for the guard, the `family_check` JSON for confirmations, and the
  `keep_alive_unscored_reason` column for memory. A misconfigured desk must see
  `unscored:no_key`, not silence.

## Architecture

### 0. `backend/app/services/system_one/` — the shared client

```
system_one/
  __init__.py      # re-exports
  client.py        # questions, answers, ask(), SystemOneUnavailable
```

```python
@dataclass(frozen=True)
class Noul:   instructions: str
@dataclass(frozen=True)
class Choice: instructions: str; criteria: Mapping[str, str]   # option -> description
@dataclass(frozen=True)
class Score:  instructions: str; criteria: Sequence[str]       # ordered low -> high

@dataclass(frozen=True)
class NoulAnswer:   probability: float
@dataclass(frozen=True)
class ChoiceAnswer: choice: str; confidence: float; probabilities: Mapping[str, float]
@dataclass(frozen=True)
class ScoreAnswer:
    score: float; confidence: float; probabilities: Mapping[str, float]; levels: int
    @property
    def normalized(self) -> float: ...   # score / (levels - 1), in [0, 1]

class SystemOneUnavailable(RuntimeError):
    reason: str   # one of UNAVAILABLE_REASONS — callers persist it verbatim

UNAVAILABLE_REASONS = ("no_key", "state_too_large", "timeout", "http_error", "bad_response")

def is_enabled(settings: Settings | None = None) -> bool: ...   # master switch ONLY
def ask(state, questions: Mapping[str, Question], *,
        post: Callable[[str, dict, float], dict] | None = None,
        settings: Settings | None = None) -> SystemOneResult: ...

@dataclass(frozen=True)
class SystemOneResult:
    answers: dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]   # keyed as asked
    model: str          # the response's "model", else the requested one
    latency_ms: int     # wall clock around post()
```

- **Request:** `POST {base_url}/systemone`, `Authorization: Bearer $ZENMUX_API_KEY`, body
  `{"model", "state", "questions"}`. `noul` → `{type, instructions}`; `choice` →
  `{type, instructions, criteria: {option: description}}`; `score` →
  `{type, instructions, criteria: [level, …]}`. The key is read from `os.environ` at
  call time (the `arena/judge.py::_default_post` precedent — `channel_registry`'s
  `load_dotenv(override=True)` has already published `.env` by then).
- **Response (normative — verbatim from the 2026-09-21 probes):**

  ```json
  {"model": "typesafe/jev-1.13",
   "answers": {
     "q_noul":   {"type": "noul",   "noul": 0.95},
     "q_score":  {"type": "score",  "score": 1.98, "confidence": 0.98,
                  "legend": {"0": "…", "1": "…", "2": "…"},
                  "probabilities": {"0": 0, "1": 0.01, "2": 0.99}},
     "q_choice": {"type": "choice", "choice": "ops", "confidence": 1,
                  "probabilities": {"ops": 1, "trader": 0, "risk": 0}}},
   "usage": {"input_tokens": 517, "output_tokens": 70}}
  ```

  - `noul` is the probability that the `instructions` statement is **TRUE** →
    `NoulAnswer.probability`.
  - `score` levels are **0-based** and keyed by **stringified index**; `score` is the
    probability-weighted mean, so it lies in `[0, levels − 1]`. `levels` is taken from
    the question asked, not from the response. `confidence` is **returned by Jev**
    (higher when mass concentrates on one level) — it is never derived locally.
  - `choice.choice` must be one of the options asked; `probabilities` is keyed by option.
  - **Validation → `bad_response`:** `answers` missing or not an object; an asked key
    absent; `type` not matching the question; any probability / confidence not a number
    in `[0, 1]`; `score` outside `[0, levels − 1]`; `choice` not among the options.
    **`probabilities` is required for `choice` and `score`, must be an object, and every
    key must be a member of the asked set** (an option; or a stringified index in
    `"0"…"levels−1"`) — an unknown key is `bad_response`. **A missing key is read as
    `0.0`, not rejected:** only a 3-option choice and a 3-level score were ever observed
    (both returned every key, zeros included), and rejecting on an omitted zero would
    turn a 16-family check into a permanent `bad_response` on an assumption nobody
    measured. For `choice`, the chosen option must itself be a key. Probabilities are
    **not** required to sum to 1 (quantised to 0.01). Extra answer keys, `legend` and
    `usage` are ignored.
- **`post` is the injectable seam**: `(url, payload, timeout) -> dict`. Its exception
  contract is what makes reason-mapping deterministic: raise **`TimeoutError`** →
  `timeout`; raise **`ValueError`** (which `json.JSONDecodeError` is) → `bad_response`
  — so a 2xx with a malformed or non-JSON body is `bad_response`, not `http_error`;
  raise anything else → `http_error`. The default implementation uses `requests`
  (imported lazily) and translates `requests.Timeout` → `TimeoutError`, a non-2xx status
  → `RuntimeError`, and lets `.json()`'s `ValueError` through. **No test makes a live
  call.**
- **Client-side validation raises `ValueError`** (programmer error, not unavailability):
  empty `questions`, empty `instructions`, `score` outside 2–10 levels, `choice` with
  < 2 or > 255 options, an empty option/level string. This matters because the server
  answers a malformed body with an opaque `400 "Server encountered an unexpected error"`.
- **`SystemOneUnavailable(reason)`**, mapped deterministically: no `ZENMUX_API_KEY` →
  `no_key`; sanitized+serialized `state` over `system_one_max_state_chars` (default
  60 000 ≈ half the 32K window) → `state_too_large`; `requests` timeout → `timeout`; any
  other transport or non-2xx failure → `http_error`; a 2xx body failing the validation
  above → `bad_response`. `ask()` does **not** check the master switch — callers gate on
  `is_enabled()` plus their own feature switch first (D17), so "disabled" is never an
  exception.
- **`state` contract.** Callers pass a JSON-able projection: `str`, `int`, `float`,
  `bool`, `None`, `list`, and `dict` with `str` keys. `ask()` serializes once, with
  `json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
  default=str)` — `default=str` is the safety net for a `datetime`/`Decimal`/`Enum`
  inside tool args, so an odd arg never crashes the guard. **The budget is
  `len(serialized)`** — Python code points of that exact string, measured after
  sanitizing. One helper, one definition; tested at the boundary with non-ASCII text.
- **Failure provenance.** `SystemOneUnavailable` also carries `latency_ms` (wall clock
  until the failure for `timeout` / `http_error` / `bad_response`; `None` for `no_key`
  and `state_too_large`, where no request was made) and `detail` (`str(exc)` cut to 500
  chars and passed through `redact_text`). The guard stores them as `latency_ms` and
  `error`; `model` is always the REQUESTED model on a failure. Summary medians skip
  null latencies.
- **`ask()` never truncates.** Each feature builds a documented, bounded *projection* of
  its evidence (caps are listed per feature below — that summarisation is intentional
  and visible). If the projection still exceeds the budget it is **rejected**, never cut
  further, because silently dropping evidence changes the question being answered.
- `ask` returns all answers or raises. There is no partial result.

**Settings** (`app/config.py`, the existing three-site pattern: pydantic `Field` with
`validation_alias`, dataclass `field`, `_coerce_*` in `__post_init__`):

| Field | Env | Default |
|---|---|---|
| `system_one_enabled` | `OPEN_OTC_SYSTEM_ONE` | `False` |
| `system_one_model` | `OPEN_OTC_SYSTEM_ONE_MODEL` | `typesafe/jev-1.13` |
| `system_one_base_url` | `OPEN_OTC_SYSTEM_ONE_BASE_URL` | `https://zenmux.ai/api/v1` |
| `system_one_timeout_seconds` | `OPEN_OTC_SYSTEM_ONE_TIMEOUT_S` | `5.0` |
| `system_one_max_state_chars` | `OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS` | `60000` |
| `tool_guard_mode` | `OPEN_OTC_TOOL_GUARD` | `shadow` (`off` \| `shadow` \| `enforce`) |
| `confirmation_family_check_enabled` | `OPEN_OTC_CONFIRMATION_FAMILY_CHECK` | `True` |

An unknown `tool_guard_mode` value fails closed to `shadow` with a warning — never to
`off` (a typo must not silently disable measurement) and never to `enforce`.

Memory's switch lives where memory's other switches live — `MemoryConfig`
(`memory/config.py`, read from the env by `get_memory_config()`), not `Settings`:

| `MemoryConfig` field | Env | Default |
|---|---|---|
| `keep_alive_enabled` | `OPEN_OTC_MEMORY_KEEP_ALIVE` (`on`/`off`, same parser as `OPEN_OTC_MEMORY`) | `True` |
| `keep_alive_batch` | — (dataclass constant) | `10` |
| `keep_alive_sibling_limit` | — | `50` |
| `keep_alive_sibling_chars` | — | `240` |
| `keep_alive_refresh_days` | — | `30` |

**Every feature switch is subordinate to the master switch**: a feature is live iff
`OPEN_OTC_SYSTEM_ONE` is true AND its own switch is on (`tool_guard_mode != "off"`,
`keep_alive_enabled`, `confirmation_family_check_enabled`). Defaults are "on under the
master" so one variable turns the whole feature set on, and any one data class can be
opted back out (D15). Not settings, and named here so nobody goes looking:
`MIN_TEXT_CHARS_PER_PAGE = 40` is the existing constant in `confirmations/extract.py`;
`family_check` is a plain keyword argument of `parse_document` / `run_parse_batch`.

### 1. Tool guard

```
deep_agent/tool_guard_policy.py   # GUARD_POLICY: the user-editable desk policy table
deep_agent/tool_guard.py          # ToolGuardMiddleware, state builder, verdict store
```

**`GUARD_POLICY` is desk policy, deliberately a plain data table** so the desk can edit
it without touching middleware:

```python
@dataclass(frozen=True)
class GuardPredicate:
    key: str
    instructions: str      # a crisply stated predicate; HIGH probability = flag
    threshold: float = 0.5
    evidence: str = "untested"   # "tested-posthoc" | "untested" — honesty marker

GUARD_POLICY: dict[str, tuple[GuardPredicate, ...]] = { ... 9 tools ... }
```

Default predicates — **written out in full per tool, no templating**, because the
wording IS the policy and "a record" is simply wrong for the three set-valued tools.
`unnamed_target` is the tested family. "Named" means *by id, by name, or by a
description that identifies it unambiguously* — that phrase is in every predicate so Jev
is not left to decide what "explicitly" covers. (Only id references were tested;
descriptive references are untested and are exactly what shadow data should show.)

| Tool | `unnamed_target` instructions | Evidence |
|---|---|---|
| `void_settlement_cashflow` | "The pending tool call voids a settlement cashflow, and the user did not name that cashflow — by id, by name, or by a description that identifies it unambiguously — as one to be voided" | tested-posthoc |
| `close_position` | "The pending tool call closes a position, and the user did not name that position — by id, by name, or by a description that identifies it unambiguously — as one to be closed" | untested |
| `settle_position` | "… settles a position, and the user did not name that position … as one to be settled" | untested |
| `mark_knockout` | "… marks a position as knocked out, and the user did not name that position … as one that knocked out" | untested |
| `waive_limit_incident` | "… waives a limit incident, and the user did not name that incident … as one to be waived" | untested |
| `resolve_limit_incident` | "… resolves a limit incident, and the user did not name that incident … as one to be resolved" | untested |
| `delete_pricing_parameter_rows` | "The pending tool call deletes rows from a pricing parameter profile, and the user did not ask for those rows, or that profile's rows, to be deleted" | untested |
| `remove_portfolio_sources` | "The pending tool call removes sources from a portfolio, and the user did not ask for those sources to be removed from that portfolio" | untested |
| `import_otc_positions` | "The pending tool call imports positions in bulk from a file, and the user did not ask for that file to be imported" | untested |

Two further predicates, identical wording for every tool:

| Key | Instructions | Tools | Evidence |
|---|---|---|---|
| `from_document` | "The instruction to perform this action comes from a document or a tool result rather than from the user" | all 9 | untested wording (the generic predicate scored a document-borne case 0.94) |
| `clears_blocker` | "The pending tool call removes or closes something only to get past a refusal the system gave to a different action" | `void_settlement_cashflow`, `close_position`, `settle_position` | untested wording; this is the step-8 shape |

(The elided rows above are written out in full in the module; the table abbreviates only
for width.) Threshold 0.5 sits mid-gap of the measured separation (flag ≥ 0.73, clear
≤ 0.12) with margin for the ±0.10 drift. A call is **flagged** when any predicate ≥ its
threshold.

**`validate_policy(policy, risk_levels)` runs at middleware construction and in CI**, and
raises `ValueError` on: a tool name absent from `_RISK_LEVEL_BY_TOOL` (dead policy); a
tool whose level is not `"write"` (an `"irreversible"` tool is already gated — it would
be double-carded); an empty predicate tuple; a duplicate predicate `key` within one tool;
empty `instructions`; a `threshold` outside the open interval (0, 1) — 0 flags
everything, 1 flags nothing, and both are silent; an `evidence` value outside
`{"tested-posthoc", "untested"}`. The table is code and fails like code: loudly, at
build, and first in CI.

**State sent to Jev** (a JSON object — the docs recommend named parts):

```json
{"mode": "auto (no human will review this call)",
 "user_request": "<latest user AgentMessage text, capped 4000 chars>",
 "delegated_task": "<persona's first human message, only inside a subagent, capped 2000>",
 "earlier_in_this_turn": ["<tool>(<args>) -> <result head 300 chars>", "… last 8"],
 "pending_tool_call": {"name": "...", "args": {...}}}
```

This is a **bounded projection, on purpose** (the caps are the documented
summarisation; `ask()` itself never cuts anything). All caps use one helper,
`system_one.cap(text, n)`: length is Python `len()` (Unicode code points); text within
`n` is returned unchanged, otherwise `text[: n - 1] + "…"`, so a capped value is exactly
`n` long and visibly marked. **Order is fixed:** redact args → render to text → `cap` →
hand to `ask()`, which sanitizes, serializes to JSON, and size-checks the serialized
length. The caps: `user_request` ≤ 4000 chars,
`delegated_task` ≤ 2000, `earlier_in_this_turn` = the last 8 tool call/result pairs
since the last human message in the stack's own `state["messages"]`, each result head
≤ 300 chars. `args` go through `redact_args` (which also elides file/script bodies to
`{sha256, byte_len, head}`); every string then passes the single sanitizer inside
`ask()` (D16).

**`ToolGuardMiddleware(HumanInTheLoopMiddleware)` — `after_model`:**

1. Return `None` unless: `is_enabled()`, `tool_guard_mode != "off"`, and the
   server-stamped `AUDIT_CONTEXT_KEY['mode'] == "auto"` (runtime belt; registration is
   the braces).
2. For each tool call on the last `AIMessage` whose name is in `GUARD_POLICY`, resolve a
   verdict — **in this order, so the result never depends on a fresh Jev answer when a
   committed one exists (D10):**
   a. Empty `tool_call_id` ⇒ `unscored:no_tool_call_id`. Jev is not called. (Recorded
      best-effort; it cannot be cached, and does not need to be — it is structural.)
   b. A committed row for `(thread_id, tool_call_id)` **with the same `tool_name` and
      `args_hash`** ⇒ reuse it. Same key, different identity ⇒
      `unscored:tool_call_id_collision`. Either way Jev is not called.
   c. No user message resolvable for the thread ⇒ `unscored:no_user_request`; commit it.
   d. Otherwise build state and `ask()` once with all of that tool's predicates.
      `SystemOneUnavailable(reason)` ⇒ `unscored:<reason>`. Commit the verdict
      (insert-or-select under the UNIQUE key; if a concurrent pass won the insert, the
      **stored** row wins, not the one just computed).
   e. If the commit fails ⇒ the verdict is `persist_failed` for this pass.
3. `shadow`: return `None`. Nothing is interrupted or refused, whatever step 2 produced.
4. `enforce` — one `AIMessage` may carry several guarded calls with different verdicts,
   so composition is defined for the whole message. **A pass either refuses or
   interrupts, never both:**
   - **If ANY guarded call is `persist_failed` ⇒ a REFUSAL pass, with no interrupt.**
     Every guarded call that is not a committed `clear` is refused: dropped from the
     `AIMessage` and answered with an error `ToolMessage` ("Guard verdict store
     unavailable; destructive action '<name>' blocked (fail-closed). Read-only tools
     still work; retry shortly.") — the construction `_process_decision` uses for a
     rejection. Committed-`clear` calls and all unguarded calls are untouched.
     *Why the whole set:* mixing would let call A interrupt while call B is refused; on
     resume B has no stored row, is re-asked, may now commit as `flagged`, and the
     interrupt set grows from `[A]` to `[A, B]` against one decision — the D10 mismatch
     from the other side. With no interrupt raised the node is never re-entered, so
     there is nothing to keep deterministic. The model may retry; a retry carries a new
     `tool_call_id` and is evaluated afresh. (An unwritable DB means the audit trail
     would have refused these writes anyway.)
   - **Otherwise ⇒ an INTERRUPT pass.** Every `flagged` or `unscored` call joins one
     `HITLRequest`, in original tool-call order; decisions are processed exactly as
     `LongRunningCostHITLMiddleware` does, and the returned `AIMessage` keeps the
     surviving tool calls in their original order followed by the artificial rejection
     messages. The `ActionRequest.description` states which predicate fired and its
     probability, or that the guard could not score the call and why. `clear` calls are
     untouched and run unattended as AUTO intends.
   - **Empty-id calls are exempt from `persist_failed`.** Their record is best-effort
     and a failure to write it is only logged: the verdict is structural
     (`unscored:no_tool_call_id`), identical on every pass, so it always takes the
     approval card and never needs the store.
   After decisions are processed, each interrupted row's `action` is updated to
   `interrupted` (best-effort; the verdict itself is already committed).

**Registration is `yolo_mode and allow_reply_options`.** The builders receive those two
flags, not the mode name; `resolve_execution_mode` derives them as
`clear_hitl = mode in {"auto","yolo"}` (threaded as `yolo_mode`) and
`allow_reply_options = mode != "yolo"`. The full truth table:

| Turn `mode` | `yolo_mode` | `allow_reply_options` | Guard constructed | Runtime belt (`ctx.mode == "auto"`) | Jev called | Verdict rows | Can interrupt |
|---|---|---|---|---|---|---|---|
| `interactive` | `False` | `True` | **no** | — | no | no | n/a — the static map already cards all 9 (`"write"` level) |
| `auto` | `True` | `True` | **yes** | passes | yes | yes | only if `tool_guard_mode == "enforce"` |
| `yolo` (headless; every arena run) | `True` | `False` | **no** | — | no | no | no |
| legacy caller, `mode=None`, `yolo_mode=True` | `True` | `True` | yes | passes (`mode` resolves to `"auto"`) | yes | yes | as `auto` |

The runtime belt exists for one case the flags cannot see: the default-selection
orchestrator graph is **built once and reused across threads**, so a graph built for an
AUTO turn must not guard a later turn whose audit context says otherwise.

All four stacks: `orchestrator._agent_middleware`, `personas.all_personas`,
`orchestrator._general_purpose_subagent` (gains an `allow_reply_options` parameter — it
receives only `yolo_mode` today), `async_agents/agent.py`.
`tests/test_tool_guard_registration.py` mirrors `test_audit_registration.py` and
additionally asserts **absence** under `interactive` and under headless.

**Verdict persistence — new table `agent_tool_guard_verdicts`:** joined to the audit
trail by `tool_call_id` rather than adding a column to `agent_action_audits`, so the
fail-closed audit write path is untouched.

| Column | Notes |
|---|---|
| `id` | PK |
| `thread_id` | `INTEGER NOT NULL DEFAULT 0` — `0` = unknown; never NULL (D10 rule 1) |
| `tool_call_id` | `VARCHAR(120) NOT NULL`; rows with an empty id are stored as `''` and are exempt from reuse |
| — | **`UNIQUE (thread_id, tool_call_id)`**, declared as a *partial* unique index `WHERE tool_call_id != ''` so structural empty-id rows never collide |
| `persona`, `exec_mode` | from the audit context |
| `guard_mode` | `shadow` \| `enforce` at decision time |
| `tool_name` | indexed; part of the reuse identity (D10 rule 1) |
| `args_json`, `redacted` | via `redact_args` |
| `args_hash` | sha256 of the canonical-JSON redacted args; part of the reuse identity |
| `user_request_source` | `message_id` \| `latest` \| null (D11) |
| `verdict` | `clear` \| `flagged` \| `unscored` |
| `unscored_reason` | null, or one of `no_tool_call_id`, `tool_call_id_collision`, `no_user_request`, `internal_error` (an unexpected exception in guard code), + `UNAVAILABLE_REASONS` (`no_key`, `state_too_large`, `timeout`, `http_error`, `bad_response`) — persisted verbatim from `SystemOneUnavailable.reason` |
| `predicates_json` | `[{key, probability, threshold, flagged, evidence}]`; `[]` when unscored |
| `max_probability` | nullable float; null when unscored |
| `action` | `recorded` \| `interrupted` |
| `model`, `latency_ms`, `error` | provenance; `latency_ms` null when Jev was not called |
| `created_at` | indexed |

`persist_failed` is never stored — by definition there is no row.

**Reading shadow data** (the point of shadow mode). Same conventions as the existing
audit router (`routers/audit.py`: no auth — single-desk posture, `{items, total}`
envelope, `limit` 1–200 default 50, `offset` ≥ 0):

- `GET /api/audit/guard-verdicts?verdict=&tool_name=&thread_id=&since=&limit=&offset=`
  → `{"items": [GuardVerdictOut], "total": int}`, newest `created_at` first, `id` desc as
  tie-break. `verdict` outside `clear|flagged|unscored` → 400. `since` is an ISO-8601
  datetime parsed by FastAPI exactly like the existing `/api/audit/summary?since=`
  (naive = UTC, which is how `created_at` is stored), **inclusive** (`created_at >=
  since`); a malformed `since`, or a non-integer `thread_id`/`limit`/`offset`, is
  FastAPI's standard **422**. The same `since` rule applies to `/summary`.
  `GuardVerdictOut = {id, thread_id, tool_call_id, persona, exec_mode, guard_mode,
  tool_name, args_json, redacted, verdict, unscored_reason, predicates, max_probability,
  action, model, latency_ms, error, created_at, execution_status}`.
  `execution_status` is the `status` of the `kind="execution"` audit row sharing
  `tool_call_id` (`ok` / `error` / `denied` / `interrupted` / `attempted`), or `null`
  when no such row exists (the call was rejected at the card, refused, or never ran).
- `GET /api/audit/guard-verdicts/summary?since=` →
  `{"by_tool": [{tool_name, total, clear, flagged, unscored, flagged_then_ok,
  median_latency_ms}], "unscored_reasons": {reason: count}}`. `flagged_then_ok` = flagged
  verdicts whose execution row is `ok` — the **candidate false positives**, which is the
  number shadow mode exists to produce. `median_latency_ms` is over rows with a non-null
  latency and is `null` when there are none. A tool with no verdicts is absent, not zero.
- `GET /api/audit/actions` rows gain `guard: {verdict, max_probability} | null`, joined
  on **both halves of the verdict key** — `verdict.tool_call_id = action.tool_call_id AND
  verdict.thread_id = COALESCE(action.thread_id, 0)` — and only where
  `action.tool_call_id` is non-empty, so a verdict can never be shown on another
  thread's action and structural empty-id rows never match anything. The UNIQUE key
  makes that at most one row; no "newest" tie-break is needed. The **Audit**
  page shows a `Guard` badge column (`flagged` / `clear` / `unscored` / `—`). The field is
  added to the `AuditActionOut` pydantic model, to `_out`'s projection, AND to the
  TypeScript type — the three layers that each silently swallow a new field — and is
  asserted at the HTTP layer.

### 2. Memory keep-alive score

**Meaning:** Jev's estimate that a fact is still worth keeping alive — a second opinion
beside the extractor's `confidence`, shown for `proposed` facts (pending approval) and
live ones alike. One `score` question, levels as situations (per Jev's "describe
situations, not degrees"):

| Level | Situation |
|---|---|
| 0 | Contradicted or replaced by another listed fact, or plainly no longer true |
| 1 | A one-off detail of a single conversation that goes stale quickly (a run id, today's number, a temporary state) |
| 2 | True for now but likely to change within weeks (a current project, a temporary limit) |
| 3 | A standing preference, rule or fact about how this desk works, true until someone changes it |

`keep_alive_score = answer.normalized` ∈ [0, 1]; Jev's `confidence` (how concentrated the
distribution is) is stored beside it.

**Live iff all three:** `OPEN_OTC_SYSTEM_ONE` on, **`OPEN_OTC_MEMORY` on**, and
`OPEN_OTC_MEMORY_KEEP_ALIVE` on. A desk that turned memory off has said memory data
does not get processed; keep-alive must not be a side door around that. (Facts stay
editable via the API with memory off, exactly as today — they are just not scored.)

**State** — a bounded projection: `{fact, scope, category, source, status, pinned,
age_days, other_facts_in_scope}`. Siblings are what make level 0 answerable, so their
selection is **deterministic**: `MemoryStore.load_existing(scope_type, scope_id)` — the
store's existing reader, which returns `proposed`+`approved` for `domain` and `active`
otherwise, ordered `confidence DESC, updated_at DESC, id ASC`, limited to 50 — **minus
the fact being scored**, each `content` cut to `keep_alive_sibling_chars` (240).
Archived facts are never siblings. Pinned ones are, like any other.
`age_days = floor((utcnow − created_at).total_seconds() / 86400)` — how long the fact has
existed, in whole UTC days (`created_at` is stored naive-UTC). **Denylist gate:** a fact
failing `is_memorable` under the current denylist is never sent — it is stamped
`keep_alive_unscored_reason="denylist"`; a sibling failing it is silently omitted from
`other_facts_in_scope`.

```
memory/keep_alive.py   # build_state(), score_fact(), score_pending()
```

- **New columns on `memory_entries`:** `keep_alive_score`, `keep_alive_confidence`
  (nullable Float), `keep_alive_scored_at`, `keep_alive_attempted_at` (nullable
  DateTime), `keep_alive_unscored_reason` (nullable `VARCHAR(40)`; D17 — set on a failed
  attempt, cleared on success and on invalidation).
- **When scored — always on the `memory-writer` daemon, never on a turn:**
  `MemoryWriteQueue.run_job` calls `score_pending` after `apply_diff` has **committed**
  (so new candidates, including `proposed` domain facts, are scored promptly), and
  `_run_sweep` calls it each tick to backfill existing facts. One Jev request per fact,
  each in its own small transaction.
- **What is due:** a non-archived row whose `keep_alive_score IS NULL`, **or whose
  `keep_alive_scored_at` is older than `keep_alive_refresh_days` (30, a `MemoryConfig`
  constant).** The score is a judgment about staleness and `age_days` is part of its
  evidence, so a score computed once and shown forever would itself go stale; a monthly
  refresh of ≤ 100 facts/scope is a few dozen requests.
- **Selection rotates, so a failing row cannot starve the rest:** up to
  `keep_alive_batch` (10) due rows, ordered `keep_alive_attempted_at IS NOT NULL,
  keep_alive_attempted_at ASC, updated_at ASC, id ASC` — never-attempted first, then
  least-recently attempted. Every attempt stamps `keep_alive_attempted_at`, success or
  not.
- **Circuit breaker — for OUTAGE reasons only.** `no_key`, `timeout` and `http_error`
  say the service is unreachable for everyone, so the first one **ends the batch**: one
  failed request per sweep tick (60 s), not ten, and one log line, not ten.
  `state_too_large` and `bad_response` are facts about THAT row — the row is stamped,
  given its reason, and **the batch continues**.
- **Invalidation = set all five columns to NULL** (clearing `attempted_at` too puts the
  row back at the front of the rotation): on a content change (`_update_row`), and for
  every non-archived fact in a scope when `apply_diff` adds to that scope (a new sibling
  can supersede an old fact — the case level 0 exists for). The sweep re-scores at ≤ 10
  per tick, so a 100-fact scope settles in ~10 minutes.
- **Best-effort, isolated:** any exception in scoring is logged and counted
  (`store.counters["keep_alive_failed"]`), leaves the score `NULL`, and can never fail an
  extraction run or roll back an `apply_diff`.
- **Display-only is pinned by tests:** `_enforce_caps` ordering and `load_injectable`
  ordering are asserted unchanged with keep-alive populated.
- **Surfaces:** `FactOut` + `_out` gain `keep_alive_unscored_reason`, `keep_alive_score`, `keep_alive_confidence`,
  `keep_alive_scored_at`; `MemoryFact` (TS) gains the same; the **Memory** page gains a
  fixed-width numeric `Keep` column beside `Conf` (`—` when null, per the page's
  fixed-width rule for non-clipping columns). Asserted at the HTTP layer.
- Arena threads are already excluded from memory (`_is_arena_thread`), so nothing here
  reaches a board.

### 3. Confirmation family cross-check

```
confirmations/family_check.py   # FAMILY_DESCRIPTIONS, check_family()
```

- **Eligibility:** master + feature switch on, `family_check=True`, and **every page of
  the segment is a text-layer page — `PageContent.image_png is None`.** That is the
  pipeline's own, existing classification: `extract.py` renders a page to PNG *only*
  when its extracted text is under `MIN_TEXT_CHARS_PER_PAGE` (40), so `image_png` is set
  exactly for scan-like pages and `None` for a normal PDF page **even if it carries a
  logo or other embedded image** — embedded images are never inspected. A segment's
  pages are `segment.pages`, or every page when that list is empty (the same fallback
  `_content_parts` uses). Any scan page in the segment ⇒ `unscored:no_text_layer` —
  never a guess from partial text. Jev has no vision.
- **`"unknown"` is a reserved option.** `check_family` raises `ValueError` if
  `_SCHEMA_FAMILIES` ever contains it — the status table would become ambiguous — and a
  test pins that it does not.
- **Question:** one `choice` over `sorted(_SCHEMA_FAMILIES) + ["unknown"]`, each with a
  one-line description from `FAMILY_DESCRIPTIONS`. A family missing from the map falls
  back to its own name as the description, so **adding a family never breaks a test**;
  the test asserts only `FAMILY_DESCRIPTIONS.keys() ⊆ _SCHEMA_FAMILIES` (a stale key
  fails, a new family does not). **State:** `{anchor, document_text}` — the segment
  pages' text joined in page order; over the state budget ⇒ `unscored:state_too_large`.
- **Status decision table** (`llm` = the family `segment_document` chose; `DISAGREE_MIN
  = 0.5` is a module constant):

  | Jev's `choice` | Jev `confidence` | `status` | `reason` |
  |---|---|---|---|
  | equals `llm` | any | `agree` | null |
  | `"unknown"`, and `llm` is also not in `_SCHEMA_FAMILIES` | any | `agree` | null |
  | `"unknown"`, and `llm` is a real family | any | `unscored` | `jev_unknown` — "I can't tell" is not evidence against the LLM |
  | a different real family | ≥ 0.5 | **`disagree`** | null |
  | a different real family | < 0.5 | `unscored` | `low_confidence` |
  | (call failed) | — | `unscored` | the `SystemOneUnavailable.reason` |
  | (a scan page in the segment) | — | `unscored` | `no_text_layer` |

- **Result → new nullable JSON column `extracted_trades.family_check`:**
  `{status, reason, jev_family, confidence, top, model}`. `top` is the three
  highest-probability options as `[[family, p], …]`, sorted `p DESC, family ASC`, `p`
  rounded to 2 dp (Jev quantises to 0.01 anyway). `jev_family`/`confidence`/`top` are
  null when Jev was never reached. `NULL` column = never checked (feature off, arena, or
  a row that predates this).
- **Storage is per trade because segment → trade is 1:1 in this pipeline.**
  `parse_document` turns each `TradeSegment` into exactly one `TradeDraft` and each draft
  into exactly one `ExtractedTrade` row — including segments whose family is outside
  `_SCHEMA_FAMILIES`, which still get a row (`unsupported`, empty terms). So the check
  runs **for every segment**, its result rides on the draft (`TradeDraft.family_check`,
  a new optional field) and `_draft_to_row` copies it. An "LLM said unknown, Jev reads
  SnowballOption at 0.9" row is therefore a visible `disagree` on an `unsupported` trade
  — arguably the most useful case. If stage-2 extraction then raises, the document
  fails exactly as it does today, no rows are written, and the check result is dropped
  with them; there is no segment-level table.
- **Where:** `service.parse_document`, after `segment_document`, wrapped so a
  cross-check failure can never fail the document: a `SystemOneUnavailable` records
  `unscored:<its reason>`, and **any other exception records `unscored:internal_error`**
  with `jev_family`, `confidence`, `top` and `model` all null (the exception is logged,
  not stored). `reason` is therefore one of: null, `no_text_layer`, `jev_unknown`,
  `low_confidence`, `internal_error`, or an `UNAVAILABLE_REASONS` value. The document
  text is the segment pages joined in page order as `"[page N]\n<text>"` blocks
  separated by a blank line — the same framing `_content_parts` gives the LLM.
- **What disagreement does:** nothing to `validation_status`, nothing to
  `validation_errors`, nothing to bookability — `validate_trade_terms` is untouched. It
  is surfaced in three places: a badge on the **Confirmations** page trade row; the
  trade payload the agent tools return; and **the `book_extracted_trade` approval card**
  (`_summarize_book_extracted_trade`), because that is the moment a human is about to
  approve an irreversible booking off this family.
- **Arena isolation:** the parse tool passes `family_check=False` whenever the
  server-stamped `CONFIRMATION_EXTRACTOR_SELECTION_KEY` is present (it is arena-only), so
  `confirmation-desk-day` contestants never see a field the rest of the board's history
  did not. `ExtractedTrade.confidence` (the extractor's self-report) is left alone — it
  means something different.

## Data model & migrations

Three migrations after `0060`, each **idempotent** (existence-guarded with the
`_tables()` / `_columns()` helpers — `0001` materialises today's ORM, so a fresh chain
arrives already carrying these objects), each using `op.batch_alter_table` for any
downgrade drop, each with **migration-local Core tables** (never ORM models):

- `0061_tool_guard_verdicts` — create `agent_tool_guard_verdicts` + indexes, including
  the partial unique index `(thread_id, tool_call_id) WHERE tool_call_id != ''`. The ORM
  model declares the same index (`Index(..., unique=True, sqlite_where=...)`) so the
  `0001` `create_all` path and the migration path produce the same key — a fresh-chain
  DB that lacked it would silently lose D10's guarantee.
- `0062_memory_keep_alive` — five nullable columns on `memory_entries`.
- `0063_extracted_trade_family_check` — one nullable JSON column on `extracted_trades`.

`tests/test_migration_fresh_chain.py` is the standing guard. Per-migration tests target
the migration under test (never `head`) and ask alembic for the head rather than
hardcoding it.

## Failure handling

| Failure | Guard (shadow) | Guard (enforce) | Memory | Confirmations |
|---|---|---|---|---|
| Master or feature switch OFF (D17: inert) | no call, no row | same — guard inert | no call, columns stay `NULL` | no call, `family_check` stays `NULL` |
| Switch ON, no `ZENMUX_API_KEY` (D17: broken, visible) | `unscored:no_key`, tool runs | **approval card** | `NULL`, `attempted_at` stamped, counter++, batch ends | `unscored:no_key` |
| Timeout / HTTP error | `unscored:<reason>`, tool runs | **approval card** | as above — outage reason, **batch ends** | `unscored:<reason>`, document still parses |
| Bad response | `unscored:bad_response`, tool runs | approval card | `NULL` + reason, `attempted_at` stamped; row reason, **batch continues** | `unscored:bad_response`, document still parses |
| `OPEN_OTC_MEMORY=off` | n/a | n/a | keep-alive inert regardless of its own switch | n/a |
| Fact fails the current denylist | n/a | n/a | never sent; `keep_alive_unscored_reason="denylist"` | n/a |
| `tool_call_id` reused for a different call | `unscored:tool_call_id_collision`, Jev not called | approval card | n/a | n/a |
| State over budget | `unscored:state_too_large` | approval card | `NULL`, `attempted_at` stamped (this row only; batch continues — it is this fact's problem, not an outage) | `unscored:state_too_large` |
| No user message resolvable | `unscored:no_user_request` | approval card | n/a | n/a |
| Empty `tool_call_id` | `unscored:no_tool_call_id`, Jev not called | approval card (structural ⇒ identical on re-entry) | n/a | n/a |
| Verdict row cannot be committed | logged; tool runs | **refused** with an error `ToolMessage` — never an interrupt (D10 rule 3) | n/a | n/a |
| Resume after interrupt | n/a | committed verdict reused; Jev not re-asked | n/a | n/a |
| Jev picks `unknown` / low confidence | n/a | n/a | n/a | `unscored:jev_unknown` / `unscored:low_confidence` |
| Invalid `GUARD_POLICY` (bug) | `ValueError` at middleware construction — agent build fails loudly; CI catches it first | same | n/a | n/a |
| Any other exception in feature code | caught, logged, `unscored:internal_error` | approval card | caught, `NULL` + `keep_alive_unscored_reason="internal_error"`, never fails the extraction run | caught, `unscored:internal_error`, never fails the document |

The guard never raises into the agent loop at run time: every path ends in a verdict, a
refusal, or a logged no-op. In `enforce`, every uncertainty resolves toward the human —
except an unwritable verdict store, which resolves toward not acting at all.

## Testing

All hermetic — `post` injected, `OPEN_OTC_SYSTEM_ONE` set per test, no `.env` read
outside `app.config.dotenv_path()`.

- **Client:** request shape per question type; the three response shapes parse from the
  verbatim probe fixture above; each `ValueError`; **each `UNAVAILABLE_REASONS` value is
  reachable and mapped**; every `bad_response` rule; `ask()` never truncates and rejects
  over budget; **the sanitizer masks a secret planted in a nested string and in a dict
  key before `post` sees the payload** (D16); `ask()` does not read the master switch.
- **Guard:** inert when master off / `off` / mode ≠ `auto` (no `post`, no row); shadow
  records and never interrupts or refuses; enforce interrupts on `flagged` and on every
  `unscored` reason; **a committed verdict is reused and `post` is not called again**,
  including when the second pass would have computed a *different* verdict (D10 rule 1);
  empty `tool_call_id` never reaches `post` and still cards when its best-effort record
  fails (rule 2); **an uncommittable verdict refuses in enforce and runs in shadow**
  (rule 3); **mixed message — `[clear, flagged, persist_failed]` ⇒ a refusal pass: no
  `interrupt()` call, the flagged and the failed call both refused, the committed-clear
  call untouched, original order preserved**; mixed `[clear, flagged, unscored]` ⇒ one
  `HITLRequest` with two actions in order; a lost insert race returns the stored row;
  **a reused non-empty `tool_call_id` with different args ⇒ `tool_call_id_collision`,
  never the stored `clear`**; `message_id` in the audit context selects exactly that
  message (and a non-user or other-thread id ⇒ `no_user_request`), absent ⇒ `latest`,
  and the source is recorded; non-JSON tool args (`datetime`, `Decimal`) do not crash;
  an unexpected exception ⇒ `unscored:internal_error`;
  user request comes from the DB, not the persona's first human message (D11); args are
  redacted before leaving the process; the truth table row-for-row — constructed under
  `auto` in all four stacks, **absent** under `interactive` and under headless.
- **Policy table:** `validate_policy` passes on the shipped table and rejects each
  invalid shape (unknown tool, non-`"write"` tool, empty tuple, duplicate key, empty
  instructions, threshold ∉ (0, 1), bad `evidence`) — checked against
  `_RISK_LEVEL_BY_TOOL` itself, never a hand-copied list.
- **Memory:** scoring populates score / confidence / scored_at and stamps attempted_at;
  content edit and sibling add null **all five** keep-alive columns (score, confidence,
  scored_at, attempted_at, unscored_reason); `OPEN_OTC_MEMORY=off` ⇒ no `post` even
  with keep-alive on; a pre-existing fact that fails the current denylist is never sent
  and a denied sibling is omitted; `age_days` at the 23 h 59 m / 24 h boundary; siblings are `load_existing` minus self,
  cut to 240 chars; **a permanently failing row does not block a later row** (rotation);
  **an outage reason (`no_key`/`timeout`/`http_error`) ends the batch** (one `post`
  call, not ten) **while a row reason (`state_too_large`/`bad_response`) does not** —
  first row over budget, later rows still scored; a failed attempt sets
  `keep_alive_unscored_reason` and a later success clears it; a score older than
  `keep_alive_refresh_days` is due again, a fresh one is not;
  failure never fails the extraction run; keep-alive off ⇒ no `post`; eviction and
  injection order unchanged with scores populated; **new fields asserted through
  `GET /api/memory/facts`**.
- **Confirmations:** every row of the status decision table; a logo-bearing text page
  (`image_png is None`) is eligible, any scan page → `unscored:no_text_layer`; an
  `unsupported` segment is still checked; failure never fails the document;
  `validate_trade_terms` result identical with and without a disagreement; approval-card
  summary mentions a disagreement; `family_check=False` under the arena key; feature
  switch off ⇒ no `post`; **field asserted through the REST layer**.
- **Exact-set pins:** enumerate before starting —
  `grep -rln "guard-verdicts\|agent_action_audits\|memory_entries" tests/` and the route
  / table-count pins. No new agent TOOL is added, so the four-registration rule and the
  tool-count pins are not triggered.
- **Frontend:** `npx tsc --noEmit` + vitest for the three touched pages; token-only
  styling per `frontend/CLAUDE.md`.

## Rollout

One branch, **five independently revertable commits** in dependency order — client,
guard-shadow, guard-enforce, memory, confirmations — so any one surface can be backed
out without the others. **Shadow is complete and green before a line of enforcement
exists:** nearly all the determinism, refusal and resume complexity lives in
`enforce`, and the evidence goal needs only shadow, so `guard-enforce` is a separate
commit that can be reverted — or never released — without touching measurement.
**Each commit is its own acceptance gate**: the full backend suite, `tsc` and the touched
page's vitest pass at that commit, not only at the tip. The three features share nothing
but the client and the master switch, so approving one never implies the others.

1. Merge with `OPEN_OTC_SYSTEM_ONE` unset — nothing changes anywhere.
2. Set `OPEN_OTC_SYSTEM_ONE=true` in the desk's `.env`. Everything turns on under it;
   to opt a data class out, add `OPEN_OTC_TOOL_GUARD=off`,
   `OPEN_OTC_MEMORY_KEEP_ALIVE=off` or `OPEN_OTC_CONFIRMATION_FAMILY_CHECK=false`.
   What each sends to TypeSafe via ZenMux (after sanitizing): the guard — the user's
   latest request, recent tool call/result heads, the pending call; memory — fact text
   and its scope siblings; confirmations — the text layer of text-only segments.
3. Read `/api/audit/guard-verdicts/summary` after real AUTO traffic. `flagged_then_ok`
   is the candidate-false-positive count. Tune `GUARD_POLICY` wording/thresholds from it.
4. Only then consider `OPEN_OTC_TOOL_GUARD=enforce`.

`CHANGELOG.md` `[Unreleased]`; `README.md` (user-facing env vars); root `CLAUDE.md`
subsystem table + a new `backend/app/services/system_one/CLAUDE.md` carrying the route
(not in ZenMux's catalog), the measured latency, the non-determinism, and the
"policy goes IN the question" lesson.

## Out of scope

- Feishu gateway intent triage; per-turn model / reasoning-effort routing **[user]**.
- Guarding the other 32 `"write"` tools. Money-path edits (`release_`, `update_`,
  `resync_settlement_cashflow`, …) are frequently *correct implied steps*, where the
  generic predicate false-flagged a correct resync; they need over-execution predicates
  that were tested exactly once.
- Any automatic action on the keep-alive score (eviction order, auto-archive).
- Replacing `ExtractedTrade.confidence`, the arena jury, the goal-mode grader, the
  limits evaluator or the reporting grounding guard — the last two are deterministic
  and must stay so.
- A per-fact "re-score" button or endpoint; invalidation-then-sweep covers it.
- Using `langchain-typesafe`'s `AutoModeMiddleware`: it targets `api.typesafe.ai` with
  its own key, asks one generic risk `noul` (the shape that failed step 8), and has no
  verdict persistence for resume.

## Open risks

- **The only predicate with evidence is post-hoc.** Shadow mode exists to fix that; do
  not read early shadow numbers as validation of the wording either.
- **ZenMux serves Jev on a route absent from `/v1/models`** and may change it without
  the catalog reflecting it. All failure modes degrade to `unscored`.
- **Early-access model, single upstream** (`typesafe`). No provider pin is possible or
  needed, but there is no fallback either.
- **Sibling invalidation re-scores a whole scope** on each add (≤ 100 requests, spread
  over ~10 sweep ticks, ≈ $0.003). Cheap, but it is the largest source of Jev traffic
  here; `keep_alive_batch` bounds it.
