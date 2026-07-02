# Hedge Tag System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `"hedge"` a real, server-derived, stored tag on `Instrument.tags` (mirroring the existing `"underlying"` tag) so the Instruments admin page can drop its computed `ROLES` badges entirely and show `TAGS` as the single classification concept.

**Architecture:** A single pure-derivation function, `sync_hedge_tag(session, instrument_id)`, recomputes whether an instrument should carry `"hedge"` from ground truth — an active `HedgeMapEntry` referencing it (directly or via the legacy `(exchange, contract_code)` fallback), or the pre-existing "a stock is its own hedge" rule — and writes the tag if it changed. This function is wired into every write path that can change that ground truth (7 call sites across 4 files), made impossible for a human to override via the tags API, backfilled by a migration that fully recomputes (not appends) the tag across the whole table, and the frontend drops its parallel computed-badge concept in favor of reading the stored tag.

**Tech Stack:** FastAPI + SQLAlchemy (backend), Alembic (migration), React 19 / TypeScript (frontend), pytest / vitest.

## Global Constraints

- Migration-local Core SQL only in Alembic files — never import app models/services into a migration (`backend/CLAUDE.md` house rule, restated in the spec's Section 4).
- Token-only CSS — no hardcoded colors/spacing (`frontend/CLAUDE.md`).
- `"hedge"` must never be settable by a client through `PUT /api/instruments/{id}/tags` — it is always server-derived.
- Zero behavior change to `HedgeMapEntry`, `mark`/`unmark`/`purge_stale`/`get_map`, or `_active_instruments` — this feature only *reads* those to derive a tag, never changes their logic.
- The read-modify-write race on `Instrument.tags` across the new call sites is an **explicitly accepted trade-off** (spec Section 2, "Accepted trade-off"). Do not introduce row locking, versioning, or ETag machinery to "fix" it — that would contradict the reviewed and accepted design.

---

### Task 1: `sync_hedge_tag` — the core derivation function

**Files:**
- Modify: `backend/app/services/instruments.py`
- Test: `tests/test_instruments_service.py`

**Interfaces:**
- Produces: `sync_hedge_tag(session: Session, instrument_id: int) -> None` — every later task calls this exact signature after a write that can change hedge-tag truth. No return value; it's a side-effecting write against the passed session (caller commits/flushes as it already does).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_instruments_service.py` (this file already has a `_mk(session, **kw)` helper at line 18 that creates an `Instrument` with `status="active", kind="index"` defaults — reuse it):

```python
def test_sync_hedge_tag_adds_tag_for_active_map_entry():
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX", contract_code="IC2406")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" in inst.tags


def test_sync_hedge_tag_removes_tag_when_no_active_entry_remains():
    from app import database
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", tags=["hedge", "underlying"])
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert inst.tags == ["underlying"]


def test_sync_hedge_tag_matches_legacy_entry_with_null_instrument_id():
    """A HedgeMapEntry never backfilled with a durable instrument_id link is
    still real ground truth — reconcile_map/_active_instruments both fall
    back to matching (exchange, contract_code). sync_hedge_tag must too."""
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX", contract_code="IC2406")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=None,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" in inst.tags


def test_sync_hedge_tag_ignores_stale_entry():
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2403.CFFEX", kind="futures", exchange="CFFEX", contract_code="IC2403")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2403", reconcile_status="stale",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" not in inst.tags


def test_sync_hedge_tag_requires_instrument_itself_active_even_with_active_map_entry():
    """reconcile_status is only refreshed by mark/unmark/reconcile_map — a
    direct Instrument.status edit (e.g. via PATCH) doesn't touch it, so it
    can be stale "active" data. The real MILP eligibility query
    (_active_instruments) always filters Instrument.status == "active" too;
    sync_hedge_tag must not grant "hedge" off a stale-active map entry when
    the instrument itself is no longer active."""
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX",
                   contract_code="IC2406", status="expired")
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert "hedge" not in inst.tags


def test_sync_hedge_tag_active_stock_is_self_hedging():
    from app import database
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        stock = _mk(session, symbol="600519.SH", kind="stock", status="active")
        session.commit()

        sync_hedge_tag(session, stock.id)
        session.commit()

        session.refresh(stock)
        assert "hedge" in stock.tags


def test_sync_hedge_tag_inactive_stock_is_not_self_hedging():
    from app import database
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        stock = _mk(session, symbol="600519.SH", kind="stock", status="draft", tags=["hedge"])
        session.commit()

        sync_hedge_tag(session, stock.id)
        session.commit()

        session.refresh(stock)
        assert "hedge" not in stock.tags


def test_sync_hedge_tag_preserves_other_tags():
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import sync_hedge_tag

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX",
                   contract_code="IC2406", tags=["underlying", "custom"])
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        sync_hedge_tag(session, inst.id)
        session.commit()

        session.refresh(inst)
        assert set(inst.tags) == {"underlying", "custom", "hedge"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_instruments_service.py -k sync_hedge_tag -v`
Expected: FAIL with `ImportError: cannot import name 'sync_hedge_tag'`

- [ ] **Step 3: Implement `sync_hedge_tag`**

In `backend/app/services/instruments.py`, change the import block at the top from:

```python
from sqlalchemy.orm import Session

from ..models import Instrument
```

to:

```python
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..models import HedgeMapEntry, Instrument
```

(No circular-import risk: `models.py` only imports `.database` — `HedgeMapEntry` and `Instrument` are both plain model classes in the same module, unlike `hedging_legs.py`'s genuine cross-package cycle that forces a local import there.)

Add `"sync_hedge_tag"` to the `__all__` list (after `"set_instrument_tags"`), and add the function right after `set_instrument_tags` (end of file):

```python
def sync_hedge_tag(session: Session, instrument_id: int) -> None:
    """Recompute the derived "hedge" tag for one instrument from ground
    truth (HedgeMapEntry active membership + the stock self-hedge default)
    and write it if it changed. Never touches any other tag.

    Truth mirrors the two existing eligibility checks exactly:
    hedging_legs.py::_active_instruments and
    services/domains/hedging.py::get_map's synthetic stock entry.
    """
    row = session.get(Instrument, instrument_id)
    if row is None:
        return
    match_conditions = [HedgeMapEntry.instrument_id == instrument_id]
    if row.exchange and row.contract_code:
        # Legacy entries never backfilled with a durable instrument_id are
        # still real ground truth — reconcile_map/_active_instruments both
        # fall back to (exchange, contract_code) for exactly these rows.
        # Guarded on both columns being non-null so two different NULL/NULL
        # rows never falsely match each other.
        match_conditions.append(
            and_(
                HedgeMapEntry.instrument_id.is_(None),
                HedgeMapEntry.exchange == row.exchange,
                HedgeMapEntry.contract_code == row.contract_code,
            )
        )
    # reconcile_status is only refreshed by mark()/unmark()/reconcile_map() —
    # a direct status edit on the Instrument itself (e.g. via PATCH) doesn't
    # touch it, so it can be stale "active" data at the moment this runs.
    # _active_instruments (the real MILP eligibility query) always filters
    # Instrument.status == "active" in addition to the map entry, so this
    # must too, or an instrument PATCHed to expired/inactive would keep
    # advertising "hedge" until some later reconcile_map() call happens to
    # catch up.
    has_active_entry = row.status == "active" and (
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_instruments_service.py -k sync_hedge_tag -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/instruments.py tests/test_instruments_service.py
git commit -m "feat(instruments): add sync_hedge_tag ground-truth derivation"
```

---

### Task 2: Wire into `mark()` / `unmark()`

**Files:**
- Modify: `backend/app/services/domains/hedging.py:206-291`
- Test: `tests/test_hedging_domain.py`

**Interfaces:**
- Consumes: `sync_hedge_tag(session, instrument_id)` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hedging_domain.py` (reuses the file's existing `_underlying`/`_instrument` helpers, shown above):

```python
def test_mark_adds_hedge_tag(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="desk_user")
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags


def test_mark_expired_instrument_does_not_add_hedge_tag(session):
    u = _underlying(session)
    inst = _instrument(session, u, code="IC2403", status="expired")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_unmark_by_instrument_id_removes_hedge_tag(session):
    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    assert "hedge" in inst.tags

    hedging_domain.unmark(session, instrument_ids=[inst.id])
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_unmark_by_map_entry_id_removes_hedge_tag(session):
    from app.models import HedgeMapEntry

    u = _underlying(session)
    inst = _instrument(session, u)
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    entry_id = session.query(HedgeMapEntry).one().id

    hedging_domain.unmark(session, map_entry_ids=[entry_id])
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_unmark_keeps_hedge_tag_when_another_active_entry_remains(session):
    """An instrument marked as an allowed hedge for two different underlyings
    keeps the tag after being unmarked from only one of them."""
    u1 = _underlying(session, symbol="000905.SH")
    u2 = _underlying(session, symbol="000300.SH")
    inst = _instrument(session, u1, code="IC2406")
    hedging_domain.mark(session, [inst.id], actor="a")
    session.flush()
    # Second entry for the same instrument under a different underlying.
    from app.models import HedgeMapEntry
    session.add(HedgeMapEntry(
        underlying_id=u2.id, instrument_id=inst.id,
        exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    session.flush()

    first_entry_id = (
        session.query(HedgeMapEntry.id)
        .filter(HedgeMapEntry.underlying_id == u1.id).scalar()
    )
    hedging_domain.unmark(session, map_entry_ids=[first_entry_id])
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags


def test_unmark_by_map_entry_id_removes_hedge_tag_for_legacy_null_instrument_entry(session):
    """A legacy HedgeMapEntry with instrument_id=NULL is still real ground
    truth for sync_hedge_tag (matched via exchange/contract_code) — deleting
    it by map_entry_id must resync the matching instrument's tag, not skip
    it just because the row's own instrument_id column is NULL."""
    from app.models import HedgeMapEntry

    u = _underlying(session)
    inst = _instrument(session, u)
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=None,
        exchange=inst.exchange, contract_code=inst.contract_code, reconcile_status="active",
        family="index_future", series_root="IC", instrument_type="future",
    ))
    session.flush()
    from app.services.instruments import sync_hedge_tag
    sync_hedge_tag(session, inst.id)
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags

    entry_id = session.query(HedgeMapEntry).one().id
    hedging_domain.unmark(session, map_entry_ids=[entry_id])
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hedging_domain.py -k "hedge_tag" -v`
Expected: FAIL — `assert "hedge" in inst.tags` fails (empty list), since `mark`/`unmark` don't call `sync_hedge_tag` yet.

- [ ] **Step 3: Wire the calls**

In `backend/app/services/domains/hedging.py`, add the import (top of file, alongside the existing relative imports):

```python
from ..instruments import sync_hedge_tag
```

In `mark()` (currently ends at line 250-251 with `session.add(entry); created.append(entry)` inside the loop, then `return created`), add a call after both the "existing entry" branch and the "new entry" branch — i.e. once per instrument processed, right before the loop's `continue`/end:

```python
def mark(session: Session, instrument_ids: list[int], *, actor: str | None = None) -> list[HedgeMapEntry]:
    created: list[HedgeMapEntry] = []
    now = datetime.utcnow()
    session.flush()
    for inst in (
        session.query(Instrument)
        .filter(Instrument.id.in_(instrument_ids))
        .all()
    ):
        underlying_id = _owning_underlying_id(session, inst)
        if underlying_id is None:
            continue
        existing = (
            session.query(HedgeMapEntry)
            .filter(
                HedgeMapEntry.underlying_id == underlying_id,
                HedgeMapEntry.exchange == inst.exchange,
                HedgeMapEntry.contract_code == inst.contract_code,
            )
            .one_or_none()
        )
        if existing is not None:
            if existing.instrument_id is None:
                existing.instrument_id = inst.id
            sync_hedge_tag(session, inst.id)
            continue
        entry = HedgeMapEntry(
            underlying_id=underlying_id,
            instrument_id=inst.id,
            exchange=inst.exchange,
            contract_code=inst.contract_code,
            family=_family_for(inst),
            series_root=inst.series_root,
            instrument_type="option" if _is_option(inst) else "future",
            option_type=inst.option_type,
            strike=inst.strike,
            expiry=inst.expiry,
            reconcile_status="active" if inst.status == "active" else "stale",
            marked_by=actor,
            marked_at=now,
        )
        session.add(entry)
        session.flush()
        sync_hedge_tag(session, inst.id)
        created.append(entry)
    return created
```

(Note the added `session.flush()` right before `sync_hedge_tag` in the new-entry branch — `sync_hedge_tag` queries `HedgeMapEntry` by SQL, so the just-`add`ed entry must be flushed first or the query won't see it.)

In `unmark()`, collect affected instrument ids before each delete, then sync after:

```python
def unmark(
    session: Session,
    *,
    instrument_ids: list[int] | None = None,
    map_entry_ids: list[int] | None = None,
) -> int:
    affected: set[int] = set()
    removed = 0
    if map_entry_ids:
        # Resolve BOTH durably-linked rows and legacy (instrument_id IS NULL)
        # rows matched only by (exchange, contract_code) — sync_hedge_tag
        # treats the legacy match as real ground truth too (Task 1), so a
        # legacy row's instrument must not be skipped here just because the
        # id column that would normally identify it is NULL.
        rows_being_deleted = (
            session.query(HedgeMapEntry.instrument_id, HedgeMapEntry.exchange, HedgeMapEntry.contract_code)
            .filter(HedgeMapEntry.id.in_(map_entry_ids))
            .all()
        )
        for instrument_id, exchange, contract_code in rows_being_deleted:
            if instrument_id is not None:
                affected.add(instrument_id)
            elif exchange and contract_code:
                affected.update(
                    iid for (iid,) in session.query(Instrument.id)
                    .filter(Instrument.exchange == exchange, Instrument.contract_code == contract_code)
                )
        removed += (
            session.query(HedgeMapEntry)
            .filter(HedgeMapEntry.id.in_(map_entry_ids))
            .delete(synchronize_session=False)
        )
    if instrument_ids:
        affected.update(instrument_ids)
        removed += (
            session.query(HedgeMapEntry)
            .filter(HedgeMapEntry.instrument_id.in_(instrument_ids))
            .delete(synchronize_session=False)
        )
        keys = [
            (i.exchange, i.contract_code)
            for i in session.query(Instrument)
            .filter(Instrument.id.in_(instrument_ids))
            .all()
        ]
        for exch, code in keys:
            removed += (
                session.query(HedgeMapEntry)
                .filter(
                    HedgeMapEntry.instrument_id.is_(None),
                    HedgeMapEntry.exchange == exch,
                    HedgeMapEntry.contract_code == code,
                )
                .delete(synchronize_session=False)
            )
    session.flush()
    for instrument_id in affected:
        sync_hedge_tag(session, instrument_id)
    return removed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_hedging_domain.py -v`
Expected: all pass (existing tests unaffected, 6 new ones pass)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/domains/hedging.py tests/test_hedging_domain.py
git commit -m "feat(hedging): sync hedge tag on mark/unmark"
```

---

### Task 3: Wire into `reconcile_map()`

**Files:**
- Modify: `backend/app/services/hedging_loader.py:228-250`
- Test: `tests/test_hedging_loader.py`

**Interfaces:**
- Consumes: `sync_hedge_tag(session, instrument_id)` from Task 1.

- [ ] **Step 1: Write the failing test**

`tests/test_hedging_loader.py` has `_underlying(session, symbol=...)` (line 12-16, creates an `Underlying`/Instrument row) and `_contract(code=..., strike=..., option_type=...)` (line 19-25, returns an `EnumeratedContract`) helpers, and `hedging_loader._upsert_catalog(session, [contract], underlying_id)` (used at line 30) which creates the real `Instrument` catalog row and returns the set of `(exchange, contract_code)` seen. Add:

```python
def test_reconcile_map_syncs_hedge_tag_on_status_flip(session):
    u = _underlying(session)
    hedging_loader._upsert_catalog(session, [_contract("IC2406")], u.id)
    inst = session.query(Instrument).filter(Instrument.kind == "futures").one()
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=inst.id,
        exchange="CFFEX", contract_code="IC2406",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="active",
    ))
    session.flush()

    hedging_loader.reconcile_map(session)
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags

    # Now the instrument goes inactive (e.g. delisted) — reconcile should
    # flip reconcile_status to "stale" and the tag should follow.
    inst.status = "expired"
    session.flush()
    hedging_loader.reconcile_map(session)
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags


def test_reconcile_map_syncs_hedge_tag_for_legacy_null_instrument_entry(session):
    """A legacy HedgeMapEntry with instrument_id=NULL, matched only via
    (exchange, contract_code), must still resync its matching instrument's
    tag when reconcile_map flips its status — not just entries with a
    durable instrument_id link."""
    u = _underlying(session)
    hedging_loader._upsert_catalog(session, [_contract("IC2409")], u.id)
    inst = session.query(Instrument).filter(Instrument.kind == "futures").one()
    session.add(HedgeMapEntry(
        underlying_id=u.id, instrument_id=None,
        exchange="CFFEX", contract_code="IC2409",
        family="index_future", series_root="IC", instrument_type="future",
        reconcile_status="stale",
    ))
    session.flush()

    hedging_loader.reconcile_map(session)
    session.flush()
    session.refresh(inst)
    assert "hedge" in inst.tags  # instrument is active -> entry flips to active

    inst.status = "expired"
    session.flush()
    hedging_loader.reconcile_map(session)
    session.flush()
    session.refresh(inst)
    assert "hedge" not in inst.tags
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_hedging_loader.py -k reconcile_map_syncs_hedge_tag -v`
Expected: FAIL at the first `assert "hedge" in inst.tags` (empty tags list)

- [ ] **Step 3: Wire the call**

In `backend/app/services/hedging_loader.py`, add the import near the top (alongside the other relative service imports in that file):

```python
from .instruments import sync_hedge_tag
```

Change `reconcile_map` from:

```python
def reconcile_map(session: Session) -> None:
    """Recompute every map entry's reconcile_status against active catalog rows.
    ...
    """
    active_ids = {
        r[0]
        for r in session.query(Instrument.id).filter(Instrument.status == "active")
    }
    active_keys = {
        (r.exchange, r.contract_code)
        for r in session.query(
            Instrument.exchange, Instrument.contract_code
        ).filter(Instrument.status == "active")
    }
    for entry in session.query(HedgeMapEntry).all():
        if entry.instrument_id is not None:
            is_active = entry.instrument_id in active_ids
        else:
            is_active = (entry.exchange, entry.contract_code) in active_keys
        entry.reconcile_status = "active" if is_active else "stale"
```

to:

```python
def reconcile_map(session: Session) -> None:
    """Recompute every map entry's reconcile_status against active catalog rows.
    ...
    """
    active_ids = {
        r[0]
        for r in session.query(Instrument.id).filter(Instrument.status == "active")
    }
    active_keys = {
        (r.exchange, r.contract_code)
        for r in session.query(
            Instrument.exchange, Instrument.contract_code
        ).filter(Instrument.status == "active")
    }
    # Every instrument reachable by (exchange, contract_code) — not just
    # active ones — so a legacy entry flipping stale still resolves to the
    # instrument whose tag needs to come back off.
    key_to_instrument_ids: dict[tuple[str, str], list[int]] = {}
    for iid, exch, code in session.query(Instrument.id, Instrument.exchange, Instrument.contract_code):
        if exch and code:
            key_to_instrument_ids.setdefault((exch, code), []).append(iid)

    touched_instrument_ids: set[int] = set()
    for entry in session.query(HedgeMapEntry).all():
        if entry.instrument_id is not None:
            is_active = entry.instrument_id in active_ids
            touched_instrument_ids.add(entry.instrument_id)
        else:
            is_active = (entry.exchange, entry.contract_code) in active_keys
            # Legacy row (never backfilled with a durable instrument_id) —
            # sync_hedge_tag treats this match as real ground truth too
            # (Task 1), so its instrument(s) must be resynced here just like
            # the direct-link case above.
            touched_instrument_ids.update(
                key_to_instrument_ids.get((entry.exchange, entry.contract_code), [])
            )
        entry.reconcile_status = "active" if is_active else "stale"
    session.flush()
    for instrument_id in touched_instrument_ids:
        sync_hedge_tag(session, instrument_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_hedging_loader.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/hedging_loader.py tests/test_hedging_loader.py
git commit -m "feat(hedging): sync hedge tag on catalog reconciliation"
```

---

### Task 4: Wire into `register_underlying_tool` and `delete_underlying_default`

**Files:**
- Modify: `backend/app/tools/underlyings.py:23-73`
- Modify: `backend/app/services/underlying_defaults.py:42-52`
- Test: `tests/test_register_underlying_tool.py`
- Test: `tests/test_underlying_defaults.py`

**Interfaces:**
- Consumes: `sync_hedge_tag(session, instrument_id)` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_register_underlying_tool.py` — this file's convention (see `test_register_underlying_creates_new_instrument`) is an autouse `_db` fixture plus `database.SessionLocal()` directly (no `session` fixture parameter):

```python
def test_register_underlying_reactivating_a_stock_syncs_hedge_tag():
    """Reactivating an inactive stock instrument is the one non-hedge-map
    write path that can flip its self-hedge eligibility — the tool must
    sync the hedge tag, not just the underlying tag."""
    from app.tools.underlyings import register_underlying_tool

    with database.SessionLocal() as session:
        stock = Instrument(symbol="600519.SH", kind="stock", status="draft", currency="CNY")
        session.add(stock)
        session.commit()
        stock_id = stock.id

    register_underlying_tool.invoke({"symbol": "600519.SH"})

    with database.SessionLocal() as session:
        row = session.get(Instrument, stock_id)
        assert row.status == "active"
        assert "hedge" in row.tags
```

(`database` and `Instrument` are already imported at the top of this file.)

Add to `tests/test_underlying_defaults.py` — this file's convention is a `session: Session` fixture parameter backed by `Base.metadata.create_all` on an in-memory engine (see `test_underlying_pricing_default_persists_and_round_trips`), and `UnderlyingPricingDefault(underlying=..., ...)` where `underlying=` is a synonym for the `symbol` column:

```python
def test_delete_underlying_default_syncs_hedge_tag_for_stock(session: Session) -> None:
    from app.services.underlying_defaults import delete_underlying_default

    stock = UnderlyingPricingDefault(
        underlying="600519.SH", kind="stock", status="active", tags=["hedge"],
    )
    session.add(stock)
    session.commit()

    delete_underlying_default(session, underlying="600519.SH")
    session.commit()

    session.refresh(stock)
    assert stock.status == "inactive"
    assert "hedge" not in stock.tags
```

(`UnderlyingPricingDefault` and `Session` are already imported at the top of this file.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_register_underlying_tool.py -k syncs_hedge_tag tests/test_underlying_defaults.py -k syncs_hedge_tag -v`
Expected: both FAIL — `"hedge"` absent/present opposite of the assertion, since neither call site syncs yet.

- [ ] **Step 3: Wire the calls**

In `backend/app/tools/underlyings.py`, add the import:

```python
from app.services.instruments import sync_hedge_tag
```

Change the body right after the status-activation branch (currently lines 40-42) — add the sync call after the whole tags-mutation block, before `session.flush()`:

```python
        else:
            instrument = existing
            action = "already_registered"
            if instrument.status != "active":
                instrument.status = "active"
                action = "tagged_existing"
        tags = list(instrument.tags or [])
        if "underlying" not in tags:
            tags.append("underlying")
            instrument.tags = tags
            if action == "already_registered":
                action = "tagged_existing"
        sync_hedge_tag(session, instrument.id)
        session.flush()
```

In `backend/app/services/underlying_defaults.py`, add the import:

```python
from .instruments import sync_hedge_tag
```

Change `delete_underlying_default` from:

```python
def delete_underlying_default(session: Session, *, underlying: str) -> None:
    cleaned = (underlying or "").strip()
    row = (
        session.query(UnderlyingPricingDefault)
        .filter(UnderlyingPricingDefault.symbol == cleaned)
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"underlying not found: {cleaned}")
    row.status = "inactive"
    session.flush()
```

to:

```python
def delete_underlying_default(session: Session, *, underlying: str) -> None:
    cleaned = (underlying or "").strip()
    row = (
        session.query(UnderlyingPricingDefault)
        .filter(UnderlyingPricingDefault.symbol == cleaned)
        .one_or_none()
    )
    if row is None:
        raise LookupError(f"underlying not found: {cleaned}")
    row.status = "inactive"
    session.flush()
    sync_hedge_tag(session, row.id)
    session.flush()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_register_underlying_tool.py tests/test_underlying_defaults.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/tools/underlyings.py backend/app/services/underlying_defaults.py tests/test_register_underlying_tool.py tests/test_underlying_defaults.py
git commit -m "feat(underlyings): sync hedge tag on stock activation/deactivation"
```

---

### Task 5: `set_instrument_tags` strips and re-derives `"hedge"`

**Files:**
- Modify: `backend/app/services/instruments.py:113-119`
- Test: `tests/test_instruments_service.py`

**Interfaces:**
- Consumes: `sync_hedge_tag(session, instrument_id)` from Task 1.

This is the invariant that makes `"hedge"` structurally impossible to hand-edit. There's an existing test that currently expects the OLD behavior (client-supplied `"Hedge"` sticks) — it must be corrected, not just added around.

- [ ] **Step 1: Update the existing test and write new ones**

In `tests/test_instruments_service.py`, the existing test at line 121-130 currently reads:

```python
def test_set_instrument_tags_replaces_and_normalizes():
    from app import database
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        row = _mk(session, symbol="SETTAGS.SH")
        session.commit()

        updated = set_instrument_tags(session, row.id, ["Underlying", " underlying ", "Hedge"])
        assert updated.tags == ["underlying", "hedge"]
```

This assertion is about to become wrong: once `"hedge"` is server-derived, a client-supplied `"Hedge"` must be stripped rather than kept, and re-derived from ground truth — which is false here (no `HedgeMapEntry`, `kind="index"` not a self-hedging stock). Replace the assertion:

```python
def test_set_instrument_tags_replaces_and_normalizes():
    from app import database
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        row = _mk(session, symbol="SETTAGS.SH")
        session.commit()

        updated = set_instrument_tags(session, row.id, ["Underlying", " underlying ", "Hedge"])
        # "Hedge" is client-supplied and stripped; ground truth (no active
        # HedgeMapEntry, not a self-hedging stock) says it shouldn't be re-added.
        assert updated.tags == ["underlying"]
```

Add new tests right after it:

```python
def test_set_instrument_tags_ignores_client_supplied_hedge_removal():
    """A client can't remove a true-derived "hedge" tag by omitting it either
    -- set_instrument_tags re-derives from ground truth after stripping."""
    from app import database
    from app.models import HedgeMapEntry
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        underlying = _mk(session, symbol="000905.SH")
        inst = _mk(session, symbol="IC2406.CFFEX", kind="futures", exchange="CFFEX",
                   contract_code="IC2406", tags=["hedge"])
        session.add(HedgeMapEntry(
            underlying_id=underlying.id, instrument_id=inst.id,
            exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
            family="index_future", series_root="IC", instrument_type="future",
        ))
        session.commit()

        # Client PUTs a tag list with "hedge" dropped, trying to remove it by hand.
        updated = set_instrument_tags(session, inst.id, ["underlying"])
        assert set(updated.tags) == {"underlying", "hedge"}


def test_set_instrument_tags_client_supplied_hedge_has_no_effect_when_untrue():
    from app import database
    from app.services.instruments import set_instrument_tags

    with database.SessionLocal() as session:
        row = _mk(session, symbol="NOTHEDGE.SH")
        session.commit()

        updated = set_instrument_tags(session, row.id, ["hedge", "custom"])
        assert updated.tags == ["custom"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_instruments_service.py -k set_instrument_tags -v`
Expected: `test_set_instrument_tags_replaces_and_normalizes` FAILs (still returns `["underlying", "hedge"]`); the two new tests FAIL too.

- [ ] **Step 3: Implement the strip-and-rederive**

In `backend/app/services/instruments.py`, change:

```python
def set_instrument_tags(session: Session, instrument_id: int, tags: list[str]) -> Instrument:
    row = session.get(Instrument, instrument_id)
    if row is None:
        raise LookupError(f"Instrument {instrument_id} not found")
    row.tags = _normalize_tags(tags)
    session.flush()
    return row
```

to:

```python
def set_instrument_tags(session: Session, instrument_id: int, tags: list[str]) -> Instrument:
    row = session.get(Instrument, instrument_id)
    if row is None:
        raise LookupError(f"Instrument {instrument_id} not found")
    # "hedge" is server-derived (see sync_hedge_tag) — strip any client-
    # supplied value before saving, then re-derive it from ground truth in
    # the same call so it can never be hand-added or hand-removed.
    row.tags = _normalize_tags([t for t in tags if t.strip().lower() != "hedge"])
    session.flush()
    sync_hedge_tag(session, instrument_id)
    session.flush()
    return row
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_instruments_service.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/instruments.py tests/test_instruments_service.py
git commit -m "feat(instruments): make hedge tag non-client-writable via tags PUT"
```

---

### Task 6: Wire into manual create/patch endpoints

**Files:**
- Modify: `backend/app/main.py:242-248,1971-2071`
- Test: `tests/test_instruments_api.py`

**Interfaces:**
- Consumes: `sync_hedge_tag(session, instrument_id)` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_instruments_api.py`, as new methods inside `class TestCreateInstrument` (line 192, already holds `test_creates_minimal_instrument`):

```python
    def test_creates_active_stock_gets_hedge_tag(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.post(
            "/api/instruments",
            json={"symbol": "600519.SH", "kind": "stock", "status": "active"},
        )
        assert resp.status_code == 201
        assert "hedge" in resp.json()["tags"]

    def test_creates_draft_stock_has_no_hedge_tag(self, tmp_path: Path):
        client = _make_client(tmp_path)
        resp = client.post(
            "/api/instruments",
            json={"symbol": "600519.SH", "kind": "stock", "status": "draft"},
        )
        assert resp.status_code == 201
        assert "hedge" not in resp.json()["tags"]
```

Add these methods inside `class TestPatchInstrument` (line 279, already holds `test_patch_applies_only_provided_fields`):

```python
    def test_patch_stock_to_active_adds_hedge_tag(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="600519.SH", kind="stock", status="draft")
        resp = client.patch(f"/api/instruments/{iid}", json={"status": "active"})
        assert resp.status_code == 200
        assert "hedge" in resp.json()["tags"]

    def test_patch_active_stock_to_inactive_removes_hedge_tag(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="600519.SH", kind="stock", status="active")
        with database.SessionLocal() as s:
            from app.services.instruments import sync_hedge_tag
            sync_hedge_tag(s, iid)
            s.commit()
        resp = client.patch(f"/api/instruments/{iid}", json={"status": "inactive"})
        assert resp.status_code == 200
        assert "hedge" not in resp.json()["tags"]

    def test_patch_stock_to_non_stock_kind_removes_hedge_tag(self, tmp_path: Path):
        client = _make_client(tmp_path)
        iid = _add_instrument(tmp_path, symbol="600519.SH", kind="stock", status="active")
        with database.SessionLocal() as s:
            from app.services.instruments import sync_hedge_tag
            sync_hedge_tag(s, iid)
            s.commit()
        resp = client.patch(f"/api/instruments/{iid}", json={"kind": "index"})
        assert resp.status_code == 200
        assert "hedge" not in resp.json()["tags"]

    def test_patch_active_futures_to_expired_removes_hedge_tag_despite_stale_active_map_entry(self, tmp_path: Path):
        """The real gap this regression pins down: reconcile_status on the
        HedgeMapEntry row is only refreshed by mark/unmark/reconcile_map, not
        by this PATCH endpoint. sync_hedge_tag must not be fooled by that
        stale-active map entry once the instrument's own status changes —
        it must also check Instrument.status itself, matching what
        _active_instruments (the real MILP query) actually filters on."""
        from app.models import HedgeMapEntry

        client = _make_client(tmp_path)
        iid = _add_instrument(
            tmp_path, symbol="IC2406.CFFEX", kind="futures",
            exchange="CFFEX", contract_code="IC2406",
        )
        underlying_id = _add_instrument(tmp_path, symbol="000905.SH", kind="index")
        with database.SessionLocal() as s:
            from app.services.instruments import sync_hedge_tag
            s.add(HedgeMapEntry(
                underlying_id=underlying_id, instrument_id=iid,
                exchange="CFFEX", contract_code="IC2406", reconcile_status="active",
                family="index_future", series_root="IC", instrument_type="future",
            ))
            s.commit()
            sync_hedge_tag(s, iid)
            s.commit()

        resp = client.patch(f"/api/instruments/{iid}", json={"status": "expired"})
        assert resp.status_code == 200
        assert "hedge" not in resp.json()["tags"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_instruments_api.py -k "hedge_tag" -v`
Expected: FAIL — none of the responses carry `"hedge"` yet, and the "removes" tests fail because the tag was never added by these endpoints in the first place (create/patch don't call `sync_hedge_tag` yet, so the pre-seeded tag from the test's own manual `sync_hedge_tag` call stays untouched — still a real failure for `test_patch_active_stock_to_inactive_removes_hedge_tag` / `test_patch_stock_to_non_stock_kind_removes_hedge_tag` / `test_patch_active_futures_to_expired_removes_hedge_tag_despite_stale_active_map_entry`).

- [ ] **Step 3: Wire the calls**

In `backend/app/main.py`, change the import block at line 242-248 from:

```python
from .services.instruments import (
    list_instruments,
    resolvable_market_data_instruments,
    set_instrument_tags,
    sync_instruments_from_positions,
    validate_instrument_terms,
)
```

to:

```python
from .services.instruments import (
    list_instruments,
    resolvable_market_data_instruments,
    set_instrument_tags,
    sync_hedge_tag,
    sync_instruments_from_positions,
    validate_instrument_terms,
)
```

In `create_instrument_endpoint`, add the call right after `session.flush()` (currently line 2014), before `record_audit`:

```python
        session.add(row)
        session.flush()
        sync_hedge_tag(session, row.id)
        record_audit(
```

In `patch_instrument_endpoint`, add the call right after `session.flush()` (currently line 2060), before `record_audit`. This runs unconditionally regardless of which fields were patched — cheaper and simpler than checking whether `kind`/`status` were in the patched set, and `sync_hedge_tag` is a no-op write when the tag already matches truth:

```python
        for key, value in fields.items():
            setattr(row, key, value)
        session.flush()
        sync_hedge_tag(session, row.id)
        record_audit(
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_instruments_api.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py tests/test_instruments_api.py
git commit -m "feat(instruments): sync hedge tag on manual create/patch"
```

---

### Task 7: Migration `0044_hedge_tag.py` + dev-DB repair path

**Files:**
- Create: `backend/alembic/versions/0044_hedge_tag.py`
- Modify: `backend/app/database.py` (near `_backfill_instrument_underlying_tags`, line 516, and its call site at line 277-284)
- Test: `tests/test_migration_0044.py`

**Interfaces:**
- Produces: `_backfill_instrument_hedge_tags(active_engine: Engine, tables: set[str], inspector: Any) -> None` in `database.py`, called from `_ensure_incremental_schema`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_migration_0044.py`, mirroring `tests/test_migration_0042.py`'s harness exactly but adding a `hedge_map_entries` table to the fixture:

```python
"""Round-trip test for migration 0044_hedge_tag."""
from __future__ import annotations

import importlib
import json
from pathlib import Path

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect


def _run_migration(module, method: str, engine: sa.Engine) -> None:
    connection = engine.connect()
    original_op = module.op
    module.op = Operations(MigrationContext.configure(connection))
    try:
        getattr(module, method)()
        connection.commit()
    finally:
        module.op = original_op
        connection.close()


def _engine_with_instruments_and_hedge_map(tmp_path: Path, name: str) -> sa.Engine:
    # Matches the FULL real `instruments` schema (backend/app/database.py's
    # _ensure_underlying_schema bootstrap CREATE TABLE) — same reason as
    # tests/test_migration_0042.py's fixture: _ensure_incremental_schema
    # always runs _ensure_underlying_schema first, which unconditionally
    # does `CREATE INDEX ... ON instruments (akshare_symbol)`; a stripped-down
    # table missing that column breaks with "no such column", unrelated to
    # anything this test is actually checking.
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE instruments ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "symbol VARCHAR(80) NOT NULL UNIQUE, "
            "display_name VARCHAR(160), "
            "kind VARCHAR(40) NOT NULL DEFAULT 'index', "
            "market VARCHAR(40), "
            "exchange VARCHAR(40), "
            "currency VARCHAR(8) NOT NULL DEFAULT 'CNY', "
            "akshare_symbol VARCHAR(80), "
            "akshare_asset_class VARCHAR(40), "
            "status VARCHAR(40) NOT NULL DEFAULT 'draft', "
            "source VARCHAR(40) NOT NULL DEFAULT 'manual', "
            "rate FLOAT, "
            "dividend_yield FLOAT, "
            "volatility FLOAT, "
            "notes TEXT, "
            "contract_code VARCHAR(80), "
            "series_root VARCHAR(40), "
            "expiry DATE, "
            "multiplier FLOAT, "
            "strike FLOAT, "
            "option_type VARCHAR(4), "
            "parent_id INTEGER, "
            "loaded_at DATETIME, "
            "tags JSON NOT NULL DEFAULT '[]', "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE hedge_map_entries ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "underlying_id INTEGER, "
            "instrument_id INTEGER, "
            "exchange VARCHAR(40), "
            "contract_code VARCHAR(80), "
            "reconcile_status VARCHAR(20) NOT NULL DEFAULT 'active'"
            ")"
        ))
    return engine


def test_upgrade_tags_instrument_with_active_map_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "map.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, 'IC2406.CFFEX', 'futures', 'active')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, 1, 'CFFEX', 'IC2406', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_upgrade_tags_via_legacy_null_instrument_id_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "legacy.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status, exchange, contract_code) "
            "VALUES (1, 'IC2406.CFFEX', 'futures', 'active', 'CFFEX', 'IC2406')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, NULL, 'CFFEX', 'IC2406', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_upgrade_ignores_active_map_entry_when_instrument_itself_inactive(tmp_path: Path) -> None:
    """reconcile_status can be stale relative to the instrument's current
    status (it's only refreshed by mark/unmark/reconcile_map) — a one-time
    backfill must not trust a stale-active map entry over the instrument's
    own status column, matching sync_hedge_tag's truth condition (Task 1)."""
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "inactive_status.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, 'IC2406.CFFEX', 'futures', 'expired')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, 1, 'CFFEX', 'IC2406', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]) == []


def test_upgrade_tags_active_stock_with_no_map_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "stock.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_upgrade_ignores_stale_entry(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "stale.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind) VALUES (1, 'IC2403.CFFEX', 'futures')"
        ))
        conn.execute(sa.text(
            "INSERT INTO hedge_map_entries (underlying_id, instrument_id, exchange, contract_code, reconcile_status) "
            "VALUES (1, 1, 'CFFEX', 'IC2403', 'stale')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]) == []


def test_upgrade_scrubs_pre_existing_false_hedge_tag(tmp_path: Path) -> None:
    """A hand-typed 'hedge' tag (from before this feature made it server-
    derived) on an instrument with no active map entry and not a
    self-hedging stock must be removed, not left in place — this is the
    difference between a true recomputation and an append-only backfill."""
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "scrub.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, tags) "
            "VALUES (1, 'NOTAHEDGE.SH', 'index', '[\"hedge\", \"underlying\"]')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]) == ["underlying"]


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "idem.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second call must not raise or duplicate

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert json.loads(row[0]).count("hedge") == 1


def test_downgrade_removes_hedge_tag(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_hedge_map(tmp_path, "down.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "downgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" not in json.loads(row[0])


def test_init_db_incremental_repair_also_syncs_hedge_tags(tmp_path: Path) -> None:
    """A DB that already has the `tags` column (past 0042) but hasn't run
    0044 yet must still get "hedge" backfilled by the boot-time repair path,
    the same duplicate-not-import convention as the underlying-tag backfill."""
    from app import database

    engine = _engine_with_instruments_and_hedge_map(tmp_path, "repair.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    database._ensure_incremental_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def _engine_with_instruments_only(tmp_path: Path, name: str) -> sa.Engine:
    """No hedge_map_entries table at all — the schema-drift case the
    migration/repair must still tolerate for the active-stock rule. Full
    real `instruments` schema, same reason as
    _engine_with_instruments_and_hedge_map above."""
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE instruments ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "symbol VARCHAR(80) NOT NULL UNIQUE, "
            "display_name VARCHAR(160), "
            "kind VARCHAR(40) NOT NULL DEFAULT 'index', "
            "market VARCHAR(40), "
            "exchange VARCHAR(40), "
            "currency VARCHAR(8) NOT NULL DEFAULT 'CNY', "
            "akshare_symbol VARCHAR(80), "
            "akshare_asset_class VARCHAR(40), "
            "status VARCHAR(40) NOT NULL DEFAULT 'draft', "
            "source VARCHAR(40) NOT NULL DEFAULT 'manual', "
            "rate FLOAT, "
            "dividend_yield FLOAT, "
            "volatility FLOAT, "
            "notes TEXT, "
            "contract_code VARCHAR(80), "
            "series_root VARCHAR(40), "
            "expiry DATE, "
            "multiplier FLOAT, "
            "strike FLOAT, "
            "option_type VARCHAR(4), "
            "parent_id INTEGER, "
            "loaded_at DATETIME, "
            "tags JSON NOT NULL DEFAULT '[]', "
            "created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
    return engine


def test_upgrade_tags_active_stock_when_hedge_map_entries_table_absent(tmp_path: Path) -> None:
    engine = _engine_with_instruments_only(tmp_path, "no_map_table.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0044_hedge_tag")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])


def test_init_db_incremental_repair_tags_active_stock_when_hedge_map_entries_table_absent(tmp_path: Path) -> None:
    from app import database

    engine = _engine_with_instruments_only(tmp_path, "repair_no_map_table.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (id, symbol, kind, status) VALUES (1, '600519.SH', 'stock', 'active')"
        ))
    database._ensure_incremental_schema(engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE id=1")).fetchone()
    assert "hedge" in json.loads(row[0])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_migration_0044.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backend.alembic.versions.0044_hedge_tag'`

- [ ] **Step 3: Write the migration**

Create `backend/alembic/versions/0044_hedge_tag.py`:

```python
"""hedge tag — server-derived "hedge" classification on Instrument.tags

Revision ID: 0044_hedge_tag
Revises: 0043_agent_action_audits
Create Date: 2026-07-02

Recomputes (not appends) "hedge" on every instrument's `tags` column:
tagged iff it's referenced by a `hedge_map_entries` row with
reconcile_status='active' (directly via instrument_id, or via legacy
(exchange, contract_code) matching for rows never backfilled with a durable
link), OR kind='stock' AND status='active' (the pre-existing stock
self-hedge default). Must be a full recomputation, not an append-only
backfill: the tags PUT endpoint accepted arbitrary tags before this feature
shipped its "hedge"-stripping, so a pre-existing hand-typed "hedge" tag that
doesn't satisfy either truth condition must be scrubbed, not left in place.

HOUSE RULE: migration-local Core SQL / sa.Table on a fresh MetaData only —
never import app models/services (they drift to the future schema).
"""
from __future__ import annotations

import json

from alembic import op
from sqlalchemy import inspect, text


revision = "0044_hedge_tag"
down_revision = "0043_agent_action_audits"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # Only `instruments` is required. The active-stock self-hedge rule is
    # derivable from `instruments` alone — gating the whole function on
    # `hedge_map_entries` also existing would silently skip tagging active
    # stocks on a DB that has `instruments.tags` but no hedge-map table yet
    # (exactly the schema-drift scenario this migration/repair path exists
    # to tolerate).
    if "instruments" not in _tables():
        return
    bind = op.get_bind()
    hedge_ids: set[int] = set()

    if "hedge_map_entries" in _tables():
        # Both queries also require the Instrument row itself to be
        # status='active' — mirrors sync_hedge_tag's truth condition (Task
        # 1), which must check this because reconcile_status can be stale
        # relative to the instrument's current status. A one-time backfill
        # is exactly the place that stale data is most likely to already
        # exist, so this isn't optional here either.
        for (instrument_id,) in bind.execute(
            text(
                "SELECT DISTINCT h.instrument_id FROM hedge_map_entries h "
                "JOIN instruments i ON i.id = h.instrument_id "
                "WHERE h.reconcile_status = 'active' AND i.status = 'active'"
            )
        ).fetchall():
            hedge_ids.add(instrument_id)

        for (instrument_id,) in bind.execute(
            text(
                "SELECT DISTINCT i.id FROM instruments i "
                "JOIN hedge_map_entries h ON h.instrument_id IS NULL "
                "AND h.exchange = i.exchange AND h.contract_code = i.contract_code "
                "WHERE h.reconcile_status = 'active' AND i.status = 'active' "
                "AND i.exchange IS NOT NULL AND i.contract_code IS NOT NULL"
            )
        ).fetchall():
            hedge_ids.add(instrument_id)

    for (instrument_id,) in bind.execute(
        text("SELECT id FROM instruments WHERE kind = 'stock' AND status = 'active'")
    ).fetchall():
        hedge_ids.add(instrument_id)

    # Full recompute, not append-only: strip any pre-existing "hedge" tag
    # from every row first (it may have been hand-typed through the tags
    # PUT endpoint before this feature made "hedge" server-derived), then
    # add it back only where ground truth says so.
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
    if "instruments" not in _tables():
        return
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

- [ ] **Step 4: Run migration tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_migration_0044.py -v -k "not incremental_repair"`
Expected: 9 pass (all except the 2 `_ensure_incremental_schema` ones, not written yet)

- [ ] **Step 5: Add the dev-DB repair duplicate**

In `backend/app/database.py`, add `_backfill_instrument_hedge_tags` right after `_backfill_instrument_underlying_tags` (which ends around line 588 — check with `grep -n "^def _ensure_underlying_schema" backend/app/database.py` to find the exact boundary):

```python
def _backfill_instrument_hedge_tags(active_engine: Engine, tables: set[str], inspector: Any) -> None:
    """Mirrors migration 0044_hedge_tag's full-recompute backfill —
    duplicated intentionally, not imported (migrations aren't reusable
    modules; same duplicate-not-import convention as
    _backfill_instrument_underlying_tags). Keep the two in sync if either
    changes. Only requires `instruments` — the active-stock self-hedge rule
    is derivable from `instruments` alone, so a DB without `hedge_map_entries`
    yet must still get that half of the recompute, not skip the function
    entirely."""
    with active_engine.begin() as connection:
        hedge_ids: set[int] = set()

        if "hedge_map_entries" in tables:
            # Both queries also require the Instrument row itself to be
            # status='active' — see the matching comment in migration
            # 0044_hedge_tag.upgrade(); reconcile_status can be stale
            # relative to the instrument's current status.
            for (instrument_id,) in connection.execute(
                text(
                    "SELECT DISTINCT h.instrument_id FROM hedge_map_entries h "
                    "JOIN instruments i ON i.id = h.instrument_id "
                    "WHERE h.reconcile_status = 'active' AND i.status = 'active'"
                )
            ).fetchall():
                hedge_ids.add(instrument_id)

            for (instrument_id,) in connection.execute(
                text(
                    "SELECT DISTINCT i.id FROM instruments i "
                    "JOIN hedge_map_entries h ON h.instrument_id IS NULL "
                    "AND h.exchange = i.exchange AND h.contract_code = i.contract_code "
                    "WHERE h.reconcile_status = 'active' AND i.status = 'active' "
                    "AND i.exchange IS NOT NULL AND i.contract_code IS NOT NULL"
                )
            ).fetchall():
                hedge_ids.add(instrument_id)

        for (instrument_id,) in connection.execute(
            text("SELECT id FROM instruments WHERE kind = 'stock' AND status = 'active'")
        ).fetchall():
            hedge_ids.add(instrument_id)

        for (instrument_id, tags_raw) in connection.execute(
            text("SELECT id, tags FROM instruments WHERE tags LIKE '%\"hedge\"%'")
        ).fetchall():
            current = json.loads(tags_raw) if tags_raw else []
            if "hedge" in current and instrument_id not in hedge_ids:
                current = [t for t in current if t != "hedge"]
                connection.execute(
                    text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                    {"tags": json.dumps(current), "id": instrument_id},
                )

        for instrument_id in sorted(hedge_ids):
            row = connection.execute(
                text("SELECT tags FROM instruments WHERE id = :id"),
                {"id": instrument_id},
            ).fetchone()
            if row is None:
                continue
            current = json.loads(row[0]) if row[0] else []
            if "hedge" not in current:
                current.append("hedge")
                connection.execute(
                    text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                    {"tags": json.dumps(current), "id": instrument_id},
                )
```

Then call it unconditionally on every boot (not gated on the one-time `"tags" not in instrument_cols` column-add event, unlike the underlying backfill) — change the block at line 277-284 from:

```python
    if "instruments" in tables:
        instrument_cols = {c["name"] for c in inspector.get_columns("instruments")}
        if "tags" not in instrument_cols:
            with active_engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE instruments ADD COLUMN tags JSON NOT NULL DEFAULT '[]'")
                )
            _backfill_instrument_underlying_tags(active_engine, tables, inspector)
```

to:

```python
    if "instruments" in tables:
        instrument_cols = {c["name"] for c in inspector.get_columns("instruments")}
        if "tags" not in instrument_cols:
            with active_engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE instruments ADD COLUMN tags JSON NOT NULL DEFAULT '[]'")
                )
            _backfill_instrument_underlying_tags(active_engine, tables, inspector)
        # Unlike the underlying-tag backfill above, this runs every boot, not
        # just once at column-add time: "hedge" must track hedge_map_entries
        # ground truth even on a DB that already has the `tags` column (past
        # 0042) but hasn't run migration 0044 yet, and the recompute is cheap
        # and idempotent by construction (see sync_hedge_tag).
        _backfill_instrument_hedge_tags(active_engine, tables, inspector)
```

- [ ] **Step 6: Run all migration + database tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_migration_0044.py tests/test_migration_0042.py -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add backend/alembic/versions/0044_hedge_tag.py backend/app/database.py tests/test_migration_0044.py
git commit -m "feat(instruments): migration 0044 backfills/recomputes hedge tag"
```

---

### Task 8: Frontend — drop `ROLES`, extend `TAGS`

**Files:**
- Modify: `frontend/src/routes/Instruments.tsx`
- Modify: `frontend/src/routes/Instruments.live.tsx`
- Modify: `frontend/src/components/TagEditor.css`
- Test: `frontend/src/routes/Instruments.test.tsx` (check the exact filename first: `ls frontend/src/routes/Instruments*.test.tsx`)

**Interfaces:**
- Consumes: `Instrument.tags: string[]` (already exists on the frontend `Instrument` type, `Instruments.tsx:65`), containing `"hedge"` whenever the backend has derived it true (Tasks 1-7).

- [ ] **Step 1: Update the existing ROLES tests and write new ones**

`frontend/src/routes/Instruments.test.tsx` has a fixture factory `instrument(overrides)` (line 13-42, defaults `tags: []`), a `defaultProps` object (line 44+, currently includes `rolesByInstrumentId: {}` at line 65), and renders via `render(<Instruments {...defaultProps} .../>)`.

Two existing tests (lines 280-290) assert the now-removed behavior and must be deleted:

```tsx
  it('renders ROLES badges from rolesByInstrumentId', () => {
    const roles = { 1: { underlying: true, hedge: false } };
    render(<Instruments {...defaultProps} rolesByInstrumentId={roles} />);
    expect(screen.getByText('underlying')).toBeInTheDocument();
  });

  it('renders hedge badge when rolesByInstrumentId marks hedge=true', () => {
    const roles = { 1: { underlying: false, hedge: true } };
    render(<Instruments {...defaultProps} rolesByInstrumentId={roles} />);
    expect(screen.getByText('hedge')).toBeInTheDocument();
  });
```

Delete both, and remove `rolesByInstrumentId: {},` from `defaultProps` (line 65).

Replace them with:

```tsx
  it('does not render a ROLES column', () => {
    render(<Instruments {...defaultProps} rows={[instrument({ tags: ['underlying', 'hedge'] })]} />);
    expect(screen.queryByText('ROLES')).not.toBeInTheDocument();
  });

  it('renders hedge as a non-removable chip in the TAGS cell', () => {
    render(<Instruments {...defaultProps} rows={[instrument({ tags: ['underlying', 'hedge'] })]} />);
    // The readonly hedge chip has no remove button inside it.
    const hedgeChip = screen.getByText('hedge');
    expect(hedgeChip.querySelector('button')).toBeNull();
    // "underlying" is still editable via TagEditor's chip-with-remove-button.
    const underlyingChip = screen.getByText('underlying');
    expect(underlyingChip.querySelector('button')).not.toBeNull();
  });

  it('preserves the hedge tag when removing another tag', async () => {
    const onSetInstrumentTags = vi.fn().mockResolvedValue(undefined);
    render(
      <Instruments
        {...defaultProps}
        rows={[instrument({ id: 1, tags: ['underlying', 'hedge'] })]}
        onSetInstrumentTags={onSetInstrumentTags}
      />,
    );
    const user = userEvent.setup();
    const underlyingChip = screen.getByText('underlying');
    await user.click(underlyingChip.querySelector('button')!);
    expect(onSetInstrumentTags).toHaveBeenCalledWith(1, ['hedge']);
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- Instruments.test.tsx`
Expected: FAIL — the old ROLES-badge tests fail once `rolesByInstrumentId` is removed from `defaultProps` (TypeScript prop error) / the new tests fail because the `ROLES` column and the readonly hedge chip don't exist yet.

- [ ] **Step 3: Remove `ROLES`, extend `TAGS`**

In `frontend/src/routes/Instruments.tsx`:

Remove the `InstrumentRoles` type (lines 70-73):

```tsx
export type InstrumentRoles = {
  underlying?: boolean;
  hedge?: boolean;
};
```

Remove the `rolesByInstrumentId` prop from `Props` (line 105, with its comment on line 104):

```tsx
  /** Map from instrument_id to role flags. */
  rolesByInstrumentId: Record<number, InstrumentRoles>;
```

Remove the `RolesBadges` component (lines 285-293):

```tsx
function RolesBadges({ roles }: { roles: InstrumentRoles | undefined }) {
  if (!roles) return null;
  return (
    <span className="wl-instruments__roles">
      {roles.underlying && <span className="wl-instruments__role-badge is-underlying">underlying</span>}
      {roles.hedge && <span className="wl-instruments__role-badge is-hedge">hedge</span>}
    </span>
  );
}
```

Remove `rolesByInstrumentId` from every remaining occurrence:
- Line 307 (`  rolesByInstrumentId,` in a destructure) — delete the line.
- Line 308: `}: Pick<Props, 'rows' | 'loading' | 'onSaveInstrument' | 'onSetInstrumentTags' | 'rolesByInstrumentId'> & {` becomes `}: Pick<Props, 'rows' | 'loading' | 'onSaveInstrument' | 'onSetInstrumentTags'> & {`.
- Line 625 (`  rolesByInstrumentId,` in the main component's destructure) — delete the line.
- Line 1078 (`          rolesByInstrumentId={rolesByInstrumentId}` in the JSX render) — delete the line.

Confirm nothing is left with `grep -n "rolesByInstrumentId\|InstrumentRoles" frontend/src/routes/Instruments.tsx` — expect zero matches after this step (the type definition and `RolesBadges` function were already removed above).

Remove the `ROLES` column header (line 358) and the `ROLES`+`TAGS` cell block (lines 450-461), replacing it with `TAGS` only:

```tsx
                <th>ROLES</th>
```

becomes (deleted entirely — no replacement header).

```tsx
                    {/* ROLES */}
                    <td>
                      <RolesBadges roles={rolesByInstrumentId[row.id]} />
                    </td>

                    {/* TAGS */}
                    <td>
                      <TagEditor
                        tags={row.tags}
                        onChange={(next) => { void onSetInstrumentTags(row.id, next); }}
                      />
                    </td>
```

becomes:

```tsx
                    {/* TAGS */}
                    <td>
                      {row.tags.includes('hedge') && (
                        <span
                          className="wl-tageditor__chip wl-tageditor__chip--readonly"
                          title="Auto-managed from Allowed Hedges"
                        >
                          hedge
                        </span>
                      )}
                      <TagEditor
                        tags={row.tags.filter((t) => t !== 'hedge')}
                        onChange={(next) => {
                          void onSetInstrumentTags(row.id, row.tags.includes('hedge') ? [...next, 'hedge'] : next);
                        }}
                      />
                    </td>
```

In `frontend/src/routes/Instruments.live.tsx`:

Remove the `rolesByInstrumentId` `useMemo` (lines 74-87) and its comment:

```tsx
  /** Roles are computed, never stored: an instrument is an "underlying"
   * because open positions reference it (exposure groups in the hedge map),
   * and an "allowed hedge" because a map entry points at it. */
  const rolesByInstrumentId: Record<number, InstrumentRoles> = useMemo(() => {
    const roles: Record<number, InstrumentRoles> = {};
    for (const group of hedgeGroups) {
      if ((group.open_position_count ?? 0) > 0) {
        roles[group.underlying_id] = { ...roles[group.underlying_id], underlying: true };
      }
      for (const entry of group.entries ?? []) {
        if (entry.instrument_id != null) {
          roles[entry.instrument_id] = { ...roles[entry.instrument_id], hedge: true };
        }
      }
    }
    return roles;
  }, [hedgeGroups]);
```

Leave `assumptionUnderlyingRoleSymbols`/`assumptionUnderlyingRoleSymbolSet` (lines 88-99) untouched — they're a separate, unrelated computed value for the Assumptions tab, not part of this feature.

Remove the `rolesByInstrumentId={rolesByInstrumentId}` prop pass (line 643). Leave `hedgeGroups={hedgeGroups}` (line 644) — the Allowed Hedges tab still needs it.

Change the import at line 5 from:

```tsx
import type { Instrument, InstrumentRoles, Tab } from './Instruments';
```

to:

```tsx
import type { Instrument, Tab } from './Instruments';
```

In `frontend/src/components/TagEditor.css`, add the readonly modifier after the existing `.wl-tageditor__chip` rules:

```css
.wl-tageditor__chip--readonly {
  opacity: 0.75;
  cursor: default;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npm test -- Instruments.test.tsx`
Expected: all pass

Run the full frontend suite to catch any other consumer of `InstrumentRoles`/`rolesByInstrumentId`:

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors referencing `InstrumentRoles` or `rolesByInstrumentId`

- [ ] **Step 5: Fix `refreshHedgeData` leaving the Registry TAGS cell stale after mark/unmark**

**This closes a real regression the removal of `ROLES` exposes.** Before this task, the `ROLES` "hedge" badge was computed live from `hedgeGroups` state, which `onHedgeMark`/`onHedgeUnmark`/`onHedgePurgeStale` already refresh via `refreshHedgeData()` (`Instruments.live.tsx:212-218`). Now that the Registry's `TAGS` cell reads `row.tags` from the separate `rows` state instead, marking or unmarking a hedge in the Allowed Hedges tab no longer updates what the Registry tab displays — `refreshHedgeData()` never re-fetches `/api/instruments`. The backend already recomputes the `"hedge"` tag synchronously inside `mark`/`unmark`/`purge_stale` (Tasks 1-2), so the fix is to also reload the instruments list whenever hedge data is refreshed.

First, write the failing test. Create `frontend/src/routes/Instruments.live.test.tsx` (new file — none exists yet):

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { InstrumentsLive } from './Instruments.live';

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  if (input instanceof Request) return input.url;
  return input.toString();
}

const staleGroup = {
  underlying_id: 1,
  underlying_symbol: 'IC2406.CFFEX',
  open_position_count: 0,
  entries: [
    {
      id: 5, instrument_id: 2, exchange: 'CFFEX', contract_code: 'IC2406',
      family: 'index_future', series_root: 'IC', instrument_type: 'future',
      option_type: null, strike: null, expiry: null, reconcile_status: 'stale',
    },
  ],
};

describe('InstrumentsLive — Allowed Hedges mutations refresh the Registry TAGS cell', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('re-fetches /api/instruments after purging stale hedge entries', async () => {
    let instrumentsFetchCount = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = requestUrl(input);
      if (url === '/api/instruments' && !init?.method) {
        instrumentsFetchCount += 1;
        return response([]);
      }
      if (url === '/api/hedging/map' && !init?.method) return response([staleGroup]);
      if (url === '/api/market-data/quotes?latest=1' && !init?.method) return response([]);
      if (url.startsWith('/api/hedging/instruments?') && !init?.method) return response([]);
      if (url === '/api/hedging/map/purge-stale?underlying_id=1' && init?.method === 'POST') {
        return response({ purged: 1 });
      }
      throw new Error(`Unexpected request: ${url} ${init?.method ?? 'GET'}`);
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    render(<InstrumentsLive />);

    await waitFor(() => expect(instrumentsFetchCount).toBe(1));
    await userEvent.click(await screen.findByRole('tab', { name: /allowed hedges/i }));
    await screen.findByLabelText('Purge stale entries');

    await userEvent.click(screen.getByLabelText('Purge stale entries'));

    await waitFor(() => expect(instrumentsFetchCount).toBe(2));
  });
});
```

Run: `cd frontend && npm test -- Instruments.live.test.tsx`
Expected: FAIL at the final `waitFor` — `instrumentsFetchCount` stays `1` (`refreshHedgeData` never re-fetches `/api/instruments`).

Now apply the fix in `frontend/src/routes/Instruments.live.tsx` — change `refreshHedgeData` (line 212-218) from:

```tsx
  const refreshHedgeData = async () => {
    await loadHedgeMap();
    await loadQuotes();
    if (selectedHedgeUnderlyingId !== null) {
      await loadCandidates(selectedHedgeUnderlyingId, hedgeCandidateFilters);
    }
  };
```

to:

```tsx
  const refreshHedgeData = async () => {
    await loadHedgeMap();
    await loadQuotes();
    if (selectedHedgeUnderlyingId !== null) {
      await loadCandidates(selectedHedgeUnderlyingId, hedgeCandidateFilters);
    }
    // Marking/unmarking/purging changes the server-derived "hedge" tag
    // (Tasks 1-2) — the Registry tab's TAGS cell now reads that tag from
    // `rows`, not from `hedgeGroups`, so it must be reloaded here too or it
    // goes stale until an unrelated filter change happens to refetch it.
    await load();
  };
```

Run: `cd frontend && npm test -- Instruments.live.test.tsx`
Expected: PASS

- [ ] **Step 6: Run all Instruments tests to verify nothing else broke**

Run: `cd frontend && npm test -- Instruments`
Expected: all pass (`Instruments.test.tsx`, `Instruments.live.test.tsx`, `InstrumentsAllowedHedges.test.tsx`, `InstrumentsAssumptions.test.tsx`, `InstrumentsMarketData.test.tsx`, `InstrumentsPager.test.tsx`)

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/Instruments.tsx frontend/src/routes/Instruments.live.tsx frontend/src/routes/Instruments.live.test.tsx frontend/src/components/TagEditor.css frontend/src/routes/Instruments.test.tsx
git commit -m "feat(instruments-ui): drop computed ROLES badges, show hedge as a read-only tag chip"
```

---

## Final verification

- [ ] Run the full backend suite: `.venv/bin/python -m pytest` — expect no new failures (pre-existing `.env`-leak gateway test failures are a known environment artifact, not a regression).
- [ ] Run the full frontend suite: `cd frontend && npm test` — expect no new failures.
- [ ] Run frontend type-check: `cd frontend && npx tsc --noEmit`.
- [ ] Confirm `GET /api/instruments?tag=hedge` behaves like `?tag=underlying` by manually hitting it against a dev DB with at least one marked hedge instrument (or via a quick pytest one-liner using the Task 6 test client).
