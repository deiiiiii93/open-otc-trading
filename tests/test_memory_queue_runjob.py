# tests/test_memory_queue_runjob.py
from app import database
from app.models import MemoryEntry, MemoryExtractionRun
from app.services.deep_agent.memory.config import MemoryConfig
from app.services.deep_agent.memory.store import MemoryStore
from app.services.deep_agent.memory.runs import ExtractionRunStore, RunSpec, session_run_key
from app.services.deep_agent.memory.queue import MemoryWriteQueue


def _spec(sid):
    return RunSpec(run_key=session_run_key(sid), kind="session", session_id=sid,
                   thread_id=1, persona="trader", book_scope_id=None, trigger_message_id=None)


def _queue(*, window=None, llm=None):
    cfg = MemoryConfig()
    return MemoryWriteQueue(
        cfg, MemoryStore(cfg), ExtractionRunStore(cfg),
        session_factory=lambda: database.SessionLocal(),
        window_loader=lambda sid, after, c: (window if window is not None else
                                             [{"id": 1, "role": "user", "content": "I book in USD"}]),
        extractor_llm=lambda p: (llm if llm is not None else
                                 '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}'),
        portfolio_resolver=lambda s, sid: None)


def test_run_job_applies_and_marks_succeeded(session):
    q = _queue()
    with database.SessionLocal() as s:
        q.run_job(s, _spec(7)); s.commit()
    with database.SessionLocal() as s:
        assert s.query(MemoryEntry).filter_by(scope_type="user", status="active").count() == 1
        assert s.get(MemoryExtractionRun, "session:7").status == "succeeded"


def test_run_job_zero_facts_still_succeeds(session):
    q = _queue(llm='{"add": []}')
    with database.SessionLocal() as s:
        q.run_job(s, _spec(8)); s.commit()
    with database.SessionLocal() as s:
        assert s.get(MemoryExtractionRun, "session:8").status == "succeeded"


def test_run_job_malformed_marks_failed(session):
    q = _queue(llm="garbage not json")
    with database.SessionLocal() as s:
        q.run_job(s, _spec(9)); s.commit()
    with database.SessionLocal() as s:
        run = s.get(MemoryExtractionRun, "session:9")
        assert run.status == "failed" and run.attempts == 1
    assert q.counters["malformed_diff"] >= 1


def test_run_job_window_failure_marks_failed(session):
    q = _queue(window=None)  # window_loader returns None
    q._window_loader = lambda sid, after, c: None
    with database.SessionLocal() as s:
        q.run_job(s, _spec(10)); s.commit()
    with database.SessionLocal() as s:
        assert s.get(MemoryExtractionRun, "session:10").status == "failed"
    assert q.counters["window_failed"] >= 1


def test_run_job_window_exception_marks_failed(session):
    q = _queue()
    def boom(sid, after, c):
        raise RuntimeError("checkpointer down")
    q._window_loader = boom
    with database.SessionLocal() as s:
        q.run_job(s, _spec(12)); s.commit()
    with database.SessionLocal() as s:
        assert s.get(MemoryExtractionRun, "session:12").status == "failed"
    assert q.counters["window_failed"] >= 1


def test_run_job_llm_exception_marks_failed(session):
    q = _queue()
    def boom(prompt):
        raise RuntimeError("LLM 500")
    q._llm = boom
    with database.SessionLocal() as s:
        q.run_job(s, _spec(13)); s.commit()
    with database.SessionLocal() as s:
        assert s.get(MemoryExtractionRun, "session:13").status == "failed"
    assert q.counters["extract_failed"] >= 1


def test_run_job_skips_terminal_failed_run(session):
    calls = {"window": 0}
    q = _queue()
    def counting_window(sid, after, c):
        calls["window"] += 1
        return [{"id": 1, "role": "user", "content": "x"}]
    q._window_loader = counting_window
    # drive the run to failed-at-max (3 attempts) so enqueue_run() returns False
    with database.SessionLocal() as s:
        q.runs.enqueue_run(s, _spec(14))
        for _ in range(3):
            s.get(MemoryExtractionRun, "session:14").status = "pending"
            q.runs.mark_failed(s, "session:14", "boom")
        s.commit()
    with database.SessionLocal() as s:
        q.run_job(s, _spec(14)); s.commit()
    assert calls["window"] == 0  # terminal run -> no window load, no extraction
    with database.SessionLocal() as s:
        run = s.get(MemoryExtractionRun, "session:14")
        assert run.status == "failed" and run.attempts == 3
        assert s.query(MemoryEntry).count() == 0


def test_process_one_commits_and_uses_writer_busy_timeout(session):
    q = _queue()
    with database.SessionLocal() as s:
        q.runs.enqueue_run(s, _spec(11)); s.commit()
    from app.services.deep_agent.memory.queue import QueueJob
    q.enqueue(QueueJob(_spec(11), "normal"))
    assert q.process_one() is True
    with database.SessionLocal() as s:
        assert s.get(MemoryExtractionRun, "session:11").status == "succeeded"
