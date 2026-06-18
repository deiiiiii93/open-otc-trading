# Structured Reply Options (Pickable Buttons)

**Date:** 2026-05-17
**Status:** Approved, ready for implementation plan
**Author:** Brainstorming session (fuxinyao + Claude)

## Problem

The pickable-reply-options feature (commits `ee9b82d`, `4d21677`) ships today as a **frontend-only heuristic**: `frontend/src/components/replyOptions.ts` reverse-engineers the assistant's markdown to detect 2–5 trailing bullet/icon-prefixed lines preceded by a choice-context cue (question mark, "choose", "select", etc.).

Two problems with the heuristic-only approach:

1. **False negatives.** The LLM phrases choices in many ways the regex misses (paragraph-style alternatives, options without a question mark, options in the middle of a reply, multi-line option bodies, etc.). Users lose the buttons.
2. **No upstream contract.** The LLM doesn't know it's offering buttons. It writes natural prose; the client decides retroactively. That makes the rendering opaque to the agent and prevents the LLM from deliberately authoring "ask the user to pick" moments.

## Goal

Add a **deterministic structured channel** from agent → UI for reply options, while keeping the existing heuristic as a fallback so older messages and LLM lapses still render correctly.

## Non-Goals

- Redesigning the visual appearance of reply-option buttons. The current `.wl-chat-bubble__reply-option` styling stays.
- Adding hotkeys, hover previews, or richer interaction modes.
- Changing how the user's chosen option is sent — clicks continue to call `onSelectReplyOption(messageId, value)`, which calls `onSend(value)` and surfaces as a normal user message.
- Wiring this into channels other than the orchestrator agent (e.g., async subagents do not need it; their results are auto-posted, not interactive).

## Architecture

### New BaseTool: `propose_reply_options`

A LangChain `BaseTool` the LLM calls **immediately before** writing its final reply, whenever it intends to offer a choice. Free-to-call (no HITL gate) because proposing options is a UI hint, not a state mutation.

**Location:** `backend/app/services/reply_options/__init__.py` and `tool.py` (new module, mirrors the `async_agents/` layout pattern).

```python
# backend/app/services/reply_options/tool.py
from typing import Any
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class ReplyOptionSpec(BaseModel):
    label: str = Field(..., min_length=1, max_length=56)
    description: str | None = Field(None, max_length=240)
    value: str | None = Field(None, max_length=400)


class ProposeReplyOptionsInput(BaseModel):
    options: list[ReplyOptionSpec] = Field(..., min_length=2, max_length=5)


class ProposeReplyOptionsTool(BaseTool):
    name: str = "propose_reply_options"
    description: str = (
        "Attach 2–5 pickable reply buttons to your NEXT assistant message. "
        "Call this immediately before writing the final reply, whenever you "
        "are asking the user to choose between alternatives. Each option has "
        "a short label (what the button shows), an optional description "
        "(secondary text under the label), and an optional value (the user "
        "message sent on click; defaults to the label). "
        "After calling this tool, phrase the question in your reply text but "
        "do NOT list the options as markdown bullets — the tool renders them."
    )
    args_schema: type[BaseModel] = ProposeReplyOptionsInput

    def _run(
        self,
        options: list[dict[str, Any]],
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        # Pure declaration: tool returns an acknowledgement; the orchestrator
        # captures the *input args* into the StreamCollector and ultimately
        # message meta. No state mutation, no side effects.
        return {"ok": True, "count": len(options)}

    async def _arun(
        self,
        options: list[dict[str, Any]],
        config: RunnableConfig = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        return self._run(options, config=config)
```

### Registration

`backend/app/services/langchain_tools.py`:

- Import: `from .reply_options.tool import ProposeReplyOptionsTool`
- Append `ProposeReplyOptionsTool()` to `QUANT_AGENT_TOOLS` (in the "Async-subagent dispatch" neighborhood — both are free-to-call UI-affecting tools).

`backend/app/services/agents.py`:

- Add `"propose_reply_options"` to `DEEP_AGENT_TOOL_NAMES` so deep-agent paths also expose it.

### Stream wiring: tool call → collector → meta

The truth source is the tool **input args**, not the return value. The orchestrator already streams `on_tool_start` and `on_tool_end` events for every tool call.

**`StreamCollector`** (in `agents.py`) gains:

```python
self.reply_options: list[dict[str, Any]] | None = None
```

In `_handle_event` (around `agents.py:884-916`), inside the `on_tool_end` branch, when `name == "propose_reply_options"` **and the tool did not error**:

