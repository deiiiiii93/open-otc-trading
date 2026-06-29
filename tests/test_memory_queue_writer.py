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
