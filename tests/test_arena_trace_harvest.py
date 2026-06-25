import json

from app.services.arena.models import ArenaModel
from app.services.arena.trace_harvest import (
    _parse_tool_output,
    _spans_to_turn_events,
    transcript_from_trace,
)


def _tool_output(content: dict, name: str, tcid: str = "tc1") -> str:
    return json.dumps({"output": f"content='{json.dumps(content)}' name='{name}' tool_call_id='{tcid}'"})


def _llm_output(text: str) -> str:
    return json.dumps({"generations": [[{"text": text}]]})


def test_parse_tool_output_extracts_content_dict():
    raw = _tool_output({"task_id": 12, "status": "queued"}, "run_batch_pricing")
    content, name, tcid = _parse_tool_output(raw)
    assert content == {"task_id": 12, "status": "queued"}
    assert name == "run_batch_pricing"
    assert tcid == "tc1"


def test_parse_tool_output_non_json_falls_back_to_raw():
    raw = json.dumps({"output": "content='plain text' name='x' tool_call_id='tc2'"})
    content, _, _ = _parse_tool_output(raw)
    assert content == {"raw": "plain text"}


def test_parse_tool_output_structured_dict_output_preserved():
    # A tool span that records a dict output (not a stringified ToolMessage)
    # must keep its payload (e.g. task_id) instead of being dropped.
    raw = json.dumps({"output": {"task_id": 7, "status": "queued"}})
    content, _, _ = _parse_tool_output(raw)
    assert content == {"task_id": 7, "status": "queued"}


def test_llm_text_falls_back_to_message_content_blocks():
    # When the flat `text` is empty, harvest the AIMessage content blocks.
    from app.services.arena.trace_harvest import _llm_text

    raw = json.dumps({"generations": [[{
        "text": "",
        "message": {"kwargs": {"content": [{"type": "text", "text": "AAPL is the hotspot."}]}},
    }]]})
    assert _llm_text(raw) == "AAPL is the hotspot."


def test_llm_text_falls_back_to_string_message_content():
    from app.services.arena.trace_harvest import _llm_text

    raw = json.dumps({"generations": [[{
        "text": None,
        "message": {"kwargs": {"content": "plain string answer"}},
    }]]})
    assert _llm_text(raw) == "plain string answer"


def test_skills_routed_from_read_file_ordered():
    spans = [
        {"run_type": "tool", "name": "read_file", "start_time": "2026-01-01T00:00:02",
         "inputs": json.dumps({"file_path": "/skills/workflows/reporting/generate-report/SKILL.md"})},
        {"run_type": "tool", "name": "read_file", "start_time": "2026-01-01T00:00:01",
         "inputs": json.dumps({"file_path": "/skills/workflows/risk/run-risk/SKILL.md"})},
        {"run_type": "tool", "name": "read_file", "start_time": "2026-01-01T00:00:03",
         "inputs": json.dumps({"file_path": "/skills/references/pricing/engines.md"})},  # not a SKILL.md
    ]
    turn = _spans_to_turn_events(0, "do it", spans)
    assert turn["skills_routed"] == ["run-risk", "generate-report"]  # ordered by start_time, reference skipped


def test_tool_calls_exclude_meta_and_capture_args_results():
    spans = [
        {"run_type": "tool", "name": "task", "start_time": "1",
         "inputs": json.dumps({"subagent_type": "trader"}), "outputs": None},
        {"run_type": "tool", "name": "get_positions", "start_time": "2", "id": "sp1",
         "inputs": json.dumps({"portfolio_id": 4}),
         "outputs": _tool_output({"total_count": 2}, "get_positions", "tc9")},
        {"run_type": "llm", "name": "ChatAnthropic", "start_time": "3",
         "outputs": _llm_output("Here is your answer.")},
    ]
    turn = _spans_to_turn_events(0, "u", spans)
    assert [c["name"] for c in turn["tool_calls"]] == ["get_positions"]
    assert turn["tool_calls"][0]["args"] == {"portfolio_id": 4}
    assert turn["tool_results"][0]["content"] == {"total_count": 2}
    assert turn["tool_results"][0]["tool_call_id"] == "tc9"
    assert turn["response_text"] == "Here is your answer."


