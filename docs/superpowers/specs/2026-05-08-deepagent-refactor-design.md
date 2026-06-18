# DeepAgent Refactor — Design Spec

**Date:** 2026-05-08
**Owner:** fuxinyao
**Status:** Approved for planning
**Related:** `backend/app/services/agents.py`, `backend/app/services/langchain_tools.py`, `backend/app/main.py`, `frontend/src/components/ActionProposal.tsx`, `frontend/src/routes/AgentDesk.live.tsx`, `frontend/src/types.ts`

---

## 1. Problem statement

The Agent Desk advertises itself as "LangChain DeepAgent–style" but in practice runs three loosely-coupled systems side by side:

1. A real `deepagents.create_deep_agent()` graph that, when configured, only handles freeform Q&A and never drives any persisted workflow.
2. A regex-based `_propose_actions` layer that proposes the five persisted-action types to the frontend without any LLM involvement.
3. A deterministic keyword-matching fallback (`_deterministic_agent_step`, `_persona_response`, `_try_execute_tools`, `_format_tool_response`) that silently replaces the LLM whenever the LLM key is missing or any LLM exception is raised.

Specific issues this refactor addresses:

- Personas (trader / risk_manager / high_board) are string-prefix routing on the deterministic path and a bare prompt prefix on the LLM path; they are not real reasoning subagents.
- The LLM is decorative for the action workflow — every persisted action goes through the regex proposer, not the agent.
- `_execute_confirmed_agent_action` is a ~250-line if/elif tree in `main.py` that duplicates each tool's persistence logic.
- `InMemorySaver` makes thread state worker-local and lost on restart.
- `AnthropicPromptCachingMiddleware` silently no-ops because the model is `ChatOpenAI` against ZenMux's OpenAI endpoint; long system prompts and tool schemas are re-billed every turn.
- The deterministic fallback hides every LLM error.

The goal is a real DeepAgent with real subagents per persona, where the LLM drives the workflow and the persisted-action confirmation flow is expressed natively via Human-in-the-Loop (HITL) interrupts.

## 2. Locked decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Persisted-action ownership | LLM tool calls + HITL `interrupt_on` |
| Persona routing | Top-level orchestrator can chain multiple persona subagents in one user turn |
| Persona shape | Same full tool set, different system prompts |
| Fallback policy | Strict "agent disabled" stub when no key; runtime errors fail loud |
| Model backend | Pluggable: Anthropic via ZenMux's `/api/anthropic` (default, caching works) or OpenAI via ZenMux's `/api/v1` |
| HITL surface | Generic `{tool_name, label, summary, payload}` shape; gate every state-mutating tool |
| Architectural approach | A — Canonical deepagents (orchestrator + 3 declarative SubAgents) |
| Checkpointer | Persistent SqliteSaver (forced by HITL state-across-requests requirement) |

## 3. Architecture

### 3.1 Topology

```
┌──────────────────────────────────────────────────────────────────────┐
│  AgentService (backend/app/services/agents.py — façade only)         │
│   ┌──────────────────────────────────────────────────────────────┐  │
│   │  Orchestrator deepagent  (build_orchestrator)                │  │
│   │   tools (built-in only): write_todos, ls/read_file/...,      │  │
│   │                          task → dispatches to subagents      │  │
│   │   system_prompt: ORCHESTRATOR_PROMPT                         │  │
│   │   subagents: [trader, risk_manager, high_board]              │  │
│   │   interrupt_on: { every state-mutating tool: True }          │  │
│   │   checkpointer: SqliteSaver(./agent_checkpoints.sqlite)      │  │
│   │   model: build_agent_model(settings)                         │  │
│   └──────────────────────────────────────────────────────────────┘  │
│                            │                                         │
│           ┌────────────────┼────────────────┐                        │
│           ▼                ▼                ▼                        │
│      ┌────────┐      ┌────────────┐   ┌───────────┐                  │
│      │ trader │      │risk_manager│   │high_board │                  │
│      │ (full  │      │ (full tool │   │(full tool │                  │
│      │ tools) │      │   set)     │   │  set)     │                  │
│      │ TRADER_│      │RISK_PROMPT │   │BOARD_     │                  │
│      │ PROMPT │      │            │   │ PROMPT    │                  │
│      └────────┘      └────────────┘   └───────────┘                  │
│           ▲                ▲                ▲                        │
│           └────────────── tools ──────────────┘                      │
│                            │                                         │
│              backend/app/services/langchain_tools.py                 │
│              (10 tools: 6 read-only + 4 NEW HITL tools +             │
│               3 existing import/price tools, all HITL-gated)         │
└──────────────────────────────────────────────────────────────────────┘
```

