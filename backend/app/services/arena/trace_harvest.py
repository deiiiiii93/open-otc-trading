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
        pass
    # The trace serializer can emit a Python-repr artifact: ``\'`` is not a valid
    # JSON escape, so an embedded one (e.g. inside a stringified error message)
    # breaks json.loads. JSON never legitimately contains ``\'``, so stripping it
    # is a safe one-shot repair before giving up.
    if isinstance(raw, str) and "\\'" in raw:
        try:
            return json.loads(raw.replace("\\'", "'"))
        except (json.JSONDecodeError, TypeError):
            return None
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
        # LangChain v3 serializes a ToolMessage as an lc-constructor dict:
        # {"lc":1,"type":"constructor","id":[...,"ToolMessage"],
        #  "kwargs":{"content":"<json>","name":..,"tool_call_id":..}}. The real
        # tool payload is the (JSON-string) ``kwargs.content`` — unwrap it so
        # tool_result_path / rfq-id harvest see the payload, not the envelope.
        if output_val.get("lc") and isinstance(output_val.get("kwargs"), dict):
            kw = output_val["kwargs"]
            inner = _loads(kw.get("content"))
            if isinstance(inner, dict):
                content = inner
            elif kw.get("content") is not None:
                content = {"raw": kw.get("content")}
            else:
                content = {}
            return content, kw.get("name"), kw.get("tool_call_id")
        return output_val, output_val.get("name"), output_val.get("tool_call_id")
    if not isinstance(output_val, str):
        return {}, None, None
    m = TOOL_OUTPUT_RE.match(output_val)
    if not m:
        # Some tool spans record the bare output payload (no ToolMessage repr
        # wrapper). Parse it directly so tool_result_path assertions can
        # introspect nested tool results instead of seeing an opaque string.
        # A bare domain payload's own ``name``/``tool_call_id`` keys are business
        # data (e.g. a portfolio named "Control"), NOT the tool identity — never
        # mine them here. Return None so the caller uses the span's own name/id.
        direct = _loads(output_val)
        if isinstance(direct, dict):
            return direct, None, None
        return {"raw": output_val}, None, None
    content = _loads(m.group("content"))
    if not isinstance(content, dict):
        content = {"raw": m.group("content")}
    return content, m.group("name"), m.group("tcid")


def _message_content_text(gen: dict) -> str:
    """Extract assistant text from a generation's AIMessage payload.

    LangChain stores the message under ``generation.message.kwargs.content``,
    which is either a plain string or a list of ``{type: "text", text: ...}``
    content blocks (anthropic/openai v1 shape).
    """
    msg = gen.get("message") if isinstance(gen, dict) else None
    kwargs = msg.get("kwargs") if isinstance(msg, dict) else None
    content = kwargs.get("content") if isinstance(kwargs, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _llm_text(outputs_raw: Any) -> str:
    parsed = _loads(outputs_raw) or {}
    try:
        gen = parsed["generations"][0][0]
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(gen, dict):
        return ""
    # Prefer the flat ``text`` field; fall back to the AIMessage content blocks,
    # which is where ChatOpenAI/Anthropic v1 keep the text when ``text`` is empty.
    return gen.get("text") or _message_content_text(gen)


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


# RFQ tools whose outputs carry an rfq id. These TOUCH an rfq (create OR update,
# quote, submit) — touched != created, so run_match filters by an id baseline and an
# arena client sentinel before deleting. Names are the tools' ``.name`` (no _tool
# suffix), matching the raw span name recorded in the trace.
_RFQ_TOOLS = {"create_or_update_rfq_draft", "quote_rfq", "submit_rfq_for_approval"}


def _extract_rfq_id(content: Any) -> int | None:
    if isinstance(content, dict):
        for key in ("rfq_id", "id"):
            v = content.get(key)
            if isinstance(v, int):
                return v
    return None


def collect_rfq_ids_touched(thread_id, store=None) -> set[int]:
    """Return the rfq ids appearing in this thread's RFQ-tool span outputs.

    These are ids the agent TOUCHED (created or merely quoted/submitted/updated).
    The caller (run_match) intersects this with an "id > pre-match baseline" guard
    and an arena client sentinel to delete only RFQs created BY THIS MATCH — never a
    pre-existing real or seeded RFQ the agent referenced. Needed because RFQ has no
    portfolio_id/position_id column and direct book_position leaves Position.rfq_id
    null, so the portfolio-scoped purge cannot reach them.
    """
    if store is None:
        from app.config import get_settings
        from app.services.tracing.store import get_trace_store
        store = get_trace_store(get_settings())
    if hasattr(store, "flush"):
        store.flush()

    out: set[int] = set()
    for root in store.list_thread_traces(thread_id, limit=1000):
        for sp in store.get_trace(root["trace_id"]):
            if sp.get("run_type") != "tool" or sp.get("name") not in _RFQ_TOOLS:
                continue
            content, _name, _tcid = _parse_tool_output(sp.get("outputs"))
            rid = _extract_rfq_id(content)
            if rid is not None:
                out.add(rid)
    return out


def transcript_from_trace(thread_id, workflow, model, *, store=None) -> MatchTranscript:
    """Build a MatchTranscript for *thread_id* by reading its trace spans.

    Root traces (one orchestrator run per turn) map 1:1 to workflow steps in
    chronological order — the arena drives exactly one YOLO turn per step. A step
    with no matching root records a ``missing_trace`` error rather than silently
    dropping.
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
