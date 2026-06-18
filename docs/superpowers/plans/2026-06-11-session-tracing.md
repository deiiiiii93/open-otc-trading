# Session Tracing & Audit Module Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Self-hosted, LangSmith-style session tracing & audit: a `LocalTracer` callback handler captures every agent run (full nested span tree, prompts, tool args/results, tokens, errors) into an append-only SQLite store, switchable via `OPEN_OTC_TRACING=local|langsmith|both|off`, with a `/tracing` viewer page and a per-thread trace link in AgentDesk.

**Architecture:** A new `backend/app/services/tracing/` module hooks LangChain's callback layer (same mechanism LangSmith's `LangChainTracer` uses — callbacks propagate into subagent graphs, unlike `astream_events`). Integration is a single chokepoint: `graph_run_config()` (`backend/app/services/deep_agent/runtime_config.py`), which every agent entry point already calls, attaches the tracer callbacks + trace metadata. A FastAPI router serves traces; the frontend adds a `tracing` route with a three-pane viewer.

**Tech Stack:** Python 3.11, langchain_core 1.4.0 (`BaseTracer`), stdlib `sqlite3` + `queue`/`threading` (no SQLAlchemy/alembic for the trace DB — independent lifecycle), FastAPI, React + vitest (no URL router — `Route` string union).

**Spec:** `docs/superpowers/specs/2026-06-11-session-tracing-design.md`

---

## Repo-specific gotchas (read first)

- **Work in a git worktree.** A concurrent agent shares this repo and churns HEAD/branches. Never bare `git stash`/`pop` here.
- **`python -c` imports `app` from the MAIN checkout** (venv `.pth`), not the worktree. Run backend tests as `python -m pytest tests/...` from the worktree root (pytest config handles the path), and never verify imports with `python -c`.
- **Frontend tests in a worktree** need `node_modules` — run `npm install` (or symlink) inside `<worktree>/frontend` first if vitest can't resolve imports.
- **Pre-existing failures:** some migration/Skills tests fail on main independent of this work (see memory/engine-config notes). Before claiming regressions, compare against a baseline run of the same files on the base commit.
- **Frontend style:** `frontend/UI_STYLE_GUIDE.md` is law — zero hardcoded colors/hex/rgba; use existing `wl-*` patterns and design tokens only.
- The existing langchain version pins matter: `BaseTracer` subclass must implement abstract `_persist_run`; `dotted_order` and `trace_id` are computed for free by `_TracerCore._start_trace` (verified in installed `langchain_core/tracers/core.py:125-148`).

## File structure

```
backend/app/services/tracing/__init__.py    # re-exports: TracingMode, tracing_callbacks, get_trace_store
backend/app/services/tracing/config.py      # TracingMode enum, resolve_tracing_mode(), tracing_callbacks()
backend/app/services/tracing/store.py       # SpanStart/SpanEnd records, TraceStore, get_trace_store()
backend/app/services/tracing/tracer.py      # LocalTracer(BaseTracer), token extraction
backend/app/routers/tracing.py              # /api/tracing/* router (build_tracing_router pattern, like skills.py)
backend/app/config.py                       # + tracing_mode, trace_db_path settings        (modify)
backend/app/services/deep_agent/runtime_config.py  # + trace_meta param, attach callbacks   (modify)
backend/app/services/agents.py              # trace_meta at 2 call sites                    (modify)
backend/app/services/deep_agent/executor.py # trace_meta                                    (modify)
backend/app/services/async_agents/resume.py # trace_meta                                    (modify)
backend/app/main.py                         # trace_meta at 2 HITL resume sites + mount router (modify)
tests/conftest.py                           # default OPEN_OTC_TRACING=off for the suite    (modify)
tests/test_tracing_store.py
tests/test_tracing_tracer.py
tests/test_tracing_config.py
tests/test_tracing_runtime_config.py
tests/test_tracing_router.py
frontend/src/types.ts                       # Route + trace types                           (modify)
frontend/src/api/client.ts                  # tracing API calls                             (modify)
frontend/src/lib/tracing.ts                 # openTraceTarget() pure helper
frontend/src/lib/tracing.test.ts
frontend/src/routes/Tracing.tsx             # presentational three-pane viewer
frontend/src/routes/Tracing.css
frontend/src/routes/Tracing.test.tsx
frontend/src/routes/Tracing.live.tsx        # data-fetching wrapper
frontend/src/main.tsx                       # nav item, route render, onOpenTrace wiring    (modify)
frontend/src/routes/AgentDesk.tsx           # per-thread trace button                       (modify)
frontend/src/routes/AgentDesk.live.tsx      # forward onOpenTrace prop                      (modify)
.env.example                                # OPEN_OTC_TRACING, OPEN_OTC_TRACE_DB_PATH      (modify)
```

---

### Task 1: Settings + env plumbing

**Files:**
- Modify: `backend/app/config.py` (two places: `_EnvironmentSettings` ~line 95, `Settings` dataclass ~line 140)
- Modify: `tests/conftest.py` (top of file)
- Modify: `.env.example`
- Test: `tests/test_tracing_config.py` (settings part only)

- [ ] **Step 1: Write the failing test**

Create `tests/test_tracing_config.py`:

```python
"""Tracing settings + mode resolution + callback composition."""
from __future__ import annotations

from app.config import Settings


def test_settings_default_tracing_mode_is_off_under_pytest(monkeypatch):
    # tests/conftest.py pins OPEN_OTC_TRACING=off for the whole suite so
    # agent-driving tests don't write trace DBs. Verify the pin works.
    settings = Settings()
    assert settings.tracing_mode == "off"


def test_settings_tracing_mode_reads_env(monkeypatch):
    monkeypatch.setenv("OPEN_OTC_TRACING", "both")
    settings = Settings()
    assert settings.tracing_mode == "both"


def test_settings_trace_db_path_default_and_env(monkeypatch):
    settings = Settings()
    assert settings.trace_db_path == "./data/agent_traces.sqlite3"
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", "/tmp/x/traces.sqlite3")
    assert Settings().trace_db_path == "/tmp/x/traces.sqlite3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tracing_config.py -v`
Expected: FAIL — `Settings` has no attribute/field `tracing_mode`.

- [ ] **Step 3: Implement**

In `backend/app/config.py`, add to `_EnvironmentSettings` (after `feature_skills_write_api`):

```python
    tracing_mode: str = Field(
        "local",
        validation_alias="OPEN_OTC_TRACING",
    )
    trace_db_path: str = Field(
        "./data/agent_traces.sqlite3",
        validation_alias="OPEN_OTC_TRACE_DB_PATH",
    )
```

Add to the `Settings` dataclass (after `feature_skills_write_api`, same `_env_value` pattern as siblings):

```python
    tracing_mode: str = field(default_factory=lambda: _env_value("tracing_mode"))
    trace_db_path: str = field(default_factory=lambda: _env_value("trace_db_path"))
```

In `tests/conftest.py`, immediately after the existing imports of `os`-free stdlib — add at the very top of the import block (before `from app import database`):

```python
import os

# Default the whole suite to no tracing: agent-driving tests must not write
# trace DBs into data/. Tracing tests opt in explicitly via monkeypatch.
os.environ.setdefault("OPEN_OTC_TRACING", "off")
```

In `.env.example`, after the `LANGSMITH_PROJECT` line:

```
# Session tracing: local (self-hosted audit store, default) | langsmith | both | off
OPEN_OTC_TRACING="local"
OPEN_OTC_TRACE_DB_PATH="./data/agent_traces.sqlite3"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tracing_config.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/config.py tests/conftest.py tests/test_tracing_config.py .env.example
git commit -m "feat(tracing): settings + env plumbing for OPEN_OTC_TRACING"
```

---

### Task 2: TraceStore — append-only SQLite store with background writer

**Files:**
- Create: `backend/app/services/tracing/__init__.py`
- Create: `backend/app/services/tracing/store.py`
- Test: `tests/test_tracing_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing_store.py`:

```python
"""TraceStore: schema bootstrap, insert/finalize, reads, failure isolation."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services.tracing.store import SpanEnd, SpanStart, TraceStore


T0 = datetime(2026, 6, 11, 9, 0, 0)


def _start(run_id: str, *, trace_id: str | None = None, parent: str | None = None,
           thread_id: int | None = 7, dotted: str | None = None,
           run_type: str = "chain", name: str = "step",
           offset_s: int = 0) -> SpanStart:
    return SpanStart(
        id=run_id,
        trace_id=trace_id or run_id,
        parent_run_id=parent,
        dotted_order=dotted or f"20260611T0900000000Z{run_id}",
        thread_id=thread_id,
        task_id=None,
        workflow_id=None,
        message_id=None,
        name=name,
        run_type=run_type,
        start_time=(T0 + timedelta(seconds=offset_s)).isoformat(),
        inputs='{"q": 1}',
        extra="{}",
    )


def _end(run_id: str, *, parent: str | None = None, trace_id: str | None = None,
         status: str = "success", error: str | None = None,
         prompt_tokens: int | None = None, completion_tokens: int | None = None,
         total_tokens: int | None = None) -> SpanEnd:
    return SpanEnd(
        id=run_id,
        trace_id=trace_id or run_id,
        parent_run_id=parent,
        end_time=(T0 + timedelta(seconds=5)).isoformat(),
        status=status,
        outputs='{"a": 2}',
        error=error,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


@pytest.fixture()
def store(tmp_path):
    s = TraceStore(tmp_path / "traces.sqlite3")
    yield s
    s.close()


def test_insert_then_finalize_round_trip(store):
    store.enqueue_insert(_start("r1"))
    store.enqueue_finalize(_end("r1"))
    store.flush()
    run = store.get_run("r1")
    assert run is not None
    assert run["status"] == "success"
    assert run["inputs"] == '{"q": 1}'
    assert run["outputs"] == '{"a": 2}'
    assert run["end_time"] is not None


def test_running_row_visible_before_finalize(store):
    # Audit property: a crash mid-run still leaves evidence of the attempt.
    store.enqueue_insert(_start("r1"))
    store.flush()
    run = store.get_run("r1")
    assert run["status"] == "running"
    assert run["end_time"] is None


def test_trace_tree_ordered_by_dotted_order(store):
    store.enqueue_insert(_start("root", dotted="20260611T0900000000Zroot"))
    store.enqueue_insert(_start(
        "childB", trace_id="root", parent="root",
        dotted="20260611T0900000000Zroot.20260611T0900020000ZchildB", offset_s=2))
    store.enqueue_insert(_start(
        "childA", trace_id="root", parent="root",
        dotted="20260611T0900000000Zroot.20260611T0900010000ZchildA", offset_s=1))
    store.flush()
    runs = store.get_trace("root")
    assert [r["id"] for r in runs] == ["root", "childA", "childB"]


def test_list_thread_traces_roots_only_newest_first(store):
    store.enqueue_insert(_start("t1", offset_s=0))
    store.enqueue_insert(_start("t1c", trace_id="t1", parent="t1", offset_s=1))
    store.enqueue_insert(_start("t2", offset_s=10))
    store.enqueue_insert(_start("other", thread_id=99, offset_s=20))
    store.flush()
    traces = store.list_thread_traces(7)
    assert [t["id"] for t in traces] == ["t2", "t1"]  # roots only, newest first


def test_root_finalize_aggregates_descendant_tokens(store):
    store.enqueue_insert(_start("root"))
    store.enqueue_insert(_start("llm1", trace_id="root", parent="root", run_type="llm"))
    store.enqueue_insert(_start("llm2", trace_id="root", parent="root", run_type="llm"))
    store.enqueue_finalize(_end("llm1", parent="root", trace_id="root",
                                prompt_tokens=10, completion_tokens=5, total_tokens=15))
    store.enqueue_finalize(_end("llm2", parent="root", trace_id="root",
                                prompt_tokens=20, completion_tokens=5, total_tokens=25))
    store.enqueue_finalize(_end("root"))
    store.flush()
    root = store.get_run("root")
    assert root["total_tokens"] == 40
    assert root["prompt_tokens"] == 30


def test_error_finalize(store):
    store.enqueue_insert(_start("r1"))
    store.enqueue_finalize(_end("r1", status="error", error="Boom\ntraceback..."))
    store.flush()
    assert store.get_run("r1")["status"] == "error"
    assert "Boom" in store.get_run("r1")["error"]


def test_no_mutation_api():
    # Append-only convention: the public surface has no update/delete.
    public = {n for n in dir(TraceStore) if not n.startswith("_")}
    assert public <= {
        "enqueue_insert", "enqueue_finalize", "flush", "close",
        "get_run", "get_trace", "list_thread_traces", "list_recent_traces",
    }


def test_unopenable_db_self_disables(tmp_path, caplog):
    # A *file* where the parent dir should be makes mkdir/connect fail.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    s = TraceStore(blocker / "nested" / "traces.sqlite3")
    s.enqueue_insert(_start("r1"))  # must not raise
    s.flush()
    assert s.get_run("r1") is None
    s.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tracing_store.py -v`
Expected: FAIL — `ModuleNotFoundError: app.services.tracing`.

- [ ] **Step 3: Implement**

Create `backend/app/services/tracing/__init__.py`:

```python
from .config import TracingMode, resolve_tracing_mode, tracing_callbacks  # noqa: F401
from .store import TraceStore, get_trace_store, reset_trace_store_cache  # noqa: F401
```

(`config.py` doesn't exist until Task 4 — for this task create it as a stub so the
package imports; Task 4 fills it in:)

```python
# backend/app/services/tracing/config.py  (stub, completed in Task 4)
from __future__ import annotations

from enum import Enum


class TracingMode(str, Enum):
    LOCAL = "local"
    LANGSMITH = "langsmith"
    BOTH = "both"
    OFF = "off"


def resolve_tracing_mode(settings) -> TracingMode:  # completed in Task 4
    raise NotImplementedError


def tracing_callbacks(settings, **kwargs) -> list:  # completed in Task 4
    raise NotImplementedError
```