The orchestrator has **no domain tools of its own**. Its only job is plan + delegate (via the auto-injected `task` tool) + synthesize. All quant tools live on the personas. HITL is declared once at the orchestrator level and inherited by every subagent (deepagents inheritance rule in `subagents.py`).

### 3.2 Module layout (new + modified)

```
backend/app/services/
├── agents.py                    # SHRUNK to AgentService façade only
├── langchain_tools.py           # MODIFIED: add 4 new persisted tools
└── deep_agent/                  # NEW PACKAGE
    ├── __init__.py              # exports build_deep_agent_app
    ├── orchestrator.py          # build_orchestrator(...)
    ├── personas.py              # trader_spec, risk_spec, board_spec + prompts
    ├── model_factory.py         # build_agent_model(settings) -> BaseChatModel | None
    ├── checkpointer.py          # build_checkpointer(settings) -> Checkpointer
    ├── hitl.py                  # interrupt_on_config, pending_actions_from_state, resume_command
    └── prompts/
        ├── orchestrator.md
        ├── trader.md
        ├── risk_manager.md
        └── high_board.md

backend/app/main.py              # confirm_agent_action rewired to Command(resume=...)
                                 # _execute_confirmed_agent_action DELETED
                                 # NEW dismiss_agent_action endpoint
backend/app/schemas.py           # AgentActionProposal: type Literal -> tool_name str
backend/app/config.py            # agent_provider, agent_model_anthropic,
                                 # agent_model_openai, agent_checkpoint_db_path

frontend/src/types.ts            # AgentActionProposal: type -> tool_name + new optional fields
frontend/src/components/ActionProposal.tsx  # generic card renderer
frontend/src/routes/AgentDesk.live.tsx      # add dismiss POST
```

### 3.3 What goes away

- `_route_character`, `_persona_response`, `_deterministic_agent_step`, `_try_execute_tools`, `_format_tool_response`, `_propose_actions` (regex layer in `agents.py`).
- `_execute_confirmed_agent_action` and helpers in `main.py` (~250 lines of type-dispatch).
- `_format_response` (orchestrator composes its own final reply).
- `InMemorySaver` import.
- `PERSISTED_ACTION_TOOL_NAMES` block list — every persisted tool is now bindable but HITL-gated.

### 3.4 What stays

- `langchain_tools.py` tool definitions (extended with 4 new tools).
- `_lightweight_portfolio_summary`, `_context_assets`, `_extract_pricing_overrides` (still useful for context injection).
- `respond()` HTTP path and `stream_response()` SSE shape (rewired internals).
- `AgentMessage` / `AgentThread` DB models.
- `confirm_agent_action` endpoint URL and the frontend's confirm POST.

## 4. Components

### 4.1 Orchestrator (`deep_agent/orchestrator.py`)

```python
def build_orchestrator(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Checkpointer,
    interrupt_on: dict[str, bool],
) -> CompiledStateGraph:
    return create_deep_agent(
        model=model,
        tools=[],                       # orchestrator has no domain tools
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=[
            trader_spec(model, tools),
            risk_spec(model, tools),
            board_spec(model, tools),
        ],
        interrupt_on=interrupt_on,      # inherited by all 3 subagents
        checkpointer=checkpointer,
        permissions=[FilesystemPermission(operations=["read", "write"], paths=["/", "/**"], mode="deny")],
        name="otc_desk_orchestrator",
    )
```

**Orchestrator system prompt outline** (`prompts/orchestrator.md`):
- Role: route + chain + synthesize. Never call domain tools directly.
- Routing heuristic: pricing/RFQ/quotes → `trader`; risk/VaR/stress/hedge → `risk_manager`; reports/release/board questions → `high_board`.
- For compound queries, call `task(...)` multiple times in sequence; pass each subagent only the slice of intent it needs.
- Synthesize final answer; cite which persona produced which fact.
- Forbidden: calling persisted tools directly, claiming work was done that requires confirmation.
- **Batch-size-1 rule for HITL** (also enforced in each persona prompt): never request more than one persisted/HITL-gated tool call in a single assistant turn. If multiple persisted operations are needed, request the first, wait for confirmation, then request the next. This keeps the resume path deterministic (one user click → one decision).

### 4.2 Personas (`deep_agent/personas.py`)

Three `SubAgent` TypedDicts, each:

