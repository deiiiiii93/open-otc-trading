# Hedge tag system — design

## Problem

The underlying-tag system (`docs/superpowers/specs/2026-07-02-underlying-tag-system-design.md`,
merged `de2d2aa`) added a real, stored `Instrument.tags` column and deliberately
left the Instruments page's `ROLES` badges (`Instruments.tsx:285-293`
`RolesBadges`) as a second, independently-computed concept: `underlying`
(derived from open positions/backfill, now stored) and `hedge` (derived live
from `HedgeMapEntry`, never stored).

That was the right call at the time, but the two-concepts split is now
explicitly unwanted: the tag system should be the **only** displayed
classification on the Instruments page. Today:

- `rolesByInstrumentId` (`Instruments.live.tsx:74-87`) is computed from
  `hedgeGroups` (`GET /api/hedging/map`): an instrument reads `hedge: true`
  only if it appears as an `entries[].instrument_id` in some hedge-map group.
- `Instrument.tags` has no `"hedge"` value at all — `list_instruments(tag=...)`
  (`services/instruments.py:59-92`) and `GET /api/instruments?tag=` already
  support arbitrary tags generically, but nothing ever writes `"hedge"` into
  the column.
- Hedge eligibility itself is **not** a per-instrument boolean — it's a
  per-underlying relationship stored in `HedgeMapEntry`
  (`models.py:1828-1861`): one instrument can be marked as an allowed hedge
  for underlying A and not underlying B. `_active_instruments`
  (`services/hedging_legs.py:67-108`) and `mark`/`unmark`/`get_map`
  (`services/domains/hedging.py:206-401`) are the real source of truth and
  must stay exactly as they are.
- One extra business rule already lives in two places: a `kind == "stock"`
  instrument is **always** its own allowed hedge by default, with no
  `HedgeMapEntry` row required — baked into both `get_map`'s synthetic
  `entries` injection (`services/domains/hedging.py:384-399`) and
  `_active_instruments`'s early return (`hedging_legs.py:85-86`).

## Goals

1. Replace the computed `ROLES` badges on the Instruments page with the
   `TAGS` column as the single displayed classification concept — for both
   `underlying` and `hedge`.
2. Make `"hedge"` a real, stored, queryable tag (`GET /api/instruments?tag=hedge`
   works like `tag=underlying` already does).
3. Never let the `"hedge"` tag drift from ground truth: it is a **derived**
   fact (server-computed from `HedgeMapEntry` + the stock self-hedge rule),
   not a freeform classification a human assigns — unlike `"underlying"`,
   which stays hand-editable.
4. Zero behavior change to hedge eligibility / the MILP hedging engine. This
   is a display-and-query consistency refactor, not a re-architecture of how
   hedges are chosen.

## Non-goals

- Not changing `HedgeMapEntry`, `mark`/`unmark`/`purge_stale`/`get_map`, or
  `_active_instruments`'s eligibility query in any way that affects behavior.
  The tag is a read model derived from these, never the other way around.
- Not making `"hedge"` manually editable. There is no product need for a
  human to hand-tag an instrument as a hedge independent of the Allowed
  Hedges tab — that tab (mark/unmark) remains the one true way to change
  hedge eligibility.