Create `backend/app/services/tracing/store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tracing_store.py -v`
Expected: 8 PASS. (If `test_unopenable_db_self_disables` flakes on `flush()`: the
disabled store never starts a writer, so the queue is empty and `join()` returns
immediately — that's the intended behavior; `enqueue_*` checks `_disabled` after
`_ensure_writer`.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tracing tests/test_tracing_store.py
git commit -m "feat(tracing): append-only SQLite TraceStore with background writer"
```

---

### Task 3: LocalTracer — BaseTracer subclass

**Files:**
- Create: `backend/app/services/tracing/tracer.py`
- Test: `tests/test_tracing_tracer.py`

**Background for the engineer:** `BaseTracer` (langchain_core 1.4.0) maintains the
run tree itself. `_TracerCore._start_trace` assigns `run.trace_id` and
`run.dotted_order` (`core.py:125-148`) before calling `_on_run_create(run)`;
`_end_trace` calls `_on_run_update(run)` after the run has `end_time`,
`outputs`/`error` set. `_persist_run` is abstract but only called for root runs —
we persist per-span in the two hooks instead, so it's a no-op. Chat model inputs
arrive already `dumpd`-serialized; other payloads may contain arbitrary objects,
hence `json.dumps(..., default=str)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing_tracer.py`:

```python
"""LocalTracer: real callback sequences -> persisted span tree."""
from __future__ import annotations

import uuid

import pytest

from app.services.tracing.store import TraceStore
from app.services.tracing.tracer import LocalTracer, extract_token_usage


@pytest.fixture()
def store(tmp_path):
    s = TraceStore(tmp_path / "traces.sqlite3")
    yield s
    s.close()


def _drive_nested_run(tracer: LocalTracer) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Simulate chain -> (tool, llm) via the public callback API BaseTracer exposes."""
    root_id, tool_id, llm_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    tracer.on_chain_start({"name": "orchestrator"}, {"q": "price it"}, run_id=root_id)
    tracer.on_tool_start({"name": "price_position"}, "AAPL 100", run_id=tool_id,
                         parent_run_id=root_id)
    tracer.on_tool_end("priced ok", run_id=tool_id)
    tracer.on_llm_start({"name": "ChatAnthropic"}, ["prompt text"], run_id=llm_id,
                        parent_run_id=root_id)
    from langchain_core.outputs import Generation, LLMResult
    tracer.on_llm_end(
        LLMResult(
            generations=[[Generation(text="answer")]],
            llm_output={"token_usage": {"prompt_tokens": 11,
                                        "completion_tokens": 4,
                                        "total_tokens": 15}},
        ),
        run_id=llm_id,
    )
    tracer.on_chain_end({"answer": "done"}, run_id=root_id)
    return root_id, tool_id, llm_id


def test_nested_run_tree_persisted(store):
    tracer = LocalTracer(store, thread_id=42)
    root_id, tool_id, llm_id = _drive_nested_run(tracer)
    store.flush()

    runs = store.get_trace(str(root_id))
    assert [r["run_type"] for r in runs] == ["chain", "tool", "llm"]
    by_id = {r["id"]: r for r in runs}
    assert by_id[str(tool_id)]["parent_run_id"] == str(root_id)
    assert by_id[str(root_id)]["thread_id"] == 42
    assert by_id[str(llm_id)]["thread_id"] == 42
    assert all(r["status"] == "success" for r in runs)
    assert by_id[str(llm_id)]["prompt_tokens"] == 11
    # Root aggregated descendant tokens on finalize.
    assert by_id[str(root_id)]["total_tokens"] == 15
    # Full-fidelity payloads.
    assert "price it" in by_id[str(root_id)]["inputs"]
    assert "priced ok" in by_id[str(tool_id)]["outputs"]


def test_error_run_persisted(store):
    tracer = LocalTracer(store, thread_id=1)
    root_id = uuid.uuid4()
    tracer.on_chain_start({"name": "orchestrator"}, {"q": 1}, run_id=root_id)
    tracer.on_chain_error(ValueError("bad terms"), run_id=root_id)
    store.flush()
    run = store.get_run(str(root_id))
    assert run["status"] == "error"
    assert "bad terms" in run["error"]


def test_store_failure_never_raises(store, monkeypatch):
    tracer = LocalTracer(store, thread_id=1)
    monkeypatch.setattr(store, "enqueue_insert",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("disk full")))
    # Must not propagate into the agent run.
    tracer.on_chain_start({"name": "x"}, {"q": 1}, run_id=uuid.uuid4())


def test_extract_token_usage_variants():
    assert extract_token_usage(
        {"llm_output": {"token_usage": {"prompt_tokens": 1, "completion_tokens": 2,
                                        "total_tokens": 3}}}
    ) == (1, 2, 3)
    # usage_metadata path (Anthropic-style message payload, dumpd form)
    assert extract_token_usage(
        {"generations": [[{"message": {"kwargs": {"usage_metadata": {
            "input_tokens": 5, "output_tokens": 6, "total_tokens": 11}}}}]]}
    ) == (5, 6, 11)
    assert extract_token_usage(None) == (None, None, None)
    assert extract_token_usage({"generations": [[{"text": "x"}]]}) == (None, None, None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tracing_tracer.py -v`
Expected: FAIL — no module `app.services.tracing.tracer`.

- [ ] **Step 3: Implement**

Create `backend/app/services/tracing/tracer.py`:

```python
"""LocalTracer: LangSmith's architecture pointed at our own store.

Subclasses the same ``BaseTracer`` that LangSmith's ``LangChainTracer`` uses,
so it sees every chain/LLM/tool start+end — including inside subagent graphs,
which ``astream_events`` provably does not surface to the parent. Every hook
is exception-wrapped: tracing must never break an agent run.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tracers.base import BaseTracer
from langchain_core.tracers.schemas import Run

from .store import SpanEnd, SpanStart, TraceStore

logger = logging.getLogger(__name__)


def _json(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return json.dumps(str(obj))


def extract_token_usage(
    outputs: dict[str, Any] | None,
) -> tuple[int | None, int | None, int | None]:
    """Token counts from an LLM run's outputs; handles both payload shapes."""
    if not outputs:
        return (None, None, None)
    llm_output = outputs.get("llm_output") or {}
    usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
    if usage:
        return (
            usage.get("prompt_tokens", usage.get("input_tokens")),
            usage.get("completion_tokens", usage.get("output_tokens")),
            usage.get("total_tokens"),
        )
    try:
        message = outputs["generations"][0][0]["message"]
        meta = message["kwargs"]["usage_metadata"]
        return (
            meta.get("input_tokens"),
            meta.get("output_tokens"),
            meta.get("total_tokens"),
        )
    except (KeyError, IndexError, TypeError):
        return (None, None, None)


class LocalTracer(BaseTracer):
    """Persists every span to the local trace store as it starts and ends."""

    name = "open_otc_local_tracer"
    run_inline = True  # keep callback ordering deterministic in async runs

    def __init__(
        self,
        store: TraceStore,
        *,
        thread_id: int | None = None,
        task_id: int | None = None,
        workflow_id: int | None = None,
        message_id: int | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._thread_id = thread_id
        self._task_id = task_id
        self._workflow_id = workflow_id
        self._message_id = message_id

    def _persist_run(self, run: Run) -> None:
        """Per-span persistence happens in _on_run_create/_on_run_update."""

    def _on_run_create(self, run: Run) -> None:
        try:
            self._store.enqueue_insert(
                SpanStart(
                    id=str(run.id),
                    trace_id=str(run.trace_id or run.id),
                    parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
                    dotted_order=run.dotted_order or str(run.id),
                    thread_id=self._thread_id,
                    task_id=self._task_id,
                    workflow_id=self._workflow_id,
                    message_id=self._message_id,
                    name=run.name,
                    run_type=run.run_type,
                    start_time=run.start_time.isoformat(),
                    inputs=_json(run.inputs),
                    extra=_json(run.extra),
                )
            )
        except Exception:
            logger.warning("Dropping trace span on create", exc_info=True)

    def _on_run_update(self, run: Run) -> None:
        try:
            prompt_t, completion_t, total_t = (
                extract_token_usage(run.outputs)
                if run.run_type == "llm"
                else (None, None, None)
            )
            self._store.enqueue_finalize(
                SpanEnd(
                    id=str(run.id),
                    trace_id=str(run.trace_id or run.id),
                    parent_run_id=str(run.parent_run_id) if run.parent_run_id else None,
                    end_time=run.end_time.isoformat() if run.end_time else "",
                    status="error" if run.error else "success",
                    outputs=_json(run.outputs),
                    error=run.error,
                    prompt_tokens=prompt_t,
                    completion_tokens=completion_t,
                    total_tokens=total_t,
                )
            )
        except Exception:
            logger.warning("Dropping trace span on update", exc_info=True)
```

Note: chat-model runs surface with `run_type == "llm"` in this langchain version
(`on_chat_model_start` creates an llm-type run). If the first test shows
`run_type == "chat_model"`, extend the token-extraction condition to
`run.run_type in ("llm", "chat_model")` and update the test's expected list.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tracing_tracer.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tracing/tracer.py tests/test_tracing_tracer.py
git commit -m "feat(tracing): LocalTracer BaseTracer subclass with token extraction"
```

---

### Task 4: Mode resolution + tracing_callbacks factory

**Files:**
- Modify: `backend/app/services/tracing/config.py` (replace Task 2 stub)
- Test: `tests/test_tracing_config.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_tracing_config.py`:

```python
import pytest

from app.services.tracing.config import (
    TracingMode,
    resolve_tracing_mode,
    tracing_callbacks,
)
from app.services.tracing.store import reset_trace_store_cache
from app.services.tracing.tracer import LocalTracer


@pytest.fixture(autouse=True)
def _fresh_store_cache():
    reset_trace_store_cache()
    yield
    reset_trace_store_cache()


def _settings(monkeypatch, tmp_path, mode: str):
    monkeypatch.setenv("OPEN_OTC_TRACING", mode)
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", str(tmp_path / "t.sqlite3"))
    return Settings()


@pytest.mark.parametrize("raw,expected", [
    ("local", TracingMode.LOCAL),
    ("langsmith", TracingMode.LANGSMITH),
    ("both", TracingMode.BOTH),
    ("off", TracingMode.OFF),
    ("LOCAL", TracingMode.LOCAL),       # case-insensitive
    ("nonsense", TracingMode.LOCAL),    # unknown -> local with warning
])
def test_resolve_tracing_mode(monkeypatch, tmp_path, raw, expected):
    assert resolve_tracing_mode(_settings(monkeypatch, tmp_path, raw)) is expected


def test_callbacks_off_is_empty(monkeypatch, tmp_path):
    assert tracing_callbacks(_settings(monkeypatch, tmp_path, "off")) == []


def test_callbacks_local_carries_trace_meta(monkeypatch, tmp_path):
    handlers = tracing_callbacks(
        _settings(monkeypatch, tmp_path, "local"), thread_id=7, workflow_id=3
    )
    assert len(handlers) == 1
    tracer = handlers[0]
    assert isinstance(tracer, LocalTracer)
    assert tracer._thread_id == 7
    assert tracer._workflow_id == 3


def test_callbacks_both_includes_langsmith(monkeypatch, tmp_path):
    handlers = tracing_callbacks(_settings(monkeypatch, tmp_path, "both"))
    kinds = [type(h).__name__ for h in handlers]
    assert "LocalTracer" in kinds
    assert "LangChainTracer" in kinds


def test_each_call_returns_fresh_tracer(monkeypatch, tmp_path):
    # BaseTracer keeps per-run state; concurrent runs need fresh instances
    # sharing one store (one writer thread per DB file).
    settings = _settings(monkeypatch, tmp_path, "local")
    a = tracing_callbacks(settings)[0]
    b = tracing_callbacks(settings)[0]
    assert a is not b
    assert a._store is b._store
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tracing_config.py -v`
Expected: new tests FAIL with `NotImplementedError` (stub); Task 1 tests still pass.

- [ ] **Step 3: Implement**

Replace `backend/app/services/tracing/config.py` with:

```python
"""Tracing mode resolution and callback-handler composition.

``OPEN_OTC_TRACING`` is the single authority: in ``langsmith``/``both`` mode
the ``LangChainTracer`` is attached explicitly per run (project from
``LANGSMITH_PROJECT``), so the legacy global ``LANGSMITH_TRACING`` /
``LANGCHAIN_TRACING_V2`` env vars should stay false — no double tracing.
"""
from __future__ import annotations

import logging
import os
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class TracingMode(str, Enum):
    LOCAL = "local"
    LANGSMITH = "langsmith"
    BOTH = "both"
    OFF = "off"


def resolve_tracing_mode(settings) -> TracingMode:
    raw = (getattr(settings, "tracing_mode", None) or "local").strip().lower()
    try:
        return TracingMode(raw)
    except ValueError:
        logger.warning("Unknown OPEN_OTC_TRACING=%r — falling back to 'local'", raw)
        return TracingMode.LOCAL


def tracing_callbacks(
    settings,
    *,
    thread_id: int | None = None,
    task_id: int | None = None,
    workflow_id: int | None = None,
    message_id: int | None = None,
) -> list[Any]:
    """Callback handlers to attach to one agent run. Never raises."""
    mode = resolve_tracing_mode(settings)
    handlers: list[Any] = []
    if mode in (TracingMode.LOCAL, TracingMode.BOTH):
        try:
            from .store import get_trace_store
            from .tracer import LocalTracer

            handlers.append(
                LocalTracer(
                    get_trace_store(settings),
                    thread_id=thread_id,
                    task_id=task_id,
                    workflow_id=workflow_id,
                    message_id=message_id,
                )
            )
        except Exception:
            logger.warning("Local tracer unavailable — skipping", exc_info=True)
    if mode in (TracingMode.LANGSMITH, TracingMode.BOTH):
        try:
            from langchain_core.tracers.langchain import LangChainTracer

            handlers.append(
                LangChainTracer(
                    project_name=os.environ.get("LANGSMITH_PROJECT")
                    or "open-otc-trading"
                )
            )
        except Exception:
            logger.warning("LangSmith tracer unavailable — skipping", exc_info=True)
    return handlers
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tracing_config.py -v`
Expected: all PASS. (`test_callbacks_both_includes_langsmith` needs no API key —
`LangChainTracer` construction is local; if this environment's langsmith client
insists on a key at construction time, monkeypatch `LANGSMITH_API_KEY` to `"test"`.)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/tracing/config.py tests/test_tracing_config.py
git commit -m "feat(tracing): mode resolution + per-run callback factory"
```

---

### Task 5: Wire into graph_run_config + entry-point trace_meta

**Files:**
- Modify: `backend/app/services/deep_agent/runtime_config.py`
- Modify: `backend/app/services/agents.py` (two call sites: ~line 1859 and ~line 2315)
- Modify: `backend/app/services/deep_agent/executor.py` (~line 97)
- Modify: `backend/app/services/async_agents/resume.py` (~line 141)
- Modify: `backend/app/main.py` (two HITL resume sites: ~line 1129 and ~line 1278)
- Test: `tests/test_tracing_runtime_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing_runtime_config.py`:

```python
"""graph_run_config attaches tracing callbacks + metadata; end-to-end via a runnable."""
from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableLambda

from app.config import Settings
from app.services.tracing.store import get_trace_store, reset_trace_store_cache
from app.services.tracing.tracer import LocalTracer
from app.services.deep_agent.runtime_config import graph_run_config


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_trace_store_cache()
    yield
    reset_trace_store_cache()


def _settings(monkeypatch, tmp_path, mode="local"):
    monkeypatch.setenv("OPEN_OTC_TRACING", mode)
    monkeypatch.setenv("OPEN_OTC_TRACE_DB_PATH", str(tmp_path / "t.sqlite3"))
    return Settings()


def test_off_mode_keeps_config_shape(monkeypatch, tmp_path):
    config = graph_run_config(_settings(monkeypatch, tmp_path, "off"), thread_id=1)
    assert "callbacks" not in config
    assert config["configurable"]["thread_id"] == "1"
    assert "recursion_limit" in config


def test_local_mode_attaches_callbacks_and_metadata(monkeypatch, tmp_path):
    config = graph_run_config(
        _settings(monkeypatch, tmp_path),
        thread_id="wf:1:orchestrator",          # checkpointer key, NOT AgentThread id
        trace_meta={"thread_id": 7, "workflow_id": 3},
    )
    assert isinstance(config["callbacks"][0], LocalTracer)
    assert config["metadata"] == {"thread_id": 7, "workflow_id": 3}
    # configurable untouched by trace_meta
    assert config["configurable"]["thread_id"] == "wf:1:orchestrator"


def test_end_to_end_runnable_lands_in_store(monkeypatch, tmp_path):
    settings = _settings(monkeypatch, tmp_path)
    config = graph_run_config(settings, thread_id=1, trace_meta={"thread_id": 7})
    chain = (RunnableLambda(lambda x: x + 1) | RunnableLambda(lambda x: x * 2)).with_config(
        {"run_name": "audit-me"}
    )
    # graph_run_config carries recursion_limit etc. — pass through unchanged
    assert chain.invoke(1, config=config) == 4

    store = get_trace_store(settings)
    store.flush()
    traces = store.list_thread_traces(7)
    assert len(traces) == 1
    root = traces[0]
    assert root["name"] == "audit-me"
    assert root["status"] == "success"
    runs = store.get_trace(root["trace_id"])
    assert len(runs) >= 3  # sequence root + two lambda children
    assert all(r["thread_id"] == 7 for r in runs)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tracing_runtime_config.py -v`
Expected: FAIL — `graph_run_config() got an unexpected keyword argument 'trace_meta'`.

- [ ] **Step 3: Implement runtime_config.py**

Replace `backend/app/services/deep_agent/runtime_config.py` content with:

```python
from __future__ import annotations

from typing import Any

from ...config import Settings
from ..tracing import tracing_callbacks


def graph_run_config(
    settings: Settings,
    *,
    thread_id: str | int,
    configurable_extra: dict[str, Any] | None = None,
    trace_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run config for every agent invocation.

    ``thread_id`` here is the checkpointer key (sometimes a composite string),
    NOT necessarily an AgentThread id. ``trace_meta`` carries the audit join
    keys (AgentThread ``thread_id``, ``task_id``, ``workflow_id``,
    ``message_id``) stamped onto every trace span and forwarded as run
    metadata so LangSmith can filter by them too. Tracing callbacks are
    attached here so every entry point is audited without per-call-site work.
    """
    configurable: dict[str, Any] = {"thread_id": str(thread_id)}
    if configurable_extra:
        configurable.update(configurable_extra)
    config: dict[str, Any] = {
        "configurable": configurable,
        "recursion_limit": settings.agent_recursion_limit,
    }
    meta = dict(trace_meta or {})
    callbacks = tracing_callbacks(
        settings,
        thread_id=meta.get("thread_id"),
        task_id=meta.get("task_id"),
        workflow_id=meta.get("workflow_id"),
        message_id=meta.get("message_id"),
    )
    if callbacks:
        config["callbacks"] = callbacks
        if meta:
            config["metadata"] = meta
    return config
```

Import note: `..tracing` from `deep_agent` resolves to `app.services.tracing`.
If a circular import appears (tracing → config → ... → deep_agent), move the
`tracing_callbacks` import inside the function body.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tracing_runtime_config.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Add trace_meta at the entry points**

Each is a keyword-argument addition to an existing `graph_run_config(...)` call.

`backend/app/services/agents.py` ~line 1859 (inside
`_prepare_workflow_routed_stream_turn`; `thread_id` is the method's int param):

```python
            config = graph_run_config(
                self.settings,
                thread_id=agent_session.checkpointer_key,
                configurable_extra=configurable_extra,
                trace_meta={"thread_id": thread_id, "workflow_id": route.workflow_id},
            )
```

`backend/app/services/agents.py` ~line 2315 (non-workflow interactive path;
`thread_id` is the int param of `stream_and_persist`):

```python
        config = graph_run_config(
            self.settings,
            thread_id=thread_id,
            configurable_extra=configurable_extra,
            trace_meta={"thread_id": thread_id},
        )
```

`backend/app/services/deep_agent/executor.py` ~line 97 (vars in scope: `task`,
`workflow`, `agent_session`):

```python
        config = graph_run_config(
            self.settings,
            thread_id=task_thread_id,
            configurable_extra={
                "workflow_id": workflow.id,
                "session_id": agent_session.id,
                "task_id": task.id,
                "context_pack_id": pack.id,
                "envelope": envelope_value,
                "tools_scope": sorted(registration.tools_scope),
            },
            trace_meta={"workflow_id": workflow.id, "task_id": task.id},
        )
```

`backend/app/services/async_agents/resume.py` ~line 141 (vars in scope:
`parent_thread_id`, `task_id`):

```python
            config = graph_run_config(
                settings,
                thread_id=f"async:{parent_thread_id}:{task_id}",
                trace_meta={"thread_id": parent_thread_id, "task_id": task_id},
            )
```

`backend/app/main.py` ~line 1129 (orchestrator HITL resume; `thread_id` in scope):

```python
                    config=graph_run_config(
                        active_agent_service.settings,
                        thread_id=checkpointer_key,
                        configurable_extra=resume_extras,
                        trace_meta={
                            "thread_id": thread_id,
                            "workflow_id": action_source_meta["workflow_id"],
                        },
                    ),
```

`backend/app/main.py` ~line 1278 (legacy HITL resume; `thread_id` in scope):

```python
                config=graph_run_config(
                    active_agent_service.settings,
                    thread_id=thread_id,
                    configurable_extra=resume_extras,
                    trace_meta={"thread_id": thread_id},
                ),
```

The remaining `graph_run_config` call sites (agents.py lines ~1481, ~1611,
~2530, ~2620 — preview/summarize/compaction paths) intentionally get callbacks
with no `trace_meta`: still captured for audit, just not thread-linked. Do not
modify them.

- [ ] **Step 6: Run the agent integration tests to verify no regression**

Run: `python -m pytest tests/test_agent_integration.py tests/test_tracing_runtime_config.py -v`
Expected: pass (conftest pins `OPEN_OTC_TRACING=off`, so existing agent tests get
empty callbacks and identical config shape plus no `callbacks` key).

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/deep_agent/runtime_config.py backend/app/services/agents.py \
        backend/app/services/deep_agent/executor.py backend/app/services/async_agents/resume.py \
        backend/app/main.py tests/test_tracing_runtime_config.py
git commit -m "feat(tracing): attach tracer callbacks + audit metadata in graph_run_config"
```

---

### Task 6: Tracing API router

**Files:**
- Create: `backend/app/routers/tracing.py`
- Modify: `backend/app/main.py` (~line 3845, next to `build_skills_router`)
- Test: `tests/test_tracing_router.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_tracing_router.py`:

```python
"""/api/tracing/* endpoints over a seeded trace store."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers.tracing import build_tracing_router
from app.services.tracing.store import SpanEnd, SpanStart, TraceStore


@pytest.fixture()
def store(tmp_path):
    s = TraceStore(tmp_path / "traces.sqlite3")
    s.enqueue_insert(SpanStart(
        id="root", trace_id="root", parent_run_id=None,
        dotted_order="20260611T0900000000Zroot",
        thread_id=7, task_id=None, workflow_id=None, message_id=None,
        name="orchestrator", run_type="chain",
        start_time="2026-06-11T09:00:00",
        inputs=json.dumps({"q": "x" * 5000}),   # > preview cap
        extra="{}"))
    s.enqueue_insert(SpanStart(
        id="tool1", trace_id="root", parent_run_id="root",
        dotted_order="20260611T0900000000Zroot.20260611T0900010000Ztool1",
        thread_id=7, task_id=None, workflow_id=None, message_id=None,
        name="price_position", run_type="tool",
        start_time="2026-06-11T09:00:01", inputs='{"sym": "AAPL"}', extra="{}"))
    s.enqueue_finalize(SpanEnd(
        id="tool1", trace_id="root", parent_run_id="root",
        end_time="2026-06-11T09:00:02", status="success",
        outputs='{"pv": 1.23}', error=None,
        prompt_tokens=None, completion_tokens=None, total_tokens=None))
    s.enqueue_finalize(SpanEnd(
        id="root", trace_id="root", parent_run_id=None,
        end_time="2026-06-11T09:00:03", status="success",
        outputs='{"a": "done"}', error=None,
        prompt_tokens=None, completion_tokens=None, total_tokens=None))
    s.flush()
    yield s
    s.close()


@pytest.fixture()
def client(store, monkeypatch):
    monkeypatch.setenv("OPEN_OTC_TRACING", "local")
    app = FastAPI()
    app.include_router(build_tracing_router(get_store=lambda: store))
    return TestClient(app)


def test_config_endpoint(client):
    body = client.get("/api/tracing/config").json()
    assert body["mode"] == "local"
    assert body["langsmith_url"].startswith("https://")


def test_thread_traces(client):
    body = client.get("/api/tracing/threads/7/traces").json()
    assert body["thread_id"] == 7
    assert len(body["traces"]) == 1
    assert body["traces"][0]["name"] == "orchestrator"
    assert client.get("/api/tracing/threads/999/traces").json()["traces"] == []


def test_trace_tree_truncates_previews(client):
    body = client.get("/api/tracing/traces/root").json()
    runs = body["runs"]
    assert [r["id"] for r in runs] == ["root", "tool1"]
    root = runs[0]
    assert root["inputs_truncated"] is True
    assert len(root["inputs_preview"]) <= 2000
    tool = runs[1]
    assert tool["inputs_truncated"] is False
    assert json.loads(tool["inputs_preview"]) == {"sym": "AAPL"}


def test_trace_tree_404(client):
    assert client.get("/api/tracing/traces/nope").status_code == 404


def test_run_detail_full_payload(client):
    body = client.get("/api/tracing/runs/root").json()
    assert len(json.loads(body["inputs"])["q"]) == 5000  # untruncated
    assert client.get("/api/tracing/runs/nope").status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tracing_router.py -v`
Expected: FAIL — no module `app.routers.tracing`.

- [ ] **Step 3: Implement**

Create `backend/app/routers/tracing.py`:

```python
"""Read-only tracing & audit API.

Serves the local trace store to the /tracing viewer. Strictly read-only by
design — the trace store is an append-only audit record; no mutating
endpoints exist or should ever be added here.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.services.tracing.config import resolve_tracing_mode
from app.services.tracing.store import TraceStore, get_trace_store

_PREVIEW_CHARS = 2000


def _preview(raw: str | None) -> tuple[str | None, bool]:
    if raw is None:
        return None, False
    if len(raw) <= _PREVIEW_CHARS:
        return raw, False
    return raw[:_PREVIEW_CHARS], True


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "trace_id": row["trace_id"],
        "name": row["name"],
        "run_type": row["run_type"],
        "status": row["status"],
        "start_time": row["start_time"],
        "end_time": row["end_time"],
        "total_tokens": row["total_tokens"],
        "thread_id": row["thread_id"],
        "task_id": row["task_id"],
        "workflow_id": row["workflow_id"],
    }


def _tree_node(row: dict[str, Any]) -> dict[str, Any]:
    inputs_preview, inputs_truncated = _preview(row["inputs"])
    outputs_preview, outputs_truncated = _preview(row["outputs"])
    return {
        **_summary(row),
        "parent_run_id": row["parent_run_id"],
        "dotted_order": row["dotted_order"],
        "error": row["error"],
        "prompt_tokens": row["prompt_tokens"],
        "completion_tokens": row["completion_tokens"],
        "inputs_preview": inputs_preview,
        "inputs_truncated": inputs_truncated,
        "outputs_preview": outputs_preview,
        "outputs_truncated": outputs_truncated,
    }


def build_tracing_router(
    get_store: Callable[[], TraceStore] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/tracing", tags=["tracing"])

    def _store() -> TraceStore:
        if get_store is not None:
            return get_store()
        return get_trace_store(get_settings())

    @router.get("/config")
    def tracing_config() -> dict[str, Any]:
        mode = resolve_tracing_mode(get_settings())
        return {
            "mode": mode.value,
            "langsmith_url": os.environ.get("LANGSMITH_PROJECT_URL")
            or "https://smith.langchain.com",
        }

    @router.get("/threads/{thread_id}/traces")
    def thread_traces(
        thread_id: int, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        rows = _store().list_thread_traces(thread_id, limit=limit, offset=offset)
        return {"thread_id": thread_id, "traces": [_summary(r) for r in rows]}

    @router.get("/traces/{trace_id}")
    def trace_tree(trace_id: str) -> dict[str, Any]:
        rows = _store().get_trace(trace_id)
        if not rows:
            raise HTTPException(status_code=404, detail="Trace not found")
        return {"trace_id": trace_id, "runs": [_tree_node(r) for r in rows]}

    @router.get("/runs/{run_id}")
    def run_detail(run_id: str) -> dict[str, Any]:
        row = _store().get_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return row

    return router
```

In `backend/app/main.py`: add the import near the existing
`from .routers.skills import build_skills_router` import (search for it), and
mount next to the skills router (~line 3845):

```python
from .routers.tracing import build_tracing_router
```

```python
    app.include_router(build_skills_router(active_agent_service))
    app.include_router(build_tracing_router())
    return app
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_tracing_router.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/tracing.py backend/app/main.py tests/test_tracing_router.py
git commit -m "feat(tracing): read-only /api/tracing router + mount"
```

---

### Task 7: Frontend types, API client, openTraceTarget helper

**Files:**
- Modify: `frontend/src/types.ts` (Route union + new types)
- Modify: `frontend/src/api/client.ts`
- Create: `frontend/src/lib/tracing.ts`
- Test: `frontend/src/lib/tracing.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/tracing.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { openTraceTarget } from './tracing';
import type { TracingConfig } from '../types';

const cfg = (mode: TracingConfig['mode']): TracingConfig => ({
  mode,
  langsmith_url: 'https://smith.langchain.com',
});

describe('openTraceTarget', () => {
  it('routes internally for local mode', () => {
    expect(openTraceTarget(cfg('local'), 7)).toEqual({ kind: 'internal', threadId: 7 });
  });
  it('routes internally for both mode', () => {
    expect(openTraceTarget(cfg('both'), 7)).toEqual({ kind: 'internal', threadId: 7 });
  });
  it('opens LangSmith externally for langsmith mode', () => {
    expect(openTraceTarget(cfg('langsmith'), 7)).toEqual({
      kind: 'external',
      url: 'https://smith.langchain.com',
    });
  });
  it('hides the link when tracing is off or config missing', () => {
    expect(openTraceTarget(cfg('off'), 7)).toEqual({ kind: 'none' });
    expect(openTraceTarget(null, 7)).toEqual({ kind: 'none' });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/tracing.test.ts`
Expected: FAIL — cannot resolve `./tracing` / missing types.

- [ ] **Step 3: Implement**

In `frontend/src/types.ts`, extend the `Route` union (after `| 'skills'`):

```ts
  | 'skills'
  | 'tracing';
```

Append the trace types (near the other API payload types):

```ts
export type TracingConfig = {
  mode: 'local' | 'langsmith' | 'both' | 'off';
  langsmith_url: string | null;
};

export type TraceSummary = {
  id: string;
  trace_id: string;
  name: string;
  run_type: string;
  status: 'running' | 'success' | 'error';
  start_time: string;
  end_time: string | null;
  total_tokens: number | null;
  thread_id: number | null;
  task_id: number | null;
  workflow_id: number | null;
};

export type TraceRunNode = TraceSummary & {
  parent_run_id: string | null;
  dotted_order: string;
  error: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  inputs_preview: string | null;
  inputs_truncated: boolean;
  outputs_preview: string | null;
  outputs_truncated: boolean;
};

export type TraceRunDetail = TraceSummary & {
  parent_run_id: string | null;
  dotted_order: string;
  error: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  inputs: string | null;
  outputs: string | null;
  extra: string | null;
};
```

In `frontend/src/api/client.ts`, add to the type import list:
`TracingConfig, TraceRunDetail, TraceRunNode, TraceSummary`, then append:

```ts
export function fetchTracingConfig(): Promise<TracingConfig> {
  return api<TracingConfig>('/api/tracing/config');
}

export function fetchThreadTraces(
  threadId: number,
): Promise<{ thread_id: number; traces: TraceSummary[] }> {
  return api(`/api/tracing/threads/${threadId}/traces`);
}

export function fetchTraceTree(
  traceId: string,
): Promise<{ trace_id: string; runs: TraceRunNode[] }> {
  return api(`/api/tracing/traces/${traceId}`);
}

export function fetchTraceRun(runId: string): Promise<TraceRunDetail> {
  return api(`/api/tracing/runs/${runId}`);
}
```

Create `frontend/src/lib/tracing.ts`:

```ts
import type { TracingConfig } from '../types';

export type TraceTarget =
  | { kind: 'internal'; threadId: number }
  | { kind: 'external'; url: string }
  | { kind: 'none' };

/** Where the per-thread trace link should go for the active tracing mode. */
export function openTraceTarget(
  config: TracingConfig | null,
  threadId: number,
): TraceTarget {
  if (!config || config.mode === 'off') return { kind: 'none' };
  if (config.mode === 'langsmith') {
    return config.langsmith_url
      ? { kind: 'external', url: config.langsmith_url }
      : { kind: 'none' };
  }
  return { kind: 'internal', threadId };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/lib/tracing.test.ts`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api/client.ts frontend/src/lib/tracing.ts frontend/src/lib/tracing.test.ts
git commit -m "feat(tracing): frontend types, API client, trace-target helper"
```

---

### Task 8: Tracing viewer page

**Files:**
- Create: `frontend/src/routes/Tracing.tsx` (presentational)
- Create: `frontend/src/routes/Tracing.css`
- Create: `frontend/src/routes/Tracing.live.tsx` (fetching wrapper)
- Test: `frontend/src/routes/Tracing.test.tsx`

Follow the repo's presentational/live split (`AgentDesk.tsx` / `AgentDesk.live.tsx`).
**Styling: tokens only** — copy class/variable conventions from an existing page CSS
(e.g. `Backtest.css`); zero hex/rgba literals.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/routes/Tracing.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { Tracing } from './Tracing';
import type { TraceRunNode, TraceSummary } from '../types';

const summary: TraceSummary = {
  id: 'root', trace_id: 'root', name: 'orchestrator', run_type: 'chain',
  status: 'success', start_time: '2026-06-11T09:00:00',
  end_time: '2026-06-11T09:00:03', total_tokens: 40,
  thread_id: 7, task_id: null, workflow_id: null,
};

const runs: TraceRunNode[] = [
  {
    ...summary, parent_run_id: null, dotted_order: 'a', error: null,
    prompt_tokens: null, completion_tokens: null,
    inputs_preview: '{"q":"price it"}', inputs_truncated: false,
    outputs_preview: '{"a":"done"}', outputs_truncated: false,
  },
  {
    ...summary, id: 'tool1', name: 'price_position', run_type: 'tool',
    parent_run_id: 'root', dotted_order: 'a.b', error: null,
    prompt_tokens: null, completion_tokens: null, total_tokens: null,
    inputs_preview: '{"sym":"AAPL"}', inputs_truncated: false,
    outputs_preview: '{"pv":1.23}', outputs_truncated: false,
  },
];

function renderPage(overrides: Partial<Parameters<typeof Tracing>[0]> = {}) {
  const props = {
    threadId: 7,
    traces: [summary],
    selectedTraceId: 'root',
    onSelectTrace: vi.fn(),
    runs,
    selectedRunId: 'root',
    onSelectRun: vi.fn(),
    runDetail: null,
    loading: false,
    ...overrides,
  };
  render(<Tracing {...props} />);
  return props;
}

describe('Tracing', () => {
  it('renders trace list, span tree, and thread filter chip', () => {
    renderPage();
    expect(screen.getByText('Thread #7')).toBeInTheDocument();
    expect(screen.getAllByText('orchestrator').length).toBeGreaterThan(0);
    expect(screen.getByText('price_position')).toBeInTheDocument();
    expect(screen.getByText('tool')).toBeInTheDocument(); // run-type badge
  });

  it('selecting a span calls onSelectRun', () => {
    const props = renderPage();
    fireEvent.click(screen.getByRole('button', { name: /price_position/ }));
    expect(props.onSelectRun).toHaveBeenCalledWith('tool1');
  });

  it('shows the span detail payloads', () => {
    renderPage({
      runDetail: {
        ...summary, parent_run_id: null, dotted_order: 'a', error: null,
        prompt_tokens: 11, completion_tokens: 4,
        inputs: '{"q":"price it"}', outputs: '{"a":"done"}', extra: '{}',
      },
    });
    expect(screen.getByText(/price it/)).toBeInTheDocument();
    expect(screen.getByText(/Inputs/)).toBeInTheDocument();
  });

  it('renders empty state without traces', () => {
    renderPage({ traces: [], runs: [], selectedTraceId: null, runDetail: null });
    expect(screen.getByText(/No traces/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/Tracing.test.tsx`
Expected: FAIL — `./Tracing` does not exist.

- [ ] **Step 3: Implement the presentational page**

Create `frontend/src/routes/Tracing.tsx`:

```tsx
import { useMemo } from 'react';
import { AlertCircle, CheckCircle2, CircleDashed } from 'lucide-react';
import type { TraceRunDetail, TraceRunNode, TraceSummary } from '../types';
import { Empty } from '../components/Empty';
import './Tracing.css';

type Props = {
  threadId: number | null;
  traces: TraceSummary[];
  selectedTraceId: string | null;
  onSelectTrace: (traceId: string) => void;
  runs: TraceRunNode[];
  selectedRunId: string | null;
  onSelectRun: (runId: string) => void;
  runDetail: TraceRunDetail | null;
  loading: boolean;
};

function statusIcon(status: TraceSummary['status']) {
  if (status === 'error') return <AlertCircle size={14} aria-label="error" />;
  if (status === 'running') return <CircleDashed size={14} aria-label="running" />;
  return <CheckCircle2 size={14} aria-label="success" />;
}

function durationMs(start: string, end: string | null): string {
  if (!end) return '—';
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms}ms`;
}

function depthOf(run: TraceRunNode): number {
  return run.dotted_order.split('.').length - 1;
}

function prettyJson(raw: string | null): string {
  if (!raw) return '—';
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

export function Tracing({
  threadId, traces, selectedTraceId, onSelectTrace,
  runs, selectedRunId, onSelectRun, runDetail, loading,
}: Props) {
  const orderedRuns = useMemo(
    () => [...runs].sort((a, b) => a.dotted_order.localeCompare(b.dotted_order)),
    [runs],
  );

  return (
    <section className="wl-tracing" aria-label="Session traces">
      <aside className="wl-tracing__list" aria-label="Traces">
        <div className="wl-tracing__head">
          <div className="wl-tracing__eyebrow">Traces</div>
          {threadId != null && (
            <span className="wl-tracing__filter-chip">Thread #{threadId}</span>
          )}
        </div>
        {traces.length === 0 ? (
          <Empty title="No traces" message="Run an agent turn to record a trace." />
        ) : (
          traces.map((trace) => (
            <button
              key={trace.id}
              type="button"
              className={`wl-tracing__trace-card${trace.id === selectedTraceId ? ' is-active' : ''}`}
              onClick={() => onSelectTrace(trace.trace_id)}
            >
              <span className="wl-tracing__trace-status">{statusIcon(trace.status)}</span>
              <span className="wl-tracing__trace-name">{trace.name}</span>
              <span className="wl-tracing__trace-meta">
                {new Date(trace.start_time).toLocaleString()} · {durationMs(trace.start_time, trace.end_time)}
                {trace.total_tokens != null ? ` · ${trace.total_tokens} tok` : ''}
              </span>
            </button>
          ))
        )}
      </aside>

      <div className="wl-tracing__tree" aria-label="Span tree">
        {loading ? (
          <div className="wl-tracing__placeholder">Loading…</div>
        ) : orderedRuns.length === 0 ? (
          <div className="wl-tracing__placeholder">Select a trace to inspect its spans.</div>
        ) : (
          orderedRuns.map((run) => (
            <button
              key={run.id}
              type="button"
              className={`wl-tracing__span${run.id === selectedRunId ? ' is-active' : ''}`}
              style={{ ['--wl-span-depth' as string]: depthOf(run) }}
              onClick={() => onSelectRun(run.id)}
            >
              <span className="wl-tracing__trace-status">{statusIcon(run.status)}</span>
              <span className={`wl-tracing__span-type wl-tracing__span-type--${run.run_type}`}>
                {run.run_type}
              </span>
              <span className="wl-tracing__span-name">{run.name}</span>
              <span className="wl-tracing__span-meta">
                {durationMs(run.start_time, run.end_time)}
                {run.total_tokens != null ? ` · ${run.total_tokens} tok` : ''}
              </span>
            </button>
          ))
        )}
      </div>

      <aside className="wl-tracing__detail" aria-label="Span detail">
        {runDetail === null ? (
          <div className="wl-tracing__placeholder">Select a span to see its payloads.</div>
        ) : (
          <>
            <div className="wl-tracing__detail-head">
              <span className="wl-tracing__span-name">{runDetail.name}</span>
              <span className="wl-tracing__span-meta">
                {runDetail.run_type} · {runDetail.status}
                {runDetail.prompt_tokens != null
                  ? ` · ${runDetail.prompt_tokens}→${runDetail.completion_tokens} tok`
                  : ''}
              </span>
            </div>
            {runDetail.error && (
              <div className="wl-tracing__error" role="alert">
                <pre>{runDetail.error}</pre>
              </div>
            )}
            <div className="wl-tracing__payload">
              <div className="wl-tracing__eyebrow">Inputs</div>
              <pre>{prettyJson(runDetail.inputs)}</pre>
            </div>
            <div className="wl-tracing__payload">
              <div className="wl-tracing__eyebrow">Outputs</div>
              <pre>{prettyJson(runDetail.outputs)}</pre>
            </div>
          </>
        )}
      </aside>
    </section>
  );
}
```

Create `frontend/src/routes/Tracing.css` using **existing tokens only** (look up
exact token names in `frontend/src/tokens/` and an existing page CSS before
writing; the names below must be replaced with the project's real tokens if they
differ):

```css
.wl-tracing {
  display: grid;
  grid-template-columns: 280px minmax(320px, 1fr) minmax(360px, 1.2fr);
  gap: var(--space-4);
  height: 100%;
  min-height: 0;
}

.wl-tracing__list,
.wl-tracing__tree,
.wl-tracing__detail {
  overflow-y: auto;
  min-height: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  padding: var(--space-3);
}

.wl-tracing__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: var(--space-3);
}

.wl-tracing__eyebrow {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-text-muted);
}

.wl-tracing__filter-chip {
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  border-radius: var(--radius-full);
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.wl-tracing__trace-card,
.wl-tracing__span {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  text-align: left;
  padding: var(--space-2);
  border: none;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text);
  cursor: pointer;
}

.wl-tracing__trace-card:hover,
.wl-tracing__span:hover {
  background: var(--color-surface-raised);
}

.wl-tracing__trace-card.is-active,
.wl-tracing__span.is-active {
  background: var(--color-surface-active);
}

.wl-tracing__trace-card {
  flex-wrap: wrap;
}

.wl-tracing__trace-name,
.wl-tracing__span-name {
  font-weight: 600;
  font-size: var(--text-sm);
}

.wl-tracing__trace-meta,
.wl-tracing__span-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-left: auto;
}

.wl-tracing__span {
  padding-left: calc(var(--space-2) + var(--wl-span-depth, 0) * var(--space-4));
}

.wl-tracing__span-type {
  font-size: var(--text-xs);
  padding: 1px var(--space-2);
  border-radius: var(--radius-full);
  background: var(--color-surface-raised);
  color: var(--color-text-muted);
}

.wl-tracing__placeholder {
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  padding: var(--space-4);
}

.wl-tracing__detail-head {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.wl-tracing__payload pre,
.wl-tracing__error pre {
  white-space: pre-wrap;
  word-break: break-word;
  font-size: var(--text-xs);
  background: var(--color-surface-raised);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}

.wl-tracing__error pre {
  color: var(--color-danger);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/Tracing.test.tsx`
Expected: 4 PASS.

- [ ] **Step 5: Implement the live wrapper**

Create `frontend/src/routes/Tracing.live.tsx`:

```tsx
import { useCallback, useEffect, useState } from 'react';
import {
  fetchThreadTraces,
  fetchTraceRun,
  fetchTraceTree,
} from '../api/client';
import type { TraceRunDetail, TraceRunNode, TraceSummary } from '../types';
import { Tracing } from './Tracing';

type Props = {
  threadId: number | null;
};

export function TracingLive({ threadId }: Props) {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [runs, setRuns] = useState<TraceRunNode[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<TraceRunDetail | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (threadId == null) {
      setTraces([]);
      return;
    }
    fetchThreadTraces(threadId)
      .then((body) => {
        setTraces(body.traces);
        if (body.traces.length > 0) selectTrace(body.traces[0].trace_id);
      })
      .catch(() => setTraces([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId]);

  const selectTrace = useCallback((traceId: string) => {
    setSelectedTraceId(traceId);
    setSelectedRunId(null);
    setRunDetail(null);
    setLoading(true);
    fetchTraceTree(traceId)
      .then((body) => setRuns(body.runs))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, []);

  const selectRun = useCallback((runId: string) => {
    setSelectedRunId(runId);
    fetchTraceRun(runId)
      .then(setRunDetail)
      .catch(() => setRunDetail(null));
  }, []);

  return (
    <Tracing
      threadId={threadId}
      traces={traces}
      selectedTraceId={selectedTraceId}
      onSelectTrace={selectTrace}
      runs={runs}
      selectedRunId={selectedRunId}
      onSelectRun={selectRun}
      runDetail={runDetail}
      loading={loading}
    />
  );
}
```

- [ ] **Step 6: Run the full frontend suite for this area**

Run: `cd frontend && npx vitest run src/routes/Tracing.test.tsx src/lib/tracing.test.ts`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/Tracing.tsx frontend/src/routes/Tracing.css \
        frontend/src/routes/Tracing.live.tsx frontend/src/routes/Tracing.test.tsx
git commit -m "feat(tracing): three-pane trace viewer page"
```

---

### Task 9: main.tsx wiring + AgentDesk per-thread trace link

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/routes/AgentDesk.tsx`
- Modify: `frontend/src/routes/AgentDesk.live.tsx`
- Test: `frontend/src/routes/AgentDesk.test.tsx` (append cases)

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/routes/AgentDesk.test.tsx` (match the file's existing
render-helper style — read it first and reuse its setup; the assertions to add):

```tsx
describe('thread trace link', () => {
  it('renders a trace button per thread when onOpenTrace is provided', () => {
    // render AgentDesk with the file's standard props + onOpenTrace: vi.fn()
    // and at least one thread named "Pricing chat" with id 7
    const onOpenTrace = vi.fn();
    renderDesk({ onOpenTrace });
    const btn = screen.getByRole('button', { name: 'View trace for Pricing chat' });
    fireEvent.click(btn);
    expect(onOpenTrace).toHaveBeenCalledWith(7);
  });

  it('renders no trace button when onOpenTrace is absent', () => {
    renderDesk();
    expect(
      screen.queryByRole('button', { name: /View trace/ }),
    ).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/routes/AgentDesk.test.tsx`
Expected: the two new cases FAIL (no trace button rendered).

- [ ] **Step 3: Implement the AgentDesk button**

In `frontend/src/routes/AgentDesk.tsx`:

1. Add `Waypoints` to the existing `lucide-react` import.
2. Add `onOpenTrace?: (threadId: number) => void;` to BOTH the `AgentDesk` props
   type (~line 22 block) and the `ThreadsPane` props type (~line 204 block), and
   pass it through where `ThreadsPane` is rendered (~line 136).
3. In the thread-tools div (the one rendering the Pencil button, ~line 332), add
   BEFORE the rename button:

```tsx
                      {onOpenTrace && (
                        <button
                          type="button"
                          className="wl-agent-desk__icon-btn"
                          aria-label={`View trace for ${thread.title}`}
                          onClick={() => onOpenTrace(thread.id)}
                        >
                          <Waypoints size={14} aria-hidden="true" />
                        </button>
                      )}
```

In `frontend/src/routes/AgentDesk.live.tsx`: add `onOpenTrace?: (threadId: number) => void;`
to the `Props` type and thread it through `AgentDeskLive` → `AgentDeskLiveStandalone` /
`AgentDeskLiveView` → `<AgentDesk onOpenTrace={onOpenTrace} ... />`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/routes/AgentDesk.test.tsx`
Expected: PASS (new cases + all pre-existing cases).

- [ ] **Step 5: Wire main.tsx**

In `frontend/src/main.tsx`:

1. Imports:

```tsx
import { TracingLive } from './routes/Tracing.live';
import { fetchTracingConfig } from './api/client';
import { openTraceTarget } from './lib/tracing';
import type { TracingConfig } from './types';
```

2. Nav item (after the `skills` entry in `navItems`):

```tsx
  { route: 'tracing' as const,   label: 'Tracing' },
```

3. Command palette entry (after `jump-skills` in `commandItems`):

```tsx
    { id: 'jump-tracing',   group: 'Jump To', label: 'Tracing',       shortcut: '↵' },
```

4. Inside `App()`, alongside the existing state:

```tsx
  const [traceThreadId, setTraceThreadId] = useState<number | null>(null);
  const [tracingConfig, setTracingConfig] = useState<TracingConfig | null>(null);

  useEffect(() => {
    fetchTracingConfig().then(setTracingConfig).catch(() => setTracingConfig(null));
  }, []);

  const handleOpenTrace = useCallback((threadId: number) => {
    const target = openTraceTarget(tracingConfig, threadId);
    if (target.kind === 'external') {
      window.open(target.url, '_blank', 'noopener');
    } else if (target.kind === 'internal') {
      setTraceThreadId(target.threadId);
      setRoute('tracing');
    }
  }, [tracingConfig]);
```

5. Route render (next to the `skills` line ~239):

```tsx
            {route === 'tracing' && <TracingLive threadId={traceThreadId} />}
```

6. Pass the handler into the AgentDesk render (find `<AgentDeskLive` in main.tsx;
   only pass it when tracing isn't off so the button hides itself):

```tsx
              onOpenTrace={tracingConfig && tracingConfig.mode !== 'off' ? handleOpenTrace : undefined}
```

- [ ] **Step 6: Run the frontend suite**

Run: `cd frontend && npx vitest run`
Expected: PASS (compare any failures against a baseline run on the base commit —
there may be pre-existing failures unrelated to this work). Note: adding a nav
item may break a `Sidebar`/`main` snapshot or nav-count assertion — if a test
pins the nav item list, update it to include `tracing`.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/main.tsx frontend/src/routes/AgentDesk.tsx \
        frontend/src/routes/AgentDesk.live.tsx frontend/src/routes/AgentDesk.test.tsx
git commit -m "feat(tracing): nav route + per-thread trace link wiring"
```

---

### Task 10: Full verification + finish

- [ ] **Step 1: Full backend suite**

Run: `python -m pytest tests/ -x -q`
Expected: all tracing tests pass; any other failures must be checked against a
baseline run of the same files on the base commit (known pre-existing
migration/Skills failures exist on main).

- [ ] **Step 2: Full frontend suite**

Run: `cd frontend && npx vitest run`
Expected: same baseline-comparison rule.

- [ ] **Step 3: Manual smoke (optional but recommended)**

```bash
# backend
uvicorn app.main:app --reload --app-dir backend &
# frontend
cd frontend && npm run dev
```

With `OPEN_OTC_TRACING=local` (default): send one Agent Desk message, then click
the thread's trace button → `/tracing` should show the run tree with the
orchestrator chain, LLM spans (with token counts), and tool spans. Verify
`data/agent_traces.sqlite3` exists and `sqlite3 data/agent_traces.sqlite3
'SELECT count(*) FROM trace_runs'` grows.

- [ ] **Step 4: Final commit & merge prep**

Use superpowers:finishing-a-development-branch. The feature is complete when:
backend + frontend suites match baseline, the smoke test shows a thread-linked
trace, and `OPEN_OTC_TRACING=off` produces zero rows.
