"""Shared transcript extraction utilities for golden-workflow evaluation.

No dependency on the test tree (no _scripted_graph imports).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from app.golden_workflows.assertions import AssertionContext, evaluate_assertion


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class MatchStep(BaseModel):
    """A single turn in a MatchTranscript.

    Fields mirror the keys that extract_assertion_context consumes, plus
    the turn-level metadata (index, user, messages, errors) required for
    arena comparison.  ``model_dump()`` is a valid step_record dict for
    extract_assertion_context.
    """
    index: int
    user: str
    messages: list  # raw message dicts/objects — opaque payload
    tool_calls: list[dict]
    tool_results: list[dict]   # normalised: {name, tool_call_id, content, error?}
    skills_routed: list[str]
    artifacts: list[dict]
    task_ids: list[str]
    response_text: str
    errors: list


class MatchTranscript(BaseModel):
    """Full run transcript produced either from a live arena run or from replay.

    ``schema_version`` must be exactly 1 (Literal[1]) — any other value or its
    absence raises a ValidationError.
    """
    schema_version: Literal[1]
    run_id: int | None
    workflow_id: str
    model_id: str
    started_at: str | None
    finished_at: str | None
    steps: list[MatchStep]


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_content(raw: Any) -> dict:
    """Normalise a tool-result content value to a dict.

    If ``raw`` is already a dict, return it unchanged.
    Otherwise wrap it as ``{"raw": raw}`` so downstream path-extraction
    (``_dig``) and task_id harvesting always receive a dict.
    """
    if isinstance(raw, dict):
        return raw
    return {"raw": raw}


def _dedup_ordered(seq: list[str]) -> list[str]:
    """Return a de-duplicated copy of *seq* preserving first-occurrence order."""
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _harvest_task_ids(tool_results: list[dict]) -> list[str]:
    """Extract task_id from successful tool results.

    Skips results with a truthy 'error' key and only harvests task_id
    from content that is a dict.
    """
    ids = []
    for r in tool_results:
        if r.get("error"):
            continue
        content = r.get("content")
        if isinstance(content, dict):
            tid = content.get("task_id")
            if tid:
                ids.append(str(tid))
    return ids


# ---------------------------------------------------------------------------
# Public extraction functions
# ---------------------------------------------------------------------------

def extract_step_from_events(turn_events: dict) -> MatchStep:
    """Convert a turn-events dict into a MatchStep.

    Accepts a dict with the following keys (all optional except ``index``
    and ``user``):

    - ``index``: int — turn index (0-based)
    - ``user``: str — the user message that triggered this turn
    - ``messages``: list — raw LangChain message objects or pre-normalised
      dicts; stored opaquely (the assertion layer never reads this field)
    - ``tool_calls``: list[dict] — AI-issued tool calls ``{id, name, args}``
    - ``tool_results``: list[dict] — raw tool results; normalised on ingestion
      to ``{name, tool_call_id, content, error?}``
    - ``skills_routed``: list[str] — skills selected this turn (order-preserved,
      de-duplicated)
    - ``artifacts``: list[dict] — artefacts produced this turn
    - ``response_text``: str — final assistant text
    - ``errors``: list — error records produced this turn

    Normalisation rules applied here:

    1. ``tool_results[].content`` is wrapped to a dict if it is not already one.
    2. ``task_ids`` are harvested from **successful** tool results only
       (results with a truthy ``error`` key are skipped).
    3. ``skills_routed`` is de-duplicated (first occurrence wins).
    """
    index: int = turn_events.get("index", 0)
    user: str = turn_events.get("user", "")
    messages: list = list(turn_events.get("messages") or [])
    raw_tool_calls: list[dict] = list(turn_events.get("tool_calls") or [])
    raw_tool_results: list[dict] = list(turn_events.get("tool_results") or [])
    skills_raw: list[str] = list(turn_events.get("skills_routed") or [])
    artifacts: list[dict] = list(turn_events.get("artifacts") or [])
    response_text: str = turn_events.get("response_text") or ""
    errors: list = list(turn_events.get("errors") or [])

    # Normalise tool_results
    normalised_results: list[dict] = []

    for r in raw_tool_results:
        raw_content = r.get("content")
        norm_content = _normalise_content(raw_content)
        norm_r: dict = {
            "name": r.get("name", ""),
            "tool_call_id": r.get("tool_call_id", ""),
            "content": norm_content,
        }
        error = r.get("error")
        if error:
            norm_r["error"] = error
        normalised_results.append(norm_r)

    # Harvest task_ids from normalised results
    task_ids = _harvest_task_ids(normalised_results)

    skills_routed = _dedup_ordered(skills_raw)

    return MatchStep(
        index=index,
        user=user,
        messages=messages,
        tool_calls=raw_tool_calls,
        tool_results=normalised_results,
        skills_routed=skills_routed,
        artifacts=artifacts,
        task_ids=task_ids,
        response_text=response_text,
        errors=errors,
    )


def transcript_from_replay(loaded) -> MatchTranscript:
    """Build a MatchTranscript from a LoadedWorkflow's replay entries.

    Produces ``model_id="replay"`` and ``run_id=None``; there are no
    wall-clock timestamps for a canned replay.

    Each workflow step maps to exactly one MatchStep (in definition order).
    The step-index is zero-based.  The ``user`` field is taken from the
    workflow step's ``user`` string, not from the replay entry (the
    replay entry's ``ai`` block carries the assistant turn, not the human
    turn).

    This function is the replay→transcript producer used by Phase 3 /
    ``--source regression`` runs to obtain a baseline transcript without
    requiring a live LLM call.
    """
    steps: list[MatchStep] = []

    for i, wf_step in enumerate(loaded.workflow.steps):
        entry = loaded.fixtures.replay[wf_step.replay]

        # Build a turn-events dict in the same shape as extract_step_from_events
        # expects, sourced entirely from the ReplayEntry.
        turn_events = {
            "index": i,
            "user": wf_step.user,
            "messages": [],  # no raw message objects in replay
            "tool_calls": list(entry.ai.get("tool_calls", [])),
            "tool_results": list(entry.tool_results),
            "skills_routed": list(entry.skills_routed),
            "artifacts": list(entry.artifacts),
            "response_text": entry.response_text,
            "errors": [],
        }
        steps.append(extract_step_from_events(turn_events))

    return MatchTranscript(
        schema_version=1,
        run_id=None,
        workflow_id=loaded.workflow.id,
        model_id="replay",
        started_at=None,
        finished_at=None,
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Assertion-layer helpers (unchanged from Phase 1)
# ---------------------------------------------------------------------------

def extract_assertion_context(step_record: dict) -> AssertionContext:
    """Build an AssertionContext from a single graph-turn result dict.

    step_record keys (all optional, default to empty):
      - tool_calls:    list[dict]  — AI-issued tool calls
      - tool_results:  list[dict]  — normalised tool results {name, tool_call_id, content, error?}
      - skills_routed: list[str]   — skill names routed this turn
      - artifacts:     list[dict]  — artefact dicts {kind, ...}
      - response_text: str         — final AI response text

    task_ids: the content.task_id of each tool_result that carries one.
    """
    tool_calls = list(step_record.get("tool_calls") or [])
    tool_results = list(step_record.get("tool_results") or [])
    skills_routed = list(step_record.get("skills_routed") or [])
    artifacts = list(step_record.get("artifacts") or [])
    response_text = step_record.get("response_text") or ""

    task_ids = _harvest_task_ids(tool_results)

    return AssertionContext(
        response_text=response_text,
        tool_calls=tool_calls,
        tool_results=tool_results,
        skills_routed=skills_routed,
        artifacts=artifacts,
        task_ids=task_ids,
    )


def evaluate_step(step, ctx: AssertionContext) -> list[tuple[bool, str]]:
    """Evaluate all assertions for a single workflow step.

    Returns a list of (passed, message) tuples, one per assertion.
    """
    return [evaluate_assertion(a, ctx) for a in step.assertions]


def evaluate_success(success, session_ctx: AssertionContext) -> list[tuple[bool, str]]:
    """Evaluate all success assertions against the full-session context.

    Returns a list of (passed, message) tuples, one per assertion.
    """
    return [evaluate_assertion(a, session_ctx) for a in success.assertions]