```python
def trader_spec(model, tools) -> SubAgent:
    return {
        "name": "trader",
        "description": "Quotes, pricing, RFQ solving, market snapshots. Uses price_positions for batch repricing.",
        "system_prompt": load_prompt("trader.md"),
        "tools": list(tools),       # full set, gated by HITL
        # model & middleware inherit from parent
    }
```

| Persona | Decision lens | Typical calls (not enforced) |
|---|---|---|
| `trader` | Quote readiness, pricing accuracy | `price_product`, `solve_rfq`, `get_positions`, `fetch_market_snapshot`, `price_positions`* |
| `risk_manager` | Limits, exposure, hedge feasibility | `calculate_risk`, `recommend_hedge`, `get_positions`, `run_risk`* |
| `high_board` | Release/approve, reporting | `run_report_batch`, `create_report`*, `approve_rfq`*, `reject_rfq`* |

`*` = HITL-gated. All three personas hold the full tool list per the locked decision; the table shows expected usage, not enforcement.

### 4.3 Tools (`langchain_tools.py`)

**Existing — unchanged definitions, removed from block list:**
- `price_positions`, `import_otc_positions`, `import_position_market_inputs` become bindable but HITL-gated.

**New tools added (currently exist only as `AgentActionProposal.type` strings in regex):**

```python
@tool("run_risk", args_schema=RunRiskInput)
def run_risk_tool(portfolio_id: int, method: str = "summary") -> dict: ...

@tool("create_report", args_schema=CreateReportInput)
def create_report_tool(portfolio_id: int, title: str, report_type: str = "portfolio") -> dict: ...

@tool("approve_rfq", args_schema=ApproveRfqInput)
def approve_rfq_tool(rfq_id: int, approver: str = "agent_confirmed", comment: str | None = None) -> dict: ...

@tool("reject_rfq", args_schema=RejectRfqInput)
def reject_rfq_tool(rfq_id: int, approver: str = "agent_confirmed", comment: str | None = None) -> dict: ...
```

Each new tool body:
- Opens `database.SessionLocal()` itself (mirrors existing `price_positions_tool`).
- Performs persistence + audit logic that lives in `main.py:_execute_confirmed_agent_action` today.
- Returns a dict the LLM summarizes for the user.

After this, `_execute_confirmed_agent_action` is fully replaced.

### 4.4 Model factory (`deep_agent/model_factory.py`)

```python
def build_agent_model(settings: Settings) -> BaseChatModel | None:
    if not settings.zenmux_api_key:
        return None                                # triggers "agent disabled" stub

    if settings.agent_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model=settings.agent_model_anthropic,  # default "anthropic/claude-sonnet-4-6"
            api_key=settings.zenmux_api_key,
            base_url="https://zenmux.ai/api/anthropic",
            default_headers={"anthropic-version": "2023-06-01"},
        )
    if settings.agent_provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=settings.agent_model_openai,     # default "openai/gpt-5.4-mini"
            api_key=settings.zenmux_api_key,
            base_url=settings.zenmux_base_url,     # "https://zenmux.ai/api/v1"
        )
    raise ValueError(f"unknown agent_provider: {settings.agent_provider}")
```

### 4.5 Checkpointer (`deep_agent/checkpointer.py`)

```python
def build_checkpointer(settings: Settings) -> Checkpointer:
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver
    # SqliteSaver.from_conn_string is a context manager intended for short-lived use.
    # For an app-lifetime saver shared across requests we construct from a long-lived
    # connection with check_same_thread=False so FastAPI's threadpool workers can use it.
    conn = sqlite3.connect(settings.agent_checkpoint_db_path, check_same_thread=False)
    return SqliteSaver(conn)
```

Notes:

- Separate SQLite file from the app DB so agent state can be reset without touching domain data; dev/test runs can pass `":memory:"`.
- For async streaming (`astream_events`) we add an `AsyncSqliteSaver` variant with `aiosqlite`; the model factory chooses the right saver based on the call site (sync `respond()` vs async streaming).
- Postgres deployments can swap in `PostgresSaver` via the same factory.

### 4.6 HITL helpers (`deep_agent/hitl.py`)

The langchain HITL contract (verified against `langchain/agents/middleware/human_in_the_loop.py`):

- `DecisionType = Literal["approve", "edit", "reject"]`.
- An interrupt's payload is a `HITLRequest`: `{"action_requests": list[ActionRequest], "review_configs": list[ReviewConfig]}`. `ActionRequest` is `{"name": str, "args": dict, "description": str?}`.
- The resume payload is a `HITLResponse`: `{"decisions": list[Decision]}`, where decisions are *positional* — index `i` corresponds to `action_requests[i]`. Each `Decision` is `{"type": "approve"} | {"type": "edit", "edited_action": Action} | {"type": "reject", "message": str?}`.

