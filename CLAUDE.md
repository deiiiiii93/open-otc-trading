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
