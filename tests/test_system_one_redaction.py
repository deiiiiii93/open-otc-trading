"""The single outbound sanitizer (D16): the audit trail's key regex for dict
keys, plus token patterns inside free text."""
from __future__ import annotations

import copy

from app.services.deep_agent.audit_redaction import mask_secrets, mask_text

TOKEN = "sk-" + "A1b2C3d4E5f6G7h8I9j0"


def test_masks_openai_style_token_in_free_text():
    assert TOKEN not in mask_text(f"use key {TOKEN} for this")
    assert "[REDACTED]" in mask_text(f"use key {TOKEN} for this")


def test_masks_bearer_and_key_value_pairs_but_keeps_the_label():
    out = mask_text("Authorization: Bearer abcdefghijklmnopqrstuvwxyz password=hunter2")
    assert "abcdefghijklmnopqrstuvwxyz" not in out
    assert "hunter2" not in out
    assert "password=[REDACTED]" in out


def test_leaves_ordinary_desk_text_alone():
    text = "Void cashflow 9300 and close position 27 (KO at 105%)."
    assert mask_text(text) == text


def test_masks_nested_strings_and_secret_keys_without_mutating_input():
    state = {
        "user_request": f"here is {TOKEN}",
        "pending_tool_call": {"name": "x", "args": {"api_key": "zzz", "note": ["ok", TOKEN]}},
        "count": 3,
    }
    before = copy.deepcopy(state)
    out = mask_secrets(state)
    assert TOKEN not in str(out)
    assert out["pending_tool_call"]["args"]["api_key"] == "[REDACTED]"
    assert out["pending_tool_call"]["args"]["note"][0] == "ok"
    assert out["count"] == 3
    assert state == before
