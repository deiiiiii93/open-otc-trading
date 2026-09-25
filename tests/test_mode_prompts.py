"""Mode-assembled prompts: a headless run is never told to ask the user.

Run #139: gpt-6-luna, headless, asked "did you mean Control Desk Portfolio?" on
9 of 9 risk-manager steps — the turn prompt told it to "Ask the user which
portfolio", and the orchestrator's clarification protocol said "ASK". Those
sentences are now assembled per mode; these tests pin that the headless
variant carries none of them and the interactive variant loses none.
"""
from __future__ import annotations

import re

import pytest

from app.services.agents import _orchestrator_user_prompt, render_context_brief
from app.services.deep_agent import mode_prompts
from app.services.deep_agent.orchestrator import _orchestrator_prompt
from app.services.deep_agent.personas import (
    board_spec,
    risk_spec,
    trader_spec,
)

_ASKS = re.compile(r"(?i:\bask (the user|which)\b|defaulted question)|\bASK\b")

_NOTHING_IN_VIEW = {"current_page_context": {"title": "Chat"}}
_PORTFOLIO_NO_PROFILE = {
    "current_page_context": {"title": "Risk", "entity_ids": {"portfolio_id": 7}},
    "portfolio_summary": {"name": "Desk", "position_count": 3},
}


@pytest.mark.parametrize("context", [_NOTHING_IN_VIEW, _PORTFOLIO_NO_PROFILE])
def test_headless_turn_prompt_never_says_ask(context):
    prompt = _orchestrator_user_prompt("run the check", "auto", context, mode="yolo")
    assert not _ASKS.search(prompt), prompt
    assert "no user is present" in prompt


def test_headless_brief_resolves_instead_of_asking():
    assert "list_portfolios" in render_context_brief(_NOTHING_IN_VIEW, mode="yolo")
    assert "list_pricing_parameter_profiles" in render_context_brief(
        _PORTFOLIO_NO_PROFILE, mode="yolo"
    )


@pytest.mark.parametrize("mode", ["interactive", "auto"])
def test_user_present_modes_keep_the_questions(mode):
    assert "Ask the user which portfolio" in render_context_brief(_NOTHING_IN_VIEW, mode=mode)
    assert "ask which pricing parameter profile" in render_context_brief(
        _PORTFOLIO_NO_PROFILE, mode=mode
    )


def test_legacy_yolo_bool_is_not_headless():
    # respond()/non-stream callers pass only yolo_mode, which meant AUTO.
    prompt = _orchestrator_user_prompt("x", "auto", _NOTHING_IN_VIEW, yolo_mode=True)
    assert "Ask the user which portfolio" in prompt
    assert "no user is present" not in prompt


def test_execution_mode_text_leaves_tool_authorization_unchanged():
    # Only the sentences about ASKING differ by mode; what tools may do is the
    # HITL interrupt map's business, so headless inherits AUTO's wording.
    assert mode_prompts.execution_mode_text("yolo").startswith(
        mode_prompts.execution_mode_text("auto")
    )


def test_orchestrator_system_prompt_assembles_each_mode():
    interactive = _orchestrator_prompt(allow_reply_options=True)
    headless = _orchestrator_prompt(allow_reply_options=False)
    for text in (interactive, headless):
        assert "MODE_SECTION" not in text
        assert "## Clarification protocol" in text
    assert "If multiple plausible targets exist OR no portfolio is in view, ASK." in interactive
    assert "profile-choice clarification is mandatory" in interactive
    body = headless.split("## Routing")[0]
    assert not _ASKS.search(body)
    assert "profile-choice clarification" not in headless


def test_every_mode_section_has_both_variants():
    from app.services.deep_agent.orchestrator import _PROMPTS_DIR

    names = set(
        mode_prompts._MODE_SECTION.findall((_PROMPTS_DIR / "orchestrator.md").read_text())
    )
    assert names == {"clarification", "profile-choice", "batch-size-one"}
    for name in names:
        for variant in ("interactive", "headless"):
            assert (mode_prompts._MODES_DIR / f"{name}.{variant}.md").is_file()


def test_missing_mode_section_file_raises():
    with pytest.raises(FileNotFoundError):
        mode_prompts.assemble_mode_sections("<!-- MODE_SECTION:nope -->", headless=True)


@pytest.mark.parametrize("spec", [trader_spec, risk_spec, board_spec])
def test_headless_personas_drop_clarification_policy(spec):
    user = spec(None, [], allow_reply_options=True)["system_prompt"]
    headless = spec(None, [], allow_reply_options=False)["system_prompt"]
    assert "## Clarify before acting" in user
    assert "## Clarify before acting" not in headless
    assert "## Resolve ambiguity with reads" in headless


def test_headless_drops_the_batch_size_one_rule():
    """Run #141: headless gpt-6-luna booked 1 of 5 valid trades, citing the
    one-write-per-turn limit that exists only to pair approval cards."""
    interactive = _orchestrator_prompt(allow_reply_options=True)
    headless = _orchestrator_prompt(allow_reply_options=False)
    assert "request the first, wait for confirmation" in interactive
    assert "request the first, wait for confirmation" not in headless
    for spec in (trader_spec, risk_spec, board_spec):
        assert "## Batch-size-1 HITL rule" in spec(None, [], allow_reply_options=True)["system_prompt"]
        assert "## Batch-size-1 HITL rule" not in spec(None, [], allow_reply_options=False)["system_prompt"]
