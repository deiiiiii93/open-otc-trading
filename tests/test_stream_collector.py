from __future__ import annotations

from app.services.deep_agent.stream_collector import StreamCollector, _truncate


def test_collector_starts_empty():
    c = StreamCollector()
    assert c.final_text == ""
    assert c.process_events == []
    assert c.interrupts == []
    assert c.personas_invoked == []
    assert c.error is None


def test_collector_pairs_tool_start_and_end_by_run_id():
    c = StreamCollector()
    c.on_tool_start("run-1", "price_product", {"underlying": "SPX"}, started_at=100.0)
    c.on_tool_end("run-1", {"price": 102.5}, ended_at=100.120)

    events = c.process_events
    assert len(events) == 1
    ev = events[0]
    assert ev["id"] == "run-1"
    assert ev["name"] == "price_product"
    assert ev["status"] == "done"
    assert ev["duration_ms"] == 120
    assert ev["args"] == {"underlying": "SPX"}
    assert ev["output"] == {"price": 102.5}
    assert "started_at" not in ev  # internal field is dropped from serialization


def test_collector_keeps_concurrent_tool_calls_distinct():
    c = StreamCollector()
    c.on_tool_start("run-1", "price_product", {"id": 1}, started_at=100.0)
    c.on_tool_start("run-2", "price_product", {"id": 2}, started_at=100.05)
    c.on_tool_end("run-2", {"price": 50}, ended_at=100.20)
    c.on_tool_end("run-1", {"price": 99}, ended_at=100.30)

    events = c.process_events
    assert len(events) == 2
    by_id = {e["id"]: e for e in events}
    assert by_id["run-1"]["output"] == {"price": 99}
    assert by_id["run-2"]["output"] == {"price": 50}


def test_collector_records_tool_error():
    c = StreamCollector()
    c.on_tool_start("run-x", "approve_rfq", {"rfq_id": 7}, started_at=0.0)
    c.on_tool_end("run-x", None, ended_at=0.05, error="permission denied")

    ev = c.process_events[0]
    assert ev["status"] == "error"
    assert ev["error"] == "permission denied"
    assert ev["output"] is None
    assert ev["duration_ms"] == 50


def test_collector_concatenates_text_chunks_in_order():
    c = StreamCollector()
    c.on_token("Hello")
    c.on_token(" ")
    c.on_token("world")
    assert c.final_text == "Hello world"


def test_collector_strips_final_text():
    c = StreamCollector()
    c.on_token("  hi  ")
    assert c.final_text == "hi"


def test_truncate_passes_through_small_values():
    assert _truncate({"a": 1}) == {"a": 1}
    assert _truncate("short") == "short"


def test_truncate_replaces_oversize_values_with_envelope():
    big = "x" * 5000
    out = _truncate(big, limit=1000)
    assert out["_truncated"] is True
    assert len(out["preview"]) == 1000
    assert out["size"] > 5000
    # Preview is JSON-stringified content (which adds quote chars for strings)
    assert out["preview"].startswith('"xxxxx')


def test_truncate_handles_unserializable_values():
    class Obj:
        def __repr__(self):
            return "Obj()"
    out = _truncate(Obj(), limit=1000)
    # default=str ensures the object is stringified, not crashed on
    assert out == "Obj()" or (isinstance(out, dict) and out.get("_truncated"))
