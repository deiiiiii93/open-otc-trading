"""Backend tool: declare an interactive term-collection card for the next turn.

The LLM calls ``propose_term_form`` when a direct booking has missing/invalid
economics. The orchestrator captures the tool's input args (after Pydantic
validation) and writes them onto the persisted assistant message as
``meta["term_form"]``; the frontend renders a card. The tool is a pure
declaration — it does not mutate state. ``build_product`` remains the
authoritative gate, so card content is advisory only.
"""
from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

_KEY_MAX = 64
_LABEL_MAX = 56
_HELP_MAX = 160
_CHOICE_LABEL_MAX = 40
_CHOICE_VALUE_MAX = 64
_MAX_CHOICES = 5
_MIN_FIELDS = 1
_MAX_FIELDS = 12
_FIELD_TYPES = ("percent", "number", "date", "enum", "text")


class ChoiceSpec(BaseModel):
    label: str = Field(..., min_length=1, max_length=_CHOICE_LABEL_MAX)
    value: str | float | int = Field(...)


class FieldSpec(BaseModel):
    key: str = Field(..., min_length=1, max_length=_KEY_MAX)
    label: str = Field(..., min_length=1, max_length=_LABEL_MAX)
    help: str | None = Field(None, max_length=_HELP_MAX)
    type: Literal["percent", "number", "date", "enum", "text"]
    choices: list[ChoiceSpec] | None = Field(None, max_length=_MAX_CHOICES)
    default: ChoiceSpec | None = None
    required: bool = True


class ProposeTermFormInput(BaseModel):
    title: str = Field(..., min_length=1, max_length=120)
    subtitle: str | None = Field(None, max_length=200)
    fields: list[FieldSpec] = Field(..., min_length=_MIN_FIELDS, max_length=_MAX_FIELDS)
    submit_label: str = Field("Review & book", max_length=40)


class ProposeTermFormTool(BaseTool):
    name: str = "propose_term_form"
    description: str = (
        "Attach an interactive term-collection card to your NEXT assistant "
        "message to gather missing booking economics. Use immediately before "
        "your reply when build_product reports missing/invalid fields for a "
        "direct booking. Each field has: key (the flat build_product terms key "
        "you will merge, e.g. 'ko_barrier_pct'), label, optional help, a type "
        "('percent'|'number'|'date'|'enum'|'text'), optional choices (<=5 "
        "chips), an optional default (the suggested chip), and required. "
        "Suggest defaults (latest spot for initial_price, today for "
        "trade_start_date) but never assume them. After calling this, phrase a "
        "short prompt in your reply; do NOT list the fields as bullets - the "
        "card renders them."
    )
    args_schema: type[BaseModel] = ProposeTermFormInput

    def _run(
        self,
        title: str,
        fields: list[dict[str, Any]],
        subtitle: str | None = None,
        submit_label: str = "Review & book",
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return {"ok": True, "count": len(fields)}

    async def _arun(
        self,
        title: str,
        fields: list[dict[str, Any]],
        subtitle: str | None = None,
        submit_label: str = "Review & book",
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(
            title, fields, subtitle=subtitle, submit_label=submit_label, config=config
        )


def _normalize_choice(choice: Any) -> dict[str, Any] | None:
    if not isinstance(choice, dict):
        return None
    raw_label = choice.get("label")
    if not isinstance(raw_label, str) or not raw_label.strip():
        return None
    value = choice.get("value")
    if isinstance(value, str):
        value = value.strip()[:_CHOICE_VALUE_MAX]
        if not value:
            return None  # empty/whitespace-only value is meaningless as a chip
    elif not isinstance(value, (int, float)) or isinstance(value, bool):
        # bool is a subclass of int; reject it as a semantic non-value
        return None
    return {"label": raw_label.strip()[:_CHOICE_LABEL_MAX], "value": value}


def _normalize_term_field(field: Any) -> dict[str, Any] | None:
    """Defensive normalizer for raw field dicts read out of tool args.

    Mirrors reply_options._normalize_reply_option: re-checks shape and enforces
    caps because the orchestrator reads raw args recovered from event payloads.
    """
    if not isinstance(field, dict):
        return None
    raw_key = field.get("key")
    raw_label = field.get("label")
    if not isinstance(raw_key, str) or not raw_key.strip():
        return None
    if not isinstance(raw_label, str) or not raw_label.strip():
        return None
    field_type = field.get("type")
    if field_type not in _FIELD_TYPES:
        field_type = "text"
    out: dict[str, Any] = {
        "key": raw_key.strip()[:_KEY_MAX],
        "label": raw_label.strip()[:_LABEL_MAX],
        "type": field_type,
        "required": bool(field.get("required", True)),
    }
    raw_help = field.get("help")
    if isinstance(raw_help, str) and raw_help.strip():
        out["help"] = raw_help.strip()[:_HELP_MAX]
    raw_choices = field.get("choices")
    if isinstance(raw_choices, list):
        choices: list[dict[str, Any]] = []
        for choice in raw_choices:
            norm = _normalize_choice(choice)
            if norm is not None:
                choices.append(norm)
            if len(choices) >= _MAX_CHOICES:
                break
        if choices:
            out["choices"] = choices
    default = _normalize_choice(field.get("default"))
    if default is not None:
        out["default"] = default
    return out
