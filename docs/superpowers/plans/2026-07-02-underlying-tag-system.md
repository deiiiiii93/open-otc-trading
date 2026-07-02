# Underlying Tag System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `Instrument` a real, storable `tags` column so "underlying" is an explicit, pre-assignable classification (not just a role computed from live position data), filter the Booking/TrySolve underlying pickers to that tag, and gate the agent's booking tools so an unregistered underlying triggers a HITL approval (auto-added only under `yolo` mode).

**Architecture:** A `tags: list[str]` JSON column mirrors the existing `Portfolio.tags` pattern exactly (same normalize/dedupe rules, same full-replace PUT endpoint shape, same `TagEditor` frontend component). A new `register_underlying` agent tool creates-or-tags an instrument through the existing HITL interrupt mechanism (`risk_level="irreversible"` so it's gated under `interactive`/`auto`, auto-executes only under `yolo`). `book_position`/`book_hedge` validate the tag before booking and return a structured retry signal instead of silently auto-vivifying instruments.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic (backend), React 19 + TypeScript (frontend), LangChain/LangGraph HITL interrupts (agent layer).

## Global Constraints

- Reviewed spec: `docs/superpowers/specs/2026-07-02-underlying-tag-system-design.md` — every task below implements a section of it verbatim, including its documented accepted/declined trade-offs (full-replace tag writes, no join-table rewrite for `tag=` filtering).
- Follow `frontend/CLAUDE.md`: token-only styling, verify both themes if any new CSS is touched (this plan reuses the existing `TagEditor` component/CSS as-is, so no new styling should be needed).
- Migrations use migration-local Core SQL / `sa.Table` on a fresh `MetaData` — never import app models/services into a migration file (house rule stated in `backend/alembic/versions/0024_instrument_unification.py`; breaking it once broke migration 0018).
- Backend tests: `.venv/bin/python -m pytest`. Frontend tests: `cd frontend && npm test` (vitest); type-check with `npx tsc --noEmit`.

---

### Task 1: Data model, migration, and incremental-schema repair

**Files:**
- Modify: `backend/app/models.py:522-603` (`Instrument` class)
- Modify: `backend/app/database.py:166-260` (`_ensure_incremental_schema`)
- Create: `backend/alembic/versions/0042_instrument_tags.py`
- Test: `tests/test_instrument_models.py`
- Test: `tests/test_migration_0042.py`

**Interfaces:**
- Produces: `Instrument.tags: list[str]` (ORM attribute, JSON column, default `[]`, not null). Every later task reads/writes this attribute directly (no service-layer wrapper needed for the raw attribute itself — `set_instrument_tags()` in Task 2 wraps it for the API).

- [ ] **Step 1: Write the failing model test**

Add to `tests/test_instrument_models.py` (check the file first — if it doesn't import `Instrument`/session fixtures the way shown below, match its existing imports/fixtures instead of introducing new ones):

```python
def test_instrument_tags_defaults_to_empty_list(session):
    from app.models import Instrument

    row = Instrument(symbol="TAGTEST.SH", kind="index")
    session.add(row)
    session.flush()
    assert row.tags == []


def test_instrument_tags_round_trips_a_list(session):
    from app.models import Instrument

    row = Instrument(symbol="TAGTEST2.SH", kind="index", tags=["underlying"])
    session.add(row)
    session.flush()
    session.expire(row)
    assert row.tags == ["underlying"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_instrument_models.py -k tags -v`
Expected: FAIL with `TypeError: 'tags' is an invalid keyword argument for Instrument` (or `AttributeError`).

- [ ] **Step 3: Add the column to the model**

In `backend/app/models.py`, inside `class Instrument` (around line 570, right after `updated_at`):

```python
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
```

Confirm `JSON` is already imported at the top of `models.py` (it is — `Portfolio.tags` at line 506 already uses it).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_instrument_models.py -k tags -v`
Expected: PASS

- [ ] **Step 5: Write the failing incremental-schema-repair test**

Add to `tests/test_migration_0042.py` (new file):

```python
"""Round-trip test for migration 0042_instrument_tags."""
from __future__ import annotations

import importlib
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


def _engine_with_instruments_and_positions(tmp_path: Path, name: str) -> sa.Engine:
    engine = sa.create_engine(f"sqlite+pysqlite:///{tmp_path / name}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE instruments ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " symbol VARCHAR(80) NOT NULL UNIQUE,"
            " kind VARCHAR(40) NOT NULL DEFAULT 'index',"
            " currency VARCHAR(8) NOT NULL DEFAULT 'CNY',"
            " status VARCHAR(40) NOT NULL DEFAULT 'draft',"
            " source VARCHAR(40) NOT NULL DEFAULT 'manual',"
            " expiry DATE,"
            " contract_code VARCHAR(80),"
            " created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,"
            " updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE positions ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " underlying VARCHAR(80),"
            " status VARCHAR(40),"
            " position_kind VARCHAR(20),"
            " source_payload JSON)"
        ))
    return engine


def test_upgrade_adds_tags_column(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "up.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    cols = {c["name"] for c in inspect(engine).get_columns("instruments")}
    assert "tags" in cols


def test_backfill_tags_open_position_underlying(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_open.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('000300.SH', 'index', 'draft')"
        ))
        conn.execute(sa.text(
            "INSERT INTO positions (underlying, status, position_kind, source_payload) "
            "VALUES ('000300.SH', 'open', 'otc', '{}')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='000300.SH'")).fetchone()
    assert row is not None
    import json
    assert "underlying" in json.loads(row[0])


def test_backfill_tags_active_root_instrument_without_open_position(tmp_path: Path) -> None:
    """The chicken-and-egg case this feature exists to fix: an active, curated
    instrument with no open position yet must still get backfilled."""
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_active.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('000905.SH', 'index', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='000905.SH'")).fetchone()
    import json
    assert "underlying" in json.loads(row[0])


def test_backfill_creates_stub_instrument_for_open_position_with_no_instrument_row(tmp_path: Path) -> None:
    """Position.underlying is free-text; an open OTC position can reference a
    symbol with no matching instruments row at all (legacy/bulk-imported data
    that predates ensure_underlying()). The migration must create a minimal
    stub row and tag it, not silently skip it -- otherwise that position's
    underlying becomes permanently unbookable once the tool gate ships."""
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_stub.sqlite3")
    with engine.begin() as conn:
        # Deliberately NO row in `instruments` for this symbol.
        conn.execute(sa.text(
            "INSERT INTO positions (underlying, status, position_kind, source_payload) "
            "VALUES ('ORPHAN.SH', 'open', 'otc', '{}')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT status, tags FROM instruments WHERE symbol='ORPHAN.SH'")).fetchone()
    assert row is not None
    import json
    assert row[0] == "active"
    assert "underlying" in json.loads(row[1])


def test_backfill_excludes_dated_derivative_contracts(tmp_path: Path) -> None:
    """An active dated futures contract (has expiry + contract_code) must NOT
    be backfilled as an underlying, even though it's active."""
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_excl.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status, expiry, contract_code) "
            "VALUES ('IC2606.CFE', 'futures', 'active', '2026-06-01', 'IC2606')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='IC2606.CFE'")).fetchone()
    import json
    assert json.loads(row[0]) == []


def test_backfill_excludes_listed_option(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "backfill_opt.sqlite3")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO instruments (symbol, kind, status) VALUES ('IO2606-C-5000.CFE', 'listed_option', 'active')"
        ))
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)

    with engine.begin() as conn:
        row = conn.execute(sa.text("SELECT tags FROM instruments WHERE symbol='IO2606-C-5000.CFE'")).fetchone()
    import json
    assert json.loads(row[0]) == []


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = _engine_with_instruments_and_positions(tmp_path, "idem.sqlite3")
    mig = importlib.import_module("backend.alembic.versions.0042_instrument_tags")
    _run_migration(mig, "upgrade", engine)
    _run_migration(mig, "upgrade", engine)  # second call must not raise


