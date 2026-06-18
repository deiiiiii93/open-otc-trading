# Pricing Parameter Tools Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the desk agent 11 tools (4 reads, 7 HITL-gated writes) to create/maintain `PricingParameterProfile`s and drive the instrument-defaults → `AssumptionSet` pipeline, routed through a new `pricing-parameter-maintenance` workflow skill.

**Architecture:** Three layers matching the portfolios precedent — domain write functions in `app/services/domains/` (own their session + commit + `record_audit`), thin `@tool` adapters in `app/tools/` returning `{"ok": ...}` envelopes, HITL/capability wiring in `deep_agent/hitl.py` + `tools/__init__.py`. Spec: `docs/superpowers/specs/2026-06-05-pricing-parameter-tools-design.md`.

**Tech Stack:** SQLAlchemy 2.0 typed ORM, LangChain `@tool` + Pydantic schemas, pytest.

**Spec corrections locked in here** (verified against the codebase, supersede the spec where they differ):
1. Tests live in repo-root `tests/`, NOT `backend/tests/`.
2. Session pattern follows `domains/portfolios.py` (domain fn accepts `session: Session | None`, commits inside `_session_scope`), not the hedging "tool commits" pattern — `domains/pricing_profiles.py` already has `_session_scope`.
3. Error-code list gains: `blank_symbol`, `blank_name`, `no_fields`, `invalid_clear_field`, `invalid_valuation_date` (refinements, same two-tier model).
4. Nested row inputs are `list[dict[str, Any]]` with rich Field descriptions (the `book_hedge` legs precedent), validated in the service — not nested Pydantic models.

---

## Execution environment (read first)

**Work in a git worktree** — a concurrent agent churns this checkout's HEAD (use superpowers:using-git-worktrees; branch `feature/pricing-parameter-tools` off `main`).

**Python path gotcha:** the venv `.pth` points at the MAIN checkout's backend. In the worktree, every pytest run needs:

```bash
cd <worktree-root>
export PYTHONPATH="$(pwd)/backend"
```

All `Run:` commands below assume this. Baseline check before Task 1:

```bash
python -m pytest tests/test_services_domains_pricing_profiles.py tests/test_assumptions.py tests/test_capability_assignments.py tests/test_hitl.py -q
```
Expected: all pass (if not, STOP — pre-existing breakage, report it).

**Known pre-existing env failures:** see the hedging-module gotcha list; do not chase failures unrelated to these files.

---

### Task 1: Shared `DomainWriteError` + `create_profile` service

**Files:**
- Create: `backend/app/services/domains/_errors.py`
- Modify: `backend/app/services/domains/pricing_profiles.py`
- Test: `tests/test_pricing_profile_writes.py` (new)

