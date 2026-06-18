"""Frozen Phase 3 skill-rewrite regression fixtures.

These fixtures lock the post-rewrite workflow catalog against the behavior
covered by the original Desk/Pet thread failures and the compound routing
contracts. They intentionally avoid LLM wording assertions; the stable contract
is envelope, workflow availability, tool sequence, and key factual handles.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from deepagents.middleware.skills import _list_skills
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool

from app.schemas import AgentPageContext, LoadedContext, PageAction
from app.services.agents import AgentService
from app.services.deep_agent.envelopes import Envelope
from app.services.deep_agent.orchestrator import _build_backend
from app.services.deep_agent.orchestrator import build_orchestrator
from app.services.deep_agent.skills_paths import SKILLS_ROOT, WORKFLOWS_DIR


@dataclass(frozen=True)
class ExpectedToolCall:
    name: str
    arg_needles: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrozenPromptFixture:
    name: str
    prompt: str
    page_context: AgentPageContext | None
    input_envelope: str | None
    expected_envelope: Envelope
    workflows: tuple[str, ...]
    tool_sequence: tuple[ExpectedToolCall, ...]
    key_facts: tuple[str, ...]


class RuntimeFakeModel(FakeMessagesListChatModel):
    def bind_tools(
        self, tools: list[Any], *, tool_choice: str | None = None, **kwargs: Any
    ):
        object.__setattr__(
            self,
            "bound_tool_names",
            [getattr(tool, "name", str(tool)) for tool in tools],
        )
        return self


def _task_call(fixture: FrozenPromptFixture) -> dict:
    persona = _persona_for_fixture(fixture)
    workflow = fixture.workflows[0].split("/")[-1]
    return {
        "name": "task",
        "args": {
            "description": f"Use `{workflow}`. {fixture.prompt}",
            "subagent_type": persona,
        },
        "id": f"call_task_{fixture.name}",
        "type": "tool_call",
    }


def _tool_call(expected: ExpectedToolCall, fixture: FrozenPromptFixture) -> dict:
    return {
        "name": expected.name,
        "args": _tool_args(expected, fixture),
        "id": f"call_{fixture.name}_{expected.name}",
        "type": "tool_call",
    }


def _persona_for_fixture(fixture: FrozenPromptFixture) -> str:
    if fixture.name == "risk_page_rerun":
        return "risk_manager"
    if fixture.name == "snowball_book_audit":
        return "risk_manager"
    return "trader"


def _tool_args(expected: ExpectedToolCall, fixture: FrozenPromptFixture) -> dict[str, Any]:
    args: dict[str, Any] = {}
    text = _contract_text(fixture)
    if "portfolio_id" in expected.arg_needles and (
        '"portfolio_id": 6' in text or "portfolio 6" in text.lower()
    ):
        args["portfolio_id"] = 6
    if "position_id" in expected.arg_needles and "21" in expected.arg_needles:
        args["position_id"] = 21
    if "pricing_parameter_profile_id" in expected.arg_needles and (
        '"pricing_parameter_profile_id": 3' in text or "profile 3" in text
    ):
        args["pricing_parameter_profile_id"] = 3
    if "000905.SH" in expected.arg_needles:
        args["symbol"] = "000905.SH"
    if "rfq_id" in expected.arg_needles and "42" in expected.arg_needles:
        args["rfq_id"] = 42
    if expected.name == "run_batch_pricing":
        args.setdefault("method", "summary")
    return args


def _runtime_responses(fixture: FrozenPromptFixture) -> list[AIMessage]:
    final = " ".join(fixture.key_facts)
    if not fixture.tool_sequence:
        return [AIMessage(content=final)]

    responses = [
        AIMessage(content="", tool_calls=[_task_call(fixture)]),
    ]
    for expected in fixture.tool_sequence:
        responses.append(AIMessage(content="", tool_calls=[_tool_call(expected, fixture)]))
    responses.extend(
        [
            AIMessage(content=f"Subagent result: {final}"),
            AIMessage(content=final),
        ]
    )
    return responses


def _runtime_tools(
    fixture: FrozenPromptFixture,
) -> tuple[list[StructuredTool], list[tuple[str, dict]]]:
    calls: list[tuple[str, dict]] = []

    def make_tool(name: str) -> StructuredTool:
        def tool_func(
            portfolio_id: int | None = None,
            position_id: int | None = None,
            pricing_parameter_profile_id: int | None = None,
            symbol: str | None = None,
            rfq_id: int | None = None,
            method: str | None = None,
            limit: int | None = None,
            position_ids: list[int] | None = None,
            **kwargs: Any,
        ) -> dict:
            payload = {
                key: value
                for key, value in locals().items()
                if key not in {"kwargs", "name", "calls"} and value is not None
            }
            calls.append((name, payload))
            return {"ok": True, "tool": name, "args": payload}

        return StructuredTool.from_function(
            func=tool_func,
            name=name,
            description=f"Deterministic fake {name} tool for runtime fixture tests.",
        )

    names = sorted({expected.name for expected in fixture.tool_sequence})
    return [make_tool(name) for name in names], calls


def _page_context(
    *,
    route: str,
    title: str,
    entity_ids: dict[str, int | str | None],
    snapshot: dict,
    completeness: str = "complete",
    actions: tuple[PageAction, ...] = (),
) -> AgentPageContext:
    return AgentPageContext(
        route=route,
        title=title,
        entity_ids=entity_ids,
        snapshot=snapshot,
        loaded_context=LoadedContext(
            completeness=completeness,
            visible_count=snapshot.get("visible_count"),
            total_count=snapshot.get("total_count"),
            query_ref=snapshot.get("query_ref"),
        ),
        actions=list(actions),
    )


FROZEN_FIXTURES: tuple[FrozenPromptFixture, ...] = (
    FrozenPromptFixture(
        name="positions_count_from_complete_page",
        prompt="How many positions do we have?",
        page_context=_page_context(
            route="positions",
            title="Positions",
            entity_ids={"portfolio_id": 6},
            snapshot={"visible_count": 64, "total_count": 64},
        ),
        input_envelope=None,
        expected_envelope=Envelope.PET_PAGE,
        workflows=("positions/position-snapshot",),
        tool_sequence=(),
        key_facts=("64", "portfolio_id", "6"),
    ),
    FrozenPromptFixture(
        name="position_21_diagnostic_followup",
        prompt="what's the delta of position 21? why is it so big?",
        page_context=_page_context(
            route="positions",
            title="Positions",
            entity_ids={"portfolio_id": 6, "position_id": 21},
            snapshot={"selected_position": {"id": 21, "delta": 1420000}},
        ),
        input_envelope="pet_diagnostic",
        expected_envelope=Envelope.PET_DIAGNOSTIC,
        workflows=("positions/position-snapshot", "positions/position-diagnosis"),
        tool_sequence=(
            ExpectedToolCall("get_positions", ("position_id", "21")),
            ExpectedToolCall("get_latest_position_valuations", ("portfolio_id", "6")),
        ),
        key_facts=("21", "delta", "1420000"),
    ),
    FrozenPromptFixture(
        name="risk_page_rerun",
        prompt="rerun risk",
        page_context=_page_context(
            route="risk",
            title="Risk Dashboard",
            entity_ids={"portfolio_id": 6, "pricing_parameter_profile_id": 3},
            snapshot={"latest_task_id": 91},
            actions=(
                PageAction(
                    name="run_batch_pricing",
                    required_ids=["portfolio_id"],
                    confirmation="explicit",
                    backend_endpoint="POST /api/batch-pricing/runs",
                ),
            ),
        ),
        input_envelope="desk_workflow",
        expected_envelope=Envelope.DESK_WORKFLOW,
        workflows=("risk/run-risk",),
        tool_sequence=(
            ExpectedToolCall("get_positions", ("portfolio_id", "6")),
            ExpectedToolCall(
                "run_batch_pricing",
                ("portfolio_id", "6", "pricing_parameter_profile_id", "3"),
            ),
        ),
        key_facts=("risk", "portfolio_id", "6", "3"),
    ),
    FrozenPromptFixture(
        name="try_solve_snowball_missing_terms",
        prompt="price a Snowball product with 000852.SH, 3Y, KO 103%, KI 75%",
        page_context=_page_context(
            route="try-solve",
            title="Try Solve",
            entity_ids={"row_id": "row-17"},
            snapshot={
                "row_id": "row-17",
                "underlying": "000852.SH",
                "tenor": "3Y",
                "ko": "103%",
                "ki": "75%",
                "status": "missing_terms",
            },
            actions=(
                PageAction(
                    name="solve_imported_row",
                    required_ids=["row_id"],
                    confirmation="implicit",
                    backend_endpoint="POST /api/rfq/try-solve/solve",
                ),
            ),
        ),
        input_envelope=None,
        expected_envelope=Envelope.PET_PAGE,
        workflows=("try-solve/solve-imported-row",),
        tool_sequence=(),
        key_facts=("row-17", "000852.SH", "KO 103%", "KI 75%"),
    ),
    FrozenPromptFixture(
        name="portfolio_6_repricing",
        prompt="Reprice portfolio 6 with profile 3.",
        page_context=None,
        input_envelope=None,
        expected_envelope=Envelope.DESK_WORKFLOW,
        workflows=("pricing/price-portfolio",),
        tool_sequence=(
            ExpectedToolCall(
                "run_batch_pricing",
                ("portfolio_id", "6", "pricing_parameter_profile_id", "3"),
            ),
        ),
        key_facts=("portfolio 6", "profile 3"),
    ),
    FrozenPromptFixture(
        name="market_data_drift_then_reprice",
        prompt="Check market-data drift for 000905.SH in portfolio 6, then reprice if needed.",
        page_context=None,
        input_envelope=None,
        expected_envelope=Envelope.DESK_WORKFLOW,
        workflows=(
            "market-data/fetch-market-data",
            "market-data/explain-market-data-drift",
            "pricing/price-portfolio",
        ),
        tool_sequence=(
            ExpectedToolCall("fetch_market_snapshot", ("000905.SH",)),
            ExpectedToolCall("run_batch_pricing", ("portfolio_id", "6")),
        ),
        key_facts=("000905.SH", "portfolio 6"),
    ),
    FrozenPromptFixture(
        name="snowball_book_audit",
        prompt="Audit the Snowball book for portfolio 6 and explain pricing and risk drivers.",
        page_context=None,
        input_envelope=None,
        expected_envelope=Envelope.DESK_WORKFLOW,
        workflows=(
            "positions/position-snapshot",
            "risk/read-risk-result",
            "snowballs/snowball-pricing",
            "snowballs/snowball-risk-explain",
        ),
        tool_sequence=(
            ExpectedToolCall("get_positions", ("portfolio_id", "6")),
            ExpectedToolCall("get_latest_position_valuations", ("portfolio_id", "6")),
            ExpectedToolCall("get_latest_risk_run", ("portfolio_id", "6")),
        ),
        key_facts=("Snowball", "portfolio 6"),
    ),
    FrozenPromptFixture(
        name="rfq_quote_and_submit",
        prompt="Client wants RFQ 42 quoted and submitted for approval.",
        page_context=None,
        input_envelope=None,
        expected_envelope=Envelope.DESK_WORKFLOW,
        workflows=(
            "rfq/intake-request",
            "rfq/quote-rfq",
            "rfq/submit-for-approval",
        ),
        tool_sequence=(
            ExpectedToolCall("get_rfq_catalog", ()),
            ExpectedToolCall("solve_rfq", ("rfq_id", "42")),
            ExpectedToolCall("quote_rfq", ("rfq_id", "42")),
            ExpectedToolCall("submit_rfq_for_approval", ("rfq_id", "42")),
        ),
        key_facts=("RFQ 42", "approval"),
    ),
)


def _workflow_path(workflow: str) -> Path:
    return WORKFLOWS_DIR / workflow / "SKILL.md"


def _workflow_body(workflow: str) -> str:
    return _workflow_path(workflow).read_text(encoding="utf-8")


def _contract_text(fixture: FrozenPromptFixture) -> str:
    context = (
        fixture.page_context.model_dump(mode="json")
        if fixture.page_context is not None
        else {}
    )
    return fixture.prompt + "\n" + json.dumps(context, sort_keys=True)


def test_frozen_fixture_set_has_eight_prompts() -> None:
    assert len(FROZEN_FIXTURES) == 8
    assert len({fixture.name for fixture in FROZEN_FIXTURES}) == 8


@pytest.mark.parametrize("fixture", FROZEN_FIXTURES, ids=lambda fixture: fixture.name)
def test_frozen_fixtures_select_expected_envelopes(
    fixture: FrozenPromptFixture,
) -> None:
    service = AgentService.__new__(AgentService)

    assert (
        service._resolve_envelope(fixture.input_envelope, fixture.page_context)
        is fixture.expected_envelope
    )


@pytest.mark.parametrize("fixture", FROZEN_FIXTURES, ids=lambda fixture: fixture.name)
def test_frozen_fixtures_use_runtime_workflow_catalog(
    fixture: FrozenPromptFixture,
) -> None:
    backend = _build_backend()

    for workflow in fixture.workflows:
        domain, skill = workflow.split("/", maxsplit=1)
        assert _workflow_path(workflow).is_file()
        assert not (SKILLS_ROOT / "legacy" / workflow / "SKILL.md").exists()

        names = {
            item["name"]
            for item in _list_skills(backend, f"/skills/workflows/{domain}/")
        }
        assert skill in names


@pytest.mark.parametrize("fixture", FROZEN_FIXTURES, ids=lambda fixture: fixture.name)
def test_frozen_fixtures_keep_tool_sequence_contracts(
    fixture: FrozenPromptFixture,
) -> None:
    combined_workflow_text = "\n".join(
        _workflow_body(workflow) for workflow in fixture.workflows
    )
    contract_text = _contract_text(fixture)

    cursor = -1
    for expected in fixture.tool_sequence:
        next_index = combined_workflow_text.find(expected.name, cursor + 1)
        assert next_index > cursor, f"{fixture.name}: missing {expected.name}"
        cursor = next_index
        for needle in expected.arg_needles:
            assert needle in contract_text or needle in combined_workflow_text


@pytest.mark.parametrize("fixture", FROZEN_FIXTURES, ids=lambda fixture: fixture.name)
def test_frozen_fixtures_keep_key_facts(fixture: FrozenPromptFixture) -> None:
    contract_text = _contract_text(fixture)

    for fact in fixture.key_facts:
        assert fact in contract_text


@pytest.mark.parametrize("fixture", FROZEN_FIXTURES, ids=lambda fixture: fixture.name)
def test_frozen_fixtures_execute_runtime_graph(
    fixture: FrozenPromptFixture,
) -> None:
    tools, calls = _runtime_tools(fixture)
    model = RuntimeFakeModel(responses=_runtime_responses(fixture))
    graph = build_orchestrator(
        model=model,
        tools=tools,
        checkpointer=None,
        interrupt_on={},
    )

    result = graph.invoke(
        {"messages": [("human", fixture.prompt)]},
        config={
            "configurable": {
                "thread_id": f"skill-rewrite-{fixture.name}",
                "envelope": fixture.expected_envelope.value,
            },
            "recursion_limit": 50,
        },
    )

    assert [name for name, _args in calls] == [
        expected.name for expected in fixture.tool_sequence
    ]
    assert [args for _name, args in calls] == [
        _tool_args(expected, fixture) for expected in fixture.tool_sequence
    ]
    final = result["messages"][-1].content
    for fact in fixture.key_facts:
        assert fact in final
