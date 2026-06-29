# Three-mode desk execution: Interactive / AUTO / YOLO

**Date:** 2026-06-26
**Status:** Approved design, pre-implementation

## Problem

The desk agent has a single `yolo_mode` boolean: `False` surfaces HITL (human-in-the-loop)
confirmation prompts; `True` auto-clears them. But even with `yolo_mode=True`, the model can
still **defer to a human** by calling `propose_reply_options` — attaching pickable choice cards
and ending its turn to wait for a pick.

This deferral path broke the arena. The arena drives the real orchestrator headless, so a model
that proposes cards just stalls. We added an "autonomy auto-continue" loop that answered the cards
on the user's behalf ("choose sensible defaults and execute"). That mechanism is brittle and
introduced a real failure: Opus 4.8 read the nudge "sensible defaults" literally and re-ran a risk
calculation on the **Default** portfolio with no pricing profile (instead of the named Control
portfolio + Control Profile), corrupting every downstream step.

The root cause is that the model is allowed to *pretend a human is present* and ask. The fix is to
remove that ability entirely in a true headless mode, rather than paper over it by faking a human's
answer.

## Goals

- A true headless mode where the model **cannot** defer to a human (no `propose_reply_options`, no
  asking in prose) — it proceeds on best judgement.
- Rename the user-facing concept: today's "YOLO" toggle (auto-clears HITL, may still ask) becomes
  **AUTO**; the new headless mode is **YOLO**.
- Product-wide: the desk UI exposes all three modes.
- The arena drives matches under YOLO, and the autonomy auto-continue mechanism is deleted.

## Non-goals

- Changing what HITL interrupts exist or how cost-preview / run-python gating works (only *when*
  they auto-clear, which is unchanged: AUTO and YOLO both clear them; Interactive does not).
- Per-persona mode overrides — mode is per-turn, applied uniformly.

## The three modes

| Mode | HITL interrupts | Model may ask / propose cards | Today's equivalent |
|------|-----------------|-------------------------------|--------------------|
| **Interactive** | surfaced to user | yes | `yolo_mode=False` |
| **AUTO** (default) | auto-cleared | yes | `yolo_mode=True` |
| **YOLO** | auto-cleared | **no — headless** | (new) |

## Design

### A. Representation: enum at the edge, two flags inside

The boundary speaks a mode enum; the interior threads two derived capability booleans. This honors
the user-facing rename while keeping the deep refactor mechanical and low-risk.

**Boundary** (`schemas.py` request model, `main.py` endpoint, `AgentMessage.meta`, arena runner):
a string `mode: "interactive" | "auto" | "yolo"`, default `"auto"`.

**Back-compat:** a request that sends the legacy `yolo_mode: bool` and no `mode` maps
`true → "auto"`, `false → "interactive"`. `AgentMessage.meta` stores `mode`; readers that still
look for `yolo_mode` continue to work because we also keep writing a derived `yolo_mode` for
historical compatibility (= `clear_hitl`).

**Convert once** — at the first internal consumption point in `agents.py.stream_and_persist`,
derive:

- `clear_hitl = mode in {"auto", "yolo"}`
- `allow_reply_options = mode != "yolo"`

