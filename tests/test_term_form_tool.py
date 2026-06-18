from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.services.term_form.tool import (
    _CHOICE_LABEL_MAX,
    _HELP_MAX,
    _KEY_MAX,
    _LABEL_MAX,
    _MAX_CHOICES,
    _MAX_FIELDS,
    ChoiceSpec,
    FieldSpec,
    ProposeTermFormInput,
    ProposeTermFormTool,
    _normalize_choice,
    _normalize_term_field,
)

_OK_FIELD = {
    "key": "ko_barrier_pct",
    "label": "KO barrier",
    "help": "early-redemption level",
    "type": "percent",
    "choices": [{"label": "103%", "value": 103}],
    "default": {"label": "103%", "value": 103},
    "required": True,
}


def test_tool_metadata():
    tool = ProposeTermFormTool()
    assert tool.name == "propose_term_form"
    assert "term-collection card" in tool.description
    assert tool.args_schema is ProposeTermFormInput


def test_run_returns_count_ack():
    tool = ProposeTermFormTool()
    out = tool._run(title="Finish booking", fields=[_OK_FIELD])
    assert out == {"ok": True, "count": 1}


def test_arun_mirrors_run():
    tool = ProposeTermFormTool()
    out = asyncio.run(tool._arun(title="t", fields=[_OK_FIELD]))
    assert out == {"ok": True, "count": 1}


def test_input_schema_rejects_zero_fields():
    with pytest.raises(ValidationError):
        ProposeTermFormInput(title="t", fields=[])


def test_input_schema_rejects_too_many_fields():
    with pytest.raises(ValidationError):
        ProposeTermFormInput(
            title="t",
            fields=[{**_OK_FIELD, "key": f"k{i}"} for i in range(_MAX_FIELDS + 1)],
        )


def test_input_schema_rejects_bad_type():
    with pytest.raises(ValidationError):
        FieldSpec(key="k", label="L", type="dropdown")


def test_input_schema_rejects_too_many_choices():
    with pytest.raises(ValidationError):
        FieldSpec(
            key="k",
            label="L",
            type="enum",
            choices=[{"label": f"c{i}", "value": i} for i in range(_MAX_CHOICES + 1)],
        )


def test_input_schema_rejects_oversized_label():
    with pytest.raises(ValidationError):
        FieldSpec(key="k", label="x" * (_LABEL_MAX + 1), type="text")


def test_normalize_drops_non_dict():
    assert _normalize_term_field("nope") is None


def test_normalize_requires_key_and_label():
    assert _normalize_term_field({"label": "no key", "type": "text"}) is None
    assert _normalize_term_field({"key": "k", "type": "text"}) is None


def test_normalize_caps_and_coerces():
    norm = _normalize_term_field(
        {
            "key": "k" * (_KEY_MAX + 5),
            "label": "L" * (_LABEL_MAX + 5),
            "help": "h" * (_HELP_MAX + 5),
            "type": "percent",
            "choices": [{"label": "c" * (_CHOICE_LABEL_MAX + 5), "value": 1}] * 9,
            "default": {"label": "d", "value": 1},
        }
    )
    assert norm is not None
    assert len(norm["key"]) == _KEY_MAX
    assert len(norm["label"]) == _LABEL_MAX
    assert len(norm["help"]) == _HELP_MAX
    assert len(norm["choices"]) == _MAX_CHOICES
    assert len(norm["choices"][0]["label"]) == _CHOICE_LABEL_MAX
    assert norm["required"] is True


def test_normalize_defaults_unknown_type_to_text():
    norm = _normalize_term_field({"key": "k", "label": "L", "type": "weird"})
    assert norm is not None
    assert norm["type"] == "text"


def test_normalize_choice_drops_empty_string_value():
    assert _normalize_choice({"label": "y", "value": "   "}) is None


def test_normalize_choice_rejects_bool_value():
    assert _normalize_choice({"label": "y", "value": True}) is None


def test_propose_term_form_registered_in_quant_agent_tools():
    from app.tools import QUANT_AGENT_TOOLS

    names = {t.name for t in QUANT_AGENT_TOOLS}
    assert "propose_term_form" in names
