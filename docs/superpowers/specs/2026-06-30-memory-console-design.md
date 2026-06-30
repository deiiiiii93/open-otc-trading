# Memory Console — Design Spec

**Date:** 2026-06-30
**Status:** Design approved; ready for implementation plan
**Author:** brainstormed with the desk owner

## Problem

The long-term cross-session memory feature (merged to `main` at `666719f`, extended at `33d2179`) shipped **headless**: it exposes a REST API (`/api/memory/*`) but no frontend page. Consequences:

- **Proposed `domain` facts can only be approved via curl.** The memory design stages domain knowledge as `status="proposed"` and requires a human to approve it through `POST /api/memory/facts/{id}/approve` before it is ever injected into the agent. With no UI, that human-in-the-loop gate is effectively unusable.
- The desk cannot **see what the agent remembers** (per scope), cannot **correct or delete** wrong facts, cannot **pin** a fact to protect it from cap-eviction, and cannot see a fact's **provenance** (extractor vs human, which model, which session).
- There is no surface for the `/api/memory/status` health/config view (enabled flag, confidence floor, budgets, per-scope counts).

This spec defines a **Memory Console** page: a full management console over the existing memory API, plus the small backend additions needed to surface provenance and pinning.

## Decisions (locked during brainstorming)

1. **Scope of the page:** Full management console — browse facts across all four scopes (`user`, `book`, `domain`, `correction`), approve proposed domain facts, edit, delete (archive), pin/unpin, and manually create facts.
2. **Provenance + pin/unpin:** Surface provenance (`created_by`, extractor model, source session) and provide a pin/unpin control. Requires small backend additions (below).
3. **Layout:** Table + scope Tabs + edit Modal (mirrors the existing Skills page primitives). Not master-detail, not an approval-only dashboard.
4. **Book scope selection:** A portfolio dropdown. The stored book `scope_id` is the **stringified portfolio integer id** (`resolve_book_scope` returns `("book", str(portfolio_id))`), so the dropdown sources `{id, name}` from `GET /api/portfolios` (the `PortfolioOut` rows, which carry `id`), displays `name`, and uses `String(id)` as both the filter `scope_id` and the create `scope_id`. The existing `listPortfolios()` helper discards the id (maps to names) and is therefore unusable here; a new client fn returns `{id, name}` pairs.
5. **Refresh model:** Pull-only. The user's own mutations take effect immediately via explicit refetch; external changes (background extractor, other admins) appear via a **Refresh** button. No websocket, no polling.
6. **Pin endpoint shape:** A single `PATCH /api/memory/facts/{id}/pin` with body `{pinned: bool}` (not separate `/pin` + `/unpin`).
7. **Disabled state:** When `status.enabled === false`, show a non-blocking banner but keep the console fully usable — the REST API works regardless of the capture flag, and this page is the admin surface for it.

## Backend additions

All mutations continue to go through `MemoryStore` (the established invariant: the store centralizes caps, dedup, pinned-protection, and status transitions).

### B1. Thread provenance through `Fact`
`backend/app/services/deep_agent/memory/store.py`

- `Fact` already carries `pinned: bool`. Add `created_by: str` and `meta: dict` to the `Fact` dataclass.
- `_to_fact(row)` populates the two new fields from the `MemoryEntry` row (`row.created_by`, `row.meta`).
- These are **read-only passthroughs**; no behavior change to create/update/status/archive.

### B2. `MemoryStore.set_pinned`
`backend/app/services/deep_agent/memory/store.py`

```python
def set_pinned(self, session, fact_id, pinned: bool) -> Fact:
    """Set the pinned flag. Raises MemoryNotFound if absent; raises
    MemoryConflictError if the row is archived (archived facts are read-only).
    Pinned facts are protected from cap-eviction (see create/apply_diff)."""
```

Follows the shape of the existing `set_status`: load row (or raise `MemoryNotFound`); if `row.status == "archived"` raise `MemoryConflictError("archived is read-only")`; else set `row.pinned`, bump `updated_at`, `session.flush()`, return `_to_fact(row)`. (This server-enforces the archived-immutability the UI also presents, rather than leaving it UI-only.)

