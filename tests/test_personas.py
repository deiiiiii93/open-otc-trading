from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.orchestrator import build_orchestrator, _filesystem_permissions
from app.services.deep_agent.checkpointer import build_checkpointer
from app.services.deep_agent.hitl import interrupt_on_config
from app.config import Settings
import dataclasses


class _FakeModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _all_tool_names(spec: dict) -> set[str]:
    return {t.name for t in spec["tools"]}


def test_trader_spec_carries_full_tool_set_and_nonempty_prompt():
    from app.tools import QUANT_AGENT_TOOLS

    model = _FakeModel(responses=[AIMessage(content="ok")])
    spec = trader_spec(model, QUANT_AGENT_TOOLS)

    assert spec["name"] == "trader"
    assert spec["system_prompt"]
    assert "trader" in spec["system_prompt"].lower()
    assert "propose_reply_options" in spec["system_prompt"]
    assert _all_tool_names(spec) == {t.name for t in QUANT_AGENT_TOOLS}


def test_all_personas_carry_delegated_scope_policy():
    """Every persona subagent prompt must instruct it to treat orchestrator-
    supplied scope as authoritative (satisfying required_context) — the fix for
    subagents blocking on 'missing required scope' when the id was in the prompt."""
    from app.tools import QUANT_AGENT_TOOLS

    model = _FakeModel(responses=[AIMessage(content="ok")])
    for spec in (
        trader_spec(model, QUANT_AGENT_TOOLS),
        risk_spec(model, QUANT_AGENT_TOOLS),
        board_spec(model, QUANT_AGENT_TOOLS),
    ):
        p = spec["system_prompt"].lower()
        assert "required_context" in p, f"{spec['name']} missing required_context guidance"
        assert "authoritative" in p, f"{spec['name']} missing authoritative-scope guidance"


def test_risk_and_board_specs_have_distinct_names_and_prompts():
    from app.tools import QUANT_AGENT_TOOLS

    model = _FakeModel(responses=[AIMessage(content="ok")])
    risk = risk_spec(model, QUANT_AGENT_TOOLS)
    board = board_spec(model, QUANT_AGENT_TOOLS)

    assert risk["name"] == "risk_manager"
    assert board["name"] == "high_board"
    assert risk["system_prompt"] != board["system_prompt"]
    assert "propose_reply_options" in risk["system_prompt"]
    assert "propose_reply_options" in board["system_prompt"]


def test_build_orchestrator_registers_all_three_personas():
    from app.tools import QUANT_AGENT_TOOLS

    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")
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


def test_build_orchestrator_keeps_deepagents_delta_channels():
    """DeepAgents v0.6+ should own DeltaChannel wiring for growing state."""
    from langgraph.channels import DeltaChannel

    from app.tools import QUANT_AGENT_TOOLS

    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")
    model = _FakeModel(responses=[AIMessage(content="hello")])
    checkpointer = build_checkpointer(settings)

    graph = build_orchestrator(
        model=model,
        tools=QUANT_AGENT_TOOLS,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on_config(),
    )

    channels = getattr(graph, "channels", {})
    for channel_name in ("messages", "files"):
        channel = channels.get(channel_name)
        assert isinstance(channel, DeltaChannel)
        assert getattr(channel, "snapshot_frequency", None) == 50


def test_orchestrator_receives_propose_reply_options_tool(monkeypatch):
    """The orchestrator prompt instructs the model to call
    ``propose_reply_options`` before offering choice replies, so the
    orchestrator keeps its own copy even though personas also receive one.
    """
    from app.tools import QUANT_AGENT_TOOLS

    captured: dict = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)

        class _Dummy:
            name = kwargs.get("name", "")

        return _Dummy()

    # `create_deep_agent` is imported inside `build_orchestrator`, so patch the
    # canonical import path. The function-local import resolves to the fake.
    monkeypatch.setattr("deepagents.create_deep_agent", _fake_create)

    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")
    model = _FakeModel(responses=[AIMessage(content="hello")])
    checkpointer = build_checkpointer(settings)

    build_orchestrator(
        model=model,
        tools=QUANT_AGENT_TOOLS,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on_config(),
    )

    orchestrator_tool_names = {getattr(t, "name", None) for t in captured.get("tools", [])}
    assert "propose_reply_options" in orchestrator_tool_names
    # Domain tools should still be scoped to persona subagents, not the orchestrator.
    assert "price_product" not in orchestrator_tool_names
    assert "run_batch_pricing" not in orchestrator_tool_names


