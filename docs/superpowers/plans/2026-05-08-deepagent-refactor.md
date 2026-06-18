# DeepAgent Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the regex-and-fallback agent layer in `backend/app/services/agents.py` and `backend/app/main.py` with a real `deepagents.create_deep_agent` orchestrator + 3 persona subagents (trader / risk_manager / high_board), drive every persisted action through HITL-gated LLM tool calls, persist LangGraph state in a SqliteSaver, and surface a generic `tool_name`-keyed pending-action shape to the frontend.

**Architecture:** A new `backend/app/services/deep_agent/` package owns the orchestrator/personas/HITL/checkpointer/model-factory wiring. `AgentService` becomes a thin façade. Persisted-action tools (`run_risk`, `create_report`, `approve_rfq`, `reject_rfq` — new; `price_positions`, `import_otc_positions`, `import_position_market_inputs` — existing) are bound to the agent and gated via `interrupt_on`. The confirm/dismiss endpoints are rewired to `Command(resume=...)` against a SqliteSaver-backed thread.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy, deepagents 0.5.3, langchain-anthropic, langchain-openai, langgraph (SqliteSaver, AsyncSqliteSaver), pydantic v2. Frontend: React + Vite + Vitest.

**Source spec:** `docs/superpowers/specs/2026-05-08-deepagent-refactor-design.md` (commit `e4140d4`).

---

## File Structure

**New backend package:**

| Path | Responsibility |
|---|---|
| `backend/app/services/deep_agent/__init__.py` | Public exports |
| `backend/app/services/deep_agent/model_factory.py` | `build_agent_model(settings)` returns `BaseChatModel \| None` based on provider |
| `backend/app/services/deep_agent/checkpointer.py` | `build_checkpointer(settings)` returns sync SqliteSaver bound to a long-lived sqlite3 connection |
| `backend/app/services/deep_agent/hitl.py` | Interrupt tool-name registry, projection from interrupt → `AgentActionProposal`, resume Command builder |
| `backend/app/services/deep_agent/personas.py` | Three `SubAgent` spec factories + risk-level mapping + label/summary helpers |
| `backend/app/services/deep_agent/orchestrator.py` | `build_orchestrator(model, tools, checkpointer, interrupt_on)` calling `create_deep_agent` |
| `backend/app/services/deep_agent/prompts/orchestrator.md` | Top-level system prompt |
| `backend/app/services/deep_agent/prompts/trader.md` | Trader persona prompt |
| `backend/app/services/deep_agent/prompts/risk_manager.md` | Risk manager persona prompt |
| `backend/app/services/deep_agent/prompts/high_board.md` | Board persona prompt |

**New tests:**

| Path | Covers |
|---|---|
| `tests/test_model_factory.py` | provider branches and missing-key behavior |
| `tests/test_hitl.py` | `interrupt_on_config`, projection, resume Command shape |
| `tests/test_personas.py` | persona spec factories + orchestrator graph shape |
| `tests/test_agent_integration.py` | scripted-model end-to-end (happy path, single pause + resume, multi-pause, dismiss) |

**Modified files:**

| Path | Change |
|---|---|
| `backend/app/services/agents.py` | Shrunk to `AgentService` façade; deletes regex/deterministic helpers |
| `backend/app/services/langchain_tools.py` | Adds `run_risk_tool`, `create_report_tool`, `approve_rfq_tool`, `reject_rfq_tool` |
| `backend/app/main.py` | Rewires `confirm_agent_action`; adds `dismiss_agent_action`; deletes `_execute_confirmed_agent_action` |
| `backend/app/schemas.py` | `AgentActionProposal.type` → `tool_name`; adds optional `persona`, `risk_level`; adds read-side normalization |
| `backend/app/config.py` | Adds `agent_provider`, `agent_model_anthropic`, `agent_model_openai`, `agent_checkpoint_db_path` |
| `backend/app/cli.py` | Adds `reset-agent-state` command |
| `frontend/src/types.ts` | `tool_name: string` (kept `type?` for legacy-compat read) |
| `frontend/src/components/ActionProposal.tsx` | Generic renderer (label / summary / persona chip / args disclosure) |
| `frontend/src/routes/AgentDesk.live.tsx` | Adds dismiss POST flow; appends new assistant messages from confirm/dismiss responses |
| `frontend/src/components/ActionProposal.test.tsx` | Asserts new fields + legacy fallback |
| `frontend/src/routes/AgentDesk.live.test.tsx` | Asserts dismiss POST + multi-pause flow |
| `tests/test_agent_tools.py` | Removes `PERSISTED_ACTION_TOOL_NAMES` assertion; tests new tools |
| `tests/test_api.py` | Removes `agent_graph == "deterministic"` and regex-proposal assertions; updates to `tool_name` |

---

## Conventions for this plan

- All Python tests: `pytest <path> -v`. Project pytest config sets `pythonpath=["backend"]`, so imports use `from app.services...`.
- All TypeScript/React tests: `npm --prefix frontend test -- --run <pattern>`.
- Each task ends with a commit, message format: `<area>(agent): <change>` (e.g. `feat(agent): add HITL helpers`). Co-author trailer omitted from this plan; the implementer adds it as the project requires.
- Where a step says "verify", run the exact command shown and confirm the exact expected output before moving on.

---

## Task 1: Settings additions for the new agent stack

**Files:**
- Modify: `backend/app/config.py`
- Test: `tests/test_api.py` (smoke read of settings) — additive

- [ ] **Step 1: Read current config.py to understand the existing pattern**

Run: `cat backend/app/config.py`

Confirm the existing class is a Pydantic settings dataclass that reads from `os.getenv` with defaults.

- [ ] **Step 2: Write a failing test for the new settings keys**

Append to `tests/test_quant_services.py` (or wherever other config tests live; if none, create `tests/test_config.py`):

```python
# tests/test_config.py
from app.config import Settings


def test_settings_have_pluggable_agent_provider_with_anthropic_default():
    settings = Settings()

    assert settings.agent_provider == "anthropic"
    assert settings.agent_model_anthropic == "anthropic/claude-sonnet-4-6"
    assert settings.agent_model_openai == "openai/gpt-5.4-mini"
    assert settings.agent_checkpoint_db_path.endswith(".sqlite")
```

- [ ] **Step 3: Run the test, verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `AttributeError: ... has no attribute 'agent_provider'` (or similar).

- [ ] **Step 4: Add the settings**

Modify `backend/app/config.py` — inside the existing `Settings` class, add (preserving existing fields):

```python
agent_provider: Literal["anthropic", "openai"] = os.getenv("AGENT_PROVIDER", "anthropic")
agent_model_anthropic: str = os.getenv("AGENT_MODEL_ANTHROPIC", "anthropic/claude-sonnet-4-6")
agent_model_openai: str = os.getenv(
    "AGENT_MODEL_OPENAI",
    os.getenv("OPEN_OTC_DEFAULT_MODEL", "openai/gpt-5.4-mini"),
)
agent_checkpoint_db_path: str = os.getenv(
    "AGENT_CHECKPOINT_DB",
    "./agent_checkpoints.sqlite",
)
```

If `Literal` is not imported at the top of `config.py`, add `from typing import Literal`.

- [ ] **Step 5: Run the test, verify it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full test suite for regressions**

Run: `pytest -q`
Expected: No new failures attributable to this change.

- [ ] **Step 7: Commit**

```bash
git add backend/app/config.py tests/test_config.py
git commit -m "feat(agent): add pluggable provider + checkpoint-path settings"
```

---

## Task 2: Model factory

**Files:**
- Create: `backend/app/services/deep_agent/__init__.py`
- Create: `backend/app/services/deep_agent/model_factory.py`
- Test: `tests/test_model_factory.py`

- [ ] **Step 1: Create the package init**

```python
# backend/app/services/deep_agent/__init__.py
"""Deep agent stack: orchestrator + persona subagents + HITL helpers."""
```

- [ ] **Step 2: Write failing tests**

```python
# tests/test_model_factory.py
from __future__ import annotations

import pytest

from app.config import Settings
from app.services.deep_agent.model_factory import build_agent_model


def _settings(**overrides) -> Settings:
    base = Settings()
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_build_agent_model_returns_none_when_no_zenmux_key():
    settings = _settings(zenmux_api_key=None)

    assert build_agent_model(settings) is None


def test_build_agent_model_returns_chat_anthropic_for_anthropic_provider():
    from langchain_anthropic import ChatAnthropic

    settings = _settings(
        zenmux_api_key="zm_fake",
        agent_provider="anthropic",
        agent_model_anthropic="anthropic/claude-sonnet-4-6",
    )

    model = build_agent_model(settings)

    assert isinstance(model, ChatAnthropic)
    # base_url must point at the Zenmux Anthropic endpoint
    assert "zenmux.ai/api/anthropic" in str(getattr(model, "anthropic_api_url", "")) or \
           "zenmux.ai/api/anthropic" in str(getattr(model, "base_url", ""))


def test_build_agent_model_returns_chat_openai_for_openai_provider():
    from langchain_openai import ChatOpenAI

    settings = _settings(
        zenmux_api_key="zm_fake",
        agent_provider="openai",
        agent_model_openai="openai/gpt-5.4-mini",
        zenmux_base_url="https://zenmux.ai/api/v1",
    )

    model = build_agent_model(settings)

    assert isinstance(model, ChatOpenAI)


def test_build_agent_model_raises_on_unknown_provider():
    settings = _settings(zenmux_api_key="zm_fake", agent_provider="bedrock")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="agent_provider"):
        build_agent_model(settings)
```

- [ ] **Step 3: Run tests, verify all four fail**

Run: `pytest tests/test_model_factory.py -v`
Expected: ImportError (module not yet created).

- [ ] **Step 4: Implement the factory**

```python
# backend/app/services/deep_agent/model_factory.py
"""Pluggable model factory for the desk deep agent.

Returns None when no API key is configured so the AgentService can render
the 'agent disabled' stub without raising.
"""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from app.config import Settings


_ANTHROPIC_BASE_URL = "https://zenmux.ai/api/anthropic"


def build_agent_model(settings: Settings) -> BaseChatModel | None:
    if not settings.zenmux_api_key:
        return None

    provider = settings.agent_provider
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.agent_model_anthropic,
            api_key=settings.zenmux_api_key,
            base_url=_ANTHROPIC_BASE_URL,
            default_headers={"anthropic-version": "2023-06-01"},
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.agent_model_openai,
            api_key=settings.zenmux_api_key,
            base_url=settings.zenmux_base_url,
        )

    raise ValueError(f"unknown agent_provider: {provider}")
```

- [ ] **Step 5: Run tests, verify all four pass**

