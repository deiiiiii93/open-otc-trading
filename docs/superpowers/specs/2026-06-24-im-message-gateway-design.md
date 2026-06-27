# IM Message Gateway — Design Spec

**Date:** 2026-06-24
**Status:** Draft (feature-flow stage 1)
**Topic:** `im-message-gateway`

## Problem

Desk users can only reach the OTC trading agent through the web UI. We want them to
drive the agent — query positions/risk/pricing, run scenarios, and **book/quote/approve
with full parity** — from the instant-messaging tools they already live in (Feishu/Lark
first, with five more platforms to follow).

The agent's safety and audit guarantees (capability gate, envelope scoping, per-tool HITL
interrupts, immutable audit ledger) must remain **unchanged and unbypassable**. IM becomes
a third front-end (alongside web and async agents) behind the same gates — not a new,
weaker path to irreversible actions like `book_position` or `release_rfq`.

This pattern is adapted from ByteDance DeerFlow's "message gateway" pillar. DeerFlow runs
IM channels in-process inside its FastAPI gateway and routes them through an internal
LangGraph SDK client; it binds IM accounts via a `/connect` linking-code flow keyed by
`(provider, external_account_id, workspace_id)` with single-active-owner transfer. We adopt
that *philosophy* but two things differ deliberately: (1) DeerFlow ships essentially **no
interactive HITL approval buttons** over IM — we require them, because full parity includes
irreversible bookings; (2) DeerFlow's internal client is an HTTP LangGraph SDK because its
agent is a LangGraph server — **our repo has no such SDK**, so our clean seam is the
*service layer*, not self-HTTP.

### Authorization model — explicit assumption

The current web path calls `record_audit(..., actor="desk_user", ...)` with a **hardcoded
string**: the app today is effectively single-desk-principal and has **no per-user RBAC**.
This spec does **not** introduce an RBAC system (that would be scope creep and would
over-claim a capability the web app lacks). "Authorization parity" therefore means:
IM actions traverse the **identical envelope (`DESK_WORKFLOW`) and identical per-tool HITL
gates** as web — no new privilege path, and no weaker one. What the gateway *adds* is
**identity capture**: the bound desk identity is threaded as an explicit `actor`
parameter into the audit ledger and the resume/submit service functions, replacing the
hardcoded `"desk_user"` string for IM-originated actions. The `gateway_binding.desk_user`
column is a stable identifier ready to become a real user FK if/when the web app grows
per-user principals; building that is out of scope.

**`desk_user` / `issued_by` source (v1):** because the web app has no per-user principal,
both `gateway_linking_code.desk_user` and `issued_by` are set to the configured constant
`settings.gateway_default_desk_user` (default `"desk_user"`). The linking-code request body
does **not** carry `desk_user`; it is derived server-side. The column exists so that when
web RBAC arrives, the issuing principal flows in without a schema change. Persona is the
only caller-supplied field on the issue request and is validated against the known persona
set.

## Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Platform coverage | A connector protocol **validated against Feishu**, built end-to-end now; the other five (Slack, Telegram, WeChat, WeCom, DingTalk) extend the protocol in their own later specs | Avoids baking in unverified provider assumptions (callback/edit/identity/workspace models differ); the protocol stays minimal + capability-flagged |
| 2 | IM capability scope | **HITL/write parity** for the listed desk workflows (see Parity Matrix), with explicit v1 exclusions (model-selection override, page-context tools, group chat, accounting-date override, approve-all) | "Full parity" made testable by the matrix; exclusions enumerated so it is unambiguous |
| 3 | Integration architecture | **Approach C** — in-process subsystem behind a single `AgentBridge` facade over the service layer | Faithful translation of DeerFlow's "one clean internal client" to our codebase (we have no LangGraph SDK; the service layer is the seam). Reuses checkpointer/DB/audit/HITL directly; avoids re-streaming over self-HTTP (B) and preserves a testable boundary (vs. raw A) |
| 4 | Identity binding | **Linking-code enrollment**; identity keyed by `(provider, external_account_id, workspace_id)`; single-active-owner transfer; revocable | Provable identity is mandatory before chat may book irreversible trades |
| 5 | Thread mapping | **One agent thread per `(binding, chat_ref)`** | IM context persists across messages in a chat, like a web thread; no cross-chat bleed |
| 6 | Unbound users | **Hard refuse** — enrollment instructions only; nothing reaches the agent | Full parity demands provable identity; no anonymous desk-data exposure |
| 7 | Chat type (v1) | **Direct messages only**; group chats are refused with an explanatory message | Group chats complicate approver identity and reply visibility; deferred to a later spec |
| 8 | Test posture | **Fully mockable** — `FakeConnector` vertical slice + Feishu adapter unit tests; no live creds to merge | Decouples landing the feature from provisioning a real Feishu app |
| 9 | Audit actor | Every IM-originated turn/action records `actor = bound desk_user` via existing `record_audit` | IM actions are first-class ledger entries with zero ledger schema changes |
| 10 | Streaming | Feishu **edit-card-in-place** (typewriter); connectors lacking the capability fall back to a single final send | Matches DeerFlow's Feishu technique; good UX for minutes-long tasks |
| 11 | Deployment | **Single gateway worker**, enforced by a **single-row sentinel lease** (`gateway_worker_lock`) on the app's SQLite DB | DeerFlow-identical; one concrete portable mechanism (not a separate advisory-lock concept); stale-owner expiry by DB time |