### B3. Extend `FactOut` and the pin endpoint
`backend/app/routers/memory.py`

- `FactOut` gains: `pinned: bool`, `created_by: str`, `extractor_model: str | None`, `source_session_id: int | None`. The last two are read from `fact.meta` (`meta.get("extractor_model")`, `meta.get("session_id")`) in `_out()`.
- New handler:
  ```python
  class FactPin(BaseModel):
      pinned: bool

  @router.patch("/facts/{fact_id}/pin")
  def pin_fact(fact_id: int, body: FactPin):
      # MemoryStore.set_pinned; map MemoryNotFound -> 404, MemoryConflictError -> 409
  ```
- **Archived read-only (server-enforced, all mutation paths):** `update`, `set_pinned`, and `set_status` raise `MemoryConflictError` when `row.status == "archived"`; `archive` on an already-archived row is an idempotent no-op (returns success). `patch_fact`/`pin_fact`/`approve_fact` map that conflict to `409`. This makes the archived-immutability the UI presents real on the server too, consistently across edit/pin/approve, not just pin. (`create_fact`, `delete_fact`, `memory_status` request/response shapes are otherwise unchanged.)

## Memory semantics (store-enforced — the UI mirrors these, does not reinvent them)

These rules already live in `MemoryStore.create` / `set_status` / `_validate_new`; the console must present them faithfully.

### Create matrix (per scope)
The create Modal sends `{scope_type, scope_id?, content, confidence, category?}`. The store derives the rest:

| scope_type   | `scope_id` sent            | resulting `status` | `pinned` | `source_error` |
|--------------|----------------------------|--------------------|----------|----------------|
| `user`       | omitted → store uses `desk`| `active`           | `true`   | `false`        |
| `correction` | omitted → store uses `desk`| `active`           | `true`   | `true`         |
| `domain`     | omitted → store uses `global` | `proposed`      | `false`  | `false`        |
| `book`       | `String(portfolio.id)` (required) | `active`    | `true`   | `false`        |

Notes: the router maps `user/correction→desk`, `domain→global` (`_CANON`); `book` requires a non-empty `scope_id` or the API returns `400`. A manually created **domain** fact lands as `proposed` (same as extractor output) and must be approved before injection — the console shows it in the proposed queue immediately after creation. `api`-created non-domain facts are auto-pinned by the store (intentional: human-entered facts are protected from cap-eviction).

### Validation (server is authoritative; client checks only the observable subset)
The server enforces the full rule set; the client validates **only** the checks it can perform without duplicating server internals, and relies on the server's `400`/`409` for the rest (the `_out()`/error contract surfaces the message).
- **content:** *Server:* required; non-empty after normalization; `len ≤ content_max_chars` (2000); must pass the denylist (`is_memorable`). *Client:* disables Save when content is empty/whitespace or `len > 2000`. The client does **not** reimplement normalization or the denylist — a denylist/normalize rejection comes back as a `400` shown in the modal.
- **confidence:** *Server:* must be within `[confidence_floor, 1.0]`. *Client:* the number input prefills **1.0** with `min={floor} max={1} step={0.01}` (`floor` read from `/status.config.confidence_floor`, not hard-coded). No auto-clamping — an out-of-range value stays visible and **disables Save** with an inline message ("confidence must be between {floor} and 1.0"). Required on create.
- **category:** optional. *Server:* `_clean_category` **strips leading/trailing whitespace and truncates to `category_max_chars` (64)**, mapping blank → `null`. *Clearing on edit:* `FactPatch.category` distinguishes "unchanged" (field omitted) from "clear" — the edit modal sends `category: ""` when the user clears a previously-set category, and the server maps blank → `null` (the plan verifies `_clean_category("")` returns `null`; add the mapping if it does not). Sending a non-empty string sets it; omitting the field leaves it unchanged.
- **dedup:** a normalized-content duplicate within the same `(scope_type, scope_id)` raises `MemoryConflictError`→`409`; the modal surfaces the message ("duplicate").

