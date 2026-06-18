from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.services.reply_options.tool import (
    _DESCRIPTION_MAX,
    _LABEL_MAX,
    _VALUE_MAX,
    ProposeReplyOptionsInput,
    ProposeReplyOptionsTool,
    ReplyOptionSpec,
    _normalize_reply_option,
)


def test_tool_metadata():
    tool = ProposeReplyOptionsTool()
    assert tool.name == "propose_reply_options"
    assert "pickable reply buttons" in tool.description
    assert tool.args_schema is ProposeReplyOptionsInput


def test_run_happy_path_returns_count_ack():
    tool = ProposeReplyOptionsTool()
    options = [
        {"label": "Yes"},
        {"label": "No", "description": "Stop here"},
    ]
    out = tool._run(options=options)
    assert out == {"ok": True, "count": 2}


def test_arun_mirrors_run():
    tool = ProposeReplyOptionsTool()
    out = asyncio.run(tool._arun(options=[{"label": "A"}, {"label": "B"}]))
    assert out == {"ok": True, "count": 2}


def test_input_schema_rejects_fewer_than_two_options():
    with pytest.raises(ValidationError):
        ProposeReplyOptionsInput(options=[{"label": "Only one"}])


def test_input_schema_rejects_more_than_five_options():
    with pytest.raises(ValidationError):
        ProposeReplyOptionsInput(
            options=[{"label": f"opt{i}"} for i in range(6)]
        )


def test_input_schema_rejects_oversized_label():
    with pytest.raises(ValidationError):
        ReplyOptionSpec(label="x" * (_LABEL_MAX + 1))


def test_input_schema_rejects_oversized_description():
    with pytest.raises(ValidationError):
        ReplyOptionSpec(label="ok", description="x" * (_DESCRIPTION_MAX + 1))


def test_input_schema_rejects_oversized_value():
    with pytest.raises(ValidationError):
        ReplyOptionSpec(label="ok", value="x" * (_VALUE_MAX + 1))


def test_input_schema_accepts_label_at_max_length():
    spec = ReplyOptionSpec(label="x" * _LABEL_MAX)
    assert len(spec.label) == _LABEL_MAX


def test_input_schema_accepts_description_at_max_length():
    spec = ReplyOptionSpec(label="ok", description="x" * _DESCRIPTION_MAX)
    assert spec.description is not None
    assert len(spec.description) == _DESCRIPTION_MAX


def test_input_schema_accepts_value_at_max_length():
    spec = ReplyOptionSpec(label="ok", value="x" * _VALUE_MAX)
    assert spec.value is not None
    assert len(spec.value) == _VALUE_MAX


def test_input_schema_accepts_full_shape():
    spec = ReplyOptionSpec(label="Yes", description="Run it", value="Yes, run it now")
    assert spec.label == "Yes"
    assert spec.description == "Run it"
    assert spec.value == "Yes, run it now"


def test_normalize_drops_non_dict():
    assert _normalize_reply_option("nope") is None
    assert _normalize_reply_option(None) is None
    assert _normalize_reply_option(42) is None


def test_normalize_drops_missing_or_empty_label():
    assert _normalize_reply_option({}) is None
    assert _normalize_reply_option({"label": ""}) is None
    assert _normalize_reply_option({"label": "   "}) is None


def test_normalize_drops_oversized_label():
    assert _normalize_reply_option({"label": "x" * (_LABEL_MAX + 1)}) is None


def test_normalize_drops_non_string_label():
    assert _normalize_reply_option({"label": 42}) is None
    assert _normalize_reply_option({"label": None}) is None
    assert _normalize_reply_option({"label": ["Yes"]}) is None


def test_normalize_ignores_non_string_optional_fields():
    out = _normalize_reply_option({"label": "Yes", "description": 42, "value": []})
    assert out == {"label": "Yes"}


def test_normalize_trims_and_keeps_optional_fields():
    out = _normalize_reply_option(
        {"label": "  Yes  ", "description": "  Run it  ", "value": "  Yes, run it now  "}
    )
    assert out == {"label": "Yes", "description": "Run it", "value": "Yes, run it now"}


def test_normalize_omits_absent_optional_fields():
    out = _normalize_reply_option({"label": "Yes"})
    assert out == {"label": "Yes"}


def test_normalize_truncates_oversized_description():
    out = _normalize_reply_option(
        {"label": "Yes", "description": "x" * (_DESCRIPTION_MAX + 60)}
    )
    assert out is not None
    assert len(out["description"]) == _DESCRIPTION_MAX


def test_normalize_truncates_oversized_value():
    out = _normalize_reply_option(
        {"label": "Yes", "value": "x" * (_VALUE_MAX + 100)}
    )
    assert out is not None
    assert len(out["value"]) == _VALUE_MAX


def test_tool_is_in_quant_agent_tools_for_personas():
    """Personas can also ask the user to choose between alternatives, so
    propose_reply_options must be available in their shared tool list.
    """
    from app.tools import QUANT_AGENT_TOOLS

    names = {getattr(t, "name", None) for t in QUANT_AGENT_TOOLS}
    assert "propose_reply_options" in names


def test_tool_is_listed_in_deep_agent_tool_names():
    """DEEP_AGENT_TOOL_NAMES is the persona-scoped allowlist."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert "propose_reply_options" in DEEP_AGENT_TOOL_NAMES


def test_orchestrator_prompt_mentions_propose_reply_options():
    from app.services.deep_agent.orchestrator import _orchestrator_prompt

    body = _orchestrator_prompt()
    assert "propose_reply_options" in body
    assert "2-5" in body or "2–5" in body  # ASCII hyphen or en-dash
    assert "The only tools you should use" in body
    assert body.count("## Pickable reply options") == 1


def test_orchestrator_raw_prompt_does_not_duplicate_pickable_options_policy():
    from app.services.deep_agent.orchestrator import _PROMPTS_DIR

    raw = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")
    assert "## Pickable reply options" not in raw
