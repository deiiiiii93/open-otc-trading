"""Shared write-action taxonomy (audit spec §5.1a).

One classification, two consumers with different group sets:
- FanoutReadOnlyMiddleware blocks PAGE_ACTION too (include_page_action=True);
- AuditTrailMiddleware audits persistent writes only (include_page_action=False).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool

from .envelopes import ToolGroup

# deepagents filesystem/shell built-ins are NOT capability-gated; classify by name.
FS_WRITE_TOOLS = frozenset({"write_file", "edit_file", "execute"})

_GROUP_TO_CLASS = {
    ToolGroup.DOMAIN_WRITE: "domain_write",
    ToolGroup.ASYNC_DISPATCH: "async_dispatch",
    ToolGroup.PAGE_ACTION: "page_action",
}


def write_names_by_class(tools: Sequence[BaseTool]) -> dict[str, str]:
    """Map tool name -> write class for capability-gated write tools."""
    out: dict[str, str] = {}
    for t in tools:
        group = getattr(t, "__capability_group__", None)
        if isinstance(group, ToolGroup) and group in _GROUP_TO_CLASS:
            out[t.name] = _GROUP_TO_CLASS[group]
    return out


def classify_write_action(
    name: str,
    args: dict[str, Any] | None,
    gated: dict[str, str],
    *,
    include_page_action: bool,
) -> str | None:
    """Return the write class for a tool call, or None for reads."""
    if name == "run_python":
        return "artifact_write" if (args or {}).get("writes_artifacts") else None
    if name in FS_WRITE_TOOLS:
        return "fs_write"
    cls = gated.get(name)
    if cls == "page_action" and not include_page_action:
        return None
    return cls
