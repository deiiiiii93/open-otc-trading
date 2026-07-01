"""Task 7: route dynamic-subagents fan-out events to the web SSE data path."""
import json

from app.services.agents import _subagent_sse_line
from app.services.deep_agent.stream_collector import StreamCollector


def test_subagent_event_becomes_sse_line_and_is_recorded():
    collector = StreamCollector()
    event = {
        "type": "subagent", "phase": "start", "id": "ptc_task_ab12",
        "eval_id": "call_9", "subagent_type": "risk_manager",
        "label": "breach 42", "description": "investigate",
    }
    line = _subagent_sse_line(event, collector)
    assert line.startswith("event: subagent\n")
    payload = json.loads(line.split("data: ", 1)[1])
    assert payload["phase"] == "start"
    assert payload["subagent_type"] == "risk_manager"
    assert collector.subagent_events[-1]["id"] == "ptc_task_ab12"


def test_collector_accumulates_lifecycle_grouped_by_eval_id():
    collector = StreamCollector()
    for phase in ("start", "complete"):
        _subagent_sse_line(
            {"type": "subagent", "phase": phase, "id": "s1", "eval_id": "call_9"}, collector
        )
    assert [e["phase"] for e in collector.subagent_events] == ["start", "complete"]
    assert {e["eval_id"] for e in collector.subagent_events} == {"call_9"}
