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
from .skills_loader import load_policy_fragments
from .skills_paths import SKILLS_ROOT

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SKILLS_FS_ROOT = SKILLS_ROOT


def _artifacts_root() -> Path:
    """Resolve the artifacts directory at agent build time.

    Sourced from `database.settings.artifact_dir` so the mount tracks
    whatever Settings the app is running under (test fixtures swap settings
    via `database.configure_database`; deployments set OPEN_OTC_ARTIFACT_DIR).
    Falls back to repo-root `artifacts/` if no setting is available.
    """
    try:
        from ... import database

        return Path(database.settings.artifact_dir)
    except Exception:  # pragma: no cover — defensive default
        return Path(__file__).parent.parent.parent.parent.parent / "artifacts"


def _orchestrator_prompt(allow_reply_options: bool = True) -> str:
    from .routing_table import inject_known_skills_table

    base = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8").rstrip()
    base = inject_known_skills_table(base)
    # AUTO/Interactive: teach pickable reply options. YOLO (headless): swap in the
    # headless policy so the model never asks or proposes cards.
    fragment = "reply-options-policy" if allow_reply_options else "headless-policy"
    policy = load_policy_fragments((fragment,))
    return base + "\n\n" + policy


def _build_backend() -> Any:
    """Build a CompositeBackend for skills, artifacts, and large tool blobs.

    Path semantics:
    - Virtual mode on each FilesystemBackend prevents path traversal and pins
      reads to the root directory.
    - CompositeBackend strips the matched prefix when routing, so the
      FilesystemBackend sees paths like
      `/workflows/pricing/price-portfolio/SKILL.md` (for /skills/) or
      `/report-1.html` (for /artifacts/) relative to its own virtual root.
    """
    from deepagents.backends import StateBackend
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend

    from .cas_backend import ContentAddressedFilesystemBackend

    skills_fs = FilesystemBackend(root_dir=str(_SKILLS_FS_ROOT), virtual_mode=True)
    artifacts_fs = FilesystemBackend(
        root_dir=str(_artifacts_root()), virtual_mode=True
    )
    large_tool_results = ContentAddressedFilesystemBackend()
    return CompositeBackend(
        default=StateBackend(),
        routes={
            "/skills/": skills_fs,
            "/artifacts/": artifacts_fs,
            "/large_tool_results/": large_tool_results,
        },
    )


