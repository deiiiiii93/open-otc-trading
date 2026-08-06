"""Capture seam for the chat booking card.

Unlike the reply-options / term-form captures (which read tool ARGS at
on_tool_start), this one reads the tool RESULT at on_tool_end — a booking
call's args are only ``{portfolio_id, trade_id}``, which says nothing about
what got booked.
"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, ToolMessage

from app.services.agents import _capture_booking_result_from_tool_end
from app.services.deep_agent.stream_collector import StreamCollector

_BOOKING = {
    "status": "booked",
    "position_id": 27,
    "trade_id": 2,
    "family": "BarrierOption",
    "underlying": "TSLA",
    "quantity": 750.0,
    "entry_price": 9.15,
    "currency": "USD",
    "counterparty": "Kestrel Capital Partners LLC",
    "external_trade_id": "ARD-EQO-2026-04701",
    "source_document": "conf-05-knockout-barrier-tsla.pdf",
    "portfolio": {"id": 1, "name": "Default"},
    "terms": {"strike": 330.0, "barrier": 420.0},
}


def _tool_message(payload: dict) -> ToolMessage:
    return ToolMessage(
        content=json.dumps(payload), name="book_extracted_trade", tool_call_id="c1"
    )


def test_capture_lifts_the_booking_summary_from_the_tool_result():
    c = StreamCollector()
    _capture_booking_result_from_tool_end(
        c, name="book_extracted_trade",
        output=_tool_message({"ok": True, "position_id": 27, "booking": _BOOKING}),
        error_text=None,
    )
    assert c.booking_result is not None
    assert c.booking_result["position_id"] == 27
    assert c.booking_result["underlying"] == "TSLA"
    assert c.booking_result["terms"]["strike"] == 330.0


def test_capture_records_a_refusal_too():
    """A failed booking is a result the desk needs to see — the card must be
    able to say why nothing was booked."""
    c = StreamCollector()
    failed = {**_BOOKING, "status": "failed", "position_id": None,
              "error": "validation_failed", "detail": ["underlying not bookable"]}
    _capture_booking_result_from_tool_end(
        c, name="book_extracted_trade",
        output=_tool_message({"ok": False, "error": "validation_failed",
                              "booking": failed}),
        error_text=None,
    )
    assert c.booking_result is not None
    assert c.booking_result["status"] == "failed"
    assert c.booking_result["error"] == "validation_failed"


def test_capture_ignores_other_tools():
    """Keyed by tool name, so an unrelated tool returning a "booking" key can
    never spoof a booking card."""
    c = StreamCollector()
    _capture_booking_result_from_tool_end(
        c, name="get_confirmation_batch",
        output=_tool_message({"ok": True, "booking": _BOOKING}),
        error_text=None,
    )
    assert c.booking_result is None


def test_capture_skips_on_tool_error():
    c = StreamCollector()
    _capture_booking_result_from_tool_end(
        c, name="book_extracted_trade",
        output=_tool_message({"ok": True, "booking": _BOOKING}),
        error_text="boom",
    )
    assert c.booking_result is None


def test_capture_tolerates_a_thin_or_unparsable_result():
    """The pre-enrichment tool contract ({"ok", "position_id"}) and any
    non-JSON body must leave the collector untouched, never raise."""
    c = StreamCollector()
    for output in (
        _tool_message({"ok": True, "position_id": 27}),
        ToolMessage(content="not json", name="book_extracted_trade", tool_call_id="c"),
        ToolMessage(content="[1, 2]", name="book_extracted_trade", tool_call_id="c"),
        None,
    ):
        _capture_booking_result_from_tool_end(
            c, name="book_extracted_trade", output=output, error_text=None,
        )
        assert c.booking_result is None


def test_capture_requires_a_status():
    c = StreamCollector()
    _capture_booking_result_from_tool_end(
        c, name="book_extracted_trade",
        output=_tool_message({"ok": True, "booking": {"position_id": 27}}),
        error_text=None,
    )
    assert c.booking_result is None


def test_last_booking_of_the_turn_wins():
    c = StreamCollector()
    for position_id in (27, 28):
        _capture_booking_result_from_tool_end(
            c, name="book_extracted_trade",
            output=_tool_message(
                {"ok": True, "booking": {**_BOOKING, "position_id": position_id}}
            ),
            error_text=None,
        )
    assert c.booking_result is not None
    assert c.booking_result["position_id"] == 28


def test_booking_result_from_a_non_streaming_resume_result():
    """The HITL resume path does NOT stream: `resume_pending_action` calls
    `agent.invoke(...)` and hand-builds the message meta, so on_tool_end never
    fires and `process_events` comes back empty. Verified live (thread 683):
    the approved booking created position 28 while the persisted message had
    no events and no card. The non-streaming paths must therefore dig the
    payload out of the result messages, exactly like _term_form_from_result.
    """
    from app.services.agents import _booking_result_from_result

    result = {"messages": [
        AIMessage(content="booking now"),
        _tool_message({"ok": True, "position_id": 28, "booking": {**_BOOKING, "position_id": 28}}),
        AIMessage(content="done"),
    ]}
    booking = _booking_result_from_result(result)
    assert booking is not None
    assert booking["position_id"] == 28


def test_booking_result_from_result_ignores_other_tools_and_bad_shapes():
    from app.services.agents import _booking_result_from_result

    other = ToolMessage(
        content=json.dumps({"ok": True, "booking": _BOOKING}),
        name="get_confirmation_batch", tool_call_id="c1",
    )
    assert _booking_result_from_result({"messages": [other]}) is None
    assert _booking_result_from_result({"messages": []}) is None
    assert _booking_result_from_result(None) is None
    assert _booking_result_from_result({"messages": [
        ToolMessage(content="not json", name="book_extracted_trade", tool_call_id="c")
    ]}) is None


def test_booking_result_from_result_takes_the_last_booking():
    from app.services.agents import _booking_result_from_result

    result = {"messages": [
        _tool_message({"ok": True, "booking": {**_BOOKING, "position_id": 28}}),
        _tool_message({"ok": True, "booking": {**_BOOKING, "position_id": 29}}),
    ]}
    booking = _booking_result_from_result(result)
    assert booking is not None
    assert booking["position_id"] == 29


def test_payload_survives_a_trailing_artifact_ref_block():
    """The live root cause. `GroundTruthArtifactMiddleware` runs INSIDE this
    middleware and appends an `<artifact_ref>{...}</artifact_ref>` block to the
    tool result body, so the ToolMessage content is JSON *followed by trailing
    data*. `json.loads` raises "Extra data: line 3 column 1" and the booking was
    dropped on every single gated run — with content_type=str and the booking
    plainly visible in the repr, which is what made it so slow to spot.
    """
    from app.services.deep_agent.booking_capture import (
        booking_payload_from_tool_output,
    )

    body = json.dumps({"ok": True, "position_id": 28, "booking": _BOOKING})
    artifact_ref = (
        '<artifact_ref>{"artifact_id": 41, "sha256": "abc", '
        '"tool_name": "book_extracted_trade"}</artifact_ref>'
    )
    message = ToolMessage(
        content=f"{body}\n\n{artifact_ref}",
        name="book_extracted_trade", tool_call_id="c1",
    )
    assert booking_payload_from_tool_output(message) == _BOOKING

    # Non-JSON leading content is still rejected, artifact_ref or not.
    assert booking_payload_from_tool_output(ToolMessage(
        content=f"refused\n\n{artifact_ref}",
        name="book_extracted_trade", tool_call_id="c1")) is None


def test_payload_survives_every_shape_the_seam_can_hand_back():
    """Regression, caught only by instrumenting the live seam.

    The middleware fired with the correct run key and still parsed nothing
    (`CAPTURE ... payload=False`), because `wrap_tool_call` does not hand back
    one stable shape. A plain `isinstance(content, str)` check silently drops
    LangChain's structured content-block list, and the HITL-resume path can
    hand back a Command instead of a ToolMessage.
    """
    from langgraph.types import Command

    from app.services.deep_agent.booking_capture import (
        booking_payload_from_tool_output,
    )

    body = json.dumps({"ok": True, "booking": _BOOKING})

    # 1. plain string content
    assert booking_payload_from_tool_output(_tool_message(
        {"ok": True, "booking": _BOOKING})) == _BOOKING
    # 2. structured content blocks — the live failure
    assert booking_payload_from_tool_output(
        ToolMessage(content=[{"type": "text", "text": body}],
                    name="book_extracted_trade", tool_call_id="c1")
    ) == _BOOKING
    # 3. Command carrying the ToolMessage (HITL resume)
    assert booking_payload_from_tool_output(
        Command(update={"messages": [ToolMessage(
            content=body, name="book_extracted_trade", tool_call_id="c1")]})
    ) == _BOOKING
    # 4. raw dict, unwrapped
    assert booking_payload_from_tool_output({"ok": True, "booking": _BOOKING}) == _BOOKING
    # ...and still no false positives
    assert booking_payload_from_tool_output(
        ToolMessage(content=[{"type": "text", "text": "not json"}],
                    name="book_extracted_trade", tool_call_id="c1")
    ) is None
    assert booking_payload_from_tool_output(None) is None


def test_run_keys_prefer_the_agent_thread_id_over_the_checkpointer_key(monkeypatch):
    """Regression: the persist path looks the booking up by AgentThread id, but
    `configurable["thread_id"]` is the CHECKPOINTER key — "sometimes a composite
    string, NOT necessarily an AgentThread id" per graph_run_config, and inside
    a persona subagent it is not the plain thread id. Keying only on it silently
    dropped every gated booking (live: position 28 booked, card blank).
    """
    from app.services.audit_trail import AUDIT_CONTEXT_KEY
    from app.services.deep_agent import booking_capture

    fake_config = {"configurable": {
        "thread_id": "685:sub:abc123",           # checkpointer key
        AUDIT_CONTEXT_KEY: {"thread_id": 685},   # AgentThread id
    }}
    import langgraph.config as lg_config
    monkeypatch.setattr(lg_config, "get_config", lambda: fake_config)

    keys = booking_capture.current_run_keys()
    assert keys[0] == "685", "AgentThread id must be first — the persist path uses it"
    assert "685:sub:abc123" in keys, "checkpointer key kept as a fallback"

    booking_capture.clear_bookings()
    booking_capture.record_booking(keys, _BOOKING)
    # Whichever identity the reader holds, it resolves.
    assert booking_capture.take_booking("685") is not None
    booking_capture.record_booking(keys, _BOOKING)
    assert booking_capture.take_booking("685:sub:abc123") is not None


def test_run_keys_tolerate_a_missing_audit_context(monkeypatch):
    from app.services.deep_agent import booking_capture

    import langgraph.config as lg_config
    monkeypatch.setattr(
        lg_config, "get_config", lambda: {"configurable": {"thread_id": "77"}}
    )
    assert booking_capture.current_run_keys() == ["77"]

    monkeypatch.setattr(lg_config, "get_config", lambda: {"configurable": {}})
    assert booking_capture.current_run_keys() == []


def test_escalation_reset_preserves_the_booking_record():
    """reset_user_facing_output_for_retry drops pre-escalation prose and UI proposals,
    but a booking is a write that really happened — same reason tool events
    survive it."""
    c = StreamCollector()
    _capture_booking_result_from_tool_end(
        c, name="book_extracted_trade",
        output=_tool_message({"ok": True, "booking": _BOOKING}),
        error_text=None,
    )
    c.term_form = {"title": "x", "fields": []}
    c.reset_user_facing_output_for_retry()
    assert c.term_form is None
    assert c.booking_result is not None