def test_orchestrator_can_enable_quickjs_code_interpreter_middleware(monkeypatch):
    from app.tools import QUANT_AGENT_TOOLS

    captured: dict = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)

        class _Dummy:
            name = kwargs.get("name", "")

        return _Dummy()

    monkeypatch.setattr("deepagents.create_deep_agent", _fake_create)

    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")
    model = _FakeModel(responses=[AIMessage(content="hello")])
    checkpointer = build_checkpointer(settings)

    build_orchestrator(
        model=model,
        tools=QUANT_AGENT_TOOLS,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on_config(),
        enable_code_interpreter=True,
    )

    middleware = captured.get("middleware") or []
    assert [type(item).__name__ for item in middleware] == [
        "ToolErrorBoundaryMiddleware",
        "AuditTrailMiddleware",
        "DeskContextMiddleware",
        "RunPythonArtifactHITLMiddleware",
        "LedgerScopedCompactionMiddleware",
        "TermGroundingMiddleware",
        "EvalAttributionGateMiddleware",
        "CodeInterpreterMiddleware",
    ]
    ci = middleware[7]
    # task() is exposed via subagents=True (default), NOT ptc=["task"] (which the
    # lib rejects); the per-eval backstop is lowered to 24.
    assert not getattr(ci, "_ptc")
    assert getattr(ci, "_max_ptc_calls") == 24
    assert getattr(ci, "_subagents") is True


def test_orchestrator_installs_ledger_scoped_compaction_middleware(monkeypatch):
    from app.tools import QUANT_AGENT_TOOLS

    captured: dict = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)

        class _Dummy:
            name = kwargs.get("name", "")

        return _Dummy()

    monkeypatch.setattr("deepagents.create_deep_agent", _fake_create)

    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")
    model = _FakeModel(responses=[AIMessage(content="hello")])
    checkpointer = build_checkpointer(settings)

    build_orchestrator(
        model=model,
        tools=QUANT_AGENT_TOOLS,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on_config(),
    )

    middleware_names = [type(item).__name__ for item in captured.get("middleware", [])]
    assert middleware_names == [
        "ToolErrorBoundaryMiddleware",
        "AuditTrailMiddleware",
        "DeskContextMiddleware",
        "RunPythonArtifactHITLMiddleware",
        "LedgerScopedCompactionMiddleware",
        "TermGroundingMiddleware",
    ]


def test_orchestrator_yolo_installs_long_running_hitl_on_parent_and_personas(monkeypatch):
    from app.tools import QUANT_AGENT_TOOLS

    captured: dict = {}

    def _fake_create(**kwargs):
        captured.update(kwargs)

        class _Dummy:
            name = kwargs.get("name", "")

        return _Dummy()

    monkeypatch.setattr("deepagents.create_deep_agent", _fake_create)

    settings = dataclasses.replace(Settings(), agent_checkpoint_db_path=":memory:")
    model = _FakeModel(responses=[AIMessage(content="hello")])
    checkpointer = build_checkpointer(settings)

    build_orchestrator(
        model=model,
        tools=QUANT_AGENT_TOOLS,
        checkpointer=checkpointer,
        interrupt_on=interrupt_on_config(yolo_mode=True),
        yolo_mode=True,
    )

    middleware_names = [type(item).__name__ for item in captured.get("middleware", [])]
    # ToolErrorBoundaryMiddleware is always outermost; the yolo HITL middleware
    # sits just inside it on the parent.
    assert middleware_names[0] == "ToolErrorBoundaryMiddleware"
    assert "LongRunningCostHITLMiddleware" in middleware_names
    for spec in captured["subagents"]:
        persona_middleware_names = [
            type(item).__name__ for item in spec.get("middleware", [])
        ]
        assert "LongRunningCostHITLMiddleware" in persona_middleware_names


