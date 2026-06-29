"""Best-effort content-safety denylist (spec §Content safety)."""
from __future__ import annotations

import re
from collections.abc import Sequence

from .config import DEFAULT_DENYLIST


def is_memorable(
    content: str, denylist: Sequence[str] = DEFAULT_DENYLIST
) -> tuple[bool, str | None]:
    for pattern in denylist:
        if re.search(pattern, content or "", re.IGNORECASE):
            return False, pattern
    return True, None
