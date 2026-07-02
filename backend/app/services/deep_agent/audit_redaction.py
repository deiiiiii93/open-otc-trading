"""Redaction before audit persistence (audit spec §5.1b).

The audit trail is append-only with no deletion UI, so nothing secret or bulky
may be persisted verbatim: secret-looking keys are masked, content/code bodies
are elided to {sha256, byte_len, head}, oversized payloads are truncated.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_SECRET_KEY_RE = re.compile(
    r"(token|password|secret|api[_-]?key|credential|authorization)", re.IGNORECASE
)

# Tool -> argument names whose values are content/code bodies to elide.
_BODY_ARGS = {
    "write_file": ("content",),
    "edit_file": ("content", "new_string", "old_string"),
    "run_python": ("code",),
    "execute": ("command",),
}

_HEAD_CHARS = 256
_MAX_SERIALIZED_BYTES = 8 * 1024
_TEXT_CAP = 2000


def _elide(value: str) -> dict[str, Any]:
    raw = value.encode("utf-8", errors="replace")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_len": len(raw),
        "head": value[:_HEAD_CHARS],
    }


def _redact_value(key: str, value: Any) -> tuple[Any, bool]:
    if _SECRET_KEY_RE.search(key):
        return "[REDACTED]", True
    if isinstance(value, dict):
        return _redact_dict(value)
    if isinstance(value, list):
        changed = False
        out = []
        for item in value:
            new, item_changed = _redact_value(key, item)
            out.append(new)
            changed = changed or item_changed
        return out, changed
    return value, False


def _redact_dict(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    out: dict[str, Any] = {}
    for key, value in data.items():
        new, key_changed = _redact_value(str(key), value)
        out[key] = new
        changed = changed or key_changed
    return out, changed


def redact_args(tool_name: str, args: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Return (persistable payload, redacted flag) for a tool call's args."""
    payload, redacted = _redact_dict(dict(args or {}))
    for body_arg in _BODY_ARGS.get(tool_name, ()):
        value = payload.get(body_arg)
        if isinstance(value, str):
            payload[body_arg] = _elide(value)
            redacted = True
    try:
        serialized = json.dumps(payload, default=str)
    except (TypeError, ValueError):
        serialized = repr(payload)
        payload = {"__repr__": serialized[:_MAX_SERIALIZED_BYTES]}
        redacted = True
    if len(serialized.encode("utf-8", errors="replace")) > _MAX_SERIALIZED_BYTES:
        payload = {
            "__truncated__": True,
            "head": serialized[: _MAX_SERIALIZED_BYTES // 4],
        }
        redacted = True
    return payload, redacted


def redact_text(text: str | None, cap: int = _TEXT_CAP) -> str | None:
    """Cap free-text previews (results/errors) before persistence."""
    if text is None:
        return None
    return text if len(text) <= cap else text[:cap] + "…[truncated]"
