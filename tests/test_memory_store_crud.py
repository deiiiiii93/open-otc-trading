import pytest
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import (
    MemoryStore, MemoryValidationError, MemoryConflictError, MemoryNotFound,
)


@pytest.fixture
def store():
    return MemoryStore(MemoryConfig())


def test_create_sets_source_error_and_status(session, store):
    f = store.create(session, scope_type="correction", scope_id="desk",
                     content="do not assume ACT/365")
    assert f.source_error is True and f.status == "active"
    u = store.create(session, scope_type="user", scope_id="desk", content="books in USD")
    assert u.source_error is False


def test_create_domain_defaults_proposed(session, store):
    f = store.create(session, scope_type="domain", scope_id="global",
                     content="CNH uses ACT/365 fixings")
    assert f.status == "proposed" and f.pinned is False


def test_create_below_floor_rejected(session, store):
    with pytest.raises(MemoryValidationError):
        store.create(session, scope_type="user", scope_id="desk",
                     content="weak", confidence=0.5)


def test_create_dedup_conflict(session, store):
    store.create(session, scope_type="user", scope_id="desk", content="Books in USD")
    with pytest.raises(MemoryConflictError):
        store.create(session, scope_type="user", scope_id="desk", content="books   in usd")


def test_create_api_sets_pinned(session, store):
    f = store.create(session, scope_type="user", scope_id="desk",
                     content="hedges net delta", created_by="api")
    assert f.pinned is True


def test_create_denylist_rejected(session, store):
    with pytest.raises(MemoryValidationError):
        store.create(session, scope_type="user", scope_id="desk",
                     content="api_key: sk-ABCDEF0123456789ABCD")


def test_create_invalid_scope_type_rejected(session, store):
    with pytest.raises(MemoryValidationError):
        store.create(session, scope_type="bogus", scope_id="desk",
                     content="some durable content here")


def test_validate_scope_status_matrix():
    from app.services.deep_agent.memory.store import (
        _validate_scope_status, MemoryValidationError,
    )
    with pytest.raises(MemoryValidationError):
        _validate_scope_status("user", "proposed")    # non-domain proposed
    with pytest.raises(MemoryValidationError):
        _validate_scope_status("domain", "active")     # domain active
    _validate_scope_status("domain", "proposed")       # ok (no raise)
    _validate_scope_status("user", "active")           # ok (no raise)


def test_update_preserves_status_and_revalidates(session, store):
    f = store.create(session, scope_type="domain", scope_id="global",
                     content="cnh fixings act/365")
    u = store.update(session, f.id, content="CNH fixings use ACT/365 convention")
    assert u.status == "proposed"
    with pytest.raises(MemoryValidationError):
        store.update(session, f.id, confidence=0.1)


def test_update_missing_404(session, store):
    with pytest.raises(MemoryNotFound):
        store.update(session, 99999, content="nothing here at all")


def test_approve_makes_domain_pinned(session, store):
    f = store.create(session, scope_type="domain", scope_id="global",
                     content="snowball KO observed monthly")
    a = store.set_status(session, f.id, "approved")
    assert a.status == "approved" and a.pinned is True


def test_approve_non_domain_conflict(session, store):
    f = store.create(session, scope_type="user", scope_id="desk", content="x books usd")
    with pytest.raises(MemoryConflictError):
        store.set_status(session, f.id, "approved")


def test_archive_idempotent(session, store):
    f = store.create(session, scope_type="user", scope_id="desk", content="net delta hedger")
    assert store.archive(session, f.id) is True
    assert store.archive(session, f.id) is True


def test_create_over_cap_pinned_overflow_counter(session):
    store = MemoryStore(MemoryConfig(max_facts_per_scope=2))
    for i in range(3):
        store.create(session, scope_type="user", scope_id="desk",
                     content=f"stable preference number {i}")
    # all api-created => pinned => never evicted; cap path runs and counts overflow.
    from app.models import MemoryEntry
    active = session.query(MemoryEntry).filter_by(scope_type="user", status="active").count()
    assert active == 3
    assert store.counters["memory_cap_pinned_overflow"] >= 1


def test_load_injectable_eligibility(session, store):
    store.create(session, scope_type="user", scope_id="desk", content="books in USD")
    store.create(session, scope_type="domain", scope_id="global", content="cnh act/365")
    facts = store.load_injectable(session, [("user", "desk"), ("domain", "global")])
    contents = {f.content for f in facts}
    assert "books in USD" in contents
    assert "cnh act/365" not in contents  # proposed, not approved


