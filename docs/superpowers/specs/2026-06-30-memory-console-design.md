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
4. **Book scope selection:** A portfolio dropdown populated by the existing `listPortfolios()` (`GET /api/portfolios`), not free text.
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
    """Set the pinned flag. Raises MemoryNotFound if the row is absent.
    Pinned facts are protected from cap-eviction (see create/apply_diff)."""
```

Follows the shape of the existing `set_status`: load row (or raise `MemoryNotFound`), set `row.pinned`, bump `updated_at`, `session.flush()`, return `_to_fact(row)`.

### B3. Extend `FactOut` and the pin endpoint
`backend/app/routers/memory.py`

- `FactOut` gains: `pinned: bool`, `created_by: str`, `extractor_model: str | None`, `source_session_id: int | None`. The last two are read from `fact.meta` (`meta.get("extractor_model")`, `meta.get("session_id")`) in `_out()`.
- New handler:
  ```python
  class FactPin(BaseModel):
      pinned: bool

  @router.patch("/facts/{fact_id}/pin")
  def pin_fact(fact_id: int, body: FactPin):
      # MemoryStore.set_pinned; map MemoryNotFound -> 404
  ```
- Existing handlers (`list_facts`, `create_fact`, `patch_fact`, `approve_fact`, `delete_fact`, `memory_status`) are unchanged.

## Frontend architecture

Mirrors the Skills page: a presentational `.tsx`, a container `.live.tsx`, and a co-located token-only BEM `.css`.

### F1. `frontend/src/routes/Memory.tsx` (presentational)
- `PageScaffold` with title "Memory" and **status chips** from `/status`: an `enabled` chip and per-scope count chips (`user N · book N · domain N · proposed N`).
- `Tabs`: `User | Book | Domain | Correction | All`.
- `PageToolbar`: status filter `select`, search input, a portfolio `select` **only on the Book tab**, `[Refresh]`, `[+ New]`. The status options map to the API's `status` param: **`Current`** (omit the param → server returns all non-archived; this is the **default** so proposed domain facts are visible without changing the filter), `Proposed` (`status=proposed`), `Approved` (`status=approved`), `Active` (`status=active`), `Archived` (`status=archived`), `All` (`status=all`).
- `Table<FactRow>` columns:
  - **Scope** — shown only on the **All** tab (a `Badge` with `scope_type`, and `scope_id` for book rows); hidden on the per-scope tabs where it is redundant.
  - **Status** — `Badge` (proposed→`warn`, active/approved→`pos`, archived→`ink`).
  - **Content** — wraps; no horizontal scroll.
  - **Confidence** — numeric, `--font-numeric`.
  - **Category** — text (or em-dash when null).
  - **Source** — provenance `Badge` over `created_by` (the values the write paths actually use: `extractor` for the LLM extractor, `api` for facts created through this console / the REST API). Extractor rows show `extractor_model` + source session on hover (title attr). The badge does not assume any value beyond those `created_by` actually contains.
  - **Actions** — `Approve` (only when `status === "proposed"`), Pin/Unpin toggle (reflects `pinned`), `Edit`, `Delete`.
- Create/Edit `Modal`: fields `content` (textarea), `confidence` (number), `category` (text). On **create**, also `scope_type` (select) and — when `scope_type === "book"` — a portfolio `select`. Scope is fixed after creation (not editable).

### F2. `frontend/src/routes/Memory.live.tsx` (container)
- State: `facts, status, loading, error, activeScope, statusFilter, search, selectedPortfolio, modal, mutating`.
- Effects: on mount, and whenever `activeScope` / `statusFilter` / `selectedPortfolio` change → fetch `listMemoryFacts(...)` + `getMemoryStatus()`.
- Mutation handlers (`approve`, `setPinned`, `save` (create/patch), `remove`): call the API, then refetch the current view + status; on error set a feedback message.
- `[Refresh]` re-runs the same fetch for the current scope/filter.

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
```
Uses the existing `api<T>(path, init)` wrapper (JSON headers, throws on non-2xx, returns `undefined` for 204).

