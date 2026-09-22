# Open OTC Trading — agent guidance

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
- **Before opening a PR / merging** — update `CHANGELOG.md` (Keep a Changelog,
  under `[Unreleased]`); update `README.md` if the change is user-facing, and this
  file if it introduces a new subsystem or a gotcha future agents need. A `pre-push`
  hook (`.githooks/pre-push`, enable via `git config core.hooksPath .githooks`)
  blocks pushing backend/frontend changes without a `CHANGELOG.md` update and
  reminds (non-blocking) about `README.md`/`CLAUDE.md`.

---

## Subsystem guides — read the guide before working in the tree

Detail lives next to the code it governs, so a session loads only what it touches.

| Guide | Read before |
|---|---|
| [`frontend/`](frontend/CLAUDE.md) | any UI work — token-only styling is non-negotiable there |
| [`config/`](config/CLAUDE.md) | adding or repointing a model, reasoning effort, output budgets, the Model Maintenance UI |
| [`backend/app/services/deep_agent/`](backend/app/services/deep_agent/CLAUDE.md) | the agent runtime — audit trail, ground-truth artifacts and compaction, long-term memory, dynamic subagents, the binary-read guard and filesystem scoping |
| [`backend/app/golden_workflows/`](backend/app/golden_workflows/CLAUDE.md) | authoring or rescoring a workflow manifest, harvesting fixtures, the ability card |
| [`backend/app/services/arena/`](backend/app/services/arena/CLAUDE.md) | launching, resuming, merging, purging or reading an arena board |
| [`backend/app/services/confirmations/`](backend/app/services/confirmations/CLAUDE.md) | parsing counterparty confirmations into booked positions |
| [`backend/app/services/settlement/`](backend/app/services/settlement/CLAUDE.md) | settlement cashflows, drift, notices |
| [`backend/app/services/reporting/`](backend/app/services/reporting/CLAUDE.md) | templated reports and the grounding guard |
| [`backend/app/services/domains/`](backend/app/services/domains/CLAUDE.md) | position lifecycle events, term-structure pricing curves |
| [`backend/app/services/gateway/`](backend/app/services/gateway/CLAUDE.md) | the Feishu/Lark IM gateway |
| [`backend/app/services/system_one/`](backend/app/services/system_one/CLAUDE.md) | System One (TypeSafe Jev): the AUTO tool guard, memory keep-alive, the confirmation family cross-check, the limit incident review |
| [`backend/app/routers/`](backend/app/routers/CLAUDE.md) | the chat thread list's scoping, paging and search |
| [`docs/arena/deploy/`](docs/arena/deploy/CLAUDE.md) | building or shipping the artena.one arena blog |
| [`docs/arena/intro-video/`](docs/arena/intro-video/CLAUDE.md) | the HyperFrames intro-video composition |

### Publishing the arena blog

`https://www.artena.one/arena/` is built from a manifest and shipped by rsync out of
`docs/arena/deploy/` (`deploy.sh build | preview | publish | status | boards |
stats | bootstrap`). That subsystem keeps its own guide at
[`docs/arena/deploy/CLAUDE.md`](docs/arena/deploy/CLAUDE.md) — **read it before any
publishing, leaderboard-export or nginx work.** It owns the manifest rules, the
derived-leaderboard and provisional-cards contracts, the two stylesheets (web vs
print), and the deploy traps (`rsync --delete`, the nginx `add_header` replacement
rule, the single-file bind-mount inode).

---

## Cross-cutting rules

These bite in more than one subsystem. Each links to where it is argued in full.

- **A new agent tool needs FOUR registrations, not one** — `QUANT_AGENT_TOOLS`
  (`tools/__init__.py`), `DEEP_AGENT_TOOL_NAMES` (`services/agents.py`), and all three
  structures in `services/deep_agent/hitl.py`. A tool registered but not allowlisted is
  silently dropped from every persona's toolset.
  ([confirmations](backend/app/services/confirmations/CLAUDE.md),
  [domains](backend/app/services/domains/CLAUDE.md))
- **HITL `"write"` means interactive-only, NOT gated.** AUTO/headless strips every
  `"write"` tool from the interrupt map and executes it unattended. Anything that books
  or moves money is `"irreversible"`.
  ([confirmations](backend/app/services/confirmations/CLAUDE.md),
  [settlement](backend/app/services/settlement/CLAUDE.md))