Run: `pytest tests/test_model_factory.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/__init__.py \
        backend/app/services/deep_agent/model_factory.py \
        tests/test_model_factory.py
git commit -m "feat(agent): add pluggable Anthropic/OpenAI model factory"
```

---

## Task 3: Checkpointer factory

**Files:**
- Create: `backend/app/services/deep_agent/checkpointer.py`
- Test: extend `tests/test_model_factory.py` (close-enough scope) OR create `tests/test_checkpointer.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_checkpointer.py`:

```python
from __future__ import annotations

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import Settings
from app.services.deep_agent.checkpointer import build_checkpointer


def test_build_checkpointer_with_in_memory_path_returns_sqlite_saver():
    settings = Settings()
    settings.agent_checkpoint_db_path = ":memory:"

    saver = build_checkpointer(settings)

    assert isinstance(saver, SqliteSaver)
    # It must be usable for at least a list call (no checkpoints yet -> empty)
    assert list(saver.list({"configurable": {"thread_id": "nonexistent"}})) == []


def test_build_checkpointer_with_disk_path(tmp_path):
    settings = Settings()
    settings.agent_checkpoint_db_path = str(tmp_path / "ck.sqlite")

    saver = build_checkpointer(settings)

    assert isinstance(saver, SqliteSaver)
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_checkpointer.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement the factory**

```python
# backend/app/services/deep_agent/checkpointer.py
"""Persistent SqliteSaver factory for HITL-aware DeepAgent threads.

HITL needs graph state to survive across HTTP requests (the user's confirm
click hits a separate process from the one that produced the pause), so the
checkpointer cannot be InMemorySaver.

We construct from a long-lived sqlite3 connection with check_same_thread=False
because FastAPI's threadpool may invoke the saver from worker threads.
"""
from __future__ import annotations

import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import Settings


def build_checkpointer(settings: Settings) -> SqliteSaver:
    conn = sqlite3.connect(
        settings.agent_checkpoint_db_path,
        check_same_thread=False,
    )
    return SqliteSaver(conn)
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_checkpointer.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/checkpointer.py tests/test_checkpointer.py
git commit -m "feat(agent): add persistent SqliteSaver factory for HITL state"
```

---

## Task 4: HITL helpers — registry and config

**Files:**
- Create: `backend/app/services/deep_agent/hitl.py`
- Test: `tests/test_hitl.py`

- [ ] **Step 1: Write failing tests for registry + config**

```python
# tests/test_hitl.py
from __future__ import annotations

from app.services.deep_agent.hitl import (
    INTERRUPT_TOOL_NAMES,
    interrupt_on_config,
)


def test_interrupt_tool_names_covers_all_state_mutating_tools():
    assert set(INTERRUPT_TOOL_NAMES) == {
        "price_positions",
        "run_risk",
        "create_report",
        "approve_rfq",
        "reject_rfq",
        "import_otc_positions",
        "import_position_market_inputs",
    }


def test_interrupt_on_config_only_allows_approve_and_reject():
    config = interrupt_on_config()

    for tool_name, cfg in config.items():
        assert tool_name in INTERRUPT_TOOL_NAMES
        assert cfg["allowed_decisions"] == ["approve", "reject"]
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_hitl.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement registry and config**

```python
# backend/app/services/deep_agent/hitl.py
"""HITL projection helpers for the desk deep agent.

Verified against langchain.agents.middleware.human_in_the_loop:
- DecisionType = Literal["approve", "edit", "reject"]
- HITLRequest:  {"action_requests": list[ActionRequest], "review_configs": [...]}
- ActionRequest: {"name": str, "args": dict, "description": str?}
- HITLResponse: {"decisions": list[Decision]}  # positional
- Decision:    {"type": "approve"} | {"type": "reject", "message": str?} | {"type": "edit", ...}

v1 exposes only approve/reject at the API edge.
"""
from __future__ import annotations

from typing import Any, TypedDict


INTERRUPT_TOOL_NAMES: tuple[str, ...] = (
    "price_positions",
    "run_risk",
    "create_report",
    "approve_rfq",
    "reject_rfq",
    "import_otc_positions",
    "import_position_market_inputs",
)


class _InterruptOnConfig(TypedDict):
    allowed_decisions: list[str]


def interrupt_on_config() -> dict[str, _InterruptOnConfig]:
    """Return the interrupt_on mapping passed to create_deep_agent."""
    return {
        name: {"allowed_decisions": ["approve", "reject"]}
        for name in INTERRUPT_TOOL_NAMES
    }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_hitl.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py tests/test_hitl.py
git commit -m "feat(agent): add HITL interrupt-on registry and config"
```

---

## Task 5: HITL helpers — projection and resume

**Files:**
- Modify: `backend/app/services/deep_agent/hitl.py`
- Test: extend `tests/test_hitl.py`

- [ ] **Step 1: Write failing tests for projection and resume**

Append to `tests/test_hitl.py`:

```python
from langgraph.types import Command, Interrupt

from app.schemas import AgentActionProposal
from app.services.deep_agent.hitl import (
    build_resume_command,
    pending_actions_from_interrupts,
)


def _fake_interrupt(interrupt_id: str, action_name: str, args: dict) -> Interrupt:
    # Mirrors langchain.agents.middleware.human_in_the_loop.HumanInTheLoopMiddleware:
    # interrupt({"action_requests": [...], "review_configs": [...]})
    return Interrupt(
        value={
            "action_requests": [
                {"name": action_name, "args": args, "description": f"Run {action_name}"}
            ],
            "review_configs": [
                {"action_name": action_name, "allowed_decisions": ["approve", "reject"]}
            ],
        },
        id=interrupt_id,
    )


def test_pending_actions_from_interrupts_projects_each_action_request():
    interrupts = [
        _fake_interrupt("intr-1", "run_risk", {"portfolio_id": 7, "method": "summary"})
    ]

    actions = pending_actions_from_interrupts(interrupts, persona="risk_manager")

    assert len(actions) == 1
    proposal = actions[0]
    assert isinstance(proposal, AgentActionProposal)
    assert proposal.id == "intr-1:0"
    assert proposal.tool_name == "run_risk"
    assert proposal.payload == {"portfolio_id": 7, "method": "summary"}
    assert proposal.requires_confirmation is True
    assert proposal.status == "pending"
    assert proposal.persona == "risk_manager"
    # Description from the interrupt becomes the summary fallback
    assert "run_risk" in proposal.summary


def test_build_resume_command_for_approve_decision():
    cmd = build_resume_command("approve")

    assert isinstance(cmd, Command)
    assert cmd.resume == {"decisions": [{"type": "approve"}]}


def test_build_resume_command_for_reject_decision_with_message():
    cmd = build_resume_command("reject", message="User dismissed the action.")

    assert cmd.resume == {
        "decisions": [{"type": "reject", "message": "User dismissed the action."}]
    }
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_hitl.py -v`
Expected: ImportError on `pending_actions_from_interrupts` and `build_resume_command`.

- [ ] **Step 3: Implement projection and resume**

Append to `backend/app/services/deep_agent/hitl.py`:

```python
from langgraph.types import Command, Interrupt

from app.schemas import AgentActionProposal


_RISK_LEVEL_BY_TOOL: dict[str, str] = {
    "price_positions": "write",
    "run_risk": "write",
    "create_report": "write",
    "approve_rfq": "irreversible",
    "reject_rfq": "irreversible",
    "import_otc_positions": "write",
    "import_position_market_inputs": "write",
}


_LABEL_BY_TOOL: dict[str, str] = {
    "price_positions": "Price positions",
    "run_risk": "Run risk analysis",
    "create_report": "Create report artifacts",
    "approve_rfq": "Approve RFQ",
    "reject_rfq": "Reject RFQ",
    "import_otc_positions": "Import OTC positions",
    "import_position_market_inputs": "Import market inputs",
}


def _summary_for(action_request: dict[str, Any]) -> str:
    description = action_request.get("description")
    if isinstance(description, str) and description:
        return description
    name = action_request["name"]
    args = action_request.get("args") or {}
    if not args:
        return f"Run {name}"
    arg_summary = ", ".join(f"{k}={v}" for k, v in list(args.items())[:4])
    return f"Run {name} ({arg_summary})"


def pending_actions_from_interrupts(
    interrupts: list[Interrupt],
    *,
    persona: str | None = None,
) -> list[AgentActionProposal]:
    """Project LangGraph interrupts into AgentActionProposal records.

    Composite id: f"{interrupt_id}:{i}" where i is the position in
    action_requests. The position is significant because the resume payload
    feeds decisions back as a positional list.
    """
    proposals: list[AgentActionProposal] = []
    for intr in interrupts:
        value = intr.value or {}
        action_requests = value.get("action_requests") or []
        for index, action_request in enumerate(action_requests):
            tool_name = action_request["name"]
            proposals.append(
                AgentActionProposal(
                    id=f"{intr.id}:{index}",
                    tool_name=tool_name,
                    label=_LABEL_BY_TOOL.get(tool_name, tool_name),
                    summary=_summary_for(action_request),
                    payload=dict(action_request.get("args") or {}),
                    requires_confirmation=True,
                    status="pending",
                    persona=persona,
                    risk_level=_RISK_LEVEL_BY_TOOL.get(tool_name),
                )
            )
    return proposals


def build_resume_command(decision: str, *, message: str | None = None) -> Command:
    """Build Command(resume=...) for a single-action HITL batch.

    v1 design constraint: at most one HITL action per assistant turn (see
    spec §5.3). The resume payload's `decisions` list therefore has one
    element. If a future change relaxes the batch-size-1 rule, this
    function gains an `index` and `total` argument.
    """
    if decision == "approve":
        return Command(resume={"decisions": [{"type": "approve"}]})
    if decision == "reject":
        body: dict[str, Any] = {"type": "reject"}
        if message:
            body["message"] = message
        return Command(resume={"decisions": [body]})
    raise ValueError(f"unknown HITL decision: {decision}")
```

You will need an additional import at the top: `from typing import Any, TypedDict` is already there; just confirm.

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_hitl.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/hitl.py tests/test_hitl.py
git commit -m "feat(agent): project HITL interrupts to AgentActionProposal"
```

---

## Task 6: Schema migration — `AgentActionProposal.type` → `tool_name`

**Files:**
- Modify: `backend/app/schemas.py`
- Test: extend `tests/test_api.py` or add `tests/test_schemas.py`

This task lands the schema change before any tool/agent code uses the new shape. Keep `type` reachable as an alias so legacy thread history still parses on read.

- [ ] **Step 1: Write failing tests**

Create `tests/test_schemas.py`:

```python
from __future__ import annotations

from app.schemas import AgentActionProposal


