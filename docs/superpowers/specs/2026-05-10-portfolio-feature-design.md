# Portfolio Feature Design

**Date**: 2026-05-10
**Status**: Draft — pending implementation
**Author**: Brainstormed with the agent (this session)

## 1. Context

The codebase already has a `Portfolio` table and a `Position` table linked by FK
(`backend/app/models.py:79`, `:96`). Portfolios drive valuation runs, risk runs,
import batches, and reports. What's missing:

- No frontend route for managing portfolios — the existing `'portfolio'` route
  in `frontend/src/types.ts:2` is actually the Positions page, a legacy naming
  artifact.
- Backend has partial CRUD — `GET /api/portfolios` and `POST /api/portfolios`
  exist (`backend/app/main.py:612`), but `PATCH`, `DELETE`, and per-position
  add/remove endpoints don't.
- The agent has no portfolio tools.
- There is no concept of a *filter-based* portfolio — every portfolio today is a
  container that physically owns its positions via a single FK.

This spec adds:
- A new "view" mode on the existing `Portfolio` table, where membership is
  defined by a filter rule + manual includes/excludes + cross-portfolio
  aggregation sources, instead of by FK ownership.
- Tags for organizational filtering of the portfolio list.
- A unified `/portfolios` frontend route covering both kinds.
- Full CRUD over HTTP, CLI, and LangChain tools, sharing one service module.
- Agent-driven creation and management with HITL gates on destructive actions.

The container Portfolio concept (kind = `container`) is unchanged in semantics;
all existing pricer/risk code paths continue to work because membership
resolution is moved behind a resolver that handles both kinds uniformly.

## 2. Goals

**In scope (v1):**

- New view-portfolio kind on the existing `Portfolio` table, distinguished by a
  `kind` column (`container` | `view`).
- Membership of a view = `(rule_matches ∪ source_portfolio_resolved ∪
  manual_includes) − manual_excludes`.
- Filter rule grammar: structured AND/OR/NOT expression tree with
  `eq/ne/in/not_in/lt/lte/gt/gte/between` ops over a fixed field set
  (`product_type`, `underlying`, `status`, `mapping_status`, `engine_name`,
  `quantity`, `entry_price`, `created_at`).
