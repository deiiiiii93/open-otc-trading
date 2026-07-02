# Underlying tag system — design

## Problem

`Instrument` (backend/app/models.py:522-603) holds every instrument row — real
underlyings (index/etf/stock/sge_spot) *and* derivative contracts (dated
futures, listed options). Today there is no stored way to say "this
instrument is a valid underlying to structure a product against":

- The `Instruments.tsx` admin page shows a `ROLES` badge, but it's **computed
  client-side**, never stored (`Instruments.live.tsx:71-73`) — an instrument
  reads as `"underlying"` only if an open position currently references it
  (`hedgeGroups[...].open_position_count > 0`). A brand-new underlying with no
  trades yet shows no signal at all.
- The Booking (`Booking.live.tsx:665` `activeUnderlyingSymbols()`) and Try to
  Solve (`TrySolve.tsx:930`) underlying pickers both filter only on
  `status === 'active'` — no kind or role distinction — so dated futures
  contracts and listed options can appear in a picker meant for real
  underlyings.
- `ensure_underlying()` (services/underlyings.py:116) silently
  auto-creates a draft `Instrument` for *any* symbol string handed to it, with
  zero validation or confirmation. Agent tools that accept a raw `underlying`
  string (`book_position`, `book_hedge`) inherit this: nothing stops a typo or
  an unvetted symbol from entering the system.

## Goals

1. Give "underlying" a real, storable, pre-assignable representation —
   independent of whether anything has traded against the instrument yet.
2. Filter the Booking and Try to Solve underlying pickers to only the tagged
   set.
3. Gate the agent's booking tools so an unregistered underlying can't be
   silently used: in `interactive`/`auto` mode, surface a HITL approval card
   to create-or-tag it; **only** in `yolo` mode, add it automatically. `auto`
   mode must still pause — it is not a synonym for `yolo` here.

## Non-goals

- Not building a governed tag catalog (enum, tag-definitions table, rename
  tooling). Tags are freeform strings, matching the existing
  `Portfolio.tags` precedent.
- Not changing hedge-instrument discovery. Verified directly:
  `services/domains/hedging.py`'s candidate queries (`list_instruments` at
  line 51, `_spec_roots`, `mark`/`unmark`, `get_map`) key off `Instrument.kind`,
  `series_root`, `parent_id`, and the `HedgeMapEntry` table — none of it reads
  a tags field. Adding the column is purely additive.
- Not touching the existing computed `ROLES` badge on the Instruments page.
  It stays as-is (see "Tags vs. roles" below).

## Design

### 1. Data model & migration

Add a `tags` column to `Instrument`, mirroring `Portfolio.tags`
(models.py:506) exactly:

```python
tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
```

New Alembic migration (next number after `0041`):

