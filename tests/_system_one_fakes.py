"""Test doubles for System One (Jev).

No test may reach the real POST — conftest's autouse `_no_live_system_one`
fails any that tries. Inject one of these as `post=`, or patch
`app.services.system_one.client._default_post` with one.
"""
from __future__ import annotations

import copy
from typing import Any

#: Verbatim shape of the 2026-09-21 probe response (spec §0).
PROBE_RESPONSE: dict[str, Any] = {
    "model": "typesafe/jev-1.13",
    "answers": {
        "q_noul": {"type": "noul", "noul": 0.95},
        "q_score": {"type": "score", "score": 1.98, "confidence": 0.98,
                    "legend": {"0": "…", "1": "…", "2": "…"},
                    "probabilities": {"0": 0, "1": 0.01, "2": 0.99}},
        "q_choice": {"type": "choice", "choice": "ops", "confidence": 1,
                     "probabilities": {"ops": 1, "trader": 0, "risk": 0}},
    },
    "usage": {"input_tokens": 517, "output_tokens": 70},
}


class FakePost:
    """Canned-response `post` seam; records every (url, payload, timeout)."""

    def __init__(self, response: Any = None, *, exc: BaseException | None = None) -> None:
        self.response = PROBE_RESPONSE if response is None else response
        self.exc = exc
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append((url, copy.deepcopy(payload), timeout))
        if self.exc is not None:
            raise self.exc
        return copy.deepcopy(self.response)


class JevPost:
    """Answers every `noul` question: probability `probs.get(key, 0.05)`.

    Set `.exc` to raise instead, or `.response` to return a canned body.
    `.calls` holds each request payload (deep-copied).
    """

    def __init__(self, probs: dict[str, float] | None = None) -> None:
        self.probs = dict(probs or {})
        self.exc: BaseException | None = None
        self.response: Any = None
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append(copy.deepcopy(payload))
        if self.exc is not None:
            raise self.exc
        if self.response is not None:
            return copy.deepcopy(self.response)
        return {
            "model": "typesafe/jev-1.13",
            "answers": {
                key: {"type": "noul", "noul": self.probs.get(key, 0.05)}
                for key in payload["questions"]
            },
        }


class ScorePost:
    """Answers the keep-alive `score` question with `score` / `confidence`.

    `.exc` raises instead; `.bad_for` holds fact contents that get a malformed
    reply (a per-row bad_response).
    """

    def __init__(self, score: float = 3.0, confidence: float = 0.9) -> None:
        self.score = score
        self.confidence = confidence
        self.exc: BaseException | None = None
        self.bad_for: set[str] = set()
        self.calls: list[dict] = []

    def __call__(self, url: str, payload: dict, timeout: float) -> Any:
        self.calls.append(copy.deepcopy(payload))
        if self.exc is not None:
            raise self.exc
        if payload["state"].get("fact") in self.bad_for:
            return {"answers": {}}
        levels = len(payload["questions"]["keep_alive"]["criteria"])
        probabilities = {str(i): 0.0 for i in range(levels)}
        probabilities[str(min(levels - 1, round(self.score)))] = 1.0
        return {
            "model": "typesafe/jev-1.13",
            "answers": {"keep_alive": {
                "type": "score", "score": self.score, "confidence": self.confidence,
                "probabilities": probabilities,
            }},
        }
