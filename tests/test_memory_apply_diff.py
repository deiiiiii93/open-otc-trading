import pytest
from app.models import MemoryEntry
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.extractor import MemoryDiff
from app.services.deep_agent.memory.store import MemoryStore, WriteContext


@pytest.fixture
def store():
    return MemoryStore(MemoryConfig())


def _active(session, scope_type, scope_id):
    return (session.query(MemoryEntry)
            .filter_by(scope_type=scope_type, scope_id=scope_id, status="active").all())


def test_routes_and_drops_out_of_allowlist(session, store):
    diff = MemoryDiff(add=[
        {"content": "books in USD", "scope_type": "user", "confidence": 0.9},
        {"content": "secret book detail", "scope_type": "book", "confidence": 0.9},
    ])
    store.apply_diff(session, diff, WriteContext(allowed_scopes=["user", "correction", "domain"]))
    assert len(_active(session, "user", "desk")) == 1
    assert _active(session, "book", "1") == []


def test_correction_forces_source_error(session, store):
    diff = MemoryDiff(add=[{"content": "do not use ACT/365 for CNH",
                            "scope_type": "correction", "confidence": 1.0}])
    store.apply_diff(session, diff, WriteContext(allowed_scopes=["correction"]))
    rows = _active(session, "correction", "desk")
    assert len(rows) == 1 and rows[0].source_error is True


def test_cap_evicts_lowest_confidence(session, store):
    diff = MemoryDiff(add=[
        {"content": f"avoid mistake number {i}", "scope_type": "correction",
         "confidence": 0.70 + i * 0.01} for i in range(21)])
    store.apply_diff(session, diff, WriteContext(allowed_scopes=["correction"]))
    rows = _active(session, "correction", "desk")
    assert len(rows) == 20
    assert all("number 0" not in r.content for r in rows)


def test_does_not_evict_pinned(session, store):
    pinned = store.create(session, scope_type="correction", scope_id="desk",
                          content="pinned correction keep me", created_by="api")
    diff = MemoryDiff(add=[
        {"content": f"avoid case {i}", "scope_type": "correction", "confidence": 0.71}
        for i in range(25)])
    store.apply_diff(session, diff, WriteContext(allowed_scopes=["correction"]))
    survivors = _active(session, "correction", "desk")
    assert any(r.id == pinned.id for r in survivors)


def test_apply_diff_atomic_rollback(session, store, monkeypatch):
    # Force an unexpected failure during caps; assert NO facts persist.
    def boom(*a, **k):
        raise RuntimeError("eviction blew up")
    monkeypatch.setattr(store, "_enforce_caps", boom)
    diff = MemoryDiff(add=[{"content": "books in USD", "scope_type": "user", "confidence": 0.9}])
    with pytest.raises(RuntimeError):
        store.apply_diff(session, diff, WriteContext(allowed_scopes=["user"]))
    assert session.query(MemoryEntry).count() == 0  # rolled back to savepoint


def test_apply_diff_cannot_mutate_other_book_same_scope_type(session, store):
    # A NON-pinned fact in book "2"; the job is scoped to book "1". Only the
    # scope_id guard (not the pinned guard) can stop the mutation here.
    from app.services.deep_agent.memory.normalize import normalize_content
    other = MemoryEntry(scope_type="book", scope_id="2", content="book two convention",
                        normalized_content=normalize_content("book two convention"),
                        confidence=1.0, status="active", source_error=False,
                        created_by="extractor", pinned=False, meta={})
    session.add(other); session.flush()
    diff = MemoryDiff(remove=[other.id],
                      update=[{"id": other.id, "content": "hijacked content here"}])
    store.apply_diff(session, diff,
                     WriteContext(allowed_scopes=["book"], book_scope_id="1"))
    session.refresh(other)
    assert other.status == "active"                   # not archived
    assert other.content == "book two convention"     # not updated