```python
INTERRUPT_TOOL_NAMES: tuple[str, ...] = (
    "price_positions", "run_risk", "create_report",
    "approve_rfq", "reject_rfq",
    "import_otc_positions", "import_position_market_inputs",
)

def interrupt_on_config() -> dict[str, InterruptOnConfig]:
    """Restrict allowed decisions to approve/reject for v1 (no `edit`)."""
    return {
        name: {"allowed_decisions": ["approve", "reject"]}
        for name in INTERRUPT_TOOL_NAMES
    }

def pending_actions_from_state(state: dict, interrupt_id: str) -> list[AgentActionProposal]:
    """Project __interrupt__ entries from compiled-graph state to frontend shape.

    The `id` field on each AgentActionProposal is `f"{interrupt_id}:{i}"` where i is
    the position in `action_requests`. This positional id is what the resume path
    uses to assemble the ordered `decisions` list.
    """

def build_resume_command(
    interrupt_id: str,
    decisions_by_index: dict[int, Decision],
) -> Command:
    """Build Command(resume={"decisions": [...]}) preserving action_requests order.

    Caller passes a mapping from index → Decision; this fills any unspecified
    indices with a default reject (so the graph never silently approves on
    partial input).
    """
```

v1 exposes only **confirm** (`{"type": "approve"}`) and **dismiss** (`{"type": "reject", "message": "user dismissed"}`) at the API edge. `edit` is a future hook and is excluded from `allowed_decisions`.

## 5. Data flow

### 5.1 Single-turn happy path (no HITL trigger)

```
POST /api/chat/threads/{tid}/messages
    body: {content, character?, page_context?}

AgentService.respond(thread, content, page_context):
  ├─ persist user AgentMessage
  ├─ context = _build_context(session, page_context)
  ├─ assets  = _context_assets(page_context)
  ├─ prompt  = _orchestrator_user_prompt(content, character_hint, context)
  ├─ result  = deep_agent.invoke(
  │              {"messages": [HumanMessage(prompt)]},
  │              config={"configurable": {"thread_id": str(thread.id)}})
  ├─ if "__interrupt__" in result: → see 5.2 (pause path)
  ├─ final_text = _extract_final_ai_text(result)
  ├─ persist assistant AgentMessage with meta={
  │     agent_graph: "deepagents",
  │     agent_phase: "completed",
  │     pending_actions: [],
  │     personas_invoked: [...],
  │     tool_calls: [...],
  │     assets: [...],
  │   }
  └─ return assistant message
```

`character_hint` (the existing request `character` field) becomes a soft hint to the orchestrator's prompt, not a hard route.

### 5.2 First pause path (HITL interrupts)

LangGraph surfaces interrupts as a list. Each `Interrupt` carries a single `HITLRequest` whose `action_requests` is itself a list (the model can request multiple gated tool calls in one turn). We flatten all requests into one `pending_actions` list, encoding the position so resume can reassemble the ordered `decisions`:

```python
result = deep_agent.invoke(...)

if interrupts := result.get("__interrupt__"):
    pending: list[AgentActionProposal] = []
    for intr in interrupts:
        hitl_req = intr.value                              # HITLRequest
        for i, action_req in enumerate(hitl_req["action_requests"]):
            pending.append(AgentActionProposal(
                id=f"{intr.id}:{i}",                       # composite id for ordered resume
                tool_name=action_req["name"],
                label=_label_for(action_req["name"]),
                summary=action_req.get("description") or _summary_for(action_req),
                payload=action_req["args"],
                requires_confirmation=True,
                status="pending",
                persona=_persona_from_state(result),
                risk_level=_risk_level_for(action_req["name"]),
            ))
    persist AgentMessage(
        role="assistant",
        content=_partial_text_so_far(result),
        meta={
            agent_graph: "deepagents",
            agent_phase: "awaiting_confirmation",
            pending_actions: [pending...],
            interrupt_ids: [intr.id for intr in interrupts],   # needed at resume time
            assets: [...],
        },
    )
    return that message
```

`interrupt_ids` is persisted in `meta` so the resume endpoint can rebuild `Command(resume=...)` even after the request that handles confirmation is a separate process from the one that produced the pause.

### 5.3 Resume path (confirm or dismiss)

```
POST /api/chat/threads/{tid}/messages/{mid}/actions/{aid}/confirm
POST /api/chat/threads/{tid}/messages/{mid}/actions/{aid}/dismiss   (NEW)
```