def test_init_db_incremental_repair_adds_tags_column(tmp_path: Path) -> None:
    """An existing instruments table that predates 0042 gets `tags` added by
    the boot-time incremental repair (not just by Alembic) — this app boots
    local SQLite DBs via create_all(), which doesn't alter existing tables."""
    from app import database

    engine = _engine_with_instruments_and_positions(tmp_path, "old.sqlite3")
    database._ensure_incremental_schema(engine)

    cols = {c["name"] for c in inspect(engine).get_columns("instruments")}
    assert "tags" in cols
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_migration_0042.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.alembic.versions.0042_instrument_tags'`

- [ ] **Step 7: Write the migration**

Create `backend/alembic/versions/0042_instrument_tags.py`:

```python
"""instrument tags — real, storable "underlying" classification

Revision ID: 0042_instrument_tags
Revises: 0041_morning_breach_assemble_prompt
Create Date: 2026-07-02

Adds Instrument.tags (JSON list[str], mirrors Portfolio.tags). Backfills
"underlying" onto:

  * every instrument referenced by an OPEN OTC position (mirrors
    services/underlyings.open_position_underlying_symbols' KNOCKED_OUT_STATUSES
    exclusion: {"Knocked Out", "敲出"} read from source_payload.trade_state), and
  * every ACTIVE root instrument that is NOT a dated derivative contract
    instance (kind != 'listed_option' AND expiry IS NULL AND contract_code IS
    NULL) — this second clause is required, not optional: it's the exact
    "curated but not yet traded" underlying (e.g. 000905.SH, DRAFT-turned-
    active before any position references it) this feature exists to stop
    from disappearing off the Booking/TrySolve pickers on migration day.

An open-position underlying with NO matching instruments row (Position.underlying
is free-text; legacy/bulk-imported positions may predate ensure_underlying())
gets a minimal stub instrument row created, not silently skipped — otherwise a
currently-open position's underlying becomes permanently unbookable once the
picker/tool gate ships, since there'd be no row left to register.

HOUSE RULE: migration-local Core SQL / sa.Table on a fresh MetaData only —
never import app models/services (they drift to the future schema).
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect, text


revision = "0042_instrument_tags"
down_revision = "0041_morning_breach_assemble_prompt"
branch_labels = None
depends_on = None


_KNOCKED_OUT_STATES = {"Knocked Out", "敲出"}


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    if "tags" not in _columns("instruments"):
        op.add_column(
            "instruments",
            sa.Column("tags", sa.JSON(), nullable=False, server_default="[]"),
        )

    bind = op.get_bind()
    tagged: set[str] = set()

    if "positions" in _tables():
        rows = bind.execute(
            text(
                "SELECT underlying, status, source_payload FROM positions "
                "WHERE underlying IS NOT NULL AND status = 'open' "
                "AND position_kind = 'otc'"
            )
        ).fetchall()
        for underlying, status, payload_raw in rows:
            if status == "closed":
                continue
            payload = {}
            if payload_raw:
                try:
                    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
                except (TypeError, ValueError):
                    payload = {}
            state = payload.get("trade_state") if isinstance(payload, dict) else None
            if state in _KNOCKED_OUT_STATES:
                continue
            symbol = (underlying or "").strip()
            if symbol:
                tagged.add(symbol)

    active_root_rows = bind.execute(
        text(
            "SELECT symbol FROM instruments WHERE status = 'active' "
            "AND kind != 'listed_option' AND expiry IS NULL AND contract_code IS NULL"
        )
    ).fetchall()
    for (symbol,) in active_root_rows:
        if symbol:
            tagged.add(symbol)

    for symbol in sorted(tagged):
        row = bind.execute(
            text("SELECT id, tags FROM instruments WHERE symbol = :symbol"),
            {"symbol": symbol},
        ).fetchone()
        if row is None:
            # An open OTC position can reference a symbol with no matching
            # instruments row — Position.underlying is a free-text column,
            # and legacy/bulk-imported positions may never have gone through
            # ensure_underlying(). Skipping here would leave a currently-open
            # position's underlying permanently unbookable once the picker/
            # tool gate ships (book_position would reject a symbol nothing
            # can register, since it's not even a row to tag). Create a
            # minimal stub row instead of a full ensure_underlying()-style
            # inference (the house rule bans importing app services into a
            # migration) — kind/currency mirror the Instrument model's own
            # column defaults (models.py:535-541: kind='index', currency='CNY').
            bind.execute(
                text(
                    "INSERT INTO instruments "
                    "(symbol, kind, currency, status, source, tags, created_at, updated_at) "
                    "VALUES (:symbol, 'index', 'CNY', 'active', 'migration_backfill', :tags, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"symbol": symbol, "tags": json.dumps(["underlying"])},
            )
            continue
        instrument_id, tags_raw = row
        current = json.loads(tags_raw) if tags_raw else []
        if "underlying" not in current:
            current.append("underlying")
            bind.execute(
                text("UPDATE instruments SET tags = :tags WHERE id = :id"),
                {"tags": json.dumps(current), "id": instrument_id},
            )


def downgrade() -> None:
    if "tags" in _columns("instruments"):
        with op.batch_alter_table("instruments") as batch:
            batch.drop_column("tags")
```

- [ ] **Step 8: Run migration tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_migration_0042.py -v`
Expected: 6 of 7 pass; `test_init_db_incremental_repair_adds_tags_column` still FAILs (Step 9 fixes it).

- [ ] **Step 9: Add the incremental-schema-repair mirror**

In `backend/app/database.py`, inside `_ensure_incremental_schema` (after the `if "agent_messages" in tables:` block, before the function's closing lines — match the existing `if "<table>" in tables: ... ALTER TABLE ... ADD COLUMN` style used for `arena_match`/`agent_threads` above it):

```python
    if "instruments" in tables:
        instrument_cols = {c["name"] for c in inspector.get_columns("instruments")}
        if "tags" not in instrument_cols:
            with active_engine.begin() as connection:
                connection.execute(
                    text("ALTER TABLE instruments ADD COLUMN tags JSON NOT NULL DEFAULT '[]'")
                )
```

- [ ] **Step 10: Run the full migration test file to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_migration_0042.py tests/test_instrument_models.py -v`
Expected: PASS (all tests)

- [ ] **Step 11: Commit**

```bash
git add backend/app/models.py backend/app/database.py backend/alembic/versions/0042_instrument_tags.py tests/test_migration_0042.py tests/test_instrument_models.py
git commit -m "feat(backend): add Instrument.tags column with underlying backfill migration"
```

---

### Task 2: Service layer — tag filter, set_instrument_tags, is_registered_underlying

**Files:**
- Modify: `backend/app/services/instruments.py`
- Modify: `backend/app/services/underlyings.py`
- Test: `tests/test_instruments_service.py`

**Interfaces:**
- Consumes: `Instrument.tags` (Task 1).
- Produces: `list_instruments(session, ..., tag: str | None = None)` (extends existing signature); `set_instrument_tags(session, instrument_id: int, tags: list[str]) -> Instrument` (raises `LookupError` if not found, `ValueError` if a tag is invalid); `is_registered_underlying(session, symbol: str) -> bool` (in `services/underlyings.py`, alongside `ensure_underlying`). Task 3 (API) and Task 5 (booking tools) call these directly.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_instruments_service.py` (check its existing imports/fixtures first and match them):

```python
def test_list_instruments_filters_by_tag(session):
    from app.models import Instrument
    from app.services.instruments import list_instruments

    tagged = Instrument(symbol="TAGFILT.SH", kind="index", status="active", tags=["underlying"])
    untagged = Instrument(symbol="NOTAG.SH", kind="index", status="active", tags=[])
    session.add_all([tagged, untagged])
    session.flush()

    rows = list_instruments(session, tag="underlying")
    symbols = {r.symbol for r in rows}
    assert "TAGFILT.SH" in symbols
    assert "NOTAG.SH" not in symbols