def test_agent_action_proposal_uses_tool_name():
    proposal = AgentActionProposal(
        id="intr-1:0",
        tool_name="run_risk",
        label="Run risk analysis",
        summary="Run summary risk for portfolio #7",
        payload={"portfolio_id": 7},
    )

    assert proposal.tool_name == "run_risk"
    assert proposal.requires_confirmation is True
    assert proposal.status == "pending"
    assert proposal.persona is None
    assert proposal.risk_level is None


def test_agent_action_proposal_legacy_type_field_is_accepted_and_normalized():
    """Old thread history rows have `type` and no `tool_name`. The schema must
    accept them and normalize so downstream code only needs to read tool_name."""
    proposal = AgentActionProposal.model_validate({
        "id": "act-old",
        "type": "approve_rfq",
        "label": "Approve RFQ",
        "summary": "...",
        "payload": {"rfq_id": 42},
    })

    assert proposal.tool_name == "approve_rfq"


def test_agent_action_proposal_optional_fields():
    proposal = AgentActionProposal(
        id="intr-2:0",
        tool_name="approve_rfq",
        label="Approve RFQ",
        summary="...",
        persona="high_board",
        risk_level="irreversible",
    )

    assert proposal.persona == "high_board"
    assert proposal.risk_level == "irreversible"
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `pytest tests/test_schemas.py -v`
Expected: validation errors / `AttributeError` on `tool_name`.

- [ ] **Step 3: Modify the schema**

Replace the existing `AgentActionProposal` class body in `backend/app/schemas.py` with:

```python
class AgentActionProposal(BaseModel):
    id: str
    tool_name: str
    label: str
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True
    status: Literal["pending", "confirmed", "dismissed", "failed"] = "pending"
    persona: Literal["trader", "risk_manager", "high_board"] | None = None
    risk_level: Literal["read", "write", "irreversible"] | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="before")
    @classmethod
    def _accept_legacy_type_field(cls, data: Any) -> Any:
        """Map the legacy `type` field to `tool_name` for old thread history."""
        if isinstance(data, dict) and "tool_name" not in data and "type" in data:
            data = {**data, "tool_name": data["type"]}
        return data
```

Add the import at the top of `schemas.py` if not present:

```python
from pydantic import BaseModel, Field, model_validator
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_schemas.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run full suite to flag downstream breakages early**

Run: `pytest -q`
Expected: A handful of failures in `tests/test_api.py` and `tests/test_agent_tools.py` referencing `action.type`. These are addressed in later tasks (Task 12 and Task 14). Note them now and proceed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py tests/test_schemas.py
git commit -m "feat(agent): rename AgentActionProposal.type to tool_name (legacy alias preserved)"
```

---

## Task 7: New persisted-action tools

**Files:**
- Modify: `backend/app/services/langchain_tools.py`
- Test: extend `tests/test_agent_tools.py`

The four new `@tool`s absorb the persistence + audit logic that lives in `main.py:_execute_confirmed_agent_action` today. They follow the existing `price_positions_tool` pattern: open `database.SessionLocal()`, mutate, audit, commit, return a dict.

- [ ] **Step 1: Read the existing dispatch logic to mirror it correctly**

Run: `grep -n "if action_type" -A 60 backend/app/main.py | head -200`

Skim the four branches we are absorbing: `run_risk`, `create_report`, `approve_rfq`, `reject_rfq`. Note the audit event types and result shapes. We will reuse these.

- [ ] **Step 2: Write failing tests for `run_risk_tool`**

Append to `tests/test_agent_tools.py`:

```python
def test_run_risk_tool_executes_persisted_risk(tmp_path, monkeypatch):
    from app import database
    from app.config import Settings
    from app.services.langchain_tools import run_risk_tool

    monkeypatch.setenv("OPEN_OTC_DATABASE_URL", f"sqlite:///{tmp_path}/db.sqlite")
    settings = Settings()
    database.init_db(settings)

    with database.SessionLocal() as session:
        from app.models import Portfolio, Position
        portfolio = Portfolio(name="P", base_currency="USD")
        session.add(portfolio)
        session.flush()
        portfolio_id = portfolio.id
        session.commit()

    result = run_risk_tool.invoke({"portfolio_id": portfolio_id, "method": "summary"})

    assert result["portfolio_id"] == portfolio_id
    assert "totals" in result
    assert "positions" in result

    # Confirm an audit row was written.
    with database.SessionLocal() as session:
        from app.models import AuditLog
        rows = session.query(AuditLog).filter(AuditLog.event_type == "risk.run").all()
        assert any(row.payload.get("source") == "agent_confirmed" for row in rows)
```