def _seed_raw(session, **kw):
    from app.models import MemoryEntry
    from app.services.deep_agent.memory.normalize import normalize_content
    row = MemoryEntry(normalized_content=normalize_content(kw["content"]), meta={}, **kw)
    session.add(row); session.flush()
    return row


def test_update_normalizes_source_error(session, store):
    row = _seed_raw(session, scope_type="user", scope_id="desk", content="books in usd",
                    confidence=1.0, status="active", source_error=True, created_by="api", pinned=False)
    store.update(session, row.id, content="books all trades in USD")
    session.refresh(row)
    assert row.source_error is False  # user can never be source_error


def test_set_status_normalizes_source_error(session, store):
    row = _seed_raw(session, scope_type="correction", scope_id="desk", content="avoid act/365 cnh",
                    confidence=1.0, status="active", source_error=False, created_by="api", pinned=False)
    store.set_status(session, row.id, "archived")
    session.refresh(row)
    assert row.source_error is True  # correction is always source_error


def test_archive_normalizes_source_error(session, store):
    row = _seed_raw(session, scope_type="user", scope_id="desk", content="hedges net delta",
                    confidence=1.0, status="active", source_error=True, created_by="api", pinned=False)
    store.archive(session, row.id)
    session.refresh(row)
    assert row.source_error is False


def test_cap_evicts_non_pinned_when_pinned_count_equals_cap(session):
    store = MemoryStore(MemoryConfig(max_facts_per_scope=2))
    # 2 pinned (api) + 2 non-pinned (extractor) = 4; cap 2; pinned_count == cap.
    p1 = store.create(session, scope_type="user", scope_id="desk", content="pinned pref one")
    p2 = store.create(session, scope_type="user", scope_id="desk", content="pinned pref two")
    n1 = _seed_raw(session, scope_type="user", scope_id="desk", content="weak extracted one",
                   confidence=0.71, status="active", source_error=False, created_by="extractor", pinned=False)
    n2 = _seed_raw(session, scope_type="user", scope_id="desk", content="weak extracted two",
                   confidence=0.72, status="active", source_error=False, created_by="extractor", pinned=False)
    store._enforce_caps(session, "user", "desk")
    from app.models import MemoryEntry
    active_ids = {r.id for r in session.query(MemoryEntry).filter_by(status="active").all()}
    # both pinned survive; both non-pinned evicted; back at cap; no overflow.
    assert {p1.id, p2.id} <= active_ids
    assert n1.id not in active_ids and n2.id not in active_ids
    assert store.counters["memory_cap_pinned_overflow"] == 0


# --- Memory Console additions (provenance + set_pinned + archived read-only) ---

def test_to_fact_exposes_created_by_and_meta(session):
    from app.models import MemoryEntry
    from app.services.deep_agent.memory.store import _to_fact
    row = MemoryEntry(scope_type="user", scope_id="desk", content="x",
                      normalized_content="x", confidence=0.9, status="active",
                      created_by="extractor",
                      meta={"extractor_model": "deepseek/deepseek-v4-flash", "session_id": 318})
    session.add(row); session.flush()
    fact = _to_fact(row)
    assert fact.created_by == "extractor"
    assert fact.meta == {"extractor_model": "deepseek/deepseek-v4-flash", "session_id": 318}


def test_set_pinned_round_trip(session, store):
    f = store.create(session, scope_type="user", scope_id="desk", content="pin me",
                     confidence=0.9, category=None, created_by="api")
    assert store.set_pinned(session, f.id, False).pinned is False
    assert store.set_pinned(session, f.id, True).pinned is True


def test_set_pinned_missing_raises(session, store):
    with pytest.raises(MemoryNotFound):
        store.set_pinned(session, 999999, True)


def test_archived_is_read_only(session, store):
    f = store.create(session, scope_type="user", scope_id="desk", content="archive me",
                     confidence=0.9, category=None, created_by="api")
    store.archive(session, f.id)
    with pytest.raises(MemoryConflictError):
        store.set_pinned(session, f.id, True)
    with pytest.raises(MemoryConflictError):
        store.update(session, f.id, content="new content")
    assert store.archive(session, f.id) is True


def test_set_status_on_archived_raises(session, store):
    f = store.create(session, scope_type="domain", scope_id="global", content="dom fact",
                     confidence=0.9, category=None, created_by="api")
    store.archive(session, f.id)
    with pytest.raises(MemoryConflictError):
        store.set_status(session, f.id, "approved")