The resume payload is a `HITLResponse` whose `decisions` list is positional. Decisions on previously-resolved actions in the same source message must still be replayed — but in practice, langchain HITL emits **one Interrupt per turn-batch** of action_requests, all of which must be answered together. So a single confirm/dismiss endpoint call typically resumes with a `decisions` list covering only the actions the user has explicitly resolved; any sibling actions in the same batch that the UI hasn't resolved yet block the resume.

For v1 we choose the simplest possible UX: **action batches of size 1**. That is, we configure the agent so the orchestrator and personas only request one HITL action at a time. (Most queries already trigger one persisted call at a time; the orchestrator system prompt reinforces this — see 4.1.) With batch size 1, each confirm/dismiss endpoint hit produces exactly one `decisions` element.

```python
def _resume_action(thread_id, message_id, action_id, decision: Literal["approve", "reject"]):
    source_message = lookup AgentMessage(message_id)
    pending = source_message.meta["pending_actions"]
    action  = find action by composite id (interrupt_id:index)
    require: action.status == "pending"

    decisions: list[Decision] = [
        {"type": "approve"} if decision == "approve"
        else {"type": "reject", "message": "User dismissed the action."}
    ]
    cmd = Command(resume={"decisions": decisions})
    result = agent_service.deep_agent.invoke(
        cmd, config={"configurable": {"thread_id": str(thread_id)}}
    )

    if interrupts := result.get("__interrupt__"):
        return persist_pause(result, parent=source_message)   # multi-pause; see 5.4

    final_text = _extract_final_ai_text(result)
    final_msg  = persist AgentMessage(
        role="assistant",
        content=final_text,
        meta={
            agent_graph: "deepagents",
            agent_phase: "completed",
            pending_actions: [],
            confirmed_action: action if decision == "approve" else None,
            dismissed_action: action if decision == "reject" else None,
            tool_results: [...],
            assets: [...],
        },
    )
    # Patch source_message.meta.pending_actions[i].status = "confirmed" | "dismissed"
    record_audit(
        event_type="agent.action.confirmed" if decision == "approve" else "agent.action.dismissed",
        payload={"action_id": action_id, "tool_name": action["tool_name"], "decision": decision},
    )
    return final_msg
```

Tool-level audits (e.g. `risk.run`, `rfq.approved`) happen **inside** the tool body when it executes after resume.

**If a batch of size > 1 ever appears** (a model multi-tool-calls in a single turn including more than one gated tool), we surface all entries in one assistant message but mark them as a single interrupt group. The UI must collect resolutions for all items in the batch before submitting; v1 implements this by disabling per-item confirm buttons until every batch sibling has a pending decision, then a single "Submit decisions" call sends the full ordered `decisions` list. This is documented as a follow-up once v1 demonstrates the batch-size-1 case is the dominant path.

### 5.4 Multi-pause turns

The orchestrator may chain: trader prices → risk runs → board approves. Each gated tool call is its own pause. Conversation log:

```
USER:       "Run risk for portfolio 7 and approve RFQ-42 if VaR is fine."
ASSISTANT:  "Calling risk_manager…" + pending_actions=[run_risk(portfolio_id=7)]
USER → confirm action #1
ASSISTANT:  "VaR within limit. Calling high_board…" + pending_actions=[approve_rfq(rfq_id=42)]
USER → confirm action #2
ASSISTANT:  "RFQ-42 approved. Risk run #N filed."  ← agent_phase: completed
```

Each ASSISTANT row is one `AgentMessage`. Older `pending_actions` are patched in place when their action is confirmed/dismissed. Frontend already iterates per-message — works unchanged.

### 5.5 Streaming events

`AgentService.stream_response` switches from a static label list to live LangGraph events:

```python
async for event in self.deep_agent.astream_events({"messages": [...]}, config={...}, version="v2"):
    if event["event"] == "on_tool_start":
        yield f"event: status\ndata: {event['name']} starting\n\n"
    elif event["event"] == "on_tool_end":
        yield f"event: status\ndata: {event['name']} done\n\n"
    elif event["event"] == "on_chat_model_stream":
        chunk = event["data"]["chunk"].content
        if chunk:
            yield f"data: {chunk}\n\n"
yield "event: done\ndata: [DONE]\n\n"
```

If the stream encounters a HITL interrupt, the SSE closes with `event: interrupt` carrying the pending_actions JSON.

### 5.6 Page context injection

`AgentPageContext` flows in unchanged. Two delivery channels:

1. **Lightweight summary into the orchestrator user prompt** (`_lightweight_portfolio_summary` + chips, packed as JSON in the HumanMessage).
2. **Assets attached to the assistant message meta** (`_context_assets`).