- **A gated tool whose args are ids needs a `_SUMMARY_BUILDERS` entry**, or the approval
  card shows two integers and the gate is theater.
  ([confirmations](backend/app/services/confirmations/CLAUDE.md))
- **Registered ≠ reachable ≠ discoverable.** Four gates: the vocabulary allows it, REST
  exposes it, the tool surface carries it, and something routes to it. When a model never
  reaches for a working tool, look one level UP from where you think the choice is made —
  the persona picks by tool description, the orchestrator only sees skill `routing:` lines.
  ([domains](backend/app/services/domains/CLAUDE.md),
  [golden_workflows](backend/app/golden_workflows/CLAUDE.md))
- **Three different layers silently swallow a new response field**: a pydantic
  `response_model` drops unnamed keys, a hand-built key projection omits them, and a
  TypeScript type stops the client consuming what is already on the wire. Assert new
  fields at the HTTP layer, not just in a store unit test.
  ([arena](backend/app/services/arena/CLAUDE.md),
  [config](config/CLAUDE.md))
- **`wrap_tool_call` is the only seam that sees a subagent's tool calls.** Scanning
  `result["messages"]` cannot see anything a persona did — it runs in its own checkpoint
  namespace. That is why the audit, booking-capture and binary-read middlewares all live
  there, in every stack including the one the framework adds for you.
  ([deep_agent](backend/app/services/deep_agent/CLAUDE.md))
- **QuantArk is pinned `==0.3.0` — never install it editable.** Its version is part of the
  benchmark's evidence; a sibling working tree as the pricing engine silently moves every
  harvested number.
  ([golden_workflows](backend/app/golden_workflows/CLAUDE.md))
- **Exact-set test assertions break when you add a tool, skill or route.** Adding one
  SKILL.md has broken pins in six test files. Enumerate before you start:
  `grep -rln "<existing-neighbour>" tests/`.
  ([confirmations](backend/app/services/confirmations/CLAUDE.md))
- **A test that constructs its own subject bypasses whatever validates the real one.** It
  proves satisfiability, never reachability — the lesson behind the golden replay, the
  lifecycle vocabulary and the trader-rfq live-reachability fix.
  ([domains](backend/app/services/domains/CLAUDE.md),
  [golden_workflows](backend/app/golden_workflows/CLAUDE.md))
- **A HITL resume must re-stamp the turn's audit context — `mode` and `thread_id`
  included.** LangGraph re-runs the interrupted node from the top; a mode-gated
  `after_model` middleware that sees a different mode on the resume pass skips its
  `interrupt()` and the human's decision is silently dropped.
  ([system_one](backend/app/services/system_one/CLAUDE.md))

---

## Every migration after `0001` must be IDEMPOTENT

`0001_initial` does `from app.models import Base; Base.metadata.create_all(bind=bind)`.
That is not a historical snapshot — it materialises **today's ORM metadata**, so a fresh
database arrives at revision 1 already carrying the *entire current schema* (93 tables,
including ones whose `create_table` migration has not run yet). Every migration after it
therefore executes against a database that already has the modern shape, which makes
existence-guarded DDL a **hard invariant**, not a style preference:

```python
def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())

def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}
```

`0005` and `0052` model the pattern. `0051`, `0053` and `0055` originally did not, and the
chain died at `0051` with `duplicate column name: position_id` — the documented
empty-database path was unusable for months. **`tests/test_migration_fresh_chain.py` is
the standing guard**: it asserts `alembic upgrade head` on an empty DB reaches head, so an
unguarded migration fails CI rather than rotting silently.

Two consequences worth knowing:

- **A migration's body is nearly decorative for fresh databases** (create_all already made
  the objects). It is load-bearing for the LIVE database, which really does replay the
  chain — so write it correctly anyway, and never let a guard skip work a real upgrade needs.
- **A create_all DB also hands a column its ORM foreign key**, which a real historical
  migration may never have created — so `DROP COLUMN` can be illegal on the fresh-chain
  path while it was fine historically. **Never drop a column with a direct
  `op.drop_column`; always use `op.batch_alter_table`.** SQLite refuses
  `ALTER TABLE ... DROP COLUMN` while any FK definition names the column, and batch mode
  rebuilds the table (create new, copy rows, drop old, rename) so the column and its FK
  go together. `recreate="auto"` (the default) is enough — alembic's
  `SQLiteImpl.requires_recreate_in_batch` recreates for **any** op outside
  `add_column`/`create_index`/`drop_index` and never consults the SQLite version, so the
  fact that SQLite ≥ 3.35 supports DROP COLUMN does not tempt it onto the native path.
  `0051` was the only direct-drop site; the eleven batch sites (`0005`, `0012`, `0018`,
  `0047`, …) were already correct. Covered by `tests/test_migration_0051_position_id.py`,
  which pins the rebuild's real risk: that sibling FKs, the other indexes, and the row
  data all survive.