```python
if name == "propose_reply_options" and not error_text:
    # The tool has just returned; LangChain has already passed Pydantic
    # validation (else _run wouldn't have been called). Read the args from
    # the run's tool_events entry, which was captured at on_tool_start.
    args = (collector.tool_events.get(run_id, {}) or {}).get("args") or {}
    raw_options = args.get("options")
    if isinstance(raw_options, list):
        normalized = [
            opt for opt in (_normalize_reply_option(o) for o in raw_options)
            if opt is not None
        ][:5]
        if len(normalized) >= 2:
            # Last call wins — LLM can correct itself.
            collector.reply_options = normalized
```

Rationale for capturing on `on_tool_end` (not `on_tool_start`):
- Pydantic validation happens between `on_tool_start` and `_run`. If the LLM emits malformed args (1 option, oversized field), the tool errors and we should *not* overwrite any previously valid `reply_options` from an earlier call this turn.
- `tool_events[run_id]` is populated at `on_tool_start` and is still available at `on_tool_end`, so we can recover the args without re-parsing the event.

`_normalize_reply_option(o)` returns `dict | None`:
- Returns `None` if `o` is not a dict, label is missing or empty after `.strip()`, or label exceeds 56 chars (defensive duplicate of Pydantic — covers the case where args reached us bypassing validation).
- Otherwise returns `{"label": ..., "description": ..., "value": ...}` with truncated/trimmed values, omitting `description` / `value` keys when not set.

`_normalize_reply_option` and `_is_valid_reply_option` are small helpers that:
- Coerce dict-shaped args into `{label, description?, value?}`.
- Enforce length caps (56 label / 240 description / 400 value), trim whitespace.
- Reject options where label is empty after trim.

### Persistence

In `agents.py:_persist_from_collector` (around lines 944-1000), both branches (interrupt and completed) gain:

```python
**({"reply_options": collector.reply_options} if collector.reply_options else {}),
```

inserted into the `meta=` dict literal. Key absent when no tool call happened — preserves backward compatibility on the frontend's "if structured present, use it" check.

### Frontend: prefer structured, fallback to heuristic

**Type addition** (`frontend/src/types.ts` — wherever `ChatMessage.meta` is typed):

```ts
type ReplyOptionMeta = {
  label: string;
  description?: string;
  value?: string;
};
// In the message meta type:
reply_options?: ReplyOptionMeta[];
```

**`ChatBubble.tsx`** changes around lines 57-68:

```tsx
const structuredOptions = Array.isArray(meta.reply_options)
  ? (meta.reply_options as ReplyOptionMeta[])
  : null;
const heuristicExtraction = !structuredOptions
  && variant === 'assistant'
  && !isStreaming
  && pendingActions.length === 0
  ? extractReplyOptions(message.content)
  : null;
const showReplyOptions = !!(
  replyOptionsEnabled
  && onSelectReplyOption
  && (
    (structuredOptions && structuredOptions.length > 0)
    || (heuristicExtraction && heuristicExtraction.options.length > 0)
  )
);
const visibleContent = showReplyOptions && heuristicExtraction
  ? heuristicExtraction.contentWithoutOptions
  : message.content; // structured path leaves content untouched
const optionsToRender: ReplyOption[] = structuredOptions
  ?? heuristicExtraction?.options
  ?? [];
```

Click handler:

```tsx
onSelect={(option) => onSelectReplyOption(
  message.id,
  (option as ReplyOptionMeta).value ?? option.label,
)}
```

`ReplyOption` type in `replyOptions.ts` gains optional `value?: string` so the structured and heuristic shapes unify.

`MessageList.tsx` does not change — the `latestCompletedAssistantId` gate continues to apply to both structured and heuristic paths.

### Prompt update

Add to the orchestrator system prompt in `backend/app/services/deep_agent/orchestrator.py:_orchestrator_prompt()`:

> **Asking the user to pick between alternatives.** When your reply asks the user to choose between 2–5 alternatives, you MUST call the `propose_reply_options` tool **immediately before** writing the reply. Each option needs a short label (what the user sees on the button, ≤56 chars), an optional one-line description, and an optional `value` (what gets sent when clicked; defaults to the label — set it when the label alone would be ambiguous as a user message). Phrase the question naturally in your reply text; do **not** repeat the options as a markdown bullet list — the UI renders the buttons for you. Do not call this tool for confirmation prompts that already have a structured ActionProposal — that flow has its own buttons.

No separate "deep-agent prompt" exists — `_orchestrator_prompt()` is the single source. Adding the rule there covers both paths.

## Data Flow