We do **not** inject page_context into subagent prompts directly; the orchestrator decides what slice each subagent sees when it calls `task(prompt=...)`.

## 6. Schema & API contract

### 6.1 `AgentActionProposal` (`backend/app/schemas.py`)

```python
# AFTER
class AgentActionProposal(BaseModel):
    id: str                              # = LangGraph interrupt_id == tool_call_id
    tool_name: str                       # generic — replaces closed Literal `type`
    label: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)   # tool args
    requires_confirmation: bool = True
    status: Literal["pending", "confirmed", "dismissed", "failed"] = "pending"
    persona: str | None = None           # which subagent emitted the call
    risk_level: Literal["read", "write", "irreversible"] | None = None
```

`type` is dropped on the wire. Read-side shim translates legacy `type → tool_name` for old thread history (no DB write-back).

### 6.2 `AgentMessageOut.meta` (no schema change; keys evolve)

| Key | Before | After |
|---|---|---|
| `agent_graph` | `"deepagents" \| "deterministic" \| "action_confirmation"` | `"deepagents" \| "disabled"` |
| `agent_backend` | `"deepagents" \| "deterministic"` | removed |
| `agent_phase` | — | `"awaiting_confirmation" \| "completed"` (NEW) |
| `routed_character` | persona keyword-routed | removed |
| `personas_invoked` | — | `["trader", "risk_manager"]` (NEW) |
| `tool_calls` | — | `[{name, args_summary, ok}]` (NEW) |
| `confirmed_action` | dict | unchanged |
| `dismissed_action` | — | dict (NEW) |
| `pending_actions` | typed proposals | generic proposals |
| `process_events` | static labels | real langgraph event labels |
| `assets`, `context_used` | unchanged | unchanged |
| `zenmux_configured` | bool | renamed `agent_enabled` |
| `tool_count` | int | removed |

### 6.3 New input schemas (`langchain_tools.py`)

```python
class RunRiskInput(BaseModel):
    portfolio_id: int
    method: Literal["summary", "stress"] = "summary"

class CreateReportInput(BaseModel):
    portfolio_id: int
    report_type: Literal["portfolio"] = "portfolio"
    title: str = "Agent Generated Desk Report"

class ApproveRfqInput(BaseModel):
    rfq_id: int
    approver: str = "agent_confirmed"
    comment: str | None = None

class RejectRfqInput(BaseModel):
    rfq_id: int
    approver: str = "agent_confirmed"
    comment: str | None = None
```

### 6.4 Settings (`backend/app/config.py`)

```python
agent_provider: Literal["anthropic", "openai"] = os.getenv("AGENT_PROVIDER", "anthropic")
agent_model_anthropic: str = os.getenv("AGENT_MODEL_ANTHROPIC", "anthropic/claude-sonnet-4-6")
agent_model_openai: str = os.getenv("AGENT_MODEL_OPENAI", os.getenv("OPEN_OTC_DEFAULT_MODEL", "openai/gpt-5.4-mini"))
agent_checkpoint_db_path: str = os.getenv("AGENT_CHECKPOINT_DB", "./agent_checkpoints.sqlite")
# default_model: kept as alias mapped to agent_model_openai for back-compat
```

### 6.5 HTTP endpoints (`backend/app/main.py`)

| Endpoint | URL | Status |
|---|---|---|
| Create thread | `POST /api/chat/threads` | unchanged |
| List threads | `GET /api/chat/threads` | unchanged |
| Send message | `POST /api/chat/threads/{tid}/messages/stream` | internals rewired; SSE shape unchanged |
| Confirm action | `POST /api/chat/threads/{tid}/messages/{mid}/actions/{aid}/confirm` | internals rewired to `Command(resume={"decisions": [{"type": "approve"}]})` |
| Dismiss action | `POST /api/chat/threads/{tid}/messages/{mid}/actions/{aid}/dismiss` | NEW; `Command(resume={"decisions": [{"type": "reject", "message": "..."}]})` |

`_execute_confirmed_agent_action` (~250 lines) is deleted entirely. Its behavior is replaced by tool bodies running after resume.

### 6.6 Frontend changes

**`frontend/src/types.ts`:**

```ts
export interface AgentActionProposal {
  id: string;
  tool_name: string;                                     // was `type`
  label: string;
  summary: string;
  payload?: Record<string, unknown>;
  requires_confirmation?: boolean;
  status?: 'pending' | 'confirmed' | 'dismissed' | 'failed';
  persona?: 'trader' | 'risk_manager' | 'high_board';
  risk_level?: 'read' | 'write' | 'irreversible';
}
```

