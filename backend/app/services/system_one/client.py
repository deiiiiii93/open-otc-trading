"""System One (TypeSafe Jev) client — the single exit for every Jev request.

Spec: docs/superpowers/specs/2026-09-21-jev-system-one-design.md §0.

Jev is non-generative: `state` + typed questions in, calibrated probabilities
out. It cannot generate text, call tools or see images, so it is never a persona
model, an arena contestant, or a source of numbers. ZenMux serves it on
`/systemone`, a route absent from `/v1/models`.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Union

from ...config import Settings, get_settings
from ..deep_agent.audit_redaction import mask_secrets, redact_text

UNAVAILABLE_REASONS: tuple[str, ...] = (
    "no_key", "state_too_large", "timeout", "http_error", "bad_response",
)
_DETAIL_CHARS = 500


@dataclass(frozen=True)
class Noul:
    """Yes/no: the answer is the probability that `instructions` is TRUE."""

    instructions: str


@dataclass(frozen=True)
class Choice:
    """One of N options; `criteria` maps option -> description."""

    instructions: str
    criteria: Mapping[str, str]


@dataclass(frozen=True)
class Score:
    """Ordered levels, low -> high, 0-based. Describe situations, not degrees."""

    instructions: str
    criteria: Sequence[str]


Question = Union[Noul, Choice, Score]


@dataclass(frozen=True)
class NoulAnswer:
    probability: float


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True)
class ScoreAnswer:
    score: float
    confidence: float
    probabilities: Mapping[str, float]
    levels: int

    @property
    def normalized(self) -> float:
        """`score` mapped onto [0, 1]."""
        return self.score / (self.levels - 1)


Answer = Union[NoulAnswer, ChoiceAnswer, ScoreAnswer]


@dataclass(frozen=True)
class SystemOneResult:
    answers: dict[str, Answer]  # keyed as asked
    model: str                  # the response's "model", else the requested one
    latency_ms: int             # wall clock around post()


class SystemOneUnavailable(RuntimeError):
    """The call could not be made or answered. `reason` is persisted verbatim."""

    def __init__(
        self, reason: str, *, latency_ms: int | None = None, detail: str | None = None
    ) -> None:
        if reason not in UNAVAILABLE_REASONS:
            raise ValueError(f"unknown System One unavailable reason {reason!r}")
        super().__init__(f"System One unavailable: {reason}")
        self.reason = reason
        self.latency_ms = latency_ms
        self.detail = detail


def is_enabled(settings: Settings | None = None) -> bool:
    """The MASTER switch only. Callers also check their own feature switch (D17)."""
    return bool((settings or get_settings()).system_one_enabled)


def cap(text: str, n: int) -> str:
    """Bound `text` to `n` code points; a capped value is exactly `n` long, ending "…"."""
    if len(text) <= n:
        return text
    return text[: n - 1] + "…"


def serialize_state(state: Any) -> str:
    """The one serialization `ask()` measures and sends.

    `default=str` is the safety net for a datetime / Decimal / Enum inside tool
    args, so an odd argument never crashes a caller.
    """
    return json.dumps(
        state, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str
    )


def _nonempty(text: Any) -> bool:
    return isinstance(text, str) and bool(text.strip())


def validate_questions(questions: Mapping[str, Question]) -> None:
    """Raise ValueError on a programmer error.

    The server answers a malformed body with an opaque
    `400 "Server encountered an unexpected error"`, so catch it locally.
    """
    if not questions:
        raise ValueError("System One: questions must not be empty")
    for key, question in questions.items():
        if not _nonempty(key):
            raise ValueError("System One: question keys must be non-empty strings")
        if not isinstance(question, (Noul, Choice, Score)):
            raise ValueError(f"System One: question {key!r} has unsupported type")
        if not _nonempty(question.instructions):
            raise ValueError(f"System One: question {key!r} has empty instructions")
        if isinstance(question, Choice):
            if not 2 <= len(question.criteria) <= 255:
                raise ValueError(f"System One: choice {key!r} needs 2-255 options")
            for option, description in question.criteria.items():
                if not _nonempty(option) or not _nonempty(description):
                    raise ValueError(f"System One: choice {key!r} has an empty option")
        if isinstance(question, Score):
            if not 2 <= len(question.criteria) <= 10:
                raise ValueError(f"System One: score {key!r} needs 2-10 levels")
            if not all(_nonempty(level) for level in question.criteria):
                raise ValueError(f"System One: score {key!r} has an empty level")


def question_payload(question: Question) -> dict[str, Any]:
    if isinstance(question, Noul):
        return {"type": "noul", "instructions": question.instructions}
    if isinstance(question, Choice):
        return {
            "type": "choice",
            "instructions": question.instructions,
            "criteria": dict(question.criteria),
        }
    return {
        "type": "score",
        "instructions": question.instructions,
        "criteria": list(question.criteria),
    }


# --- transport ---------------------------------------------------------------

PostFn = Callable[[str, dict, float], Any]


def requests_post(url: str, payload: dict, timeout: float) -> Any:
    """Production transport. Its exception contract makes reason-mapping exact:
    TimeoutError -> timeout; ValueError (a 2xx with a non-JSON body) ->
    bad_response; anything else (RuntimeError) -> http_error.
    """
    import requests  # lazy: never needed on a test path

    headers = {
        "Authorization": f"Bearer {os.environ.get('ZENMUX_API_KEY', '').strip()}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=timeout)
    except requests.Timeout as exc:
        raise TimeoutError(str(exc)) from exc
    except requests.RequestException as exc:
        # Several RequestExceptions (InvalidURL, MissingSchema, ...) are ALSO
        # ValueErrors; re-raise as RuntimeError so they map to http_error, not
        # to bad_response.
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response.json()  # ValueError on a non-JSON body -> bad_response


#: Module-level so the test suite can replace it (conftest `_no_live_system_one`).
_default_post: PostFn = requests_post


# --- response validation ----------------------------------------------------

class _BadResponse(ValueError):
    """A 2xx body that fails validation (internal)."""


def _unit(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _BadResponse(f"{what} is not a number")
    number = float(value)
    if not 0.0 <= number <= 1.0:  # NaN fails this too
        raise _BadResponse(f"{what} is outside [0, 1]")
    return number


def _probabilities(raw: Any, allowed: Sequence[str], what: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise _BadResponse(f"{what}.probabilities is missing or not an object")
    unknown = set(raw) - set(allowed)
    if unknown:
        raise _BadResponse(f"{what}.probabilities has unknown keys {sorted(map(str, unknown))}")
    # A missing key reads as 0.0: only 3-option / 3-level answers were ever
    # observed, and rejecting an omitted zero would make a 16-family check a
    # permanent bad_response on an assumption nobody measured.
    return {
        key: _unit(raw[key], f"{what}.probabilities[{key}]") if key in raw else 0.0
        for key in allowed
    }


def _parse_answer(key: str, question: Question, raw: Any) -> Answer:
    if not isinstance(raw, dict):
        raise _BadResponse(f"answer {key!r} is not an object")
    expected = "noul" if isinstance(question, Noul) else (
        "choice" if isinstance(question, Choice) else "score")
    if raw.get("type") != expected:
        raise _BadResponse(f"answer {key!r} has type {raw.get('type')!r}, asked {expected!r}")
    if isinstance(question, Noul):
        return NoulAnswer(probability=_unit(raw.get("noul"), f"{key}.noul"))
    if isinstance(question, Choice):
        choice = raw.get("choice")
        if not isinstance(choice, str) or choice not in question.criteria:
            raise _BadResponse(f"{key}.choice {choice!r} is not an option asked")
        probabilities = _probabilities(raw.get("probabilities"), list(question.criteria), key)
        if choice not in raw["probabilities"]:
            raise _BadResponse(f"{key}.choice {choice!r} is absent from probabilities")
        return ChoiceAnswer(
            choice=choice,
            confidence=_unit(raw.get("confidence"), f"{key}.confidence"),
            probabilities=probabilities,
        )
    levels = len(question.criteria)
    score = raw.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not (
        0.0 <= float(score) <= levels - 1
    ):
        raise _BadResponse(f"{key}.score {score!r} is outside [0, {levels - 1}]")
    return ScoreAnswer(
        score=float(score),
        confidence=_unit(raw.get("confidence"), f"{key}.confidence"),
        probabilities=_probabilities(
            raw.get("probabilities"), [str(i) for i in range(levels)], key),
        levels=levels,
    )


def _parse_response(
    body: Any, questions: Mapping[str, Question]
) -> tuple[dict[str, Answer], str | None]:
    if not isinstance(body, dict):
        raise _BadResponse("response is not an object")
    answers = body.get("answers")
    if not isinstance(answers, dict):
        raise _BadResponse("answers is missing or not an object")
    parsed: dict[str, Answer] = {}
    for key, question in questions.items():
        if key not in answers:
            raise _BadResponse(f"answer {key!r} is missing")
        parsed[key] = _parse_answer(key, question, answers[key])
    model = body.get("model")
    return parsed, model if isinstance(model, str) and model else None


# --- ask ----------------------------------------------------------------------

def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _detail(exc: BaseException) -> str:
    return redact_text(mask_secrets(str(exc))[:_DETAIL_CHARS]) or ""


def ask(
    state: Any,
    questions: Mapping[str, Question],
    *,
    post: PostFn | None = None,
    settings: Settings | None = None,
) -> SystemOneResult:
    """One Jev request answering every question in parallel (D12).

    Returns all answers or raises: `ValueError` for a programmer error in
    `questions`, `SystemOneUnavailable` for everything else. Does NOT check the
    master switch — callers gate on `is_enabled()` plus their feature switch
    first, so "disabled" is never an exception (D17). Never truncates: an
    over-budget state is rejected, not cut (the callers' projections are the
    documented summarisation).
    """
    validate_questions(questions)
    cfg = settings or get_settings()
    if not os.environ.get("ZENMUX_API_KEY", "").strip():
        raise SystemOneUnavailable("no_key")
    # D16: the single sanitizer, over `state` only — questions are
    # developer-authored constants and rewriting one would change the predicate.
    serialized = serialize_state(mask_secrets(state))
    if len(serialized) > cfg.system_one_max_state_chars:
        raise SystemOneUnavailable(
            "state_too_large",
            detail=f"{len(serialized)} > {cfg.system_one_max_state_chars} chars",
        )
    payload = {
        "model": cfg.system_one_model,
        # Send exactly what was measured (the default=str round trip included).
        "state": json.loads(serialized),
        "questions": {key: question_payload(q) for key, q in questions.items()},
    }
    url = f"{cfg.system_one_base_url.rstrip('/')}/systemone"
    post_fn = post or _default_post
    started = time.monotonic()
    try:
        body = post_fn(url, payload, float(cfg.system_one_timeout_seconds))
    except TimeoutError as exc:
        raise SystemOneUnavailable(
            "timeout", latency_ms=_elapsed_ms(started), detail=_detail(exc)) from exc
    except ValueError as exc:
        raise SystemOneUnavailable(
            "bad_response", latency_ms=_elapsed_ms(started), detail=_detail(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — every other transport failure
        raise SystemOneUnavailable(
            "http_error", latency_ms=_elapsed_ms(started), detail=_detail(exc)) from exc
    latency_ms = _elapsed_ms(started)
    try:
        answers, model = _parse_response(body, questions)
    except _BadResponse as exc:
        raise SystemOneUnavailable(
            "bad_response", latency_ms=latency_ms, detail=_detail(exc)) from exc
    return SystemOneResult(
        answers=answers, model=model or cfg.system_one_model, latency_ms=latency_ms)