The existing internal `yolo_mode` boolean — threaded through ~30 `agents.py` methods plus the
deep-agent layer (`build_orchestrator`, `_agent_middleware`, `all_personas`, the cost-preview /
run-python middleware) — **carries `clear_hitl`'s value** and keeps its name. Renaming it through
every site is high-churn, low-reward risk on a live system, and the name remains defensible ("YOLO
behavior = auto-clear HITL", now shared by AUTO and YOLO). The new behavior rides on one new
parameter, `allow_reply_options`, threaded only as far as it is needed: `build_orchestrator` →
`orchestrator_tools` gating and `all_personas` → persona tool-scope + prompt. The clean `mode` enum
lives at the boundary (schema / public `stream_and_persist` / `AgentMessage.meta` / arena); the
interior sees `(yolo_mode, allow_reply_options)`.

### B. YOLO mechanism: remove the card tool + headless prompt

When `allow_reply_options` is `False`:

1. **Orchestrator** (`orchestrator.py`): build `orchestrator_tools` without `ProposeReplyOptionsTool`.
2. **Personas** (`task_registry` / persona tool resolution): filter `"propose_reply_options"` out of
   the `tools_scope` of `synthesise_workflow_response`, `converse_with_user`, and
   `plan_workflow_step`. With the tool gone, those tasks degrade to text-only synthesis — exactly
   the headless intent (a final reply with no attached buttons).
3. **Prompt**: compose a headless directive into the orchestrator and persona system prompts
   (alongside the existing `yolo-hitl-policy` fragment mechanism):

   > You are running headless. No user is available to answer questions or pick options. Never ask
   > the user anything, never request confirmation, and never propose reply options. Proceed on your
   > best judgement using the targets and parameters provided in the instruction; do not substitute
   > defaults for named targets.

   The "do not substitute defaults for named targets" clause directly counters the Opus drift.

Belt-and-suspenders: removing the tool makes deferral *impossible*; the prompt makes it *unattempted*
(and discourages prose-asking, which the tool removal alone wouldn't stop).

### C. Frontend

- Desk composer (`ChatComposer.tsx` + `.css`, and `FloatingAgentMiniChat.tsx`): replace the current
  two-state YOLO toggle with a three-way segmented control **Interactive / AUTO / YOLO**, default
  **AUTO**. The YOLO option shows a short caveat ("headless — auto-executes, never prompts").
- `types.ts`: a `mode` field (`'interactive' | 'auto' | 'yolo'`).
- `useAgentChatController.ts` / `AgentDesk.tsx`: send `mode` instead of `yolo_mode`.

### D. Arena

- `runner.run_match` drives every turn with `mode="yolo"` (replacing `yolo_mode=True`).
- **Delete** the autonomy auto-continue machinery added previously:
  `_choose_autonomous_reply`, `_latest_assistant_reply_options`, `_AUTONOMY_DIRECTIVE`,
  `_PROCEED_KW` / `_DEFER_KW`, `AUTONOMY_MAX_CONTINUES`, and the continue loop in `_default_drive`
  (it returns to one `_drive_once` per step, returning `None`).
- **Revert** the harvester `turns_per_step` generalization in `trace_harvest.transcript_from_trace`
  back to the 1:1 root→step mapping (its sole consumer was autonomy), and `run_match` stops
  collecting/passing `turns_per_step`. Drop the autonomy-specific tests
  (`test_turns_per_step_merges_...`, the `_choose_autonomous_reply` / auto-continue tests).
- Net: a deferring model cannot defer; there is no faked human answer, so the "sensible defaults"
  drift is structurally impossible. The arena now measures genuine autonomous capability.

## Data flow

```
UI (radio: interactive|auto|yolo)
  → POST /agent/... { mode }                         (schemas.py)
  → AgentService.stream_and_persist(mode=...)        (agents.py)
       derive: yolo_mode (= clear_hitl), allow_reply_options
  → build_orchestrator(yolo_mode=, allow_reply_options=)
       orchestrator_tools: [ProposeReplyOptionsTool] only if allow_reply_options
       _agent_middleware(yolo_mode=...)              (cost/run-python HITL gating, unchanged)
       all_personas(yolo_mode=, allow_reply_options=)
            persona tools_scope drops propose_reply_options if not allow_reply_options
            persona prompt += headless fragment if not allow_reply_options
  → AgentMessage.meta { mode, yolo_mode: clear_hitl } (compat)

Arena: runner.run_match → _drive_once(mode="yolo")  (no auto-continue)
```

## Error handling / edge cases

- Unknown `mode` string → 422 at the schema boundary (validated `Literal`).
- Both `mode` and legacy `yolo_mode` present → `mode` wins; `yolo_mode` ignored.
- A persona task whose `tools_scope` becomes empty after filtering → valid; the task produces a
  text-only artifact (no tool calls), which is the intended headless behavior.
- Interactive and AUTO behavior is byte-for-byte unchanged (regression-pinned by tests).

## Testing

**Backend**
- `mode → (clear_hitl, allow_reply_options)` derivation table for all three modes.
- Back-compat: `yolo_mode=True/False` (no `mode`) maps to auto/interactive; `mode` wins when both
  present.
- `build_orchestrator`: orchestrator tool list omits `ProposeReplyOptionsTool` iff
  `allow_reply_options=False`.
- Persona scope: `propose_reply_options` dropped from the three task scopes under YOLO.
- Prompt: headless fragment present under YOLO, absent under AUTO/Interactive.

**Arena**
- `_default_drive` drives exactly one turn (no auto-continue helpers remain).
- `run_match` passes `mode="yolo"`.
- Harvester back to strict 1:1 root→step (existing test restored).

**Frontend**
- Three-mode control renders, defaults to AUTO, and the controller sends the selected `mode`.

## Migration / compatibility

- No DB migration: `mode` rides in `AgentMessage.meta` (JSON); `yolo_mode` continues to be written
  as a derived field for any historical reader.
- The legacy `yolo_mode` request field stays accepted (deprecated) for one transition; remove later.

## Files touched (estimate)

- Backend: `schemas.py` (+`mode` field), `main.py` (pass `mode`), `services/agents.py` (derive +
  thread `allow_reply_options`), `deep_agent/orchestrator.py` (tool gating),
  `deep_agent/personas.py` (scope + prompt), `deep_agent/task_registry.py` or persona tool
  resolution, prompt fragments, `services/arena/runner.py`, `services/arena/trace_harvest.py`.
- Frontend: `types.ts`, `ChatComposer.tsx/.css`, `FloatingAgentMiniChat.tsx`,
  `useAgentChatController.ts`, `AgentDesk.tsx`.
- Tests across the above.
