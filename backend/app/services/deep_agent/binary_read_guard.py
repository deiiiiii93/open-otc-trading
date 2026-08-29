"""Keep raw binary out of the conversation history.

deepagents' ``read_file`` returns a langchain v1 media content block for any
file it considers binary::

    {"type": "file", "base64": "<bytes>", "mime_type": "application/pdf"}

``langchain_openai`` translates that correctly into the documented OpenAI wire
shape (``file.file_data``), so the client is not at fault. The gateways are:
measured 2026-08-29 against ZenMux with a real confirmation PDF, sending the
identical part inside a **tool** message returns HTTP 400 on three of four
routes, each in its own dialect --

    deepseek-v4-flash-vision  ``.messages[N]: file must have a file_id or file_data``
    gemini-3.7-flash          ``contents[N].parts[0].data: required oneof field 'data' ...``
    gpt-5.6-luna              ``Missing required parameter: 'input[N].output[0].text'``
    glm-5.3-flash             accepted

The same PDF inside a **user** message is accepted by all but deepseek, so the
defect is specifically "a binary returned from a tool", which is the only shape
``read_file`` can produce.

Two properties make this worth a guard rather than a caveat:

* **It is unrecoverable.** The rejected message stays in the history, so every
  later turn re-sends it and draws the same 400. On arena run #1
  ``deepseek-v4-flash-vision`` read one PDF during step 3 and then made zero
  tool calls for steps 4-8 -- the model never saw another prompt. It looks like
  a model that gave up; it is a model that was never asked.
* **It is invisible to every gate we have.** Nothing truncates, so the
  truncation flag reads zero. The tool call itself is ``status=success``, so
  ``_is_infra_blank`` -- which corroborates blankness with step *errors* -- sees
  a healthy step. Only the provider span carries the 400.

The guard is deliberately **uniform, not per-route**. Letting the one tolerant
gateway through would hand that contestant an advantage conferred by its
gateway rather than by its ability, which is exactly the confound the arena
exists to avoid. It also costs nothing: this desk never reads documents by
pushing bytes into the prompt -- ``parse_trade_confirmation`` renders pages to
images and runs the real extraction pipeline, and image parts are accepted on
every route we measured.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)

_ToolResult = Any

#: Media block types deepagents' ``read_file`` can emit for a binary file.
MEDIA_BLOCK_TYPES = frozenset({"file", "image", "audio", "video"})

#: Payload keys a media block may carry. A block with none of these is already
#: empty and cannot be what poisoned the request.
_PAYLOAD_KEYS = ("base64", "data", "url", "file_id")

#: Routed prefix holding staged counterparty confirmations.
_CONFIRMATION_HINT_PREFIX = "/artifacts/uploads/confirmations"


def _media_blocks(content: Any) -> list[dict[str, Any]]:
    """Return the media blocks in ``content`` that actually carry bytes."""
    if not isinstance(content, list):
        return []
    found = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") not in MEDIA_BLOCK_TYPES:
            continue
        if any(block.get(key) for key in _PAYLOAD_KEYS):
            found.append(block)
    return found


def _describe(block: dict[str, Any]) -> str:
    mime = block.get("mime_type") or "application/octet-stream"
    payload = next((block.get(k) for k in _PAYLOAD_KEYS if block.get(k)), "")
    size = len(payload) if isinstance(payload, str) else 0
    return f"{mime}, ~{size} base64 chars"


def _guidance(path: str) -> str:
    """Tell the model what to do instead, in terms of this desk's tools."""
    if path.startswith(_CONFIRMATION_HINT_PREFIX) or "confirmation" in path.lower():
        return (
            "This is a counterparty confirmation. Call "
            "parse_trade_confirmation(paths=[...]) with this path -- it renders "
            "every page (including scanned ones) and extracts the trade terms. "
            "Then read the terms back with get_confirmation_batch."
        )
    return (
        "Use the tool that owns this file type rather than reading its bytes: "
        "parse_trade_confirmation for confirmation documents, and "
        "list_artifacts / inspect_artifact / read_artifact for stored artifacts."
    )


def _replacement_text(path: str, blocks: list[dict[str, Any]]) -> str:
    shapes = "; ".join(_describe(b) for b in blocks)
    return (
        f"Error: '{path}' is a binary file ({shapes}) and cannot be read into "
        f"the conversation. {_guidance(path)}"
    )


class BinaryReadGuardMiddleware(AgentMiddleware):
    """Replace binary ``read_file`` results with actionable text.

    Sits at ``wrap_tool_call`` beside the audit and booking-capture middlewares,
    which is the only seam that sees a subagent's tool calls (a persona runs in
    its own LangGraph checkpoint namespace, so scanning ``result["messages"]``
    in the orchestrator would miss it -- and on run #1 the poisoned reads
    happened in BOTH namespaces).
    """

    def _guard(self, request: ToolCallRequest, result: _ToolResult) -> _ToolResult:
        if not isinstance(result, ToolMessage):
            return result
        blocks = _media_blocks(result.content)
        if not blocks:
            return result

        args = (request.tool_call or {}).get("args") or {}
        path = str(args.get("file_path") or args.get("path") or "") or (
            str(result.additional_kwargs.get("read_file_path") or "<unknown>")
        )
        logger.info(
            "binary read guard: replaced %d media block(s) from %s (%s)",
            len(blocks),
            (request.tool_call or {}).get("name", "?"),
            path,
        )
        return result.model_copy(
            update={
                "content": _replacement_text(path, blocks),
                "status": "error",
            }
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], _ToolResult],
    ) -> _ToolResult:
        return self._guard(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[_ToolResult]],
    ) -> _ToolResult:
        return self._guard(request, await handler(request))
