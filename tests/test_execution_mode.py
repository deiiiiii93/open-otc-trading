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


# --- headless cost-preview conflict resolution ---
#
# `cost-preview-policy` tells a persona to "reply with a cost preview and wait
# for the user's yes" — which directly contradicts headless mode ("never ask,
# proceed"). Cautious instruction-followers honor the more conservative directive
# and stall in prose forever (no user answers). Headless must therefore DROP the
# contradictory fragment and replace it with an explicit autonomous override.


def test_cost_preview_policy_dropped_in_headless():
    base = (
        "escalation-policy",
        "cost-preview-policy",
        "reply-options-policy",
        "clarification-policy",
    )
    # Interactive/AUTO: unchanged — the preview-and-wait rule applies.
    assert _resolve_policy_fragments(base, True) == base
    # Headless: cost-preview dropped, reply-options swapped for headless.
    assert _resolve_policy_fragments(base, False) == (
        "escalation-policy",
        "headless-policy",
        "clarification-policy",
    )


def test_headless_policy_carries_expensive_action_override():
    from app.services.deep_agent.skills_loader import load_policy_fragments

    body = load_policy_fragments(("headless-policy",)).lower()
    assert "expensive actions in headless mode" in body
    # Autonomous long-run handling replaces preview-and-wait.
    assert "dispatch" in body and "async" in body


def test_persona_headless_prompt_drops_preview_and_wait():
    auto = trader_spec(None, [], allow_reply_options=True)["system_prompt"].lower()
    yolo = trader_spec(None, [], allow_reply_options=False)["system_prompt"].lower()
    # Interactive persona holds the preview-and-wait instruction.
    assert "wait for the user" in auto
    # Headless persona drops it (no user to wait for) and carries the override.
    assert "wait for the user" not in yolo
    assert "expensive actions in headless mode" in yolo


def test_orchestrator_headless_prompt_neutralizes_cost_preview():
    yolo = _orchestrator_prompt(allow_reply_options=False).lower()
    # The appended headless-policy explicitly overrides the orchestrator.md
    # embedded "Cost-preview rule" (which is static and always present).
    assert "expensive actions in headless mode" in yolo
