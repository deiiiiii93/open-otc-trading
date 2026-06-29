# tests/test_memory_queue_enqueue.py
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.runs import RunSpec, session_run_key, correction_run_key
from app.services.deep_agent.memory.queue import (
    MemoryWriteQueue, QueueJob, memory_write_session,
)


def _spec(sid, kind="session", tmid=None):
    key = session_run_key(sid) if kind == "session" else correction_run_key(sid, tmid)
    return RunSpec(run_key=key, kind=kind, session_id=sid, thread_id=1,
                   persona="trader", book_scope_id=None, trigger_message_id=tmid)


def _queue(cfg=None):
    cfg = cfg or MemoryConfig()
    return MemoryWriteQueue(cfg, store=None, runs=None, session_factory=None,
                            window_loader=None, extractor_llm=None, portfolio_resolver=None)


def test_coalesce_normal_by_key():
    q = _queue()
    assert q.enqueue(QueueJob(_spec(1), "normal")) is True
    assert q.enqueue(QueueJob(_spec(1), "normal")) is True
    assert q.pending_normal_count() == 1


def test_high_dedupe_by_run_key():
    q = _queue()
    q.enqueue(QueueJob(_spec(3, kind="correction", tmid=9), "high"))
    q.enqueue(QueueJob(_spec(3, kind="correction", tmid=9), "high"))  # same run_key
    assert q.pending_high_count() == 1


def test_high_overflow_sheds_and_counts():
    q = _queue(MemoryConfig(max_high_queue_size=4))
    for i in range(9):
        q.enqueue(QueueJob(_spec(i, kind="correction", tmid=i), "high"))
    assert q.pending_high_count() == 4
    assert q.counters["high_shed"] >= 5


def test_fairness_four_high_then_one_normal():
    q = _queue()
    for i in range(6):
        q.enqueue(QueueJob(_spec(100 + i, kind="correction", tmid=i), "high"))
    q.enqueue(QueueJob(_spec(1), "normal"))
    picked = [q._next_job() for _ in range(6)]
    priorities = [j.priority for j in picked if j is not None]
    assert priorities[:5] == ["high", "high", "high", "high", "normal"]


def test_memory_write_session_sets_busy_timeout(session):
    from app import database
    from sqlalchemy import text
    with memory_write_session(lambda: database.SessionLocal(), 2000) as s:
        val = s.execute(text("PRAGMA busy_timeout")).scalar()
    assert int(val) == 2000