def test_list_instruments_tag_filter_applies_before_pagination(session):
    """A tagged row sorted past the unfiltered limit must still be returned —
    regression for filtering-after-SQL-pagination silently dropping rows."""
    from app.models import Instrument
    from app.services.instruments import list_instruments

    # Symbols sort alphabetically; put the tagged one last so a naive
    # SQL-then-filter implementation with a small limit would miss it.
    for i in range(5):
        session.add(Instrument(symbol=f"AAA{i}.SH", kind="index", status="active", tags=[]))
    session.add(Instrument(symbol="ZZZ_TAGGED.SH", kind="index", status="active", tags=["underlying"]))
    session.flush()

    rows = list_instruments(session, tag="underlying", limit=2)
    assert [r.symbol for r in rows] == ["ZZZ_TAGGED.SH"]


def test_set_instrument_tags_replaces_and_normalizes(session):
    from app.models import Instrument
    from app.services.instruments import set_instrument_tags

    row = Instrument(symbol="SETTAGS.SH", kind="index")
    session.add(row)
    session.flush()

    updated = set_instrument_tags(session, row.id, ["Underlying", " underlying ", "Hedge"])
    assert updated.tags == ["underlying", "hedge"]


def test_set_instrument_tags_raises_lookup_error_for_missing_instrument(session):
    from app.services.instruments import set_instrument_tags

    try:
        set_instrument_tags(session, 999999, ["underlying"])
        assert False, "expected LookupError"
    except LookupError:
        pass


def test_set_instrument_tags_rejects_non_string_tag(session):
    from app.models import Instrument
    from app.services.instruments import set_instrument_tags

    row = Instrument(symbol="BADTAG.SH", kind="index")
    session.add(row)
    session.flush()

    try:
        set_instrument_tags(session, row.id, [123])
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_is_registered_underlying(session):
    from app.models import Instrument
    from app.services.underlyings import is_registered_underlying

    tagged = Instrument(symbol="REG.SH", kind="index", tags=["underlying"])
    untagged = Instrument(symbol="UNREG.SH", kind="index", tags=[])
    session.add_all([tagged, untagged])
    session.flush()

    assert is_registered_underlying(session, "REG.SH") is True
    assert is_registered_underlying(session, "UNREG.SH") is False
    assert is_registered_underlying(session, "DOES_NOT_EXIST.SH") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_instruments_service.py -k "tag or registered" -v`
Expected: FAIL — `ImportError`/`TypeError` (functions don't exist / wrong signature yet).

- [ ] **Step 3: Implement `list_instruments(tag=...)` with pagination-correct filtering**

In `backend/app/services/instruments.py`, replace the existing `list_instruments` function:

```python
def list_instruments(
    session: Session,
    *,
    kind: str | None = None,
    status: str | None = None,
    parent_id: int | None = None,
    series_root: str | None = None,
    search: str | None = None,
    tag: str | None = None,
    limit: int = 1000,
    offset: int = 0,
) -> list[Instrument]:
    q = session.query(Instrument)
    if kind:
        q = q.filter(Instrument.kind == kind)
    if status:
        q = q.filter(Instrument.status == status)
    if parent_id is not None:
        q = q.filter(Instrument.parent_id == parent_id)
    if series_root:
        q = q.filter(Instrument.series_root == series_root)
    if search:
        like = f"%{search.strip()}%"
        q = q.filter(Instrument.symbol.ilike(like))
    q = q.order_by(Instrument.symbol.asc())
    if tag is None:
        return q.offset(offset).limit(limit).all()
    # Tag filtering happens in Python (JSON column, no portable SQL
    # containment query — matches list_portfolios(tags=...)). It MUST run
    # before offset/limit, or a tagged row sorted past the unfiltered page
    # would be silently dropped.
    wanted = tag.strip().lower()
    matched = [row for row in q.all() if wanted in {t.lower() for t in (row.tags or [])}]
    return matched[offset : offset + limit]
```

- [ ] **Step 4: Implement `set_instrument_tags`**

In `backend/app/services/instruments.py`, add near the bottom (after `list_instruments`):

```python
def _normalize_tags(tags: list[str]) -> list[str]:
    """Mirrors services/portfolio_service.py's _normalize_tags (private,
    duplicated rather than shared — it's a 10-line pure function, not worth a
    cross-module dependency for)."""
    seen: list[str] = []
    for t in tags or []:
        if not isinstance(t, str):
            raise ValueError(f"Tag must be a string, got {type(t).__name__}")
        s = t.strip().lower()
        if not s:
            continue
        if len(s) > 40:
            raise ValueError(f"Tag too long (>40 chars): {t!r}")
        if s not in seen:
            seen.append(s)
    return seen


def set_instrument_tags(session: Session, instrument_id: int, tags: list[str]) -> Instrument:
    row = session.get(Instrument, instrument_id)
    if row is None:
        raise LookupError(f"Instrument {instrument_id} not found")
    row.tags = _normalize_tags(tags)
    session.flush()
    return row
```

Update `__all__` at the top of the file to include `"set_instrument_tags"`.

- [ ] **Step 5: Implement `is_registered_underlying`**

In `backend/app/services/underlyings.py`, add after `update_underlying` (around line 195):

```python
def is_registered_underlying(session: Session, symbol: str) -> bool:
    """True when the symbol resolves to an Instrument tagged "underlying" —
    the gate book_position/book_hedge check before booking."""
    cleaned = normalize_underlying_symbol(symbol)
    if not cleaned:
        return False
    row = session.query(Underlying).filter(Underlying.symbol == cleaned).one_or_none()
    if row is None:
        return False
    return "underlying" in (row.tags or [])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_instruments_service.py -v`
Expected: PASS (all tests in the file, including pre-existing ones — full-file run to catch regressions from the `list_instruments` signature change)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/instruments.py backend/app/services/underlyings.py tests/test_instruments_service.py
git commit -m "feat(backend): add tag filter, set_instrument_tags, is_registered_underlying"
```

---

### Task 3: REST API — PUT tags endpoint, GET tag filter, InstrumentOut.tags

**Files:**
- Modify: `backend/app/schemas.py:1010-1082` (`InstrumentOut`, add `InstrumentTagsBody`)
- Modify: `backend/app/main.py` (imports, `list_instruments_endpoint`, new `PUT /api/instruments/{instrument_id}/tags`)
- Test: `tests/test_instruments_api.py`

**Interfaces:**
- Consumes: `set_instrument_tags`, `list_instruments(tag=...)` (Task 2).
- Produces: `GET /api/instruments?tag=underlying` (filtered `InstrumentOut[]`, now including `tags`); `PUT /api/instruments/{id}/tags` (body `{"tags": [...]}`, returns `InstrumentOut` with the new tags). Task 6/7 (frontend) consume these over HTTP.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_instruments_api.py` (check its existing client/fixture setup first and match it — likely a `client` fixture wrapping the FastAPI app, based on the file's ~850 existing lines):

```python
def test_get_instruments_filters_by_tag(client):
    create = client.post("/api/instruments", json={"symbol": "APITAG.SH", "kind": "index", "status": "active"})
    assert create.status_code == 201
    instrument_id = create.json()["id"]
    client.put(f"/api/instruments/{instrument_id}/tags", json={"tags": ["underlying"]})

    resp = client.get("/api/instruments", params={"tag": "underlying"})
    assert resp.status_code == 200
    symbols = {row["symbol"] for row in resp.json()}
    assert "APITAG.SH" in symbols


def test_put_instrument_tags_replaces_full_list(client):
    create = client.post("/api/instruments", json={"symbol": "APITAG2.SH", "kind": "index"})
    instrument_id = create.json()["id"]

    resp = client.put(f"/api/instruments/{instrument_id}/tags", json={"tags": ["underlying", "watchlist"]})
    assert resp.status_code == 200
    assert resp.json()["tags"] == ["underlying", "watchlist"]

    resp2 = client.put(f"/api/instruments/{instrument_id}/tags", json={"tags": ["underlying"]})
    assert resp2.status_code == 200
    assert resp2.json()["tags"] == ["underlying"]