- Adds the `tags` column (JSON, default `[]`, not null).
- Backfill: append `"underlying"` to `tags` (if not already present) for any
  `Instrument` row matching **either**:
  - referenced by `open_position_underlying_symbols()`
    (services/underlyings.py:239 — the existing canonical "has an open OTC
    position" query), **or**
  - `status == "active"` **and** it's a root instrument, not a derivative
    contract instance: `kind != "listed_option"` and `expiry IS NULL` and
    `contract_code IS NULL`.

  The second clause is required, not optional: the problem this feature
  exists to solve is precisely the instrument that's already been curated
  (`status="active"`) but has no open position yet — e.g. `000905.SH` in the
  screenshot that motivated this spec. Backfilling only the open-position
  clause would silently drop every pre-registered, not-yet-traded underlying
  from the Booking/Try to Solve pickers the moment this ships — the exact
  failure mode the feature is meant to prevent, just moved to migration day.
  Without either clause, every real underlying currently in production would
  vanish from the pickers, since they now require the explicit tag.

### 2. Backend API

- `PUT /api/instruments/{id}/tags` — body `{"tags": list[str]}`, full
  replace. New `set_instrument_tags()` service function in
  `services/instruments.py`. Mirrors `PUT /api/portfolios/{id}/tags`
  (main.py:1646) exactly.
- `GET /api/instruments?tag=underlying` — extend `list_instruments()`
  (services/instruments.py:58) with an optional `tag: str | None` param.
  Tag filtering happens **in Python** (matching `list_portfolios(tags=...)`,
  services/portfolio_service.py:51-53: `wanted.issubset(set(p.tags or []))`),
  not a SQL JSON-containment query. **Ordering relative to pagination
  matters**: `list_portfolios` never paginates, but `list_instruments`
  already applies `.offset(offset).limit(limit)` in SQL
  (services/instruments.py:81) — filtering by tag *after* that would only
  ever see the current page and silently drop tagged rows sorted beyond it.
  So when `tag` is given, apply every other filter (`kind`, `status`,
  `parent_id`, `series_root`, `search`) in SQL as today but **skip the SQL
  `.offset().limit()`**, filter the full matching result set by tag in
  Python, and apply `offset`/`limit` to *that* filtered list before
  returning.

  **Declined**: replacing the JSON `tags` column with a join table (or a
  SQLite `json_each` containment query) purely for `tag=` scan performance.
  In practice every caller of `tag=` filters to a small eligible set (a
  desk's active underlyings, at most low hundreds of rows, not the full
  instrument-plus-every-dated-contract catalog), and Booking.live.tsx already
  does an equivalent full unfiltered fetch (`limit=1000`, no `tag`/`status`
  params) in production today with no observed scale problem. Revisit only if
  the instrument catalog or this endpoint's usage pattern actually grows
  large enough to matter.

  **Accepted trade-off — full-replace concurrency**: like
  `PUT /api/portfolios/{id}/tags`, this is a full-list replace, not an
  additive/removal op. A stale client snapshot (e.g. two open Instruments-page
  tabs, or the agent's `register_underlying` tool adding the tag between the
  drawer loading and a human clicking save) could in principle overwrite a
  concurrently-added tag. This is higher-consequence here than for Portfolio
  tags, since losing `"underlying"` breaks Booking/TrySolve eligibility and
  `book_position`/`book_hedge`, not just cosmetic grouping — but this codebase
  has no ETag/optimistic-concurrency infrastructure anywhere else, and adding
  one exclusively for this endpoint would be new machinery this feature
  doesn't otherwise need. Mitigation instead of new infra: the Instruments
  page tag editor (Section 3) always loads the row's current `tags` as its
  editing baseline immediately before save, bounding the race to the same
  narrow window `Portfolio.tags` already accepts in production today, not a
  new or wider one. Separately, `register_underlying` (Section 4) does **not**
  go through this PUT endpoint at all — it's a direct read-modify-write on the
  `Instrument` row inside its own DB transaction, so it's atomic with respect
  to itself regardless of what the PUT endpoint does. The only remaining race
  is human-PUT-vs-human-PUT or human-PUT-vs-agent-write, both covered by the
  mitigation above.

  **Declined**: adding an ETag/`updated_at`-precondition mechanism or
  converting this endpoint to atomic add/remove ops. Raised twice by the
  Tier-1 reviewer without new information the second time — this app has a
  single-desk operator model with no existing optimistic-concurrency
  infrastructure anywhere (`Portfolio.tags` accepts the identical risk), and
  building bespoke versioning for one endpoint would be new machinery the
  rest of the codebase doesn't have or need. Revisit if real multi-user
  concurrent editing is ever added.

### 3. Frontend

- **Instruments.tsx / Instruments.live.tsx**: add a small tags chip-editor to
  the instrument row/drawer, wired to the new `PUT .../tags` endpoint. This is
  the primary "real tag system" surface — a human can pre-register an
  underlying without going through the agent at all. It sits alongside, not
  in place of, the existing `ROLES` column.
- **Fetch server-filtered, not client-filtered**: Booking.live.tsx currently
  fetches `/api/instruments` with **no query params at all**
  (Booking.live.tsx:160), relying on the backend's default `limit=1000` and
  filtering client-side in `activeUnderlyingSymbols()` (line 665). That
  pattern reproduces the exact pagination-loss bug from Section 2 the moment
  the instrument catalog exceeds 1000 rows: valid tagged underlyings sorted
  past the unfiltered page would silently vanish from the picker. Both
  Booking.live.tsx and TrySolve.tsx's data-loading effect must instead call
  `/api/instruments?status=active&tag=underlying` (the new, correctly-ordered
  server-side filter from Section 2) and use the response directly — no
  client-side re-filtering by tag needed, since the server already returns
  exactly the eligible set.
- `activeUnderlyingSymbols()` (Booking.live.tsx:665) and TrySolve.tsx's
  underlying field (line 930) simplify accordingly: with the fetch already
  scoped to `status=active&tag=underlying`, they just map/list the returned
  rows directly rather than re-applying the `status`/`tags` predicate.
- `Instrument`/`Underlying` frontend type (`frontend/src/types.ts`) gets a
  `tags?: string[]` field (still needed for the Instruments admin page tag
  editor and display, even though the picker no longer filters on it
  client-side).

#### Tags vs. roles

These stay two distinct, independently-visible concepts on the Instruments
page:

- **`ROLES`** (existing, computed): "is *currently* being used this way" —
  derived live from open positions / hedge-map entries. Unchanged by this
  work.
- **`TAGS`** (new, stored): "is *registered/eligible* to be used this way" —
  set explicitly, ahead of any trade. An instrument can be tagged
  `"underlying"` with an empty `ROLES` badge (registered but not yet traded)
  — that is expected, not a bug.

### 4. New agent tool: `register_underlying`

New file `backend/app/tools/underlyings.py`.

- **Input**: `symbol: str`.
- **Behavior**:
  - Instrument doesn't exist → create via the existing
    `ensure_underlying(session, symbol, source="agent", status="active", activate=True)`
    inference (kind/currency/market/exchange all auto-derived from the symbol
    string, exactly as today), then add the `"underlying"` tag.
  - Instrument exists but lacks the tag → add the tag (and activate if
    currently `draft`).
  - Instrument exists and already tagged → no-op, returns ok.
- **Gating**: `@capability_gated(group=ToolGroup.DOMAIN_WRITE)`, added to
  `INTERRUPT_TOOL_NAMES` (services/deep_agent/hitl.py:23) with
  `risk_level="irreversible"` (`_RISK_LEVEL_BY_TOOL`) — **not** `"write"` —
  and a label in `_LABEL_BY_TOOL` (e.g. `"Register/tag underlying"`). This
  distinction is load-bearing: per `interrupt_on_config()` (hitl.py:148-173),
  `"write"`-risk tools bypass confirmation under **both** `auto` and `yolo`
  mode (`resolve_execution_mode()` sets `clear_hitl=True` for `auto` too),
  which would let the model silently persist an unvetted underlying under
  `auto` mode — contradicting the explicit requirement that only `yolo`
  auto-adds. `"irreversible"`-risk tools stay gated under `auto` and only
  bypass under `yolo`/headless, which is exactly the interactive-and-auto-warn,
  yolo-only-auto-add behavior this feature requires. (The name
  `"irreversible"` describes the risk *category* LangGraph gates on here, not
  a literal claim that tags can't be removed — they can, via the tags PUT
  endpoint.)
- **Approval card preflight**: LangGraph's interrupt fires *before* the tool
  body runs (interrupt is at tool-call granularity, gated on tool name +
  args), so the default per-tool summary would only be able to show the raw
  `symbol` arg — not enough for a human to meaningfully approve "create a new
  instrument" vs. "tag an existing one." Add a dedicated summary builder
  (`_summarize_register_underlying`, alongside the existing
  `_summarize_book_position` at hitl.py:204) registered in
  `_SUMMARY_BUILDERS` (hitl.py:231). Unlike the existing builders, this one
  needs a **read-only** DB lookup on the symbol to classify the action before
  rendering the card, so `_summary_for`/`pending_actions_from_interrupts`
  (hitl.py:236, 251) gain an optional `session` parameter threaded from their
  call site in `agents.py`. The resulting card summary distinguishes the
  three cases and previews the inferred metadata for the create case, e.g.:
  - *"Register NEW underlying 000905.SH — inferred kind=index, currency=CNY,
    market=CN"* (does not exist yet)
  - *"Add 'underlying' tag to existing instrument 000905.SH (kind=index,
    status=draft)"* (exists, untagged)
  - *(already valid — in practice `book_position`/`book_hedge` would not have
    signaled `underlying_not_registered` for this symbol, so the model has no
    reason to call the tool; this case is a no-op safety net, not a card a
    human should normally see)*

  **Explicitly out of scope**: rejecting unresolvable/unrecognized symbols by
  default. The whole point of this feature is that a legitimate new
  underlying *can* be created through this path — refusing unknown symbols
  would contradict the goal, not harden it. The human (interactive/auto) or
  the desk's own YOLO-mode trust boundary (yolo) is the actual approval gate;
  the preview above gives that approval something concrete to evaluate, but
  doesn't second-guess it with a denylist.