- Not adding an agent-tool HITL/YOLO gate for hedge eligibility (mirroring
  `register_underlying`). Explicitly declined: hedge eligibility already has
  its own human-driven gate (the Allowed Hedges tab's mark/unmark), and nothing
  in the booking/hedging agent tools currently checks a hedge-related tag to
  gate on.
- Not collapsing the per-underlying granularity of `HedgeMapEntry` into the
  tag. The tag answers "is this instrument an allowed hedge for **any**
  underlying" — a coarser, summary fact, useful for display/filtering only.

## Design

### 1. `sync_hedge_tag` — the single source of derived truth

New function in `services/instruments.py`, alongside `set_instrument_tags`:

```python
def sync_hedge_tag(session: Session, instrument_id: int) -> None:
    """Recompute the derived "hedge" tag for one instrument from ground
    truth (HedgeMapEntry membership + the stock self-hedge default) and
    write it if it changed. Never touches any other tag."""
    from sqlalchemy import and_, or_
    from ..models import HedgeMapEntry  # local import: avoid cycle at module load

    row = session.get(Instrument, instrument_id)
    if row is None:
        return
    match_conditions = [HedgeMapEntry.instrument_id == instrument_id]
    if row.exchange and row.contract_code:
        # Legacy entries never backfilled with a durable instrument_id are
        # still real ground truth — reconcile_map/_active_instruments both
        # fall back to (exchange, contract_code) for exactly these rows
        # (hedging_loader.py:246-249, hedging_legs.py:93-98). Guarded on both
        # columns being non-null so two different NULL/NULL rows never
        # falsely match each other.
        match_conditions.append(
            and_(
                HedgeMapEntry.instrument_id.is_(None),
                HedgeMapEntry.exchange == row.exchange,
                HedgeMapEntry.contract_code == row.contract_code,
            )
        )
    has_active_entry = (
        session.query(HedgeMapEntry.id)
        .filter(or_(*match_conditions), HedgeMapEntry.reconcile_status == "active")
        .first()
        is not None
    )
    is_self_hedging_stock = row.kind == "stock" and row.status == "active"
    should_have_tag = has_active_entry or is_self_hedging_stock

    current = list(row.tags or [])
    has_tag = "hedge" in current
    if should_have_tag and not has_tag:
        row.tags = _normalize_tags([*current, "hedge"])
    elif not should_have_tag and has_tag:
        row.tags = _normalize_tags([t for t in current if t != "hedge"])
```

Truth condition mirrors the two existing eligibility checks exactly
(`_active_instruments` line 85-108, `get_map` line 384-399) — an instrument
gets `"hedge"` iff it has **at least one** `reconcile_status == "active"`
`HedgeMapEntry` row pointing at it **either directly by `instrument_id` or,
for legacy never-backfilled rows, by matching `(exchange, contract_code)`**,
or it is an active stock (which is always its own hedge, per the existing
rule). Omitting the legacy fallback would let an instrument stay truly
hedge-eligible (per the MILP engine and `get_map`) while never receiving the
tag — a direct violation of the "never drifts from ground truth" goal.

### 2. Call sites — every place ground truth can change

| Site | File:line | Why it needs the hook |
|---|---|---|
| `mark()` | `services/domains/hedging.py:206-251` | Creates a new `HedgeMapEntry`, or backfills `instrument_id` onto an existing anonymous entry — either can newly satisfy `has_active_entry` for the instrument. Call `sync_hedge_tag(session, inst.id)` once per instrument processed in the loop (skip only the `underlying_id is None: continue` case, where no entry exists at all). |
| `unmark()` | `services/domains/hedging.py:254-291` | Deletes entries; the instrument they pointed at may now have zero active entries left. Before the three `.delete(synchronize_session=False)` calls, collect the affected `instrument_id`s: query `HedgeMapEntry.instrument_id` for the `map_entry_ids` branch (excluding NULL), plus the `instrument_ids` argument itself directly (the third, legacy `(exchange, contract_code)` branch only ever deletes rows with `instrument_id IS NULL`, so it contributes nothing). After all three deletes, call `sync_hedge_tag` for each collected id. |
| `purge_stale()` | `services/domains/hedging.py:404-412` | **No hook needed.** It only ever deletes `reconcile_status == "stale"` rows, which by definition never contributed to `has_active_entry` — removing them cannot change any instrument's tag. |
| `reconcile_map()` | `services/hedging_loader.py:228-250` | Flips `reconcile_status` on every entry in bulk (active↔stale) as the catalog is reloaded — this is the main way an entry's contribution to `has_active_entry` changes without going through `mark`/`unmark` at all. Collect every `entry.instrument_id` seen in the existing loop into a `set[int]` (skip `None`); after the loop, call `sync_hedge_tag` for each. |
| `register_underlying_tool` | `tools/underlyings.py:23-73` | Reactivates an existing non-active instrument (`instrument.status = "active"`, line 41) — the only write path that can flip a **stock's** `is_self_hedging_stock` condition from false to true outside the hedge-map flow. Call `sync_hedge_tag(session, instrument.id)` right after, before `session.flush()`. |
| `delete_underlying_default` | `services/underlying_defaults.py:42-52` | Soft-deletes by setting `status = "inactive"` (line 51) — the corresponding path that can flip a stock's self-hedge condition from true to false. Call `sync_hedge_tag(session, row.id)` right after, before `session.flush()`. |
| `create_instrument_endpoint` | `main.py:1971-2025` | The generic manual-create API takes arbitrary `kind`/`status` from the request body (`InstrumentCreate`, line 1992-2012) — a desk user can create an already-active stock directly, satisfying the self-hedge rule from row one with no `hedge` tag written. Call `sync_hedge_tag(session, row.id)` after `session.flush()` (line 2014), before `record_audit`/`commit`. |
| `patch_instrument_endpoint` | `main.py:2027-2071` | The generic manual-edit API applies any subset of fields via `setattr` (line 2058-2059), including `kind` and `status` — editing a non-stock/inactive row into an active stock, or an active stock into inactive/non-stock, is a normal admin edit through this endpoint. Call `sync_hedge_tag(session, row.id)` unconditionally after `session.flush()` (line 2060), before `record_audit`/`commit` — cheaper and simpler than checking whether `kind`/`status` were among the patched fields, and `sync_hedge_tag` is a no-op write when the tag already matches truth. |

`update_underlying` (`services/underlyings.py:189-193`) is a generic
`setattr` over caller-supplied fields; no current caller passes `status`
through it, so it needs no hook today — noted here so a future caller that
starts passing `status` knows to add one.

All eight hook calls run inside the same session/transaction as the write
they follow — no new commit boundaries, no new failure modes. `sync_hedge_tag`
is a plain read-modify-write on the `Instrument` row already loaded (or
cheaply re-fetched) in that transaction.

**Accepted trade-off — read-modify-write races on `Instrument.tags`**:
`sync_hedge_tag` reads the row's current `tags`, mutates the in-memory list,
and writes the full array back — so a concurrent edit to a *different* tag on
the same instrument (e.g. a human editing `"underlying"` via the tags PUT
endpoint) that commits in the narrow window between this read and this write
could be silently lost. This is not a new risk class introduced by this
feature: `register_underlying_tool` (`tools/underlyings.py:43-46`, merged in
the underlying-tag feature) already does the identical
read-list-append-write-back on this exact column today, and the underlying-tag
spec's "Accepted trade-off — full-replace concurrency" section already
examined and declined building ETag/optimistic-concurrency infrastructure for
`Instrument.tags`, for the same reason repeated here: this codebase has no
such infrastructure anywhere, and one endpoint/helper is not the place to
introduce it. What's genuinely new in *this* feature is call-site count —
`sync_hedge_tag` fires from eight places instead of one, several of them
automatic (`reconcile_map` on every catalog reload) rather than only on rare
human clicks, which widens how often the race window opens (not how wide any
single window is). Mitigation already designed into `sync_hedge_tag` and
`set_instrument_tags` (Section 3): both always `session.get()` a fresh row
and flush immediately, rather than reusing a possibly-stale in-memory
reference passed in from elsewhere — this keeps each individual window as
narrow as the existing accepted pattern, it just doesn't make the count of
windows go away. **Declined**: row versioning / `SELECT ... FOR UPDATE`
locking around `Instrument.tags` — new machinery this single-desk-operator
codebase doesn't have or need anywhere else. Revisit if real concurrent
multi-user editing of the same instrument's tags becomes an actual observed
problem, not a theoretical one.

### 3. `"hedge"` is never client-writable

`set_instrument_tags` (`services/instruments.py:113-119`, backing
`PUT /api/instruments/{id}/tags`) is a full-replace call driven by the
Instruments-page tag editor. It must not let a human accidentally add or
remove `"hedge"` by hand and have it stick — the next `mark`/`unmark`/reload
would silently fight it, and in between, the UI would show a lie.

Fix: strip any client-supplied `"hedge"` before saving, then re-derive it
from ground truth in the same call:

```python
def set_instrument_tags(session: Session, instrument_id: int, tags: list[str]) -> Instrument:
    row = session.get(Instrument, instrument_id)
    if row is None:
        raise LookupError(f"Instrument {instrument_id} not found")
    row.tags = _normalize_tags([t for t in tags if t != "hedge"])
    session.flush()
    sync_hedge_tag(session, instrument_id)
    session.flush()
    return row
```

This makes `"hedge"` structurally impossible to desync via the API, not just
by convention — the same guarantee `register_underlying` already gives
`"underlying"` by never routing through this endpoint at all.

### 4. Migration `0044_hedge_tag.py`

Chains on `0043_agent_action_audits` (already the head; `0043` was claimed by
the concurrently-merged audit module). Migration-local Core SQL only, per
house rule — no app model/service imports.

**This must be a true recomputation, not an append-only backfill.** The
existing `PUT /api/instruments/{id}/tags` endpoint accepted arbitrary tag
strings before this feature shipped `set_instrument_tags`'s `"hedge"`-stripping
(Section 3) — so a desk user could already have hand-typed `"hedge"` onto an
instrument that has zero active `HedgeMapEntry` rows and isn't an active
stock. An append-only migration would leave that false tag in place
indefinitely (nothing would ever touch it again), directly violating the
"never drifts from ground truth" invariant this feature exists to establish.
So the migration recomputes every instrument's `hedge` membership from
scratch: strip `"hedge"` from every row's tags first, then re-add it only to
the derived-true set.

Recompute `"hedge"` membership for every instrument as: referenced by a
`hedge_map_entries` row with `reconcile_status = 'active'` — either directly
via `instrument_id`, or (for legacy rows never backfilled with a durable
link) by matching `(exchange, contract_code)` against an instruments row —
**or** `kind = 'stock' AND status = 'active'` (the self-hedge default). The
legacy-fallback join mirrors `sync_hedge_tag`'s Python-side logic above; skip
it here would silently under-tag exactly the same rows.

```python
def upgrade() -> None:
    bind = op.get_bind()
    hedge_ids: set[int] = set()

    for (instrument_id,) in bind.execute(
        text(
            "SELECT DISTINCT instrument_id FROM hedge_map_entries "
            "WHERE reconcile_status = 'active' AND instrument_id IS NOT NULL"
        )
    ).fetchall():
        hedge_ids.add(instrument_id)

    for (instrument_id,) in bind.execute(
        text(
            "SELECT DISTINCT i.id FROM instruments i "
            "JOIN hedge_map_entries h ON h.instrument_id IS NULL "
            "AND h.exchange = i.exchange AND h.contract_code = i.contract_code "
            "WHERE h.reconcile_status = 'active' "
            "AND i.exchange IS NOT NULL AND i.contract_code IS NOT NULL"
        )
    ).fetchall():
        hedge_ids.add(instrument_id)

    for (instrument_id,) in bind.execute(
        text("SELECT id FROM instruments WHERE kind = 'stock' AND status = 'active'")
    ).fetchall():
        hedge_ids.add(instrument_id)

    # Full recompute, not append-only: strip any pre-existing "hedge" tag from
    # every row first (it may have been hand-typed through the tags PUT
    # endpoint before this feature made "hedge" server-derived), then add it
    # back only where ground truth says so.
    for (instrument_id, tags_raw) in bind.execute(
        text("SELECT id, tags FROM instruments WHERE tags LIKE '%\"hedge\"%'")
    ).fetchall():
        current = json.loads(tags_raw) if tags_raw else []
        if "hedge" in current and instrument_id not in hedge_ids:
            current = [t for t in current if t != "hedge"]
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )

    for instrument_id in sorted(hedge_ids):
        row = bind.execute(
            text("SELECT tags FROM instruments WHERE id = :id"),
            {"id": instrument_id},
        ).fetchone()
        if row is None:
            continue
        current = json.loads(row[0]) if row[0] else []
        if "hedge" not in current:
            current.append("hedge")
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )


def downgrade() -> None:
    bind = op.get_bind()
    for (instrument_id, tags_raw) in bind.execute(
        text("SELECT id, tags FROM instruments WHERE tags LIKE '%\"hedge\"%'")
    ).fetchall():
        current = json.loads(tags_raw) if tags_raw else []
        if "hedge" in current:
            current = [t for t in current if t != "hedge"]
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )
```

No new column — `tags` already exists from `0042`. Duplicate the same
full-recompute logic (strip-then-add, not append-only) into `database.py`'s
`_ensure_incremental_schema` dev-DB repair path, next to the existing
`_backfill_instrument_underlying_tags` call, as a new
`_backfill_instrument_hedge_tags(active_engine, tables, inspector)` function.

### 5. Frontend: drop `ROLES`, extend `TAGS`

**Remove** (`Instruments.tsx`):
- `RolesBadges` component (lines 285-293).
- `InstrumentRoles` type (line 70) and the `rolesByInstrumentId` prop
  threading (`Props` line 105, destructured at 307-308 and 625, rendered at
  452, passed at 1078).
- The `ROLES` column header (line 358) and its cell (lines 450-452).

**Remove** (`Instruments.live.tsx`):
- The `rolesByInstrumentId` `useMemo` (lines 74-87) and its prop pass-through
  (line 643).
- `hedgeGroups` state/fetch (line 41) **stays** — the Allowed Hedges tab
  (`InstrumentsAllowedHedges.tsx`) still needs it; only the roles derivation
  goes away.

**Extend the existing TAGS cell** (`Instruments.tsx:457-458`, the `TagEditor`
call): `"hedge"` must render but not be removable/addable by hand, per
Section 3. `TagEditor` itself (`components/TagEditor.tsx`) stays a generic,
dumb freeform editor with no new prop — the read-only-tag split is specific
to this one page's use case, not a concern the shared component should carry.
Instead, `Instruments.tsx` does the split at the call site:

```tsx
{row.tags.includes('hedge') && (
  <span className="wl-tageditor__chip wl-tageditor__chip--readonly" title="Auto-managed from Allowed Hedges">
    hedge
  </span>
)}
<TagEditor
  tags={row.tags.filter((t) => t !== 'hedge')}
  onChange={(next) => onSetInstrumentTags(row.id, row.tags.includes('hedge') ? [...next, 'hedge'] : next)}
/>
```

`wl-tageditor__chip--readonly` is a new modifier class in
`components/TagEditor.css` (or a co-located style in `Instruments.css`) that
renders the existing chip look without the `x` remove button — a pure CSS
addition, token-only per `frontend/CLAUDE.md`, no new component.

## Testing

- Backend: `sync_hedge_tag` unit tests covering all four truth-condition
  transitions (gains tag via new active entry / loses tag when last active
  entry removed / gains via stock reactivation / loses via stock
  soft-delete), each hook site (`mark`, `unmark`, `reconcile_map`,
  `register_underlying_tool`, `delete_underlying_default`,
  `create_instrument_endpoint`, `patch_instrument_endpoint`) actually calling
  it and producing the right end state — including creating an already-active
  stock via `POST /api/instruments`, and patching a row `inactive→active`
  stock, `active→inactive` stock, and `stock→non-stock` via
  `PATCH /api/instruments/{id}` — `purge_stale` provably **not** changing any
  tag, `set_instrument_tags` stripping and re-deriving a client-supplied
  `"hedge"` value (both add and remove attempts), a legacy `HedgeMapEntry`
  row with `instrument_id IS NULL` matching an active instrument only via
  `(exchange, contract_code)` still producing the tag (both in
  `sync_hedge_tag` and in the migration), migration `0044`'s full-recompute
  backfill (map-entry-derived + stock-self-hedge-derived, and their union for
  an instrument matching both, **plus** a case where a row has a pre-existing
  hand-typed `"hedge"` tag that does *not* satisfy either truth condition and
  must be scrubbed, not left in place), `GET /api/instruments?tag=hedge`.
- Frontend: `ROLES` column/badges gone from the rendered table; `TAGS` cell
  shows a non-removable `hedge` chip when present and a normal editable
  `TagEditor` for the rest; saving edited tags never sends `"hedge"` in the
  request body change-set in a way that could flip it (covered by the
  backend-side stripping, but a frontend test should assert the chip has no
  remove control to catch a future accidental prop regression).