### Action matrix (by status)
Action visibility is gated by **`status` only**. (Note: `Fact.mutable == not pinned` — it is the *pin state*, surfaced by the Pin/Unpin toggle, **not** an action gate. The store permits editing/deleting pinned facts; pinning only protects from automatic cap-eviction.)

| status     | Approve | Pin/Unpin | Edit | Delete |
|------------|---------|-----------|------|--------|
| `proposed` | ✅ (→approved) | ✅   | ✅   | ✅     |
| `active`   | —       | ✅        | ✅   | ✅     |
| `approved` | —       | ✅        | ✅   | ✅     |
| `archived` | —       | —         | —    | —  (read-only; no un-archive endpoint exists) |

Exact predicates: **Approve** iff `status === "proposed"`; **Pin/Unpin**, **Edit**, **Delete** iff `status !== "archived"`. The Pin/Unpin toggle label/icon reflects `pinned` (`pinned ? "Unpin" : "Pin"`). `Approve` calls `POST …/approve` (store transitions `proposed→approved`, which also sets `pinned=true`). If the server rejects an action with `409` (illegal transition, archived row, or already in the target state from a stale view), the row's feedback shows the message and the view refetches. Archived rows render with all actions hidden; the server independently rejects mutations on archived rows.

### Provenance contract (`_out()` coercion)
`created_by` is a NOT-NULL column (DB default `"extractor"`), so every row has a non-empty string today; values in use are `"extractor"` and `"api"`. **Badge rule (deterministic):** `"extractor"` → `info` badge "extractor"; `"api"` → `ink` badge "api"; any other **non-empty** value → `ink` badge rendering the value verbatim (forward-compatible with e.g. `"migration"`); empty/whitespace → `ink` badge "unknown". The two `meta`-derived fields are defensive against legacy/malformed rows: in `_out()`, `m = fact.meta if isinstance(fact.meta, dict) else {}`; `extractor_model = m["extractor_model"]` only when it `isinstance(str)`, else `null`; `source_session_id = m["session_id"]` only when it `isinstance(int)`, else `null`. Old rows with `meta == {}` yield `null` for both — the UI shows an em-dash.

## Frontend architecture

Mirrors the Skills page: a presentational `.tsx`, a container `.live.tsx`, and a co-located token-only BEM `.css`.

### F1. `frontend/src/routes/Memory.tsx` (presentational)
- `PageScaffold` with title "Memory" and **status chips** from `/status`. The chip set is exactly: an **`enabled`** chip (pos when `enabled`, ink when off); one **count chip per scope** — `user`, `book`, `domain`, `correction` — each showing that scope's **non-archived** total `= Σ over status∈{active,proposed,approved} of counts[scope][status]` (missing keys read as 0); and one **attention chip `proposed N`** = `Σ over scope of counts[scope]["proposed"]`, rendered `warn` when `N > 0` and hidden when `N == 0`. All sums tolerate missing `counts[scope]` / status keys (treated as 0). Below the chips, a small **config caption** renders the `/status.config` values the operator cares about: `floor 0.7 · budget 2000 / corr 1000 · cap 100 / corr 20` (confidence_floor, injection_token_budget, correction_token_budget, max_facts_per_scope, max_correction_facts). This satisfies the stated goal of surfacing the health/config view, and the same `confidence_floor` feeds the create modal's validation.
- `Tabs`: `User | Book | Domain | Correction | All`.
- `PageToolbar`: status filter `select`, search input, a portfolio `select` **only on the Book tab**, `[Refresh]`, `[+ New]`. The status options map to the API's `status` param: **`Current`** (omit the param → server returns all non-archived; this is the **default** so proposed domain facts are visible without changing the filter), `Proposed` (`status=proposed`), `Approved` (`status=approved`), `Active` (`status=active`), `Archived` (`status=archived`), `All` (`status=all`).
- `Table<FactRow>` columns:
  - **Scope** — shown only on the **All** tab (a `Badge` with `scope_type`, and `scope_id` for book rows); hidden on the per-scope tabs where it is redundant.
  - **Status** — `Badge` (proposed→`warn`, active/approved→`pos`, archived→`ink`).
  - **Content** — wraps; no horizontal scroll.
  - **Confidence** — numeric, `--font-numeric`.
  - **Category** — text (or em-dash when null).
  - **Source** — provenance `Badge` over `created_by` (per the Badge rule below), with `extractor_model` and `session #N` rendered as **visible secondary caption text** beneath the badge (not hover-only — discoverable and accessible; an em-dash when both are null). The badge does not assume any value beyond what `created_by` actually contains.
  - **Actions** — per the Action matrix: `Approve` (only when `status === "proposed"`), Pin/Unpin toggle (label from `pinned`), `Edit`, `Delete`; all hidden when `status === "archived"`. A row whose mutation is in flight has its buttons disabled (`rowBusy`).