(If the test database / Settings layout differs, adapt the bootstrap to match the project's existing test fixtures; mirror what `tests/test_position_import_pricing.py` does.)

- [ ] **Step 3: Run, verify it fails**

Run: `pytest tests/test_agent_tools.py::test_run_risk_tool_executes_persisted_risk -v`
Expected: ImportError or `AttributeError` on `run_risk_tool`.

- [ ] **Step 4: Implement `run_risk_tool`**

Add to `backend/app/services/langchain_tools.py` (and add necessary imports at the top — `from sqlalchemy.orm import selectinload`, `from .audit import record_audit`, `from ..models import AuditLog, Portfolio, Position`):

```python
class RunRiskInput(BaseModel):
    portfolio_id: int
    method: Literal["summary", "stress"] = "summary"


@tool("run_risk", args_schema=RunRiskInput)
def run_risk_tool(portfolio_id: int, method: str = "summary") -> dict[str, Any]:
    """Run audited persisted risk over a portfolio. Returns metrics + audit ref."""
    from .. import database
    from ..models import Portfolio, Position
    from .quantark import calculate_portfolio_risk
    from .audit import record_audit

    database.init_db()
    with database.SessionLocal() as session:
        portfolio = (
            session.query(Portfolio)
            .options(selectinload(Portfolio.positions).selectinload(Position.market_inputs))
            .filter(Portfolio.id == portfolio_id)
            .one_or_none()
        )
        if not portfolio:
            raise ValueError(f"portfolio {portfolio_id} not found")
        metrics = calculate_portfolio_risk(portfolio)
        record_audit(
            session,
            event_type="risk.run",
            actor="desk_user",
            subject_type="portfolio",
            subject_id=portfolio.id,
            payload={"method": method, "source": "agent_confirmed"},
        )
        session.commit()
        return {
            "portfolio_id": portfolio.id,
            "method": method,
            "totals": metrics.get("totals", {}),
            "positions": metrics.get("positions", []),
        }
```

You will need `from typing import Literal` if not already imported. The exact `selectinload(...)` chain should mirror what the current `_execute_confirmed_agent_action` uses for `run_risk`.

- [ ] **Step 5: Run, verify it passes**

Run: `pytest tests/test_agent_tools.py::test_run_risk_tool_executes_persisted_risk -v`
Expected: PASS.

- [ ] **Step 6: Repeat steps 2–5 for `create_report_tool`**

Mirror the `create_report` branch in `_execute_confirmed_agent_action`. The schema:

```python
class CreateReportInput(BaseModel):
    portfolio_id: int
    report_type: Literal["portfolio"] = "portfolio"
    title: str = "Agent Generated Desk Report"


@tool("create_report", args_schema=CreateReportInput)
def create_report_tool(portfolio_id: int, title: str, report_type: str = "portfolio") -> dict[str, Any]:
    """Create persisted desk report artifacts for a portfolio."""
    from .. import database
    from .reports import create_portfolio_report
    from .audit import record_audit

    database.init_db()
    with database.SessionLocal() as session:
        report = create_portfolio_report(session, portfolio_id=portfolio_id, title=title, report_type=report_type)
        record_audit(
            session,
            event_type="report.created",
            actor="desk_user",
            subject_type="report",
            subject_id=report.id,
            payload={"portfolio_id": portfolio_id, "source": "agent_confirmed"},
        )
        session.commit()
        return {
            "report_id": report.id,
            "portfolio_id": portfolio_id,
            "title": report.title,
            "status": report.status,
            "artifact_paths": report.artifact_paths,
        }
```

If the existing `create_portfolio_report` helper has a different signature or the report model uses different attribute names, adapt to match. The implementer should grep `backend/app/services/reports.py` for the actual API.

The companion test:

```python
def test_create_report_tool_writes_artifact(tmp_path, monkeypatch):
    # Same bootstrap as run_risk test.
    ...
    result = create_report_tool.invoke({
        "portfolio_id": portfolio_id,
        "title": "Test Report",
        "report_type": "portfolio",
    })
    assert result["portfolio_id"] == portfolio_id
    assert result["status"]
```

- [ ] **Step 7: Repeat for `approve_rfq_tool` and `reject_rfq_tool`**

Schemas:

```python
class ApproveRfqInput(BaseModel):
    rfq_id: int
    approver: str = "agent_confirmed"
    comment: str | None = None


class RejectRfqInput(BaseModel):
    rfq_id: int
    approver: str = "agent_confirmed"
    comment: str | None = None
```

Tool bodies mirror the `approve_rfq` / `reject_rfq` branches from `_execute_confirmed_agent_action`. Use the existing `mark_rfq_approved` / `mark_rfq_rejected` helpers in `app/services/quantark.py` (or wherever they currently live — grep `def approve_rfq` / `mark_rfq` to find them).

Each tool emits a `record_audit` row with `event_type="rfq.approved"` (or `rfq.rejected`) and `payload={"approver", "comment", "source": "agent_confirmed"}`.

Tests assert: returned dict carries `rfq_id`, `status`, audit row exists.

- [ ] **Step 8: Run all tool tests**

Run: `pytest tests/test_agent_tools.py -v`
Expected: All four new test functions pass alongside existing ones.

- [ ] **Step 9: Commit**

```bash
git add backend/app/services/langchain_tools.py tests/test_agent_tools.py
git commit -m "feat(agent): add run_risk/create_report/approve_rfq/reject_rfq tools"
```

---

## Task 8: Drop the persisted-action block list; bind every tool to the agent

**Files:**
- Modify: `backend/app/services/langchain_tools.py` (extend `QUANT_AGENT_TOOLS`)
- Modify: `backend/app/services/agents.py` (rewrite `select_deep_agent_tools`, drop `PERSISTED_ACTION_TOOL_NAMES`)
- Modify: `tests/test_agent_tools.py` (replace the "excludes persisted" test)

- [ ] **Step 1: Add the four new tools to `QUANT_AGENT_TOOLS`**

In `backend/app/services/langchain_tools.py`, extend the `QUANT_AGENT_TOOLS` list at the bottom of the file:

```python
QUANT_AGENT_TOOLS = [
    price_product_tool,
    solve_rfq_tool,
    get_positions_tool,
    calculate_risk_tool,
    recommend_hedge_tool,
    run_report_batch_tool,
    fetch_market_snapshot_tool,
    # Persisted-action / HITL-gated:
    price_positions_tool,
    import_otc_positions_tool,
    import_position_market_inputs_tool,
    run_risk_tool,
    create_report_tool,
    approve_rfq_tool,
    reject_rfq_tool,
]
```

- [ ] **Step 2: Rewrite `select_deep_agent_tools` in agents.py**

Replace the current `select_deep_agent_tools` and constants in `backend/app/services/agents.py` with a single coherent block (this is part of the larger AgentService rewrite in Task 11; for now we do the minimum to keep imports working):

```python
DEEP_AGENT_TOOL_NAMES: frozenset[str] = frozenset({
    "price_product",
    "solve_rfq",
    "get_positions",
    "calculate_risk",
    "recommend_hedge",
    "run_report_batch",
    "fetch_market_snapshot",
    "price_positions",
    "import_otc_positions",
    "import_position_market_inputs",
    "run_risk",
    "create_report",
    "approve_rfq",
    "reject_rfq",
})


def select_deep_agent_tools(tools: Iterable[Any] = QUANT_AGENT_TOOLS) -> list[Any]:
    by_name = {tool.name: tool for tool in tools}
    missing = sorted(DEEP_AGENT_TOOL_NAMES - set(by_name))
    if missing:
        raise RuntimeError(f"Missing required DeepAgent tools: {', '.join(missing)}")
    return [by_name[name] for name in sorted(DEEP_AGENT_TOOL_NAMES)]
```

Delete the `PERSISTED_ACTION_TOOL_NAMES` constant entirely.

- [ ] **Step 3: Update the existing test that asserted exclusion**

Replace `test_select_deep_agent_tools_excludes_persisted_action_tools` in `tests/test_agent_tools.py`:

```python
def test_select_deep_agent_tools_includes_every_required_tool():
    selected = select_deep_agent_tools()
    names = {tool.name for tool in selected}
    assert names == DEEP_AGENT_TOOL_NAMES
    # All seven persisted tools are now bound (HITL gates them at runtime).
    assert {"price_positions", "run_risk", "approve_rfq"}.issubset(names)
```

Also remove the import of `PERSISTED_ACTION_TOOL_NAMES` from the file.

- [ ] **Step 4: Run, verify**

Run: `pytest tests/test_agent_tools.py -v`
Expected: All tests pass, including the renamed one.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/agents.py backend/app/services/langchain_tools.py tests/test_agent_tools.py
git commit -m "feat(agent): bind every persisted-action tool (HITL gates them at runtime)"
```

---

## Task 9: Persona prompts (markdown files)

**Files:**
- Create: `backend/app/services/deep_agent/prompts/orchestrator.md`
- Create: `backend/app/services/deep_agent/prompts/trader.md`
- Create: `backend/app/services/deep_agent/prompts/risk_manager.md`
- Create: `backend/app/services/deep_agent/prompts/high_board.md`

No tests here — these are prompt files. Each is short and concrete.

- [ ] **Step 1: Write `orchestrator.md`**

```markdown
You are the orchestrator for an OTC derivatives trading desk assistant. Three persona subagents are available via the `task` tool: `trader`, `risk_manager`, `high_board`.

## Role
Plan, delegate, and synthesize. You DO NOT call domain tools yourself. The only tools you should use are `task` (to dispatch to a subagent), `write_todos` (for multi-step plans), and the filesystem tools when reading scratch files.

## Routing
- Pricing, RFQ solving, quotes, market data → `trader`.
- Risk, VaR, stress, exposure, hedge feasibility → `risk_manager`.
- Reporting, release readiness, board-level decisions, RFQ approve/reject → `high_board`.

## Compound queries
For requests that span personas (e.g. "run risk and then approve RFQ-42 if VaR is fine"):
1. Call the first relevant persona via `task(...)`.
2. Wait for its result.
3. Decide whether to call the next persona based on what came back.
4. Synthesize a final answer that cites which persona produced which fact.

## Batch-size-1 rule for HITL
NEVER request more than one persisted/HITL-gated tool call in a single assistant turn. The persisted tools are: `price_positions`, `run_risk`, `create_report`, `approve_rfq`, `reject_rfq`, `import_otc_positions`, `import_position_market_inputs`. Each requires user confirmation. If multiple persisted operations are needed, request the first, wait for confirmation, then request the next. (You enforce this by instructing each subagent — they will obey.)

## Forbidden
- Calling persisted tools directly. They live on the personas; you delegate.
- Claiming work was done that requires confirmation.
- Synthesizing a final answer before the called personas have returned.
```

- [ ] **Step 2: Write `trader.md`**

```markdown
You are the trader persona for an OTC derivatives desk. Your decision lens is quote readiness, pricing accuracy, and trade construction.

## Tools you use
- `price_product` — single-trade pricing through QuantArk.
- `solve_rfq` — solve unknown RFQ terms.
- `get_positions` — inspect a portfolio snapshot.
- `fetch_market_snapshot` — pull AKShare market data.
- `price_positions` — batch reprice persisted positions (HITL — requires confirmation).

## Output style
- Be concise. State the price/quote, the inputs you used, and any caveats.
- When proposing a quote, separate "what I'd quote" from "what needs confirmation before release".
- Do not editorialize about risk limits — defer those to the risk_manager persona via the orchestrator.

## Batch-size-1 HITL rule
Never call more than one persisted (HITL-gated) tool in a single assistant turn. Currently the only persisted tool in your set is `price_positions`. If you need to do follow-up persisted work after, return your result first and let the orchestrator route the next step.
```

- [ ] **Step 3: Write `risk_manager.md`**

```markdown
You are the risk_manager persona for an OTC derivatives desk. Your decision lens is exposure, limits, and hedge feasibility.

## Tools you use
- `calculate_risk` — in-memory risk metrics for a supplied snapshot.
- `recommend_hedge` — hedge suggestion from risk metrics.
- `get_positions` — inspect portfolio positions.
- `run_risk` — audited persisted risk over a portfolio (HITL — requires confirmation).

## Output style
- Lead with the verdict: within limits / breach / unknown. Cite the metric.
- Quantify exposure (delta, VaR, concentration) before proposing a hedge.
- If you recommend a hedge, state the rationale and the metric it would shift.

## Batch-size-1 HITL rule
Only one persisted tool call per turn. If `run_risk` is needed and a hedge follow-up will also need persistence, do `run_risk` first and let the orchestrator route the hedge after.
```

- [ ] **Step 4: Write `high_board.md`**

```markdown
You are the high_board persona for an OTC derivatives desk. Your decision lens is release readiness, governance, and reporting.

## Tools you use
- `run_report_batch` — prepare a report payload (does not persist).
- `create_report` — create persisted report artifacts (HITL — requires confirmation).
- `approve_rfq` — approve an RFQ for release (HITL — irreversible).
- `reject_rfq` — reject an RFQ (HITL — irreversible).

## Output style
- Begin with the decision (approve / hold / reject) and one-line rationale.
- Cite the supporting facts (pricing from trader, risk metrics from risk_manager).
- Surface any unresolved blockers explicitly. Do not approve in the presence of unresolved risk-manager flags.

## Batch-size-1 HITL rule
At most one persisted/HITL tool per turn. If both `approve_rfq` and `create_report` are needed, do them in separate turns: approve first, then report.
```

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/deep_agent/prompts/
git commit -m "feat(agent): add orchestrator and persona system prompts"
```

---

## Task 10: Persona spec factories + orchestrator builder

**Files:**
- Create: `backend/app/services/deep_agent/personas.py`
- Create: `backend/app/services/deep_agent/orchestrator.py`
- Test: `tests/test_personas.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_personas.py`:

```python
from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.orchestrator import build_orchestrator
from app.services.deep_agent.checkpointer import build_checkpointer
from app.services.deep_agent.hitl import interrupt_on_config
from app.config import Settings


class _FakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _all_tool_names(spec: dict) -> set[str]:
    return {t.name for t in spec["tools"]}


def test_trader_spec_carries_full_tool_set_and_nonempty_prompt():
    from app.services.langchain_tools import QUANT_AGENT_TOOLS

    model = _FakeModel(responses=[AIMessage(content="ok")])
    spec = trader_spec(model, QUANT_AGENT_TOOLS)

    assert spec["name"] == "trader"
    assert spec["system_prompt"]
    assert "trader" in spec["system_prompt"].lower()
    assert _all_tool_names(spec) == {t.name for t in QUANT_AGENT_TOOLS}


def test_risk_and_board_specs_have_distinct_names_and_prompts():
    from app.services.langchain_tools import QUANT_AGENT_TOOLS

    model = _FakeModel(responses=[AIMessage(content="ok")])
    risk = risk_spec(model, QUANT_AGENT_TOOLS)
    board = board_spec(model, QUANT_AGENT_TOOLS)

    assert risk["name"] == "risk_manager"
    assert board["name"] == "high_board"
    assert risk["system_prompt"] != board["system_prompt"]


def test_build_orchestrator_registers_all_three_personas_and_no_general_purpose():
    from app.services.langchain_tools import QUANT_AGENT_TOOLS

    settings = Settings()
    settings.agent_checkpoint_db_path = ":memory:"
    model = _FakeModel(responses=[AIMessage(content="hello")])
    checkpointer = build_checkpointer(settings)

    graph = build_orchestrator(
        model=model,
        tools=QUANT_AGENT_TOOLS,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on_config(),
    )

    # The orchestrator name must be set so logs / LangSmith traces are searchable.
    assert graph.name == "otc_desk_orchestrator"
```

- [ ] **Step 2: Run, verify they fail**

Run: `pytest tests/test_personas.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `personas.py`**

```python
# backend/app/services/deep_agent/personas.py
"""Persona SubAgent spec factories.

All three personas hold the *same full tool list* (per the design spec
locked decision); differentiation is via system prompt only. HITL gates
the persisted/state-mutating tools at runtime.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool


_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _spec(
    *,
    name: str,
    description: str,
    prompt_file: str,
    tools: Sequence[BaseTool],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "system_prompt": _load_prompt(prompt_file),
        "tools": list(tools),
        # model and middleware inherit from the parent orchestrator.
    }


def trader_spec(model: BaseChatModel, tools: Sequence[BaseTool]) -> dict[str, Any]:
    return _spec(
        name="trader",
        description=(
            "Quotes, pricing, RFQ solving, market snapshots. "
            "Uses price_positions for batch repricing."
        ),
        prompt_file="trader.md",
        tools=tools,
    )


def risk_spec(model: BaseChatModel, tools: Sequence[BaseTool]) -> dict[str, Any]:
    return _spec(
        name="risk_manager",
        description=(
            "Limits, exposure, hedge feasibility. "
            "Uses run_risk for audited persisted risk runs."
        ),
        prompt_file="risk_manager.md",
        tools=tools,
    )


def board_spec(model: BaseChatModel, tools: Sequence[BaseTool]) -> dict[str, Any]:
    return _spec(
        name="high_board",
        description=(
            "Release/approve, reporting. "
            "Uses approve_rfq, reject_rfq, create_report — all HITL-gated."
        ),
        prompt_file="high_board.md",
        tools=tools,
    )


def all_personas(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
) -> list[dict[str, Any]]:
    return [
        trader_spec(model, tools),
        risk_spec(model, tools),
        board_spec(model, tools),
    ]
```

- [ ] **Step 4: Implement `orchestrator.py`**

```python
# backend/app/services/deep_agent/orchestrator.py
"""Top-level orchestrator builder.

The orchestrator has *no domain tools* of its own — its job is to plan,
delegate via the auto-injected `task` tool, and synthesize. All quant
tools live on the persona subagents, gated by HITL at runtime.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .hitl import interrupt_on_config
from .personas import all_personas


_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _orchestrator_prompt() -> str:
    return (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8")


def build_orchestrator(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    interrupt_on: dict[str, Any] | None = None,
) -> Any:
    """Create the desk deep-agent orchestrator with three persona subagents."""
    from deepagents import create_deep_agent
    from deepagents.middleware.permissions import FilesystemPermission

    return create_deep_agent(
        model=model,
        tools=[],  # orchestrator has no domain tools
        system_prompt=_orchestrator_prompt(),
        subagents=all_personas(model, tools),
        interrupt_on=interrupt_on if interrupt_on is not None else interrupt_on_config(),
        checkpointer=checkpointer,
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/", "/**"],
                mode="deny",
            )
        ],
        name="otc_desk_orchestrator",
    )
```

- [ ] **Step 5: Run, verify they pass**

Run: `pytest tests/test_personas.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/deep_agent/personas.py \
        backend/app/services/deep_agent/orchestrator.py \
        tests/test_personas.py
git commit -m "feat(agent): add persona specs and orchestrator builder"
```

---

## Task 11: AgentService rewire — façade only

**Files:**
- Modify: `backend/app/services/agents.py` (large rewrite)
- Modify: `tests/test_agent_tools.py` and `tests/test_api.py` (drop deterministic assertions)

This is the largest single task. We delete the regex/deterministic helpers and rewire `respond` to drive the orchestrator. HITL pause persistence lands in this task; resume in Task 13.

- [ ] **Step 1: Write a failing happy-path integration test**

Append to `tests/test_agent_tools.py`:

```python
def test_agent_service_respond_through_orchestrator(monkeypatch, tmp_path):
    """Stubs out create_deep_agent to return a callable that emits a deterministic
    final AIMessage. Asserts that respond() persists the assistant message and
    surfaces meta keys agent_graph='deepagents' and agent_phase='completed'."""
    from langchain_core.messages import AIMessage

    from app import database
    from app.config import Settings
    from app.models import AgentMessage, AgentThread
    from app.services import agents as agents_module

    monkeypatch.setenv("OPEN_OTC_DATABASE_URL", f"sqlite:///{tmp_path}/db.sqlite")
    monkeypatch.setenv("ZENMUX_API_KEY", "zm_fake")
    monkeypatch.setenv("AGENT_CHECKPOINT_DB", ":memory:")
    settings = Settings()
    database.init_db(settings)

    class _StubGraph:
        def invoke(self, payload, config=None):
            return {
                "messages": [AIMessage(content="Stub final reply")],
            }

        def astream_events(self, payload, config=None, version="v2"):  # not exercised here
            raise NotImplementedError

        @property
        def name(self):
            return "stub"

    def fake_build_orchestrator(**kwargs):
        return _StubGraph()

    monkeypatch.setattr(agents_module, "build_orchestrator", fake_build_orchestrator)

    service = agents_module.AgentService(settings=settings)
    with database.SessionLocal() as session:
        thread = service.create_thread(session, title="t", character="trader")
        message = service.respond(session, thread, content="hello")
        session.commit()

    assert message.content == "Stub final reply"
    assert message.meta["agent_graph"] == "deepagents"
    assert message.meta["agent_phase"] == "completed"
    assert message.meta["pending_actions"] == []
```

(Adjust the database/Settings bootstrap to match the project's existing test fixtures — see `tests/test_position_import_pricing.py`.)

- [ ] **Step 2: Run, verify it fails**

Run: `pytest tests/test_agent_tools.py::test_agent_service_respond_through_orchestrator -v`
Expected: One of: ImportError on `build_orchestrator` from agents module, or wrong meta keys.

- [ ] **Step 3: Rewrite `agents.py`**

Replace the whole `backend/app/services/agents.py` body with:

```python
from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from langchain_core.messages import HumanMessage
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..models import AgentMessage, AgentThread, MemoryEntry, Portfolio, Position
from ..schemas import AgentActionProposal, AgentAssetOut, AgentPageContext
from .audit import record_audit
from .deep_agent.checkpointer import build_checkpointer
from .deep_agent.hitl import (
    INTERRUPT_TOOL_NAMES,
    interrupt_on_config,
    pending_actions_from_interrupts,
)
from .deep_agent.model_factory import build_agent_model
from .deep_agent.orchestrator import build_orchestrator
from .langchain_tools import QUANT_AGENT_TOOLS


logger = logging.getLogger("agent.deep")


DEEP_AGENT_TOOL_NAMES: frozenset[str] = frozenset({
    "price_product", "solve_rfq", "get_positions", "calculate_risk",
    "recommend_hedge", "run_report_batch", "fetch_market_snapshot",
    "price_positions", "import_otc_positions", "import_position_market_inputs",
    "run_risk", "create_report", "approve_rfq", "reject_rfq",
})


def select_deep_agent_tools(tools: Iterable[Any] = QUANT_AGENT_TOOLS) -> list[Any]:
    by_name = {tool.name: tool for tool in tools}
    missing = sorted(DEEP_AGENT_TOOL_NAMES - set(by_name))
    if missing:
        raise RuntimeError(f"Missing required DeepAgent tools: {', '.join(missing)}")
    return [by_name[name] for name in sorted(DEEP_AGENT_TOOL_NAMES)]


_DISABLED_RESPONSE = (
    "Agent unavailable — LLM is not configured. "
    "Set ZENMUX_API_KEY (and optionally AGENT_PROVIDER, AGENT_MODEL_*) "
    "to enable the desk agent."
)


def _orchestrator_user_prompt(content: str, character_hint: str, context: dict[str, Any]) -> str:
    context_json = json.dumps(context, ensure_ascii=False, default=str, sort_keys=True)
    hint = f"User suggested persona: {character_hint.replace('_', ' ')}." if character_hint != "auto" else ""
    return (
        f"{hint}\n"
        f"Lightweight context JSON: {context_json}\n\n"
        f"User message: {content}"
    )


def _extract_final_ai_text(result: Any) -> str:
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in reversed(messages):
        if getattr(message, "type", None) == "ai":
            return _message_content_to_text(getattr(message, "content", ""))
    return ""


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(p.strip() for p in parts if p.strip()).strip()
    return str(content).strip() if content is not None else ""


def _personas_invoked(result: Any) -> list[str]:
    """Scan messages for `task(name=...)` tool calls to record which personas ran."""
    invoked: list[str] = []
    messages = result.get("messages", []) if isinstance(result, dict) else []
    for message in messages:
        for tool_call in getattr(message, "tool_calls", None) or []:
            if tool_call.get("name") == "task":
                args = tool_call.get("args") or {}
                name = args.get("subagent_type") or args.get("name")
                if isinstance(name, str) and name not in invoked:
                    invoked.append(name)
    return invoked


class AgentService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.tools = select_deep_agent_tools()
        self.model = build_agent_model(self.settings)
        if self.model is None:
            self.deep_agent = None
            self.checkpointer = None
        else:
            self.checkpointer = build_checkpointer(self.settings)
            self.deep_agent = build_orchestrator(
                model=self.model,
                tools=self.tools,
                checkpointer=self.checkpointer,
                interrupt_on=interrupt_on_config(),
            )

    # ─── thread management ───────────────────────────────────────────────
    def create_thread(self, session: Session, title: str, character: str) -> AgentThread:
        thread = AgentThread(title=title, character=character)
        session.add(thread)
        session.flush()
        record_audit(
            session,
            event_type="thread.created",
            actor="system",
            subject_type="thread",
            subject_id=thread.id,
            payload={"character": character},
        )
        return thread

    # ─── core respond ────────────────────────────────────────────────────
    def respond(
        self,
        session: Session,
        thread: AgentThread,
        content: str,
        requested_character: str = "auto",
        page_context: AgentPageContext | None = None,
    ) -> AgentMessage:
        user_msg = AgentMessage(
            thread_id=thread.id,
            role="user",
            character=None,
            content=content,
            meta={"page_context": page_context.model_dump(mode="json") if page_context else None},
        )
        session.add(user_msg)

        if self.deep_agent is None:
            return self._persist_disabled_response(session, thread)

        context = self._context(session, page_context)
        assets = self._context_assets(page_context)
        prompt = _orchestrator_user_prompt(content, requested_character, context)

        try:
            result = self.deep_agent.invoke(
                {"messages": [HumanMessage(content=prompt)]},
                config={"configurable": {"thread_id": str(thread.id)}},
            )
        except Exception as exc:
            logger.exception("DeepAgent invoke failed for thread %s", thread.id)
            record_audit(
                session,
                event_type="agent.error",
                actor="system",
                subject_type="thread",
                subject_id=thread.id,
                payload={"error_type": type(exc).__name__, "message": str(exc)[:500]},
            )
            raise

        return self._persist_agent_result(session, thread, result, assets, page_context)

    # ─── helpers used both for first-turn and resume ─────────────────────
    def _persist_agent_result(
        self,
        session: Session,
        thread: AgentThread,
        result: Any,
        assets: list[AgentAssetOut],
        page_context: AgentPageContext | None,
    ) -> AgentMessage:
        interrupts = result.get("__interrupt__") if isinstance(result, dict) else None
        personas = _personas_invoked(result)
        last_persona = personas[-1] if personas else None

        if interrupts:
            pending = pending_actions_from_interrupts(list(interrupts), persona=last_persona)
            interim_text = _extract_final_ai_text(result) or "Awaiting confirmation for the next step."
            assistant_msg = AgentMessage(
                thread_id=thread.id,
                role="assistant",
                character=last_persona,
                content=interim_text,
                meta={
                    "agent_graph": "deepagents",
                    "agent_phase": "awaiting_confirmation",
                    "pending_actions": [a.model_dump(mode="json") for a in pending],
                    "interrupt_ids": [intr.id for intr in interrupts],
                    "personas_invoked": personas,
                    "assets": [asset.model_dump(mode="json") for asset in assets],
                    "context_used": page_context.model_dump(mode="json") if page_context else None,
                    "agent_enabled": True,
                },
            )
            session.add(assistant_msg)
            thread.character = last_persona or thread.character
            session.flush()
            return assistant_msg

        final_text = _extract_final_ai_text(result) or "(no response)"
        assistant_msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character=last_persona,
            content=final_text,
            meta={
                "agent_graph": "deepagents",
                "agent_phase": "completed",
                "pending_actions": [],
                "personas_invoked": personas,
                "assets": [asset.model_dump(mode="json") for asset in assets],
                "context_used": page_context.model_dump(mode="json") if page_context else None,
                "agent_enabled": True,
            },
        )
        session.add(assistant_msg)
        thread.character = last_persona or thread.character
        session.flush()
        record_audit(
            session,
            event_type="chat.message",
            actor="desk_user",
            subject_type="thread",
            subject_id=thread.id,
            payload={"personas_invoked": personas},
        )
        return assistant_msg

    def _persist_disabled_response(self, session: Session, thread: AgentThread) -> AgentMessage:
        msg = AgentMessage(
            thread_id=thread.id,
            role="assistant",
            character=None,
            content=_DISABLED_RESPONSE,
            meta={
                "agent_graph": "disabled",
                "agent_phase": "completed",
                "agent_enabled": False,
                "pending_actions": [],
            },
        )
        session.add(msg)
        session.flush()
        return msg

    # ─── context (kept from old implementation) ──────────────────────────
    def _context(self, session: Session, page_context: AgentPageContext | None) -> dict[str, Any]:
        portfolio_id = _entity_int(page_context, "portfolio_id")
        query = session.query(Portfolio)
        portfolio = query.filter(Portfolio.id == portfolio_id).one_or_none() if portfolio_id else query.first()
        page_summary = _page_summary(page_context)
        if not portfolio:
            return {"page_summary": page_summary}
        return {
            "portfolio_summary": _lightweight_portfolio_summary(session, portfolio, page_context),
            "page_summary": page_summary,
        }

    def _context_assets(self, page_context: AgentPageContext | None) -> list[AgentAssetOut]:
        # Body identical to the previous implementation; keep it as-is.
        ...  # see existing source for the exact body — copy it verbatim


# ─── kept utilities (move them ABOVE the class if Python complains) ─────
def search_memories(session: Session, namespace: str, query: str, limit: int = 5) -> list[MemoryEntry]:
    tokens = [t.lower() for t in query.split() if len(t) > 2]
    rows = (
        session.query(MemoryEntry)
        .filter(MemoryEntry.namespace == namespace)
        .order_by(MemoryEntry.created_at.desc())
        .all()
    )
    if not tokens:
        return rows[:limit]
    scored = [r for r in rows if any(t in r.content.lower() for t in tokens)]
    return scored[:limit]


def _page_summary(page_context: AgentPageContext | None) -> str:
    if not page_context:
        return ""
    chips = ", ".join(page_context.chips[:5])
    if chips:
        return f" Current page context loaded: {chips}. "
    return f" Current page context loaded: {page_context.title}. "


def _entity_int(page_context: AgentPageContext | None, key: str) -> int | None:
    if not page_context:
        return None
    value = page_context.entity_ids.get(key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _lightweight_portfolio_summary(
    session: Session,
    portfolio: Portfolio,
    page_context: AgentPageContext | None,
) -> dict[str, Any]:
    # Body identical to the previous implementation; copy verbatim.
    ...
```

For the `...` placeholders (`_context_assets`, `_lightweight_portfolio_summary`): copy the body verbatim from the previous `agents.py`. Both functions are pure helpers that don't depend on the deleted regex/deterministic code.

**Critical deletions:**
- `_route_character`, `_persona_response`, `_deterministic_agent_step`, `_try_execute_tools`, `_format_tool_response`, `_format_json_context_block`, `_propose_actions`, `_format_response`, `_pricing_label`, `_extract_pricing_overrides`, `_number_after_any_pattern`, `_coerce_float`, `_scale_percentage_field`, `_format_overrides`, `_find_referenced_position`, `_reference_tokens`, `_deep_agent_user_prompt`, `build_deep_agent`, `build_zenmux_model`, `PERSISTED_ACTION_TOOL_NAMES`, `DESK_DEEP_AGENT_PROMPT` — all gone.
- Keep `process_events` out of meta entirely; the streaming layer (Task 14) will populate it from real LangGraph events.

- [ ] **Step 4: Run focused test, verify it passes**

Run: `pytest tests/test_agent_tools.py::test_agent_service_respond_through_orchestrator -v`
Expected: PASS.

- [ ] **Step 5: Run full backend suite — expect breakages in `test_api.py`**

Run: `pytest -q`
Expected: `test_api.py` failures referencing removed regex behavior. Make a note; do NOT fix them in this commit.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/agents.py tests/test_agent_tools.py
git commit -m "refactor(agent): rewire AgentService to deep_agent orchestrator"
```

---

## Task 12: Action proposals — confirm endpoint rewired to `Command(resume=...)`

**Files:**
- Modify: `backend/app/main.py` (rewrite `confirm_agent_action`; add `dismiss_agent_action`; delete `_execute_confirmed_agent_action` and helpers it depends on)
- Modify: `tests/test_api.py` (update + add tests)

- [ ] **Step 1: Write failing tests for confirm + dismiss**

Add to `tests/test_api.py`:

```python
def test_confirm_action_resumes_agent_and_appends_assistant_message(monkeypatch, tmp_path):
    """Stub the agent to first interrupt, then complete on resume. Verify the
    confirm endpoint produces a final assistant message with agent_phase=completed."""
    # Bootstrap (mirror existing make_client fixture).
    ...
    # Use monkeypatch on agent_service.deep_agent to script invoke / resume behavior.
    ...
    # First respond → interim assistant message with agent_phase=awaiting_confirmation.
    ...
    # POST /api/chat/threads/{tid}/messages/{mid}/actions/{aid}/confirm
    ...
    # Assert the response is an AgentMessageOut with agent_phase=completed.


def test_dismiss_action_resumes_agent_with_reject(monkeypatch, tmp_path):
    """Same as above but the user dismisses; resume completes with rejection logged."""
    ...
```

The detailed bootstrap should follow what the existing `make_client(tmp_path)` and tests in `tests/test_api.py` do; the monkeypatching of `agent_service.deep_agent` should expose `invoke(payload, config=...)` returning two different shapes on first call vs second call. Use a small recorder class.

- [ ] **Step 2: Run, verify they fail**

Run: `pytest tests/test_api.py -v -k "confirm_action_resumes or dismiss_action_resumes"`
Expected: 2 failures (endpoint not yet rewired or dismiss endpoint missing).

- [ ] **Step 3: Rewrite the confirm endpoint and add the dismiss endpoint**

In `backend/app/main.py`, locate the existing `confirm_agent_action` (around line 255 in the current source) and replace it together with `_execute_confirmed_agent_action` and any helpers exclusive to it (`_pricing_results_markdown_table`, etc.) with the following block:

```python
from langchain_core.messages import AIMessage  # near top, with other LC imports

from .services.deep_agent.hitl import build_resume_command
# (also keep existing imports: Command from langgraph.types if needed via build_resume_command)


def _resume_action(
    *,
    thread_id: int,
    message_id: int,
    action_id: str,
    decision: str,
    session: Session,
) -> AgentMessage:
    if agent_service.deep_agent is None:
        raise HTTPException(status_code=503, detail="Agent is disabled (no LLM configured)")

    source_message = session.query(AgentMessage).filter(AgentMessage.id == message_id).one_or_none()
    if source_message is None or source_message.role != "assistant":
        raise HTTPException(status_code=400, detail="Only assistant action proposals can be resumed")
    source_meta = deepcopy(source_message.meta or {})
    pending_actions = source_meta.get("pending_actions") or []
    action = next((a for a in pending_actions if a.get("id") == action_id), None)
    if action is None:
        raise HTTPException(status_code=404, detail="Pending action not found")
    if action.get("status") != "pending":
        raise HTTPException(status_code=409, detail=f"Action already {action.get('status')}")

    cmd = build_resume_command(
        decision=("approve" if decision == "confirm" else "reject"),
        message=("User dismissed the action." if decision == "dismiss" else None),
    )

    try:
        result = agent_service.deep_agent.invoke(
            cmd,
            config={"configurable": {"thread_id": str(thread_id)}},
        )
    except Exception as exc:
        logger.exception("Resume failed for thread %s action %s", thread_id, action_id)
        raise HTTPException(status_code=502, detail=f"Agent resume failed: {exc}") from exc

    # Patch the source message's pending_actions[i].status in place.
    new_status = "confirmed" if decision == "confirm" else "dismissed"
    for entry in pending_actions:
        if entry.get("id") == action_id:
            entry["status"] = new_status
            entry["resolved_at"] = datetime.utcnow().isoformat()
    source_message.meta = {**source_meta, "pending_actions": pending_actions}

    record_audit(
        session,
        event_type=("agent.action.confirmed" if decision == "confirm" else "agent.action.dismissed"),
        actor="desk_user",
        subject_type="thread",
        subject_id=thread_id,
        payload={"action_id": action_id, "tool_name": action.get("tool_name") or action.get("type")},
    )

    # Persist the agent's resume result (may itself be another pause for multi-pause turns).
    thread = session.query(AgentThread).filter(AgentThread.id == thread_id).one()
    return agent_service._persist_agent_result(session, thread, result, assets=[], page_context=None)


@app.post(
    "/api/chat/threads/{thread_id}/messages/{message_id}/actions/{action_id}/confirm",
    response_model=AgentMessageOut,
)
def confirm_agent_action(
    thread_id: int,
    message_id: int,
    action_id: str,
    session: Session = Depends(get_db),
):
    msg = _resume_action(
        thread_id=thread_id,
        message_id=message_id,
        action_id=action_id,
        decision="confirm",
        session=session,
    )
    session.commit()
    return msg


@app.post(
    "/api/chat/threads/{thread_id}/messages/{message_id}/actions/{action_id}/dismiss",
    response_model=AgentMessageOut,
)
def dismiss_agent_action(
    thread_id: int,
    message_id: int,
    action_id: str,
    session: Session = Depends(get_db),
):
    msg = _resume_action(
        thread_id=thread_id,
        message_id=message_id,
        action_id=action_id,
        decision="dismiss",
        session=session,
    )
    session.commit()
    return msg
```

Then **delete** the entire `_execute_confirmed_agent_action(...)` function and any helper that becomes unreferenced (`_pricing_results_markdown_table`, etc. — confirm with `grep`). Also remove the import of any helper that only those branches used.

- [ ] **Step 4: Run, verify the new tests pass and existing confirm tests still pass**

Run: `pytest tests/test_api.py -v`
Expected: New tests pass; existing tests that asserted the old confirm behavior need updating in Step 5 below.

- [ ] **Step 5: Update existing confirm tests in `tests/test_api.py`**

Walk every test referencing `pending_actions[*].type` and rename to `tool_name`. Walk every assertion of `_execute_confirmed_agent_action` behavior and replace with the new resume-driven assertion. Where a test asserted that the confirm response `content` contained a specific tool result (e.g. "Pricing run #1 completed"), assert instead that the resume response has `agent_phase` equal to `"completed"` and that the appropriate audit row exists.

Run: `pytest tests/test_api.py -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/main.py tests/test_api.py
git commit -m "feat(agent): rewire confirm endpoint to Command(resume=...) + add dismiss"
```

---

## Task 13: Stream events from real LangGraph events

**Files:**
- Modify: `backend/app/services/agents.py` (`stream_response` rewrite)
- Modify: `backend/app/main.py` (the `messages/stream` endpoint adapts to async generator if needed)
- Test: extend `tests/test_streaming.py`

- [ ] **Step 1: Skim the current streaming wiring**

Run: `grep -n "stream_response\|messages/stream" backend/app/main.py backend/app/services/agents.py | head`

Identify how SSE is produced today (the existing `stream_response` yields strings; FastAPI wraps them).

- [ ] **Step 2: Write a failing test**

Add to `tests/test_streaming.py`:

```python
def test_stream_response_emits_real_tool_events(monkeypatch, tmp_path):
    """When the agent produces tool calls, stream_response should yield
    `event: status` lines naming the tool, then `data:` token chunks, then `event: done`."""
    from langchain_core.messages import AIMessage, AIMessageChunk

    # Stub agent_service.deep_agent.astream_events to yield a known event sequence.
    async def fake_astream(payload, config=None, version="v2"):
        yield {"event": "on_tool_start", "name": "calculate_risk"}
        yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="Hel")}}
        yield {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="lo")}}
        yield {"event": "on_tool_end", "name": "calculate_risk"}

    # Wire fake_astream into the agent_service singleton.
    ...
    # Drive AgentService.stream_response and collect output.
    ...
    # Assert ordering: 'calculate_risk starting' -> token data -> 'calculate_risk done' -> [DONE].
```

(Adapt to whatever async harness `tests/test_streaming.py` already uses.)

- [ ] **Step 3: Run, verify it fails**

Run: `pytest tests/test_streaming.py -v -k stream_response_emits_real_tool_events`
Expected: failure (current implementation yields static strings).

- [ ] **Step 4: Replace `stream_response`**

In `backend/app/services/agents.py`, add an async streaming method on `AgentService`:

```python
async def stream_response(
    self,
    *,
    thread_id: int,
    content: str,
    page_context: AgentPageContext | None = None,
    requested_character: str = "auto",
):
    """Yield SSE-shaped strings for FastAPI's StreamingResponse.

    Emits:
    - `event: status\\ndata: <name> starting\\n\\n` on tool start
    - `event: status\\ndata: <name> done\\n\\n` on tool end
    - `data: <token>\\n\\n` for chat-model token chunks
    - `event: interrupt\\ndata: <json>\\n\\n` if a pause occurs mid-stream
    - `event: done\\ndata: [DONE]\\n\\n` at the end
    """
    if self.deep_agent is None:
        yield f"data: {_DISABLED_RESPONSE}\n\n"
        yield "event: done\ndata: [DONE]\n\n"
        return

    context: dict[str, Any] = {}  # streaming endpoint can pull DB context as needed
    prompt = _orchestrator_user_prompt(content, requested_character, context)
    config = {"configurable": {"thread_id": str(thread_id)}}

    async for event in self.deep_agent.astream_events(
        {"messages": [HumanMessage(content=prompt)]},
        config=config,
        version="v2",
    ):
        kind = event.get("event")
        if kind == "on_tool_start":
            yield f"event: status\ndata: {event.get('name', 'tool')} starting\n\n"
        elif kind == "on_tool_end":
            yield f"event: status\ndata: {event.get('name', 'tool')} done\n\n"
        elif kind == "on_chat_model_stream":
            chunk = event.get("data", {}).get("chunk")
            text = getattr(chunk, "content", None) if chunk is not None else None
            if isinstance(text, str) and text:
                yield f"data: {text}\n\n"

    yield "event: done\ndata: [DONE]\n\n"
```

Update the SSE endpoint in `main.py` (`/api/chat/threads/{tid}/messages/stream`) to:
1. Call `agent_service.respond(...)` synchronously to persist the user msg, the assistant msg, and (if HITL pause) the pending_actions — this gives a stable conversation log.
2. Then stream the assistant message contents using `agent_service.stream_response(...)`.

If a single endpoint that does both is awkward, split into "POST starts response (returns assistant_id)" + "GET streams assistant_id". Existing UI may already rely on the combined endpoint; if so, keep the combined behavior and emit both the persisted message and the live stream.

- [ ] **Step 5: Run, verify the test passes**

Run: `pytest tests/test_streaming.py -v -k stream_response_emits_real_tool_events`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/agents.py backend/app/main.py tests/test_streaming.py
git commit -m "feat(agent): stream real LangGraph tool/token events via SSE"
```

---

## Task 14: Frontend — `tool_name` rename, generic ActionProposal renderer, dismiss POST

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ActionProposal.tsx`
- Modify: `frontend/src/components/ActionProposal.test.tsx`
- Modify: `frontend/src/routes/AgentDesk.live.tsx`
- Modify: `frontend/src/routes/AgentDesk.live.test.tsx`

- [ ] **Step 1: Update the AgentActionProposal type**

In `frontend/src/types.ts`:

```ts
export interface AgentActionProposal {
  id: string;
  tool_name: string;
  /** Legacy field still present on rows from before the refactor. */
  type?: string;
  label: string;
  summary: string;
  payload?: Record<string, unknown>;
  requires_confirmation?: boolean;
  status?: 'pending' | 'confirmed' | 'dismissed' | 'failed';
  persona?: 'trader' | 'risk_manager' | 'high_board';
  risk_level?: 'read' | 'write' | 'irreversible';
}

export const proposalToolName = (proposal: AgentActionProposal): string =>
  proposal.tool_name ?? proposal.type ?? 'unknown_tool';
```

- [ ] **Step 2: Refactor `ActionProposal.tsx` into a generic renderer**

Rewrite `frontend/src/components/ActionProposal.tsx`:

```tsx
import type { AgentActionProposal } from '../types';
import { proposalToolName } from '../types';
import './ActionProposal.css';

interface Props {
  proposal: AgentActionProposal;
  onConfirm: () => void;
  onDismiss: () => void;
  disabled?: boolean;
}

export function ActionProposal({ proposal, onConfirm, onDismiss, disabled }: Props) {
  const status = proposal.status ?? 'pending';
  const isPending = status === 'pending';
  const toolName = proposalToolName(proposal);

  return (
    <div className={`wl-action wl-action--${status} wl-action--${proposal.risk_level ?? 'unknown'}`}>
      <div className="wl-action__head">
        <strong className="wl-action__label">{proposal.label}</strong>
        {proposal.persona && (
          <span className="wl-action__persona">{proposal.persona.replace('_', ' ')}</span>
        )}
        <code className="wl-action__tool">{toolName}</code>
      </div>
      <div className="wl-action__summary">{proposal.summary}</div>
      <details className="wl-action__args">
        <summary>Arguments</summary>
        <pre>{JSON.stringify(proposal.payload ?? {}, null, 2)}</pre>
      </details>
      <div className="wl-action__buttons">
        <button onClick={onConfirm} disabled={!isPending || disabled}>
          {status === 'confirmed' ? 'Confirmed' : 'Confirm'}
        </button>
        <button onClick={onDismiss} disabled={!isPending || disabled}>
          {status === 'dismissed' ? 'Dismissed' : 'Dismiss'}
        </button>
      </div>
    </div>
  );
}
```

(Keep the `.css` file; the new modifier classes can default to inherited styles.)

- [ ] **Step 3: Update `ActionProposal.test.tsx`**

Replace assertions about `action.type` with `tool_name`. Add a legacy-fallback test:

```tsx
test('falls back to legacy type field when tool_name is missing', () => {
  const proposal = {
    id: 'old', type: 'approve_rfq', label: 'Approve RFQ', summary: '...'
  } as unknown as AgentActionProposal;

  render(<ActionProposal proposal={proposal} onConfirm={() => {}} onDismiss={() => {}} />);

  expect(screen.getByText('approve_rfq')).toBeInTheDocument();
});
```

- [ ] **Step 4: Wire dismiss POST in `AgentDesk.live.tsx`**

Find the existing confirm POST (around line 130 in the current file) and add a sibling dismiss helper. Both should accept the response message (which is itself an AgentMessageOut, possibly with new pending_actions for multi-pause turns) and append it to the thread:

```ts
async function dismissAction(threadId: number, messageId: number, actionId: string) {
  const response = await fetch(
    `/api/chat/threads/${threadId}/messages/${messageId}/actions/${actionId}/dismiss`,
    { method: 'POST' },
  );
  if (!response.ok) throw new Error(`dismiss failed: ${response.status}`);
  const newAssistantMessage: AgentMessageOut = await response.json();
  appendMessageToThread(threadId, newAssistantMessage);
  setThreads((prev) => markActionStatus(prev, messageId, actionId, 'dismissed'));
}
```

Hook up the dismiss button on each `<ActionProposal>` to call this helper.

- [ ] **Step 5: Update `AgentDesk.live.test.tsx`**

Add a test for the dismiss flow and a test for multi-pause appending. Both follow the same shape as the existing confirm test but mock the appropriate fetch response.

- [ ] **Step 6: Run frontend tests**

Run: `npm --prefix frontend test -- --run`
Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts \
        frontend/src/components/ActionProposal.tsx \
        frontend/src/components/ActionProposal.test.tsx \
        frontend/src/routes/AgentDesk.live.tsx \
        frontend/src/routes/AgentDesk.live.test.tsx
git commit -m "feat(frontend): generic ActionProposal renderer + dismiss flow"
```

---

## Task 15: CLI command — reset agent state

**Files:**
- Modify: `backend/app/cli.py`

- [ ] **Step 1: Read the existing CLI**

Run: `cat backend/app/cli.py`

Note the framework (`typer` / `argparse` / etc.) and existing command shape.

- [ ] **Step 2: Add the command**

Append a new command to `backend/app/cli.py`:

```python
def reset_agent_state(args=None):
    """Delete the agent checkpoint sqlite file (next request creates a fresh DB)."""
    import os
    settings = get_settings()
    path = settings.agent_checkpoint_db_path
    if path == ":memory:":
        print("agent_checkpoint_db_path is :memory:; nothing on disk to reset.")
        return
    if os.path.exists(path):
        os.remove(path)
        print(f"Removed {path}")
    else:
        print(f"No checkpoint file at {path}; already clean.")
```

Register the command according to the existing CLI's pattern (e.g. add to a Typer app with `@app.command()` or extend the argparse parser).

- [ ] **Step 3: Smoke-run it**

Run: `python -m backend.app.cli reset-agent-state`
Expected: Prints either "No checkpoint file..." or "Removed ...".

- [ ] **Step 4: Commit**

```bash
git add backend/app/cli.py
git commit -m "feat(agent): add CLI command to reset agent checkpoint store"
```

---

## Task 16: Integration tests with a scripted model

**Files:**
- Create: `tests/test_agent_integration.py`
- Create: `tests/conftest_scripted_model.py` (helper, used by the integration test)

- [ ] **Step 1: Build the scripted model fixture**

Create `tests/conftest_scripted_model.py`:

```python
"""A BaseChatModel stub that replays a recorded sequence of AIMessages with tool_calls."""
from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class ScriptedToolModel(BaseChatModel):
    """Returns the next scripted AIMessage on each invocation, regardless of input."""

    script: list[AIMessage]
    _cursor: int = 0

    def __init__(self, script: list[AIMessage], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "script", list(script))
        object.__setattr__(self, "_cursor", 0)

    @property
    def _llm_type(self) -> str:
        return "scripted-tool-model"

    def bind_tools(self, tools, **kwargs):  # noqa: D401 — required interface
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self._cursor >= len(self.script):
            ai = AIMessage(content="(no more scripted turns)")
        else:
            ai = self.script[self._cursor]
            object.__setattr__(self, "_cursor", self._cursor + 1)
        return ChatResult(generations=[ChatGeneration(message=ai)])
```

- [ ] **Step 2: Write the four integration tests**

Create `tests/test_agent_integration.py`. The tests share a common bootstrap that builds an `AgentService` whose `model` is a `ScriptedToolModel`, and whose `deep_agent` is built with that model + an in-memory `SqliteSaver`.

Test 1 — happy path through trader:

```python
def test_orchestrator_dispatches_to_trader_and_completes(...):
    """Script turn 1: orchestrator emits task(name='trader', prompt='...').
    Script turn 2 (subagent): trader emits AIMessage with tool_calls=[price_product(...)].
    Script turn 3 (subagent): trader emits final AIMessage('Quoted at 12.34').
    Script turn 4 (orchestrator): emits final AIMessage synthesizing.
    Assert the assistant AgentMessage has agent_phase='completed' and personas_invoked includes 'trader'."""
```

Test 2 — single HITL pause through risk_manager + resume:

```python
def test_run_risk_pauses_for_hitl_then_completes_on_resume(...):
    """Script: orchestrator → task('risk_manager') → tool_call(run_risk, portfolio_id=X) → PAUSE.
    First respond() returns assistant message with agent_phase='awaiting_confirmation' and one
    pending action whose tool_name == 'run_risk' and id ends ':0'.
    Resume via Command(resume={'decisions':[{'type':'approve'}]}).
    Continuation script: tool result fed back, risk_manager final, orchestrator final.
    Second message has agent_phase='completed'; an audit row event_type='risk.run' exists with source='agent_confirmed'."""
```

Test 3 — multi-pause turn:

```python
def test_multi_pause_turn_chains_run_risk_then_approve_rfq(...):
    """Script: orchestrator → task('risk_manager') → tool_call(run_risk) → PAUSE → resume(approve)
    → risk_manager final → orchestrator → task('high_board') → tool_call(approve_rfq) → PAUSE
    → resume(approve) → high_board final → orchestrator final.
    Assert three assistant messages in the thread; statuses in pending_actions update to 'confirmed'
    on the right messages; final message agent_phase='completed'."""
```

Test 4 — dismiss path:

```python
def test_dismiss_pending_approve_rfq_completes_with_acknowledgement(...):
    """Script ends with high_board tool_call(approve_rfq) → PAUSE.
    Resume via Command(resume={'decisions':[{'type':'reject','message':'User dismissed.'}]}).
    Continuation script: high_board acknowledges dismissal in final AIMessage.
    Assert the final assistant message contains 'dismissed' acknowledgement; pending_actions[0].status='dismissed'."""
```

For each test, monkeypatch `agent_service.deep_agent.invoke` if necessary to drive the scripted model deterministically. The exact invocation flow depends on how deepagents wires the orchestrator + subagent task tool; the implementer should verify against the installed `deepagents` version with a tiny REPL session before writing each test.

- [ ] **Step 3: Run, fix until green**

Run: `pytest tests/test_agent_integration.py -v`
Expected: 4 passed.

- [ ] **Step 4: Commit**

```bash
git add tests/test_agent_integration.py tests/conftest_scripted_model.py
git commit -m "test(agent): add scripted-model integration tests covering HITL flows"
```

---

## Task 17: Cleanup pass — delete dead code and update remaining tests

**Files:**
- Modify: `tests/test_agent_tools.py`, `tests/test_api.py` (residual cleanup)
- Modify: `backend/app/services/agents.py` (audit for any orphan helpers)
- Sweep: any `process_events` static-string references; any `agent_graph: deterministic` checks; any `_propose_actions` references in tests

- [ ] **Step 1: Sweep for dead references**

Run: `grep -rn "deterministic\|_propose_actions\|_route_character\|_persona_response\|PERSISTED_ACTION_TOOL_NAMES\|_execute_confirmed_agent_action\|build_zenmux_model\|process_events" backend/ tests/ frontend/src/ | grep -v ".pyc" | grep -v "node_modules"`

For each hit, confirm it's either gone or intentionally kept (e.g. the spec doc itself referencing the old name). Delete dead code; for tests update the assertions to the new world.

- [ ] **Step 2: Run the full backend suite**

Run: `pytest -q`
Expected: All tests pass.

- [ ] **Step 3: Run the full frontend suite**

Run: `npm --prefix frontend test -- --run`
Expected: All tests pass.

- [ ] **Step 4: Run a manual smoke against a dev server**

Boot the backend with `ZENMUX_API_KEY` set:

```bash
cd backend && uvicorn app.main:app --reload
```

Boot the frontend (`npm --prefix frontend run dev`). Open the desk; send these messages in three different threads:

1. "What can you tell me about the latest market for AAPL?" — expect a trader-routed reply, no pending action.
2. "Run risk on portfolio 1." — expect a `run_risk` pending action card; click Confirm; expect a final assistant message with risk metrics.
3. "Run risk on portfolio 1 and approve RFQ-1 if VaR is fine." — expect `run_risk` pending; confirm; then `approve_rfq` pending; confirm; final summary message.

If any step misbehaves, capture the failure and address it before committing the smoke note.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(agent): cleanup dead refs after deepagent refactor"
```

---

## Self-Review

**1. Spec coverage:**

| Spec § | Task |
|---|---|
| §3 Architecture / module layout | Tasks 2, 3, 4, 5, 9, 10, 11 |
| §4.1 Orchestrator | Task 10 |
| §4.2 Personas | Tasks 9, 10 |
| §4.3 Tools (4 new + bind existing 3) | Tasks 7, 8 |
| §4.4 Model factory | Task 2 |
| §4.5 Checkpointer | Task 3 |
| §4.6 HITL helpers | Tasks 4, 5 |
| §5.1 Happy path | Task 11 |
| §5.2 Pause path | Task 11 |
| §5.3 Resume path (confirm + dismiss) | Task 12 |
| §5.4 Multi-pause turns | Task 16 (Test 3) |
| §5.5 Streaming events | Task 13 |
| §5.6 Page context | Task 11 |
| §6.1 AgentActionProposal schema | Task 6 |
| §6.2 Meta key evolution | Tasks 11, 12 |
| §6.3 New input schemas | Task 7 |
| §6.4 Settings | Task 1 |
| §6.5 Endpoints | Task 12 |
| §6.6 Frontend changes | Task 14 |
| §6.7 No migration | (no task — confirmed by absence) |
| §6.8 Backwards compat shim | Task 6 |
| §7 Error handling — disabled stub, fail loud | Tasks 11, 12 |
| §7.5 HITL edge cases | Task 12 |
| §7.6 CLI reset | Task 15 |
| §8 Testing strategy | Tasks 2–7, 11, 13, 16, 17 |
| §9 Observability — logger names, audit events | Tasks 7, 11, 12 |
| §10 Rollout — single coordinated PR | Implicit in commit cadence |
| §11 Out of scope | (no task) |

No gaps detected.

**2. Placeholder scan:**

- The two `...` markers in Task 11 Step 3 (`_context_assets` and `_lightweight_portfolio_summary` bodies) are explicit "copy verbatim from the previous source" instructions — not placeholders. Acceptable because the engineer is told exactly where the body lives.
- Task 7 Step 6 mentions `create_portfolio_report` and tells the engineer to grep for the actual API. This is a small, contained "verify against current code" instruction — acceptable.
- Task 13 Step 1 asks the engineer to grep before writing; Task 12 Step 5 asks them to walk every test referencing `action.type` — both are deliberate sweep instructions, not laziness.
- No "TBD", "TODO", "implement later", or "add validation" red flags.

**3. Type consistency:**

- `AgentActionProposal` fields used in Task 6 (`id`, `tool_name`, `label`, `summary`, `payload`, `requires_confirmation`, `status`, `persona`, `risk_level`) match the projection in Task 5 and the frontend type in Task 14. ✓
- `interrupt_on_config()` shape (Task 4) matches `interrupt_on=interrupt_on` parameter in Task 10 orchestrator builder. ✓
- `build_resume_command(decision, message=...)` (Task 5) matches the call in Task 12 (`decision="approve"|"reject"`). ✓
- `_persist_agent_result(session, thread, result, assets, page_context)` (Task 11) signature matches its callers in Task 12 (`assets=[], page_context=None`) and is reused for both first-turn and resume. ✓
- `pending_actions_from_interrupts(interrupts, persona=...)` keyword matches Task 11's call. ✓
- Frontend `proposalToolName(proposal)` (Task 14) is consistent with the optional-`type`/required-`tool_name` schema from Task 6. ✓

No drift detected.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-08-deepagent-refactor.md`.

Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration. Good fit here because tasks 2–10 are mostly independent (model factory, checkpointer, HITL helpers, prompts, personas) and can be executed in parallel batches; tasks 11–17 are sequential and benefit from per-task review.
2. **Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints. Lower coordination overhead, but the main context will fill up around Task 11 (large rewrite of `agents.py`).

Which approach?
