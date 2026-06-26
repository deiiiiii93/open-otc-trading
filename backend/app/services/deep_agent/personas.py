"""Persona SubAgent spec factories.

All three personas hold the *same full tool list* (per the design spec
locked decision); differentiation is via system prompt only. HITL gates
the persisted/state-mutating tools at runtime.

System prompts are composed from `prompts/<persona>.md` (identity + tools +
output style + routing-from-skills directive) plus a per-persona allowlist of
policy fragments from `skills/meta/`. See `skills_loader.py`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents.middleware.subagents import SubAgent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from .envelope_skills import EnvelopeSkillsMiddleware
from .persona_domains import workflow_skill_sources
from .skills_loader import compose_persona_prompt

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def report_currency_instruction(report_currency: str) -> str:
    """Declarative instruction block telling a persona how to treat report currency."""
    if report_currency and report_currency != "by_position":
        return (
            f"REPORT CURRENCY: All monetary risk is reported in {report_currency}. "
            f"When you present aggregate money figures from a risk run's `by_currency` "
            f"block, first call the `convert_currency` tool with that block, the target "
            f"`{report_currency}`, and the run's valuation date. Never sum money metrics "
            f"across currencies yourself; the `shared` block (delta/gamma) is "
            f"currency-invariant and needs no conversion."
        )
    return (
        "REPORT CURRENCY: by position. Present each currency group from a risk run's "
        "`by_currency` block separately. Do NOT convert or cross-sum monetary metrics "
        "across currencies."
    )


# Policy fragment allowlists per persona. Order is the order they appear in
# the composed prompt.
# `escalation-policy` leads every allowlist: envelope widening is automatic,
# but only fires when the model ATTEMPTS a gated tool. A page-scoped (pet) turn
# that declines in prose never raises the denial that would widen its scope, so
# the directive to "attempt, don't refuse" must be in-context for all personas.
_TRADER_POLICY = (
    "escalation-policy",
    "read-before-compute-policy",
    "cost-preview-policy",
    "reply-options-policy",
    "yolo-hitl-policy",
    "clarification-policy",
    "python-analysis-policy",
)
_RISK_POLICY = _TRADER_POLICY
_BOARD_POLICY = (
    "escalation-policy",
    "cost-preview-policy",
    "reply-options-policy",
    "yolo-hitl-policy",
    "clarification-policy",
    "python-analysis-policy",
)


def _load_identity(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _resolve_policy_fragments(
    fragments: Sequence[str], allow_reply_options: bool
) -> tuple[str, ...]:
    """In headless (YOLO) mode, swap the reply-options policy for the headless
    policy so the persona never asks or proposes cards."""
    if allow_reply_options:
        return tuple(fragments)
    return tuple(
        "headless-policy" if f == "reply-options-policy" else f for f in fragments
    )


def _spec(
    *,
    name: str,
    description: str,
    prompt_file: str,
    tools: Sequence[BaseTool],
    policy_fragments: Sequence[str],
    skills: Sequence[str],
    allow_reply_options: bool = True,
) -> SubAgent:
    return {
        "name": name,
        "description": description,
        "system_prompt": compose_persona_prompt(
            identity_prompt=_load_identity(prompt_file),
            policy_fragment_names=_resolve_policy_fragments(
                policy_fragments, allow_reply_options
            ),
        ),
        "tools": list(tools),
        "skills": list(skills),
        # model and middleware inherit from the parent orchestrator.
    }


def trader_spec(
    model: BaseChatModel, tools: Sequence[BaseTool], *, allow_reply_options: bool = True
) -> SubAgent:
    return _spec(
        name="trader",
        description=(
            "Quotes, pricing, RFQ solving, market snapshots. "
            "Reads stored valuations and uses run_batch_pricing for explicit "
            "batch repricing (one run also persists fresh risk metrics)."
        ),
        prompt_file="trader.md",
        tools=tools,
        policy_fragments=_TRADER_POLICY,
        skills=workflow_skill_sources("trader"),
        allow_reply_options=allow_reply_options,
    )


def risk_spec(
    model: BaseChatModel, tools: Sequence[BaseTool], *, allow_reply_options: bool = True
) -> SubAgent:
    return _spec(
        name="risk_manager",
        description=(
            "Limits, exposure, hedge feasibility. "
            "Reads stored risk and uses run_batch_pricing for explicit audited "
            "persisted runs (risk metrics + position valuations in one pass)."
        ),
        prompt_file="risk_manager.md",
        tools=tools,
        policy_fragments=_RISK_POLICY,
        skills=workflow_skill_sources("risk_manager"),
        allow_reply_options=allow_reply_options,
    )


def board_spec(
    model: BaseChatModel, tools: Sequence[BaseTool], *, allow_reply_options: bool = True
) -> SubAgent:
    return _spec(
        name="high_board",
        description=(
            "Release/approve, reporting. "
            "Uses report-generation artifacts and approve/reject/release tools."
        ),
        prompt_file="high_board.md",
        tools=tools,
        policy_fragments=_BOARD_POLICY,
        skills=workflow_skill_sources("high_board"),
        allow_reply_options=allow_reply_options,
    )


def all_personas(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    *,
    skills_backend: Any | None = None,
    yolo_mode: bool = False,
    allow_reply_options: bool = True,
) -> list[SubAgent]:
    specs: list[SubAgent] = [
        trader_spec(model, tools, allow_reply_options=allow_reply_options),
        risk_spec(model, tools, allow_reply_options=allow_reply_options),
        board_spec(model, tools, allow_reply_options=allow_reply_options),
    ]
    if skills_backend is None:
        return specs

    from .cost_preview_hitl import LongRunningCostHITLMiddleware
    from .tool_error_boundary import ToolErrorBoundaryMiddleware

    for spec in specs:
        sources = list(spec.get("skills", []))
        spec["skills"] = []
        # SkillsMiddleware's state generic (SkillsState) is invariant against
        # AgentMiddleware[AgentState, ...] declared on the SubAgent TypedDict.
        # The runtime accepts the wider type; pyright cannot prove variance.
        middleware = list(spec.get("middleware", []))
        # Outermost (index 0): each persona is the inner ToolNode that runs domain
        # tools (book_position, …). Convert tool-body exceptions into error
        # ToolMessages here so a domain ValueError reaches the LLM instead of
        # crashing the subagent — and thus the whole orchestrator resume.
        middleware.insert(0, ToolErrorBoundaryMiddleware())
        if yolo_mode:
            middleware.append(LongRunningCostHITLMiddleware(tools=tools))
        middleware.append(EnvelopeSkillsMiddleware(backend=skills_backend, sources=sources))
        spec["middleware"] = middleware  # pyright: ignore[reportGeneralTypeIssues]
    return specs
