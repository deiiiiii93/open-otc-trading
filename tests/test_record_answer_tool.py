"""record_answer tool: dual-shape capture, payload bounds, allowlist presence."""
from app.tools.record_answer import record_answer_tool
from app.services.agents import select_deep_agent_tools


def test_record_answer_nested_shape():
    out = record_answer_tool.invoke({"answer": {"hotspot": "AAPL", "delta": 573.35}})
    assert out == {"recorded": True, "fields": {"hotspot": "AAPL", "delta": 573.35}}


def test_record_answer_flat_kwargs_shape():
    # A model that ignores the `answer` wrapper and passes fields flat must still be
    # captured — otherwise live runs miss while nested replay fixtures pass.
    out = record_answer_tool.invoke({"hotspot": "AAPL", "delta": 573.35})
    assert out == {"recorded": True, "fields": {"hotspot": "AAPL", "delta": 573.35}}


def test_record_answer_bounds_payload():
    # capture-sink guard: oversized string truncated, field count capped.
    big = {"blob": "x" * 5000, **{f"k{i}": i for i in range(50)}}
    out = record_answer_tool.invoke({"answer": big})
    assert len(out["fields"]) <= 32
    assert all(not isinstance(v, str) or len(v) <= 257 for v in out["fields"].values())


def test_record_answer_is_selectable():
    names = {t.name for t in select_deep_agent_tools()}
    assert "record_answer" in names