- Below the table: a count note **"Showing {visible} of {loaded} loaded · {total} total"** (per Volume → Count copy), and a **`[Load more]`** button shown only when `facts.length < total`.
- Create/Edit `Modal`: fields `content` (textarea), `confidence` (number, `[floor, 1.0]` per Validation), `category` (text). On **create**, also `scope_type` (select) and — when `scope_type === "book"` — a portfolio `select` (required). **Save is disabled** unless: content is non-empty, confidence is in `[floor, 1.0]`, **and** (when `scope_type === "book"`) a portfolio id is selected. If portfolios failed to load or the list is empty, the book option is disabled with a hint ("no portfolios available") so a book fact cannot be started without a target. Scope is fixed after creation (not editable).
- **Delete confirmation:** Delete archives the fact and there is no un-archive path, so it is **not** immediate — it opens a small confirm `Modal` ("Archive this fact? It will no longer be injected and cannot be restored from this page."); only on confirm does it call `deleteMemoryFact`.

### F2. `frontend/src/routes/Memory.live.tsx` (container)
- State: `facts, total, nextOffset, status, loading, error, feedback, activeScope (default 'all'), statusFilter (default 'current'), search, portfolios, portfoliosError, selectedPortfolio (default null = "All portfolios"), modal, modalSaving (bool), rowBusy (Set of fact ids with a mutation in flight), reqSeq`.
- **Initial landing:** `activeScope` defaults to **`'all'`** and `statusFilter` to **`'current'`** so a freshly-proposed `domain` fact (which sorts first) is visible on first render without changing any control.
- **Portfolios:** loaded **on mount** via the new `listPortfoliosWithIds()` (single, well-defined trigger — so opening `+ New → book` from any tab already has the list). On failure set `portfoliosError`; the toolbar/modal show an inline error but the rest of the page works. The Book tab with `selectedPortfolio === null` fetches **all** book facts (`scope_type=book`, no `scope_id`); selecting a portfolio narrows to `scope_id=String(id)`. With **no** portfolios, the dropdown shows a disabled "No portfolios" option (Book tab still lists existing book facts; the create modal's book option is disabled with a hint).
- **Fetch + stale guard:** a single `loadView(reset: boolean)` runs `listMemoryFacts({...})` and `getMemoryStatus()` for the current scope/status/portfolio. It captures **one** `token = ++reqSeq` for the whole logical refresh and applies it to **both** responses; if `token !== reqSeq` when either resolves, **both** are discarded (prevents a tab switch mid-flight from mixing results).
- **Offset semantics:** `nextOffset` = the offset for the *next* Load-more fetch. `reset=true` (mount, tab/status/portfolio change, Refresh) fetches `offset=0`, **replaces** `facts`, sets `total` from the response, and sets `nextOffset = items.length`. `reset=false` (Load-more) fetches `offset=nextOffset`, **appends**, and advances `nextOffset += items.length`. `[Load more]` shows iff `facts.length < total`.
- **Partial failure:**
  - *After a prior successful load:* one call rejecting → set `feedback`, keep prior `facts`/`status`.
  - *On initial load:* if **facts fail** → full `Empty variant="error"` (the table is the page). If **facts succeed but status fails** → render the table, **hide the chips/config caption**, show a non-blocking "status unavailable — retrying via Refresh" note, and fall back to the default confidence floor **0.7** for the create modal until `/status` loads. Tests cover both initial partial-failure directions.
- **Mutations:** `approve`, `setPinned`, `save`, `remove`. Row mutations add the fact id to `rowBusy` (disabling that row's buttons → no double-submit), call the API, then `loadView(reset=true)`, then clear `rowBusy`. **Create/edit submit** uses the separate **`modalSaving`** flag (the new fact has no id yet): set on submit, disables the modal Save button, cleared on success (close modal + `loadView(reset=true)`) or error (show message in modal, keep it open).
- `[Refresh]` and `[Load more]` both call `loadView` (`reset=true` / `reset=false` respectively), incrementing `reqSeq`.

### F3. `frontend/src/routes/Memory.css`
- Token-only BEM (`wl-memory__*`). Verified in light + dark themes and compact density. No hardcoded hex/px, no `var(--token, #fallback)`.

### F4. `frontend/src/api/client.ts`
```ts
listMemoryFacts(params: { scope_type?: string; scope_id?: string; status?: string; limit?: number; offset?: number })
  -> { items: MemoryFact[]; total: number }
createMemoryFact(body: { scope_type; scope_id?; content; confidence?; category? }) -> MemoryFact
patchMemoryFact(id, body: { content?; confidence?; category? }) -> MemoryFact
approveMemoryFact(id) -> MemoryFact
setMemoryFactPinned(id, pinned: boolean) -> MemoryFact      // PATCH /facts/{id}/pin
deleteMemoryFact(id) -> void                                 // 204
getMemoryStatus() -> MemoryStatus
listPortfoliosWithIds() -> Array<{ id: number; name: string }>   // GET /api/portfolios, keep id (existing listPortfolios drops it)
```
`listMemoryFacts` always passes an explicit `limit` (see Data flow → Volume); the response's `total` is retained in state to drive the overflow banner.
Uses the existing `api<T>(path, init)` wrapper (JSON headers, throws on non-2xx, returns `undefined` for 204).

### F5. Types + routing wiring
- `frontend/src/types.ts`: add `MemoryFact` and `MemoryStatus` types; add `'memory'` to the `Route` union.
- `frontend/src/lib/routing.ts`: `ROUTE_PATHS.memory = '/memory'`.
- `frontend/src/main.tsx`: nav item `{ route: 'memory', label: 'Memory' }`, a command-palette "Jump To → Memory" entry, the route renderer `{route === 'memory' && <MemoryLive .../>}`, and the import of `MemoryLive`.

## Data flow

- **Server-side filters:** `scope_type` maps to the API `scope_type` param (omitted on the All tab); the status `select` maps to the `status` param as described in F1 (the default `Current` omits the param, yielding all non-archived facts); the Book tab adds `scope_id=<portfolio>`. **Search is client-side** over `content`/`category` (the API has no search param).
- **Status chips** come from `GET /api/memory/status` (returns `enabled`, `config`, and `counts[scope][status]`).
- **After any mutation:** refetch the active view + status so the user's change is immediately visible. **External changes** (the background extractor writing a new `proposed` fact, or another admin) are not pushed — they appear on the next mutation or when the user clicks **Refresh**.

### Ordering (server-defined, stable across pages)
The existing `list_facts` sorts the full filtered set, then slices `offset:offset+limit`, by: **status order** (`proposed` < `approved` < `active` < `archived`), then **confidence DESC**, then **`updated_at` DESC**. Two consequences the console relies on: (a) **proposed facts always sort first**, so they are visible on the first page even when a view is truncated — this makes "proposed visible on first render" deterministic; (b) the order is stable across `offset` pages (the server re-sorts the whole set each call), so Load-more appends consistently. The console must not re-sort client-side (it would fight the server order); it preserves response order and only applies the client-side search filter.

### Volume & Load-more
Caps bound a **single scope's** non-archived facts (`max_facts_per_scope = 100`, `max_correction_facts = 20`), so the per-scope tabs (User / Domain / Correction) and a **single-portfolio** Book view never exceed one `limit=100` page. But the **All** tab (sum of all scopes) and the Book **"All portfolios"** view (book caps are *per portfolio* → unbounded across many portfolios) can exceed a page. The console therefore:
- Fetches each view with **`limit=100`** starting at `offset=0`, keeping `total` from the response (offset bookkeeping per F2 → "Offset semantics").
- When `facts.length < total`, shows a **`[Load more]`** control that fetches the next page and **appends** (same scope/status/portfolio). This works for both the aggregate views and a large `archived` backlog.
- **Count copy:** the note reads **"Showing {visible} of {loaded} loaded · {total} total"**, where `visible` = rows after the client search filter, `loaded` = `facts.length`, `total` = the server count. With no active search, `visible == loaded`. Client-side **search** operates over the loaded rows only; this copy makes the partial-set limitation explicit, and Load-more pulls the rest. (Server-side search is out of scope — the API has no search param.)

### Search algorithm
Search is **case-insensitive substring** matching, applied **client-side after** the server scope/status filtering, over each fact's `content` and its `category` when non-null (the query is trimmed; an empty query disables filtering). It does not fetch; it filters the already-loaded page.

## Failure handling

- **Client error contract:** `api<T>()` throws `new Error(await response.text())`, so `error.message` is the raw response **body** (FastAPI returns `{"detail": "..."}`). A small shared helper `errorMessage(err)` parses the body as JSON and returns `parsed.detail` when present, else the raw text, else a generic fallback. The container uses it for all surfaced errors; tests mock rejections with `new Error('{"detail":"duplicate"}')` and assert the cleaned text. (No change to `api<T>` itself.)
- `api<T>()` throws on non-2xx; the container catches and renders `Empty variant="error"` (initial load) or a `PageScaffold`/modal feedback message (mutation). Loading → `Empty variant="loading"`. Empty result → `Empty variant="empty"` with a hint.
- `409` (dedup conflict on create/edit, status conflict on approve, or archived-row mutation) surfaces the parsed message in the feedback zone. `404` on a stale row triggers a `loadView(reset=true)`. `204` (delete) is treated as success.
- **Disabled banner:** if `status.enabled === false`, a non-blocking banner reads "Memory capture is off (`OPEN_OTC_MEMORY`) — existing facts are still editable here." The console stays fully functional.

## Access & visibility

The open-otc-trading app is a **single-operator desk tool**: it has no authentication, user accounts, or role system today (the `user`/`correction` scope id is the constant `desk`, and no router enforces authorization). The Memory page therefore needs **no route guard** and is visible in the nav like every other page. Surfacing all four scopes is acceptable under that single-operator assumption. (If multi-user/RBAC is ever added, gating this admin surface — and the underlying API, which is also ungated, tracked as memory follow-up #6 — becomes part of that separate effort; it is explicitly out of scope here.)

## Testing

- **Backend** (`tests/test_memory_api.py`, extend):
  - `FactOut` now includes `pinned`, `created_by`, `extractor_model`, `source_session_id`, with `extractor_model`/`source_session_id` read from `meta`.
  - **Malformed/legacy meta:** a row with `meta == {}` yields `null` for both provenance fields; a row with `meta` containing a non-str `extractor_model` or non-int `session_id` also yields `null` (coercion, no 500).
  - `PATCH /facts/{id}/pin` round-trips `pinned` true→false; `404` on unknown id.
  - `MemoryStore.set_pinned` unit coverage (pins, unpins, raises `MemoryNotFound`).
  - **Book filter:** creating a `book` fact under `scope_id="<id>"` and listing `?scope_type=book&scope_id=<id>` returns it; a different id does not.
  - **Create matrix:** a `domain` create returns `status="proposed"`; a `user` create returns `status="active"` and `pinned=true`; a `book` create with no `scope_id` returns `400`.
  - **Validation:** below-floor confidence → `400`; duplicate content in a scope → `409`.
  - **Archived read-only (all paths):** `set_pinned`, `update`, and `set_status` on an archived row → `409`; the `PATCH …/pin`, `PATCH /facts/{id}`, and `POST …/approve` endpoints surface it as `409`; `delete` on an already-archived row is an idempotent success.
  - **Category clear:** `PATCH` with `category: ""` clears a previously-set category to `null`; omitting `category` leaves it unchanged; a non-empty value sets it (and is whitespace-stripped + truncated to 64).
  - **Ordering:** `list_facts` returns `proposed` rows before `active`/`approved` regardless of insertion order (so the console's first page shows proposed facts); `offset` paging is stable.
- **Frontend** (`frontend/src/routes/Memory.live.test.tsx`, new — match the existing route-test pattern with a mocked `api/client`):
  - Renders facts; **first render lands on the `All` tab with `Current` filter and shows a proposed `domain` fact**.
  - Switching scope tab re-fetches with the right `scope_type`; status filter select maps to the right `status` param (`Current` omits it).
  - Approve / pin-toggle / edit / delete call the right client fn and refetch; a row's buttons are disabled while its mutation is in flight; a **pinned `active`** row still shows Edit/Delete (gating is by status, not `mutable`); an **archived** row shows no actions.
  - **Create flow:** opening New from the default `All` tab, choosing `book`, selecting a portfolio (loaded on mount), submitting calls `createMemoryFact` with `scope_id=String(id)`; double-clicking Save (via `modalSaving`) issues **one** create request.
  - **Load more:** when `facts.length < total`, the control appears and appends the next page (advancing `nextOffset`); the count note reflects loaded vs total; the error-contract helper turns `{"detail":"duplicate"}` into "duplicate" on a `409`.
  - **Delete confirm:** clicking Delete opens the confirm modal; `deleteMemoryFact` fires only on confirm, not on the first click.
  - **Initial partial failure:** facts-success + status-failure renders the table with chips hidden and the floor-0.7 fallback; facts-failure renders the error empty state.
  - **Book tab:** loads portfolios via `listPortfoliosWithIds`; portfolio-fetch failure renders the toolbar error but keeps the table; "no portfolios" renders the disabled option.
  - Status chips render the defined counts; search filters the loaded rows case-insensitively.
  - Disabled banner shows when `status.enabled === false`. `409` on a mutation surfaces the server message; error state renders on a rejected fetch; Refresh re-fetches. Overflow banner shows when `total > items.length`.
- **Route guard:** check for any nav/route snapshot or "exact route set" test; update it if the new `'memory'` route trips it.

## Out of scope (YAGNI)

- No **automatic** live refresh — no websocket, no polling. (Pull-only via the Refresh button.)
- No bulk actions (multi-select approve/delete).
- No multi-user identity — the `user`/`correction` scope id stays the constant `desk`.
- No changing a fact's `scope_type`/`scope_id` after creation.
- **No infinite scroll / no server-side search.** Paging is an explicit `[Load more]` button (offset-based, `limit=100`), not auto-loading on scroll; search is client-side over loaded rows (the API has no search param). (Load-more itself is **in scope** — see Volume & Load-more.)

## Resolved during review (was: open verification items)

1. **Book `scope_id` format — RESOLVED.** `resolve_book_scope` stores `("book", str(portfolio_id))`, i.e. the **stringified portfolio integer id**. `listPortfolios()` returns only names and is unusable; the design adds `listPortfoliosWithIds()` (`GET /api/portfolios` → `{id, name}`), displays `name`, and uses `String(id)` for filtering and creation. Covered by the book-filter backend test and the create-flow frontend test.
2. **Provenance `meta` keys — RESOLVED.** The extractor write path (`queue.run_job` → `WriteContext.meta`) persists `meta["extractor_model"]` and `meta["session_id"]`. `_out()` reads exactly those keys with type-guarded coercion (str / int else `null`), so malformed or legacy `{}` meta degrades to `null` rather than erroring. Covered by the malformed-meta backend test.

3. **`PortfolioOut.id` — RESOLVED.** `PortfolioOut` (`backend/app/schemas.py:693`) declares `id: int` and `name: str`, so `GET /api/portfolios` already serializes the id; `listPortfoliosWithIds()` consumes the existing response with **no backend change**.

4. **`mutable` semantics — RESOLVED.** `_to_fact` sets `mutable = not row.pinned` — i.e. the pin state, not an edit/archive gate. The console gates actions by `status` only and uses `pinned` for the Pin/Unpin toggle (see Action matrix).
