"""LLM fact extractor + diff validation (spec §Extractor output contract)."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .config import MemoryConfig
from .normalize import normalize_content

_ALLOWED_SCOPE_TYPES = {"user", "book", "domain", "correction"}
_CATEGORY = re.compile(r"^[a-z0-9_-]+$")


class MalformedDiffError(ValueError):
    """Un-parseable extractor output — drop the whole diff, mark run failed."""


@dataclass
class MemoryDiff:
    add: list[dict] = field(default_factory=list)
    remove: list[int] = field(default_factory=list)
    update: list[dict] = field(default_factory=list)


def parse_diff(text: str) -> dict:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise MalformedDiffError(str(exc)) from exc
    if not isinstance(data, dict):
        raise MalformedDiffError("diff is not an object")
    return data


def _clamp(value, default=1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0  # malformed confidence (e.g. "high") → floor-drop, not max-trust
    return max(0.0, min(1.0, v))


def _clean_category(category, max_chars) -> str | None:
    if not category or not isinstance(category, str):
        return None
    category = category.strip().lower()
    if len(category) > max_chars or not _CATEGORY.match(category):
        return None
    return category


def validate_diff(raw, allowed_scopes, existing_ids, config: MemoryConfig) -> MemoryDiff:
    diff = MemoryDiff()
    seen_norm: set[str] = set()
    for item in raw.get("add", []) or []:
        if not isinstance(item, dict):
            continue
        scope_type = item.get("scope_type")
        if scope_type not in _ALLOWED_SCOPE_TYPES or scope_type not in allowed_scopes:
            continue
        content = item.get("content") or ""
        if len(content) > config.content_max_chars:   # too long -> drop (defense in depth)
            continue
        norm = normalize_content(content)
        if not norm or norm in seen_norm:
            continue
        confidence = _clamp(item.get("confidence", 1.0))
        if confidence < config.confidence_floor:       # below floor -> drop
            continue
        seen_norm.add(norm)
        diff.add.append({
            "content": content, "scope_type": scope_type, "confidence": confidence,
            "category": _clean_category(item.get("category"), config.category_max_chars)})
    for rid in raw.get("remove", []) or []:
        if isinstance(rid, int) and rid in existing_ids:
            diff.remove.append(rid)
    for upd in raw.get("update", []) or []:
        if not (isinstance(upd, dict) and upd.get("id") in existing_ids):
            continue
        if "content" in upd and len(upd.get("content") or "") > config.content_max_chars:
            continue                                    # too-long update -> drop the item
        clean = {"id": upd["id"]}
        if "content" in upd:
            clean["content"] = upd["content"]
        if "confidence" in upd:
            clean["confidence"] = _clamp(upd["confidence"])
        if "category" in upd:
            clean["category"] = _clean_category(upd["category"], config.category_max_chars)
        diff.update.append(clean)
    return diff


def build_extractor_prompt(window, existing, allowed_scopes) -> str:
    return "\n".join([
        "You extract durable, reusable facts about a trading desk for long-term memory.",
        "STORE: stable preferences, durable habits, confirmed corrections, conventions.",
        "NEVER store: transient orders, live positions/quantities, prices/quotes, "
        "credentials/secrets, PII, counterparty-confidential, one-off analysis.",
        f"Allowed scope_type values: {sorted(allowed_scopes)}.",
        "Return ONLY JSON: {\"add\":[{\"content\":..,\"scope_type\":..,\"confidence\":0-1,"
        "\"category\":..}],\"remove\":[id],\"update\":[{\"id\":..,\"content\":..}]}.",
        "Existing facts (for dedup/update/remove):",
        json.dumps([{"id": f.id, "scope_type": f.scope_type, "content": f.content,
                     "mutable": f.mutable} for f in existing]),
        "Conversation window:",
        json.dumps([{"role": m.get("role"), "content": m.get("content")} for m in window]),
    ])


def extract_facts(window, existing, allowed_scopes, *, llm: Callable[[str], str],
                  config: MemoryConfig) -> MemoryDiff:
    raw = parse_diff(llm(build_extractor_prompt(window, existing, allowed_scopes)))
    return validate_diff(raw, allowed_scopes, {f.id for f in existing}, config)
