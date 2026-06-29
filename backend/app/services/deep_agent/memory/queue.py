"""Background write queue (spec §Queue, §Write path)."""
from __future__ import annotations

import collections
import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text

from .config import MemoryConfig
from .extractor import MalformedDiffError, extract_facts
from .runs import RunSpec, session_run_key
from .scope import active_write_scopes
from .store import WriteContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueueJob:
    spec: RunSpec
    priority: str  # "high" | "normal"


@contextlib.contextmanager
def memory_write_session(session_factory, busy_timeout_ms: int):
    session = session_factory()
    try:
        session.execute(text(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}"))
        yield session
    finally:
        session.close()


class MemoryWriteQueue:
    def __init__(self, config: MemoryConfig, store, runs, *, session_factory,
                 window_loader: Callable | None, extractor_llm: Callable | None,
                 portfolio_resolver: Callable | None) -> None:
        self.config = config
        self.store = store
        self.runs = runs
        self._session_factory = session_factory
        self._window_loader = window_loader
        self._llm = extractor_llm
        self._resolve_book = portfolio_resolver
        self._high: collections.deque[QueueJob] = collections.deque()
        self._high_keys: set[str] = set()
        self._normal: "collections.OrderedDict[tuple, QueueJob]" = collections.OrderedDict()
        self._lock = threading.Lock()
        self._writer: threading.Thread | None = None
        self._stop = threading.Event()
        self._accepting = True
        self._high_cycle = 0
        self.counters: dict[str, int] = collections.defaultdict(int)

    def sweep(self, session) -> int:
        from app.models import AgentSession, MemoryExtractionRun, Workflow

        count = 0
        for run in self.runs.eligible_runs(session):
            self.enqueue(QueueJob(RunSpec(
                run_key=run.run_key, kind=run.kind, session_id=run.session_id,
                thread_id=run.thread_id, persona=run.persona,
                book_scope_id=run.book_scope_id,
                trigger_message_id=run.trigger_message_id),
                "high" if run.kind == "correction" else "normal"))
            count += 1
        closed = (session.query(AgentSession)
                  .filter(AgentSession.status.in_(("closed", "archived"))).all())
        for s in closed:
            key = session_run_key(s.id)
            run = session.get(MemoryExtractionRun, key)
            if run is not None:
                continue  # any existing run (pending/failed/succeeded) is owned by eligible_runs
            wf = session.get(Workflow, s.workflow_id)
            thread_id = wf.thread_id if wf is not None else None   # AgentThread id, not workflow id
            book = self._resolve_book(session, s.id) if self._resolve_book else None
            self.enqueue(QueueJob(RunSpec(
                run_key=key, kind="session", session_id=s.id,
                thread_id=thread_id, persona=s.persona,
                book_scope_id=book, trigger_message_id=None), "normal"))
            count += 1
        return count

    def _run_sweep(self) -> None:
        with memory_write_session(self._session_factory,
                                  self.config.writer_busy_timeout_ms) as session:
            try:
                self.sweep(session)
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
                logger.warning("memory sweep failed", exc_info=True)

    def _ensure_writer(self) -> None:
        if self._writer is not None or not self._accepting or self._session_factory is None:
            return
        with self._lock:
            if self._writer is not None:
                return
            self._writer = threading.Thread(target=self._loop, name="memory-writer",
                                            daemon=True)
            self._writer.start()

    def _loop(self) -> None:
        self._run_sweep()  # initial reconciliation sweep on start
        last_sweep = time.monotonic()
        while not self._stop.is_set():
            did = self.process_one()
            now = time.monotonic()
            if now - last_sweep >= self.config.sweep_interval_seconds:
                self._run_sweep()
                last_sweep = now
            if not did:
                time.sleep(0.02)

    def flush(self, *, grace: float | None = None) -> None:
        self._accepting = False
        budget = grace if grace is not None else self.config.shutdown_grace_seconds
        deadline = time.monotonic() + budget
        while time.monotonic() < deadline and self.process_one():
            pass

    def close(self) -> None:
        self._stop.set()
        if self._writer is not None and self._writer.is_alive():
            self._writer.join(timeout=5)

    @staticmethod
    def _normal_key(spec: RunSpec):
        return (spec.thread_id, spec.persona, spec.session_id)

    def enqueue(self, job: QueueJob) -> bool:
        if not self._accepting:
            return False
        with self._lock:
            if job.priority == "high":
                if job.spec.run_key in self._high_keys:
                    return True  # dedupe: already queued
                if len(self._high) >= self.config.max_high_queue_size:
                    shed = self._high.popleft()
                    self._high_keys.discard(shed.spec.run_key)
                    self.counters["high_shed"] += 1
                    logger.warning("memory high queue overflow; shed (run persists)")
                self._high.append(job)
                self._high_keys.add(job.spec.run_key)
            else:
                key = self._normal_key(job.spec)
                if key in self._normal:
                    self._normal[key] = job
                else:
                    if len(self._normal) >= self.config.max_queue_size:
                        self._normal.popitem(last=False)
                        self.counters["normal_shed"] += 1
                        logger.warning("memory normal queue overflow; shed (run persists)")
                    self._normal[key] = job
        self._ensure_writer()
        return True

    def pending_normal_count(self) -> int:
        with self._lock:
            return len(self._normal)

    def pending_high_count(self) -> int:
        with self._lock:
            return len(self._high)

    def run_job(self, session, spec: RunSpec) -> None:
        # enqueue_run returns False for a TERMINAL run (succeeded, or failed at
        # max_extract_attempts). Honor it: do not load a window, call the LLM,
        # or apply anything — the run is done until a human resets it.
        if not self.runs.enqueue_run(session, spec):
            return
        run = self.runs.get(session, spec.run_key)
        if run is None or run.status == "succeeded":
            return
        cursor = run.last_extracted_message_id
        try:
            window = self._window_loader(spec.session_id, cursor, self.config)
        except Exception as exc:  # noqa: BLE001 — any window-loader failure
            self.counters["window_failed"] += 1
            self.runs.mark_failed(session, spec.run_key, f"window: {exc}")
            return
        if window is None:
            self.counters["window_failed"] += 1
            self.runs.mark_failed(session, spec.run_key, "window retrieval failed")
            return
        book = ("book", spec.book_scope_id) if spec.book_scope_id else None
        allowed = ["correction"] if spec.kind == "correction" else active_write_scopes(book)
        existing = []
        for scope_type in allowed:
            scope_id = ("desk" if scope_type in ("user", "correction")
                        else "global" if scope_type == "domain" else spec.book_scope_id)
            if scope_id is None:
                continue
            existing.extend(self.store.load_existing(session, scope_type, scope_id))
        try:
            diff = extract_facts(window, existing, allowed, llm=self._llm, config=self.config)
        except MalformedDiffError:
            self.counters["malformed_diff"] += 1
            self.runs.mark_failed(session, spec.run_key, "malformed diff")
            return
        except Exception as exc:  # noqa: BLE001 — LLM/network/other extraction failure
            self.counters["extract_failed"] += 1
            self.runs.mark_failed(session, spec.run_key, f"extract: {exc}")
            return
        ctx = WriteContext(allowed_scopes=allowed, book_scope_id=spec.book_scope_id,
                           created_by="extractor",
                           meta={"session_id": spec.session_id, "thread_id": spec.thread_id,
                                 "persona": spec.persona,
                                 "extractor_model": self.config.extractor_model})
        try:
            self.store.apply_diff(session, diff, ctx)
        except Exception as exc:  # noqa: BLE001 — facts already rolled back by savepoint
            self.counters["apply_failed"] += 1
            self.runs.mark_failed(session, spec.run_key, f"apply: {exc}")
            return
        last_id = max((m.get("id") for m in window if isinstance(m.get("id"), int)),
                      default=cursor)
        self.runs.mark_succeeded(session, spec.run_key, last_id)

    def process_one(self) -> bool:
        job = self._next_job()
        if job is None:
            return False
        with memory_write_session(self._session_factory,
                                  self.config.writer_busy_timeout_ms) as session:
            try:
                self.run_job(session, job.spec)
                session.commit()
            except Exception:  # noqa: BLE001
                session.rollback()
                logger.warning("memory job crashed; left for sweep", exc_info=True)
        return True

    def _next_job(self) -> QueueJob | None:
        with self._lock:
            if self._high and self._high_cycle < 4:
                self._high_cycle += 1
                job = self._high.popleft()
                self._high_keys.discard(job.spec.run_key)
                return job
            self._high_cycle = 0
            if self._normal:
                _key, job = self._normal.popitem(last=False)
                return job
            if self._high:
                self._high_cycle += 1
                job = self._high.popleft()
                self._high_keys.discard(job.spec.run_key)
                return job
            return None