def test_put_instrument_tags_404_for_missing_instrument(client):
    resp = client.put("/api/instruments/999999/tags", json={"tags": ["underlying"]})
    assert resp.status_code == 404


def test_instrument_out_includes_tags(client):
    create = client.post("/api/instruments", json={"symbol": "APITAG3.SH", "kind": "index"})
    assert create.json()["tags"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_instruments_api.py -k "tag" -v`
Expected: FAIL — `KeyError: 'tags'` and 404/405 on the PUT route (doesn't exist yet).

- [ ] **Step 3: Add `tags` to `InstrumentOut` and a new `InstrumentTagsBody` schema**

In `backend/app/schemas.py`, modify `InstrumentOut` (around line 1010-1036) to add one field:

```python
class InstrumentOut(BaseModel):
    id: int
    symbol: str
    display_name: str | None = None
    kind: str
    exchange: str | None = None
    currency: str
    status: str
    source: str
    akshare_symbol: str | None = None
    akshare_asset_class: str | None = None
    contract_code: str | None = None
    series_root: str | None = None
    expiry: date | None = None
    multiplier: float | None = None
    strike: float | None = None
    option_type: str | None = None
    parent_id: int | None = None
    loaded_at: datetime | None = None
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
```

Add a new schema right after `InstrumentSyncResultOut` (around line 1084-1088):

```python
class InstrumentTagsBody(BaseModel):
    tags: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Wire the `tag` query param and the PUT endpoint in main.py**

In `backend/app/main.py`, update the import block (around line 241-246):

```python
from .services.instruments import (
    list_instruments,
    resolvable_market_data_instruments,
    set_instrument_tags,
    sync_instruments_from_positions,
    validate_instrument_terms,
)
```

Add `InstrumentTagsBody` to the existing schemas import (find the line importing `InstrumentOut`, `InstrumentUpdate`, `InstrumentCreate` from `.schemas` and add `InstrumentTagsBody` alongside them).

Update `list_instruments_endpoint` (around line 1934-1955):

```python
    @app.get("/api/instruments", response_model=list[InstrumentOut])
    def list_instruments_endpoint(
        kind: str | None = None,
        status: str | None = None,
        parent_id: int | None = None,
        series_root: str | None = None,
        search: str | None = None,
        tag: str | None = None,
        limit: int = 1000,
        offset: int = 0,
        session: Session = Depends(get_db),
    ):
        rows = list_instruments(
            session,
            kind=kind,
            status=status,
            parent_id=parent_id,
            series_root=series_root,
            search=search,
            tag=tag,
            limit=limit,
            offset=offset,
        )
        return rows
```

Add a new endpoint right after `patch_instrument_endpoint` (find its closing `return row` around line 2050-2060 and add this immediately after):

```python
    @app.put("/api/instruments/{instrument_id}/tags", response_model=InstrumentOut)
    def put_instrument_tags(
        instrument_id: int,
        payload: InstrumentTagsBody,
        session: Session = Depends(get_db),
    ):
        try:
            row = set_instrument_tags(session, instrument_id, payload.tags)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        record_audit(
            session,
            event_type="instrument.tags_changed",
            actor="desk_user",
            subject_type="instrument",
            subject_id=row.id,
            payload={"tags": row.tags},
        )
        session.commit()
        session.refresh(row)
        return row
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_instruments_api.py -v`
Expected: PASS (full file — a schema change to `InstrumentOut` and a signature change to `list_instruments_endpoint` can affect other existing tests in this file)

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/main.py tests/test_instruments_api.py
git commit -m "feat(backend): add PUT /api/instruments/{id}/tags and GET tag filter"
```

---

### Task 4: `register_underlying` agent tool + HITL wiring

**Files:**
- Create: `backend/app/tools/underlyings.py`
- Modify: `backend/app/tools/__init__.py`
- Modify: `backend/app/services/deep_agent/hitl.py`
- Modify: `backend/app/services/agents.py:366-471` (`DEEP_AGENT_TOOL_NAMES`)
- Modify: `backend/app/skills/meta/yolo-hitl-policy.md`
- Test: `tests/test_hitl.py`
- Test: new `tests/test_register_underlying_tool.py`

**Interfaces:**
- Consumes: `ensure_underlying` (existing, `services/underlyings.py`).
- Produces: `register_underlying_tool` (LangChain `@tool`, name `"register_underlying"`, input `{symbol: str}`, returns `{"ok": True, "data": {"symbol", "instrument_id", "action": "created_new"|"tagged_existing"|"already_registered", "kind", "currency", "status", "tags"}}`). Task 5 (`book_position`/`book_hedge` docstrings) reference this tool's name in their retry instructions.

- [ ] **Step 1: Write the failing tool test**

Create `tests/test_register_underlying_tool.py`:

Verified against the real fixture convention used by `tests/test_tools_positions.py`
(the existing `book_position_tool` test file — an autouse `_db` fixture points
`app.database` at a temp-file SQLite DB via `configure_database()`; the tool's
own internal `database.SessionLocal()` calls then transparently hit that same
temp DB, no monkeypatching of `SessionLocal`/`init_db` needed):

```python
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import AuditEvent, Instrument


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def test_register_underlying_creates_new_instrument():
    from app.tools.underlyings import register_underlying_tool

    result = register_underlying_tool.invoke({"symbol": "NEWUL.SH"})
    assert result["ok"] is True
    assert result["data"]["action"] == "created_new"
    assert result["data"]["symbol"] == "NEWUL.SH"

    with database.SessionLocal() as session:
        row = session.query(Instrument).filter_by(symbol="NEWUL.SH").one()
        assert "underlying" in row.tags
        assert row.status == "active"

        audit = session.query(AuditEvent).filter_by(
            event_type="instrument.underlying_registered", subject_id=str(row.id)
        ).one()
        assert audit.payload["action"] == "created_new"
        assert audit.payload["symbol"] == "NEWUL.SH"


def test_register_underlying_tags_existing_instrument():
    from app.tools.underlyings import register_underlying_tool

    with database.SessionLocal() as session:
        session.add(Instrument(symbol="EXIST.SH", kind="index", status="draft", tags=[]))
        session.commit()

    result = register_underlying_tool.invoke({"symbol": "EXIST.SH"})
    assert result["ok"] is True
    assert result["data"]["action"] == "tagged_existing"

    with database.SessionLocal() as session:
        row = session.query(Instrument).filter_by(symbol="EXIST.SH").one()
        assert "underlying" in row.tags
        assert row.status == "active"

        audit = session.query(AuditEvent).filter_by(
            event_type="instrument.underlying_registered", subject_id=str(row.id)
        ).one()
        assert audit.payload["action"] == "tagged_existing"


def test_register_underlying_already_registered_is_a_noop():
    from app.tools.underlyings import register_underlying_tool

    with database.SessionLocal() as session:
        session.add(Instrument(symbol="ALREADY.SH", kind="index", status="active", tags=["underlying"]))
        session.commit()

    result = register_underlying_tool.invoke({"symbol": "ALREADY.SH"})
    assert result["ok"] is True
    assert result["data"]["action"] == "already_registered"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_register_underlying_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.tools.underlyings'`

- [ ] **Step 3: Write the tool**

Create `backend/app/tools/underlyings.py`:

```python
"""@tool wrapper: create-or-tag an instrument as a valid underlying."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app import database
from app.models import Instrument
from app.services.audit import record_audit
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.underlyings import ensure_underlying, normalize_underlying_symbol


class RegisterUnderlyingInput(BaseModel):
    symbol: str = Field(min_length=1)


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("register_underlying", args_schema=RegisterUnderlyingInput)
def register_underlying_tool(symbol: str) -> dict[str, Any]:
    """Create-or-tag an instrument as a valid underlying (adds the
    "underlying" tag; creates the instrument via the existing symbol-
    inference path if it doesn't exist yet, activating it). Call this when
    book_position/book_hedge return error=underlying_not_registered, then
    retry the booking call. HITL — requires confirmation except in yolo mode.
    """
    cleaned = normalize_underlying_symbol(symbol)
    database.init_db()
    with database.SessionLocal() as session:
        existing = session.query(Instrument).filter(Instrument.symbol == cleaned).one_or_none()
        if existing is None:
            instrument = ensure_underlying(session, cleaned, source="agent", status="active", activate=True)
            action = "created_new"
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
        session.flush()
        # This tool is risk_level="irreversible" and can auto-run headlessly
        # under yolo mode with no human in the loop — a durable, searchable
        # audit trail is the only record of who/what changed the registry.
        record_audit(
            session,
            event_type="instrument.underlying_registered",
            actor="agent",
            subject_type="instrument",
            subject_id=instrument.id,
            payload={"symbol": instrument.symbol, "action": action, "tags": instrument.tags},
        )
        session.commit()
        return {
            "ok": True,
            "data": {
                "symbol": instrument.symbol,
                "instrument_id": instrument.id,
                "action": action,
                "kind": instrument.kind,
                "currency": instrument.currency,
                "status": instrument.status,
                "tags": instrument.tags,
            },
        }


__all__ = ["register_underlying_tool"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_register_underlying_tool.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing HITL wiring tests**

In `tests/test_hitl.py`, modify `test_interrupt_tool_names_covers_all_state_mutating_tools` to add `"register_underlying"` to the expected set (add it as a new line inside the set literal, anywhere — e.g. right after `"book_hedge",`).

Modify `test_yolo_mode_uses_langchain_auto_approval_for_write_tools` to add `"register_underlying"` to the tuple of tools expected to stay gated under `yolo_mode=True` (the `for tool_name in (...)` loop around line 112-121 — add `"register_underlying"` as a new entry alongside `"book_hedge"`).

Add two new tests at the end of `tests/test_hitl.py`:

```python
def test_register_underlying_is_irreversible_risk_not_write():
    from app.services.deep_agent.hitl import _RISK_LEVEL_BY_TOOL

    # "write" risk bypasses confirmation under BOTH auto and yolo mode
    # (interrupt_on_config's yolo_mode flag) -- that would let auto mode
    # silently persist an unvetted underlying, contradicting the requirement
    # that only yolo auto-adds. "irreversible" stays gated under auto.
    assert _RISK_LEVEL_BY_TOOL["register_underlying"] == "irreversible"


def test_summarize_register_underlying_distinguishes_create_vs_tag(session):
    """Uses the shared `session` fixture from tests/conftest.py:63, which
    already calls database.configure_database()+init_db() against a temp
    SQLite file — _summarize_register_underlying's own internal
    database.SessionLocal() call transparently hits that same temp DB, no
    monkeypatching needed (this file otherwise has no DB fixtures, unlike
    test_tools_positions.py's autouse `_db`, so `session` must be requested
    as an explicit test parameter here to trigger that configuration)."""
    from app.models import Instrument
    from app.services.deep_agent import hitl

    new_summary = hitl._summarize_register_underlying({"symbol": "BRANDNEW.SH"})
    assert "NEW" in new_summary
    assert "BRANDNEW.SH" in new_summary

    session.add(Instrument(symbol="UNTAGGED.SH", kind="index", status="draft", tags=[]))
    session.commit()
    tag_summary = hitl._summarize_register_underlying({"symbol": "UNTAGGED.SH"})
    assert "UNTAGGED.SH" in tag_summary
    assert "underlying" in tag_summary.lower()
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_hitl.py -v`
Expected: FAIL — set-equality mismatch, `KeyError: 'register_underlying'`, `AttributeError: module has no attribute '_summarize_register_underlying'`.

- [ ] **Step 7: Wire risk level, label, summary builder, and INTERRUPT_TOOL_NAMES**

In `backend/app/services/deep_agent/hitl.py`:

Add `"register_underlying"` to the `INTERRUPT_TOOL_NAMES` tuple (around line 23-59, right after `"book_hedge",`):

```python
    "book_hedge",
    "register_underlying",
```

Add to `_RISK_LEVEL_BY_TOOL` (around line 62-104, right after the `"book_hedge": "irreversible",` line):

```python
    "book_hedge": "irreversible",
    "register_underlying": "irreversible",
```

Add to `_LABEL_BY_TOOL` (around line 107-143, right after the `"book_hedge": "Book hedge",` line):

```python
    "book_hedge": "Book hedge",
    "register_underlying": "Register/tag underlying",
```

Add the summary builder right after `_summarize_book_position` (around line 204-229, before `_SUMMARY_BUILDERS`):

```python
def _summarize_register_underlying(args: dict[str, Any]) -> str:
    """Preflight-aware: LangGraph's interrupt fires before the tool body
    runs, so without this the card could only show the raw symbol. Opens its
    own short-lived read-only session (self-contained in this module, no
    signature change needed on pending_actions_from_interrupts/_summary_for
    or their 5 call sites in agents.py)."""
    symbol = args.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        return "Register underlying"
    symbol = symbol.strip()

    from app import database
    from app.models import Instrument
    from app.services.underlyings import akshare_asset_class, infer_currency, infer_market

    try:
        database.init_db()
        with database.SessionLocal() as session:
            row = session.query(Instrument).filter(Instrument.symbol == symbol).one_or_none()
            if row is None:
                return (
                    f"Register NEW underlying {symbol} — inferred kind="
                    f"{akshare_asset_class(symbol)}, currency={infer_currency(symbol)}, "
                    f"market={infer_market(symbol) or 'n/a'}"
                )
            if "underlying" not in (row.tags or []):
                return (
                    f"Add 'underlying' tag to existing instrument {symbol} "
                    f"(kind={row.kind}, status={row.status})"
                )
            return f"Register underlying {symbol} (already valid)"
    except Exception:
        # Card rendering must never 500 the turn over a preview lookup.
        return f"Register underlying {symbol}"
```

Register it in `_SUMMARY_BUILDERS` (around line 231-233):

```python
_SUMMARY_BUILDERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "book_position": _summarize_book_position,
    "register_underlying": _summarize_register_underlying,
}
```

- [ ] **Step 8: Bind the tool — QUANT_AGENT_TOOLS and DEEP_AGENT_TOOL_NAMES**

In `backend/app/tools/__init__.py`, add the import (near the `.hedging` import block, around line 104-110):

```python
from .hedging import (
    book_hedge_tool,
    get_hedge_bands_tool,
    get_hedgeable_underlyings_tool,
    propose_hedge_tool,
    set_hedge_bands_tool,
)
from .underlyings import register_underlying_tool
```

Add it to `QUANT_AGENT_TOOLS` (around line 202-203, in the "hedging writes" comment block):

```python
    # hedging writes (persisted / HITL-gated):
    book_hedge_tool,
    set_hedge_bands_tool,
    register_underlying_tool,