### 5. `book_position` / `book_hedge` validation

Both tools (`tools/positions.py:599` `book_position_tool`,
`tools/hedging.py:90` `book_hedge_tool`) validate the `underlying` symbol
*before* calling their domain service. New helper
`is_registered_underlying(session, symbol) -> bool` (services/underlyings.py
or services/instruments.py) checks the instrument exists and
`"underlying" in (instrument.tags or [])`.

If invalid: return `{"ok": False, "error": "underlying_not_registered",
"detail": {"symbol": ...}}` and persist nothing (no partial booking). Each
tool's docstring is updated to instruct the model: *"If this returns
error=underlying_not_registered, call register_underlying(symbol) then
retry."* — the same self-correcting retry shape `build_assumption_set_tool`
already documents for `unfilled_underlyings` (tools/assumptions.py:130-132).

`book_hedge`'s `underlying` is sourced from `get_hedgeable_underlyings`
(i.e. an existing open position's exposure), so in practice it will almost
always already be tagged via the backfill — this check is a safety net, not
the primary path.

## Testing

- Backend: migration backfill (open-position underlyings *and*
  active/no-position root instruments both get tagged), `PUT .../tags`
  endpoint, `GET ?tag=` filter (including a regression where a tagged
  underlying sorts beyond the unfiltered page size, to catch the
  filter-before-pagination ordering), `register_underlying` tool (create-new
  / tag-existing / no-op cases), `book_position`/`book_hedge` rejecting an
  unregistered underlying, HITL card rendering for `register_underlying`
  under **both** `interactive` and `auto` mode, auto-execute (no card) only
  under `yolo`.
- Frontend: Booking and TrySolve pickers only list tagged+active instruments;
  Instruments page tag editor round-trips through the new endpoint.
