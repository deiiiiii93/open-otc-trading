"""Three-mode execution: Interactive / AUTO / YOLO.

YOLO is headless — the propose_reply_options card tool is withheld and the
prompt swaps in the headless policy, so the model cannot defer to a human.
"""
from __future__ import annotations

import pytest

from app.services.agents import resolve_execution_mode
from app.services.deep_agent.personas import _resolve_policy_fragments, trader_spec
from app.services.deep_agent.orchestrator import _orchestrator_prompt


# --- resolve_execution_mode: mode → (mode, clear_hitl, allow_reply_options) ---


@pytest.mark.parametrize(
    "mode,clear_hitl,allow_cards",
    [
        ("interactive", False, True),
        ("auto", True, True),
        ("yolo", True, False),
    ],
)
def test_mode_to_flags(mode, clear_hitl, allow_cards):
    assert resolve_execution_mode(mode, False) == (mode, clear_hitl, allow_cards)


def test_legacy_yolo_bool_maps_to_auto_or_interactive():
    # No mode given → derive from the deprecated boolean. Legacy is never headless.
    assert resolve_execution_mode(None, True) == ("auto", True, True)
    assert resolve_execution_mode(None, False) == ("interactive", False, True)


def test_mode_wins_over_legacy_bool():
    # Explicit mode takes precedence over yolo_mode.
    assert resolve_execution_mode("yolo", False)[0] == "yolo"
    assert resolve_execution_mode("interactive", True)[0] == "interactive"


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown execution mode"):
        resolve_execution_mode("turbo", False)


# --- persona prompt fragment swap ---


def test_policy_fragments_swap_to_headless_under_yolo():
    base = ("escalation-policy", "reply-options-policy", "clarification-policy")
    assert _resolve_policy_fragments(base, True) == base
    assert _resolve_policy_fragments(base, False) == (
        "escalation-policy", "headless-policy", "clarification-policy",
    )


def test_persona_prompt_headless_under_yolo():
    auto = trader_spec(None, [], allow_reply_options=True)["system_prompt"]
    yolo = trader_spec(None, [], allow_reply_options=False)["system_prompt"]
    # AUTO teaches reply-option cards; YOLO forbids asking and never mentions them.
    assert "propose_reply_options" in auto
    assert "Headless operation" in yolo
    assert "Never ask the user" in yolo


# --- orchestrator prompt fragment swap ---


def test_orchestrator_prompt_headless_under_yolo():
    auto = _orchestrator_prompt(allow_reply_options=True)
    yolo = _orchestrator_prompt(allow_reply_options=False)
    assert "Pickable reply options" in auto
    assert "Headless operation" in yolo
