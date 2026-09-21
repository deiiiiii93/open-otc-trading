"""Scoring runs strictly AFTER a commit, never inside apply_diff's transaction,
never during the shutdown drain, and can never break the writer."""
from __future__ import annotations

import pytest

from app import database
from app.models import MemoryEntry
from app.services.deep_agent.memory import keep_alive
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.queue import MemoryWriteQueue, QueueJob
from app.services.deep_agent.memory.runs import ExtractionRunStore, RunSpec, session_run_key
from app.services.deep_agent.memory.store import MemoryStore


def _queue(monkeypatch):
    cfg = MemoryConfig()
    q = MemoryWriteQueue(
        cfg, MemoryStore(cfg), ExtractionRunStore(cfg),
        session_factory=lambda: database.SessionLocal(),
        window_loader=lambda sid, after, c: [{"id": 1, "role": "user", "content": "I book in USD"}],
        extractor_llm=lambda p: '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}',
        portfolio_resolver=lambda s, sid: None)
    monkeypatch.setattr(q, "_ensure_writer", lambda: None)   # no background thread in tests
    return q


def _job(sid):
    return QueueJob(RunSpec(run_key=session_run_key(sid), kind="session", session_id=sid,
                            thread_id=1, persona="trader", book_scope_id=None,
                            trigger_message_id=None), "normal")


@pytest.fixture
def seen(monkeypatch):
    calls = []

    def fake_score(session_factory, store, config, **kw):
        with database.SessionLocal() as s:   # a FRESH session sees only committed rows
            calls.append(s.query(MemoryEntry).count())
        return 0

    monkeypatch.setattr(keep_alive, "score_pending", fake_score)
    return calls


def test_scoring_runs_after_the_jobs_commit(session, monkeypatch, seen):
    q = _queue(monkeypatch)
    q.enqueue(_job(7))
    assert q.process_one() is True
    assert seen == [1]


def test_a_crashed_job_is_not_followed_by_scoring(session, monkeypatch, seen):
    q = _queue(monkeypatch)

    def crash(session_, spec):
        raise RuntimeError("boom")

    monkeypatch.setattr(q, "run_job", crash)
    q.enqueue(_job(8))
    assert q.process_one() is True
    assert seen == []


def test_the_sweep_scores_after_it_commits(session, monkeypatch, seen):
    _queue(monkeypatch)._run_sweep()
    assert seen == [0]


def test_no_scoring_while_draining_at_shutdown(session, monkeypatch, seen):
    q = _queue(monkeypatch)
    q.enqueue(_job(9))
    q.flush(grace=5)
    with database.SessionLocal() as s:
        assert s.query(MemoryEntry).count() == 1   # the job still ran
    assert seen == []


def test_a_raising_scorer_never_breaks_the_writer(session, monkeypatch):
    def broken(*a, **k):
        raise RuntimeError("scorer bug")

    monkeypatch.setattr(keep_alive, "score_pending", broken)
    q = _queue(monkeypatch)
    q.enqueue(_job(10))
    assert q.process_one() is True