```
User sends message
  → orchestrator streams
    → LLM decides to offer a choice
      → LLM calls propose_reply_options(options=[...])
        → on_tool_start event fires (args stashed in collector.tool_events[run_id])
        → Pydantic validates args, _run executes, returns {ok: true, count: N}
        → on_tool_end event fires
          → if no error: StreamCollector.reply_options = [normalized options]
    → LLM streams its natural-language reply
  → stream ends
    → _persist_from_collector writes assistant message with meta.reply_options = [...]
  → SSE delivers message to client
    → ChatBubble sees meta.reply_options, renders <ReplyOptionButtons>
    → heuristic skipped, content displayed verbatim
  → user clicks a button
    → onSelectReplyOption(messageId, option.value ?? option.label)
      → onSend(value) — surfaces as a normal user turn
```

## Fallback Behavior

- **`meta.reply_options` present and non-empty** → render structured buttons, do not strip content.
- **`meta.reply_options` absent or empty** → run the existing heuristic `extractReplyOptions(message.content)`. If it returns options, render them and strip from content (current behavior). If not, no buttons.
- **`meta.reply_options` present but the LLM also bulleted the options in text** → structured wins. Content is shown verbatim including the bullet duplication. Mitigated by the prompt rule ("do not repeat as bullets"). Worst case: cosmetic duplication, not a functional bug.

## Error Handling

- Tool args fail Pydantic validation (e.g., 1 option, 6 options, label > 56 chars) → LangChain raises before `_run`; `on_tool_end` carries the error; `StreamCollector.reply_options` is **not** updated. The LLM sees the error in its tool result and can retry. The previously captured `reply_options` (if any from an earlier call) survive.
- Tool called with `options=[]` (would pass Pydantic min_length=2? No — it wouldn't) → unreachable, but defensive code in `_normalize_reply_option` filtering also enforces ≥2.
- Tool called multiple times in one turn → last call wins. Rationale: LLM may refine.
- Frontend receives `reply_options` that don't match the type shape → defensive `Array.isArray` + per-item shape check; bad entries silently dropped; if nothing valid remains, falls back to heuristic.

## Testing

### Backend
- `tests/test_reply_options_tool.py` (new): unit tests for `ProposeReplyOptionsTool._run` (happy path), Pydantic validation rejection (too few, too many, oversized fields), and `_normalize_reply_option` edge cases.
- Extend `tests/test_scripted_graph_streaming.py` with a new scenario: scripted graph emits a `propose_reply_options` tool call followed by an assistant text reply; assert the persisted assistant message has `meta["reply_options"] == [...]`.
- Same file: scenario asserting "last call wins" when the tool fires twice in one turn.
- Same file: scenario asserting that an invalid tool call does **not** clear an earlier valid `reply_options` and that absent prior calls leave `meta` without the `reply_options` key.

### Frontend
- Extend `frontend/src/components/ChatBubble.test.tsx`:
  - Structured `meta.reply_options` renders buttons; heuristic bypassed (content is **not** stripped).
  - Click on a structured option with no `value` sends the label.
  - Click on a structured option with a `value` sends the value.
  - When both `meta.reply_options` and a bullet-style trailing list exist, structured wins; content is verbatim.
- Existing `replyOptions.test.ts` tests stay — fallback path remains covered.
- Existing `MessageList.test.tsx` test for `latestCompletedAssistantId` gating still applies; no change needed.

## Files Changed

**New:**
- `backend/app/services/reply_options/__init__.py`
- `backend/app/services/reply_options/tool.py`
- `tests/test_reply_options_tool.py`

**Modified:**
- `backend/app/services/langchain_tools.py` — import + register tool
- `backend/app/services/agents.py` — `DEEP_AGENT_TOOL_NAMES`, `StreamCollector.reply_options`, capture in `_handle_event` (on_tool_end branch), persist in `_persist_from_collector` (both branches)
- `backend/app/services/deep_agent/orchestrator.py` — `_orchestrator_prompt()` adds the reply-options rule
- `frontend/src/types.ts` — add `reply_options` to message meta type
- `frontend/src/components/replyOptions.ts` — add optional `value?: string` to `ReplyOption`
- `frontend/src/components/ChatBubble.tsx` — structured-first selection logic, click handler uses `value ?? label`
- `frontend/src/components/ChatBubble.test.tsx` — new test cases
- `tests/test_scripted_graph_streaming.py` — new scenarios

**Unchanged:**
- `frontend/src/components/MessageList.tsx` — gating logic unaffected
- `frontend/src/components/FloatingAgentMiniChat.tsx` — pass-through unaffected
- `frontend/src/routes/AgentDesk.tsx` — pass-through unaffected
- CSS — visual treatment stays

## Open Questions

None remaining. All architectural choices resolved during brainstorming:
- Emission mechanism: dedicated BaseTool (confirmed)
- Fallback: heuristic stays as fallback (confirmed)
- Click value: optional `value` field with label as default (confirmed)
- HITL: free-to-call (confirmed)
