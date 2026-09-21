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