- [ ] **Step 1.1: Create the shared error type** (no test — it's a data holder used by every subsequent test)

`backend/app/services/domains/_errors.py`:

```python
"""Shared structured refusal for domain write facades."""
from __future__ import annotations

from typing import Any


class DomainWriteError(ValueError):
    """Expected domain refusal.

    Tools translate this to ``{"ok": False, "error": <code>, "detail": ...}``
    so the agent can read the refusal and self-correct. Unexpected exceptions
    are NOT wrapped — they propagate to ToolErrorBoundaryMiddleware.
    """

    def __init__(self, error: str, detail: Any = None) -> None:
        super().__init__(error)
        self.error = error
        self.detail = detail
```

- [ ] **Step 1.2: Write the failing tests**

`tests/test_pricing_profile_writes.py`:

```python
"""Write-facade tests for app.services.domains.pricing_profiles."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import database
from app.config import Settings
from app.models import (
    AuditEvent,
    Instrument,
    Portfolio,
    Position,
    PositionValuationRun,
    PricingParameterProfile,
    PricingParameterRow,
)
from app.services.domains import pricing_profiles as svc
from app.services.domains._errors import DomainWriteError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _seed_position(trade_id: str = "T-1", symbol: str = "000905.SH") -> tuple[int, int]:
    """Create portfolio + instrument + position; return (position_id, instrument_id)."""
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="Book", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol=symbol)
        session.add(instrument)
        session.flush()
        position = Position(
            portfolio_id=portfolio.id,
            underlying=symbol,
            underlying_id=instrument.id,
            product_type="vanilla_option",
            quantity=1.0,
            source_trade_id=trade_id,
        )
        session.add(position)
        session.commit()
        return position.id, instrument.id


def test_create_profile_persists_agent_source_and_rows():
    _, instrument_id = _seed_position("T-1", "000905.SH")

    profile = svc.create_profile(
        rows=[
            {"symbol": "000905.SH", "source_trade_id": "T-1", "rate": 0.037,
             "dividend_yield": 0.013, "volatility": 0.31},
            {"symbol": "USO.NEW", "volatility": 0.42},
        ],
        valuation_date=datetime(2026, 6, 5),
    )

    assert profile.source_type == "agent"
    assert profile.status == "completed"
    assert profile.name == "Agent Pricing Parameters 2026-06-05"
    assert profile.summary["row_count"] == 2
    assert len(profile.rows) == 2
    by_symbol = {row.symbol: row for row in profile.rows}
    # Trade-keyed row resolves the booked position's instrument.
    assert by_symbol["000905.SH"].instrument_id == instrument_id
    assert by_symbol["000905.SH"].rate == 0.037
    # Unknown symbol gets a draft instrument via ensure_instrument.
    assert by_symbol["USO.NEW"].instrument_id is not None
    assert by_symbol["USO.NEW"].source_trade_id == ""
    with database.SessionLocal() as session:
        created = session.query(Instrument).filter_by(symbol="USO.NEW").one()
        assert created.status == "draft"
        audit = session.query(AuditEvent).filter_by(
            event_type="pricing_parameter_profile.created"
        ).one()
        assert audit.subject_id == str(profile.id)
        assert audit.actor == "agent"


def test_create_profile_validation_refusals():
    with pytest.raises(DomainWriteError) as no_rows:
        svc.create_profile(rows=[])
    assert no_rows.value.error == "no_rows"

    with pytest.raises(DomainWriteError) as empty:
        svc.create_profile(rows=[{"symbol": "000905.SH"}])
    assert empty.value.error == "empty_row"
    assert empty.value.detail == {"row_indexes": [0]}

    with pytest.raises(DomainWriteError) as blank:
        svc.create_profile(rows=[{"symbol": "  ", "rate": 0.03}])
    assert blank.value.error == "blank_symbol"

    with pytest.raises(DomainWriteError) as dupes:
        svc.create_profile(
            rows=[
                {"symbol": "000905.SH", "rate": 0.03},
                {"symbol": "000905.sh ", "volatility": 0.2},
            ]
        )
    assert dupes.value.error == "duplicate_rows"
    assert dupes.value.detail == {"pairs": [["", "000905.sh"]]}
    # Nothing persisted on refusal.
    with database.SessionLocal() as session:
        assert session.query(PricingParameterProfile).count() == 0
```

- [ ] **Step 1.3: Run tests to verify they fail**

Run: `python -m pytest tests/test_pricing_profile_writes.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'create_profile'`

- [ ] **Step 1.4: Implement in `backend/app/services/domains/pricing_profiles.py`**

Update the module docstring first line to: `"""Pricing parameter profile domain service (reads + agent write facade)."""` and remove the now-wrong "Read-only facade" sentence. Add imports:

```python
from datetime import datetime
from typing import Any

from app.models import (
    Position,
    PositionValuationRun,
    PricingParameterProfile,
    PricingParameterRow,
    RiskRun,
)
from app.services.audit import record_audit
from app.services.instruments import ensure_instrument

from ._errors import DomainWriteError
```

(`PositionValuationRun`/`RiskRun` are used in Task 5 — import once here.) Append:

```python
# --- write facade -----------------------------------------------------------

ARCHIVED_SOURCE_TYPE = "default_underlying_archived"
PARAM_FIELDS = ("rate", "dividend_yield", "volatility")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _normalized_pair(row: dict[str, Any]) -> tuple[str, str]:
    return (_clean(row.get("source_trade_id")).lower(), _clean(row.get("symbol")).lower())


def _validate_row_inputs(rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise DomainWriteError("no_rows")
    blank = [i for i, row in enumerate(rows) if not _clean(row.get("symbol"))]
    if blank:
        raise DomainWriteError("blank_symbol", {"row_indexes": blank})
    empty = [
        i
        for i, row in enumerate(rows)
        if all(row.get(field) is None for field in PARAM_FIELDS)
    ]
    if empty:
        raise DomainWriteError("empty_row", {"row_indexes": empty})
    seen: set[tuple[str, str]] = set()
    dupes: list[list[str]] = []
    for row in rows:
        pair = _normalized_pair(row)
        if pair in seen and list(pair) not in dupes:
            dupes.append(list(pair))
        seen.add(pair)
    if dupes:
        raise DomainWriteError("duplicate_rows", {"pairs": dupes})


def _positions_by_trade_id(sess: Session, rows: list[dict[str, Any]]) -> dict[str, Position]:
    trade_ids = {_clean(row.get("source_trade_id")) for row in rows}
    trade_ids.discard("")
    if not trade_ids:
        return {}
    positions = sess.query(Position).filter(Position.source_trade_id.in_(trade_ids)).all()
    return {p.source_trade_id: p for p in positions if p.source_trade_id}


def _instrument_id_for_row(
    sess: Session, row: dict[str, Any], positions: dict[str, Position]
) -> int:
    """Mirror the xlsx importer: booked position's underlying_id, else draft instrument."""
    position = positions.get(_clean(row.get("source_trade_id")))
    if position is not None and position.underlying_id is not None:
        return position.underlying_id
    instrument = ensure_instrument(
        sess, _clean(row.get("symbol")), source="pricing_profile", status="draft"
    )
    sess.flush()
    return instrument.id


def _reload_profile(sess: Session, profile_id: int) -> PricingParameterProfile:
    return (
        sess.query(PricingParameterProfile)
        .options(selectinload(PricingParameterProfile.rows))
        .filter(PricingParameterProfile.id == profile_id)
        .one()
    )


def create_profile(
    *,
    rows: list[dict[str, Any]],
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PricingParameterProfile:
    """Create an agent-authored r/q/vol profile (``source_type="agent"``).

    Rows are trade-keyed when ``source_trade_id`` is non-empty, otherwise
    underlying-level. Spots are deliberately NOT accepted — observations
    live in the quote store.
    """
    _validate_row_inputs(rows)
    effective_valuation = valuation_date or datetime.utcnow()
    with _session_scope(session) as sess:
        profile = PricingParameterProfile(
            name=_clean(name) or f"Agent Pricing Parameters {effective_valuation:%Y-%m-%d}",
            valuation_date=effective_valuation,
            source_type="agent",
            source_path=None,
            status="completed",
            summary={"row_count": len(rows), "created_by": actor},
        )
        sess.add(profile)
        sess.flush()
        positions = _positions_by_trade_id(sess, rows)
        for row in rows:
            sess.add(
                PricingParameterRow(
                    profile_id=profile.id,
                    source_trade_id=_clean(row.get("source_trade_id")),
                    symbol=_clean(row.get("symbol")),
                    instrument_id=_instrument_id_for_row(sess, row, positions),
                    rate=row.get("rate"),
                    dividend_yield=row.get("dividend_yield"),
                    volatility=row.get("volatility"),
                    source_payload={"created_by": actor},
                )
            )
        record_audit(
            sess,
            event_type="pricing_parameter_profile.created",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"row_count": len(rows)},
        )
        sess.commit()
        return _reload_profile(sess, profile.id)
```

Add `create_profile` to `__all__`.

- [ ] **Step 1.5: Run tests to verify they pass**

Run: `python -m pytest tests/test_pricing_profile_writes.py tests/test_services_domains_pricing_profiles.py -q`
Expected: PASS (existing read tests must stay green)

- [ ] **Step 1.6: Commit**

```bash
git add backend/app/services/domains/_errors.py backend/app/services/domains/pricing_profiles.py tests/test_pricing_profile_writes.py
git commit -m "feat(pricing-profiles): agent create_profile write facade with structured refusals"
```

---

### Task 2: `update_profile` + archived-profile guard

**Files:**
- Modify: `backend/app/services/domains/pricing_profiles.py`
- Test: `tests/test_pricing_profile_writes.py`

- [ ] **Step 2.1: Write the failing tests** (append to `tests/test_pricing_profile_writes.py`)

```python
def _create_simple_profile(**overrides) -> int:
    profile = svc.create_profile(
        rows=[{"symbol": "000905.SH", "rate": 0.03, "dividend_yield": 0.01,
               "volatility": 0.2}],
        **overrides,
    )
    return profile.id


def _retag_archived(profile_id: int) -> None:
    with database.SessionLocal() as session:
        session.get(PricingParameterProfile, profile_id).source_type = (
            "default_underlying_archived"
        )
        session.commit()


def test_update_profile_renames_and_redates():
    profile_id = _create_simple_profile()

    updated = svc.update_profile(
        profile_id=profile_id,
        name="Vol bump scenario",
        valuation_date=datetime(2026, 6, 6),
    )

    assert updated.name == "Vol bump scenario"
    assert updated.valuation_date == datetime(2026, 6, 6)
    with database.SessionLocal() as session:
        assert session.query(AuditEvent).filter_by(
            event_type="pricing_parameter_profile.updated"
        ).count() == 1


def test_update_profile_refusals():
    profile_id = _create_simple_profile()

    with pytest.raises(DomainWriteError) as no_fields:
        svc.update_profile(profile_id=profile_id)
    assert no_fields.value.error == "no_fields"

    with pytest.raises(DomainWriteError) as blank:
        svc.update_profile(profile_id=profile_id, name="   ")
    assert blank.value.error == "blank_name"

    with pytest.raises(DomainWriteError) as missing:
        svc.update_profile(profile_id=99999, name="x")
    assert missing.value.error == "profile_not_found"

    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.update_profile(profile_id=profile_id, name="x")
    assert archived.value.error == "profile_archived"
```

- [ ] **Step 2.2: Run to verify failure**

Run: `python -m pytest tests/test_pricing_profile_writes.py -q`
Expected: FAIL — no attribute `update_profile`

- [ ] **Step 2.3: Implement** (append to `domains/pricing_profiles.py`)

```python
def _mutable_profile(sess: Session, profile_id: int) -> PricingParameterProfile:
    """Load a profile for mutation; archived profiles are audit artifacts."""
    profile = sess.get(PricingParameterProfile, profile_id)
    if profile is None:
        raise DomainWriteError("profile_not_found", {"profile_id": profile_id})
    if profile.source_type == ARCHIVED_SOURCE_TYPE:
        raise DomainWriteError("profile_archived", {"profile_id": profile_id})
    return profile


def update_profile(
    *,
    profile_id: int,
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> PricingParameterProfile:
    """Rename / re-date a profile. Rows are untouched (see upsert/delete rows)."""
    if name is None and valuation_date is None:
        raise DomainWriteError("no_fields")
    if name is not None and not _clean(name):
        raise DomainWriteError("blank_name")
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        changes: dict[str, Any] = {}
        if name is not None:
            profile.name = _clean(name)
            changes["name"] = profile.name
        if valuation_date is not None:
            profile.valuation_date = valuation_date
            changes["valuation_date"] = valuation_date.isoformat()
        record_audit(
            sess,
            event_type="pricing_parameter_profile.updated",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload=changes,
        )
        sess.commit()
        return _reload_profile(sess, profile.id)
```

Add to `__all__`.

- [ ] **Step 2.4: Run to verify pass**

Run: `python -m pytest tests/test_pricing_profile_writes.py -q` — Expected: PASS

- [ ] **Step 2.5: Commit**

```bash
git add backend/app/services/domains/pricing_profiles.py tests/test_pricing_profile_writes.py
git commit -m "feat(pricing-profiles): update_profile metadata write with archived guard"
```

---

### Task 3: `upsert_rows`

**Files:** same as Task 2.

- [ ] **Step 3.1: Write the failing tests** (append)

```python
def test_upsert_rows_updates_matches_and_inserts_new():
    profile_id = _create_simple_profile()

    profile, counts = svc.upsert_rows(
        profile_id=profile_id,
        rows=[
            # Matches existing ("", "000905.SH") row: vol overwritten, r/q kept.
            {"symbol": "000905.SH", "volatility": 0.35},
            # New underlying-level row.
            {"symbol": "000852.SH", "rate": 0.028, "dividend_yield": 0.02,
             "volatility": 0.27},
        ],
    )

    assert counts == {"updated": 1, "inserted": 1}
    by_symbol = {row.symbol: row for row in profile.rows}
    assert by_symbol["000905.SH"].volatility == 0.35
    assert by_symbol["000905.SH"].rate == 0.03  # untouched
    assert by_symbol["000852.SH"].volatility == 0.27
    assert profile.summary["row_count"] == 2


def test_upsert_rows_guards():
    profile_id = _create_simple_profile()
    with pytest.raises(DomainWriteError) as empty:
        svc.upsert_rows(profile_id=profile_id, rows=[{"symbol": "000905.SH"}])
    assert empty.value.error == "empty_row"

    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.upsert_rows(profile_id=profile_id,
                        rows=[{"symbol": "000905.SH", "rate": 0.01}])
    assert archived.value.error == "profile_archived"
```

- [ ] **Step 3.2: Run to verify failure** — `python -m pytest tests/test_pricing_profile_writes.py -q`

- [ ] **Step 3.3: Implement** (append)

```python
def upsert_rows(
    *,
    profile_id: int,
    rows: list[dict[str, Any]],
    actor: str = "agent",
    session: Session | None = None,
) -> tuple[PricingParameterProfile, dict[str, int]]:
    """Upsert rows by normalized (source_trade_id, symbol).

    Matched rows overwrite only the provided (non-null) fields; clearing a
    field means delete the row and recreate it. Unmatched rows insert with
    the same instrument resolution as create_profile.
    """
    _validate_row_inputs(rows)
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        existing = (
            sess.query(PricingParameterRow)
            .filter(PricingParameterRow.profile_id == profile.id)
            .all()
        )
        by_pair = {
            (_clean(row.source_trade_id).lower(), _clean(row.symbol).lower()): row
            for row in existing
        }
        positions = _positions_by_trade_id(sess, rows)
        updated = inserted = 0
        for row in rows:
            match = by_pair.get(_normalized_pair(row))
            if match is not None:
                for field in PARAM_FIELDS:
                    if row.get(field) is not None:
                        setattr(match, field, row[field])
                updated += 1
                continue
            sess.add(
                PricingParameterRow(
                    profile_id=profile.id,
                    source_trade_id=_clean(row.get("source_trade_id")),
                    symbol=_clean(row.get("symbol")),
                    instrument_id=_instrument_id_for_row(sess, row, positions),
                    rate=row.get("rate"),
                    dividend_yield=row.get("dividend_yield"),
                    volatility=row.get("volatility"),
                    source_payload={"created_by": actor},
                )
            )
            inserted += 1
        profile.summary = {**(profile.summary or {}), "row_count": len(existing) + inserted}
        record_audit(
            sess,
            event_type="pricing_parameter_profile.rows_upserted",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"updated": updated, "inserted": inserted},
        )
        sess.commit()
        return _reload_profile(sess, profile.id), {"updated": updated, "inserted": inserted}
```

Add to `__all__`.

- [ ] **Step 3.4: Run to verify pass**, then **Step 3.5: Commit**

```bash
git add backend/app/services/domains/pricing_profiles.py tests/test_pricing_profile_writes.py
git commit -m "feat(pricing-profiles): upsert_rows with field-preserving match semantics"
```

---

### Task 4: `delete_rows`

**Files:** same as Task 2.

- [ ] **Step 4.1: Write the failing tests** (append)

```python
def test_delete_rows_removes_owned_rows_only():
    profile_id = _create_simple_profile()
    other_id = _create_simple_profile(name="Other")
    with database.SessionLocal() as session:
        own_row = session.query(PricingParameterRow).filter_by(
            profile_id=profile_id
        ).one()
        foreign_row = session.query(PricingParameterRow).filter_by(
            profile_id=other_id
        ).one()

    with pytest.raises(DomainWriteError) as not_owned:
        svc.delete_rows(profile_id=profile_id, row_ids=[own_row.id, foreign_row.id])
    assert not_owned.value.error == "rows_not_in_profile"
    assert not_owned.value.detail == {"row_ids": [foreign_row.id]}

    profile, deleted = svc.delete_rows(profile_id=profile_id, row_ids=[own_row.id])
    assert deleted == 1
    assert profile.rows == []
    assert profile.summary["row_count"] == 0


def test_delete_rows_refuses_empty_and_archived():
    profile_id = _create_simple_profile()
    with pytest.raises(DomainWriteError) as empty:
        svc.delete_rows(profile_id=profile_id, row_ids=[])
    assert empty.value.error == "no_rows"

    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.delete_rows(profile_id=profile_id, row_ids=[1])
    assert archived.value.error == "profile_archived"
```

- [ ] **Step 4.2: Run to verify failure**, then **Step 4.3: Implement** (append)

```python
def delete_rows(
    *,
    profile_id: int,
    row_ids: list[int],
    actor: str = "agent",
    session: Session | None = None,
) -> tuple[PricingParameterProfile, int]:
    """Delete rows by id; refuses wholesale if any id is not in the profile."""
    if not row_ids:
        raise DomainWriteError("no_rows")
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        found = (
            sess.query(PricingParameterRow)
            .filter(
                PricingParameterRow.profile_id == profile.id,
                PricingParameterRow.id.in_(row_ids),
            )
            .all()
        )
        missing = sorted(set(row_ids) - {row.id for row in found})
        if missing:
            raise DomainWriteError("rows_not_in_profile", {"row_ids": missing})
        total = (
            sess.query(PricingParameterRow)
            .filter(PricingParameterRow.profile_id == profile.id)
            .count()
        )
        for row in found:
            sess.delete(row)
        profile.summary = {**(profile.summary or {}), "row_count": total - len(found)}
        record_audit(
            sess,
            event_type="pricing_parameter_profile.rows_deleted",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"row_ids": sorted(set(row_ids)), "deleted": len(found)},
        )
        sess.commit()
        return _reload_profile(sess, profile.id), len(found)
```

Add to `__all__`.

- [ ] **Step 4.4: Run to verify pass**, then **Step 4.5: Commit**

```bash
git add backend/app/services/domains/pricing_profiles.py tests/test_pricing_profile_writes.py
git commit -m "feat(pricing-profiles): delete_rows with whole-call ownership guard"
```

---

### Task 5: `delete_profile` (FK-guarded, irreversible)

**Files:** same as Task 2.

- [ ] **Step 5.1: Write the failing tests** (append)

```python
def test_delete_profile_refused_when_referenced_by_runs():
    profile_id = _create_simple_profile()
    with database.SessionLocal() as session:
        portfolio = Portfolio(name="RunBook", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        session.add(
            PositionValuationRun(
                portfolio_id=portfolio.id,
                pricing_parameter_profile_id=profile_id,
                valuation_date=datetime(2026, 6, 5),
                status="completed",
            )
        )
        session.commit()
        run_id = session.query(PositionValuationRun.id).scalar()

    with pytest.raises(DomainWriteError) as referenced:
        svc.delete_profile(profile_id=profile_id)
    assert referenced.value.error == "profile_referenced_by_runs"
    assert referenced.value.detail["position_valuation_run_ids"] == [run_id]
    with database.SessionLocal() as session:
        assert session.get(PricingParameterProfile, profile_id) is not None


def test_delete_profile_cascades_rows_when_unreferenced():
    profile_id = _create_simple_profile()

    result = svc.delete_profile(profile_id=profile_id)

    assert result["deleted_profile_id"] == profile_id
    assert result["deleted_row_count"] == 1
    with database.SessionLocal() as session:
        assert session.get(PricingParameterProfile, profile_id) is None
        assert session.query(PricingParameterRow).filter_by(
            profile_id=profile_id
        ).count() == 0
        assert session.query(AuditEvent).filter_by(
            event_type="pricing_parameter_profile.deleted"
        ).count() == 1


def test_delete_profile_refuses_archived():
    profile_id = _create_simple_profile()
    _retag_archived(profile_id)
    with pytest.raises(DomainWriteError) as archived:
        svc.delete_profile(profile_id=profile_id)
    assert archived.value.error == "profile_archived"
```

NOTE: if `PositionValuationRun` has additional NOT-NULL columns beyond
`portfolio_id`/`valuation_date`/`status`, supply the minimal extra defaults the
model requires (check `backend/app/models.py:1320`) — do NOT weaken the assertion.

- [ ] **Step 5.2: Run to verify failure**, then **Step 5.3: Implement** (append)

```python
def delete_profile(
    *,
    profile_id: int,
    actor: str = "agent",
    session: Session | None = None,
) -> dict[str, Any]:
    """Delete an unreferenced profile (cascades rows). IRREVERSIBLE.

    Refuses when any position_valuation_run or risk_run references the
    profile — those runs' audit trails depend on it.
    """
    with _session_scope(session) as sess:
        profile = _mutable_profile(sess, profile_id)
        valuation_run_ids = [
            run_id
            for (run_id,) in sess.query(PositionValuationRun.id).filter(
                PositionValuationRun.pricing_parameter_profile_id == profile.id
            )
        ]
        risk_run_ids = [
            run_id
            for (run_id,) in sess.query(RiskRun.id).filter(
                RiskRun.pricing_parameter_profile_id == profile.id
            )
        ]
        if valuation_run_ids or risk_run_ids:
            raise DomainWriteError(
                "profile_referenced_by_runs",
                {
                    "position_valuation_run_ids": valuation_run_ids,
                    "risk_run_ids": risk_run_ids,
                },
            )
        row_count = (
            sess.query(PricingParameterRow)
            .filter(PricingParameterRow.profile_id == profile.id)
            .count()
        )
        name = profile.name
        record_audit(
            sess,
            event_type="pricing_parameter_profile.deleted",
            actor=actor,
            subject_type="pricing_parameter_profile",
            subject_id=profile.id,
            payload={"name": name, "row_count": row_count},
        )
        sess.delete(profile)
        sess.commit()
        return {"deleted_profile_id": profile_id, "deleted_row_count": row_count, "name": name}
```

Add to `__all__` (final: `["list_profiles", "get_profile", "create_profile", "update_profile", "upsert_rows", "delete_rows", "delete_profile", "ARCHIVED_SOURCE_TYPE"]`).

- [ ] **Step 5.4: Run to verify pass** — `python -m pytest tests/test_pricing_profile_writes.py tests/test_services_domains_pricing_profiles.py -q`
- [ ] **Step 5.5: Commit**

```bash
git add backend/app/services/domains/pricing_profiles.py tests/test_pricing_profile_writes.py
git commit -m "feat(pricing-profiles): guarded delete_profile refusing run-referenced profiles"
```

---

### Task 6: `domains/assumptions.py` — reads

**Files:**
- Create: `backend/app/services/domains/assumptions.py`
- Test: `tests/test_assumptions_domain.py` (new)

- [ ] **Step 6.1: Write the failing tests**

`tests/test_assumptions_domain.py`:

```python
"""Domain facade tests for app.services.domains.assumptions."""
from __future__ import annotations

from datetime import datetime

import pytest

from app import database
from app.config import Settings
from app.models import (
    AssumptionRow,
    AssumptionSet,
    AuditEvent,
    Instrument,
    Portfolio,
    Position,
)
from app.services.domains import assumptions as svc
from app.services.domains._errors import DomainWriteError


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _insert_set(name: str, *, valuation_date: datetime) -> int:
    with database.SessionLocal() as session:
        instrument = Instrument(symbol=f"{name}.SYM")
        session.add(instrument)
        session.flush()
        assumption_set = AssumptionSet(
            name=name, valuation_date=valuation_date, status="completed",
            summary={"row_count": 1},
        )
        session.add(assumption_set)
        session.flush()
        session.add(
            AssumptionRow(
                set_id=assumption_set.id,
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                rate=0.03, dividend_yield=0.01, volatility=0.2,
                source_payload={"manual_input_sources": {"rate": "instrument_default"}},
            )
        )
        session.commit()
        return assumption_set.id


def test_list_sets_newest_first_with_query():
    _insert_set("Old", valuation_date=datetime(2026, 6, 1))
    newest = _insert_set("New", valuation_date=datetime(2026, 6, 5))

    sets = svc.list_sets()
    assert [s.name for s in sets] == ["New", "Old"]
    assert sets[0].id == newest
    assert len(sets[0].rows) == 1

    assert [s.name for s in svc.list_sets(query="2026-06-01")] == ["Old"]


def test_get_set_returns_row_or_none():
    set_id = _insert_set("One", valuation_date=datetime(2026, 6, 5))
    found = svc.get_set(set_id=set_id)
    assert found is not None and found.rows[0].symbol == "One.SYM"
    assert svc.get_set(set_id=99999) is None


def test_get_instrument_defaults_filters_by_symbols():
    with database.SessionLocal() as session:
        session.add(Instrument(symbol="AAA.SH", rate=0.03))
        session.add(Instrument(symbol="BBB.SH", volatility=0.25))
        session.commit()

    rows = svc.get_instrument_defaults()
    assert [r.symbol for r in rows] == ["AAA.SH", "BBB.SH"]

    only_b = svc.get_instrument_defaults(symbols=["BBB.SH", " "])
    assert [r.symbol for r in only_b] == ["BBB.SH"]
    assert only_b[0].volatility == 0.25
```

- [ ] **Step 6.2: Run to verify failure** — `python -m pytest tests/test_assumptions_domain.py -q` (ModuleNotFoundError)

- [ ] **Step 6.3: Implement `backend/app/services/domains/assumptions.py`**

```python
"""Assumption-set domain facade: reads + pipeline-only writes.

AssumptionSets stay DERIVED — the only write path is build_assumptions_set
(instrument defaults -> inherited profile rows, with per-field provenance).
Direct AssumptionRow writes are deliberately not exposed.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import String, cast, desc, or_
from sqlalchemy.orm import Session, selectinload

from app import database
from app.models import AssumptionSet, Instrument
from app.services.assumptions import build_assumptions_set
from app.services.audit import record_audit
from app.services.instruments import ensure_instrument

from ._errors import DomainWriteError

DEFAULT_FIELDS = ("rate", "dividend_yield", "volatility")


@contextmanager
def _session_scope(session: Session | None) -> Iterator[Session]:
    if session is not None:
        yield session
        return
    database.init_db()
    with database.SessionLocal() as sess:
        yield sess


def list_sets(
    *,
    query: str | None = None,
    limit: int = 20,
    session: Session | None = None,
) -> list[AssumptionSet]:
    """Stored assumption sets, newest first; query matches name/status/date."""
    capped = max(1, min(int(limit), 100))
    with _session_scope(session) as sess:
        stmt = sess.query(AssumptionSet).options(selectinload(AssumptionSet.rows))
        cleaned = (query or "").strip()
        if cleaned:
            pattern = f"%{cleaned}%"
            stmt = stmt.filter(
                or_(
                    AssumptionSet.name.ilike(pattern),
                    AssumptionSet.status.ilike(pattern),
                    cast(AssumptionSet.valuation_date, String).ilike(pattern),
                )
            )
        return (
            stmt.order_by(
                desc(AssumptionSet.valuation_date),
                desc(AssumptionSet.created_at),
                desc(AssumptionSet.id),
            )
            .limit(capped)
            .all()
        )


def get_set(*, set_id: int, session: Session | None = None) -> AssumptionSet | None:
    with _session_scope(session) as sess:
        return (
            sess.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .filter(AssumptionSet.id == set_id)
            .one_or_none()
        )


def get_instrument_defaults(
    *,
    symbols: list[str] | None = None,
    limit: int = 50,
    session: Session | None = None,
) -> list[Instrument]:
    """Instrument r/q/vol defaults (the assumption pipeline's first source)."""
    capped = max(1, min(int(limit), 200))
    with _session_scope(session) as sess:
        stmt = sess.query(Instrument)
        cleaned = [s.strip() for s in (symbols or []) if s and s.strip()]
        if cleaned:
            stmt = stmt.filter(Instrument.symbol.in_(cleaned))
        return stmt.order_by(Instrument.symbol.asc()).limit(capped).all()


__all__ = ["list_sets", "get_set", "get_instrument_defaults"]
```

- [ ] **Step 6.4: Run to verify pass**, then **Step 6.5: Commit**

```bash
git add backend/app/services/domains/assumptions.py tests/test_assumptions_domain.py
git commit -m "feat(assumptions): domain read facade (sets + instrument defaults)"
```

---

### Task 7: `set_instrument_defaults`

**Files:** same as Task 6.

- [ ] **Step 7.1: Write the failing tests** (append to `tests/test_assumptions_domain.py`)

```python
def test_set_instrument_defaults_sets_clears_and_creates_draft():
    instrument = svc.set_instrument_defaults(
        symbol="NEW.SH", rate=0.03, volatility=0.22
    )
    assert instrument.rate == 0.03
    assert instrument.volatility == 0.22
    assert instrument.status == "draft"

    cleared = svc.set_instrument_defaults(symbol="NEW.SH", clear=["volatility"])
    assert cleared.volatility is None
    assert cleared.rate == 0.03  # untouched
    with database.SessionLocal() as session:
        assert session.query(AuditEvent).filter_by(
            event_type="instrument.pricing_defaults_updated"
        ).count() == 2


def test_set_instrument_defaults_refusals():
    with pytest.raises(DomainWriteError) as nothing:
        svc.set_instrument_defaults(symbol="NEW.SH")
    assert nothing.value.error == "no_fields"

    with pytest.raises(DomainWriteError) as unknown:
        svc.set_instrument_defaults(symbol="NEW.SH", clear=["spot"])
    assert unknown.value.error == "invalid_clear_field"

    with pytest.raises(DomainWriteError) as conflict:
        svc.set_instrument_defaults(symbol="NEW.SH", rate=0.02, clear=["rate"])
    assert conflict.value.error == "field_set_and_cleared"

    with pytest.raises(DomainWriteError) as blank:
        svc.set_instrument_defaults(symbol="  ", rate=0.02)
    assert blank.value.error == "blank_symbol"
```

- [ ] **Step 7.2: Run to verify failure**, then **Step 7.3: Implement** (append to `domains/assumptions.py`)

```python
def set_instrument_defaults(
    *,
    symbol: str,
    rate: float | None = None,
    dividend_yield: float | None = None,
    volatility: float | None = None,
    clear: list[str] | tuple[str, ...] = (),
    actor: str = "agent",
    session: Session | None = None,
) -> Instrument:
    """Set/clear an instrument's baseline r/q/vol (ensure-creates a draft row).

    Provided non-null values set; ``clear`` entries null out. The same field
    in both is a refusal, not last-wins.
    """
    if not str(symbol or "").strip():
        raise DomainWriteError("blank_symbol")
    provided = {
        field: value
        for field, value in (
            ("rate", rate),
            ("dividend_yield", dividend_yield),
            ("volatility", volatility),
        )
        if value is not None
    }
    clear_fields = [str(field).strip() for field in (clear or []) if str(field).strip()]
    unknown = sorted(set(clear_fields) - set(DEFAULT_FIELDS))
    if unknown:
        raise DomainWriteError("invalid_clear_field", {"fields": unknown})
    conflicting = sorted(set(clear_fields) & set(provided))
    if conflicting:
        raise DomainWriteError("field_set_and_cleared", {"fields": conflicting})
    if not provided and not clear_fields:
        raise DomainWriteError("no_fields")
    with _session_scope(session) as sess:
        instrument = ensure_instrument(
            sess, symbol, source="pricing_profile", status="draft"
        )
        sess.flush()
        for field, value in provided.items():
            setattr(instrument, field, value)
        for field in clear_fields:
            setattr(instrument, field, None)
        record_audit(
            sess,
            event_type="instrument.pricing_defaults_updated",
            actor=actor,
            subject_type="instrument",
            subject_id=instrument.id,
            payload={"symbol": instrument.symbol, "set": provided, "cleared": clear_fields},
        )
        sess.commit()
        return instrument
```

Add to `__all__`.

- [ ] **Step 7.4: Run to verify pass**, then **Step 7.5: Commit**

```bash
git add backend/app/services/domains/assumptions.py tests/test_assumptions_domain.py
git commit -m "feat(assumptions): set_instrument_defaults with set/clear conflict guard"
```

---

### Task 8: `build_set`

**Files:** same as Task 6.

- [ ] **Step 8.1: Write the failing tests** (append)

```python
def _seed_open_position(symbol: str, *, with_defaults: bool) -> None:
    with database.SessionLocal() as session:
        portfolio = Portfolio(name=f"Book-{symbol}", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol=symbol)
        if with_defaults:
            instrument.rate = 0.03
            instrument.dividend_yield = 0.01
            instrument.volatility = 0.2
        session.add(instrument)
        session.flush()
        session.add(
            Position(
                portfolio_id=portfolio.id,
                underlying=symbol,
                underlying_id=instrument.id,
                product_type="vanilla_option",
                quantity=1.0,
                status="open",
            )
        )
        session.commit()


def test_build_set_builds_from_instrument_defaults():
    _seed_open_position("FULL.SH", with_defaults=True)

    built = svc.build_set(name="Nightly", valuation_date=datetime(2026, 6, 5))

    assert built.name == "Nightly"
    assert built.summary["row_count"] == 1
    assert built.rows[0].symbol == "FULL.SH"
    assert built.rows[0].volatility == 0.2
    with database.SessionLocal() as session:
        audit = session.query(AuditEvent).filter_by(event_type="assumptions.built").one()
        assert audit.actor == "agent"


def test_build_set_surfaces_unfilled_underlyings():
    _seed_open_position("BARE.SH", with_defaults=False)

    with pytest.raises(DomainWriteError) as unfilled:
        svc.build_set()
    assert unfilled.value.error == "unfilled_underlyings"
    assert unfilled.value.detail == {"underlyings": ["BARE.SH"]}
    with database.SessionLocal() as session:
        assert session.query(AssumptionSet).count() == 0


def test_build_set_surfaces_no_open_positions():
    with pytest.raises(DomainWriteError) as nothing:
        svc.build_set()
    assert nothing.value.error == "no_open_positions"
```

- [ ] **Step 8.2: Run to verify failure**, then **Step 8.3: Implement** (append)

```python
def build_set(
    *,
    name: str | None = None,
    valuation_date: datetime | None = None,
    actor: str = "agent",
    session: Session | None = None,
) -> AssumptionSet:
    """Rebuild the assumption set from open-position scope (the pipeline write).

    Translates build_assumptions_set's ValueErrors into structured refusals;
    nothing persists on refusal (no commit happens).
    """
    with _session_scope(session) as sess:
        try:
            assumption_set = build_assumptions_set(
                sess, name=name, valuation_date=valuation_date
            )
        except ValueError as exc:
            arg = exc.args[0] if exc.args else "build failed"
            if isinstance(arg, dict) and "unfilled_underlyings" in arg:
                raise DomainWriteError(
                    "unfilled_underlyings",
                    {"underlyings": list(arg["unfilled_underlyings"])},
                ) from exc
            if arg == "no open positions in scope":
                raise DomainWriteError("no_open_positions") from exc
            raise
        record_audit(
            sess,
            event_type="assumptions.built",
            actor=actor,
            subject_type="assumption_set",
            subject_id=assumption_set.id,
            payload={
                "row_count": assumption_set.summary.get("row_count"),
                "instruments": assumption_set.summary.get("instruments", []),
            },
        )
        sess.commit()
        return (
            sess.query(AssumptionSet)
            .options(selectinload(AssumptionSet.rows))
            .filter(AssumptionSet.id == assumption_set.id)
            .one()
        )
```

Add to `__all__` (final: `["list_sets", "get_set", "get_instrument_defaults", "set_instrument_defaults", "build_set"]`).

- [ ] **Step 8.4: Run to verify pass** — `python -m pytest tests/test_assumptions_domain.py tests/test_assumptions.py -q`
- [ ] **Step 8.5: Commit**

```bash
git add backend/app/services/domains/assumptions.py tests/test_assumptions_domain.py
git commit -m "feat(assumptions): build_set pipeline write with structured refusals"
```

---

### Task 9: Shaping helpers + error translator

**Files:**
- Modify: `backend/app/tools/_shaping.py`
- Test: covered by Tasks 10–11 tool tests (shapers are pure projections; no standalone test file)

- [ ] **Step 9.1: Add to `backend/app/tools/_shaping.py`** — imports `AssumptionRow, AssumptionSet, Instrument, PricingParameterRow` (extend the existing `app.models` import) plus:

```python
from datetime import datetime

from app.services.domains._errors import DomainWriteError


def shape_pricing_parameter_row(row: PricingParameterRow) -> dict[str, Any]:
    """Row ids are the agent's handles for upsert/delete row tools."""
    return {
        "id": row.id,
        "source_trade_id": row.source_trade_id,
        "symbol": row.symbol,
        "instrument_id": row.instrument_id,
        "rate": row.rate,
        "dividend_yield": row.dividend_yield,
        "volatility": row.volatility,
    }


def shape_assumption_row(row: AssumptionRow) -> dict[str, Any]:
    payload = row.source_payload or {}
    return {
        "id": row.id,
        "instrument_id": row.instrument_id,
        "symbol": row.symbol,
        "rate": row.rate,
        "dividend_yield": row.dividend_yield,
        "volatility": row.volatility,
        "field_sources": payload.get("manual_input_sources", {}),
    }


def shape_assumption_set(
    assumption_set: AssumptionSet, *, include_rows: bool = False
) -> dict[str, Any]:
    summary = assumption_set.summary if isinstance(assumption_set.summary, dict) else {}
    shaped: dict[str, Any] = {
        "id": assumption_set.id,
        "name": assumption_set.name,
        "valuation_date": (
            assumption_set.valuation_date.isoformat()
            if assumption_set.valuation_date
            else None
        ),
        "status": assumption_set.status,
        "row_count": summary.get("row_count", len(assumption_set.rows or [])),
        "created_at": (
            assumption_set.created_at.isoformat() if assumption_set.created_at else None
        ),
    }
    if include_rows:
        shaped["rows"] = [shape_assumption_row(row) for row in assumption_set.rows]
    return shaped


def shape_instrument_defaults(instrument: Instrument) -> dict[str, Any]:
    return {
        "id": instrument.id,
        "symbol": instrument.symbol,
        "status": instrument.status,
        "rate": instrument.rate,
        "dividend_yield": instrument.dividend_yield,
        "volatility": instrument.volatility,
    }


def domain_write_error_response(exc: DomainWriteError) -> dict[str, Any]:
    response: dict[str, Any] = {"ok": False, "error": exc.error}
    if exc.detail is not None:
        response["detail"] = exc.detail
    return response


def parse_valuation_date(value: str | None) -> datetime | None:
    """ISO-8601 string -> datetime; structured refusal on garbage."""
    if value is None or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise DomainWriteError("invalid_valuation_date", {"value": value}) from exc
```

- [ ] **Step 9.2: Sanity import check**

Run: `python -c "import sys; sys.path.insert(0, 'backend'); from app.tools import _shaping; print('ok')"`
Expected: `ok` (note: plain `python -c` with explicit sys.path — the venv .pth points at the main checkout)

- [ ] **Step 9.3: Commit**

```bash
git add backend/app/tools/_shaping.py
git commit -m "feat(tools): shaping helpers for profile rows, assumption sets, write errors"
```

---

### Task 10: Profile tools (1 read + 5 writes)

**Files:**
- Modify: `backend/app/tools/pricing_profiles.py`
- Test: `tests/test_tools_pricing_profiles.py` (new)

- [ ] **Step 10.1: Write the failing tests**

`tests/test_tools_pricing_profiles.py`:

```python
"""Tool-layer tests: thin adapters, ok-envelopes, error translation."""
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.tools.pricing_profiles import (
    create_pricing_parameter_profile_tool,
    delete_pricing_parameter_profile_tool,
    delete_pricing_parameter_rows_tool,
    get_pricing_parameter_profile_tool,
    update_pricing_parameter_profile_tool,
    upsert_pricing_parameter_rows_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _create(rows=None, **kwargs):
    return create_pricing_parameter_profile_tool.invoke(
        {
            "rows": rows
            or [{"symbol": "000905.SH", "rate": 0.037, "dividend_yield": 0.013,
                 "volatility": 0.31}],
            **kwargs,
        }
    )


def test_create_then_get_roundtrip():
    created = _create(valuation_date="2026-06-05T00:00:00")
    assert created["ok"] is True
    assert created["data"]["source_type"] == "agent"
    assert created["data"]["rows"][0]["volatility"] == 0.31

    fetched = get_pricing_parameter_profile_tool.invoke(
        {"profile_id": created["data"]["id"]}
    )
    assert fetched["ok"] is True
    assert fetched["data"]["rows"][0]["symbol"] == "000905.SH"
    assert fetched["data"]["rows"][0]["id"] is not None


def test_structured_refusals_surface_as_ok_false():
    assert get_pricing_parameter_profile_tool.invoke({"profile_id": 999}) == {
        "ok": False,
        "error": "profile_not_found",
        "detail": {"profile_id": 999},
    }
    empty = _create(rows=[{"symbol": "000905.SH"}])
    assert empty == {"ok": False, "error": "empty_row", "detail": {"row_indexes": [0]}}
    bad_date = _create(valuation_date="yesterday-ish")
    assert bad_date["ok"] is False
    assert bad_date["error"] == "invalid_valuation_date"


def test_update_upsert_delete_rows_and_profile():
    profile_id = _create()["data"]["id"]

    renamed = update_pricing_parameter_profile_tool.invoke(
        {"profile_id": profile_id, "name": "Vol bump"}
    )
    assert renamed["ok"] is True and renamed["data"]["name"] == "Vol bump"

    upserted = upsert_pricing_parameter_rows_tool.invoke(
        {"profile_id": profile_id,
         "rows": [{"symbol": "000905.SH", "volatility": 0.4},
                  {"symbol": "000852.SH", "rate": 0.02, "dividend_yield": 0.0,
                   "volatility": 0.3}]}
    )
    assert upserted["ok"] is True
    assert upserted["updated"] == 1 and upserted["inserted"] == 1

    row_ids = [row["id"] for row in upserted["data"]["rows"]]
    deleted_rows = delete_pricing_parameter_rows_tool.invoke(
        {"profile_id": profile_id, "row_ids": row_ids[:1]}
    )
    assert deleted_rows["ok"] is True and deleted_rows["deleted"] == 1

    deleted = delete_pricing_parameter_profile_tool.invoke({"profile_id": profile_id})
    assert deleted["ok"] is True
    assert deleted["data"]["deleted_profile_id"] == profile_id
```

- [ ] **Step 10.2: Run to verify failure** — `python -m pytest tests/test_tools_pricing_profiles.py -q` (ImportError)

- [ ] **Step 10.3: Implement — replace `backend/app/tools/pricing_profiles.py` with:**

```python
"""@tool wrappers for pricing parameter profiles (read + agent write facade)."""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import pricing_profiles as pricing_profiles_svc
from app.services.domains._errors import DomainWriteError

from ._shaping import (
    domain_write_error_response,
    parse_valuation_date,
    shape_pricing_parameter_profile,
    shape_pricing_parameter_row,
)

_ROWS_DESCRIPTION = (
    "Each row: {symbol: '000905.SH', source_trade_id: ''|'T-123', rate: 0.03, "
    "dividend_yield: 0.01, volatility: 0.22}. r/q/vol optional per row but at "
    "least one required; omit source_trade_id (or pass '') for underlying-level "
    "rows. NO spot field — spots live in the quote store. NOTE: a position only "
    "resolves a row that carries ALL of rate/dividend_yield/volatility — copy "
    "current values for fields you are not changing."
)


class ListPricingParameterProfilesInput(BaseModel):
    query: str | None = Field(
        default=None,
        description=(
            "Optional case-insensitive substring to match profile name, source type, "
            "or valuation date. Use this to resolve a user-named profile."
        ),
    )
    limit: int = Field(default=20, ge=1, le=100, description="Max profiles to return.")


class GetPricingParameterProfileInput(BaseModel):
    profile_id: int


class CreatePricingParameterProfileInput(BaseModel):
    rows: list[dict[str, Any]] = Field(description=_ROWS_DESCRIPTION)
    name: str | None = Field(
        default=None, description="Defaults to 'Agent Pricing Parameters <date>'."
    )
    valuation_date: str | None = Field(
        default=None, description="ISO datetime; defaults to now."
    )


class UpdatePricingParameterProfileInput(BaseModel):
    profile_id: int
    name: str | None = None
    valuation_date: str | None = Field(default=None, description="ISO datetime.")


class UpsertPricingParameterRowsInput(BaseModel):
    profile_id: int
    rows: list[dict[str, Any]] = Field(
        description=_ROWS_DESCRIPTION
        + " Rows match existing ones on (source_trade_id, symbol); matched rows "
        "only overwrite the provided fields."
    )


class DeletePricingParameterRowsInput(BaseModel):
    profile_id: int
    row_ids: list[int] = Field(
        description="Row ids from get_pricing_parameter_profile; all must belong "
        "to the profile or the whole call is refused."
    )


class DeletePricingParameterProfileInput(BaseModel):
    profile_id: int


def _profile_with_rows(profile: Any) -> dict[str, Any]:
    shaped = shape_pricing_parameter_profile(profile)
    shaped["rows"] = [shape_pricing_parameter_row(row) for row in profile.rows]
    return shaped


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_pricing_parameter_profiles", args_schema=ListPricingParameterProfilesInput)
def list_pricing_parameter_profiles_tool(
    query: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List stored pricing parameter profiles for selecting a profile id."""
    rows = pricing_profiles_svc.list_profiles(query=query, limit=limit)
    return {
        "ok": True,
        "data": [shape_pricing_parameter_profile(p) for p in rows],
        "total_count": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_pricing_parameter_profile", args_schema=GetPricingParameterProfileInput)
def get_pricing_parameter_profile_tool(profile_id: int) -> dict[str, Any]:
    """Fetch one pricing parameter profile with full r/q/vol rows (row ids are
    the handles for the upsert/delete row tools)."""
    profile = pricing_profiles_svc.get_profile(profile_id=profile_id)
    if profile is None:
        return {"ok": False, "error": "profile_not_found", "detail": {"profile_id": profile_id}}
    return {"ok": True, "data": _profile_with_rows(profile)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("create_pricing_parameter_profile", args_schema=CreatePricingParameterProfileInput)
def create_pricing_parameter_profile_tool(
    rows: list[dict[str, Any]],
    name: str | None = None,
    valuation_date: str | None = None,
) -> dict[str, Any]:
    """Create an agent what-if r/q/vol profile (source_type='agent'); pass the
    returned id to price_positions / run_risk. HITL — requires confirmation."""
    try:
        profile = pricing_profiles_svc.create_profile(
            rows=rows, name=name, valuation_date=parse_valuation_date(valuation_date)
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("update_pricing_parameter_profile", args_schema=UpdatePricingParameterProfileInput)
def update_pricing_parameter_profile_tool(
    profile_id: int,
    name: str | None = None,
    valuation_date: str | None = None,
) -> dict[str, Any]:
    """Rename / re-date a profile (metadata only; rows have their own tools).
    HITL — requires confirmation."""
    try:
        profile = pricing_profiles_svc.update_profile(
            profile_id=profile_id,
            name=name,
            valuation_date=parse_valuation_date(valuation_date),
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("upsert_pricing_parameter_rows", args_schema=UpsertPricingParameterRowsInput)
def upsert_pricing_parameter_rows_tool(
    profile_id: int,
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Upsert profile rows by (source_trade_id, symbol); matched rows overwrite
    only provided fields. HITL — requires confirmation."""
    try:
        profile, counts = pricing_profiles_svc.upsert_rows(
            profile_id=profile_id, rows=rows
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile), **counts}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("delete_pricing_parameter_rows", args_schema=DeletePricingParameterRowsInput)
def delete_pricing_parameter_rows_tool(
    profile_id: int,
    row_ids: list[int],
) -> dict[str, Any]:
    """Delete rows from a profile; refused wholesale if any id is foreign.
    HITL — requires confirmation."""
    try:
        profile, deleted = pricing_profiles_svc.delete_rows(
            profile_id=profile_id, row_ids=row_ids
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": _profile_with_rows(profile), "deleted": deleted}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("delete_pricing_parameter_profile", args_schema=DeletePricingParameterProfileInput)
def delete_pricing_parameter_profile_tool(profile_id: int) -> dict[str, Any]:
    """Delete an UNREFERENCED profile (cascades its rows). Refused when any
    valuation/risk run references it. IRREVERSIBLE; HITL — requires confirmation."""
    try:
        result = pricing_profiles_svc.delete_profile(profile_id=profile_id)
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": result}


__all__ = [
    "list_pricing_parameter_profiles_tool",
    "get_pricing_parameter_profile_tool",
    "create_pricing_parameter_profile_tool",
    "update_pricing_parameter_profile_tool",
    "upsert_pricing_parameter_rows_tool",
    "delete_pricing_parameter_rows_tool",
    "delete_pricing_parameter_profile_tool",
]
```

- [ ] **Step 10.4: Run to verify pass** — `python -m pytest tests/test_tools_pricing_profiles.py -q`
- [ ] **Step 10.5: Commit**

```bash
git add backend/app/tools/pricing_profiles.py tests/test_tools_pricing_profiles.py
git commit -m "feat(tools): pricing parameter profile CRUD tool surface"
```

---

### Task 11: Assumption tools (3 reads + 2 writes)

**Files:**
- Create: `backend/app/tools/assumptions.py`
- Test: `tests/test_tools_assumptions.py` (new)

- [ ] **Step 11.1: Write the failing tests**

`tests/test_tools_assumptions.py`:

```python
"""Tool-layer tests for the assumption pipeline tools."""
from __future__ import annotations

import pytest

from app import database
from app.config import Settings
from app.models import Instrument, Portfolio, Position
from app.tools.assumptions import (
    build_assumption_set_tool,
    get_assumption_set_tool,
    get_instrument_pricing_defaults_tool,
    list_assumption_sets_tool,
    set_instrument_pricing_defaults_tool,
)


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()


def _seed_open_position(symbol: str) -> None:
    with database.SessionLocal() as session:
        portfolio = Portfolio(name=f"Book-{symbol}", base_currency="CNY")
        session.add(portfolio)
        session.flush()
        instrument = Instrument(symbol=symbol)
        session.add(instrument)
        session.flush()
        session.add(
            Position(
                portfolio_id=portfolio.id,
                underlying=symbol,
                underlying_id=instrument.id,
                product_type="vanilla_option",
                quantity=1.0,
                status="open",
            )
        )
        session.commit()


def test_defaults_build_list_get_pipeline_roundtrip():
    _seed_open_position("PIPE.SH")

    unfilled = build_assumption_set_tool.invoke({})
    assert unfilled == {
        "ok": False,
        "error": "unfilled_underlyings",
        "detail": {"underlyings": ["PIPE.SH"]},
    }

    set_result = set_instrument_pricing_defaults_tool.invoke(
        {"symbol": "PIPE.SH", "rate": 0.03, "dividend_yield": 0.01,
         "volatility": 0.24}
    )
    assert set_result["ok"] is True
    assert set_result["data"]["volatility"] == 0.24

    built = build_assumption_set_tool.invoke({"name": "After defaults"})
    assert built["ok"] is True
    assert built["data"]["row_count"] == 1
    assert built["data"]["rows"][0]["symbol"] == "PIPE.SH"
    assert built["data"]["rows"][0]["field_sources"]["volatility"] == "instrument_default"

    listed = list_assumption_sets_tool.invoke({})
    assert listed["ok"] is True and listed["total_count"] == 1

    fetched = get_assumption_set_tool.invoke({"set_id": built["data"]["id"]})
    assert fetched["ok"] is True
    assert fetched["data"]["rows"][0]["rate"] == 0.03

    defaults = get_instrument_pricing_defaults_tool.invoke({"symbols": ["PIPE.SH"]})
    assert defaults["ok"] is True
    assert defaults["data"][0]["rate"] == 0.03


def test_structured_refusals():
    assert get_assumption_set_tool.invoke({"set_id": 999}) == {
        "ok": False, "error": "set_not_found", "detail": {"set_id": 999},
    }
    conflict = set_instrument_pricing_defaults_tool.invoke(
        {"symbol": "X.SH", "rate": 0.02, "clear": ["rate"]}
    )
    assert conflict["ok"] is False
    assert conflict["error"] == "field_set_and_cleared"
    nothing = build_assumption_set_tool.invoke({})
    assert nothing == {"ok": False, "error": "no_open_positions"}
```

- [ ] **Step 11.2: Run to verify failure**, then **Step 11.3: Implement**

`backend/app/tools/assumptions.py`:

```python
"""@tool wrappers for the assumption pipeline (instrument defaults -> built sets).

Pipeline-only by design: there is NO direct AssumptionRow write tool, so
provenance in source_payload always reflects a real build.
"""
from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup
from app.services.domains import assumptions as assumptions_svc
from app.services.domains._errors import DomainWriteError

from ._shaping import (
    domain_write_error_response,
    parse_valuation_date,
    shape_assumption_set,
    shape_instrument_defaults,
)


class ListAssumptionSetsInput(BaseModel):
    query: str | None = Field(
        default=None,
        description="Optional case-insensitive substring over name/status/valuation date.",
    )
    limit: int = Field(default=20, ge=1, le=100)


class GetAssumptionSetInput(BaseModel):
    set_id: int


class GetInstrumentPricingDefaultsInput(BaseModel):
    symbols: list[str] | None = Field(
        default=None, description="Filter to these symbols; omit for all."
    )
    limit: int = Field(default=50, ge=1, le=200)


class SetInstrumentPricingDefaultsInput(BaseModel):
    symbol: str = Field(min_length=1)
    rate: float | None = None
    dividend_yield: float | None = None
    volatility: float | None = Field(default=None, description="Annualized, e.g. 0.22.")
    clear: list[str] = Field(
        default_factory=list,
        description="Fields to null out: rate|dividend_yield|volatility. A field "
        "cannot be both set and cleared.",
    )


class BuildAssumptionSetInput(BaseModel):
    name: str | None = Field(default=None, description="Defaults to 'Assumptions <ts>'.")
    valuation_date: str | None = Field(default=None, description="ISO datetime; defaults to now.")


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_assumption_sets", args_schema=ListAssumptionSetsInput)
def list_assumption_sets_tool(
    query: str | None = None, limit: int = 20
) -> dict[str, Any]:
    """List stored instrument-keyed assumption sets, newest first."""
    rows = assumptions_svc.list_sets(query=query, limit=limit)
    return {
        "ok": True,
        "data": [shape_assumption_set(s) for s in rows],
        "total_count": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_assumption_set", args_schema=GetAssumptionSetInput)
def get_assumption_set_tool(set_id: int) -> dict[str, Any]:
    """Fetch one assumption set with rows + per-field provenance."""
    assumption_set = assumptions_svc.get_set(set_id=set_id)
    if assumption_set is None:
        return {"ok": False, "error": "set_not_found", "detail": {"set_id": set_id}}
    return {"ok": True, "data": shape_assumption_set(assumption_set, include_rows=True)}


@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("get_instrument_pricing_defaults", args_schema=GetInstrumentPricingDefaultsInput)
def get_instrument_pricing_defaults_tool(
    symbols: list[str] | None = None, limit: int = 50
) -> dict[str, Any]:
    """Instrument baseline r/q/vol defaults (first source the assumption build
    resolves)."""
    rows = assumptions_svc.get_instrument_defaults(symbols=symbols, limit=limit)
    return {
        "ok": True,
        "data": [shape_instrument_defaults(i) for i in rows],
        "total_count": len(rows),
    }


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("set_instrument_pricing_defaults", args_schema=SetInstrumentPricingDefaultsInput)
def set_instrument_pricing_defaults_tool(
    symbol: str,
    rate: float | None = None,
    dividend_yield: float | None = None,
    volatility: float | None = None,
    clear: list[str] | None = None,
) -> dict[str, Any]:
    """Set/clear an instrument's baseline r/q/vol; run build_assumption_set
    afterwards to materialize. HITL — requires confirmation."""
    try:
        instrument = assumptions_svc.set_instrument_defaults(
            symbol=symbol,
            rate=rate,
            dividend_yield=dividend_yield,
            volatility=volatility,
            clear=clear or [],
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": shape_instrument_defaults(instrument)}


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("build_assumption_set", args_schema=BuildAssumptionSetInput)
def build_assumption_set_tool(
    name: str | None = None, valuation_date: str | None = None
) -> dict[str, Any]:
    """Rebuild the canonical assumption set from open-position scope. On
    unfilled_underlyings, set those instruments' defaults and retry.
    HITL — requires confirmation."""
    try:
        assumption_set = assumptions_svc.build_set(
            name=name, valuation_date=parse_valuation_date(valuation_date)
        )
    except DomainWriteError as exc:
        return domain_write_error_response(exc)
    return {"ok": True, "data": shape_assumption_set(assumption_set, include_rows=True)}


__all__ = [
    "list_assumption_sets_tool",
    "get_assumption_set_tool",
    "get_instrument_pricing_defaults_tool",
    "set_instrument_pricing_defaults_tool",
    "build_assumption_set_tool",
]
```

- [ ] **Step 11.4: Run to verify pass** — `python -m pytest tests/test_tools_assumptions.py -q`
- [ ] **Step 11.5: Commit**

```bash
git add backend/app/tools/assumptions.py tests/test_tools_assumptions.py
git commit -m "feat(tools): assumption pipeline tool surface (defaults + build + reads)"
```

---

### Task 12: Registry + capability-assignment tests

**Files:**
- Modify: `backend/app/tools/__init__.py`
- Modify: `tests/test_capability_assignments.py`

- [ ] **Step 12.1: Update the failing test FIRST** (`tests/test_capability_assignments.py`)

Replace the count test body:

```python
def test_quant_agent_tools_count_unchanged():
    """Keep the exposed tool registry intentional as new gated tools land."""
    # 80 == 69 prior + 11 pricing-parameter tools (4 reads, 7 HITL writes,
    # spec docs/superpowers/specs/2026-06-05-pricing-parameter-tools-design.md).
    assert len(QUANT_AGENT_TOOLS) == 80
```

Append to the `test_spot_check_assignments` parametrize list:

```python
        # Pricing parameter tools: profile CRUD + assumption pipeline.
        ("get_pricing_parameter_profile", ToolGroup.DOMAIN_READ),
        ("list_assumption_sets", ToolGroup.DOMAIN_READ),
        ("get_instrument_pricing_defaults", ToolGroup.DOMAIN_READ),
        ("create_pricing_parameter_profile", ToolGroup.DOMAIN_WRITE),
        ("delete_pricing_parameter_profile", ToolGroup.DOMAIN_WRITE),
        ("set_instrument_pricing_defaults", ToolGroup.DOMAIN_WRITE),
        ("build_assumption_set", ToolGroup.DOMAIN_WRITE),
```

- [ ] **Step 12.2: Run to verify failure** — `python -m pytest tests/test_capability_assignments.py -q` (count 69 != 80, missing tools)

- [ ] **Step 12.3: Register in `backend/app/tools/__init__.py`**

Update the import from `.pricing_profiles`:

```python
from .pricing_profiles import (
    create_pricing_parameter_profile_tool,
    delete_pricing_parameter_profile_tool,
    delete_pricing_parameter_rows_tool,
    get_pricing_parameter_profile_tool,
    list_pricing_parameter_profiles_tool,
    update_pricing_parameter_profile_tool,
    upsert_pricing_parameter_rows_tool,
)
from .assumptions import (
    build_assumption_set_tool,
    get_assumption_set_tool,
    get_instrument_pricing_defaults_tool,
    list_assumption_sets_tool,
    set_instrument_pricing_defaults_tool,
)
```

In `QUANT_AGENT_TOOLS`, directly after the existing `list_pricing_parameter_profiles_tool,` line add the reads:

```python
    get_pricing_parameter_profile_tool,
    list_assumption_sets_tool,
    get_assumption_set_tool,
    get_instrument_pricing_defaults_tool,
```

After the `create_report_tool,` line in the HITL section add the writes:

```python
    # Pricing parameter writes (persisted / HITL-gated):
    create_pricing_parameter_profile_tool,
    update_pricing_parameter_profile_tool,
    upsert_pricing_parameter_rows_tool,
    delete_pricing_parameter_rows_tool,
    delete_pricing_parameter_profile_tool,
    set_instrument_pricing_defaults_tool,
    build_assumption_set_tool,
```

- [ ] **Step 12.4: Run to verify pass** — `python -m pytest tests/test_capability_assignments.py tests/test_capability_gate.py -q`
- [ ] **Step 12.5: Commit**

```bash
git add backend/app/tools/__init__.py tests/test_capability_assignments.py
git commit -m "feat(tools): register 11 pricing-parameter tools in QUANT_AGENT_TOOLS (69->80)"
```

---

### Task 13: HITL wiring

**Files:**
- Modify: `backend/app/services/deep_agent/hitl.py`
- Modify: `tests/test_hitl.py`

- [ ] **Step 13.1: Update the exact-set test FIRST** — in `tests/test_hitl.py::test_interrupt_tool_names_covers_all_state_mutating_tools`, add to the set literal:

```python
        "create_pricing_parameter_profile",
        "update_pricing_parameter_profile",
        "upsert_pricing_parameter_rows",
        "delete_pricing_parameter_rows",
        "delete_pricing_parameter_profile",
        "set_instrument_pricing_defaults",
        "build_assumption_set",
```

- [ ] **Step 13.2: Run to verify failure** — `python -m pytest tests/test_hitl.py -q`

- [ ] **Step 13.3: Implement in `hitl.py`** — three edits:

`INTERRUPT_TOOL_NAMES`, after `"remove_portfolio_sources",`:

```python
    "create_pricing_parameter_profile",
    "update_pricing_parameter_profile",
    "upsert_pricing_parameter_rows",
    "delete_pricing_parameter_rows",
    "delete_pricing_parameter_profile",
    "set_instrument_pricing_defaults",
    "build_assumption_set",
```

`_RISK_LEVEL_BY_TOOL`, after the portfolio block:

```python
    # Pricing parameter writes are reversible (delete/upsert exist) — "write"
    # level. Profile delete is the exception: rows are gone for good.
    "create_pricing_parameter_profile": "write",
    "update_pricing_parameter_profile": "write",
    "upsert_pricing_parameter_rows": "write",
    "delete_pricing_parameter_rows": "write",
    "delete_pricing_parameter_profile": "irreversible",
    "set_instrument_pricing_defaults": "write",
    "build_assumption_set": "write",
```

`_LABEL_BY_TOOL`:

```python
    "create_pricing_parameter_profile": "Create pricing profile",
    "update_pricing_parameter_profile": "Update pricing profile",
    "upsert_pricing_parameter_rows": "Upsert pricing profile rows",
    "delete_pricing_parameter_rows": "Delete pricing profile rows",
    "delete_pricing_parameter_profile": "Delete pricing profile",
    "set_instrument_pricing_defaults": "Set instrument pricing defaults",
    "build_assumption_set": "Build assumption set",
```

- [ ] **Step 13.4: Run to verify pass** — `python -m pytest tests/test_hitl.py -q`
- [ ] **Step 13.5: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py tests/test_hitl.py
git commit -m "feat(hitl): gate 7 pricing-parameter writes; profile delete is irreversible"
```

---

### Task 14: Workflow skill + reference doc + catalog tests

**Files:**
- Create: `backend/app/skills/workflows/pricing/pricing-parameter-maintenance/SKILL.md`
- Create: `backend/app/skills/references/pricing/parameters.md`
- Modify: `tests/test_skills_catalog.py`, `tests/test_skills_catalog_v2.py`

- [ ] **Step 14.1: Update catalog tests FIRST**

`tests/test_skills_catalog.py::test_production_composite_backend_resolves_workflow_prefix` — pricing set becomes:

```python
    assert _names(_list_skills(backend, "/skills/workflows/pricing/")) == {
        "price-product",
        "price-portfolio",
        "pricing-parameter-maintenance",
    }
```

`tests/test_skills_catalog_v2.py::test_all_workflow_domains_have_expected_skills`:

```python
        "/workflows/pricing/": {
            "price-product",
            "price-portfolio",
            "pricing-parameter-maintenance",
        },
```

`test_trader_total_workflow_catalog`: `assert len(catalog) == 23` (comment: `# 22 + pricing-parameter-maintenance`). `test_risk_manager_total_workflow_catalog`: `assert len(catalog) == 22` (same comment).

- [ ] **Step 14.2: Run to verify failure** — `python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py -q`

- [ ] **Step 14.3: Create the SKILL.md** with EXACTLY the content in spec section "Workflow skill (full sketch)" (`docs/superpowers/specs/2026-06-05-pricing-parameter-tools-design.md`). Do not paraphrase — copy the fenced markdown body from the spec.

- [ ] **Step 14.4: Create `backend/app/skills/references/pricing/parameters.md`:**

```markdown
# Pricing parameter model

Two stores feed pricing. Per-field resolution order in the pricer:
override -> pricing-parameter-profile row -> assumption-set row -> missing.

## PricingParameterProfile (trade-keyed; xlsx-imported or agent-created)

- Rows carry (source_trade_id, symbol, rate, dividend_yield, volatility).
- Row matching for a position: exact source_trade_id first; otherwise a
  UNIQUE COMPLETE row for the underlying. A row missing any of r/q/vol is
  "incomplete" and refused — what-if rows must carry all three fields
  (copy current values for the ones you are not changing).
- Empty source_trade_id = underlying-level row; trade-id matching only
  fires for positions that themselves carry a trade id.
- source_type: xlsx (imported), agent (agent-created),
  default_underlying_archived (read-only migration artifacts — never edit,
  never delete; historical runs reference them).
- Spots are NOT stored here. Observations live in the quote store.

## AssumptionSet (instrument-keyed; derived-only)

- Built from open-position scope: Instrument defaults resolve first, then
  the latest PricingParameterRow per underlying; per-field provenance is
  recorded in each row's source_payload.
- Never write AssumptionRows directly — set instrument defaults, rebuild.
- build refuses with unfilled_underlyings when any open underlying still
  misses a field after resolution: set those defaults, retry.

## Consuming a profile

price_positions / run_risk accept pricing_parameter_profile_id; run
diagnostics record market_input_source per field, so attribution is
verifiable after the run.
```

- [ ] **Step 14.5: Run catalog + lint tests**

Run: `python -m pytest tests/test_skills_catalog.py tests/test_skills_catalog_v2.py tests/test_skill_lint.py tests/test_skill_lint_ci.py tests/test_workflow_skills_phase3.py -q`
Expected: PASS. If `body_length` lint fails (>500 tokens), trim the SKILL.md "When to use" bullets and Example — do NOT cut the Routing decision or Stop conditions.

- [ ] **Step 14.6: Commit**

```bash
git add backend/app/skills tests/test_skills_catalog.py tests/test_skills_catalog_v2.py
git commit -m "feat(skills): pricing-parameter-maintenance workflow skill owns the 7 writes"
```

---

### Task 15: End-to-end characterization

**Files:**
- Modify: `tests/test_position_import_pricing.py` (append; reuse its helpers)

- [ ] **Step 15.1: Write the test** (append; add `from app.services.domains import pricing_profiles as pricing_profiles_domain` to the imports)

```python
def test_agent_created_profile_prices_positions_end_to_end(tmp_path: Path):
    """Characterization: the previously-blocked flow — agent-created profile
    consumed by the pricer with per-field attribution. Values are deliberately
    NON-default so attribution cannot pass vacuously."""
    xlsx_path = tmp_path / "trades.xlsx"
    write_trade_workbook(xlsx_path, [vanilla_row()])
    session = configure_test_db(tmp_path)
    portfolio = Portfolio(name="Agent Profile Book", base_currency="CNY")
    session.add(portfolio)
    session.commit()
    import_positions_from_xlsx(session, portfolio_id=portfolio.id, xlsx_path=xlsx_path)
    position = session.query(Position).filter_by(source_trade_id="T-VANILLA").one()
    record_quote(
        session,
        instrument_id=position.underlying_id,
        price=100.0,
        as_of=datetime(2026, 4, 30),
        source="xlsx_import",
        price_type="mid",
    )
    session.commit()

    profile = pricing_profiles_domain.create_profile(
        session=session,
        rows=[{
            "symbol": position.underlying,
            "source_trade_id": "",  # underlying-level what-if row
            "rate": 0.037,
            "dividend_yield": 0.013,
            "volatility": 0.31,
        }],
    )

    run = price_portfolio_positions(
        session,
        portfolio_id=portfolio.id,
        valuation_date=datetime(2026, 4, 30),
        pricing_parameter_profile_id=profile.id,
    )
    session.commit()

    result = session.query(PositionValuationResult).filter_by(
        valuation_run_id=run.id
    ).one()
    assert result.ok, result.error
    assert result.market_inputs["rate"] == 0.037
    assert result.market_inputs["dividend_yield"] == 0.013
    assert result.market_inputs["volatility"] == 0.31
    assert result.market_inputs["market_input_source"] == "pricing_parameter_profile"
    assert result.market_inputs["pricing_parameter_profile_id"] == profile.id
    assert result.market_inputs["pricing_parameter_match_type"] == "underlying"
```

(If the diagnostics key for match type differs, read the keys the existing
`test_pricing_profile_precedence_between_profile_and_overrides` asserts at
`tests/test_position_import_pricing.py:703` and match them — adjust the LAST
assertion only; the value/source assertions are the point of the test.)

- [ ] **Step 15.2: Run** — `python -m pytest tests/test_position_import_pricing.py::test_agent_created_profile_prices_positions_end_to_end -q`
Expected: PASS

- [ ] **Step 15.3: Commit**

```bash
git add tests/test_position_import_pricing.py
git commit -m "test(pricing): e2e — agent-created profile resolves through the pricer"
```

---

### Task 16: Full verification

- [ ] **Step 16.1: Run the full backend suite**

Run: `python -m pytest tests/ -q`
Expected: same pass/fail profile as the pre-Task-1 baseline plus all new tests green. Compare failures against the baseline — only pre-existing env failures are acceptable; anything new is yours to fix before proceeding.

- [ ] **Step 16.2: Grep for leftovers**

Run: `grep -rn "TODO\|XXX" backend/app/tools/assumptions.py backend/app/tools/pricing_profiles.py backend/app/services/domains/assumptions.py backend/app/services/domains/_errors.py`
Expected: no output.

- [ ] **Step 16.3: Final commit if anything is uncommitted, then hand off** per superpowers:finishing-a-development-branch (merge target: `main`).

---

## Plan self-review (done at write time)

- **Spec coverage:** 11 tools (T10/T11), guards incl. archived + FK delete (T2–T5), pipeline-only assumption writes (T6–T8), HITL three-structure wiring (T13), capability registry 69→80 (T12), workflow skill + reference + catalog test updates (T14), e2e with non-default values (T15), audit events with actor="agent" (T1–T8). Out-of-scope items (spots, REST, frontend, direct AssumptionRow writes) have no tasks — correct.
- **Placeholder scan:** clean; the two "if model differs, check line N" notes are verification instructions with exact fallback files, not deferred design.
- **Type consistency:** `DomainWriteError(error, detail)` used uniformly; `create_profile/update_profile/upsert_rows/delete_rows/delete_profile` and `list_sets/get_set/get_instrument_defaults/set_instrument_defaults/build_set` signatures match between service tasks and tool tasks; `parse_valuation_date`/`domain_write_error_response` defined in T9, consumed in T10/T11.
