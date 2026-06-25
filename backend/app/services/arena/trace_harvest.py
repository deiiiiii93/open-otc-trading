"""Reconstruct a MatchTranscript from persisted trace spans.

The arena drives the real desk orchestrator; every turn's tool calls, LLM
output, and skill loads are persisted by the LocalTracer. This module reads
those spans back and emits the turn_events dicts that
``extract_step_from_events`` consumes.

skills_routed is GROUND TRUTH: the deep-agent loads each skill it follows via
``read_file`` on ``/skills/workflows/<domain>/<name>/SKILL.md``. A skill the
model acts on from its injected description alone (without read_file) is NOT
captured — an accepted edge case (multi-step golden workflows read the file).
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.golden_workflows.transcript import MatchTranscript, extract_step_from_events

SKILL_PATH_RE = re.compile(r"^/skills/workflows/.+/([a-z0-9-]+)/SKILL\.md$")
TOOL_OUTPUT_RE = re.compile(
    r"^content='(?P<content>.*)' name='(?P<name>[^']*)' tool_call_id='(?P<tcid>[^']*)'\s*$",
    re.DOTALL,
)
META_TOOLS = {"task", "read_file", "write_todos"}


def _loads(raw: Any) -> Any:
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _parse_tool_output(outputs_raw: Any) -> tuple[dict, str | None, str | None]:
    """Parse a tool span's serialized ToolMessage output.

    Returns (content_dict, tool_name, tool_call_id). Falls back to
    ``{"raw": <str>}`` content when the inner payload is not a JSON object.
    """
    parsed = _loads(outputs_raw)
    output_val = parsed.get("output") if isinstance(parsed, dict) else None
    # Some tool spans record a structured dict output ({"output": {...}}) rather
    # than the stringified ToolMessage repr; use the dict directly so task_id /
    # artifact payloads survive the harvest.
    if isinstance(output_val, dict):
        return output_val, output_val.get("name"), output_val.get("tool_call_id")
    if not isinstance(output_val, str):
        return {}, None, None
    m = TOOL_OUTPUT_RE.match(output_val)
    if not m:
        return {"raw": output_val}, None, None
    content = _loads(m.group("content"))
    if not isinstance(content, dict):
        content = {"raw": m.group("content")}
    return content, m.group("name"), m.group("tcid")


def _llm_text(outputs_raw: Any) -> str:
    parsed = _loads(outputs_raw) or {}
    try:
        return parsed["generations"][0][0]["text"] or ""
    except (KeyError, IndexError, TypeError):
        return ""


def _spans_to_turn_events(index: int, user: str, spans: list[dict]) -> dict:
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    artifacts: list[dict] = []
    skill_spans: list[tuple[str, str]] = []
    response_text = ""

    for sp in spans:
        run_type = sp.get("run_type")
        name = sp.get("name", "")
        if run_type == "tool" and name == "read_file":
            inp = _loads(sp.get("inputs"))
            fp = inp.get("file_path", "") if isinstance(inp, dict) else ""
            mm = SKILL_PATH_RE.match(fp or "")
            if mm:
                skill_spans.append((sp.get("start_time") or "", mm.group(1)))
        elif run_type == "tool" and name not in META_TOOLS:
            inp = _loads(sp.get("inputs"))
            args = inp if isinstance(inp, dict) else {}
            content, _tname, tcid = _parse_tool_output(sp.get("outputs"))
            call_id = tcid or sp.get("id")
            tool_calls.append({"id": call_id, "name": name, "args": args})
            result = {"name": name, "tool_call_id": call_id, "content": content}
            if sp.get("status") == "error":
                result["error"] = sp.get("error") or "tool error"
            tool_results.append(result)
            embedded = content.get("artifacts") if isinstance(content, dict) else None
            if isinstance(embedded, list):
                artifacts.extend(a for a in embedded if isinstance(a, dict))
        elif run_type == "llm":
            txt = _llm_text(sp.get("outputs"))
            if txt:
                response_text = txt

    skills_routed = [s for _, s in sorted(skill_spans, key=lambda x: x[0])]
    return {
        "index": index,
        "user": user,
        "messages": [],
        "tool_calls": tool_calls,
        "tool_results": tool_results,
        "skills_routed": skills_routed,
        "artifacts": artifacts,
        "response_text": response_text,
        "errors": [],
    }


def transcript_from_trace(thread_id, workflow, model, *, store=None) -> MatchTranscript:
    """Build a MatchTranscript for *thread_id* by reading its trace spans.

    Root traces (one orchestrator run per turn) map 1:1 to workflow steps in
    chronological order. A step with no matching root records a ``missing_trace``
    error rather than silently dropping.
    """
    if store is None:
        from app.config import get_settings
        from app.services.tracing.store import get_trace_store
        store = get_trace_store(get_settings())

    # The LocalTracer enqueues spans to a background writer thread; drain it so
    # the just-completed turns are durable before we read them back. Guard with
    # hasattr so injected fake stores (tests) don't need a flush method.
    if hasattr(store, "flush"):
        store.flush()

    roots = sorted(
        store.list_thread_traces(thread_id, limit=1000),
        key=lambda r: r.get("start_time") or "",
    )

    steps = []
    for i, wf_step in enumerate(workflow.steps):
        if i < len(roots):
            spans = store.get_trace(roots[i]["trace_id"])
            turn = _spans_to_turn_events(i, wf_step.user, spans)
        else:
            turn = {
                "index": i, "user": wf_step.user, "messages": [],
                "tool_calls": [], "tool_results": [], "skills_routed": [],
                "artifacts": [], "response_text": "",
                "errors": [{"type": "missing_trace", "step": i}],
            }
        steps.append(extract_step_from_events(turn))

    started_at = roots[0].get("start_time") if roots else None
    finished_at = roots[-1].get("end_time") if roots else None
    return MatchTranscript(
        schema_version=1,
        run_id=None,
        workflow_id=workflow.id,
        model_id=model.slug,
        started_at=started_at,
        finished_at=finished_at,
        steps=steps,
    )
