# Phase 2 — Envelopes + Page Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add typed capability scopes ("envelopes") to the shared agent runtime, extend the existing `PageContext` with `loaded_context.completeness` + a `PageAction[]` registry, and wire automatic envelope escalation so the pet on a page can widen scope to diagnostic / desk without a UI mode switch.

**Architecture:** Single shared runtime, **single orchestrator instance** built at `AgentService.__init__`. The envelope is threaded per-request through `RunnableConfig.configurable["envelope"]`. Tools wrap themselves in a capability decorator that consults `envelopes.tool_allowed(envelope, tool_name)` at invoke time. Escalation works by **re-running the turn**: when the model calls a blocked tool, the runtime logs the transition to `audit_events`, widens the envelope per the escalation table, and re-invokes the agent with the new envelope. One transition per turn maximum.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, LangChain Core 0.3, LangGraph 0.6, Pydantic v2, React 18 + TypeScript (Vite), pytest.

**Scope guard:** This plan covers Phase 2 only (7 PRs). Phase 3 (skill relocation + rewrite) will be planned after Phase 2 lands. P2.4 / P2.5 depend on Phase 1 — already complete.

**Pre-existing constraints from the survey:**

- The endpoint is `POST /api/chat/threads/{thread_id}/messages/stream` (streaming SSE), not `/agent/respond`. `AgentMessageCreate` already accepts an optional `page_context: AgentPageContext | None`.
- `AgentPageContext` (`backend/app/schemas.py:22-28`) currently has fields: `route`, `title`, `path`, `entity_ids`, `snapshot`, `chips`. Phase 2 **adds** `loaded_context` and `actions`, **deprecates** `chips` and `path` (but keeps them optional during migration).
- Frontend `PageContext` (`frontend/src/types.ts:176-183`) mirrors that shape. Same migration applies.
- `services/agents.py:AgentService` builds the orchestrator and personas once. Tool instantiation is one-shot. The capability gate must therefore be a per-tool runtime check (reads envelope from `RunnableConfig`), not a tool-list filter.
- `services/async_agents/runner.py` already has a free-text concept called "envelope" (it's a task brief paragraph). Rename it to `task_brief` in P2.1 to free up the name.

---

## File Structure

New files (no `os.path` dance — exact paths only):

```
backend/app/services/deep_agent/envelopes.py        # Envelope enum + capability table + escalation rules
backend/app/services/deep_agent/capability_gate.py  # Tool gating decorator that reads RunnableConfig
backend/app/services/deep_agent/escalation.py       # Detect denial -> transition table -> re-invoke
tests/test_envelopes.py
tests/test_capability_gate.py
tests/test_escalation_engine.py
tests/test_page_context_schema.py
tests/test_chat_endpoint_envelope.py
frontend/src/lib/pageActions.ts                     # PageAction declaration helper + registry
frontend/src/lib/envelope.ts                        # Envelope type + display label helper
```

Modified files:

```
backend/app/schemas.py                              # Extend AgentPageContext + new AgentEnvelope + PageAction
backend/app/main.py                                 # Accept envelope on AgentMessageCreate; default
backend/app/services/agents.py                      # Thread envelope into config; wire escalation engine
backend/app/services/async_agents/runner.py         # Rename existing "envelope" -> "task_brief"
backend/app/services/async_agents/agent.py          # Same rename
backend/app/services/async_agents/tools.py          # Same rename
backend/app/tools/*.py                              # Wrap each @tool with capability_gate decorator
frontend/src/types.ts                               # Extend PageContext type; add PageAction; add AgentEnvelope
frontend/src/hooks/useAgentChatController.ts        # Pass envelope to request body; surface envelope in messages
frontend/src/components/FloatingAgentMiniChat.tsx   # Send envelope: "pet_page"; render envelope badge
frontend/src/routes/AgentDesk.tsx                   # Send envelope: "desk_workflow"
frontend/src/routes/Positions.tsx                   # Populate loaded_context + actions
frontend/src/routes/Risk.tsx                        # Populate loaded_context + actions
frontend/src/routes/TrySolve.tsx                    # Populate loaded_context + actions
```

Files left untouched in Phase 2 (deferred to follow-ups):

```
frontend/src/routes/Portfolios.tsx                  # Not in fixture-prompt set; Phase 2 ships partial pages
frontend/src/routes/PricingParameters.tsx           # Same
frontend/src/routes/MarketData.tsx                  # Same
frontend/src/routes/RfqApproval.tsx                 # Same
frontend/src/routes/Reports.tsx                     # Same
frontend/src/routes/Tasks.tsx                       # Same
```

`pet_page` envelope still works on these — they just don't get a typed `loaded_context.completeness` answer, so the agent falls back to the snapshot. Any write request escalates to `desk_workflow`.

---

## Design decisions pinned

These are decided up-front so PR authors don't waste cycles re-relitigating:

**D1. Capability gate is a per-tool decorator, not a tool-list filter.**
Single orchestrator instance. Each `@tool` is wrapped by `capability_gated(group="domain_read")` in `app/tools/<domain>.py`. At invoke time, the gate reads `RunnableConfig.configurable["envelope"]` and checks `envelopes.tool_allowed(envelope, group)`. If denied, raises `CapabilityDeniedError("tool_denied_by_envelope", tool=..., requested_group=...)` which the escalation engine catches.

**D2. Escalation works by re-invoking the turn.**
The agent's first attempt under envelope `E` may raise `CapabilityDeniedError`. The escalation engine catches it, computes `new_envelope = envelopes.transition(E, reason)`, persists an `envelope.transitioned` audit event, and re-invokes the agent under `new_envelope`. **One transition per turn maximum** — a second denial after escalation fails hard so the model returns a structured error to the user.

**D3. The legacy "envelope" in `services/async_agents/runner.py` is renamed `task_brief` in P2.1.**
This frees the name. The async-agents brief is unrelated to capability scope.

**D4. `AgentPageContext.chips` and `.path` stay backward-compatible.**
Both become `Optional[...]` with deprecation comments. Pages migrate to `loaded_context` + `actions` over P2.3 + Phase 3. After Phase 3, `chips` and `path` can be removed.

**D5. `PageAction.backend_endpoint` is informational.**
The agent does NOT call the URL directly. Instead it calls the matching `@tool` (which already wraps the same backend service). `backend_endpoint` exists for the UI button's onClick **and** for debugging — it documents which HTTP endpoint matches each action. The agent and the UI button arrive at the same `services/domains/<domain>.<func>` via different paths.

**D6. Cost-preview (P2.7) reuses `estimate_*_seconds` from Phase 1.**
The risk/pricing facades already have `estimate_run_seconds(portfolio_id=...)` and `estimate_price_seconds(...)`. The cost-preview engine queries the right estimator based on the requested action, and if `> 30s` forces a one-shot HITL confirmation regardless of YOLO state.

---

### Task 1 (P2.1) — Envelopes module + escalation rules (pure data)

**Files:**
- Create: `backend/app/services/deep_agent/envelopes.py`
- Create: `tests/test_envelopes.py`
- Modify: `backend/app/services/async_agents/runner.py` — rename `envelope` arg to `task_brief`
- Modify: `backend/app/services/async_agents/agent.py` — same rename
- Modify: `backend/app/services/async_agents/tools.py` — same rename

**No runtime behavior change.** This PR is pure data + a rename to free the name.

#### Steps

- [ ] **1.1: Write the failing test for the Envelope enum**

```python
# tests/test_envelopes.py
from app.services.deep_agent.envelopes import (
    Envelope,
    EscalationReason,
    tool_allowed,
    transition,
)


def test_envelope_enum_has_four_members():
    assert {e.value for e in Envelope} == {
        "pet_page", "pet_diagnostic", "desk_workflow", "desk_async",
    }


def test_escalation_reason_enum_complete():
    expected = {
        "missing_required_context",
        "diagnostic_followup",
        "cross_page_dependency",
        "write_action_requested",
        "long_running_work",
        "large_result_set",
        "tool_denied_by_envelope",
    }
    assert {r.value for r in EscalationReason} == expected
```

Run: `uv run pytest tests/test_envelopes.py::test_envelope_enum_has_four_members -v`
Expected: FAIL with `ImportError: cannot import name 'Envelope'`.

- [ ] **1.2: Write `envelopes.py` with the enums and tables**

```python
# backend/app/services/deep_agent/envelopes.py
"""Envelope catalog — typed capability scopes for the shared runtime.

An envelope is a typed capability scope. The runtime grants a tool-group
set per envelope; tools wrapped with the capability gate consult this
module at invoke time. Escalation transitions are also defined here.
"""
from __future__ import annotations

from enum import Enum


class Envelope(str, Enum):
    PET_PAGE = "pet_page"
    PET_DIAGNOSTIC = "pet_diagnostic"
    DESK_WORKFLOW = "desk_workflow"
    DESK_ASYNC = "desk_async"


class EscalationReason(str, Enum):
    MISSING_REQUIRED_CONTEXT = "missing_required_context"
    DIAGNOSTIC_FOLLOWUP = "diagnostic_followup"
    CROSS_PAGE_DEPENDENCY = "cross_page_dependency"
    WRITE_ACTION_REQUESTED = "write_action_requested"
    LONG_RUNNING_WORK = "long_running_work"
    LARGE_RESULT_SET = "large_result_set"
    TOOL_DENIED_BY_ENVELOPE = "tool_denied_by_envelope"


# Tool-group catalog. A tool declares its group via the capability gate
# decorator; the gate then checks the group is allowed under the current
# envelope. Groups are coarse on purpose: granularity should be in the
# *envelope*, not in the group taxonomy.
class ToolGroup(str, Enum):
    PAGE_READ = "page_read"             # read data the page already loaded
    PAGE_DETAIL = "page_detail"         # fetch one row by id (positions, portfolios)
    PAGE_ACTION = "page_action"         # invoke a page-declared action
    TASK_POLL = "task_poll"             # check task status
    DETERMINISTIC_PY = "deterministic_py"  # run_python tool
    DOMAIN_READ = "domain_read"         # broader domain queries (list, filter)
    DOMAIN_WRITE = "domain_write"       # persisted writes (import, create, delete)
    ASYNC_DISPATCH = "async_dispatch"   # start_async_agent and friends


_ALLOWED: dict[Envelope, frozenset[ToolGroup]] = {
    Envelope.PET_PAGE: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
    }),
    Envelope.PET_DIAGNOSTIC: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
    }),
    Envelope.DESK_WORKFLOW: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
        ToolGroup.DOMAIN_WRITE,
    }),
    Envelope.DESK_ASYNC: frozenset({
        ToolGroup.PAGE_READ,
        ToolGroup.PAGE_DETAIL,
        ToolGroup.PAGE_ACTION,
        ToolGroup.TASK_POLL,
        ToolGroup.DETERMINISTIC_PY,
        ToolGroup.DOMAIN_READ,
        ToolGroup.DOMAIN_WRITE,
        ToolGroup.ASYNC_DISPATCH,
    }),
}


def tool_allowed(envelope: Envelope, group: ToolGroup) -> bool:
    """Return True if the given tool group is permitted under this envelope."""
    return group in _ALLOWED[envelope]


# Escalation transitions: (current_envelope, reason) -> new_envelope.
# Entries not in this table mean "stay in current envelope" (denial bubbles up).
_TRANSITIONS: dict[tuple[Envelope, EscalationReason], Envelope] = {
    (Envelope.PET_PAGE, EscalationReason.DIAGNOSTIC_FOLLOWUP): Envelope.PET_DIAGNOSTIC,
    (Envelope.PET_PAGE, EscalationReason.MISSING_REQUIRED_CONTEXT): Envelope.PET_DIAGNOSTIC,
    (Envelope.PET_PAGE, EscalationReason.WRITE_ACTION_REQUESTED): Envelope.DESK_WORKFLOW,
    (Envelope.PET_PAGE, EscalationReason.CROSS_PAGE_DEPENDENCY): Envelope.DESK_WORKFLOW,
    (Envelope.PET_DIAGNOSTIC, EscalationReason.CROSS_PAGE_DEPENDENCY): Envelope.DESK_WORKFLOW,
    (Envelope.PET_DIAGNOSTIC, EscalationReason.WRITE_ACTION_REQUESTED): Envelope.DESK_WORKFLOW,
    (Envelope.DESK_WORKFLOW, EscalationReason.LONG_RUNNING_WORK): Envelope.DESK_ASYNC,
}


def transition(envelope: Envelope, reason: EscalationReason) -> Envelope | None:
    """Return the new envelope after escalation, or None if no transition exists."""
    return _TRANSITIONS.get((envelope, reason))


# Reverse lookup: which reason caused a tool denial under a given envelope?
# Used by the escalation engine when it catches CapabilityDeniedError.
def reason_for_denied_group(
    envelope: Envelope, group: ToolGroup
) -> EscalationReason | None:
    """If `group` is denied under `envelope`, return the reason to escalate.

    We map the denied group to a coarse escalation reason. The runtime can
    refine this if it has more context (e.g., page_context shows the page
    declared the action, so it's not a write_action_requested but a
    missing_required_context).
    """
    if tool_allowed(envelope, group):
        return None
    if group is ToolGroup.DOMAIN_WRITE:
        return EscalationReason.WRITE_ACTION_REQUESTED
    if group is ToolGroup.DOMAIN_READ:
        return EscalationReason.DIAGNOSTIC_FOLLOWUP
    if group is ToolGroup.ASYNC_DISPATCH:
        return EscalationReason.LONG_RUNNING_WORK
    return EscalationReason.TOOL_DENIED_BY_ENVELOPE


__all__ = [
    "Envelope",
    "EscalationReason",
    "ToolGroup",
    "tool_allowed",
    "transition",
    "reason_for_denied_group",
]
```

- [ ] **1.3: Write tests for capability table, transitions, and reverse lookup**

```python
# tests/test_envelopes.py (append)
import pytest

from app.services.deep_agent.envelopes import (
    Envelope,
    EscalationReason,
    ToolGroup,
    reason_for_denied_group,
    tool_allowed,
    transition,
)


def test_pet_page_blocks_domain_write():
    assert tool_allowed(Envelope.PET_PAGE, ToolGroup.DOMAIN_WRITE) is False


def test_desk_workflow_allows_domain_read_and_write():
    assert tool_allowed(Envelope.DESK_WORKFLOW, ToolGroup.DOMAIN_READ) is True
    assert tool_allowed(Envelope.DESK_WORKFLOW, ToolGroup.DOMAIN_WRITE) is True


def test_only_desk_async_can_dispatch_async():
    for env in Envelope:
        expected = env is Envelope.DESK_ASYNC
        assert tool_allowed(env, ToolGroup.ASYNC_DISPATCH) is expected


@pytest.mark.parametrize(
    "current, reason, expected",
    [
        (Envelope.PET_PAGE, EscalationReason.DIAGNOSTIC_FOLLOWUP, Envelope.PET_DIAGNOSTIC),
        (Envelope.PET_PAGE, EscalationReason.WRITE_ACTION_REQUESTED, Envelope.DESK_WORKFLOW),
        (Envelope.PET_DIAGNOSTIC, EscalationReason.CROSS_PAGE_DEPENDENCY, Envelope.DESK_WORKFLOW),
        (Envelope.DESK_WORKFLOW, EscalationReason.LONG_RUNNING_WORK, Envelope.DESK_ASYNC),
        # No transition out of DESK_ASYNC.
        (Envelope.DESK_ASYNC, EscalationReason.LONG_RUNNING_WORK, None),
    ],
)
def test_transition_table(current, reason, expected):
    assert transition(current, reason) == expected


def test_reason_for_denied_group_domain_write_from_pet():
    assert (
        reason_for_denied_group(Envelope.PET_PAGE, ToolGroup.DOMAIN_WRITE)
        is EscalationReason.WRITE_ACTION_REQUESTED
    )


def test_reason_for_denied_group_returns_none_when_allowed():
    assert reason_for_denied_group(Envelope.DESK_WORKFLOW, ToolGroup.DOMAIN_WRITE) is None
```

Run: `uv run pytest tests/test_envelopes.py -v`
Expected: all pass.

- [ ] **1.4: Rename the legacy `envelope` arg in async_agents to `task_brief`**

First, grep to confirm the surface:

```bash
grep -rn "envelope" backend/app/services/async_agents/
```

Then edit each file. Concretely:

In `backend/app/services/async_agents/runner.py`:
- Rename function parameter `envelope: str` to `task_brief: str` everywhere.
- Rename local variable `envelope` to `task_brief` in `compose_task_brief` and `start_async_agent_task`.

In `backend/app/services/async_agents/agent.py`:
- Same rename for any param/local of that name.

In `backend/app/services/async_agents/tools.py`:
- Same rename for the @tool args_schema field (if exposed) — note this changes the args_schema field name visible to the LLM. Inspect tests first to see if any assert on the field name; update them if so.

Run: `uv run pytest tests/test_async_agents_tools.py tests/test_async_agents_hitl.py -v`
Expected: all pass (after test updates if any).

- [ ] **1.5: Commit**

```bash
git add backend/app/services/deep_agent/envelopes.py tests/test_envelopes.py backend/app/services/async_agents/
git commit -m "feat(envelopes): add envelope catalog + escalation rules (P2.1)"
```

---

### Task 2 (P2.2) — Server-side PageContext + PageAction schemas

**Files:**
- Modify: `backend/app/schemas.py` — extend `AgentPageContext`; add `PageAction`, `LoadedContext`; add `AgentEnvelope` literal
- Create: `tests/test_page_context_schema.py`

**Compatibility contract:** `chips` and `path` stay accepted as optional fields. New fields `loaded_context` and `actions` are optional too — pages migrate over P2.3.

#### Steps

- [ ] **2.1: Write the failing test**

```python
# tests/test_page_context_schema.py
from app.schemas import AgentPageContext, PageAction, LoadedContext


def test_loaded_context_defaults_to_complete_when_omitted():
    """Backward compatibility: a payload without loaded_context still parses."""
    ctx = AgentPageContext(route="positions", title="Positions")
    assert ctx.loaded_context is None  # callers fall back to legacy snapshot

def test_loaded_context_completeness_enum():
    lc = LoadedContext(completeness="paginated", visible_count=20, total_count=120, query_ref="portfolio:7")
    assert lc.completeness == "paginated"
    assert lc.visible_count == 20

def test_loaded_context_rejects_invalid_completeness():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        LoadedContext(completeness="bogus")


def test_page_action_required_fields():
    a = PageAction(
        name="run_risk",
        required_ids=["portfolio_id"],
        confirmation="implicit",
        backend_endpoint="POST /api/risk/runs",
    )
    assert a.name == "run_risk"


def test_page_action_rejects_unknown_confirmation():
    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PageAction(
            name="run_risk", required_ids=[], confirmation="maybe",
            backend_endpoint="POST /x",
        )
```

Run: `uv run pytest tests/test_page_context_schema.py -v`
Expected: FAIL with `ImportError: cannot import name 'LoadedContext'`.

- [ ] **2.2: Extend `schemas.py`**

Locate the existing `AgentPageContext` (around `backend/app/schemas.py:22-28`) and modify:

```python
# backend/app/schemas.py

from typing import Literal

# (existing imports unchanged)

ConfirmationMode = Literal["implicit", "explicit", "destructive"]
LoadedCompleteness = Literal["complete", "paginated", "partial", "empty"]
AgentEnvelopeLiteral = Literal["pet_page", "pet_diagnostic", "desk_workflow", "desk_async"]


class LoadedContext(BaseModel):
    """Page-side declaration of how much of the page's data the agent can see.

    ``completeness == "complete"`` means the agent can answer count/aggregate
    queries from ``AgentPageContext.snapshot`` without escalating. ``paginated``
    means only a window is loaded; use ``query_ref`` or escalate to a domain
    read. ``partial`` and ``empty`` always trigger escalation for non-trivial
    questions.
    """

    completeness: LoadedCompleteness
    visible_count: int | None = None
    total_count: int | None = None
    query_ref: str | None = Field(
        default=None,
        description="Opaque reference the agent can pass back to a query tool to materialize the full set.",
    )


class PageAction(BaseModel):
    """A page-declared backend action the agent may invoke on behalf of the user.

    ``backend_endpoint`` is informational — the agent invokes the corresponding
    domain tool, which routes through the same service function the UI button
    uses. ``confirmation`` gates whether the pet executes the action directly
    (under YOLO) or asks first.
    """

    name: str
    required_ids: list[str] = Field(default_factory=list)
    confirmation: ConfirmationMode = "explicit"
    backend_endpoint: str = ""


class AgentPageContext(BaseModel):
    """Typed contract for the page-context payload the frontend sends per message.

    Phase 2 additions: ``loaded_context``, ``actions``. The legacy ``chips`` and
    ``path`` fields are retained as optional for backward compatibility while
    pages migrate in P2.3 + Phase 3.
    """

    route: str
    title: str
    entity_ids: dict[str, int | str | None] = Field(default_factory=dict)
    snapshot: dict[str, Any] = Field(default_factory=dict)
    # Phase 2 additions (optional during migration):
    loaded_context: LoadedContext | None = None
    actions: list[PageAction] = Field(default_factory=list)
    # Legacy (deprecated — remove in Phase 3):
    path: str | None = None
    chips: list[str] = Field(default_factory=list)
```

- [ ] **2.3: Add `envelope` field to `AgentMessageCreate`**

```python
# backend/app/schemas.py (locate AgentMessageCreate, ~line 110)

class AgentMessageCreate(BaseModel):
    content: str
    character: str | None = None
    model: AgentModelSelection | None = None
    yolo_mode: bool = False
    page_context: AgentPageContext | None = None
    context_usage: AgentContextUsage | None = None
    accounting_date: str | None = None
    # Phase 2: optional, defaults applied by the endpoint based on UI origin.
    envelope: AgentEnvelopeLiteral | None = None
```

- [ ] **2.4: Run tests**

```bash
uv run pytest tests/test_page_context_schema.py -v
```

Expected: all pass.

Run the agent test suite to confirm no regression in callers of `AgentPageContext`:

```bash
uv run pytest tests/test_agent_tools.py tests/test_async_agents_tools.py -v
```

Expected: all pass.

- [ ] **2.5: Commit**

```bash
git commit -am "feat(schema): extend AgentPageContext with loaded_context + actions (P2.2)"
```

---

### Task 3 (P2.3) — Frontend PageContext extension + three page populators

**Files:**
- Modify: `frontend/src/types.ts` — extend `PageContext`; add `LoadedContext`, `PageAction`, `Envelope`
- Create: `frontend/src/lib/pageActions.ts` — helper to declare actions in one place per page
- Create: `frontend/src/lib/envelope.ts` — envelope display labels
- Modify: `frontend/src/routes/Positions.tsx` — emit `loaded_context` + `actions`
- Modify: `frontend/src/routes/Risk.tsx` — emit `loaded_context` + `actions`
- Modify: `frontend/src/routes/TrySolve.tsx` — emit `loaded_context` + `actions`

Other pages still use the legacy shape; backend accepts both (P2.2 made the new fields optional).

#### Steps

- [ ] **3.1: Extend TypeScript types**

```typescript
// frontend/src/types.ts

// Add near the existing PageContext (~line 176)

export type LoadedCompleteness = "complete" | "paginated" | "partial" | "empty";
export type ConfirmationMode = "implicit" | "explicit" | "destructive";
export type Envelope = "pet_page" | "pet_diagnostic" | "desk_workflow" | "desk_async";

export type LoadedContext = {
  completeness: LoadedCompleteness;
  visible_count?: number;
  total_count?: number;
  query_ref?: string;
};

export type PageAction = {
  name: string;
  required_ids: string[];
  confirmation: ConfirmationMode;
  backend_endpoint: string;
};

// Replace the existing PageContext with:
export type PageContext = {
  route: Route;
  title: string;
  entity_ids: Record<string, number | string | null | undefined>;
  snapshot: Record<string, unknown>;
  // Phase 2 additions:
  loaded_context?: LoadedContext;
  actions?: PageAction[];
  // Legacy (still accepted for unmigrated pages):
  path?: string;
  chips?: string[];
};
```

- [ ] **3.2: Write the `pageActions` helper**

```typescript
// frontend/src/lib/pageActions.ts
/**
 * Declarative page-action helper. Each page calls `declareActions([...])`
 * once when building its `PageContext`. The shape is identical to the
 * backend `PageAction` schema — keeping them in sync is the page's
 * responsibility (Phase 3 will add a build-time check).
 */
import type { PageAction } from "../types";

export function declareActions(actions: PageAction[]): PageAction[] {
  // Returned as-is for now; helper exists to give pages a single import
  // site and to leave room for future validation.
  return actions;
}
```

- [ ] **3.3: Write the `envelope` display helper**

```typescript
// frontend/src/lib/envelope.ts
import type { Envelope } from "../types";

export function envelopeBadgeLabel(env: Envelope): string {
  switch (env) {
    case "pet_page": return "Pet";
    case "pet_diagnostic": return "Pet · diagnostic";
    case "desk_workflow": return "Desk";
    case "desk_async": return "Desk · async";
  }
}
```

- [ ] **3.4: Populate Positions page**

In `frontend/src/routes/Positions.tsx`, find the `usePageContextReporter` call (the place that emits the page context). Add `loaded_context` and `actions`:

```typescript
import { declareActions } from "../lib/pageActions";

// Inside the page component, when building the context payload:
const totalCount = positions.length;          // however positions are loaded
const isPaginated = paginationCursor != null; // existing pagination state

usePageContextReporter({
  route: "positions",
  title: "Positions",
  entity_ids: { portfolio_id: selectedPortfolioId ?? null },
  snapshot: { positions, /* whatever already lived here */ },
  loaded_context: {
    completeness: isPaginated ? "paginated" : "complete",
    visible_count: totalCount,
    total_count: paginationTotal ?? totalCount,
    query_ref: isPaginated ? `portfolio:${selectedPortfolioId}` : undefined,
  },
  actions: declareActions([
    {
      name: "count_positions",
      required_ids: ["portfolio_id"],
      confirmation: "implicit",
      backend_endpoint: "GET /api/positions",
    },
    {
      name: "price_portfolio_positions",
      required_ids: ["portfolio_id"],
      confirmation: "explicit",
      backend_endpoint: "POST /api/pricing/positions/run",
    },
  ]),
});
```

If `usePageContextReporter` doesn't exist yet under that name, locate the equivalent in `frontend/src/hooks/usePageContextReporter.ts` (per the survey) — and conform to its signature.

- [ ] **3.5: Populate Risk page**

In `frontend/src/routes/Risk.tsx`:

```typescript
import { declareActions } from "../lib/pageActions";

usePageContextReporter({
  route: "risk",
  title: "Risk",
  entity_ids: {
    portfolio_id: selectedPortfolioId ?? null,
    pricing_profile_id: pricingProfileId ?? null,
  },
  snapshot: { risk_totals: greeksTotals, latest_risk_run_id, running_task_id },
  loaded_context: {
    completeness: "complete",
    visible_count: positionRows.length,
    total_count: positionRows.length,
  },
  actions: declareActions([
    {
      name: "run_risk",
      required_ids: ["portfolio_id", "pricing_profile_id"],
      confirmation: "explicit",
      backend_endpoint: "POST /api/risk/runs",
    },
    {
      name: "read_risk_result",
      required_ids: ["portfolio_id"],
      confirmation: "implicit",
      backend_endpoint: "GET /api/risk/runs/latest",
    },
    {
      name: "get_task_status",
      required_ids: [],
      confirmation: "implicit",
      backend_endpoint: "GET /api/tasks/{task_id}",
    },
  ]),
});
```

- [ ] **3.6: Populate Try-Solve page**

In `frontend/src/routes/TrySolve.tsx`:

```typescript
usePageContextReporter({
  route: "try-solve",
  title: "Try Solve",
  entity_ids: { row_id: selectedRowId ?? null },
  snapshot: {
    product_key: selectedProductKey,
    label: selectedRowLabel,
    row_fields: selectedRowFields,
    market_inputs,
    quote_request,
    diagnostics,
    solver_state,
    request_queue_summaries,
  },
  loaded_context: { completeness: "complete" },
  actions: declareActions([
    {
      name: "solve_imported_row",
      required_ids: ["row_id"],
      confirmation: "implicit",
      backend_endpoint: "POST /api/try-solve/{row_id}/solve",
    },
    {
      name: "create_request_queue_item",
      required_ids: ["row_id"],
      confirmation: "explicit",
      backend_endpoint: "POST /api/rfq/requests",
    },
  ]),
});
```

- [ ] **3.7: Verify TypeScript compiles**

```bash
cd frontend && pnpm tsc --noEmit
```

Expected: zero errors.

- [ ] **3.8: Commit**

```bash
git commit -am "feat(frontend): extend PageContext + populate Positions/Risk/TrySolve (P2.3)"
```

---

### Task 4 (P2.4) — Runtime envelope threading + capability gate

**Depends on:** P2.1, P2.2, and Phase 1 complete (relies on `app.tools.<domain>` modules existing).

**Files:**
- Create: `backend/app/services/deep_agent/capability_gate.py`
- Create: `tests/test_capability_gate.py`
- Modify: `backend/app/main.py` — accept envelope on `AgentMessageCreate`; default by route hint; pass to `stream_and_persist`
- Modify: `backend/app/services/agents.py` — thread envelope into `RunnableConfig`; persist into `message.meta`
- Modify: `backend/app/tools/<domain>.py` (all 7) — wrap each `@tool` with `capability_gated(group=...)`

#### Steps

- [ ] **4.1: Write the failing test for the gate decorator**

```python
# tests/test_capability_gate.py
import pytest
from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import tool

from app.services.deep_agent.capability_gate import (
    CapabilityDeniedError,
    capability_gated,
)
from app.services.deep_agent.envelopes import Envelope, ToolGroup


@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("dangerous_write")
def dangerous_write_tool(x: int) -> dict:
    """Pretend to mutate state."""
    return {"ok": True, "x": x}


def _config_with_envelope(env: Envelope) -> RunnableConfig:
    return {"configurable": {"envelope": env.value}}


def test_gate_blocks_domain_write_under_pet_page():
    with pytest.raises(CapabilityDeniedError) as exc:
        dangerous_write_tool.invoke(
            {"x": 1}, config=_config_with_envelope(Envelope.PET_PAGE)
        )
    assert exc.value.group is ToolGroup.DOMAIN_WRITE
    assert exc.value.envelope is Envelope.PET_PAGE


def test_gate_allows_domain_write_under_desk_workflow():
    result = dangerous_write_tool.invoke(
        {"x": 1}, config=_config_with_envelope(Envelope.DESK_WORKFLOW)
    )
    assert result["ok"] is True


def test_gate_defaults_to_pet_page_when_envelope_missing():
    """No envelope in config -> fail closed: assume the most restrictive."""
    with pytest.raises(CapabilityDeniedError):
        dangerous_write_tool.invoke({"x": 1})
```

Run: `uv run pytest tests/test_capability_gate.py -v`
Expected: FAIL with `ImportError`.

- [ ] **4.2: Implement `capability_gate.py`**

```python
# backend/app/services/deep_agent/capability_gate.py
"""Tool-level capability gate.

Each tool wrapped with ``@capability_gated(group=ToolGroup.X)`` checks the
``envelope`` value passed through ``RunnableConfig.configurable`` at invoke
time. If the group is not allowed under the current envelope, the gate
raises ``CapabilityDeniedError``. The escalation engine catches this
exception and decides whether to retry the turn under a widened envelope.

Default behavior when the envelope is missing: fail closed to PET_PAGE.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from langchain_core.runnables.config import RunnableConfig
from langchain_core.tools import BaseTool

from .envelopes import Envelope, ToolGroup, tool_allowed


class CapabilityDeniedError(Exception):
    """Raised by the gate when a tool group is not allowed under the envelope."""

    def __init__(
        self,
        *,
        envelope: Envelope,
        group: ToolGroup,
        tool_name: str,
    ) -> None:
        super().__init__(
            f"tool '{tool_name}' (group={group.value}) denied under envelope "
            f"'{envelope.value}'"
        )
        self.envelope = envelope
        self.group = group
        self.tool_name = tool_name


def _envelope_from_config(config: RunnableConfig | None) -> Envelope:
    if not config:
        return Envelope.PET_PAGE
    configurable = config.get("configurable") or {}
    raw = configurable.get("envelope")
    if not raw:
        return Envelope.PET_PAGE
    try:
        return Envelope(raw)
    except ValueError:
        return Envelope.PET_PAGE


def capability_gated(*, group: ToolGroup) -> Callable[[BaseTool], BaseTool]:
    """Decorator factory: wrap a LangChain ``@tool`` with envelope gating.

    Usage:

        @capability_gated(group=ToolGroup.DOMAIN_WRITE)
        @tool("create_portfolio", args_schema=CreatePortfolioInput)
        def create_portfolio_tool(...):
            ...

    Decorator order matters — ``@capability_gated`` must be on the OUTSIDE
    so it wraps the BaseTool that ``@tool`` produces.
    """

    def wrap(t: BaseTool) -> BaseTool:
        original_invoke = t.invoke
        original_ainvoke = t.ainvoke

        @wraps(original_invoke)
        def gated_invoke(
            input: Any,  # noqa: A002 - matches LangChain signature
            config: RunnableConfig | None = None,
            **kwargs: Any,
        ) -> Any:
            envelope = _envelope_from_config(config)
            if not tool_allowed(envelope, group):
                raise CapabilityDeniedError(
                    envelope=envelope, group=group, tool_name=t.name
                )
            return original_invoke(input, config=config, **kwargs)

        async def gated_ainvoke(
            input: Any,  # noqa: A002
            config: RunnableConfig | None = None,
            **kwargs: Any,
        ) -> Any:
            envelope = _envelope_from_config(config)
            if not tool_allowed(envelope, group):
                raise CapabilityDeniedError(
                    envelope=envelope, group=group, tool_name=t.name
                )
            return await original_ainvoke(input, config=config, **kwargs)

        t.invoke = gated_invoke  # type: ignore[method-assign]
        t.ainvoke = gated_ainvoke  # type: ignore[method-assign]
        # Stash the group so callers (escalation engine, audit) can read it.
        t.__capability_group__ = group  # type: ignore[attr-defined]
        return t

    return wrap


__all__ = ["CapabilityDeniedError", "capability_gated"]
```

- [ ] **4.3: Run the gate tests**

```bash
uv run pytest tests/test_capability_gate.py -v
```

Expected: all pass.

- [ ] **4.4: Wrap each `@tool` with its capability group**

Edit `backend/app/tools/<domain>.py` for all 7 domains. Wrapping convention:

```python
# backend/app/tools/portfolios.py (example)
from app.services.deep_agent.capability_gate import capability_gated
from app.services.deep_agent.envelopes import ToolGroup

@capability_gated(group=ToolGroup.DOMAIN_READ)
@tool("list_portfolios", args_schema=ListPortfoliosInput)
def list_portfolios_tool(...) -> dict[str, Any]:
    ...

@capability_gated(group=ToolGroup.DOMAIN_WRITE)
@tool("create_portfolio", args_schema=CreatePortfolioInput)
def create_portfolio_tool(...) -> dict[str, Any]:
    ...
```

Per-tool group assignments (apply consistently across the seven domain modules):

| Tool | Group |
|---|---|
| `list_portfolios`, `get_portfolio` | `DOMAIN_READ` |
| `create_portfolio`, `update_portfolio`, `delete_portfolio`, `set_portfolio_rule`, `add_positions_to_portfolio`, `remove_positions_from_portfolio`, `add_portfolio_sources`, `remove_portfolio_sources` | `DOMAIN_WRITE` |
| `get_positions`, `get_latest_position_valuations` | `DOMAIN_READ` |
| `import_otc_positions`, `import_position_market_inputs` | `DOMAIN_WRITE` |
| `fetch_market_snapshot`, `list_market_data_profiles` | `DOMAIN_READ` |
| `price_product` | `DOMAIN_READ` (read-only — no DB write) |
| `price_positions` | `DOMAIN_WRITE` |
| `calculate_risk`, `recommend_hedge`, `get_latest_risk_run` | `DOMAIN_READ` |
| `run_risk` | `DOMAIN_WRITE` |
| `solve_rfq`, `get_rfq_catalog`, `draft_rfq_from_natural_language`, `validate_rfq_terms` | `DOMAIN_READ` |
| `create_or_update_rfq_draft`, `quote_rfq`, `submit_rfq_for_approval`, `approve_rfq`, `reject_rfq`, `release_rfq`, `mark_rfq_client_accepted`, `book_rfq_to_position` | `DOMAIN_WRITE` |
| `list_reports`, `get_report`, `run_report_batch` | `DOMAIN_READ` |
| `create_report` | `DOMAIN_WRITE` |
| `run_python` | `DETERMINISTIC_PY` |
| `start_async_agent`, `list_async_agents`, `cancel_async_agent` | `ASYNC_DISPATCH` |
| `propose_reply_options` | `PAGE_ACTION` (no DB; UI-only) |

After all edits, run:

```bash
uv run python -c "from app.tools import QUANT_AGENT_TOOLS; print(len(QUANT_AGENT_TOOLS))"
```

Expected: `43` (count unchanged).

- [ ] **4.5: Run the agent test suite to verify no regression at envelope = pet_page**

```bash
uv run pytest tests/test_agent_tools.py -v
```

The existing tests don't pass an envelope, so they fall through to PET_PAGE — and they previously called write tools (e.g., `create_portfolio_tool`) directly. The tests will now fail with `CapabilityDeniedError`.

**Fix path**: update each test that exercises a `DOMAIN_WRITE` tool to set `config={"configurable": {"envelope": "desk_workflow"}}`. Concretely:

```python
result = create_portfolio_tool.invoke(
    {"name": "P", "kind": "container"},
    config={"configurable": {"envelope": "desk_workflow"}},
)
```

Apply this to every test in `tests/test_agent_tools.py` that calls a write tool. ≈ 12 tests, each ~3 lines.

Run again:

```bash
uv run pytest tests/test_agent_tools.py -v
```

Expected: all pass.

- [ ] **4.6: Thread envelope through `AgentService.stream_and_persist`**

In `backend/app/services/agents.py`, locate `stream_and_persist` (around line 673). Add an `envelope` parameter and thread it into `RunnableConfig`:

```python
# backend/app/services/agents.py

from .deep_agent.envelopes import Envelope

async def stream_and_persist(
    self,
    # ... existing args ...
    page_context: AgentPageContext | None = None,
    envelope: Envelope | str | None = None,
    # ... rest ...
):
    resolved_envelope = self._resolve_envelope(envelope, page_context)
    # When building the LangGraph run config, attach the envelope:
    config = graph_run_config(
        thread_id=thread.id,
        # ... existing config ...
        configurable_extra={"envelope": resolved_envelope.value},
    )
    # ... rest of method unchanged ...
```

And add the helper:

```python
def _resolve_envelope(
    self,
    envelope: Envelope | str | None,
    page_context: AgentPageContext | None,
) -> Envelope:
    if isinstance(envelope, Envelope):
        return envelope
    if isinstance(envelope, str):
        try:
            return Envelope(envelope)
        except ValueError:
            pass
    # No envelope supplied — pick a default based on UI hint in page_context.
    # ``FloatingAgentMiniChat`` sets character ``"pet"`` (or omits page_context
    # for the desk). Treat presence of page_context as pet-origin.
    if page_context is not None:
        return Envelope.PET_PAGE
    return Envelope.DESK_WORKFLOW
```

In `graph_run_config` (locate it in `backend/app/services/deep_agent/runtime_config.py`), accept and pass through `configurable_extra`:

```python
def graph_run_config(
    *,
    thread_id: int,
    # ... existing args ...
    configurable_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configurable = {
        # ... existing keys ...
    }
    if configurable_extra:
        configurable.update(configurable_extra)
    return {"configurable": configurable}
```

- [ ] **4.7: Plumb envelope from the HTTP endpoint**

In `backend/app/main.py`, locate the message-stream endpoint (around line 576). The payload `AgentMessageCreate` now has `envelope: AgentEnvelopeLiteral | None`. Pass it to `stream_and_persist`:

```python
# backend/app/main.py (~line 615)

await agent_service.stream_and_persist(
    thread=thread,
    # ... existing args ...
    page_context=payload.page_context,
    envelope=payload.envelope,
    # ... rest ...
)
```

And persist `envelope` into the user message's `meta`:

```python
user_msg.meta = {
    **(user_msg.meta or {}),
    "page_context": payload.page_context.model_dump(mode="json") if payload.page_context else None,
    "envelope": payload.envelope or None,
}
```

- [ ] **4.8: Write the HTTP integration test**

```python
# tests/test_chat_endpoint_envelope.py
from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.config import Settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import database
    settings = Settings(database_url=f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setattr("app.config.get_settings", lambda: settings)
    database.configure_database(settings)
    database.init_db()
    return TestClient(app)


def test_message_post_accepts_envelope_field(client, monkeypatch):
    # Stub the streaming flow so the test stays unit-scope:
    seen: dict = {}

    async def fake_stream(self, **kwargs):
        seen.update(kwargs)
        yield "event: ready\ndata: {}\n\n"

    monkeypatch.setattr(
        "app.services.agents.AgentService.stream_and_persist",
        fake_stream,
    )

    # Create thread first (use whatever the real fixture does).
    r = client.post("/api/chat/threads", json={"title": "t"})
    thread_id = r.json()["id"]

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "hi", "envelope": "pet_page"},
    )
    assert r.status_code == 200
    assert seen.get("envelope") == "pet_page"


def test_message_post_defaults_envelope_when_omitted(client, monkeypatch):
    """No envelope -> service receives None and picks the default."""
    seen: dict = {}

    async def fake_stream(self, **kwargs):
        seen.update(kwargs)
        yield "event: ready\ndata: {}\n\n"

    monkeypatch.setattr(
        "app.services.agents.AgentService.stream_and_persist",
        fake_stream,
    )

    r = client.post("/api/chat/threads", json={"title": "t"})
    thread_id = r.json()["id"]

    r = client.post(
        f"/api/chat/threads/{thread_id}/messages/stream",
        json={"content": "hi"},
    )
    assert r.status_code == 200
    assert seen.get("envelope") is None
```

Run: `uv run pytest tests/test_chat_endpoint_envelope.py -v`
Expected: all pass.

- [ ] **4.9: Commit**

```bash
git commit -am "feat(runtime): thread envelope + capability gate through chat endpoint (P2.4)"
```

---

### Task 5 (P2.5) — Escalation engine

**Files:**
- Create: `backend/app/services/deep_agent/escalation.py`
- Create: `tests/test_escalation_engine.py`
- Modify: `backend/app/services/agents.py` — wrap the agent invocation in the escalation engine

#### Steps

- [ ] **5.1: Write the failing test**

```python
# tests/test_escalation_engine.py
import pytest

from app.services.deep_agent.capability_gate import CapabilityDeniedError
from app.services.deep_agent.envelopes import Envelope, EscalationReason, ToolGroup
from app.services.deep_agent.escalation import run_with_escalation


class _FakeGraph:
    """Pretends to be a LangGraph agent. Captures the envelope each invoke saw."""

    def __init__(self, *, denied_envelopes: list[Envelope]):
        self._denied = list(denied_envelopes)
        self.invocations: list[Envelope] = []

    async def ainvoke(self, state, config):
        env = Envelope(config["configurable"]["envelope"])
        self.invocations.append(env)
        if self._denied and env in self._denied:
            raise CapabilityDeniedError(
                envelope=env, group=ToolGroup.DOMAIN_WRITE, tool_name="x"
            )
        return {"messages": [{"role": "assistant", "content": "ok"}]}


@pytest.mark.asyncio
async def test_pet_page_escalates_to_desk_workflow_on_domain_write_denial():
    graph = _FakeGraph(denied_envelopes=[Envelope.PET_PAGE])
    audit_log: list[dict] = []

    async def audit(event):
        audit_log.append(event)

    result = await run_with_escalation(
        graph,
        state={"messages": []},
        envelope=Envelope.PET_PAGE,
        record_audit=audit,
    )
    assert graph.invocations == [Envelope.PET_PAGE, Envelope.DESK_WORKFLOW]
    assert audit_log[0]["event_type"] == "envelope.transitioned"
    assert audit_log[0]["previous_envelope"] == "pet_page"
    assert audit_log[0]["new_envelope"] == "desk_workflow"
    assert audit_log[0]["reason"] == "write_action_requested"
    assert result["messages"][0]["content"] == "ok"


@pytest.mark.asyncio
async def test_single_transition_per_turn():
    """A second denial after escalation must NOT trigger a second escalation."""
    graph = _FakeGraph(denied_envelopes=[Envelope.PET_PAGE, Envelope.DESK_WORKFLOW])
    audit_log: list[dict] = []

    async def audit(event):
        audit_log.append(event)

    with pytest.raises(CapabilityDeniedError):
        await run_with_escalation(
            graph,
            state={"messages": []},
            envelope=Envelope.PET_PAGE,
            record_audit=audit,
        )
    # Two invocations, one transition logged.
    assert len(graph.invocations) == 2
    assert len(audit_log) == 1
```

Run: `uv run pytest tests/test_escalation_engine.py -v`
Expected: FAIL with ImportError.

- [ ] **5.2: Implement `escalation.py`**

```python
# backend/app/services/deep_agent/escalation.py
"""Escalation engine.

Wraps a LangGraph agent invocation. If the model picks a tool that the
current envelope blocks (the gate raises ``CapabilityDeniedError``), we
consult the transition table and re-invoke the same agent once under the
widened envelope. Only one transition per turn — a second denial bubbles
up to the caller, which surfaces a structured error to the user.

Audit events are persisted via the ``record_audit`` callable the caller
provides; we do not import the database layer here so this module stays
unit-testable.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from .capability_gate import CapabilityDeniedError
from .envelopes import Envelope, EscalationReason, reason_for_denied_group, transition


AuditCallback = Callable[[dict], Awaitable[None]]


async def run_with_escalation(
    graph: Any,
    *,
    state: dict,
    envelope: Envelope,
    record_audit: AuditCallback,
    config_extras: dict | None = None,
) -> dict:
    """Run `graph.ainvoke(state, config)` with one-shot escalation on denial.

    Parameters:
        graph: a LangGraph agent (must have async ``ainvoke(state, config)``).
        state: the LangGraph state dict (messages + whatever else).
        envelope: the initial envelope for this turn.
        record_audit: async callback invoked once when a transition happens.
        config_extras: optional dict merged into ``configurable`` (e.g.,
            thread_id, run_id) so the gate has access to them.
    """
    config = {
        "configurable": {"envelope": envelope.value, **(config_extras or {})},
    }
    try:
        return await graph.ainvoke(state, config=config)
    except CapabilityDeniedError as denial:
        reason = reason_for_denied_group(denial.envelope, denial.group)
        if reason is None:
            raise
        new_envelope = transition(denial.envelope, reason)
        if new_envelope is None:
            raise
        await record_audit({
            "event_type": "envelope.transitioned",
            "previous_envelope": denial.envelope.value,
            "new_envelope": new_envelope.value,
            "reason": reason.value,
            "denied_tool": denial.tool_name,
            "denied_group": denial.group.value,
        })
        # One transition per turn — do NOT wrap this re-invoke in another
        # try/except. A second denial bubbles up.
        retry_config = {
            "configurable": {"envelope": new_envelope.value, **(config_extras or {})},
        }
        return await graph.ainvoke(state, config=retry_config)


__all__ = ["run_with_escalation"]
```

- [ ] **5.3: Run the escalation tests**

```bash
uv run pytest tests/test_escalation_engine.py -v
```

Expected: all pass.

- [ ] **5.4: Wire the escalation engine into `AgentService`**

In `backend/app/services/agents.py`, locate the main agent invocation in `stream_and_persist`. Replace the direct `self._agent.ainvoke(...)` call (around the location surveyed) with `run_with_escalation`:

```python
from .deep_agent.escalation import run_with_escalation
from .audit import record_audit as _record_audit

# Inside stream_and_persist, after building config:

async def _audit_envelope_transition(payload: dict) -> None:
    with self._session_factory() as session:
        _record_audit(
            session,
            event_type=payload["event_type"],
            actor="runtime",
            subject_type="thread",
            subject_id=thread.id,
            payload=payload,
        )
        session.commit()

result = await run_with_escalation(
    self._agent,
    state=state,
    envelope=resolved_envelope,
    record_audit=_audit_envelope_transition,
    config_extras={"thread_id": thread.id, "run_id": run_id},
)
```

The exact line numbers depend on the current structure of `stream_and_persist`; the survey identified the invocation point at `agents.py:647 / 713`.

**Streaming caveat:** the survey reports the endpoint uses `astream_events`, not `ainvoke`. If we replace `astream_events` with `ainvoke` we lose token-by-token streaming on the first turn. To preserve UX:

- **First attempt**: drive `astream_events` as today.
- **If** a `CapabilityDeniedError` surfaces from `astream_events` (it surfaces through the event stream as an error event with the exception payload), tear down the stream, emit a `event: envelope_transitioned` SSE event to the client, then start a fresh `astream_events` with the widened envelope.
- **One transition per turn**: a second denial in the second `astream_events` becomes a terminal error event to the client.

Concretely, refactor `stream_and_persist` so the streaming loop runs inside a helper, and wrap the helper call in the same try/except shape as `run_with_escalation`. This keeps the escalation logic in `escalation.py` (synchronous-style) while the streaming code lives in `agents.py`. The two share the transition table via `envelopes.transition`.

- [ ] **5.5: Persist envelope and transition into message meta**

When `_finalize_turn` writes the assistant message, include the **final** envelope (after any transition) in `assistant_msg.meta`:

```python
assistant_msg.meta = {
    **(assistant_msg.meta or {}),
    "envelope_final": resolved_final_envelope.value,
    "envelope_transitioned": resolved_final_envelope != initial_envelope,
}
```

- [ ] **5.6: Add an end-to-end fixture test**

```python
# tests/test_chat_endpoint_envelope.py (append)

def test_pet_page_request_escalates_when_model_calls_write_tool(client, monkeypatch):
    """A pet_page request that triggers a domain_write tool gets logged as transitioned."""
    # Stub the agent so the first invocation deterministically asks for create_portfolio_tool.
    # Stub create_portfolio_tool to actually run.
    # Verify:
    #   - response 200
    #   - assistant message meta contains envelope_final == "desk_workflow"
    #   - an envelope.transitioned audit event exists
    ...  # Implementation depends on the stub harness already used by other agent tests.
```

(The exact stubbing technique is consistent with existing patterns in `tests/test_agent_tools.py` — model fake + tool monkeypatch.)

- [ ] **5.7: Commit**

```bash
git commit -am "feat(runtime): escalation engine + envelope persistence (P2.5)"
```

---

### Task 6 (P2.6) — Frontend cutover: send envelope + render badge

**Files:**
- Modify: `frontend/src/hooks/useAgentChatController.ts` — include `envelope` in the request body
- Modify: `frontend/src/components/FloatingAgentMiniChat.tsx` — pass `envelope: "pet_page"`; render badge from latest assistant message meta
- Modify: `frontend/src/routes/AgentDesk.tsx` — pass `envelope: "desk_workflow"`

#### Steps

- [ ] **6.1: Add `envelope` to the request body**

In `frontend/src/hooks/useAgentChatController.ts`, locate the `sendMessage` POST body (around line 264 per the survey):

```typescript
// Add envelope to the controller's sendMessage signature.
async sendMessage({
  content,
  envelope = "desk_workflow",   // default for callers that don't pass one
  ...rest
}: SendMessageArgs): Promise<void> {
  const body = {
    content,
    character: rest.character,
    model: rest.model,
    yolo_mode: rest.yoloMode,
    page_context: rest.pageContext,
    context_usage: rest.contextUsage,
    accounting_date: rest.accountingDate,
    envelope,  // <-- new
  };
  // ... rest unchanged ...
}
```

And surface the assistant message's `meta.envelope_final` on the message object so the badge can read it.

- [ ] **6.2: FloatingAgentMiniChat sends `pet_page` and renders the badge**

```tsx
// frontend/src/components/FloatingAgentMiniChat.tsx
import { envelopeBadgeLabel } from "../lib/envelope";

// When calling sendMessage:
await controller.sendMessage({
  content,
  envelope: "pet_page",
  pageContext,
  // ...
});

// In the chat header, derive the badge:
const latestAssistant = messages.findLast(m => m.role === "assistant");
const badgeEnvelope = (latestAssistant?.meta?.envelope_final as Envelope | undefined) ?? "pet_page";

<span className="envelope-badge">{envelopeBadgeLabel(badgeEnvelope)}</span>
```

- [ ] **6.3: AgentDesk sends `desk_workflow`**

```tsx
// frontend/src/routes/AgentDesk.tsx
await controller.sendMessage({
  content,
  envelope: "desk_workflow",
  // (no pageContext on desk by default)
  // ...
});
```

- [ ] **6.4: Manual smoke test**

```bash
cd backend && uv run uvicorn app.main:app --port 8000 &
cd frontend && pnpm dev
```

Open the app, navigate to Positions, ask "How many positions do we have?" via FloatingAgentMiniChat — verify the badge stays "Pet". Then ask "delete portfolio 1" — verify the badge changes to "Desk" and the agent asks for confirmation.

- [ ] **6.5: Commit**

```bash
git commit -am "feat(frontend): send envelope from pet/desk; render badge (P2.6)"
```

---

### Task 7 (P2.7) — Cost-preview escape hatch

**Files:**
- Modify: `backend/app/services/deep_agent/capability_gate.py` — add `cost_estimate` parameter
- Modify: `backend/app/tools/risk.py` — `run_risk_tool` declares its estimator
- Modify: `backend/app/tools/pricing.py` — `price_positions_tool` declares its estimator
- Create: `tests/test_cost_preview.py`

#### Steps

- [ ] **7.1: Extend the gate to support cost estimation**

```python
# backend/app/services/deep_agent/capability_gate.py (extend)

LONG_RUNNING_SECONDS = 30.0


class CostPreviewRequiredError(Exception):
    """Raised when a tool's estimated cost exceeds the long-running threshold."""

    def __init__(self, *, tool_name: str, estimated_seconds: float) -> None:
        super().__init__(
            f"tool '{tool_name}' estimated at {estimated_seconds:.1f}s exceeds "
            f"{LONG_RUNNING_SECONDS:.0f}s threshold; explicit confirmation required"
        )
        self.tool_name = tool_name
        self.estimated_seconds = estimated_seconds


def capability_gated(
    *,
    group: ToolGroup,
    cost_estimator: Callable[[dict], float] | None = None,
) -> Callable[[BaseTool], BaseTool]:
    """
    Extended: an optional ``cost_estimator(tool_input) -> seconds`` raises
    ``CostPreviewRequiredError`` when its return exceeds LONG_RUNNING_SECONDS,
    UNLESS the request config has ``confirmed_cost_preview=True``.
    """

    def wrap(t: BaseTool) -> BaseTool:
        # ... existing gate setup ...
        
        original_invoke = t.invoke

        @wraps(original_invoke)
        def gated_invoke(input, config=None, **kwargs):
            envelope = _envelope_from_config(config)
            if not tool_allowed(envelope, group):
                raise CapabilityDeniedError(envelope=envelope, group=group, tool_name=t.name)
            if cost_estimator is not None:
                configurable = (config or {}).get("configurable") or {}
                confirmed = bool(configurable.get("confirmed_cost_preview"))
                if not confirmed:
                    seconds = float(cost_estimator(input) or 0.0)
                    if seconds > LONG_RUNNING_SECONDS:
                        raise CostPreviewRequiredError(
                            tool_name=t.name, estimated_seconds=seconds
                        )
            return original_invoke(input, config=config, **kwargs)

        # (async path same shape)
        # ...
```

- [ ] **7.2: Wire the estimator into `run_risk_tool`**

```python
# backend/app/tools/risk.py

from app.services.domains import risk as risk_svc

def _estimate_run_risk_cost(tool_input: dict) -> float:
    portfolio_id = tool_input.get("portfolio_id")
    if portfolio_id is None:
        return 0.0
    try:
        return float(risk_svc.estimate_run_seconds(portfolio_id=int(portfolio_id)))
    except Exception:
        return 0.0


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_estimate_run_risk_cost)
@tool("run_risk", args_schema=RunRiskInput)
def run_risk_tool(...):
    ...
```

Do the same for `price_positions_tool` using `pricing_svc.estimate_price_seconds`.

- [ ] **7.3: Surface the cost preview to the user**

In the escalation engine (or a new sibling helper), catch `CostPreviewRequiredError` and emit an SSE event to the client requesting confirmation. The frontend renders an inline confirm button; clicking it re-submits the same request with `meta.confirm_cost_preview = true`, and the runtime threads that into `configurable.confirmed_cost_preview`.

The shape of this UI affordance can reuse the existing `propose_reply_options` machinery (the survey confirms `AgentActionProposal` already exists at `frontend/src/types.ts:110-124`). A cost-preview proposal becomes one more action proposal of type `"confirm_cost"`.

- [ ] **7.4: Write tests**

```python
# tests/test_cost_preview.py
import pytest
from langchain_core.tools import tool

from app.services.deep_agent.capability_gate import (
    CostPreviewRequiredError,
    capability_gated,
)
from app.services.deep_agent.envelopes import ToolGroup


def _estimator(_tool_input: dict) -> float:
    return 45.0  # always over the 30s threshold


@capability_gated(group=ToolGroup.DOMAIN_WRITE, cost_estimator=_estimator)
@tool("slow_thing")
def slow_thing_tool(portfolio_id: int) -> dict:
    return {"ran": True}


def test_cost_preview_blocks_unconfirmed_request():
    with pytest.raises(CostPreviewRequiredError) as exc:
        slow_thing_tool.invoke(
            {"portfolio_id": 1},
            config={"configurable": {"envelope": "desk_workflow"}},
        )
    assert exc.value.estimated_seconds == 45.0


def test_cost_preview_lets_through_when_confirmed():
    result = slow_thing_tool.invoke(
        {"portfolio_id": 1},
        config={"configurable": {
            "envelope": "desk_workflow",
            "confirmed_cost_preview": True,
        }},
    )
    assert result["ran"] is True
```

Run: `uv run pytest tests/test_cost_preview.py -v`
Expected: all pass.

- [ ] **7.5: Commit**

```bash
git commit -am "feat(runtime): cost-preview escape hatch for >30s actions (P2.7)"
```

---

## Final gate

After all 7 PRs land, verify the four fixture prompts from the spec:

```
1. Positions page: "How many positions do we have?"
   Expected envelope: pet_page (no transition)
   Expected tools: none (answered from snapshot)
   Verify: assistant_msg.meta["envelope_final"] == "pet_page"

2. Risk page: "rerun risk"
   Expected envelope: pet_page (or pet_diagnostic for the explanation that follows)
   Expected tool: run_risk via the page action
   Verify: a TaskRun row was created with kind=risk_run

3. Risk page follow-up: "what's the delta of position 21?" then "why is it so big?"
   Expected envelope sequence: pet_page -> pet_diagnostic (on the "why" turn)
   Expected: an envelope.transitioned audit event with reason=diagnostic_followup

4. Try-Solve page: "price a Snowball product with 000852.SH, 3Y, KO 103%, KI 75%"
   Expected envelope: pet_page (then desk_workflow when create_request_queue_item runs)
   Expected: a request-queue item is created via the page action
```

Run the agent regression suite:

```bash
uv run pytest tests/ \
  --deselect tests/test_quant_services.py::test_solve_rfq_returns_quote_payload \
  --deselect tests/test_api.py::test_client_rfq_form_and_approval \
  --deselect tests/test_api.py::test_failed_rfq_pricing_cannot_be_approved \
  --deselect tests/test_api.py::test_rfq_release_accept_and_booking_creates_position \
  --deselect tests/test_api.py::test_repair_legacy_rfq_booked_position_applies_executable_terms \
  --deselect tests/test_api.py::test_position_and_market_upload_then_batch_price \
  --deselect tests/test_position_import_pricing.py \
  --deselect tests/test_risk_engine.py \
  --deselect tests/test_try_solve.py
```

Expected: all pass. The 14 pre-existing scipy/QuantArk failures stay deselected.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Streaming + escalation reset wastes tokens.** First attempt streams partial output; on denial we tear down and restart under widened envelope. | The reset only fires when the model picks a denied tool — which means the first attempt was about to be useless anyway. The wasted tokens are bounded by the model's response-up-to-first-tool-call. |
| **Per-tool `@capability_gated` decorator must be applied to ALL 38 domain tools.** Missing one means a write tool is silently allowed under pet_page. | P2.4 step 4.4 enumerates the assignments. Add a CI test in P2.4 step 4.5b that asserts every tool in `QUANT_AGENT_TOOLS` has `__capability_group__` set. |
| **Frontend pages don't yet expose `loaded_context.completeness` accurately.** A buggy "complete" claim makes the pet answer from a partial snapshot. | P2.3 only migrates Positions / Risk / Try-Solve. Unmigrated pages produce `loaded_context = None`, which the agent treats as "always escalate for count-style questions." |
| **The async-agents `task_brief` rename touches a public-ish surface.** | The rename is internal to `services/async_agents/`. No external API or DB column uses the name. Step 1.4 verifies. |
| **Cost estimator returns 0 on error → cost preview misses.** | The estimator is wrapped in `try/except` returning 0; missed estimates manifest as "user asks for a long task and waits." That's the existing behavior, not a regression. The reverse failure (over-triggering the preview) is the one we'd actually have to fix. |

---

## CI test additions (across PRs)

- `tests/test_envelopes.py` — exhaustive transition + capability table coverage (P2.1)
- `tests/test_capability_gate.py` — gate denies/allows correctly (P2.4)
- `tests/test_capability_assignments.py` — every tool in `QUANT_AGENT_TOOLS` has `__capability_group__` (P2.4 step 4.5b)
- `tests/test_page_context_schema.py` — typed schema accepts old + new shape (P2.2)
- `tests/test_chat_endpoint_envelope.py` — endpoint accepts `envelope` and defaults correctly (P2.4)
- `tests/test_escalation_engine.py` — one transition per turn; correct reason resolution (P2.5)
- `tests/test_cost_preview.py` — cost gate fires above threshold; bypassed when confirmed (P2.7)

---

## PR ordering and dependencies

```
P2.1 (envelopes, async rename)
    \
     +--> P2.4 (runtime + gate) --> P2.5 (escalation) --> P2.6 (frontend cutover)
    /
P2.2 (page context schema)
    \
     +--> P2.3 (frontend populators)

P2.4 + P2.5 --> P2.7 (cost preview)
```

P2.1, P2.2, P2.3 are independent and can land in parallel. P2.4 needs P2.1 + P2.2. P2.5 needs P2.4. P2.6 needs P2.5 + P2.3. P2.7 needs P2.5.

Earliest-mergeable order: **P2.1 → P2.2 → P2.3 → P2.4 → P2.5 → P2.6 → P2.7**.

---

## Out of scope (deferred to Phase 3 or a follow-up)

- Migrating Portfolios, PricingParameters, MarketData, RfqApproval, Reports, Tasks pages to the new `loaded_context` + `actions` shape.
- Routing-by-skill (handled in Phase 3).
- Removing the legacy `chips` and `path` fields from `AgentPageContext`.
- A backend `GET /api/page_actions/<route>` catalog endpoint.
- Build-time validation of frontend `PageAction.backend_endpoint` against real FastAPI routes.

These are explicit non-goals for Phase 2; capture them as Phase 3 tasks or open issues during P2.6 testing.
