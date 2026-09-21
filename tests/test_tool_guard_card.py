"""The card must say WHY AUTO paused — including for tools whose summary
builder beats `description` (close/settle/knockout)."""
from __future__ import annotations

from app.services.deep_agent.hitl import GUARD_NOTE_PREFIX, _summary_for

NOTE = f"{GUARD_NOTE_PREFIX} flagged — unnamed_target p=0.85 ≥ 0.50"


def test_builder_tool_keeps_its_summary_and_gains_the_note():
    summary = _summary_for({"name": "close_position", "args": {}, "description": NOTE})
    assert summary == f"Close position — {NOTE}"


def test_tool_without_a_builder_shows_its_args_and_the_note():
    summary = _summary_for({"name": "void_settlement_cashflow",
                            "args": {"cashflow_id": 9300}, "description": NOTE})
    assert summary == f"Run void_settlement_cashflow (cashflow_id=9300) — {NOTE}"


def test_existing_behaviour_is_unchanged():
    generic = "Tool execution requires approval\n\nTool: close_position\nArgs: {}"
    assert _summary_for({"name": "close_position", "args": {}, "description": generic}) == "Close position"
    assert _summary_for({"name": "quote_rfq", "args": {"rfq_id": 1},
                         "description": "custom text"}) == "custom text"
    assert _summary_for({"name": "quote_rfq", "args": {}}) == "Run quote_rfq"
    assert _summary_for({"name": "quote_rfq", "args": {"rfq_id": 1}}) == "Run quote_rfq (rfq_id=1)"
