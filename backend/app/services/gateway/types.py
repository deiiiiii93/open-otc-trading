"""Type definitions for the IM Message Gateway."""

from dataclasses import dataclass
from typing import Any


@dataclass
class AgentEvent:
    """Represents a parsed SSE event from the agent stream."""

    type: str  # token, done, error, heartbeat, tool_started, tool_finished, action_required, unknown
    data: Any