### F5. Types + routing wiring
- `frontend/src/types.ts`: add `MemoryFact` and `MemoryStatus` types; add `'memory'` to the `Route` union.
- `frontend/src/lib/routing.ts`: `ROUTE_PATHS.memory = '/memory'`.
- `frontend/src/main.tsx`: nav item `{ route: 'memory', label: 'Memory' }`, a command-palette "Jump To → Memory" entry, the route renderer `{route === 'memory' && <MemoryLive .../>}`, and the import of `MemoryLive`.

## Data flow

- **Server-side filters:** `scope_type` maps to the API `scope_type` param (omitted on the All tab); the status `select` maps to the `status` param as described in F1 (the default `Current` omits the param, yielding all non-archived facts); the Book tab adds `scope_id=<portfolio>`. **Search is client-side** over `content`/`category` (the API has no search param).
- **Status chips** come from `GET /api/memory/status` (returns `enabled`, `config`, and `counts[scope][status]`).
- **After any mutation:** refetch the active view + status so the user's change is immediately visible. **External changes** (the background extractor writing a new `proposed` fact, or another admin) are not pushed — they appear on the next mutation or when the user clicks **Refresh**.

## Failure handling

- `api<T>()` throws on non-2xx; the container catches and renders `Empty variant="error"` (initial load) or a `PageScaffold` feedback message (mutation). Loading → `Empty variant="loading"`. Empty result → `Empty variant="empty"` with a hint.
- `409` (dedup conflict on create/edit, or status conflict on approve) surfaces the server message verbatim in the feedback zone. `404` on a stale row triggers a refetch. `204` (delete) is treated as success.
- **Disabled banner:** if `status.enabled === false`, a non-blocking banner reads "Memory capture is off (`OPEN_OTC_MEMORY`) — existing facts are still editable here." The console stays fully functional.

## Testing

- **Backend** (`tests/test_memory_api.py`, extend):
  - `FactOut` now includes `pinned`, `created_by`, `extractor_model`, `source_session_id`, with `extractor_model`/`source_session_id` read from `meta`.
  - `PATCH /facts/{id}/pin` round-trips `pinned` true→false; `404` on unknown id.
  - `MemoryStore.set_pinned` unit coverage (pins, unpins, raises `MemoryNotFound`).
- **Frontend** (`frontend/src/routes/Memory.live.test.tsx`, new — match the existing route-test pattern with a mocked `api/client`):
  - Renders facts; switching scope tab re-fetches with the right `scope_type`.
  - Approve / pin-toggle / edit / delete call the right client fn and refetch.
  - Disabled banner shows when `status.enabled === false`.
  - Error state renders on a rejected fetch; Refresh re-fetches.
- **Route guard:** check for any nav/route snapshot or "exact route set" test; update it if the new `'memory'` route trips it.

## Out of scope (YAGNI)

- No **automatic** live refresh — no websocket, no polling. (Pull-only via the Refresh button.)
- No bulk actions (multi-select approve/delete).
- No multi-user identity — the `user`/`correction` scope id stays the constant `desk`.
- No changing a fact's `scope_type`/`scope_id` after creation.
- Pagination stays at the API's `limit`/`offset` defaults; a "load more" control is added **only** if a scope routinely exceeds the default page — otherwise deferred.

## Open verification items for the plan

1. **Book `scope_id` format.** Confirm the identifier `book_scope_for_session` stores (the portfolio id from the ContextPack) matches what `listPortfolios()` returns (names). If they differ (name vs numeric id), the Book dropdown maps its value to the stored form before filtering/creating.
2. **Provenance `meta` keys.** Confirm the extractor writes `meta["extractor_model"]` and `meta["session_id"]` (per the memory write path) so `_out()` reads the right keys.
