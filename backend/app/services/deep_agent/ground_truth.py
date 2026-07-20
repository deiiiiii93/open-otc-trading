"""Capture authoritative tool results before they become compactable context."""
from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from typing import Any, TypeAlias

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from .artifact_access import ARTIFACT_DISCLOSURE_TOOL_NAMES
from .cas_backend import (
    ARTIFACT_REFERENCE_PROVENANCE,
    ContentAddressedFilesystemBackend,
)
from .envelopes import ToolGroup

logger = logging.getLogger(__name__)

_ToolResult: TypeAlias = ToolMessage | Command

GROUND_TRUTH_GROUPS = frozenset(
    {
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
        ToolGroup.DOMAIN_WRITE,
        ToolGroup.ASYNC_DISPATCH,
    }
)

# Reading a canonical artifact is already exact and would otherwise create an
# unbounded chain of "artifact describing an artifact read" rows.
ARTIFACT_ACCESS_TOOL_NAMES = ARTIFACT_DISCLOSURE_TOOL_NAMES
ARTIFACT_CONTENT_ACCESS_TOOL_NAMES = frozenset({"read_artifact"})


def ground_truth_tool_names(tools: Sequence[BaseTool]) -> frozenset[str]:
    return frozenset(
        tool.name
        for tool in tools
        if getattr(tool, "__capability_group__", None) in GROUND_TRUTH_GROUPS
        and tool.name not in ARTIFACT_ACCESS_TOOL_NAMES
    )


def compaction_protected_tool_names(tools: Sequence[BaseTool]) -> frozenset[str]:
    available = {tool.name for tool in tools}
    return ground_truth_tool_names(tools) | (
        ARTIFACT_CONTENT_ACCESS_TOOL_NAMES & available
    )


def _runtime_config(request: ToolCallRequest) -> dict[str, Any] | None:
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None)
    return config if isinstance(config, dict) else None


def _exact_content(message: ToolMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return json.dumps(
        message.content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _result_message(result: _ToolResult) -> ToolMessage | None:
    if isinstance(result, ToolMessage):
        return result
    update = result.update if isinstance(result.update, dict) else {}
    messages = update.get("messages") if isinstance(update, dict) else None
    if not isinstance(messages, list):
        return None
    return next((item for item in messages if isinstance(item, ToolMessage)), None)


def _source_reference_from_artifact_read(
    message: ToolMessage,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(_exact_content(message))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        artifact_id = int(payload["artifact_id"])
        content_hash = str(payload["content_hash"])
        generated_raw = payload["generated_at"]
        byte_size = int(payload.get("byte_size") or 0)
    except (KeyError, TypeError, ValueError):
        return None
    generated_at = generated_raw if isinstance(generated_raw, str) else ""
    digest = content_hash.removeprefix("sha256:")
    if (
        artifact_id <= 0
        or not content_hash.startswith("sha256:")
        or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest.lower())
        or not generated_at
        or byte_size < 0
    ):
        return None
    return {
        "artifact_id": artifact_id,
        "kind": str(payload.get("kind") or "artifact"),
        "content_hash": content_hash,
        "tool_name": payload.get("tool_name"),
        "tool_call_id": str(
            payload.get("tool_call_id") or f"artifact:{artifact_id}"
        ),
        "generated_at": generated_at,
        "observed_at": str(payload.get("observed_at") or generated_at),
        "data_as_of": payload.get("data_as_of"),
        "locator": str(payload.get("locator") or f"artifact:{artifact_id}"),
        "byte_size": byte_size,
        "summary": (
            dict(payload.get("summary") or {})
            if isinstance(payload.get("summary"), dict)
            else {}
        ),
    }


def _attach_reference(message: ToolMessage, reference: dict[str, Any]) -> ToolMessage:
    if (
        (message.additional_kwargs or {}).get("artifact_ref")
        and (message.additional_kwargs or {}).get("artifact_ref_provenance")
        == ARTIFACT_REFERENCE_PROVENANCE
    ):
        return message
    ref_json = json.dumps(
        reference,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    content = message.content
    if isinstance(content, str):
        projected: str | list[Any] = (
            f"{content}\n\n<artifact_ref>{ref_json}</artifact_ref>"
        )
    else:
        projected = [
            *content,
            {"type": "text", "text": f"<artifact_ref>{ref_json}</artifact_ref>"},
        ]
    metadata = dict(message.additional_kwargs or {})
    metadata["artifact_ref"] = reference
    metadata["artifact_ref_provenance"] = ARTIFACT_REFERENCE_PROVENANCE
    return message.model_copy(
        update={"content": projected, "additional_kwargs": metadata}
    )


def _command_with_reference(
    result: Command, reference: dict[str, Any]
) -> Command:
    update = result.update
    if not isinstance(update, dict):
        return result
    messages = update.get("messages")
    if not isinstance(messages, list):
        return result
    replaced = False
    projected = []
    for message in messages:
        if isinstance(message, ToolMessage) and not replaced:
            projected.append(_attach_reference(message, reference))
            replaced = True
        else:
            projected.append(message)
    if not replaced:
        return result
    return Command(
        goto=result.goto,
        graph=result.graph,
        resume=result.resume,
        update={**update, "messages": projected},
    )


class GroundTruthArtifactMiddleware(AgentMiddleware):
    """Persist exact results from server-classified evidence tools."""

    def __init__(
        self,
        tools: Sequence[BaseTool] = (),
        *,
        backend: Any | None = None,
    ) -> None:
        super().__init__()
        self.tool_names = ground_truth_tool_names(tools)
        self._groups = {
            tool.name: getattr(tool, "__capability_group__", None)
            for tool in tools
        }
        self.backend = backend or ContentAddressedFilesystemBackend()

    def _capture(
        self,
        request: ToolCallRequest,
        result: _ToolResult,
    ) -> _ToolResult:
        call = request.tool_call
        name = str(call.get("name") or "")
        message = _result_message(result)
        if name in ARTIFACT_CONTENT_ACCESS_TOOL_NAMES:
            if message is None:
                return result
            reference = _source_reference_from_artifact_read(message)
            if reference is None:
                return result
            if isinstance(result, ToolMessage):
                return _attach_reference(result, reference)
            return _command_with_reference(result, reference)
        if name not in self.tool_names:
            return result
        if message is None or (
            (message.additional_kwargs or {}).get("artifact_ref")
            and (message.additional_kwargs or {}).get("artifact_ref_provenance")
            == ARTIFACT_REFERENCE_PROVENANCE
        ):
            return result
        try:
            group = self._groups.get(name)
            reference = self.backend.capture_tool_result(
                tool_call_id=str(call.get("id") or message.tool_call_id),
                tool_name=name,
                content=_exact_content(message),
                tool_args=(
                    dict(call.get("args") or {})
                    if isinstance(call.get("args") or {}, dict)
                    else {}
                ),
                config=_runtime_config(request),
                classification=(
                    group.value if isinstance(group, ToolGroup) else "ground_truth"
                ),
            )
        except Exception:
            # The raw ToolMessage remains in state. Compaction knows the server-owned
            # ground-truth tool set and will refuse to evict it without this reference.
            logger.exception("Ground-truth capture failed for tool %s", name)
            return result
        if isinstance(result, ToolMessage):
            return _attach_reference(result, reference)
        return _command_with_reference(result, reference)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], _ToolResult],
    ) -> _ToolResult:
        return self._capture(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[_ToolResult]],
    ) -> _ToolResult:
        return self._capture(request, await handler(request))
