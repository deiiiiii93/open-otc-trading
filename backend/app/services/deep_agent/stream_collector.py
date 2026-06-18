from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


def _truncate(value: Any, limit: int = 1000) -> Any:
    """Stringify and truncate; preserve small values, envelope-wrap large ones."""
    try:
        s = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        # Not natively JSON-serializable — fall back to str(); truncate if oversize.
        coerced = str(value)
        if len(coerced) <= limit:
            return coerced
        return {"_truncated": True, "preview": coerced[:limit], "size": len(coerced)}
    if len(s) <= limit:
        return value
    return {"_truncated": True, "preview": s[:limit], "size": len(s)}


@dataclass
class StreamCollector:
    """Buffers a single agent turn's streamed events for later persistence."""

    text_chunks: list[str] = field(default_factory=list)
    tool_events: dict[str, dict] = field(default_factory=dict)  # keyed by run_id
    interrupts: list = field(default_factory=list)
    personas_invoked: list[str] = field(default_factory=list)
    error: str | None = None
    drained: bool = False
    drain_reason: str | None = None
    reply_options: list[dict] | None = None
    term_form: dict | None = None
    todos: list[dict[str, str]] | None = None
    # P2.5: envelope trail. ``envelope_initial`` is the envelope the turn
    # started under; ``envelope_final`` is the one in effect when the turn
    # ended (either the same, or the widened envelope after a single
    # escalation). ``envelope_transitioned`` is True iff a transition fired.
    envelope_initial: str | None = None
    envelope_final: str | None = None
    envelope_transitioned: bool = False
    # P2.7: when the gate raised CostPreviewRequiredError mid-stream, the
    # streaming path stashes the structured info here so the persisted
    # assistant message carries it and the UI can render a confirm button.
    cost_preview: dict[str, Any] | None = None
    # Untruncated args for propose_reply_options tool calls, keyed by run_id.
    # The general tool_events[run_id]["args"] is _truncate-wrapped at 1000 bytes
    # which can drop options near the upper cap envelope. Reply-options needs
    # the full args to survive the round-trip.
    reply_options_args: dict[str, list] = field(default_factory=dict)
    # Untruncated args for the propose_term_form tool call, keyed by run_id.
    term_form_args: dict[str, dict] = field(default_factory=dict)
    # Files emitted by tools outside the DeepAgents StateBackend, keyed by the
    # virtual /trading_desk path expected by the normal asset materializer.
    artifact_files: dict[str, Any] = field(default_factory=dict)

    def on_tool_start(self, run_id: str, name: str, args: Any, started_at: float) -> None:
        self.tool_events[run_id] = {
            "id": run_id,
            "name": name,
            "status": "running",
            "args": _truncate(args) if args else None,
            "_started_at": started_at,
        }

    def on_tool_end(
        self,
        run_id: str,
        output: Any,
        ended_at: float,
        error: str | None = None,
    ) -> None:
        ev = self.tool_events.get(run_id)
        if ev is None:
            # tool_end with no matching start — record best-effort
            self.tool_events[run_id] = {
                "id": run_id,
                "name": "?",
                "status": "error" if error else "done",
                "duration_ms": 0,
                "args": None,
                "output": None if error else (_truncate(output) if output is not None else None),
                "error": error,
            }
            return
        started_at = ev.pop("_started_at", ended_at)
        ev["duration_ms"] = int((ended_at - started_at) * 1000)
        ev["status"] = "error" if error else "done"
        ev["output"] = None if error else (_truncate(output) if output is not None else None)
        ev["error"] = error

    def reset_user_facing_output_for_retry(self) -> None:
        """Drop first-pass user-facing output before an escalation retry.

        The pre-escalation pass ran under the narrow envelope and produced only
        a dead-end refusal; its prose and UI proposals must not leak into the
        final persisted message after a successful widen+retry. Tool events (the
        denial audit trail) and the envelope trail are deliberately preserved.
        """
        self.text_chunks.clear()
        self.reply_options = None
        self.term_form = None

    def on_token(self, text: str) -> None:
        if text:
            self.text_chunks.append(text)

    def note_persona(self, name: str) -> None:
        if name and name not in self.personas_invoked:
            self.personas_invoked.append(name)

    def set_todos(self, todos: list[dict[str, str]] | None) -> None:
        if todos is not None:
            self.todos = todos

    def add_artifact_files(self, files: dict[str, Any]) -> None:
        for path, file_data in files.items():
            if isinstance(path, str):
                self.artifact_files[path] = file_data

    @property
    def final_text(self) -> str:
        return "".join(self.text_chunks).strip()

    @property
    def process_events(self) -> list[dict]:
        # Drop any leftover internal fields and return insertion-ordered list
        out: list[dict] = []
        for ev in self.tool_events.values():
            cleaned = {k: v for k, v in ev.items() if not k.startswith("_")}
            out.append(cleaned)
        return out

    @property
    def has_tool_errors(self) -> bool:
        return any(ev.get("status") == "error" for ev in self.process_events)
