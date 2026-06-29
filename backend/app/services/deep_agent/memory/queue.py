"""Background write queue (spec §Queue, §Write path)."""
from __future__ import annotations

import collections
import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text

from .config import MemoryConfig
from .runs import RunSpec

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

    def _ensure_writer(self) -> None:
        # Task 13 replaces this with real lazy-thread start. No-op until then.
        return

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
        return len(self._normal)

    def pending_high_count(self) -> int:
        return len(self._high)

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