**`ActionProposal.tsx`:** generic card showing `label`, `summary`, optional `persona` chip, and an "args" disclosure (collapsible JSON of `payload`). Per-tool icons keyed by `tool_name` are nice-to-have, not required.

**`AgentDesk.live.tsx`:** existing confirm POST stays; add dismiss POST to the new endpoint. After confirm/dismiss, the response may itself contain `meta.pending_actions` (multi-pause turns) — append to the thread.

### 6.7 DB / migrations

No new tables. `AgentMessage.meta` is JSON; meta-key changes do not require Alembic. The new `agent_checkpoints.sqlite` file is owned and managed by `SqliteSaver` outside Alembic.

### 6.8 Backwards compatibility

We are intentionally **not** preserving `AgentActionProposal.type` on the wire. Existing thread history rows in `agent_messages.meta.pending_actions` will have `type` but no `tool_name`. Mitigation:

- Frontend renderer falls back: `proposal.tool_name ?? proposal.type` for legacy rows.
- Read-side shim in `AgentMessageOut.from_orm` (or a `_normalize_pending_actions` helper) copies `type → tool_name` for legacy rows on read. No DB write-back; no backfill.

## 7. Error handling

### 7.1 Startup-time

| Condition | Behavior |
|---|---|
| `ZENMUX_API_KEY` missing | `build_agent_model` returns `None`. `AgentService.deep_agent` is `None`. App boots normally. |
| Invalid `agent_provider` | `ValueError` at app startup — fail loud (config bug). |
| Missing required tools in `QUANT_AGENT_TOOLS` | `select_deep_agent_tools` raises `RuntimeError` at import — kept. |
| Checkpoint DB path unwritable | Raise on first `respond()`, surface as 500 with a clear message. No silent degrade. |

### 7.2 "Agent disabled" stub

```python
def _disabled_response(self) -> str:
    return (
        "Agent unavailable — LLM is not configured. "
        "Set ZENMUX_API_KEY (and optionally AGENT_PROVIDER, AGENT_MODEL_*) to enable the desk agent."
    )
```

`respond()` returns a normal `AgentMessage` with `meta={agent_graph: "disabled", agent_enabled: false}` and empty `pending_actions`. No regex; no fake tool calls.

### 7.3 Runtime LLM errors

Fail loud — no silent fallback.

```python
try:
    result = self.deep_agent.invoke(...)
except (anthropic.APIError, openai.APIError) as exc:
    record_audit(session, event_type="agent.error", actor="system", subject_type="thread",
                 subject_id=thread.id, payload={"error_type": type(exc).__name__, "message": str(exc)[:500]})
    raise HTTPException(status_code=502, detail=f"Agent provider error: {exc}") from exc
except GraphRecursionError as exc:
    raise HTTPException(status_code=500, detail="Agent exceeded reasoning budget") from exc
```

Frontend already handles non-2xx responses on the chat endpoint; no new UI work.

### 7.4 Runtime tool errors

Tool exceptions are caught by LangGraph and surfaced as `ToolMessage(content="Error: ...")`. The LLM sees and responds in the next loop. We do not intercept or reformat tool errors at the AgentService layer. Tool errors that touch `database` rollback their session (already true via `with database.SessionLocal() as session: ... session.commit()`).

### 7.5 Runtime HITL edge cases

| Scenario | Handling |
|---|---|
| Confirm called for `action_id` not in `pending_actions` | `404` (already today) |
| Confirm called twice for same `action_id` | `409` (already today) |
| Confirm after `agent_checkpoints.sqlite` was wiped | `Command(resume=...)` returns "no checkpoint for thread"; surface as `410 Gone` ("Agent state expired — start a new thread.") |
| Resume produces another interrupt | Persist a new assistant message with the new pending_actions; return it. |
| User dismisses, agent's next step would have been important | LLM sees `ToolMessage(rejected=true)` and adapts. No pre-emption. |
| Stale pending_action when a new user message arrives | Auto-mark all stale pending as `"dismissed"` when the next user message lands in the thread. |

### 7.6 Checkpoint integrity

A SQLite checkpoint is per-thread keyed by `thread_id`. Thread deletion does not currently cascade to checkpoint deletion (leak is bounded; size is small) — explicit cleanup is out of scope for v1. Dev convenience: a CLI command `python -m backend.app.cli reset-agent-state` to wipe `agent_checkpoints.sqlite`.

## 8. Testing strategy

### 8.1 Unit tests