def test_artifacts_harvested_from_tool_content():
    spans = [
        {"run_type": "tool", "name": "write_report_artifact", "start_time": "1", "id": "sp2",
         "inputs": json.dumps({}),
         "outputs": _tool_output(
             {"file_path": "/r.md", "artifacts": [{"path": "/r.md", "kind": "text"}]},
             "write_report_artifact")},
    ]
    turn = _spans_to_turn_events(0, "u", spans)
    assert any(a.get("kind") == "text" for a in turn["artifacts"])


def test_error_tool_span_sets_error():
    spans = [
        {"run_type": "tool", "name": "get_positions", "start_time": "1", "id": "sp3",
         "status": "error", "error": "boom",
         "inputs": json.dumps({"portfolio_id": 4}), "outputs": None},
    ]
    turn = _spans_to_turn_events(0, "u", spans)
    assert turn["tool_results"][0]["error"] == "boom"


class _FakeStore:
    def __init__(self, roots, traces):
        self._roots = roots
        self._traces = traces

    def list_thread_traces(self, thread_id, *, limit=50, offset=0):
        return list(self._roots)

    def get_trace(self, trace_id):
        return self._traces.get(trace_id, [])


class _WF:
    id = "wf-x"

    class _S:
        def __init__(self, user):
            self.user = user

    steps = [_S("step one"), _S("step two")]


def test_transcript_from_trace_maps_roots_to_steps_in_order():
    roots = [
        {"trace_id": "T2", "start_time": "2026-01-01T00:00:05", "end_time": "2026-01-01T00:00:06"},
        {"trace_id": "T1", "start_time": "2026-01-01T00:00:01", "end_time": "2026-01-01T00:00:02"},
    ]
    traces = {
        "T1": [{"run_type": "tool", "name": "get_positions", "start_time": "1", "id": "a",
                "inputs": json.dumps({"portfolio_id": 4}),
                "outputs": _tool_output({"total_count": 2}, "get_positions", "tc1")}],
        "T2": [{"run_type": "llm", "name": "ChatAnthropic", "start_time": "2",
                "outputs": _llm_output("done")}],
    }
    model = ArenaModel(slug="m", zenmux_name="openai/x", display_name="M", default_config={})
    transcript = transcript_from_trace(99, _WF(), model, store=_FakeStore(roots, traces))
    assert transcript.model_id == "m"
    assert transcript.workflow_id == "wf-x"
    assert len(transcript.steps) == 2
    # chronological: T1 (earlier) is step 0
    assert transcript.steps[0].user == "step one"
    assert [c["name"] for c in transcript.steps[0].tool_calls] == ["get_positions"]
    assert transcript.steps[1].response_text == "done"


def test_transcript_from_trace_flushes_store_before_reading():
    """The background trace writer must be drained before harvesting."""
    events = []

    class _FlushStore(_FakeStore):
        def flush(self):
            events.append("flush")

        def list_thread_traces(self, thread_id, *, limit=50, offset=0):
            events.append("read")
            return list(self._roots)

    roots = [{"trace_id": "T1", "start_time": "1", "end_time": "2"}]
    traces = {"T1": [{"run_type": "llm", "name": "ChatAnthropic", "start_time": "1",
                      "outputs": _llm_output("hi")}]}
    model = ArenaModel(slug="m", zenmux_name="openai/x", display_name="M", default_config={})
    transcript_from_trace(1, _WF(), model, store=_FlushStore(roots, traces))
    assert events[0] == "flush"  # flush happens before the first read
    assert "read" in events


def test_transcript_from_trace_missing_root_records_error():
    roots = [{"trace_id": "T1", "start_time": "1", "end_time": "2"}]  # only 1 root, 2 steps
    traces = {"T1": [{"run_type": "llm", "name": "ChatAnthropic", "start_time": "1",
                      "outputs": _llm_output("hi")}]}
    model = ArenaModel(slug="m", zenmux_name="openai/x", display_name="M", default_config={})
    transcript = transcript_from_trace(1, _WF(), model, store=_FakeStore(roots, traces))
    assert len(transcript.steps) == 2
    assert transcript.steps[1].errors and transcript.steps[1].errors[0]["type"] == "missing_trace"
