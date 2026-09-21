"""Keep-alive scoring (spec 2026-09-21 §2). DISPLAY-ONLY (D3)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from _system_one_fakes import ScorePost
from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory import keep_alive
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.keep_alive import KEEP_ALIVE_LEVELS, age_days, score_pending
from app.services.deep_agent.memory.store import MemoryStore

NOW = datetime(2026, 9, 21, 12, 0, 0)


@pytest.fixture
def ka_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", "true")
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")


def _fact(session, content, *, scope_type="user", scope_id="desk",
          created=NOW - timedelta(days=3), **kw):
    row = MemoryEntry(
        scope_type=scope_type, scope_id=scope_id, content=content,
        normalized_content=content.lower(), confidence=kw.pop("confidence", 0.9),
        status=kw.pop("status", "active"), created_by=kw.pop("created_by", "extractor"),
        pinned=kw.pop("pinned", False), meta={}, created_at=created,
        updated_at=kw.pop("updated_at", created), **kw)
    session.add(row)
    session.commit()
    return row.id


def _row(fact_id):
    with database.SessionLocal() as s:
        return s.get(MemoryEntry, fact_id)


def _score(post, cfg=None, now=NOW):
    cfg = cfg or MemoryConfig()
    store = MemoryStore(cfg)
    scored = score_pending(lambda: database.SessionLocal(), store, cfg, post=post, now=now)
    return scored, store


def test_scoring_populates_score_confidence_and_both_timestamps(ka_env, session):
    fid = _fact(session, "the desk reports in dollars")
    scored, _ = _score(ScorePost(score=2.0, confidence=0.7))
    row = _row(fid)
    assert scored == 1
    assert row.keep_alive_score == pytest.approx(2 / 3)       # normalized over 4 levels
    assert row.keep_alive_confidence == 0.7                   # Jev's own, never derived
    assert row.keep_alive_scored_at == NOW and row.keep_alive_attempted_at == NOW
    assert row.keep_alive_unscored_reason is None


def test_question_and_state_projection(ka_env, session):
    _fact(session, "desk prefers ACT/365", category="convention", created=NOW - timedelta(days=10))
    _fact(session, "s" * 300)
    post = ScorePost()
    _score(post, MemoryConfig(keep_alive_batch=1))
    question = post.calls[0]["questions"]["keep_alive"]
    assert question["type"] == "score" and question["criteria"] == list(KEEP_ALIVE_LEVELS)
    assert post.calls[0]["state"] == {
        "fact": "desk prefers ACT/365", "scope": "user:desk", "category": "convention",
        "source": "extractor", "status": "active", "pinned": False, "age_days": 10,
        "other_facts_in_scope": ["s" * 239 + "…"],
    }


@pytest.mark.parametrize("cfg_kw, master", [
    ({}, "false"),                          # master switch off
    ({"enabled": False}, "true"),           # OPEN_OTC_MEMORY off: no side door
    ({"keep_alive_enabled": False}, "true"),
])
def test_inert_unless_all_three_switches_are_on(session, monkeypatch, cfg_kw, master):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE", master)
    monkeypatch.setenv("ZENMUX_API_KEY", "test-key")
    fid = _fact(session, "some fact")
    post = ScorePost()
    assert _score(post, MemoryConfig(**cfg_kw))[0] == 0
    assert post.calls == [] and _row(fid).keep_alive_attempted_at is None


def test_the_current_denylist_gates_the_fact_and_its_siblings(ka_env, session):
    bad = _fact(session, "api_key: abc123", created=NOW - timedelta(days=9))
    _fact(session, "desk prefers ACT/365", created=NOW - timedelta(days=5))
    post = ScorePost()
    _score(post)
    row = _row(bad)
    assert (row.keep_alive_unscored_reason, row.keep_alive_score) == ("denylist", None)
    assert row.keep_alive_attempted_at == NOW
    assert [c["state"]["fact"] for c in post.calls] == ["desk prefers ACT/365"]
    assert post.calls[0]["state"]["other_facts_in_scope"] == []


def test_age_days_boundary():
    assert age_days(NOW - timedelta(hours=23, minutes=59), NOW) == 0
    assert age_days(NOW - timedelta(hours=24), NOW) == 1
    assert age_days(NOW + timedelta(hours=1), NOW) == 0     # clock skew never goes negative


def test_a_failing_row_does_not_block_a_later_one(ka_env, session):
    first = _fact(session, "fact alpha", created=NOW - timedelta(days=9))
    later = _fact(session, "fact beta", created=NOW - timedelta(days=1))
    post = ScorePost()
    post.bad_for = {"fact alpha"}
    cfg = MemoryConfig(keep_alive_batch=1)
    _score(post, cfg)
    _score(post, cfg)
    assert _row(first).keep_alive_unscored_reason == "bad_response"
    assert _row(later).keep_alive_score is not None


@pytest.mark.parametrize("setup, reason, calls", [
    (lambda mp, post: mp.delenv("ZENMUX_API_KEY"), "no_key", 0),
    (lambda mp, post: setattr(post, "exc", TimeoutError("slow")), "timeout", 1),
    (lambda mp, post: setattr(post, "exc", RuntimeError("HTTP 503")), "http_error", 1),
])
def test_an_outage_ends_the_batch(ka_env, session, monkeypatch, setup, reason, calls):
    ids = [_fact(session, f"fact {name}", created=NOW - timedelta(days=d))
           for name, d in (("a", 3), ("b", 2), ("c", 1))]
    post = ScorePost()
    setup(monkeypatch, post)
    _, store = _score(post)
    assert len(post.calls) == calls
    assert [_row(i).keep_alive_unscored_reason for i in ids] == [reason, None, None]
    assert _row(ids[1]).keep_alive_attempted_at is None
    assert store.counters["keep_alive_failed"] == 1


def test_state_too_large_is_a_row_reason(ka_env, session, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_SYSTEM_ONE_MAX_STATE_CHARS", "1000")
    big = _fact(session, "x" * 1500, created=NOW - timedelta(days=3))
    ok1 = _fact(session, "fact one", created=NOW - timedelta(days=2))
    ok2 = _fact(session, "fact two", created=NOW - timedelta(days=1))
    post = ScorePost()
    _score(post)
    assert _row(big).keep_alive_unscored_reason == "state_too_large"
    assert _row(ok1).keep_alive_score is not None and _row(ok2).keep_alive_score is not None
    assert len(post.calls) == 2


def test_bad_response_is_a_row_reason(ka_env, session):
    a = _fact(session, "fact alpha", created=NOW - timedelta(days=2))
    b = _fact(session, "fact beta", created=NOW - timedelta(days=1))
    post = ScorePost()
    post.bad_for = {"fact alpha"}
    _score(post)
    assert _row(a).keep_alive_unscored_reason == "bad_response"
    assert _row(b).keep_alive_score is not None


def test_a_later_success_clears_the_reason(ka_env, session):
    fid = _fact(session, "fact alpha")
    post = ScorePost()
    post.exc = RuntimeError("HTTP 503")
    _score(post)
    assert _row(fid).keep_alive_unscored_reason == "http_error"
    post.exc = None
    _score(post, now=NOW + timedelta(minutes=1))
    row = _row(fid)
    assert row.keep_alive_unscored_reason is None and row.keep_alive_score is not None


def test_a_score_older_than_the_refresh_window_is_due_again(ka_env, session):
    _fact(session, "fact old", keep_alive_score=0.5, keep_alive_scored_at=NOW - timedelta(days=31))
    _fact(session, "fact fresh", keep_alive_score=0.5, keep_alive_scored_at=NOW - timedelta(days=29))
    post = ScorePost()
    _score(post)
    assert [c["state"]["fact"] for c in post.calls] == ["fact old"]


def test_archived_facts_are_never_scored(ka_env, session):
    _fact(session, "gone", status="archived")
    post = ScorePost()
    assert _score(post)[0] == 0 and post.calls == []


def test_an_exception_is_counted_and_never_raised(ka_env, session, monkeypatch):
    fid = _fact(session, "fact alpha")

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(keep_alive, "build_state", boom)
    _, store = _score(ScorePost())
    row = _row(fid)
    assert (row.keep_alive_unscored_reason, row.keep_alive_score) == ("internal_error", None)
    assert store.counters["keep_alive_failed"] == 1


def test_scoring_never_moves_updated_at_or_injection_order(ka_env, session):
    a = _fact(session, "fact alpha", confidence=0.9, created=NOW - timedelta(days=2))
    _fact(session, "fact beta", confidence=0.9, created=NOW - timedelta(days=1))
    store = MemoryStore(MemoryConfig())
    with database.SessionLocal() as s:
        before = [f.id for f in store.load_injectable(s, [("user", "desk")])]
    _score(ScorePost())
    with database.SessionLocal() as s:
        after = [f.id for f in store.load_injectable(s, [("user", "desk")])]
    assert after == before
    assert _row(a).updated_at == NOW - timedelta(days=2)


def test_eviction_order_ignores_keep_alive(ka_env, session):
    low = _fact(session, "fact low", confidence=0.75, keep_alive_score=1.0)
    high = _fact(session, "fact high", confidence=0.95, keep_alive_score=0.0)
    cfg = MemoryConfig(max_facts_per_scope=2)
    with database.SessionLocal() as s:
        MemoryStore(cfg).create(s, scope_type="user", scope_id="desk", content="fact new",
                                confidence=0.9, created_by="extractor")
        s.commit()
    assert _row(low).status == "archived"
    assert _row(high).status == "active"
