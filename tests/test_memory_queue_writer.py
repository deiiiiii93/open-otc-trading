# tests/test_memory_queue_writer.py
import threading
import time

from app import database
from app.models import Workflow, AgentSession, MemoryExtractionRun
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import MemoryStore
from app.services.deep_agent.memory.runs import ExtractionRunStore, RunSpec, session_run_key
from app.services.deep_agent.memory.queue import MemoryWriteQueue, QueueJob


def _spec(sid):
    return RunSpec(run_key=session_run_key(sid), kind="session", session_id=sid,
                   thread_id=1, persona="trader", book_scope_id=None, trigger_message_id=None)


def _queue(called: threading.Event | None = None):
    cfg = MemoryConfig(sweep_interval_seconds=1)
    def llm(prompt):
        if called is not None:
            called.set()
        return '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}'
    return MemoryWriteQueue(
        cfg, MemoryStore(cfg), ExtractionRunStore(cfg),
        session_factory=lambda: database.SessionLocal(),
        window_loader=lambda sid, after, c: [{"id": 1, "role": "user", "content": "I book in USD"}],
        extractor_llm=llm, portfolio_resolver=lambda s, sid: None)


def test_enqueue_starts_writer_and_processes(session):
    called = threading.Event()
    q = _queue(called)
    try:
        q.enqueue(QueueJob(_spec(21), "normal"))
        assert q._writer is not None and q._writer.is_alive()
        assert called.wait(timeout=3.0) is True
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            with database.SessionLocal() as s:
                run = s.get(MemoryExtractionRun, "session:21")
                if run is not None and run.status == "succeeded":
                    break
            time.sleep(0.05)
        with database.SessionLocal() as s:
            assert s.get(MemoryExtractionRun, "session:21").status == "succeeded"
    finally:
        q.close()


def test_sweep_reenqueues_closed_session_with_thread_id(session, agent_thread_factory):
    # Create a throwaway thread first so thread.id=2 and wf.id=1 — making
    # the `thread_id != wf.id` assertion distinguishable.
    agent_thread_factory()
    thread = agent_thread_factory()
    wf = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf); session.flush()
    s = AgentSession(workflow_id=wf.id, persona="trader", episode_id=1,
                     status="closed", checkpointer_key="ksw")
    session.add(s); session.commit()
    q = _queue()
    q._ensure_writer = lambda: None   # deterministic: no background drain races the assert
    try:
        with database.SessionLocal() as s2:
            added = q.sweep(s2); s2.commit()
        assert added >= 1
        job = q._next_job()
        assert job is not None and job.spec.session_id == s.id
        # thread_id is the AgentThread id (workflow.thread_id), NOT the workflow id
        assert job.spec.thread_id == thread.id
        assert job.spec.thread_id != wf.id
    finally:
        q.close()


def test_sweep_closed_session_skipped_when_run_exists(session, agent_thread_factory):
    """Closed-session scan must NOT enqueue sessions that already have a run row.

    A pending (in-flight) or failed run is owned by the eligible_runs path; the
    closed-session scan handles ONLY sessions with no run row yet.
    We stub out eligible_runs→[] so only the closed-session-scan path contributes
    jobs, letting us assert exactly what it enqueues.
    """
    from unittest.mock import patch
    thread = agent_thread_factory()

    # Session A — closed, NO run row → must be enqueued by closed-session scan.
    wf_a = Workflow(thread_id=thread.id, title="t", intent="chat")
    session.add(wf_a); session.flush()
    s_a = AgentSession(workflow_id=wf_a.id, persona="trader", episode_id=10,
                       status="closed", checkpointer_key="ka")
    session.add(s_a); session.flush()

    # Session B — closed, pending run → must NOT be enqueued by closed-session scan.
    wf_b = Workflow(thread_id=thread.id, title="t2", intent="chat")
    session.add(wf_b); session.flush()
    s_b = AgentSession(workflow_id=wf_b.id, persona="trader", episode_id=11,
                       status="closed", checkpointer_key="kb")
    session.add(s_b); session.flush()
    from app.services.deep_agent.memory.runs import session_run_key as srk
    run_b = MemoryExtractionRun(run_key=srk(s_b.id), kind="session",
                                session_id=s_b.id, thread_id=thread.id,
                                persona="trader", status="pending", attempts=0)
    session.add(run_b)

    # Session C — closed, failed run (within budget) → must NOT be enqueued by closed-session scan.
    wf_c = Workflow(thread_id=thread.id, title="t3", intent="chat")
    session.add(wf_c); session.flush()
    s_c = AgentSession(workflow_id=wf_c.id, persona="trader", episode_id=12,
                       status="closed", checkpointer_key="kc")
    session.add(s_c); session.flush()
    run_c = MemoryExtractionRun(run_key=srk(s_c.id), kind="session",
                                session_id=s_c.id, thread_id=thread.id,
                                persona="trader", status="failed", attempts=1)
    session.add(run_c)
    session.commit()

    q = _queue()
    q._ensure_writer = lambda: None   # deterministic: no background races

    # Stub eligible_runs to return nothing so only the closed-session scan
    # contributes — lets us assert the scan's exact enqueue behaviour.
    with patch.object(q.runs, "eligible_runs", return_value=[]):
        try:
            with database.SessionLocal() as s2:
                q.sweep(s2); s2.commit()

            # Drain all enqueued normal jobs.
            enqueued_session_ids = set()
            while True:
                job = q._next_job()
                if job is None:
                    break
                enqueued_session_ids.add(job.spec.session_id)

            # Session A (no run) must be enqueued.
            assert s_a.id in enqueued_session_ids, "session with no run row must be enqueued"
            # Sessions B & C (existing run) must NOT be enqueued by the closed-session scan.
            assert s_b.id not in enqueued_session_ids, "session with pending run must not be re-enqueued by closed-scan"
            assert s_c.id not in enqueued_session_ids, "session with failed run must not be re-enqueued by closed-scan"
        finally:
            q.close()


def test_flush_drains(session):
    q = _queue()
    try:
        with database.SessionLocal() as s:
            q.runs.enqueue_run(s, _spec(22)); s.commit()
        # do not start writer; enqueue then flush synchronously
        q._accepting = True
        q._normal.clear()
        from app.services.deep_agent.memory.queue import QueueJob as J
        q._normal[(1, "trader", 22)] = J(_spec(22), "normal")
        q.flush(grace=2.0)
        with database.SessionLocal() as s:
            assert s.get(MemoryExtractionRun, "session:22").status == "succeeded"
    finally:
        q.close()
