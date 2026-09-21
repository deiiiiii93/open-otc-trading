"""A score is invalid once the fact or its scope changes (spec §2 Invalidation)."""
from __future__ import annotations

from datetime import datetime

from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import MemoryStore, WriteContext

T0 = datetime(2026, 9, 1)
SCORED = dict(keep_alive_score=0.9, keep_alive_confidence=0.8, keep_alive_scored_at=T0,
              keep_alive_attempted_at=T0, keep_alive_unscored_reason="bad_response")
COLS = tuple(SCORED)


def _fact(session, content, scope_type="user", scope_id="desk", **kw):
    row = MemoryEntry(scope_type=scope_type, scope_id=scope_id, content=content,
                      normalized_content=content.lower(), confidence=kw.pop("confidence", 0.9),
                      status=kw.pop("status", "active"), created_by="extractor", pinned=False,
                      meta={}, created_at=T0, updated_at=T0, **{**SCORED, **kw})
    session.add(row)
    session.commit()
    return row.id


def _cols(row_id):
    with database.SessionLocal() as s:
        row = s.get(MemoryEntry, row_id)
        return {c: getattr(row, c) for c in COLS}, row.updated_at


class _Diff:
    def __init__(self, add=(), remove=(), update=()):
        self.add, self.remove, self.update = list(add), list(remove), list(update)


def test_a_content_edit_nulls_all_five(session):
    store = MemoryStore(MemoryConfig())
    fid = _fact(session, "books in USD")
    with database.SessionLocal() as s:
        store.update(s, fid, content="books everything in USD")
        s.commit()
    assert set(_cols(fid)[0].values()) == {None}


def test_a_confidence_only_edit_keeps_the_score(session):
    store = MemoryStore(MemoryConfig())
    fid = _fact(session, "books in USD")
    with database.SessionLocal() as s:
        store.update(s, fid, confidence=0.95)
        s.commit()
    assert _cols(fid)[0] == SCORED


def test_an_add_invalidates_its_scope_only_and_keeps_siblings_updated_at(session):
    store = MemoryStore(MemoryConfig())
    same = _fact(session, "prefers ACT/365 for this desk")
    archived = _fact(session, "old archived fact", status="archived")
    other = _fact(session, "book fact", scope_type="book", scope_id="7")
    with database.SessionLocal() as s:
        store.apply_diff(s, _Diff(add=[{"content": "prefers EUR reporting",
                                        "scope_type": "user", "confidence": 0.9}]),
                         WriteContext(allowed_scopes=["user"]))
        s.commit()
    cols, updated_at = _cols(same)
    assert set(cols.values()) == {None}
    assert updated_at == T0                      # D3: injection order must not move
    assert _cols(archived)[0] == SCORED          # archived facts are never re-scored
    assert _cols(other)[0] == SCORED             # other scope untouched


def test_load_existing_honours_the_limit(session):
    store = MemoryStore(MemoryConfig())
    for i in range(5):
        _fact(session, f"fact number {i}")
    with database.SessionLocal() as s:
        assert len(store.load_existing(s, "user", "desk", limit=3)) == 3
        assert len(store.load_existing(s, "user", "desk")) == 5
