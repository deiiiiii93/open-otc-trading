# Workflow Builder: Clean Chat Panel + Isolated Builder Threads

**Date:** 2026-06-26
**Status:** approved
**Follow-up to:** desk-workflows module (merged `972245e`)

## Problem

The "Create with AI" builder embeds the entire page-level `AgentDeskLive`
(thread rail with all ~47 threads, search, arena toggle, assets pane, persona
chips, Detailed/Compact toggle, model + mode controls) into a narrow column.
Result: the chat text wraps vertically one word per line, and builder
conversations land in the normal Agent Desk thread list, polluting it.

## Goals

1. **Clean chat panel** — a focused, widget-sized chat for building workflows.
2. **Isolated builder threads** — builder conversations live in their own
   space, never appearing in the normal Agent Desk thread list.

## Design

### Backend — thread `source` plumbing (no migration)

`AgentThread.source` already exists (`String(20)`, `server_default='desk'`);
arena threads use `source='arena'`. Reuse it for builder threads.

- `AgentService.create_thread(session, title, character, source="desk")` —
  stamp `AgentThread(..., source=source)` and include it in the audit payload.
- `AgentThreadCreate` schema gains `source: str = "desk"`.
- `POST /api/chat/threads` passes `payload.source` through.

### Frontend — controller (`useAgentChatController`)

Add `threadSource?: string` to the options (default `'desk'`):

- All three create-thread request bodies send `source: threadSource`.
- **Auto-select is scoped to `t.source === threadSource`.** Today the rail
  hides arena threads, but if an arena/builder thread is the most-recently
  updated, the controller still auto-selects it (shown with no rail highlight).
  Scoping auto-select to the controller's own source closes that latent leak:
  Agent Desk (`'desk'`) only auto-selects desk threads; the builder
  (`'workflow_builder'`) only its own.

### Frontend — clean panel

New `WorkflowBuilderChat.tsx` (+ co-located `.css`), driven by a controller:

- Header **"BUILD A WORKFLOW"** + a **New build** button.
- `MessageList` (the existing transcript component) + `ChatComposer` with the
  model picker and Send. **No** slash launcher (don't pass `workflows`) — you're
  building, not launching. No thread rail, assets pane, or persona chips.
- Empty state: "Describe the workflow you want to build."

`WorkflowBuilderLive`:

- `controller = useAgentChatController({ onToolStart, threadSource: 'workflow_builder' })`.
- Renders `<WorkflowBuilderChat controller={...} onNewBuild={...} />` instead of
  the full `AgentDeskLive`.
- **New build** clears the draft preview and calls `controller.createThread()`.
- `onToolStart` draft capture + Save (PUT-upsert on the meta slug) unchanged.
- On mount the controller resumes the latest `workflow_builder` thread
  (persisted), else starts on the empty state.

### Frontend — Agent Desk isolation

`ThreadRail` filter: show `source === 'desk'` always, `arena` behind the
existing toggle, and **`workflow_builder` never**. The 47-thread list stops
seeing build conversations.

## Out of scope

- No builder-side multi-thread switcher (single resumable build at a time).
- No new migration, no new API endpoint.
- Mode (Interactive/AUTO/YOLO) stays as the composer default (`auto`), which is
  correct for letting the build agent call `save_desk_workflow` without HITL
  friction.

## Test plan

- Backend: `create_thread` honors `source`; `POST /api/chat/threads` with
  `source='workflow_builder'` persists it; default stays `'desk'`.
- Controller: new threads carry `threadSource`; auto-select ignores
  other-source threads.
- Frontend: `WorkflowBuilderChat` renders transcript + composer + New build,
  no rail/assets; ThreadRail hides `workflow_builder` and (default) `arena`.
- Regression: full backend desk-workflow suite + frontend builder suite green;
  pre-existing 12 seedless-market-data failures unchanged.