def test_orchestrator_prompt_uses_task_graph_planning_contract():
    from pathlib import Path

    prompt = (
        Path("backend/app/services/deep_agent/prompts/orchestrator.md")
        .read_text(encoding="utf-8")
    )

    assert "TaskSpec" in prompt
    assert "plan_workflow_step" in prompt
    assert "kind='plan'" in prompt
    assert "The scheduler validates those TaskSpecs" in prompt
    assert "The only tools you should use are `task`" not in prompt


def test_persona_prompts_are_task_workers_that_emit_artifacts():
    from pathlib import Path

    prompts_dir = Path("backend/app/services/deep_agent/prompts")
    for filename in ("trader.md", "risk_manager.md", "high_board.md"):
        prompt = (prompts_dir / filename).read_text(encoding="utf-8")
        assert "Your context pack is your only state" in prompt
        assert "produce typed artifacts" in prompt
        assert "When the task is complete, emit your final artifact and return" in prompt
        assert "The orchestrator decides what becomes truth" in prompt


def test_risk_prompt_requires_profile_choice_before_run_batch_pricing():
    from pathlib import Path

    prompt = Path("backend/app/services/deep_agent/prompts/risk_manager.md").read_text(
        encoding="utf-8"
    )
    skill = Path("backend/app/skills/workflows/risk/run-risk/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "do not propose or call `run_batch_pricing`" in prompt
    assert "Do not pass `null` silently" in prompt
    assert "position_ids" in prompt
    assert "pricing_parameter_profile_choice" in skill
    assert "Do not silently queue `run_batch_pricing`" in skill


def test_portfolio_discovery_tools_are_wired_into_deep_agent():
    """list_portfolios / get_portfolio must be available so the agent can
    resolve a user-named portfolio (e.g. "Snowballs") that is not pinned in
    the current page context. Without these, the agent says "I don't have
    that portfolio in context" instead of looking it up.
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES, select_deep_agent_tools

    assert "list_portfolios" in DEEP_AGENT_TOOL_NAMES
    assert "get_portfolio" in DEEP_AGENT_TOOL_NAMES
    tool_names = {t.name for t in select_deep_agent_tools()}
    assert "list_portfolios" in tool_names
    assert "get_portfolio" in tool_names


def test_run_python_tool_is_wired_into_deep_agent():
    """run_python must be registered and dynamically HITL-gated for artifact writes.
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES, select_deep_agent_tools
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
        run_python_requires_hitl,
    )

    # Available to personas
    assert "run_python" in DEEP_AGENT_TOOL_NAMES
    tool_names = {t.name for t in select_deep_agent_tools()}
    assert "run_python" in tool_names

    # HITL-gated only when the script is allowed to produce artifacts.
    assert "run_python" in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL["run_python"] == "read"
    assert _LABEL_BY_TOOL["run_python"] == "Run Python script"
    assert run_python_requires_hitl({"writes_artifacts": True}) is True
    assert run_python_requires_hitl({"writes_artifacts": False}) is False


