"""SSE stream parser that converts raw SSE frames to AgentEvent objects."""

import json
from typing import AsyncIterator

from app.services.gateway.types import AgentEvent

# Valid event types per the brief
VALID_EVENT_TYPES = {
    "token",
    "done",
    "error",
    "heartbeat",
    "tool_started",
    "tool_finished",
    "action_required",
}


async def parse_sse_stream(aiter_str: AsyncIterator[str]) -> AsyncIterator[AgentEvent]:
    """
    Parse an async iterator of SSE string chunks into AgentEvent objects.

    Each SSE frame follows the format:
        event: {event_type}
        data: {json_data_line_1}
        data: {json_data_line_2}  # optional: multi-line data
        \n  # blank line terminating the frame

    Handles:
    - Multi-line data: lines are concatenated with newlines
    - Unknown event types: emitted as type="unknown"
    - Malformed JSON: emitted as type="unknown" without raising
    - Comment lines (starting with ':'): ignored
    - Chunks that don't align to frame boundaries: buffered across chunks

    Args:
        aiter_str: Async iterator yielding SSE string chunks

    Yields:
        AgentEvent objects with type and data fields
    """
    buffer = ""

    async for chunk in aiter_str:
        buffer += chunk

        # Process complete frames (terminated by blank line)
        while "\n\n" in buffer:
            frame_str, buffer = buffer.split("\n\n", 1)

            # Parse the frame
            event = _parse_frame(frame_str)
            if event is not None:
                yield event


def _parse_frame(frame_str: str) -> AgentEvent | None:
    """
    Parse a single SSE frame into an AgentEvent.

    Frame format:
        event: {type}
        data: {json}
        data: {json}  # optional: multi-line
        [optional more lines]

    Returns:
        AgentEvent with type and data, or None if frame is invalid/empty.
    """
    if not frame_str.strip():
        return None

    lines = frame_str.split("\n")
    event_type = None
    data_lines = []

    for line in lines:
        if line.startswith(":"):
            # Comment line - ignore
            continue
        elif line.startswith("event:"):
            # Extract event type
            event_type = line[6:].strip()
        elif line.startswith("data:"):
            # Extract data line
            data_str = line[5:].strip()
            data_lines.append(data_str)

    # If we have no event type or no data, return None
    if event_type is None:
        return None

    # Concatenate multi-line data with newlines
    combined_data_str = "\n".join(data_lines)

    # Try to parse JSON
    try:
        data = json.loads(combined_data_str)
    except (json.JSONDecodeError, ValueError):
        # Malformed JSON -> emit as unknown type
        return AgentEvent(type="unknown", data={"raw": combined_data_str})

    # Check if event type is valid; if not, emit as unknown
    if event_type not in VALID_EVENT_TYPES:
        return AgentEvent(type="unknown", data=data)

    return AgentEvent(type=event_type, data=data)