## Architecture

New package `backend/app/services/gateway/`. New DB tables (one Alembic migration using
migration-local Core tables per project rule). Everything else reuses existing services.
Term **"gateway"** (not "channel") avoids collision with the existing *model-channel*
registry (`_channel_registry`, `/api/agent/channels/*`).

```
gateway/
  __init__.py
  runtime.py        # GatewayRuntime: lifecycle, connector registry, health, reload, single-worker lock
  bridge.py         # AgentBridge: the single internal seam over the service layer
  coalescer.py      # StreamRenderer: SSE events -> outbound messages / HITL cards
  identity.py       # binding lookup; linking-code issue/redeem/revoke; transfer
  actions.py        # outbound card-action token mint/verify; action-mapping persistence
  dispatch.py       # on_inbound dispatcher: dedupe -> identity -> enroll|refuse|turn|resume
  types.py          # InboundMessage, OutboundMessage, OutboundCard, CardAction, ChatRef, MessageRef
  config.py         # GatewayConfig parsed from settings/env (typed, with documented defaults)
  connectors/
    base.py         # MessageConnector protocol + ConnectorCapabilities
    feishu.py       # reference connector (WebSocket long-connection, no public IP)
    fake.py         # in-memory connector for tests
```

### Existing seam (verified against current code)

- `active_agent_service.create_thread(session, title, character) -> AgentThread`
  (`character` is the persona).
- `active_agent_service.stream_and_persist(thread_id, content, requested_character,
  page_context, context_usage, accounting_date, model_selection, yolo_mode, envelope,
  confirmed_cost_preview)` — async generator of SSE strings; event types observed:
  `token`, `done {message_id}`, `error`, `heartbeat`, plus tool-related pre-formatted SSE.
- HITL state lives on the persisted assistant message:
  `AgentMessage.meta.pending_actions = [{id, status: "pending", async_task_id?, ...}]`.
- `_resume_action(thread_id, message_id, action_id, decision, session) -> AgentMessage`
  (in `main.py`) resolves a pending action by `id`; `decision ∈ {"confirm","dismiss"}`;
  returns the resumed message **synchronously**; already routes the async-subagent
  bubble-up (`async_task_id`) to the subagent checkpointer thread.
- `record_audit(session, event_type, actor, subject_type, subject_id, payload)` — `actor`
  free-form string; web uses `"desk_user"`.
- `ensure_thread_workflow_state`, `normalize_model_selection` reused as-is.

**Refactor (precondition):** two seam changes, each behavior-preserving for web:
- Extract the resume body from `main.py:_resume_action` into
  `active_agent_service.resume_pending_action(*, thread_id, message_id, action_id, decision,
  actor, session) -> AgentMessage`. HTTP route → thin wrapper passing `actor="desk_user"`.
- `stream_and_persist` gains a keyword `actor: str = "desk_user"`. The web endpoint keeps the
  default; the bridge passes `actor=binding.desk_user`. `actor` flows into the audit events
  that today hard-code `"desk_user"` on the turn/resume paths (`thread.*`, action
  confirm/dismiss, tool-execution audit) — enumerated in the plan; events keyed to
  `"system"` are unchanged.
Both paths are pinned by **characterization tests** (web actor still `"desk_user"`; IM actor
is the bound identity) taken before the refactor.

### `submit_turn` argument sourcing (IM defaults)

| Arg | IM v1 source |
|-----|--------------|
| `requested_character` | `binding.persona` (validated against the known persona set at enroll **and** at turn time; stale/unknown → refuse turn with re-enroll prompt) |
| `envelope` | constant `"DESK_WORKFLOW"` |
| `yolo_mode` | `False` (never auto-skip write confirmations over IM) |
| `page_context` | `None` (no web page context in IM) |
| `context_usage` | `None` |
| `accounting_date` | `None` (agent default = today), unless a later spec adds a per-chat override |
| `model_selection` | `normalize_model_selection(None)` (registry default) |
| `confirmed_cost_preview` | `False` on the initial turn; cost-preview confirmation is delivered through the HITL card flow, not pre-confirmed |