```

In `backend/app/services/agents.py`, add `"register_underlying"` to `DEEP_AGENT_TOOL_NAMES` (around line 384-388, in the hedging-workflow comment block — this is the CLAUDE.md-documented gotcha: a tool the model must call has to be in this frozenset, not merely registered in `QUANT_AGENT_TOOLS`, or `select_deep_agent_tools()` silently drops it):

```python
        "get_hedgeable_underlyings",
        "propose_hedge",
        "get_hedge_bands",
        "book_hedge",
        "set_hedge_bands",
        "register_underlying",
```

- [ ] **Step 9: Update the yolo-hitl-policy skill doc**

In `backend/app/skills/meta/yolo-hitl-policy.md`, this doc's own maintenance rule says "This list mirrors `INTERRUPT_TOOL_NAMES`... If a new entry lands there, add it here too." Add `register_underlying` to the persisted-tools list in the "Batch-size-1 HITL rule" paragraph, right after `book_rfq_to_position`:

```markdown
`run_batch_pricing`, `create_report`,
`create_or_update_rfq_draft`, `quote_rfq`, `submit_rfq_for_approval`,
`approve_rfq`, `reject_rfq`, `release_rfq`, `mark_rfq_client_accepted`,
`book_rfq_to_position`, `register_underlying`, `import_otc_positions`,
```

- [ ] **Step 10: Run all HITL and tool-binding tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_hitl.py tests/test_register_underlying_tool.py -v`
Expected: PASS (all tests, including `test_interrupt_tools_are_bound_deep_agent_tools` which will now check `register_underlying` is bound)