- Power-user DSL toggle in the rule editor (canonical tree ↔ text
  serialization, tree is what's persisted).
- Cross-portfolio aggregation: a view can list other portfolios as sources;
  resolver recursively pulls in their resolved positions, with cycle detection
  and depth limit 3.
- Portfolio-level tags (multi-select organizational labels; do not enter the
  rule grammar).
- Full CRUD across three callers: HTTP API, CLI, LangChain tools — all wrapping
  a shared `portfolio_service.py` module.
- Ten narrow LangChain tools matching existing `langchain_tools.py` style.
- HITL gates on three destructive agent actions: `delete_portfolio`,
  `set_portfolio_rule` (views only), `remove_positions_from_portfolio`
  (containers only).
- Frontend route `/portfolios` with master-detail layout, kind & tag filter
  chips on the list, two-column detail pane (rule editor + live preview
  positions table) for views, owned positions table for containers.
- Eight new frontend components: `RuleBuilder`, `RuleTextEditor`,
  `RuleEditor`, `PositionPicker`, `PortfolioPicker`, `KindChip`, `TagEditor`,
  `ResolvedPositionsTable`.
- Audit events for all writes.
- View portfolios are first-class for valuation runs, risk runs, and reports —
  pricer/risk public signatures are unchanged; only the internal lookup
  changes.
- Each `PositionValuationRun` and `RiskRun` row records its
  `resolved_position_ids` so historical runs are pinned to the membership at
  run time.

**Out of scope (v1):**

- Sharing/permissions per portfolio.
- Rule scheduling / cached resolution. Views resolve fresh on every read.
- Position-level tags (would expand the rule grammar significantly).
- Importing/exporting view definitions.
- Changing `kind` after creation. Users delete and recreate.
- `view-of-view` chains deeper than 3.

## 3. Decisions Locked In

| Decision | Choice |
|---|---|
| Membership semantics | Filter / saved view (no schema change to `Position.portfolio_id`). |
| Naming model | Both kinds are "Portfolio" UI-wise; distinguished by `kind` field. |
| Membership rules | Rule + manual includes + manual excludes, plus source-portfolio aggregation. |
| Capabilities of a view | Full first-class — supports valuation, risk, reports. |
| Filter expressiveness | Structured AND/OR/NOT tree with comparison ops; power-user text DSL toggle in UI. |
| Tool surface | Ten narrow LangChain tools matching existing pattern. |
| HITL gates | `delete_portfolio`, `set_portfolio_rule` (views), `remove_positions_from_portfolio` (containers). |
| Architectural approach | Approach A — single polymorphic table + resolver; existing FKs unchanged. |
| Frontend layout | Master–detail (mirrors Positions route). |
| Detail pane | Two-column: rule editor left, live-preview resolved positions right. |
| Aggregation depth | 3. |
| Dangling source handling | Silent skip + `audit.portfolio.dangling_source` warning. |
| Tag scope | Portfolio-level only in v1. |

## 4. Architecture

### 4.1 Three callers, one service

The same `portfolio_service.py` module is wrapped by three thin adapters:

```
                   ┌──────────────────────────────┐
                   │   portfolio_service.py       │
                   │   (business logic + audit)   │
                   └──────────────┬───────────────┘
                                  │
       ┌──────────────────────────┼──────────────────────────┐
       │                          │                          │
       ▼                          ▼                          ▼
  HTTP API                       CLI                  LangChain tools
  (main.py)                    (cli.py)            (langchain_tools.py)
```

Membership resolution lives in a separate module so the pricer and risk engine
can use it without depending on the higher-level service:

```
portfolio_membership.resolve_positions(portfolio, session) -> list[Position]
```

### 4.2 Polymorphic Portfolio table

The existing `Portfolio` table gains six columns; the table itself is shared by
both kinds:

```python
class PortfolioKind(str, Enum):
    CONTAINER = "container"
    VIEW = "view"

# new on Portfolio
kind: Mapped[str] = mapped_column(String(20), default=PortfolioKind.CONTAINER.value)
filter_rule: Mapped[dict | None] = mapped_column(JSON, nullable=True)
manual_include_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
manual_exclude_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
source_portfolio_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
tags: Mapped[list[str]] = mapped_column(JSON, default=list)
description: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Container portfolios keep the four membership-related columns empty; views
keep `Portfolio.positions` empty (FK ownership is unused for views).

### 4.3 Filter rule grammar

Canonical persisted form is a JSON expression tree:

```json
{ "op": "and", "children": [
  { "op": "eq",  "field": "product_type", "value": "Snowball" },
  { "op": "in",  "field": "underlying", "value": ["AAPL","TSLA"] },
  { "op": "lt",  "field": "quantity", "value": 1000 },
  { "op": "not", "child": { "op": "eq", "field": "status", "value": "closed" } }
]}
```

Supported ops: `and`, `or`, `not`, `eq`, `ne`, `in`, `not_in`, `lt`, `lte`,
`gt`, `gte`, `between`.

Supported fields: `product_type`, `underlying`, `status`, `mapping_status`,
`engine_name`, `quantity`, `entry_price`, `created_at`.

Tree depth ≤ 5 (validation rejects deeper trees).

The text DSL is a parser/serializer pair that round-trips through the canonical
tree. Example: `product_type = Snowball AND underlying IN (AAPL, TSLA)`. Only
the tree is persisted; the text is regenerated on demand.

### 4.4 Membership resolver

```python
MAX_AGGREGATION_DEPTH = 3

def resolve_positions(portfolio, session, *, _visited=None, _depth=0) -> list[Position]:
    _visited = _visited or set()
    if portfolio.id in _visited:
        raise PortfolioCycleError(f"Cycle detected via portfolio {portfolio.id}")
    if _depth > MAX_AGGREGATION_DEPTH:
        raise PortfolioDepthError(f"Aggregation depth exceeded at portfolio {portfolio.id}")
    _visited = _visited | {portfolio.id}

    if portfolio.kind == PortfolioKind.CONTAINER.value:
        return list(portfolio.positions)

    matched: dict[int, Position] = {}
    if portfolio.filter_rule:
        for p in session.query(Position).filter(_compile_rule_to_sqla(portfolio.filter_rule)):
            matched[p.id] = p
    for src_id in portfolio.source_portfolio_ids:
        src = session.get(Portfolio, src_id)
        if not src:
            continue  # silent skip on dangling source; audit emitted at write time
        for p in resolve_positions(src, session, _visited=_visited, _depth=_depth+1):
            matched[p.id] = p
    for inc in portfolio.manual_include_ids:
        if (p := session.get(Position, inc)):
            matched[p.id] = p
    for exc in portfolio.manual_exclude_ids:
        matched.pop(exc, None)
    return list(matched.values())
```

`_compile_rule_to_sqla(rule)` walks the tree and emits a SQLAlchemy `BooleanClauseList`
against `Position` columns.

### 4.5 Pricer / risk integration

`backend/app/services/position_pricer.price_portfolio_positions(portfolio_id, ...)`
swaps its internal `portfolio.positions` access for
`resolve_positions(portfolio, session)`. Public signature is unchanged. Risk
engine does the same. Each `PositionValuationRun` and `RiskRun` row gains a new
JSON column `resolved_position_ids` populated at run time, so the historical
record always knows what it priced.

### 4.6 Audit events

New event types emitted via `services/audit.py`:

- `portfolio.created`
- `portfolio.updated`
- `portfolio.deleted`
- `portfolio.rule_changed`
- `portfolio.positions_added` (manual includes added)
- `portfolio.positions_removed` (manual includes/excludes removed, or container positions removed)
- `portfolio.sources_added`
- `portfolio.sources_removed`
- `portfolio.tags_changed`
- `portfolio.dangling_source` (warning, emitted at write time when a source is created/removed and we detect a previously-referenced source no longer exists)
- `portfolio.run_empty` (warning, emitted when a pricing/risk run resolves to zero positions)

All events use `subject_type='portfolio'`.

## 5. Backend Surfaces

### 5.1 Service layer (`backend/app/services/portfolio_service.py`)

```python
def list_portfolios(session, *, kind=None, tags=None) -> list[Portfolio]: ...
def get_portfolio(session, portfolio_id) -> Portfolio: ...
def create_portfolio(session, *, name, base_currency, kind, filter_rule=None,
                     manual_include_ids=(), manual_exclude_ids=(),
                     source_portfolio_ids=(), tags=(), description=None) -> Portfolio: ...
def update_portfolio(session, portfolio_id, *, name=None, description=None,
                     base_currency=None, tags=None) -> Portfolio: ...
def delete_portfolio(session, portfolio_id) -> None: ...
def set_filter_rule(session, portfolio_id, rule: dict | None) -> Portfolio: ...   # views only
def add_manual_includes(session, portfolio_id, position_ids: list[int]) -> Portfolio: ...
def remove_manual_includes(session, portfolio_id, position_ids: list[int]) -> Portfolio: ...
def add_manual_excludes(session, portfolio_id, position_ids: list[int]) -> Portfolio: ...
def remove_manual_excludes(session, portfolio_id, position_ids: list[int]) -> Portfolio: ...
def add_portfolio_sources(session, portfolio_id, source_ids: list[int]) -> Portfolio: ...
def remove_portfolio_sources(session, portfolio_id, source_ids: list[int]) -> Portfolio: ...
def set_portfolio_tags(session, portfolio_id, tags: list[str]) -> Portfolio: ...
def preview_membership(session, portfolio_id) -> list[int]: ...
def preview_membership_dry_run(session, *, kind, filter_rule=None, manual_include_ids=(),
                               manual_exclude_ids=(), source_portfolio_ids=()) -> list[int]: ...
```

Each writer:
- Emits the matching audit event.
- Validates kind invariants (rule + sources + manual lists rejected on
  containers; container-only operations rejected on views).
- Validates input types (tag length, position id existence, source id
  existence, cycle, depth).

### 5.2 HTTP API (`backend/app/main.py`)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/portfolios?kind=…&tag=…` | List with optional filters; `tag` repeated for AND. |
| `POST` | `/api/portfolios` | Create — body includes all fields. |
| `GET` | `/api/portfolios/{id}` | Detail with resolved positions for views. |
| `PATCH` | `/api/portfolios/{id}` | Update name / description / base_currency / tags. |
| `DELETE` | `/api/portfolios/{id}` | Delete. Container cascades positions; view does not. |
| `PUT` | `/api/portfolios/{id}/rule` | Replace filter rule. Views only. |
| `POST` | `/api/portfolios/{id}/includes` | Add manual includes. |
| `DELETE` | `/api/portfolios/{id}/includes` | Remove manual includes. |
| `POST` | `/api/portfolios/{id}/excludes` | Add manual excludes. |
| `DELETE` | `/api/portfolios/{id}/excludes` | Remove manual excludes. |
| `POST` | `/api/portfolios/{id}/sources` | Add source portfolios. |
| `DELETE` | `/api/portfolios/{id}/sources` | Remove source portfolios. |
| `PUT` | `/api/portfolios/{id}/tags` | Replace tag list. |
| `GET` | `/api/portfolios/{id}/membership` | Resolved position id list for a saved portfolio. |
| `POST` | `/api/portfolios/preview` | Resolve a *draft* (unsaved) view body for live UI preview. |

The legacy `POST /api/portfolios/{id}/positions` (for adding owned positions
to a container) stays valid for `kind='container'` and now returns 400 for
views with a hint to use `/includes`.

`PortfolioOut` schema gains: `kind`, `filter_rule`, `manual_include_ids`,
`manual_exclude_ids`, `source_portfolio_ids`, `tags`, `description`,
`resolved_position_count`.

### 5.3 CLI (`backend/app/cli.py`)

```
open-otc portfolios list [--kind container|view] [--tag x [--tag y]] [--json]
open-otc portfolios show --portfolio <id|name>
open-otc portfolios create --name X --kind container [--base-currency CNY]
        [--description ...] [--tag x [--tag y]]
open-otc portfolios create-view --name X
        [--rule-json @file.json | --rule-text "product_type = Snowball"]
        [--include-ids 1,2,3]
        [--source-id N [--source-id M ...]]
        [--tag x [--tag y]]
open-otc portfolios update --portfolio <id|name>
        [--name ...] [--description ...] [--base-currency ...] [--tag x [--tag y]]
open-otc portfolios delete --portfolio <id|name> --confirm
open-otc portfolios set-rule --portfolio <id|name>
        [--rule-json @file.json | --rule-text "..."]
open-otc portfolios includes add|remove --portfolio <id|name>
        --position-id N [--position-id M ...]
open-otc portfolios excludes add|remove --portfolio <id|name>
        --position-id N [--position-id M ...]
open-otc portfolios sources add|remove --portfolio <id|name>
        --source N [--source M ...]
open-otc portfolios tags set --portfolio <id|name> --tag a [--tag b ...]
open-otc portfolios resolve --portfolio <id|name>
```

`--rule-text` uses the power-user DSL; `--rule-json` reads the canonical tree
from a file. Output is JSON, matching the existing CLI pattern.

### 5.4 LangChain tools (`backend/app/services/langchain_tools.py`)

```python
list_portfolios_tool(kind=None, tags=None) -> dict
get_portfolio_tool(portfolio_id) -> dict                                 # includes resolved positions
create_portfolio_tool(name, kind, base_currency="CNY", description=None,
                      filter_rule=None, manual_include_ids=None,
                      source_portfolio_ids=None, tags=None) -> dict
update_portfolio_tool(portfolio_id, name=None, description=None,
                      base_currency=None, tags=None) -> dict
delete_portfolio_tool(portfolio_id) -> dict                              # HITL
set_portfolio_rule_tool(portfolio_id, filter_rule) -> dict               # HITL (views only)
add_positions_to_portfolio_tool(portfolio_id, position_ids) -> dict
remove_positions_from_portfolio_tool(portfolio_id, position_ids) -> dict # HITL (containers only)
add_portfolio_sources_tool(portfolio_id, source_portfolio_ids) -> dict
remove_portfolio_sources_tool(portfolio_id, source_portfolio_ids) -> dict
```

`add_positions_to_portfolio_tool` adapts behavior by kind: for containers it
adds physical positions (delegating to existing position-creation flow); for
views it adds to `manual_include_ids`. The tool returns the resolved kind in
its response so the agent knows what was done.

HITL is implemented through the existing interrupt machinery in
`AgentService`. Each gated tool registers a structured payload
(`{tool, args, prompt, summary}`) which the frontend renders as an
`ActionProposal` card in the chat thread; resume occurs on approval.

Validation failures return `{ok: False, errors: [...]}` so the agent can
self-correct.

## 6. Frontend

### 6.1 Routing rename

```ts
// frontend/src/types.ts
export type Route = 'chat' | 'rfq' | 'positions' | 'portfolios'
                   | 'risk' | 'reports' | 'client';
```

`'portfolio'` → `'positions'` (existing route — actually the Positions page).
New `'portfolios'`. Updates ripple through `main.tsx`: `navItems`,
`commandItems`, `initialRoute()`, the `useEffect` URL sync.

### 6.2 File structure

```
frontend/src/routes/
  Portfolios.tsx
  Portfolios.live.tsx
  Portfolios.css
  Portfolios.test.tsx
  Portfolios.live.test.tsx
```

Mirrors the existing Positions / Risk / Reports pattern.

### 6.3 New components

| Component | Purpose |
|---|---|
| `RuleBuilder` | Visual editor for the filter expression tree. Field/op/value selectors per condition, nested AND/OR groups via indentation. Emits canonical tree on each edit. |
| `RuleTextEditor` | Textarea for the power-user DSL. Inline parse-error message. |
| `RuleEditor` | Wrapper that holds the canonical tree and render mode. On toggle, serializes/parses through TS-mirrored helpers. Toggle disabled when text is unparseable. |
| `PositionPicker` | Modal: search + multi-select positions; returns id list. Used for manual includes/excludes. |
| `PortfolioPicker` | Modal: search + multi-select portfolios for source aggregation; excludes the current portfolio and known descendants from the picker to prevent cycles via UI. |
| `KindChip` | `view` / `container` badge. Reuses `Chip` primitive. |
| `TagEditor` | Chip-input with autocomplete from existing tags; emits new tag list on commit. |
| `ResolvedPositionsTable` | Read-only positions table; reuses `Table` primitive. |

### 6.4 Master-detail layout

Left list:
- Top filter chips: kind (`All / Container / View`), then tag chips (multi-select AND filter).
- Each row: kind chip, name, position count, up to 3 tag chips with overflow indicator.
- New button creates a portfolio (modal asks for kind first).

Right detail (view kind):
- Header: name, kind chip, inline tag chips, edit/delete buttons.
- KPIs: position count, NAV, PnL, delta, vega.
- Action row: Run pricing, Run risk, Run report.
- Two-column body:
  - Left: `RuleEditor` (with Builder/Text toggle), then Sources section (chip
    list + `PortfolioPicker`), then Manual overrides section
    (Includes chips + `PositionPicker`, Excludes chips + `PositionPicker`).
  - Right: `ResolvedPositionsTable` rendering the live preview.

Right detail (container kind):
- Same header / KPIs / actions.
- Single body column: owned positions table with Add Position / Import buttons
  (matches existing Positions route conventions).

### 6.5 Live preview

While the user edits the rule, sources, or manual lists, a 250ms-debounced
`POST /api/portfolios/preview` resolves the draft membership against the
backend and updates the right-column table. A small "Resolved through 3
sources" hint appears when aggregation crossed source boundaries.

### 6.6 Agent integration

No new portfolio-specific agent UI. The existing `FloatingAgent` pip and
`AgentDesk` overlay reach the agent from any route. HITL prompts render
through the existing `ActionProposal` card pattern.

### 6.7 Command palette

`commandItems` in `main.tsx` gains:

- `jump-portfolios`
- `portfolios-create-container` (opens "New container" modal)
- `portfolios-create-view` (opens "New view" modal)

### 6.8 Tests

| Test module | Coverage |
|---|---|
| `Portfolios.test.tsx` | Master-detail layout, kind/tag filter chips, detail-pane branching by kind. |
| `Portfolios.live.test.tsx` | Mocked API: list refresh after create, debounced live preview, optimistic chip updates with rollback, save flow, HITL confirm dialog before destructive actions. |
| `RuleBuilder.test.tsx` | Add/remove condition, nest AND/OR group, emits canonical tree. |
| `RuleTextEditor.test.tsx` | DSL parse + serialize roundtrip, parse-error display, builder-toggle disabled when text is unparseable. |
| `PositionPicker.test.tsx` | Search filter, multi-select, returns ids. |
| `PortfolioPicker.test.tsx` | Cycle-prone descendants excluded from candidates. |
| `TagEditor.test.tsx` | Autocomplete, lower-casing, duplicate rejection. |
| `KindChip.test.tsx`, `ResolvedPositionsTable.test.tsx` | Standard render tests. |

## 7. Error Handling

### 7.1 Schema-level

- `Portfolio.name` already has `unique=True`. IntegrityError on duplicate
  becomes HTTP 409 with `{detail: "Portfolio name already exists"}`. Service
  re-raises as typed `PortfolioNameConflict`.
- Deleting a container cascades positions (existing
  `cascade="all, delete-orphan"`). Deleting a view leaves positions untouched.
- Historical valuation/risk runs that referenced a now-deleted portfolio remain
  on disk; API responses set `portfolio: null` rather than raising.

### 7.2 Rule validation

| Failure | Behavior |
|---|---|
| Unknown `op` | `validate_rule()` returns `["Unsupported op: matches at $.children[0]"]`; HTTP 400; tool returns `{ok: false, errors: [...]}`. |
| Unknown `field` | Same; lists allowed fields. |
| Wrong value type for op (e.g. `eq` with list) | Listed in errors. |
| Empty `and`/`or` `children` | Listed. |
| Tree depth > 5 | Rejected. |
| `between` with reversed bounds | Listed. |

### 7.3 Manual include/exclude validation

- Position id doesn't exist → `{ok: false, error, missing_ids: [...]}`.
- Same id in both includes and excludes → `RuleValidationError("Position 42 is in both includes and excludes")`.

### 7.4 Source / aggregation validation

| Failure | Behavior |
|---|---|
| Adding a source would create a cycle | HTTP 400 `{detail, cycle_path: [a, b, a]}`. |
| Aggregation depth > 3 at resolve time | HTTP 400 with the chain of portfolio ids that exceeded depth. |
| Source portfolio deleted while still referenced | Resolver silently skips; `audit.portfolio.dangling_source` emitted. UI shows a warning chip in the Sources section. |

### 7.5 Kind invariants

- Setting a rule on `kind='container'` → HTTP 400.
- Adding sources or includes/excludes to a `kind='container'` → HTTP 400.
- Adding owned positions via `POST /api/portfolios/{id}/positions` to a
  `kind='view'` → HTTP 400 with hint to use `/includes`.
- Changing `kind` after creation → not allowed; service rejects.

### 7.6 Tag validation

Tags are lower-cased, ≤40 chars each, deduplicated, must be strings. Errors
are reported per offending tag.

### 7.7 Resolver edge cases

- View with no rule, no sources, empty includes → resolver returns `[]`.
  Pricer/risk runs return successfully with empty results; emit
  `audit.portfolio.run_empty`.
- Rule references a typed field with bad value (e.g. string for
  `created_at`) → resolver coerces; raises `RuleCompilationError`; HTTP 400.

### 7.8 HITL flow

- User declines an HITL prompt → tool returns `{ok: false, error: "User declined"}`; agent thread shows the decline; no DB changes.
- User abandons (closes tab) before approving → existing checkpoint behavior persists the interrupt; on next session the agent thread resumes the prompt.

### 7.9 Frontend errors

- Live preview API failure → preview pane shows "Preview unavailable — save rule to see results"; rule editor stays editable.
- Save fails → toast with the server error message; rule editor retains the unsaved tree.
- Optimistic UI on chip add/remove with rollback on failure.

## 8. Testing Strategy

### 8.1 Backend

| Module | Coverage |
|---|---|
| `tests/test_portfolio_service.py` (new) | All service functions: create/update/delete for both kinds, set_filter_rule, manual includes/excludes, source add/remove, tag set, kind-invariant enforcement, name-conflict error. |
| `tests/test_portfolio_membership.py` (new) | Container resolution, view-with-rule-only, view-with-manual-only, view with both, rule + sources, dangling source skip, dedup across rule + sources + manual, exclude-overrides-include, cycle detection (A→B→A), 3-deep nested aggregation, depth-exceeded error. |
| `tests/test_rule_compiler.py` (new) | All ops × all fields produce correct SQLAlchemy filters. DSL parser roundtrip. Validation error messages. Depth limit. |
| `tests/test_api.py` (extend) | New endpoints: list with kind/tag filter, create view, PUT rule, POST/DELETE includes/excludes/sources, PUT tags, GET membership, POST preview, error responses (cycle 400, depth 400, name conflict 409). |
| `tests/test_cli.py` (extend) | `portfolios list/show/create/create-view/update/delete/set-rule/includes/excludes/sources/tags/resolve`. |
| `tests/test_langchain_tools.py` (extend) | All ten portfolio tools: success path + agent-friendly error responses; HITL gating verified for delete / set-rule / remove-positions; cycle rejection passes structured error to agent. |
| `tests/test_position_pricer.py` (extend) | Pricing run on a view portfolio resolves correct positions; `resolved_position_ids` populated. |
| `tests/test_risk_engine.py` (extend) | Risk run on a view: same. |

### 8.2 Frontend

See section 6.8.

### 8.3 Integration / smoke

- One end-to-end backend test: create container → import positions → create
  view with rule covering them → resolve → run pricing → assert
  `run.resolved_position_ids == resolver_output_ids`.
- One agent integration test (extending `tests/test_agents.py`): conversation
  "create a portfolio with all snowball positions" calls
  `create_portfolio_tool` with `kind='view'`,
  `filter_rule={op:'eq', field:'product_type', value:'Snowball'}`. Optionally
  the agent then proceeds through HITL (approval) for a follow-up destructive
  action.

### 8.4 Performance

With depth ≤ 3 and at most 10 sources per node, resolver cost is O(N) in
unique positions touched. `resolve_positions` time is logged; a warning fires
on >250ms.

## 9. Migration

Single Alembic migration:

- Adds the seven new columns (`kind`, `filter_rule`, `manual_include_ids`,
  `manual_exclude_ids`, `source_portfolio_ids`, `tags`, `description`) to
  `portfolios`.
- Adds `resolved_position_ids` JSON column to `position_valuation_runs` and
  `risk_runs`.
- Reversible: downgrade drops these columns.
- No data migration required: defaults make existing rows `kind='container'`
  with empty membership-related lists.

## 10. Build sequence

The implementation plan is the next deliverable (writing-plans skill). At a
high level the sequence is:

1. Schema migration + model fields + audit event types.
2. Resolver module + rule compiler + DSL parser/serializer + unit tests.
3. Service layer + service-level unit tests.
4. HTTP endpoints + extended `tests/test_api.py`.
5. CLI subcommands + extended `tests/test_cli.py`.
6. LangChain tools + HITL wiring + extended `tests/test_langchain_tools.py`.
7. Integration test: end-to-end pricing-on-a-view.
8. Frontend route rename and Portfolios route scaffolding.
9. New components (RuleBuilder, RuleTextEditor, RuleEditor, PositionPicker,
   PortfolioPicker, KindChip, TagEditor, ResolvedPositionsTable) with their
   unit tests.
10. `Portfolios.live.tsx` integration; live preview wiring; HITL confirm
    dialog parity.
11. Command palette additions.
12. Manual smoke pass through happy paths and HITL flows.