---

## Tests must not assert against moving targets

Four classes of self-invalidating assertion have already bitten this repo. All were red on
`main` for a long time, which trains people to ignore the suite:

- **A frozen `head` literal.** `test_migration_0046`/`0047` hardcoded
  `"0049_hedge_booking_claim"` to mean "head", so every added migration broke them. Ask
  `ScriptDirectory.from_config(config).get_current_head()` instead.
- **Upgrading a synthetic fixture DB to `head`.** `test_migration_0047`'s
  `_old_0046_engine` builds five tables to exercise one migration; targeting `head` drags
  in every later migration against a schema that cannot satisfy them (`0050`'s
  `ALTER TABLE instruments` was the first). **Target the migration under test.**
- **Hand-maintained parallel exclusion lists.** `test_migration_0024` compares
  "0024-only schema" against the *current* ORM behind a list of post-0024 columns; it was
  never updated for `0050`/`0051`. It now derives the FK and index exclusions *from* the
  column list, so only one list can go stale.
- **Reading the developer's `.env`.** Fixed at the source: both dotenv readers now go
  through **`app.config.dotenv_path()`**, which honours `OPEN_OTC_ENV_FILE` (unset =
  repo-root `.env`, empty = no dotenv at all), and `tests/conftest.py` sets it empty
  before the first `app` import. **Any new `.env` reader must use that seam** — being
  hermetic on one path and leaky on another is the same as being leaky. Two things made
  this bite hard: `Settings` is a dataclass whose *field defaults* read `.env`, so
  every `Settings()` was affected, not just `get_settings()`; and
  `channel_registry.load_from_path` used `load_dotenv(override=True)`, which **writes
  into `os.environ`** and republished `.env` over conftest's own pins for every test
  that ran afterwards — so the failure set was order-dependent. A test needing a dotenv
  points `OPEN_OTC_ENV_FILE` at its own fixture file (see `test_config.py`).
- **A leaked `Settings` override.** `create_app` calls `configure_settings`, which
  parks its `Settings` in a process-wide override that `get_settings()` prefers over
  the environment. Seven test files built an app outside the `client` fixture and
  never cleared it, so every later `monkeypatch.setenv(...)` read through
  `get_settings()` was silently ignored — the System One tests passed or failed by
  alphabetical position. `tests/conftest.py::_reset_settings_override` now clears it
  after every test; don't reintroduce per-file `configure_settings(None)` workarounds.
- **Asserting on a gitignored, per-environment file.** `test_agent_channels_router`,
  `test_agent_registry_config` and `test_channel_registry_writer` read the live
  `config/agent_channels.yaml` and hardcoded `zenmux` as the default-holding channel. That
  file is per-env by design **and the Model Maintenance UI rewrites it at runtime**, so the
  tests failed wherever the default had moved (here, `deepseek`). They were hermetic against
  the `AGENT_CHANNELS_FILE` env var but not against the file's contents — those are two
  different axes. Source the **tracked** `config/agent_channels.example.yml`.

---

## Environment traps

- **The DB env var is `OPEN_OTC_DATABASE_URL`, not `DATABASE_URL`** (it is a
  `validation_alias`). Getting it wrong does NOT error — it silently falls back to
  `./data/open_otc.sqlite3`, i.e. the LIVE DB, and reports `exit=0`. The only tell is
  the absence of "Running upgrade" lines.
- **A git worktree needs `config/agent_channels.yaml` copied in.** It is gitignored
  (per-env), so without it every test that imports `app.main` dies at collection with
  `FileNotFoundError`. (Copying `.env` in used to break `test_config.py` /
  `test_tracing_config`; the suite is hermetic now, so it no longer matters either way.)
- The venv's editable-install `.pth` currently points at a deleted worktree, so a bare
  `python -c "import app"` fails. Tests are unaffected: `pyproject.toml` sets
  `pythonpath = ["backend"]` relative to pytest's rootdir.
