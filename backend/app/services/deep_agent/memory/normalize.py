"""Dedup normalization (spec §Dedup normalization)."""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")


def normalize_content(s: str) -> str:
    folded = unicodedata.normalize("NFKC", (s or "").strip()).casefold()
    return _WS.sub(" ", folded).strip()
