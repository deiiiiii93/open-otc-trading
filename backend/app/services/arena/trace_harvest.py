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


# A turn that ran out of output budget. Both wire protocols report it in
# ``response_metadata`` under their own name — Anthropic ``stop_reason:
# "max_tokens"``, OpenAI ``finish_reason: "length"`` — so a detector that reads
# only one goes blind the moment the other protocol's budget is pinned.
#
# This is the ONLY reliable instrument. A truncated turn is invisible everywhere
# else we look: the HTTP call really did succeed, so the span is
# ``status=success`` and raises no error, which means ``_is_infra_blank`` (which
# corroborates blankness with step ERRORS) cannot see it and the match is
# recorded as a legitimate score. ``completion_tokens`` sitting exactly on the
# cap corroborates it, but only if you already know the cap; the reason field
# describes itself.
# Dotted paths, because the three protocols report this in three shapes. A
# detector that knows only one goes blind the moment a run uses another — and a
# confident ZERO is worse than the honest ``null`` this module is careful to
# preserve elsewhere, because it asserts the opposite of what was measured.
_TRUNCATION_REASONS = {
    "stop_reason": "max_tokens",                       # Anthropic protocol
    "finish_reason": "length",                         # OpenAI chat-completions
    # OpenAI Responses API: `finish_reason`/`stop_reason` are BOTH absent here.
    # Keyed on the nested reason rather than `status == "incomplete"` because a
    # response can be incomplete for reasons that are not the output budget; the
    # reason field describes itself.
    "incomplete_details.reason": "max_output_tokens",  # OpenAI Responses API
}


def _dig_md(md: dict, path: str) -> Any:
    """Read a dotted path out of response_metadata, tolerating any missing hop."""
    cur: Any = md
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _response_metadata(gen: dict) -> dict:
    if not isinstance(gen, dict):
        return {}
    kwargs = (gen.get("message") or {}).get("kwargs")
    md = kwargs.get("response_metadata") if isinstance(kwargs, dict) else None
    return md if isinstance(md, dict) else {}


def _llm_truncation(outputs_raw: Any) -> dict | None:
    """Return truncation evidence for an LLM span, or None if it completed.

    ``severed_tool_call`` is what makes a truncation expensive rather than
    merely wasteful: when the cap lands mid-emission the model HAD chosen its
    tool and the JSON argument was cut, so langchain downgrades the block from
    ``tool_call`` to ``invalid_tool_call``. The call never reaches the harness,
    the step produces nothing, and the assertions that graded that call score
    zero — which is how a single lost turn can take a whole axis with it.
    """
    parsed = _loads(outputs_raw) or {}
    try:
        gen = parsed["generations"][0][0]
    except (KeyError, IndexError, TypeError):
        return None
    md = _response_metadata(gen)
    reason = next(
        (f"{field}={_dig_md(md, field)}"
         for field, value in _TRUNCATION_REASONS.items()
         if _dig_md(md, field) == value),
        None,
    )
    if reason is None:
        return None
    content = (gen.get("message") or {}).get("kwargs", {}).get("content")
    blocks = (
        [c.get("type") for c in content if isinstance(c, dict)]
        if isinstance(content, list) else []
    )
    return {
        "reason": reason,
        "severed_tool_call": "invalid_tool_call" in blocks,
    }


