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

### 3. Frontend

- **Instruments.tsx / Instruments.live.tsx**: add a small tags chip-editor to
  the instrument row/drawer, wired to the new `PUT .../tags` endpoint. This is
  the primary "real tag system" surface — a human can pre-register an
  underlying without going through the agent at all. It sits alongside, not
  in place of, the existing `ROLES` column.
- **Booking.live.tsx `activeUnderlyingSymbols()`** (line 665): change from
  `status === 'active'` to `status === 'active' && (tags ?? []).includes('underlying')`.
- **TrySolve.tsx** underlying field filter (line 930): same change.
- `Instrument`/`Underlying` frontend type (`frontend/src/types.ts`) gets a
  `tags?: string[]` field.

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
- **Accepted simplification**: LangGraph's interrupt fires *before* the tool
  body runs (interrupt is at tool-call granularity, gated on tool name +
  args), so the pre-approval card can only show the symbol being registered —
  it cannot yet say "create new" vs. "tag existing." That distinction only
  appears in the tool's result *after* approval, in the next assistant turn.

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