- [ ] **Step 11: Commit**

```bash
git add backend/app/tools/underlyings.py backend/app/tools/__init__.py backend/app/services/deep_agent/hitl.py backend/app/services/agents.py backend/app/skills/meta/yolo-hitl-policy.md tests/test_hitl.py tests/test_register_underlying_tool.py
git commit -m "feat(backend): add register_underlying agent tool with HITL gating"
```

---

### Task 5: `book_position` / `book_hedge` validation gate

**Files:**
- Modify: `backend/app/tools/positions.py:599-669` (`book_position_tool`)
- Modify: `backend/app/tools/hedging.py:90-101` (`book_hedge_tool`)
- Test: `tests/test_tools_positions.py` (existing `book_position_tool` test file — verified; uses an autouse `_db(tmp_path, monkeypatch)` fixture, no `session`/monkeypatch needed inside individual tests)
- Test: `tests/test_hedging_tools.py` (existing `book_hedge_tool`-adjacent test file — verified; uses the shared `session` fixture from `tests/conftest.py` directly, no monkeypatching)

**Interfaces:**
- Consumes: `is_registered_underlying` (Task 2).
- Produces: both tools now return `{"ok": False, "error": "underlying_not_registered", "detail": {"symbol": ...}}` instead of booking, when the underlying isn't tagged.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_tools_positions.py`, matching its existing `_db` autouse fixture (no per-test `session`/monkeypatch — `book_position_tool`'s internal `database.SessionLocal()` calls already hit the fixture-configured temp DB):

```python
def test_book_position_rejects_unregistered_underlying():
    result = book_position_tool.invoke({
        "portfolio_id": _make_portfolio(),  # this file's existing helper (see _make_portfolio above)
        "product": {
            "product_family": "spot",
            "quantark_class": "Spot",
            "underlying": "UNREGISTERED_SYMBOL.SH",
            "terms": {},
        },
        "quantity": 1,
    })
    assert result["ok"] is False
    assert result["error"] == "underlying_not_registered"
    assert result["detail"]["symbol"] == "UNREGISTERED_SYMBOL.SH"
```

(`book_position_tool` is already imported at the top of this file — see its existing import block.)

Add to `tests/test_hedging_tools.py`, matching its existing plain `session`-fixture pattern (add `book_hedge_tool` to the existing `from app.tools.hedging import (...)` import line at the top of the file):

```python
def test_book_hedge_rejects_unregistered_underlying(session):
    pf = _seed(session)  # this file's existing helper (see _seed above) — seeds
                          # 000905.SH WITHOUT the "underlying" tag, since _seed
                          # predates this feature; that's exactly the fixture we want
    result = book_hedge_tool.invoke({
        "portfolio_id": pf.id,
        "underlying": "000905.SH",
        "risk_run_id": 1,
        "strategy": "delta_neutral",
        "spot": 5600.0,
        "legs": [],
    })
    assert result["ok"] is False
    assert result["error"] == "underlying_not_registered"
    assert result["detail"]["symbol"] == "000905.SH"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_tools_positions.py -k unregistered tests/test_hedging_tools.py -k unregistered -v`
Expected: FAIL — either an exception from the domain service trying to book against a nonexistent/unvetted underlying, or (worse) it silently succeeds by auto-vivifying the instrument (this is the exact bug the feature fixes — confirm the test fails for the *right* reason before proceeding).

- [ ] **Step 3: Add the gate to `book_position_tool`**

In `backend/app/tools/positions.py`, add the import (extend the existing `from app.services.underlyings import resolve_underlying_currency` line, around line 25):

```python
from app.services.underlyings import is_registered_underlying, resolve_underlying_currency
```

Modify the tail of `book_position_tool` (around line 653-669) — insert the check right after opening the session, before calling `booking_svc.book_position`:

```python
    database.init_db()
    with database.SessionLocal() as session:
        if not is_registered_underlying(session, product.underlying):
            return {
                "ok": False,
                "error": "underlying_not_registered",
                "detail": {"symbol": product.underlying},
            }
        position = booking_svc.book_position(
            session,
            request,
            reuse_product=True,
        )
        row = shape_position(position, include_raw_terms=False)
        row["product_id"] = position.product_id
        product_row = products_svc.product_summary(position.product)
        session.commit()
    return {
        "ok": True,
        "source": "product_booking",
        "position": row,
        "product": product_row,
    }
```

Update the docstring right above `def book_position_tool(` (around line 599-600):

```python
    """Create a normalized product and book a position against it. If this
    returns error=underlying_not_registered, call register_underlying(symbol)
    then retry."""
```

- [ ] **Step 4: Add the gate to `book_hedge_tool`**

In `backend/app/tools/hedging.py`, add the import (around line 11-15):

```python
from app.services.underlyings import is_registered_underlying
```

Modify `book_hedge_tool` (around line 90-101):

```python
@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("book_hedge", args_schema=BookHedgeInput)
def book_hedge_tool(portfolio_id: int, underlying: str, risk_run_id: int,
                    strategy: str, spot: float, legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Atomically book hedge legs into the portfolio, hedge-tagged (is_hedge,
    risk_run_id, strategy, leg_role) and visible on the Hedging page. HITL —
    requires confirmation. Never book hedge legs via book_position. If this
    returns error=underlying_not_registered, call register_underlying(symbol)
    then retry."""
    with database.SessionLocal() as session:
        if not is_registered_underlying(session, underlying):
            return {
                "ok": False,
                "error": "underlying_not_registered",
                "detail": {"symbol": underlying},
            }
        out = hs.book_hedge(session, portfolio_id=portfolio_id, underlying=underlying,
                            risk_run_id=risk_run_id, strategy=strategy, legs=legs,
                            spot=spot, actor="agent")
        session.commit()
        return out
```

- [ ] **Step 5: Fix the two existing `test_tools_positions.py` tests the new gate breaks**

Run the full file first to see the breakage: `.venv/bin/python -m pytest tests/test_tools_positions.py -v`. Two pre-existing tests book against underlyings that predate this feature and were never tagged, so they'll now fail with `underlying_not_registered`: `test_book_position_tool_creates_product_and_position` (books `"000300.SH"`, line ~331) and the parametrized `test_book_position_tool_defaults_currency_to_underlying` (books `"AAPL"` and `"000300.SH"`, lines ~388-390).

Add a small helper near the top of `tests/test_tools_positions.py`, right after `_make_portfolio` (around line 44-48):

```python
def _tag_underlying(symbol: str) -> None:
    from app.models import Instrument

    with database.SessionLocal() as session:
        row = session.query(Instrument).filter_by(symbol=symbol).one_or_none()
        if row is None:
            row = Instrument(symbol=symbol, kind="index")
            session.add(row)
        row.tags = list({*(row.tags or []), "underlying"})
        session.commit()
```

Call it at the top of `test_book_position_tool_creates_product_and_position` (right after `pid = _make_portfolio()`, line ~319):

```python
    pid = _make_portfolio()
    _tag_underlying("000300.SH")
```