**Tool tests** (extend `tests/test_agent_tools.py`):
- `run_risk_tool` — happy path returns `totals`/`positions`; missing portfolio raises; audit row written.
- `create_report_tool`, `approve_rfq_tool`, `reject_rfq_tool` — same shape: persistence + audit + return payload.
- All four new tools: invariant that they open and commit their own `SessionLocal()`.

**Model factory** (new `tests/test_model_factory.py`):
- `build_agent_model(provider="anthropic")` returns `ChatAnthropic` with the ZenMux base URL and `anthropic-version` header.
- `build_agent_model(provider="openai")` returns `ChatOpenAI`.
- `build_agent_model(no key)` returns `None`.
- Bad provider raises `ValueError`.

**HITL helpers** (new `tests/test_hitl.py`):
- `interrupt_on_config()` returns the expected 7 names, each with `allowed_decisions=["approve", "reject"]` (no `edit`).
- `pending_actions_from_state` projects a synthetic `HITLRequest` payload correctly, including composite ids `f"{interrupt_id}:{i}"`.
- `build_resume_command` produces the right `Command(resume={"decisions": [...]})` shape for approve/reject and preserves positional ordering.

**Persona/orchestrator** (new `tests/test_personas.py`):
- Each `*_spec(model, tools)` returns the right `name`, non-empty `system_prompt`, full tool list.
- `build_orchestrator` produces a graph with subagent names `{"trader", "risk_manager", "high_board"}` and no auto-injected `general-purpose`.

### 8.2 Integration tests

**Full agent invocation with a stub model** (new `tests/test_agent_integration.py`):

A `make_scripted_model(turns=[...])` fixture returns a `BaseChatModel` subclass that replays a recorded sequence of `AIMessage`s with tool_calls. Tests:

1. Orchestrator dispatches `task(name="trader", ...)`; trader calls `price_product`, returns; assistant message has `tool_calls` populated and no `pending_actions`.
2. Orchestrator dispatches `task(name="risk_manager", ...)`; risk_manager calls `run_risk(portfolio_id=...)`. Asserts:
   - `respond()` returns assistant message with `agent_phase: "awaiting_confirmation"` and one pending action whose `tool_name == "run_risk"` and a composite `id` ending in `:0`.
   - Resume via `Command(resume={"decisions": [{"type": "approve"}]})` finalizes the turn — second assistant message has `agent_phase: "completed"`, the tool body executed, audit row written.
3. Multi-pause: agent calls `run_risk`, user confirms, agent calls `approve_rfq`, user confirms, agent finalizes. Three assistant messages.
4. Dismiss path: pending `approve_rfq`, user dismisses; resume completes; final assistant message acknowledges dismissal.

### 8.3 Existing tests to update

- `tests/test_api.py` and `tests/test_agent_tools.py` assertions about `agent_graph == "deterministic"` or `_propose_actions` regex output are deleted.
- Assertions about `AgentActionProposal.type` migrate to `tool_name`.

### 8.4 Frontend tests

- `ActionProposal.test.tsx` — render with new `tool_name`; ensure label/summary/persona chip render. Render with legacy `type` field; fallback works.
- `AgentDesk.live.test.tsx` — dismiss POST hits the new endpoint; confirm POST flow still works; multi-pause turn appends multiple messages.

### 8.5 Live smoke (env-gated)

Add a single end-to-end test gated by `PYTEST_ANTHROPIC_LIVE=1` that hits real ZenMux through the orchestrator and asserts a non-empty response. Skipped by default.

## 9. Observability

- **Audit log:** every persisted-tool execution emits the same `record_audit` rows it does today (the audits move *into* the tool bodies). Add `"agent.action.dismissed"` for explicit rejects.
- **Logging:** `logging.getLogger("agent.deep")` for orchestrator and middleware, `logging.getLogger("agent.tool")` for tool execution. Default `INFO`. LangGraph's own logging stays as-is.
- **Metrics (out of scope for v1):** per-persona invocation count, HITL pause/resume latency, tool call success rate.

## 10. Rollout

Single-PR refactor. The frontend type change for `AgentActionProposal` is a coordinated rename, so backend and frontend ship together. Legacy thread history reads via the `type → tool_name` shim. No feature flag — the deterministic path is removed and the LLM path is required (with the disabled stub for missing-key environments).

## 11. Out of scope for this refactor

- `edit` decisions in HITL (user editing tool args before approval).
- Per-persona model differences (the locked decision is uniform model across personas).
- Postgres `PostgresSaver` migration (kept compatible via factory; not required for v1).
- Per-persona invocation metrics dashboard.
- Cleanup of orphaned checkpoints when a thread is deleted.
- Background checkpoint compaction.
