"""Append-only SQLite trace store with a background writer thread.

Separate DB file from the business database on purpose: independent
backup/retention, no alembic coupling, and high-volume span writes never
contend with business tables. Append-only convention: each span row is
INSERTed at start and finalized exactly once at end — there is no other
mutation path and no delete API. Tracing must never break an agent run:
writer failures log a warning and drop the span.
"""
from __future__ import annotations

import logging
import queue
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trace_runs (
    id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL,
    parent_run_id TEXT,
    dotted_order TEXT NOT NULL,
    thread_id INTEGER,
    task_id INTEGER,
    workflow_id INTEGER,
    message_id INTEGER,
    name TEXT NOT NULL,
    run_type TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    inputs TEXT,
    outputs TEXT,
    error TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS ix_trace_runs_thread_start
    ON trace_runs (thread_id, start_time);
CREATE INDEX IF NOT EXISTS ix_trace_runs_trace ON trace_runs (trace_id);
CREATE INDEX IF NOT EXISTS ix_trace_runs_parent ON trace_runs (parent_run_id);
"""


@dataclass(frozen=True)
class SpanStart:
    id: str
    trace_id: str
    parent_run_id: str | None
    dotted_order: str
    thread_id: int | None
    task_id: int | None
    workflow_id: int | None
    message_id: int | None
    name: str
    run_type: str
    start_time: str
    inputs: str | None
    extra: str | None


@dataclass(frozen=True)
class SpanEnd:
    id: str
    trace_id: str
    parent_run_id: str | None
    end_time: str
    status: str
    outputs: str | None
    error: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


_SENTINEL = object()


class TraceStore:
    def __init__(self, db_path: str | Path):
        self._db_path = Path(db_path)
        self._queue: queue.Queue[Any] = queue.Queue()
        self._disabled = False
        self._writer: threading.Thread | None = None
        self._start_lock = threading.Lock()

    # -- write path -------------------------------------------------------

    def enqueue_insert(self, record: SpanStart) -> None:
        self._ensure_writer()
        if not self._disabled:
            self._queue.put(record)

    def enqueue_finalize(self, record: SpanEnd) -> None:
        self._ensure_writer()
        if not self._disabled:
            self._queue.put(record)

    def flush(self) -> None:
        """Block until every enqueued record has been applied (tests/shutdown)."""
        self._queue.join()

    def close(self) -> None:
        if self._writer is not None and self._writer.is_alive():
            self._queue.put(_SENTINEL)
            self._writer.join(timeout=5)

    def _ensure_writer(self) -> None:
        if self._writer is not None or self._disabled:
            return
        with self._start_lock:
            if self._writer is not None or self._disabled:
                return
            try:
                self._db_path.parent.mkdir(parents=True, exist_ok=True)
                with self._connect() as conn:
                    conn.executescript(_SCHEMA)
            except Exception:
                logger.error(
                    "Trace store unavailable at %s — local tracing disabled",
                    self._db_path,
                    exc_info=True,
                )
                self._disabled = True
                return
            self._writer = threading.Thread(
                target=self._writer_loop, name="trace-store-writer", daemon=True
            )
            self._writer.start()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _writer_loop(self) -> None:
        conn = self._connect()
        while True:
            item = self._queue.get()
            try:
                if item is _SENTINEL:
                    return
                if isinstance(item, SpanStart):
                    self._apply_insert(conn, item)
                elif isinstance(item, SpanEnd):
                    self._apply_finalize(conn, item)
            except Exception:
                logger.warning("Dropping trace span (writer failure)", exc_info=True)
            finally:
                self._queue.task_done()

    @staticmethod
    def _apply_insert(conn: sqlite3.Connection, r: SpanStart) -> None:
        conn.execute(
            """INSERT OR IGNORE INTO trace_runs
               (id, trace_id, parent_run_id, dotted_order, thread_id, task_id,
                workflow_id, message_id, name, run_type, start_time, inputs, extra)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (r.id, r.trace_id, r.parent_run_id, r.dotted_order, r.thread_id,
             r.task_id, r.workflow_id, r.message_id, r.name, r.run_type,
             r.start_time, r.inputs, r.extra),
        )
        conn.commit()

    @staticmethod
    def _apply_finalize(conn: sqlite3.Connection, r: SpanEnd) -> None:
        conn.execute(
            """UPDATE trace_runs
               SET end_time=?, status=?, outputs=?, error=?,
                   prompt_tokens=?, completion_tokens=?, total_tokens=?
               WHERE id=? AND end_time IS NULL""",
            (r.end_time, r.status, r.outputs, r.error,
             r.prompt_tokens, r.completion_tokens, r.total_tokens, r.id),
        )
        if r.parent_run_id is None:
            # Root finalize: aggregate descendant token usage for cheap
            # per-trace totals in list views.
            conn.execute(
                """UPDATE trace_runs SET
                     prompt_tokens=(SELECT SUM(prompt_tokens) FROM trace_runs
                                    WHERE trace_id=? AND parent_run_id IS NOT NULL),
                     completion_tokens=(SELECT SUM(completion_tokens) FROM trace_runs
                                    WHERE trace_id=? AND parent_run_id IS NOT NULL),
                     total_tokens=(SELECT SUM(total_tokens) FROM trace_runs
                                    WHERE trace_id=? AND parent_run_id IS NOT NULL)
                   WHERE id=?""",
                (r.trace_id, r.trace_id, r.trace_id, r.id),
            )
        conn.commit()

    # -- read path ---------------------------------------------------------

    def _read_conn(self) -> sqlite3.Connection | None:
        if self._disabled or not self._db_path.exists():
            return None
        conn = sqlite3.connect(self._db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        conn = self._read_conn()
        if conn is None:
            return None
        try:
            row = conn.execute(
                "SELECT * FROM trace_runs WHERE id=?", (run_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def get_trace(self, trace_id: str) -> list[dict[str, Any]]:
        conn = self._read_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                "SELECT * FROM trace_runs WHERE trace_id=? ORDER BY dotted_order",
                (trace_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_thread_traces(
        self, thread_id: int, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        conn = self._read_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                """SELECT * FROM trace_runs
                   WHERE thread_id=? AND parent_run_id IS NULL
                   ORDER BY start_time DESC LIMIT ? OFFSET ?""",
                (thread_id, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_recent_traces(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        conn = self._read_conn()
        if conn is None:
            return []
        try:
            rows = conn.execute(
                """SELECT * FROM trace_runs WHERE parent_run_id IS NULL
                   ORDER BY start_time DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


_STORE_CACHE: dict[str, TraceStore] = {}
_CACHE_LOCK = threading.Lock()


def get_trace_store(settings) -> TraceStore:
    """Process-wide store per DB path (one writer thread per file)."""
    key = str(Path(settings.trace_db_path).resolve())
    with _CACHE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = TraceStore(key)
            _STORE_CACHE[key] = store
        return store


def reset_trace_store_cache() -> None:
    """Tests only: close and forget cached stores."""
    with _CACHE_LOCK:
        for store in _STORE_CACHE.values():
            store.close()
        _STORE_CACHE.clear()