### Component 1 — Connector protocol + normalized types (`connectors/base.py`, `types.py`)

Platform-agnostic, **minimal and Feishu-validated**. Connectors are pure transport.

```python
@dataclass(frozen=True)
class ConnectorCapabilities:
    supports_edit_in_place_message: bool
    supports_edit_in_place_card: bool
    supports_interactive_cards: bool
    max_message_chars: int          # measured in Unicode code points; chunking/truncation unit

class MessageConnector(Protocol):
    name: str                       # "feishu"
    capabilities: ConnectorCapabilities
    async def start(self, on_inbound: Callable[[InboundMessage], Awaitable[None]]) -> None
    async def stop(self) -> None
    async def send_message(self, chat: ChatRef, msg: OutboundMessage, *, idempotency_key: str) -> MessageRef
    async def update_message(self, ref: MessageRef, msg: OutboundMessage) -> None
    async def send_card(self, chat: ChatRef, card: OutboundCard, *, idempotency_key: str) -> MessageRef
    async def update_card(self, ref: MessageRef, card: OutboundCard) -> None
    async def health(self) -> ConnectorHealth
```

`send_*` take an `idempotency_key` (the dispatcher's per-output key) so a retried send does
not double-post; `update_*` are naturally idempotent on `MessageRef`. A connector that
cannot honor idempotency natively records `(idempotency_key → MessageRef)` in a small
in-memory/outbox map for the worker lifetime.

Normalized types:
- `ChatRef{connector, workspace_id, chat_id, chat_type: "dm"|"group"}`.
- `MessageRef{connector, workspace_id, chat_id, message_id}` (carries workspace + provider
  for precise callback validation).
- `InboundMessage{connector, workspace_id, external_account_id, provider_event_id,
  chat: ChatRef, kind: "message"|"card_action", text: str|None,
  action: CardActionInbound|None, raw}` — `provider_event_id` is the platform's unique
  event/message id, used for dedupe.
- `OutboundMessage{text}`; `OutboundCard{title, body, sections: list[CardSection],
  actions: list[CardAction], resolved: bool, footer}`.
- `CardAction{label, style: "primary"|"danger"|"default", token}` — the **only** value sent
  to the provider as button payload is the opaque `token`; `action_id` and `decision` are
  derived server-side exclusively from `gateway_card_action` (never trusted from the
  callback). `label`/`style` are render-only.
- `CardActionInbound{source_message_ref, token}`.

**`workspace_id` is normalized non-null**: platforms without a workspace concept use the
empty string `""`. Uniqueness and indexes treat `""` as a real value.

**Inbound text validation:** `text` may be `None` (attachments/stickers/images) or
whitespace-only. The dispatcher trims text; empty/unsupported content gets a help message
and never reaches the agent. Max inbound length is `gateway_max_inbound_chars` (default
4000 code points); longer inbound is rejected with a "please shorten / use the web desk"
notice. Linking-code redemption is attempted **only** when the trimmed text exactly matches
the code shape (base32, fixed length).

### Component 2 — Feishu reference connector (`connectors/feishu.py`)

Wraps Feishu's WebSocket event subscription (lark-oapi). No public IP. Maintains the
long-connection with exponential-backoff reconnect; translates inbound Feishu message and
card-action events → `InboundMessage` (populating `provider_event_id`); translates
`OutboundCard` → Feishu interactive-card JSON and card-action callbacks →
`InboundMessage{kind:"card_action"}`; implements `update_card`/`update_message` for
edit-in-place. Creds: `FEISHU_APP_ID` / `FEISHU_APP_SECRET`.
`capabilities = ConnectorCapabilities(True, True, True, max_message_chars=10000)`.

**Feishu identity & event mapping (exact):** `external_account_id` = sender **`open_id`**
(stable per-app-per-user — sufficient for our single Feishu app; `union_id` only needed
across multiple apps); `workspace_id` = **`tenant_key`**; `chat_id` = event **`chat_id`**
with `chat_type` from the event (`p2p`→`dm`, else `group`); `provider_event_id` = event
header **`event_id`** (unique per tenant; combined with `workspace_id` in the dedupe key).
Event **authenticity** is verified via the Feishu **verification token** and AES decryption
with the **encrypt key** (config `FEISHU_VERIFICATION_TOKEN` / `FEISHU_ENCRYPT_KEY`) before
any handling. Adapter tests use representative message and card-action payload fixtures.

### Component 3 — Identity & enrollment (`identity.py` + tables)

Safety keystone for full parity.

```
gateway_binding(
  id, provider, external_account_id, workspace_id,    # see partial unique index below
  desk_user,                                           # stable identifier (audit actor; future user FK)
  persona,                                             # validated ∈ known persona set
  status: "active"|"revoked",
  bound_at, last_seen_at, revoked_at, supersedes_binding_id|null  # set on the NEW active row
)
gateway_linking_code(
  code,                                                # >= 128 bits entropy, base32, single-use
  desk_user, persona,
  expires_at,                                          # issued_at + 10 minutes
  redeemed_by_binding_id|null, issued_by, created_at
)
gateway_thread_map(
  binding_id, chat_id, thread_id,                      # UNIQUE(binding_id, chat_id)
)
gateway_inbound_seen(
  connector, workspace_id, provider_event_id,         # UNIQUE(connector, workspace_id, provider_event_id)
  state: "processing"|"processed",
  owner_token, claimed_at, attempts, seen_at          # lease-reclaim; TTL-pruned after gateway_dedupe_ttl (default 24h)
)
gateway_card_action(
  token,                                               # unique, signed/opaque, >=128 bits
  out_connector, out_workspace_id, out_chat_id, out_message_id,  # outbound MessageRef for callback-source validation
  binding_id, thread_id, message_id, action_id, decision,
  expires_at,                                          # minted_at + gateway_card_action_ttl (default 30m)
  status: "pending"|"resolving"|"resolved"|"failed"|"unknown", resolved_by_binding_id|null
  # UNIQUE(thread_id, message_id, action_id, decision) — idempotent minting: re-render finds the existing row
)
gateway_worker_lock(
  id=1, owner_token, acquired_at, lease_expires_at    # single-row sentinel lease (SQLite); stale-owner expiry by DB time
)
```

**Transfer vs. uniqueness (resolved):** enforce a **partial unique index** on
`(provider, external_account_id, workspace_id) WHERE status='active'`. A transfer
**inserts a new active row** and flips the prior row to `revoked` (`revoked_at` set) inside
one transaction; the **new** row's `supersedes_binding_id` points to the prior (revoked)
row, so history is preserved without violating uniqueness.

Flow:
1. Web desk issues a one-time `linking_code` (≥128-bit base32, 10-min TTL, recorded with
   `issued_by`). Requires a desk-web-authorized request (same auth as other internal write
   endpoints; see Component 6).
2. User DMs the code to the bot (DM only; group codes never redeem). Redemption runs in one
   transaction with this **exact order** (avoids violating the partial unique index):
   (a) `SELECT ... FOR UPDATE` the code; validate unexpired + unredeemed;
   (b) if an active binding already exists for this identity, set it `revoked` (+`revoked_at`);
   (c) `INSERT` the new active binding (`supersedes_binding_id` = the just-revoked row, if any);
   (d) `UPDATE` the code `redeemed_by_binding_id` = new binding id;
   (e) audit `gateway.bound` (or `gateway.transferred` if step (b) revoked one); commit.
   If the same identity is already actively bound to the **same** desk_user/persona, redemption
   is a no-op refresh (audited `gateway.rebound`), not an error.
3. A valid code from an already-bound user **always attempts redemption** (it is not silently
   treated as chat text); the result is a transfer/rebind per step 2.
4. Unbound inbound that is not a valid code → refusal + enrollment instructions; never
   reaches the agent.
5. Revocation flips `status="revoked"` (from web desk); revoked identities are unbound and
   refused. Revocation re-checked **at card-click time** (Component 5).

### Component 4 — AgentBridge facade (`bridge.py`)

The single internal seam — DeerFlow's "one clean internal client," over our service layer.

```python
class AgentBridge:
    def thread_for(self, session, binding, chat: ChatRef) -> AgentThread:
        # atomic upsert into gateway_thread_map keyed (binding_id, chat_id);
        # on miss create_thread(persona) then insert; INSERT ... ON CONFLICT to avoid races
    async def submit_turn(self, binding, thread, text) -> AsyncIterator[AgentEvent]:
        # stream_and_persist(...) with the IM defaults table; parses SSE -> AgentEvent;
        # audit actor=binding.desk_user
    def resume(self, binding, thread_id, message_id, action_id, decision) -> AgentMessage:
        # resume_pending_action(..., actor=binding.desk_user)
```

`AgentEvent{type, data}` is a parsed SSE line so the coalescer never re-parses raw strings.

### Component 5 — HITL card-action mapping & approver policy (`actions.py`, `coalescer.py`)

**Idempotent minting.** A card row is minted per pending action via `INSERT ... ON CONFLICT
(thread_id, message_id, action_id, decision) DO NOTHING RETURNING token`; a re-render (e.g.
both `action_required` and `done` events, or a redelivery) finds the **existing** token
rather than minting a second clickable surface. Cards are therefore rendered **at most once
per `(message_id, action_id)`**, on whichever of `action_required` / `done` arrives first.

**Spoof-safe mapping.** The button carries **only** the opaque signed `token`. On card-action
inbound the dispatcher: (1) verifies the token (exists, signature valid, unexpired); (2)
validates the callback source — the inbound `source_message_ref` must match **all** of the
stored `out_connector` / `out_workspace_id` / `out_chat_id` / `out_message_id`; (3)
**atomically claims**: `UPDATE gateway_card_action SET status='resolving' WHERE token=? AND
status='pending' RETURNING ...`. Only a successful claim (exactly one row) proceeds to
`AgentBridge.resume`; concurrent double-clicks / retried callbacks lose the claim and get an
idempotent "already handled" card. `action_id` / `decision` come from the row, never the
callback. Because the clickable surface is unique per action and the claim is atomic,
duplicate UI posts cannot cause duplicate resumes.

**Claim outcome semantics (irreversible-safe):**
- `resume` returns → `status='resolved'`, `resolved_by_binding_id` set, render resolved card;
  if the card update then fails, the resolution is still audited and a follow-up plain message
  reports the outcome (Component 6).
- `resume` **raises** → because the gated tool may have already executed an irreversible side
  effect (e.g. `book_position`) before raising, the gateway does **not** offer a retry. The
  action is marked `status='unknown'`, the outcome-uncertainty is audited, and the card is
  re-rendered **without buttons** stating "outcome unknown — verify in the web desk" with a
  deep-link. No fresh token is minted. (Resumed execution is not assumed idempotent.)
- **Expired token at click time** → the IM button is disabled with "expired — ask the agent
  to re-send this approval"; expiry is audited. Expiry only disables the **IM surface**: the
  underlying `pending_action` on the assistant message is untouched and **remains approvable
  in the web desk**; the user may also ask the agent to regenerate a fresh IM card for the
  same pending action.

**Card-action expiry** is a gateway default (`gateway_card_action_ttl`, default 30m), not
derived from the pending action.

**Revocation mid-flight.** Before each outbound flush/card send the coalescer re-checks that
the binding is still `active`; on revocation it stops streaming, drops queued turns for that
binding, audits, and (if safe) sends a single "session ended" notice. In-flight `resume` is
not started for a revoked binding.

**Approver policy (v1):** only the **original bound user who initiated the turn** may
approve/reject; the dispatcher checks the click's resolved binding equals
`gateway_card_action.binding_id` and that the binding is still `active` at click time.
Cross-user clicks and revoked-at-click bindings are refused with an explanatory card
update. (Group chats are already refused at Decision 7, so multi-user chats don't arise in
v1.)

**Approval-card content contract (fail-closed).** Each gated action declares a set of
**required decision fields** (tool name, key parameters, cost/risk preview when the tool
produces one, booking/RFQ identifiers, expiry, irreversible-warning). The card builder reads
these from the pending action's payload (same data the web UI consumes). Two distinct cases:
- **Missing** a required field → **fail closed**: render a *non-approvable* card (no
  buttons) directing the user to approve in the web desk. IM must never offer a weaker HITL
  path than web.
- **Oversized** (present but too large for the card) → render a truncated summary that still
  includes every required field's identity, with a "view full details in web desk"
  deep-link; approval **remains enabled** because all required fields are present.

**Required decision fields per gated tool/workflow** (source = the pending action payload;
each row has a golden test for present-all, missing-required→fail-closed, and oversized→
truncated paths):

| Gated action | Required decision fields (payload keys) | Irreversible warning |
|--------------|------------------------------------------|----------------------|
| `book_position` | instrument/product, side, notional, price/terms, portfolio, cost/risk preview, position-id-on-confirm | Yes |
| `book_hedge` | hedge instrument, side, notional, linked position/portfolio, cost preview | Yes |
| RFQ draft / `quote_rfq` | rfq id, underlying, structure, size, quoted level, cost preview | No (revisable) |
| `submit_rfq_for_approval` | rfq id, summary, target approver step | No |
| `approve_rfq` / `reject_rfq` | rfq id, summary, current state | Yes (`approve`) |
| `release_rfq` | rfq id, counterparty, final terms | Yes |
| Cost-preview confirmation | tool name, estimated cost/runtime, scope | No |

Any tool reaching IM HITL without a registered field set fails closed (non-approvable card →
web). Golden tests cover `book_position` and `release_rfq` in full plus one revisable
(`quote_rfq`).

**Multiple pending actions:** render **one card per pending action** (each with its own
token), approved/rejected independently; resolving one updates only its card. Ordering
follows `pending_actions` order. Tests cover 0, 1, and N simultaneous pending actions
including partial resolution.

### Component 6 — Stream→output coalescer + runtime + HTTP (`coalescer.py`, `runtime.py`, `config.py`)

**`AgentEvent` enum & SSE grammar.** `stream_and_persist` emits SSE frames via the app's
`_sse(event, data)` helper: an `event: <type>` line followed by one or more `data: <json>`
lines, terminated by a blank line; comment (`:`-prefixed) lines are ignored. The parser
accumulates multi-line `data:` payloads, JSON-decodes once per frame, and maps `event` →
`AgentEvent{type, data}` with `type ∈ {token, done, error, heartbeat, tool_started,
tool_finished, action_required, unknown}`. Tool events → optional progress updates. Pending
actions render on whichever of `action_required` / `done` arrives **first**, made idempotent
by the unique card minting (Component 5), so the two never double-render. **Unknown** types
and **malformed/undecodable** frames are logged and treated as `unknown` (skipped), never
crashing the turn. Each event type plus the malformed and multi-line cases have tests.

**Coalescer (turn path)** consumes `submit_turn` events:
- Buffer `token`s; on connectors with `supports_edit_in_place_message`, flush to one edited
  message every `flush_interval_ms` (default 700) / `flush_chars` (default 280); otherwise
  accumulate and send once at `done`.
- On `done {message_id}`: load the assistant message; for each pending action render an
  approval card (Component 5).
- On `heartbeat` / `tool_*`: optional progress update on long tasks.
- On `error`: refusal/notice text + web deep-link.

**Coalescer (resume path).** Because `resume_pending_action` returns an `AgentMessage`
synchronously (not a stream), the dispatcher calls
`coalescer.render_resume_result(binding, clicked_card_ref, AgentMessage) -> None`, which:
updates the clicked card to its resolved state; renders the resumed assistant `content` as a
new message; and renders any **new** `pending_actions` on the resumed message as fresh
approval cards (chained approvals). Errors surfaced by the resumed message follow the
`error` path.
- **Size/limits:** outputs exceeding `capabilities.max_message_chars` are chunked (plain
  messages) or truncated with a web deep-link (cards). A token-bucket limiter caps update
  frequency to respect platform rate limits; on a platform rate-limit response the coalescer
  coalesces harder (larger flush interval) and retries with backoff.
- **Outbound delivery failures:** sends/updates use an idempotency key (the
  `MessageRef`/token); a failed `update_*` falls back to a fresh `send_*`; if a resolved-card
  update fails after a successful resume, the resolution is still audited and a follow-up
  plain message reports the outcome so audit visibility is never lost.

**Dispatcher (`dispatch.py`)** per inbound:

1. **Dedupe (state machine).** Claim `(connector, workspace_id, provider_event_id)` in
   `gateway_inbound_seen`: `INSERT ... state='processing', owner_token, claimed_at`. If a row
   exists: `state='processed'` → **skip** (already handled); `state='processing'` with a
   **fresh lease** (claimed within `gateway_dedupe_lease_s`, default 120) → **skip** (a
   sibling is handling it); `state='processing'` with an **expired lease** → **reclaim**
   (take ownership, `attempts++`). The row flips to `processed` in the transaction that
   commits the terminal handling outcome. Because resumed tool execution and card minting are
   made idempotent (below), a reclaim/redelivery cannot double-execute.
2. **Branch on `kind` (before text validation):**
   - `kind == "card_action"` → skip text validation entirely; go to token / source / binding
     validation (Component 5) → `resume` → `render_resume_result`. Card actions use a
     **separate small priority lane** per `(binding, chat)` so an approval click is never
     dropped behind a turn backlog; if that lane is somehow saturated the source card is
     updated to "couldn't process the click — tap again."
   - `kind == "message"` → continue to step 3.
3. **Refuse if group chat** (before any redemption — a code posted in a group never binds).
4. Resolve identity.
5. Code redemption / enroll (DM only).
6. Refuse if unbound.
7. Validate inbound text (trim / length / non-text → help message).
8. `thread_for` → `submit_turn` → coalescer.

Turns for one `(binding, chat)` are **serialized** (per-key async lock). Backpressure (turns
only): at most `gateway_max_queued_per_chat` (default 8) queued per key; past the bound the
**newest** turn is dropped with a "busy — resend shortly" notice; queued turns older than
`gateway_queue_max_age_s` (default 120) are dropped with notice. Overflow has acceptance
tests.

**Runtime** starts enabled connectors as asyncio tasks in the FastAPI **lifespan**, after
acquiring a **single-worker lock** held for the worker lifetime: a SQLite sentinel row
`gateway_worker_lock(id=1, owner_token, acquired_at)` claimed via a conditional
`INSERT`/`UPDATE ... WHERE owner expired`, refreshed on a heartbeat (lease,
`gateway_lock_lease_s` default 30). **Behavior when the lock is held by another worker:** the
process still starts and serves HTTP, but **does not start connectors**; `GET
/api/gateway/health` reports `worker_lock_owner: false` and connector state `disabled`. If
the lock/DB connection is lost mid-life, connectors are stopped and the runtime retries
acquisition, reporting `degraded` meanwhile. On shutdown the lease row is released.
`health()` returns the typed schema below; `reload()` re-reads config and restarts connector
tasks (lock-owner only).

**HTTP endpoints** (enrollment/admin from the web desk; all require the same desk-web
authorization as existing internal write endpoints):
- `POST /api/gateway/linking-codes` → `{code, expires_at}` (body `{persona}`, validated ∈
  persona set; rate-limited to `gateway_code_issue_per_min` (default 10) per issuer → 429).
- `GET /api/gateway/bindings?status=active|revoked|all&limit=&cursor=` → `{items:[{id,
  provider, external_account_id, workspace_id, desk_user, persona, status, bound_at,
  last_seen_at}], next_cursor}`. `limit` default 50, max 200; ordered `bound_at DESC, id
  DESC`; `cursor` is an opaque base64 of `(bound_at, id)`.
- `DELETE /api/gateway/bindings/{id}` → idempotent revoke (`{ok:true, status:"revoked"}`;
  already-revoked → 200; unknown id → 404).
- `GET /api/gateway/health` → `{worker_lock_owner: bool, connectors: [{name, state:
  "up"|"degraded"|"down"|"disabled", detail: {last_event_at, reconnects, last_error|null}}]}`.
- `POST /api/gateway/reload` → re-reads config and restarts connector tasks (lock-owner
  only; a non-owner returns `409`). `{ok, connectors:[...]}`.
Error shape matches the app's existing `HTTPException` JSON.

**Config & limits (`config.py`, typed, documented defaults):** `flush_interval_ms`=700,
`flush_chars`=280, `gateway_card_action_ttl`=30m, `gateway_linking_code_ttl`=10m,
`gateway_max_inbound_chars`=4000, `gateway_max_queued_per_chat`=8,
`gateway_queue_max_age_s`=120, `gateway_dedupe_ttl`=24h, `gateway_dedupe_lease_s`=120,
`gateway_lock_lease_s`=30, `gateway_code_issue_per_min`=10,
`gateway_default_desk_user`="desk_user", `gateway_web_base_url` (required; e.g.
`https://desk.internal`). **Deep-link formats:** thread `…/chat?thread={id}`, action
`…/chat?thread={id}&message={mid}&action={aid}`; if `gateway_web_base_url` is unset the
gateway logs an error and renders a plain "open the web desk" text without a URL.
**Rate limiter:** token-bucket scoped **per (connector, chat)** for card/message updates,
burst 5, refill 5/s, jittered exponential backoff (base 0.5s, cap 8s) on provider 429s,
retry ≤3; under backoff the coalescer widens its flush interval (observable, tested). Feishu
`max_message_chars`=10000; cards beyond this are summarized + deep-linked. All limits measured
in **Unicode code points**.

## Parity Matrix (Feishu v1)

Each row has an acceptance test. "Behavior" is the expected IM experience.

| Web capability | IM v1 behavior |
|----------------|----------------|
| Query positions / risk / pricing / market data | Full — streamed text answer |
| Run scenario / batch pricing (read-style) | Full — streamed; long-run progress via heartbeat |
| RFQ draft / quote | Full — HITL card for any gated tool |
| `book_position` / `book_hedge` (irreversible) | Full — approval card with content contract; resume on Approve |
| `submit/approve/reject/release_rfq` | Full — approval card; approver-policy enforced |
| Cost-preview confirmation | Delivered as a HITL card, not pre-confirmed |
| Model selection override | **Not in v1** (registry default); deferred |
| Page-context-dependent tools | **Degraded** — `page_context=None`; tools needing it report the limitation |
| Multi-action approve-all | **Not in v1** — one card per action |

## Authorization & audit

Existing defenses unchanged and unbypassed: capability gate, envelope scoping
(`DESK_WORKFLOW`), per-tool HITL interrupts, cost-preview middleware, tool-error boundary.
The gateway adds: (1) no agent access without an active binding; (2) `actor = bound
desk_user` threaded into `record_audit` and `resume_pending_action`. No RBAC is introduced
(see Authorization model assumption).

## Failure handling

| Condition | Behavior |
|-----------|----------|
| Unbound user (not a code) | Refuse + enrollment instructions; agent untouched |
| Invalid / expired / redeemed code | Refuse + "request a new code" |
| Revoked binding (turn or click time) | Treated as unbound; refused |
| Revocation mid-flight turn | Coalescer re-checks before each flush; stops streaming, drops queued turns, audits, "session ended" notice |
| `resume` raises (possible partial side effect) | `status='unknown'`, audited; buttonless "outcome unknown — verify in web" card; **no retry** |
| Group chat inbound (incl. a valid code) | Refused before redemption; never binds (v1 DM-only) |
| Empty / non-text / overlong inbound | Help message; never reaches agent |
| Connector disconnect | Exponential-backoff reconnect; health → `degraded` |
| Agent `error` event | Notice + web deep-link; turn persisted (existing resilience) |
| Unknown / unparseable agent event | Logged, treated as `unknown`, ignored; turn continues |
| Duplicate inbound event | `gateway_inbound_seen` skip; crash mid-handling → row stays `processing` → redelivery reprocessed (at-least-once) |
| Concurrent turns on one `(binding, chat)` | Serialized per-key lock; queue bound 8 / age 120s, drop-newest-with-notice |
| Concurrent double-click card action | Atomic `pending→resolving` claim; loser gets idempotent "already handled" card |
| Required approval field missing | Fail closed: non-approvable card → approve in web |
| Spoofed / forged token or mismatched callback source | Verification fails → refused |
| Outbound send/update failure | Idempotent retry (`idempotency_key`); update→send fallback; resolution still audited |
| Missing platform creds | Connector `disabled` at boot (logged); others unaffected |
| Second gateway worker | Lock not acquired → HTTP serves, connectors not started, health `worker_lock_owner:false` |
| Lock/DB lost mid-life | Connectors stopped; retry acquisition; health `degraded` |
| Stale/unknown persona on binding | Turn refused with re-enroll prompt |

## Out of scope

- Slack, Telegram, WeChat, WeCom, DingTalk connectors (own later specs, each confirming
  callback/edit/identity/workspace models before extending the protocol).
- Group-chat support; per-chat accounting-date / model-selection overrides; approve-all.
- Per-user RBAC (the app has none today; `desk_user` is a forward-compatible identifier).
- Cross-worker stream bridge / horizontal scaling.
- Long-term cross-session memory; outbound proactive alerts.
- Live-Feishu end-to-end CI gating (feature lands on mocks; manual smoke test later).

## Testing strategy

- **Vertical slice via `FakeConnector`** (no network): enroll-with-code → query positions →
  trigger a HITL booking → Approve via card-action → assert booking executed and audit
  `actor = desk_user`. Plus: unbound refusal, expired code, revoke, transfer, group-chat
  refusal, duplicate-event dedupe, concurrent-turn serialization.
- **Card-action security tests**: forged token, expired token, mismatched callback source
  (connector/workspace/chat/message), cross-user click, revoked-at-click, atomic-claim
  double-click (only one `resume`), `resume`-raises → buttonless unknown-outcome card (no
  retry), idempotent re-mint (one clickable surface per `(message_id, action_id)`),
  valid-code-in-group-chat does-not-bind.
- **Dedup/lease tests**: redelivery while `processing` (fresh lease) skipped; expired-lease
  reclaim; `processed` skipped; idempotent re-handling causes no double turn/card.
- **Revocation mid-turn test**: streaming halts at next flush; queued turns dropped.
- **Feishu identity test**: `open_id`/`tenant_key`/`chat_id`/`event_id` mapping + event
  authenticity (verification token / decrypt) from payload fixtures.
- **Approval-card golden tests**: content contract for `book_position` and `release_rfq`
  (tool name, params, cost/risk preview, identifiers, expiry, warning); truncation path.
- **Feishu adapter unit tests**: event → `InboundMessage` (incl. `provider_event_id`);
  `OutboundCard` → Feishu JSON; callback → `InboundMessage{kind:"card_action"}`. No network.
- **Coalescer tests**: flush cadence, chunking/truncation at `max_message_chars`, edit-in-
  place vs single-send fallback, rate-limit backoff, outbound-failure fallback.
- **Identity tests**: code entropy/TTL/single-use, atomic redemption race, partial-unique-
  index transfer, revoke idempotency.
- **HTTP contract tests**: unauthorized issue/revoke rejected; pagination; health schema;
  reload restarts tasks and non-owner reload → 409; second-worker lock → connectors not
  started + `worker_lock_owner:false`.
- **Coalescer resume-path test**: `render_resume_result` updates clicked card, posts resumed
  content, and chains a new pending-action card.
- **Event-enum tests**: each of token/done/error/heartbeat/tool_*/unknown handled; parse
  error degrades to `unknown` without crashing.
- **Backpressure tests**: queue overflow drops newest with notice; stale queue item dropped.
- **Characterization test** pinning the existing `_resume_action` HTTP route before/after
  the resume extraction refactor.
- Full existing suite stays green (validate in a no-`.env` worktree per the known tracing
  false-failure trap).