Call it at the top of `test_book_position_tool_defaults_currency_to_underlying` (right after `pid = _make_portfolio()`, line ~397 — this one is parametrized, so tag the specific `underlying` value the test received):

```python
    pid = _make_portfolio()
    _tag_underlying(underlying)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_tools_positions.py tests/test_hedging_tools.py -v`
Expected: PASS (full files — confirm no *other* existing test in either file was relying on an untagged underlying auto-booking successfully; if one surfaces, apply the same `_tag_underlying`/inline-tag fix rather than weakening the gate)

- [ ] **Step 7: Commit**

```bash
git add backend/app/tools/positions.py backend/app/tools/hedging.py tests/test_tools_positions.py tests/test_hedging_tools.py
git commit -m "feat(backend): gate book_position/book_hedge on underlying tag validation"
```

---

### Task 6: Frontend — Instruments admin page tag editor

**Files:**
- Modify: `frontend/src/types.ts:883-908` (`Instrument` type)
- Modify: `frontend/src/routes/Instruments.tsx`
- Modify: `frontend/src/routes/Instruments.live.tsx`
- Test: `frontend/src/routes/Instruments.test.tsx`

**Interfaces:**
- Consumes: `PUT /api/instruments/{id}/tags` (Task 3), existing `TagEditor` component (`frontend/src/components/TagEditor.tsx` — reused as-is, no changes).
- Produces: `onSetInstrumentTags: (id: number, tags: string[]) => Promise<void>` prop threaded through `Instruments` → `RegistryTab`.

- [ ] **Step 1: Add `tags` to the `Instrument` type**

In `frontend/src/types.ts`, modify the `Instrument` type (around line 883-908):

```typescript
export type Instrument = {
  id: number;
  symbol: string;
  display_name: string | null;
  kind: string;
  exchange: string | null;
  currency: string;
  status: string;
  source: string;
  akshare_symbol: string | null;
  akshare_asset_class: string | null;
  contract_code: string | null;
  series_root: string | null;
  expiry: string | null;
  multiplier: number | null;
  strike: number | null;
  option_type: string | null;
  parent_id: number | null;
  loaded_at: string | null;
  rate: number | null;
  dividend_yield: number | null;
  volatility: number | null;
  notes: string | null;
  tags: string[];
  created_at: string;
  updated_at: string;
};
```

Making `tags` required (not `tags?: string[]`) means every existing object literal explicitly typed as `Instrument` across the frontend test suite now fails to compile until it includes a `tags` value. Three verified call sites need a one-line addition — do these now, in this step, since Step 3 (component test) and later Task 7 depend on a green `tsc --noEmit` baseline first:

- `frontend/src/routes/Instruments.test.tsx:13-38`, the `instrument(overrides)` factory — add `tags: [],` right after the `notes: null,` line (around line 36), so it still returns a valid `Instrument` by default.
- `frontend/src/routes/Booking.live.test.tsx:47-72`, the `activeUnderlying` object literal — add `tags: ['underlying'],` right after `notes: null,` (around line 69). `secondActiveUnderlying` (line 74, `{ ...activeUnderlying, ... }`) inherits it via spread, no separate edit needed.
- `frontend/src/routes/TrySolve.live.test.tsx:605-631`, the `underlying(overrides)` factory — add `tags: ['underlying'],` right after `notes: null,` (around line 628).

Run `cd frontend && npx tsc --noEmit` now to confirm these three are the only breakages from the type change (a 4th call site may exist that grep missed — if `tsc` reports another, fix it the same way: add a `tags` value matching whether that fixture represents a tagged-underlying or not).

- [ ] **Step 2: Write the failing component test**

Check `frontend/src/routes/Instruments.test.tsx`'s existing test setup (row fixtures, render helpers) first, then add a test matching its conventions:

```typescript
it('renders a TAGS column with an editable tag list per row', async () => {
  const onSetInstrumentTags = vi.fn().mockResolvedValue(undefined);
  // Use this test file's existing row-fixture/render helper, passing
  // onSetInstrumentTags among the other required props, and a row with
  // tags: ['underlying'].
  render(<Instruments {...defaultProps} onSetInstrumentTags={onSetInstrumentTags} />);

  expect(screen.getByText('underlying')).toBeInTheDocument();
});
```

Adjust the exact assertions/setup to match this file's existing patterns (e.g. if it uses a `buildProps()` helper or per-test fixture rows, extend those rather than introducing a parallel convention).

- [ ] **Step 3: Run test to verify it fails**

Run: `cd frontend && npx vitest run Instruments.test.tsx -t "TAGS column"`
Expected: FAIL — `onSetInstrumentTags` prop doesn't exist yet / no TAGS column rendered.

- [ ] **Step 4: Add the `onSetInstrumentTags` prop and TAGS column**

In `frontend/src/routes/Instruments.tsx`:

Import `TagEditor` near the top (alongside other component imports):

```typescript
import { TagEditor } from '../components/TagEditor';
```

Add to `Props` type (around line 97, right after `onSaveInstrument`):

```typescript
  onSaveInstrument: (id: number, fields: Partial<Instrument>) => Promise<void>;
  onSetInstrumentTags: (id: number, tags: string[]) => Promise<void>;
```

Thread it through `RegistryTab`'s prop signature (around line 298-306):

```typescript
function RegistryTab({
  rows,
  pagedRows,
  loading,
  onSaveInstrument,
  onSetInstrumentTags,
  rolesByInstrumentId,
}: Pick<Props, 'rows' | 'loading' | 'onSaveInstrument' | 'onSetInstrumentTags' | 'rolesByInstrumentId'> & {
  pagedRows: Instrument[];
}) {
```

Add the `TAGS` column header (around line 349-359, right after `<th>ROLES</th>`):

```typescript
              <tr>
                <th>SYMBOL</th>
                <th>KIND</th>
                <th>PARENT</th>
                <th>STATUS</th>
                <th>ROLES</th>
                <th>TAGS</th>
                <th>TERMS</th>
                <th>AKSHARE</th>
                <th>NOTES</th>
                <th>ACTIONS</th>
              </tr>
```

Add the `TAGS` cell (right after the `{/* ROLES */}` cell, around line 445-448):

