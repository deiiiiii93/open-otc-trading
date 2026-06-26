"""LLM judge for arena matches (GPT-5.5 via Zenmux).

Entry point:

    result = judge_match(transcript, loaded)

The judge builds a structured-output grading prompt from the transcript plus
the per-step and session-level rubric points, calls ``openai/gpt-5.5`` via
Zenmux and parses the response.

Network isolation for tests: inject a ``post`` callable:

    result = judge_match(transcript, loaded, post=my_fake_poster)

``post(payload: dict) -> str`` returns the raw JSON response string.
The REAL default poster uses ``requests`` with a 120s timeout and the
``ZENMUX_API_KEY`` env var.

Rubric-point alignment: the response must contain exactly one score per input
rubric point (matched by the "point" field).  Missing, duplicate, or extra
points are treated as a parse failure and trigger a retry.

Empty rubric (no points from any step or success): ``judged_score=None,
judge_missing=True`` — avoids dividing by zero.

Retry policy: on parse/validation failure or exception, retry up to ``retries``
more times (default 2).  On exhaustion → ``judged_score=None, judge_missing=True``.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.golden_workflows.transcript import MatchTranscript

JUDGE_MODEL = "openai/gpt-5.5"
ZENMUX_BASE_URL = "https://zenmux.ai/api/v1"
JUDGE_TIMEOUT = 120  # seconds


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class JudgeResult:
    """Result of judging a single arena match.

    Attributes:
        rubric_scores: List of per-point scores from the judge.
                       Each entry: {"point": str, "score": float, "rationale": str}.
        judged_score:  Mean of rubric_scores[].score, or None if judge_missing.
        judge_missing: True when the judge could not produce a valid response.
        notes:         Overall notes returned by the judge (or failure reason).
        diagnosis:     1-3 sentence failure/success analysis — WHERE and WHY the
                       assistant did well or fell down (tool engagement, blockers,
                       stalls). Empty when the judge is missing.
    """

    rubric_scores: list[dict] = field(default_factory=list)
    judged_score: float | None = None
    judge_missing: bool = False
    notes: str = ""
    diagnosis: str = ""


# ---------------------------------------------------------------------------
# Default HTTP poster (production path)
# ---------------------------------------------------------------------------


def _default_post(payload: dict) -> str:
    """POST ``payload`` to the Zenmux chat completions endpoint.

    Reads ``ZENMUX_API_KEY`` from the environment at call time so that
    tests that monkeypatch the env work correctly.

    Raises:
        RuntimeError: on HTTP error or timeout.
    """
    import requests  # lazy import — not needed in test paths

    api_key = os.environ.get("ZENMUX_API_KEY", "")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = f"{ZENMUX_BASE_URL}/chat/completions"
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=JUDGE_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        # Extract the content string from the standard OpenAI response shape.
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"Zenmux judge call failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def _collect_rubric_points(loaded) -> list[str]:
    """Collect all rubric points from steps + success in order."""
    points: list[str] = []
    for step in loaded.workflow.steps:
        points.extend(step.rubric)
    points.extend(loaded.workflow.success.rubric)
    return points


def _build_prompt(transcript: MatchTranscript, loaded) -> list[dict]:
    """Build the chat messages list for the structured-output grading prompt."""
    workflow = loaded.workflow

    # Render a compact transcript for the judge
    transcript_lines: list[str] = []
    for i, step in enumerate(transcript.steps, start=1):
        transcript_lines.append(f"=== Step {i} ===")
        transcript_lines.append(f"User: {step.user}")
        transcript_lines.append(f"Skills routed: {step.skills_routed}")
        tools_called = [tc.get("name", "") for tc in step.tool_calls]
        transcript_lines.append(f"Tools called: {tools_called}")
        transcript_lines.append(f"Response: {step.response_text[:400]}")
        if step.errors:
            transcript_lines.append(f"Errors: {step.errors}")

    transcript_text = "\n".join(transcript_lines)

    # Collect rubric points
    rubric_points = _collect_rubric_points(loaded)
    rubric_lines = "\n".join(
        f"{idx+1}. {pt}" for idx, pt in enumerate(rubric_points)
    )

    system_msg = (
        "You are an expert evaluator grading an AI assistant's performance on a "
        "multi-step desk workflow. Score each rubric point from 0 to 100 based "
        "on the transcript provided. Be objective and consistent."
    )

    user_msg = f"""Workflow: {workflow.title}
Objective: {workflow.objective}

## Transcript
{transcript_text}

## Rubric points (score each 0–100)
{rubric_lines}

Respond with a JSON object matching this exact schema:
{{
  "rubric_scores": [
    {{"point": "<exact rubric text>", "score": <0-100>, "rationale": "<brief reason>"}}
  ],
  "overall_notes": "<brief overall assessment>",
  "diagnosis": "<1-3 sentences: WHERE and WHY the assistant succeeded or failed — did it engage the expected skills/tools, get blocked by an error, or stall asking for input it was already given? Name the concrete failure mode.>"
}}