def _llm_malformed_tool_calls(outputs_raw: Any) -> list[dict]:
    """Tool calls this LLM span emitted that the harness CANNOT dispatch.

    A provider can return HTTP 200 with a well-formed message whose tool calls
    carry an empty ``id`` and empty ``name``. deepagents' ``task()`` guards on
    exactly that, so the call never runs; the agent retries, loops to the
    recursion limit, and the transcript ends up blank. Nothing raises, so the
    step records no error, so ``_is_infra_blank`` cannot corroborate the
    blankness and the match is recorded ``scored`` — at the ~7.7 prohibition
    floor, which inaction earns by satisfying every ``tool_not_called`` check.

    Measured on run #122: ``deepseek-v4-flash`` at effort ``max`` over the
    OpenAI chat-completions protocol emitted 95 tool calls, **100% of them**
    malformed, and the harness executed none. The same model on the Responses
    API emitted 132 with zero malformed and scored 91.1 instead of 7.7. Five
    other models on the same gateway in the same window emitted 1,283 tool
    calls with zero malformed, so this is a per-route defect, not a gateway
    outage.

    ``arg_keys`` is retained because it identifies WHICH call shape is being
    mangled without storing the argument bodies: the run #122 evidence was 84
    of 95 carrying ``description``, i.e. the ``task()`` persona delegation.
    """
    parsed = _loads(outputs_raw) or {}
    try:
        gen = parsed["generations"][0][0]
    except (KeyError, IndexError, TypeError):
        return []
    kwargs = (gen.get("message") or {}).get("kwargs")
    calls = kwargs.get("tool_calls") if isinstance(kwargs, dict) else None
    if not isinstance(calls, list):
        return []
    out: list[dict] = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        no_id = not call.get("id")
        no_name = not call.get("name")
        if not (no_id or no_name):
            continue
        reason = ("empty_id_and_name" if no_id and no_name
                  else "empty_id" if no_id else "empty_name")
        args = call.get("args")
        out.append({
            "name": call.get("name") or "",
            "reason": reason,
            "arg_keys": sorted(args)[:8] if isinstance(args, dict) else [],
        })
    return out


def _spans_to_turn_events(index: int, user: str, spans: list[dict]) -> dict:
    tool_calls: list[dict] = []
    tool_results: list[dict] = []
    artifacts: list[dict] = []
    skill_spans: list[tuple[str, str]] = []
    errors: list[dict] = []
    truncations: list[dict] = []
    malformed_tool_calls: list[dict] = []
    response_text = ""

    for sp in spans:
        run_type = sp.get("run_type")
        name = sp.get("name", "")
        # Propagate span-level failures into turn errors — a provider/LLM span
        # that errors with no text and no tool calls is exactly the evidence
        # the arena's infra-blank gate needs (_is_infra_blank corroborates
        # blankness with step errors before excluding a match).
        if sp.get("status") == "error":
            errors.append({
                "span": run_type or "",
                "name": name,
                "error": sp.get("error") or "error",
            })
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
            clipped = _llm_truncation(sp.get("outputs"))
            if clipped is not None:
                # Deliberately NOT appended to ``errors``: that list feeds the
                # infra-blank gate, and a truncated match must stay SCORED and
                # visibly flagged rather than be swept to ``invalid``. Sweeping
                # it would silently shrink historical boards — Run #20 would
                # lose four contestants — and a truncation does not reliably
                # destroy a score anyway (run #114 scored 100.0 and 90.9 on two
                # workflows that truncated), so its presence is a caveat on the
                # measurement, not grounds to discard it.
                truncations.append({**clipped, "name": name})
            # Same absence discipline as truncation, and the same reason for
            # keeping it OUT of ``errors``: that list feeds the infra-blank
            # gate, and putting it there would sweep the match to ``invalid``
            # — silently shrinking any board that contains one, and asserting
            # an infra failure the provider never reported. It is a caveat on
            # the measurement, never grounds to discard it, and it NEVER
            # changes the score.
            malformed_tool_calls.extend(
                {**bad, "span": name}
                for bad in _llm_malformed_tool_calls(sp.get("outputs"))
            )

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
        "errors": errors,
        "truncations": truncations,
        "malformed_tool_calls": malformed_tool_calls,
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


_PORTFOLIO_CREATE_TOOLS = {"create_portfolio"}

