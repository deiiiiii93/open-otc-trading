# Open OTC Trading — agent guidance

Orientation for anyone (human or agent) working in this repo. The frontend has its
own guide at [`frontend/CLAUDE.md`](frontend/CLAUDE.md) — **read it before any UI
work** (token-only styling is non-negotiable there).

- **Backend** — FastAPI + Uvicorn, LangGraph agents, SQLAlchemy models, Alembic
  migrations. Pricing/risk math is delegated to **QuantArk** (deterministic quant
  engine) — numbers never come from an LLM. Tests: `.venv/bin/python -m pytest`.
- **Frontend** — React 19 / Vite / TypeScript, Radix UI, "Warm Ledger" tokens.
  Tests: `cd frontend && npm test` (vitest), type-check `npx tsc --noEmit`.
- **DB** — SQLite at `data/open_otc.sqlite3`; **Alembic is the upgrade path**
  (`.venv/bin/python -m alembic upgrade head`). The live DB can lag `head`; a 500
  from a feature usually means migrations are behind, not a code bug.
- **LLM channels** — `config/agent_channels.yaml` is **gitignored** (per-env); the
  tracked template is `config/agent_channels.example.yml`. Tag/model edits must go
  to **both**.

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

## Instant-messaging gateway (Feishu/Lark)

Drive the full desk agent over IM with web parity — streaming markdown replies,
HITL Approve/Reject cards, pickable reply-option cards, linking-code enrollment.

**Package:** `backend/app/services/gateway/` — `runtime` (single-worker DB-lease
election + heartbeat), `connectors/{base,fake,feishu}`, `dispatch` (dedup +
message/card lanes + per-chat serialization), `bridge` (threads `actor=binding.desk_user`
into the agent service), `coalescer` (StreamRenderer: streaming, approval cards,
reply-option cards, revocation, rate limit), `identity`, `actions`, `cards`,
`config`, `sse`, `types`. Endpoints: `/api/gateway/*` in `main.py`. Migration
`0037_gateway_tables`. Tests: `tests/gateway/` (run from **repo root**).

### Run model — dedicated worker, NOT `--reload`

The lark WS client (`lark_oapi.ws.Client`) owns a **module-global event loop**
and `start()` is a **blocking** call run on a daemon thread (events are marshalled
to dicts via `lark.JSON.marshal` and dispatched onto the server loop with
`run_coroutine_threadsafe`). This **does not survive uvicorn `--reload`** — a
hot-reload while the worker holds the lock wedges it. So: keep
`GATEWAY_ENABLED_CONNECTORS` **empty in `.env`** (reloading dev servers stay inert)
and enable the connector via a launch override on a stable, non-reload worker:
`GATEWAY_ENABLED_CONNECTORS=feishu uvicorn app.main:app --app-dir backend --port 8001`.
A single-row `gateway_worker_lock` lease guarantees one Feishu handler; every other
backend stays in standby (and standby workers do **not** retry — they pick up the
lock only on restart).

### lark schema-2.0 card realities (all live-only — fakes hid them)

- **Cards must be schema 2.0.** Buttons are elements inside `body.elements` — there
  is **no top-level `actions`** (that's 1.x). Buttons fire via a `behaviors`
  callback array, **not** a `value` field. The `note` element is gone — use
  `markdown`. Wrong shape → `code=230099 / 200621`.
- **Text bubbles can't render markdown**; replies are sent as a headerless
  `markdown`-element card (`_text_to_markdown_card`), and in-place streaming edits
  use `message.patch` (interactive), not `message.update` (text).
- **Outbound builders:** `receive_id_type` goes on the *request* builder, not the
  body; `PatchMessageRequestBody` has only `content`. Always check `resp.success()`
  — failures are otherwise swallowed.

### HITL & reply options ride the `done` SSE event

`agents.py::_done_payload` enriches the terminal `done` event with `thread_id` +
`pending_actions` + `reply_options` (read back from the persisted message meta) so
IM connectors can render cards — the web UI re-fetches and ignores the extras. Both
finalize paths emit it (`_finalize_turn` **and** `_finalize_workflow_stream_turn`;
`DESK_WORKFLOW` envelope uses the latter). Approval buttons carry a one-time token
(→ `resume`); reply-option buttons carry `{reply, label}` and are replayed as a
normal `message` turn that also locks their card (`InboundMessage.card_lock_ref`).

### Config & gotchas

- `GATEWAY_AGENT_MODEL` (`channel:provider:model`, e.g.
  `zenmux:openai:deepseek/deepseek-v4-flash`) selects the IM-turn model; it's a real
  `Settings` field (loads from `.env`), resolved by the bridge as explicit-arg →
  settings → process-env → registry default. `GATEWAY_WEB_BASE_URL` points card
  deep-links at the web desk (e.g. `http://localhost:5173`).
- `lark-oapi` is a hard dep in `pyproject.toml`; a stale `.venv` may lack it
  (`uv sync`). WS long-connection mode needs **no** public webhook URL.
- **`.env` leak in tests:** real `FEISHU_*` / `GATEWAY_*` values bleed into
  `Settings()` and fail the "defaults are None/empty" assertions in
  `test_config.py` / one `test_identity.py` case — validate those in a no-`.env`
  environment. All other gateway tests are connector-agnostic (FakeConnector).