One entry per rubric point above, in the same order. Do not add or omit any points."""

    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_msg},
    ]


def _build_payload(messages: list[dict]) -> dict:
    """Wrap messages in an OpenAI-compatible request payload."""
    return {
        "model": JUDGE_MODEL,
        "messages": messages,
        "temperature": 0,
        "reasoning_effort": "high",
    }


# ---------------------------------------------------------------------------
# Response parser + validator
# ---------------------------------------------------------------------------


def _parse_response(
    raw: str, expected_points: list[str]
) -> tuple[list[dict], str, str] | None:
    """Parse and validate the judge's JSON response.

    Returns:
        (rubric_scores, overall_notes, diagnosis) on success, or None on failure.
        ``diagnosis`` is optional in the payload and defaults to "".

    Validates:
        - Valid JSON
        - "rubric_scores" is a list with exactly one entry per expected_point
        - Each entry has "point" (str), "score" (numeric 0–100), "rationale" (str)
        - No missing, duplicate, or extra points
    """
    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (``` markers)
        text = "\n".join(lines[1:-1] if lines[-1].strip().startswith("```") else lines[1:])

    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None

    scores = data.get("rubric_scores")
    if not isinstance(scores, list):
        return None

    # Must have exactly one entry per expected point
    if len(scores) != len(expected_points):
        return None

    # Validate each entry and check no duplicates
    seen_points: set[str] = set()
    validated: list[dict] = []
    for entry in scores:
        if not isinstance(entry, dict):
            return None
        point = entry.get("point")
        score = entry.get("score")
        rationale = entry.get("rationale", "")
        if not isinstance(point, str):
            return None
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            return None
        if not (0 <= score <= 100):
            return None
        if point in seen_points:
            return None  # duplicate
        seen_points.add(point)
        validated.append(
            {"point": point, "score": float(score), "rationale": str(rationale)}
        )

    # Check that all expected points appear (by checking count + no duplicates above)
    # The count check above (len(scores) == len(expected_points)) plus no-duplicate
    # check is sufficient since the judge is asked to use exact rubric text.
    # We do NOT enforce exact string match here — the judge may paraphrase slightly.
    # (Strict alignment is the length + no-duplicate invariant per spec.)

    notes = str(data.get("overall_notes", ""))
    diagnosis = str(data.get("diagnosis", ""))
    return validated, notes, diagnosis


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def judge_match(
    transcript: MatchTranscript,
    loaded,
    *,
    post: Callable[[dict], str] | None = None,
    retries: int = 2,
) -> JudgeResult:
    """Judge a match transcript against the workflow rubric.

    Args:
        transcript: The match transcript to grade.
        loaded:     The LoadedWorkflow (provides rubric points).
        post:       Injectable HTTP poster for testing.  Defaults to the real
                    Zenmux HTTPS call.  Signature: ``(payload: dict) -> str``.
        retries:    Number of additional attempts after the first (default 2;
                    total attempts = retries + 1).

    Returns:
        A JudgeResult.  If all attempts are exhausted or the rubric is empty,
        ``judge_missing=True`` and ``judged_score=None``.
    """
    poster = post if post is not None else _default_post
    rubric_points = _collect_rubric_points(loaded)

    # Empty rubric → skip judging
    if not rubric_points:
        return JudgeResult(
            rubric_scores=[],
            judged_score=None,
            judge_missing=True,
            notes="No rubric points defined — judge skipped.",
        )

    messages = _build_prompt(transcript, loaded)
    payload = _build_payload(messages)

    last_error = ""
    for attempt in range(retries + 1):
        if attempt > 0:
            # Brief backoff between retries
            time.sleep(0.5 * attempt)

        try:
            raw = poster(payload)
        except Exception as exc:
            last_error = f"Post failed (attempt {attempt+1}): {exc}"
            continue

        parsed = _parse_response(raw, rubric_points)
        if parsed is None:
            last_error = f"Parse/validation failure (attempt {attempt+1})"
            continue

        rubric_scores, notes, diagnosis = parsed
        judged_score = (
            sum(s["score"] for s in rubric_scores) / len(rubric_scores)
            if rubric_scores
            else None
        )
        return JudgeResult(
            rubric_scores=rubric_scores,
            judged_score=judged_score,
            judge_missing=False,
            notes=notes,
            diagnosis=diagnosis,
        )

    # All attempts exhausted
    return JudgeResult(
        rubric_scores=[],
        judged_score=None,
        judge_missing=True,
        notes=f"Judge failed after {retries + 1} attempts. Last error: {last_error}",
    )