# The only two tools that WRITE a named set file (both land in
# ``scenario_catalog.save_set``). Deliberately excludes ``run_scenario_test``,
# which merely names a set to execute — running a set is not evidence that this
# match created it, and treating it as such would delete real desk sets.
_SCENARIO_SET_WRITE_TOOLS = {"save_scenario_set", "generate_scenario_set"}


def _extract_portfolio_id(content: Any) -> int | None:
    """Dig the minted portfolio id out of a create_portfolio result.

    The tool wraps its payload as ``{ok, data:{...}}`` (same convention the
    workflow's ``data.kind`` assertions read), so the id lives at ``data.id``;
    the unwrapped form is accepted too for robustness.
    """
    if isinstance(content, dict):
        for scope in (content.get("data"), content):
            if isinstance(scope, dict) and isinstance(scope.get("id"), int):
                return scope["id"]
    return None


def collect_portfolio_ids_created(thread_id, store=None) -> set[int]:
    """Return the portfolio ids this thread's ``create_portfolio`` calls MINTED.

    Mirrors ``collect_rfq_ids_touched``: the caller intersects these with an
    "id > pre-match baseline" guard so only portfolios created BY THIS MATCH are
    ever deleted.

    Needed because a model-created portfolio is invisible to
    ``_purge_seeded_portfolios``, which is scoped to rows carrying
    ``ARENA_PORTFOLIO_TAG`` *and* sharing a current-bundle fixture name: the
    agent's own ``create_portfolio`` call tags nothing and names the row whatever
    the model chose. Run #58 accordingly left 23 orphan "Board Review" views in the
    real DB. That is not merely cosmetic — the golden workflows resolve books BY
    NAME, so every leaked near-homonym makes the NEXT match's name resolution
    harder than the last, biasing scores by position in the field.
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
            if sp.get("run_type") != "tool" or sp.get("name") not in _PORTFOLIO_CREATE_TOOLS:
                continue
            content, _name, _tcid = _parse_tool_output(sp.get("outputs"))
            pid = _extract_portfolio_id(content)
            if pid is not None:
                out.add(pid)
    return out


def collect_scenario_set_names_saved(thread_id, store=None) -> set[str]:
    """Return the scenario-set names this thread's set-WRITING tools saved.

    Mirrors ``collect_portfolio_ids_created``: the caller intersects these with a
    pre-match name baseline so only sets created BY THIS MATCH are ever deleted.

    Needed because a model-created set is invisible to ``_purge_seeded_trap_sets``,
    which removes only the exact reserved ``trap_absent_sets`` name. A model asked
    for a set that does not exist does not stop — it INVENTS one under a near-miss
    name, and that file then outlives the match. Measured: one 2026-07-09 session
    left ``stagflation-shock-2011-compact`` (45 scenarios) and ``-x10`` in the live
    library, and every later board saw them. That is not cosmetic — the flagship's
    step-8 trap asks for a set that must not exist, so a leaked near-homonym hands
    the next model a real set to run, and its 141 KB result then dominates the step
    (Run #109: 273 tool calls, 91 errors on the arm that tried to mine it).

    Evidence, not names: a set is only claimed when a WRITE tool reported saving it.
    """
    if store is None:
        from app.config import get_settings
        from app.services.tracing.store import get_trace_store
        store = get_trace_store(get_settings())
    if hasattr(store, "flush"):
        store.flush()

    out: set[str] = set()
    for root in store.list_thread_traces(thread_id, limit=1000):
        for sp in store.get_trace(root["trace_id"]):
            if (sp.get("run_type") != "tool"
                    or sp.get("name") not in _SCENARIO_SET_WRITE_TOOLS):
                continue
            content, _name, _tcid = _parse_tool_output(sp.get("outputs"))
            for scope in (content.get("data"), content):
                if isinstance(scope, dict) and isinstance(scope.get("name"), str):
                    name = scope["name"].strip()
                    if name:
                        out.add(name)
                    break
    return out


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
                "artifacts": [], "response_text": "", "truncations": [],
                "malformed_tool_calls": [],
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
