"""Test SSE -> AgentEvent parser."""

import pytest
from app.services.gateway.sse import parse_sse_stream
from app.services.gateway.types import AgentEvent


@pytest.mark.asyncio
async def test_parse_sse_stream_basic():
    """Test parsing basic SSE frames."""
    frames = [
        'event: token\ndata: {"text": "hi"}\n\n',
        'event: done\ndata: {"message_id": "msg-1"}\n\n',
    ]

    async def aiter_frames():
        for frame in frames:
            yield frame

    events = []
    async for event in parse_sse_stream(aiter_frames()):
        events.append(event)

    assert len(events) == 2
    assert events[0].type == "token"
    assert events[0].data == {"text": "hi"}
    assert events[1].type == "done"
    assert events[1].data == {"message_id": "msg-1"}


@pytest.mark.asyncio
async def test_parse_sse_stream_multiline_data():
    """Test parsing frames with multiple data: lines (SSE spec concatenation)."""
    # In SSE, multiple data lines are concatenated with \n
    # This tests a frame where the data lines concatenate to form valid JSON
    frame = 'event: token\ndata: {"text":\ndata: "hello"}\n\n'
    frames = [frame]

    async def aiter_frames():
        for f in frames:
            yield f

    events = []
    async for event in parse_sse_stream(aiter_frames()):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "token"
    # When concatenated with newline: {"text":\n"hello"}
    assert events[0].data == {"text": "hello"}


@pytest.mark.asyncio
async def test_parse_sse_stream_malformed_json():
    """Test that malformed JSON yields unknown type without raising."""
    frames = [
        'event: token\ndata: {invalid json}\n\n',
        'event: done\ndata: {"message_id": "msg-1"}\n\n',
    ]

    async def aiter_frames():
        for frame in frames:
            yield frame

    events = []
    async for event in parse_sse_stream(aiter_frames()):
        events.append(event)

    assert len(events) == 2
    assert events[0].type == "unknown"
    assert events[1].type == "done"


@pytest.mark.asyncio
async def test_parse_sse_stream_comment_ignored():
    """Test that comment lines (starting with :) are ignored."""
    frames = [
        ": this is a comment\n",
        'event: token\ndata: {"text": "hi"}\n\n',
    ]

    async def aiter_frames():
        for frame in frames:
            yield frame

    events = []
    async for event in parse_sse_stream(aiter_frames()):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "token"


@pytest.mark.asyncio
async def test_parse_sse_stream_all_event_types():
    """Test mapping all event types."""
    frames = [
        'event: token\ndata: {"text": "hi"}\n\n',
        'event: done\ndata: {"message_id": "msg-1"}\n\n',
        'event: error\ndata: {"message": "err"}\n\n',
        'event: heartbeat\ndata: {}\n\n',
        'event: tool_started\ndata: {"tool_name": "test"}\n\n',
        'event: tool_finished\ndata: {"tool_name": "test"}\n\n',
        'event: action_required\ndata: {"action": "approve"}\n\n',
    ]

    async def aiter_frames():
        for frame in frames:
            yield frame

    events = []
    async for event in parse_sse_stream(aiter_frames()):
        events.append(event)

    assert len(events) == 7
    event_types = [e.type for e in events]
    assert event_types == [
        "token",
        "done",
        "error",
        "heartbeat",
        "tool_started",
        "tool_finished",
        "action_required",
    ]


@pytest.mark.asyncio
async def test_parse_sse_stream_unknown_event_type():
    """Test that unknown event types produce unknown type."""
    frames = [
        'event: unknown_type\ndata: {"something": "value"}\n\n',
    ]

    async def aiter_frames():
        for frame in frames:
            yield frame

    events = []
    async for event in parse_sse_stream(aiter_frames()):
        events.append(event)

    assert len(events) == 1
    assert events[0].type == "unknown"
