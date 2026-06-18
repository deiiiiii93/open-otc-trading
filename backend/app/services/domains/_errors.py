"""Shared structured refusal for domain write facades."""
from __future__ import annotations

from typing import Any


class DomainWriteError(ValueError):
    """Expected domain refusal.

    Tools translate this to ``{"ok": False, "error": <code>, "detail": ...}``
    so the agent can read the refusal and self-correct. Unexpected exceptions
    are NOT wrapped — they propagate to ToolErrorBoundaryMiddleware.
    """

    def __init__(self, error: str, detail: Any = None) -> None:
        super().__init__(error)
        self.error = error
        self.detail = detail
