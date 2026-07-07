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


def test_parse_tool_output_bare_json_string_is_parsed():
    # Some tool spans record the bare JSON output (no ToolMessage repr wrapper);
    # it must be parsed into a dict so tool_result_path can introspect nested
    # results, not stored as an opaque {"raw": ...} string.
    raw = json.dumps({"output": json.dumps(
        {"id": 1, "status": "completed",
         "results": {"var_cvar": {"cvar": -10918.13}}})})
    content, _, _ = _parse_tool_output(raw)
    assert content["results"]["var_cvar"]["cvar"] == -10918.13


def test_parse_tool_output_repairs_python_repr_escape():
    # The trace serializer can emit a Python-repr ``\'`` escape (invalid JSON)
    # inside an embedded error string; _loads repairs it so the surrounding
    # structured payload still parses.
    payload = "{\"status\": \"completed\", \"note\": \"\\'dict\\' object\", \"pnl\": -5.0}"
    raw = json.dumps({"output": payload})
    content, _, _ = _parse_tool_output(raw)
    assert content["pnl"] == -5.0
    assert content["status"] == "completed"


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




# ---------------------------------------------------------------------------
# Span-error propagation (flagship v2 infra-blank evidence)
# ---------------------------------------------------------------------------


def test_errored_llm_span_propagates_into_turn_errors():
    """A provider/LLM span that fails with no output must leave error evidence
    in the turn — the arena infra-blank gate corroborates blankness with it."""
    from app.services.arena.trace_harvest import _spans_to_turn_events

    spans = [{
        "run_type": "llm", "name": "ChatOpenAI",
        "status": "error", "error": "402 quota exceeded",
        "outputs": None,
    }]
    turn = _spans_to_turn_events(0, "user turn", spans)
    assert turn["tool_calls"] == []
    assert turn["response_text"] == ""
    assert turn["errors"] and turn["errors"][0]["error"] == "402 quota exceeded"
    assert turn["errors"][0]["span"] == "llm"


def test_ok_spans_leave_errors_empty():
    from app.services.arena.trace_harvest import _spans_to_turn_events

    spans = [{"run_type": "llm", "name": "ChatOpenAI", "status": "ok",
              "outputs": None}]
    turn = _spans_to_turn_events(0, "user turn", spans)
    assert turn["errors"] == []


def test_record_answer_survives_trace_harvest_into_answer_fields():
    """BLOCKING plumbing gate: a record_answer tool span, as the tracer persists it,
    must survive the harvest into ctx.tool_calls where the answer_field_* checks read
    it — including the _tool-suffixed name the live registry emits. Proves the live
    trace→transcript→score path, not just synthetic transcripts / hand-edited replay."""
    from app.golden_workflows.transcript import extract_step_from_events, extract_assertion_context
    from app.golden_workflows.assertions import answer_fields, evaluate_assertion
    from app.golden_workflows.schema import _AnswerFieldQuotes, _AnswerFieldEquals

    spans = [
        {"run_type": "tool", "name": "get_latest_risk_run_tool", "start_time": "1",
         "inputs": json.dumps({"portfolio_id": 6}),
         "outputs": _tool_output({"found": True}, "get_latest_risk_run_tool")},
        {"run_type": "tool", "name": "record_answer_tool", "start_time": "2",
         "inputs": json.dumps({"answer": {"hotspot": "AAPL", "delta": 573.3467058766552}}),
         "outputs": _tool_output(
             {"recorded": True, "fields": {"hotspot": "AAPL", "delta": 573.3467058766552}},
             "record_answer_tool")},
    ]
    turn = _spans_to_turn_events(0, "what's the hotspot?", spans)
    step = extract_step_from_events(turn)
    actx = extract_assertion_context(step.model_dump())

    fields = answer_fields(actx)
    assert fields.get("hotspot") == "AAPL"
    assert fields.get("delta") == 573.3467058766552

    q = _AnswerFieldQuotes(type="answer_field_quotes", field="delta", value=573.3467058766552)
    assert evaluate_assertion(q, actx)[0] is True
    e = _AnswerFieldEquals(type="answer_field_equals", field="hotspot", equals="AAPL")
    assert evaluate_assertion(e, actx)[0] is True
