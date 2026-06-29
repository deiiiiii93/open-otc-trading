from app import database
from app.models import MemoryExtractionRun


def test_shutdown_drains_queued_job(session, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_MEMORY", "on")
    from app.services.deep_agent.memory import runtime as rt
    from app.services.deep_agent.memory.queue import QueueJob
    from app.services.deep_agent.memory.runs import RunSpec, session_run_key
    rt.reset_memory_runtime()
    monkeypatch.setattr(rt, "_extractor_llm",
                        lambda prompt: '{"add":[{"content":"books in USD","scope_type":"user","confidence":0.9}]}')
    monkeypatch.setattr(rt, "_window_loader",
                        lambda sid, after, cfg: [{"id": 1, "role": "user", "content": "book in USD"}])
    q = rt.get_memory_queue()
    q._ensure_writer = lambda: None   # no bg thread; flush() drains synchronously
    spec = RunSpec(run_key=session_run_key(41), kind="session", session_id=41,
                   thread_id=1, persona="trader", book_scope_id=None, trigger_message_id=None)
    q.enqueue(QueueJob(spec, "normal"))
    rt.shutdown_memory_runtime(grace=3.0)
    with database.SessionLocal() as s:
        assert s.get(MemoryExtractionRun, "session:41").status == "succeeded"
    rt.reset_memory_runtime()


def test_shutdown_noop_when_queue_uncreated(monkeypatch):
    from app.services.deep_agent.memory import runtime as rt
    rt.reset_memory_runtime()
    rt.shutdown_memory_runtime(grace=0.1)   # _QUEUE is None -> no creation, no thread
    assert rt._QUEUE is None