```typescript
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

Find the call site where `RegistryTab` is rendered (around line 1057-1063) and pass the new prop through:

```typescript
      {activeTab === 'registry' ? (
        <RegistryTab
          rows={rows}
          pagedRows={registryPagination.pagedRows}
          loading={loading}
          onSaveInstrument={onSaveInstrument}
          onSetInstrumentTags={onSetInstrumentTags}
          rolesByInstrumentId={rolesByInstrumentId}
        />
```

Destructure `onSetInstrumentTags` from the top-level `Instruments` component's props (find where `onSaveInstrument` is destructured near the top of the component function and add it alongside).

- [ ] **Step 5: Wire the live data layer**

In `frontend/src/routes/Instruments.live.tsx`, add the handler right after `onSaveInstrument` (around line 588-597):

```typescript
  const onSaveInstrument = async (id: number, fields: Partial<Instrument>) => {
    const updated = await api<Instrument>(`/api/instruments/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(fields),
    });
    if (!cancelledRef.current) {
      setRows((current) => current.map((row) => (row.id === id ? updated : row)));
      setFeedback({ tone: 'success', message: `Saved ${updated.symbol}.` });
    }
  };

  const onSetInstrumentTags = async (id: number, tags: string[]) => {
    const updated = await api<Instrument>(`/api/instruments/${id}/tags`, {
      method: 'PUT',
      body: JSON.stringify({ tags }),
    });
    if (!cancelledRef.current) {
      setRows((current) => current.map((row) => (row.id === id ? updated : row)));
    }
  };
```

Pass it to `<Instruments>` (around line 607-629, alongside `onSaveInstrument={onSaveInstrument}`):

```typescript
      onSaveInstrument={onSaveInstrument}
      onSetInstrumentTags={onSetInstrumentTags}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd frontend && npx vitest run Instruments.test.tsx`
Expected: PASS (full file — a new required prop can break other existing tests in this file that render `<Instruments>` without it; add `onSetInstrumentTags: vi.fn()` to this file's shared default-props fixture if one exists)

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors

- [ ] **Step 7: Verify visually in both themes**

Per `frontend/CLAUDE.md`: start the dev server, open the Instruments page, confirm the TAGS column renders correctly and the chip editor is usable in both light/dark theme and compact density (the `TagEditor` component/CSS is reused unchanged from Portfolios, so this is a smoke check, not new styling work).

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/routes/Instruments.tsx frontend/src/routes/Instruments.live.tsx frontend/src/routes/Instruments.test.tsx
git commit -m "feat(frontend): add TAGS column with TagEditor to Instruments admin page"
```

---

### Task 7: Frontend — Booking/TrySolve picker server-filtered fetch

**Files:**
- Modify: `frontend/src/routes/Booking.live.tsx:157-161,665-669`
- Modify: `frontend/src/routes/TrySolve.live.tsx:53,82`
- Modify: `frontend/src/routes/TrySolve.tsx:930` (simplify redundant filter — see Interfaces note)
- Modify: `frontend/src/routes/Booking.live.test.tsx` (verified: 16 occurrences of the exact-match string `url === '/api/instruments'` in its per-test `fetchMock` bodies)
- Modify: `frontend/src/routes/TrySolve.live.test.tsx` (verified: 4 occurrences of the same pattern)

**Interfaces:**
- Consumes: `GET /api/instruments?status=active&tag=underlying` (Task 3).
- Produces: no new exports — this task only changes what URL Booking/TrySolve fetch and removes now-redundant client-side filtering. `TrySolve.tsx:930`'s `activeUnderlyings` variable is kept (renaming it would touch every downstream usage in that file for no behavioral benefit) but its filter predicate becomes a passthrough, since the fetch is now pre-scoped.

**Important — verified test-breakage scope**: both `Booking.live.test.tsx` and `TrySolve.live.test.tsx` mock `globalThis.fetch` per-test with a function body that does exact string matching, e.g. (`Booking.live.test.tsx:114`):
```typescript
if (url === '/api/instruments' && !init?.method) return response([activeUnderlying]);
```
Changing the real fetch call's URL (Step 3/4 below) means every one of these 20 exact-match branches (16 in Booking.live.test.tsx, 4 in TrySolve.live.test.tsx) stops matching and throws `Unexpected request: /api/instruments?status=active&tag=underlying` inside the mock. This is a mechanical, uniform find-and-replace within each file, not 20 separate design decisions.

- [ ] **Step 1: Confirm current test behavior (baseline)**

Run: `cd frontend && npx vitest run Booking.live.test.tsx TrySolve.live.test.tsx`
Expected: PASS (establishes the baseline before the breaking change, so Step 5's re-run has something to diff against)

- [ ] **Step 2: Update Booking.live.tsx's fetch**

In `frontend/src/routes/Booking.live.tsx`, modify the `Promise.all` (around line 157-161):

```typescript
    Promise.all([
      api<Portfolio[]>('/api/portfolios'),
      api<MarketDataProfile[]>('/api/market-data/profiles'),
      api<Instrument[]>('/api/instruments?status=active&tag=underlying'),
    ])
```

Simplify `activeUnderlyingSymbols` (around line 665-669) since the fetch is now pre-scoped:

```typescript
function activeUnderlyingSymbols(rows: Instrument[]): string[] {
  return rows.map((underlying) => underlying.symbol);
}
```

- [ ] **Step 3: Update TrySolve.live.tsx's fetch**

In `frontend/src/routes/TrySolve.live.tsx`, modify the fetch call (around line 82):

```typescript
      api<Instrument[]>('/api/instruments?status=active&tag=underlying'),
```

- [ ] **Step 4: Simplify the redundant filter in TrySolve.tsx**

In `frontend/src/routes/TrySolve.tsx`, modify line 930 — the data now arrives pre-filtered, so the local `status === 'active'` check is redundant:

```typescript
    const activeUnderlyings = underlyings;
```

(Leave the variable name and every downstream usage of `activeUnderlyings` in this component untouched — only the right-hand side changes, from a filter expression to a passthrough.)

- [ ] **Step 5: Run tests to see the expected breakage**

Run: `cd frontend && npx vitest run Booking.live.test.tsx TrySolve.live.test.tsx`
Expected: FAIL — every test whose `fetchMock` body matches `/api/instruments` exactly now throws `Unexpected request: /api/instruments?status=active&tag=underlying` (16 sites in the first file, 4 in the second, per the note above Step 1). This is the expected, mechanical breakage this step's fix addresses next — not a design problem.

- [ ] **Step 6: Update the exact-match URL string in both test files**

In `frontend/src/routes/Booking.live.test.tsx`, every occurrence of the literal string:

```typescript
if (url === '/api/instruments' && !init?.method) return response(...);
```

becomes:

```typescript
if (url === '/api/instruments?status=active&tag=underlying' && !init?.method) return response(...);
```

This is a uniform substring replacement — every one of the 16 occurrences has the identical `'/api/instruments'` literal (only the `response(...)` payload varies per test), so `url === '/api/instruments'` → `url === '/api/instruments?status=active&tag=underlying'` is the same edit repeated, not 16 distinct judgment calls. Apply the identical substitution to the 4 occurrences in `frontend/src/routes/TrySolve.live.test.tsx`.

The `activeUnderlying`/`underlying()` fixtures in these two files already got `tags: ['underlying']` in Task 6 Step 1 (required there to keep `tsc --noEmit` green). No further fixture changes needed here — this step is only the URL-string substitution.

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd frontend && npx vitest run Booking.live.test.tsx TrySolve.live.test.tsx TrySolve.test.tsx`
Expected: PASS

Run: `cd frontend && npx tsc --noEmit`
Expected: no new type errors

- [ ] **Step 8: Verify visually**

Start the dev server, open Booking and Try to Solve, confirm the underlying picker only lists instruments tagged `underlying` (cross-check against the Instruments admin page from Task 6 — an instrument with an empty tags chip list should NOT appear in either picker).

- [ ] **Step 9: Commit**

```bash
git add frontend/src/routes/Booking.live.tsx frontend/src/routes/TrySolve.live.tsx frontend/src/routes/TrySolve.tsx frontend/src/routes/Booking.live.test.tsx frontend/src/routes/TrySolve.live.test.tsx
git commit -m "feat(frontend): fetch Booking/TrySolve underlying pickers server-filtered by tag"
```

---

## Self-Review Notes

**Spec coverage:**
- §1 Data model & migration → Task 1 (column + migration + both backfill clauses + incremental-schema mirror, which the spec didn't call out explicitly but is required by this codebase's own documented `create_all()` boot pattern).
- §2 Backend API → Task 3 (PUT tags, GET tag filter, pagination-before-filter ordering).
- §3 Frontend (tag editor, tags vs. roles, picker fetch) → Task 6 (admin editor) + Task 7 (picker fetch).
- §4 `register_underlying` tool (behavior, gating, approval-card preflight) → Task 4.
- §5 `book_position`/`book_hedge` validation → Task 5.
- Declined-recommendation notes (ETag concurrency, join-table rewrite) → intentionally have no task; nothing to implement.

**Type/name consistency check:** `is_registered_underlying` (Task 2) is the exact name Task 5 imports; `set_instrument_tags` (Task 2) is the exact name Task 3's endpoint calls; `register_underlying_tool`/`"register_underlying"` (Task 4) is the exact name Task 5's docstrings reference and Task 4's own HITL/binding steps use; `onSetInstrumentTags` (Task 6) is threaded with one consistent signature (`(id: number, tags: string[]) => Promise<void>`) from `Instruments.live.tsx` through `Instruments.tsx` to `RegistryTab`.