def test_book_position_is_wired_into_deep_agent():
    """Direct booking must be reachable by personas AND HITL-gated.

    book-position/SKILL.md instructs the agent to call book_position, but the
    tool only reaches a persona's manifest if it is named in the enforced
    allowlist; and because it persists a new product + position row it must be
    interrupt-gated like book_rfq_to_position.
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES, select_deep_agent_tools
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    # Reachable by personas.
    assert "book_position" in DEEP_AGENT_TOOL_NAMES
    tool_names = {t.name for t in select_deep_agent_tools()}
    assert "book_position" in tool_names

    # HITL-gated as an irreversible persisted write.
    assert "book_position" in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL["book_position"] == "irreversible"
    assert _LABEL_BY_TOOL["book_position"] == "Book position"


def test_snowball_ko_query_tool_is_wired_into_deep_agent():
    from app.services.agents import DEEP_AGENT_TOOL_NAMES, select_deep_agent_tools

    assert "query_snowball_ko_from_spot" in DEEP_AGENT_TOOL_NAMES
    tool_names = {t.name for t in select_deep_agent_tools()}
    assert "query_snowball_ko_from_spot" in tool_names


def test_escalation_policy_states_attempt_not_refuse():
    """The escalation-policy fragment must tell the model HOW escalation is
    triggered: by attempting the tool, not by refusing.

    Escalation is a runtime reaction to a CapabilityDeniedError, which only
    fires when the model actually calls a gated tool. A pet that declines in
    prose ("I have little access to these tools") never raises the denial, so
    the runtime never widens its envelope. The policy must make the mechanism
    explicit.
    """
    from pathlib import Path

    body = Path(
        "backend/app/skills/meta/escalation-policy.md"
    ).read_text(encoding="utf-8")

    assert "call that tool anyway" in body
    assert "Do not refuse" in body
    assert "lack access" in body


def test_persona_prompts_include_escalation_attempt_directive():
    """The escalation-policy must be composed into every persona prompt.

    The fragment exists in skills/meta/ but was orphaned — no persona allowlist
    referenced it, so the model never saw escalation guidance. Wire it into all
    three personas so a page-scoped (pet) turn knows to attempt the desk tool
    and let the runtime widen the envelope.
    """
    from app.tools import QUANT_AGENT_TOOLS

    model = _FakeModel(responses=[AIMessage(content="ok")])
    for spec in (
        trader_spec(model, QUANT_AGENT_TOOLS),
        risk_spec(model, QUANT_AGENT_TOOLS),
        board_spec(model, QUANT_AGENT_TOOLS),
    ):
        assert "call that tool anyway" in spec["system_prompt"], spec["name"]


def test_deep_agent_filesystem_permissions_allow_trading_desk_artifacts():
    from deepagents.middleware.filesystem import _check_fs_permission

    permissions = _filesystem_permissions()

    assert _check_fs_permission(permissions, "read", "/") == "allow"
    assert _check_fs_permission(permissions, "read", "/trading_desk") == "allow"
    assert _check_fs_permission(permissions, "write", "/trading_desk/charts/candle_000852_SH.html") == "allow"
    assert _check_fs_permission(permissions, "read", "/etc/passwd") == "deny"
    assert _check_fs_permission(permissions, "write", "/charts/candle_000852_SH.html") == "deny"
    # deepagents offloads large tool results to /large_tool_results/<tool_call_id>
    # and instructs the agent to read them back. Reads must be allowed; writes
    # to that prefix must NOT be allowed (the offloader owns the writes).
    assert _check_fs_permission(permissions, "read", "/large_tool_results") == "allow"
    assert _check_fs_permission(
        permissions, "read", "/large_tool_results/toolu_01ABCdef"
    ) == "allow"
    assert _check_fs_permission(
        permissions, "write", "/large_tool_results/toolu_01ABCdef"
    ) == "deny"


def test_orchestrator_holds_record_answer_so_it_can_score_answer_fields():
    """Regression: grounding/answer follow-ups are synthesized by the ORCHESTRATOR
    directly (it delegates domain tool-work to a persona, then answers itself), so
    ``record_answer`` must live on the orchestrator toolset — not only the personas.
    Without it the orchestrator answers in prose ("record_answer isn't available in
    my toolset") and every answer_field_* check scores 0 (found live in arena run
    #14). Personas still receive the full toolset separately."""
    from app.services.deep_agent.orchestrator import _orchestrator_tools
    from app.tools import QUANT_AGENT_TOOLS

    # non-headless: recorder + reply-options card both present
    names = {getattr(t, "name", None) for t in
             _orchestrator_tools(QUANT_AGENT_TOOLS, allow_reply_options=True)}
    assert "record_answer" in names
    assert "propose_reply_options" in names

    # headless (YOLO): recorder still present, reply-options card withheld
    names_headless = {getattr(t, "name", None) for t in
                      _orchestrator_tools(QUANT_AGENT_TOOLS, allow_reply_options=False)}
    assert "record_answer" in names_headless
    assert "propose_reply_options" not in names_headless


def test_orchestrator_tools_omits_record_answer_if_toolset_lacks_it():
    """The helper pulls the recorder OUT of the passed toolset (no re-registration),
    so a toolset without it yields an orchestrator without it — never a phantom."""
    from app.services.deep_agent.orchestrator import _orchestrator_tools

    class _T:
        def __init__(self, name):
            self.name = name

    tools = [_T("get_latest_risk_run"), _T("run_scenario_test")]
    names = {getattr(t, "name", None) for t in
             _orchestrator_tools(tools, allow_reply_options=True)}
    assert "record_answer" not in names
    assert "propose_reply_options" in names