def _filesystem_permissions() -> list[Any]:
    from deepagents.middleware.permissions import FilesystemPermission

    return [
        FilesystemPermission(
            operations=["read"],
            paths=["/"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/trading_desk", "/trading_desk/**"],
            mode="allow",
        ),
        # When a tool result exceeds the model context, deepagents offloads
        # it to /large_tool_results/<tool_call_id> and tells the agent to
        # read it from there. Allow read access; never write.
        FilesystemPermission(
            operations=["read"],
            paths=["/large_tool_results", "/large_tool_results/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/skills", "/skills/**"],
            mode="allow",
        ),
        # v2: /artifacts/ holds persisted report HTML/XLSX. Read-only for all
        # personas; the high_board report-query-and-display skill body
        # governs which artifacts are actually read (HTML yes, XLSX surface-only).
        FilesystemPermission(
            operations=["read"],
            paths=["/artifacts", "/artifacts/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read", "write"],
            paths=["/", "/**"],
            mode="deny",
        ),
    ]


def _agent_middleware(
    enable_code_interpreter: bool,
    *,
    model: BaseChatModel,
    backend: Any,
    tools: Sequence[BaseTool],
    yolo_mode: bool = False,
    goal_grader: Any = None,
) -> list[Any]:
    from .audit_trail_middleware import AuditTrailMiddleware
    from .compaction import LedgerScopedCompactionMiddleware
    from .cost_preview_hitl import LongRunningCostHITLMiddleware
    from .desk_context import DeskContextMiddleware
    from .run_python_hitl import RunPythonArtifactHITLMiddleware
    from .tool_error_boundary import ToolErrorBoundaryMiddleware

    # Outermost (first = outermost): convert any tool-body exception into an error
    # ToolMessage so the agent recovers instead of crashing the run. Interrupts
    # (GraphBubbleUp) still propagate. See tool_error_boundary for the rationale.
    # Just inside the boundary: always-on dangerous-action audit (audit spec §5.2a).
    middleware: list[Any] = [
        ToolErrorBoundaryMiddleware(),
        AuditTrailMiddleware(tools=tools),
        # Snoop resolved scope (portfolio_id, profile_id, dates) from the
        # orchestrator's direct domain-tool calls into desk_context state, which
        # propagates to persona subagents so their required_context is satisfied.
        DeskContextMiddleware(),
    ]
    if yolo_mode:
        middleware.append(LongRunningCostHITLMiddleware(tools=tools))
    middleware.extend(
        [
            RunPythonArtifactHITLMiddleware(enabled=not yolo_mode),
            LedgerScopedCompactionMiddleware(model=model, backend=backend),
        ]
    )
    from .memory.config import get_memory_config
    if get_memory_config().enabled:
        from .memory.runtime import get_memory_middleware
        middleware.append(get_memory_middleware())
    # Ungrounded term-completeness verdicts bounce back once. The orchestrator
    # holds no domain tools, so its nudge resolves by delegating to a persona
    # (see term_grounding.py NUDGE_TEXT).
    from .term_grounding import TermGroundingMiddleware
    middleware.append(TermGroundingMiddleware())
    if not enable_code_interpreter:
        _append_goal_grader(middleware, goal_grader)
        return middleware

    from .dynamic_subagents import MAX_PTC_CALLS
    from .eval_gate import EvalAttributionGateMiddleware
    from langchain_quickjs import (  # pyright: ignore[reportMissingImports]
        CodeInterpreterMiddleware,
    )

    # Pre-eval gate (outer to the interpreter): reject every `eval` unless the run
    # carries server-set Case-3 attribution for an allowlisted workflow.
    middleware.append(EvalAttributionGateMiddleware())

    # `task()` is exposed as a top-level subagent global via subagents=True (the
    # default) — NOT through `ptc` (the lib rejects `ptc=["task"]` at model-call time).
    middleware.append(
        CodeInterpreterMiddleware(
            max_ptc_calls=MAX_PTC_CALLS,  # per-eval backstop (lowered from 64)
            timeout=5.0,
        )
    )
    _append_goal_grader(middleware, goal_grader)
    return middleware


def _append_goal_grader(middleware: list[Any], goal_grader: Any) -> None:
    """Attach the goal-mode acceptance grader last (it gates the agent's finish).

    The caller supplies ``goal_grader`` only while an active goal run is ``running``
    (spec §H activation gate); otherwise the agent has no rubric and runs unchanged.
    """
    if goal_grader is not None:
        middleware.append(goal_grader)


def build_orchestrator(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    interrupt_on: dict[str, Any] | None = None,
    enable_code_interpreter: bool = False,
    yolo_mode: bool = False,
    allow_reply_options: bool = True,
    goal_grader: Any = None,
) -> Any:
    """Create the desk deep-agent orchestrator with three persona subagents.

    ``allow_reply_options`` is False in YOLO (headless) mode: the
    ``propose_reply_options`` card tool is withheld from BOTH the orchestrator
    and the personas so neither can defer to a human, and the system prompt swaps
    in the headless policy.
    """
    from deepagents import create_deep_agent

    # Orchestrator has no DOMAIN tools, but in non-headless modes it also needs
    # the UI-control tool ``propose_reply_options`` so final synthesis replies can
    # attach pickable buttons. Personas receive their own copy through ``tools``.
    from ..reply_options.tool import ProposeReplyOptionsTool
    orchestrator_tools = [ProposeReplyOptionsTool()] if allow_reply_options else []
    # Headless: also strip the card tool from the persona toolset so no subagent
    # can defer either.
    persona_tools = (
        list(tools)
        if allow_reply_options
        else [t for t in tools if getattr(t, "name", None) != "propose_reply_options"]
    )
    backend = _build_backend()

    return create_deep_agent(
        model=model,
        tools=orchestrator_tools,
        system_prompt=_orchestrator_prompt(allow_reply_options),
        middleware=_agent_middleware(
            enable_code_interpreter,
            model=model,
            backend=backend,
            tools=persona_tools,
            yolo_mode=yolo_mode,
            goal_grader=goal_grader,
        ),
        subagents=all_personas(
            model,
            persona_tools,
            skills_backend=backend,
            yolo_mode=yolo_mode,
            allow_reply_options=allow_reply_options,
        ),
        interrupt_on=interrupt_on if interrupt_on is not None else interrupt_on_config(),
        checkpointer=checkpointer,
        backend=backend,
        permissions=_filesystem_permissions(),
        # P3.8: compound routing is covered by explicit prompt contracts and
        # router tests, not by a runtime routing skill catalog.
        skills=[],
        name="otc_desk_orchestrator",
    )
