"""Backend tool: declare pickable reply buttons for the next assistant turn.

The LLM calls ``propose_reply_options`` immediately before its final reply
whenever it is asking the user to choose between 2-5 alternatives. The
orchestrator captures the tool's input arguments after Pydantic validation
and writes them onto the persisted assistant message as
``meta["reply_options"]``. The tool itself is a pure declaration: it does
not mutate state.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

_LABEL_MAX = 56
_DESCRIPTION_MAX = 240
_VALUE_MAX = 400
_MIN_OPTIONS = 2
_MAX_OPTIONS = 5


class ReplyOptionSpec(BaseModel):
    label: str = Field(..., min_length=1, max_length=_LABEL_MAX)
    description: str | None = Field(None, max_length=_DESCRIPTION_MAX)
    value: str | None = Field(None, max_length=_VALUE_MAX)


class ProposeReplyOptionsInput(BaseModel):
    options: list[ReplyOptionSpec] = Field(
        ..., min_length=_MIN_OPTIONS, max_length=_MAX_OPTIONS
    )


class ProposeReplyOptionsTool(BaseTool):
    name: str = "propose_reply_options"
    description: str = (
        "Attach 2-5 pickable reply buttons to your NEXT assistant message. "
        "Call this immediately before writing the final reply, whenever you "
        "are asking the user to choose between alternatives. Each option has "
        "a short label (what the button shows), an optional description "
        "(secondary text under the label), and an optional value (the user "
        "message sent on click; defaults to the label). "
        "After calling this tool, phrase the question in your reply text but "
        "do NOT list the options as markdown bullets - the tool renders them."
    )
    args_schema: type[BaseModel] = ProposeReplyOptionsInput

    def _run(
        self,
        options: list[dict[str, Any]],
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return {"ok": True, "count": len(options)}

    async def _arun(
        self,
        options: list[dict[str, Any]],
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(options, config=config)


def _normalize_reply_option(option: Any) -> dict[str, Any] | None:
    """Defensive normalizer for raw option dicts read out of tool args.

    Pydantic has already validated when the tool is invoked through the
    standard path, but the orchestrator reads from the raw args dict
    (possibly recovered from event payloads), so we re-check shape and
    enforce caps to keep persisted meta safe.
    """
    if not isinstance(option, dict):
        return None
    raw_label = option.get("label")
    if not isinstance(raw_label, str):
        return None
    label = raw_label.strip()
    if not label or len(label) > _LABEL_MAX:
        return None
    out: dict[str, Any] = {"label": label}
    raw_desc = option.get("description")
    if isinstance(raw_desc, str):
        desc = raw_desc.strip()
        if desc:
            out["description"] = desc[:_DESCRIPTION_MAX]
    raw_value = option.get("value")
    if isinstance(raw_value, str):
        value = raw_value.strip()
        if value:
            out["value"] = value[:_VALUE_MAX]
    return out
