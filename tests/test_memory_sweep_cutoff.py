# tests/test_memory_sweep_cutoff.py
"""First-enable cutoff: the reconciliation sweep must not mass-extract the
historical backlog of already-closed sessions when memory is first enabled on
an existing DB. MemoryConfig.reconcile_since gates the closed-session discovery
loop by AgentSession.closed_at."""
from datetime import datetime

from app.models import AgentSession, Workflow
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.queue import MemoryWriteQueue
from app.services.deep_agent.memory.runs import ExtractionRunStore
from app.services.deep_agent.memory.store import MemoryStore


def _make_closed_session(session, thread_id, persona, closed_at, key):
    wf = Workflow(thread_id=thread_id, title="t", intent="chat")
    session.add(wf)
    session.flush()
    s = AgentSession(workflow_id=wf.id, persona=persona, episode_id=1,
                     status="closed", checkpointer_key=key, closed_at=closed_at)
    session.add(s)
    session.flush()
    return s


def _queue(cfg):
    # session_factory=None: enqueue's _ensure_writer returns early, so no writer
    # thread starts and sweep's enqueues stay inspectable in q._normal.
    return MemoryWriteQueue(
        cfg, MemoryStore(cfg), ExtractionRunStore(cfg),
        session_factory=None, window_loader=None, extractor_llm=None,
        portfolio_resolver=lambda s, sid: None)


def _enqueued_session_ids(q):
    return {job.spec.session_id for job in q._normal.values()}


def test_sweep_cutoff_excludes_sessions_closed_before(session, agent_thread_factory):
    thread = agent_thread_factory()
    old = _make_closed_session(session, thread.id, "trader", datetime(2026, 1, 1), "k-old")
    new = _make_closed_session(session, thread.id, "trader", datetime(2026, 6, 30), "k-new")
    session.commit()

    q = _queue(MemoryConfig(reconcile_since=datetime(2026, 6, 1)))
    q.sweep(session)

    ids = _enqueued_session_ids(q)
    assert new.id in ids
    assert old.id not in ids


def test_sweep_without_cutoff_reconciles_all_closed(session, agent_thread_factory):
    thread = agent_thread_factory()
    a = _make_closed_session(session, thread.id, "trader", datetime(2026, 1, 1), "k-a")
    b = _make_closed_session(session, thread.id, "trader", datetime(2026, 6, 30), "k-b")
    session.commit()

    q = _queue(MemoryConfig(reconcile_since=None))
    q.sweep(session)

    ids = _enqueued_session_ids(q)
    assert a.id in ids and b.id in ids


def test_sweep_cutoff_excludes_null_closed_at(session, agent_thread_factory):
    """Legacy closed sessions with no closed_at are conservatively excluded
    (closed_at >= cutoff is NULL-false in SQL)."""
    thread = agent_thread_factory()
    s = _make_closed_session(session, thread.id, "trader", None, "k-null")
    session.commit()

    q = _queue(MemoryConfig(reconcile_since=datetime(2026, 6, 1)))
    q.sweep(session)

    assert s.id not in _enqueued_session_ids(q)


def test_sweep_cutoff_is_inclusive(session, agent_thread_factory):
    """A session closed exactly at the cutoff instant is reconciled (>=)."""
    thread = agent_thread_factory()
    cutoff = datetime(2026, 6, 1, 9, 0, 0)
    s = _make_closed_session(session, thread.id, "trader", cutoff, "k-eq")
    session.commit()

    q = _queue(MemoryConfig(reconcile_since=cutoff))
    q.sweep(session)

    assert s.id in _enqueued_session_ids(q)
