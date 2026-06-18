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
