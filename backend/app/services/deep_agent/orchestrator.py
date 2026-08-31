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
    from .fs_policy import build_scoped_artifacts_backend

    skills_fs = FilesystemBackend(root_dir=str(_SKILLS_FS_ROOT), virtual_mode=True)
    # Scoped, not plain: restricted subtrees refused, agent/thread-<N>/
    # visible only to its own thread (see fs_policy.py for the run #133
    # measurement that forced this).
    artifacts_fs = build_scoped_artifacts_backend()
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

    from .fs_policy import restricted_artifact_permissions

    return [
        # Restricted stores first — _check_fs_permission is first-match-wins,
        # so these must precede the /artifacts/** read allow below.
        *restricted_artifact_permissions(),
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


def _general_purpose_subagent(
    tools: Sequence[BaseTool], *, yolo_mode: bool = False
) -> dict[str, Any]:
    """Claim the `general-purpose` name so our guards reach that stack too.

    `create_deep_agent` auto-adds a general-purpose subagent whose middleware it
    builds itself — `[TodoList, Filesystem, summarization, PatchToolCalls]` plus
    harness-profile extras. **Nothing we pass as `middleware=` reaches it**, so
    it is a fourth agent stack that the "all three stacks" registration tests
    never covered, and it holds the parent's full toolset.

    That is not theoretical. On the first `confirmation-desk-day` board,
    `gemini-3.7-flash` delegated to this subagent and it issued three
    `read_file` calls, one of them `conf-08-mixed-text-and-scan-amd.pdf` — the
    unrecoverable-400 shape `BinaryReadGuardMiddleware` exists to prevent. It
    was also running **unaudited**, which contradicts the audit trail's
    always-on contract.

    Supplying our own spec is the documented override ("an explicit spec is how
    callers override the default"), and it is behaviour-preserving by
    construction: for a caller-supplied spec deepagents PREPENDS the same base
    middleware stack, and `spec.get("interrupt_on", interrupt_on)` /
    `spec.get("tools") if "tools" in spec else tools` mean that **omitting**
    those two keys inherits exactly what the auto-added agent would have got —
    including the filesystem-permission interrupt merge. So we deliberately do
    not set them; setting `interrupt_on` here would silently narrow write
    gating on a subagent that can book.

    `yolo_mode` adds the cost-preview gate for the same reason every persona
    gets it: this subagent inherits the parent's toolset, so it can start a
    long-running priced run, and the auto-added version never carried that gate
    either. Two existing suite assertions walk *every* subagent and require it —
    they passed before only because the auto-added agent was invisible to them,
    which is the same "not covered, not compliant" gap this function closes.

    `skills: []` matches what `all_personas` sets. Behaviourally it is identical
    to omitting the key (deepagents reads `spec.get("skills")` and treats `None`
    and `[]` alike), but the explicit empty list states that skills reach this
    stack through our own middleware, not deepagents'.

    Scope note: this carries the persona stack's *head* only. Ground-truth
    capture, booking-card capture and the Case-3 fan-out read-only gate are
    NOT added — each has semantics that deserve their own review rather than a
    drive-by here.
    """
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    from .audit_trail_middleware import AuditTrailMiddleware
    from .binary_read_guard import BinaryReadGuardMiddleware
    from .cost_preview_hitl import LongRunningCostHITLMiddleware
    from .tool_error_boundary import ToolErrorBoundaryMiddleware

    middleware: list[Any] = [
        ToolErrorBoundaryMiddleware(),
        AuditTrailMiddleware(tools=tools),
        BinaryReadGuardMiddleware(),
    ]
    if yolo_mode:
        middleware.append(LongRunningCostHITLMiddleware(tools=tools))

    return {
        **GENERAL_PURPOSE_SUBAGENT,
        "skills": [],
        "middleware": middleware,
    }


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
    from .binary_read_guard import BinaryReadGuardMiddleware
    from .booking_capture import BookingResultMiddleware
    from .compaction import LedgerScopedCompactionMiddleware
    from .cost_preview_hitl import LongRunningCostHITLMiddleware
    from .desk_context import DeskContextMiddleware
    from .run_python_hitl import RunPythonArtifactHITLMiddleware
    from .tool_error_boundary import ToolErrorBoundaryMiddleware
    from .ground_truth import (
        GroundTruthArtifactMiddleware,
        compaction_protected_tool_names,
    )

    # Outermost (first = outermost): convert any tool-body exception into an error
    # ToolMessage so the agent recovers instead of crashing the run. Interrupts
    # (GraphBubbleUp) still propagate. See tool_error_boundary for the rationale.
    # Just inside the boundary: always-on dangerous-action audit (audit spec §5.2a).
    middleware: list[Any] = [
        ToolErrorBoundaryMiddleware(),
        AuditTrailMiddleware(tools=tools),
        # Same seam, same reason: a booking made inside a persona subagent is
        # invisible to result-message scanning (separate checkpoint namespace).
        BookingResultMiddleware(),
        # Same seam, third reason: deepagents' read_file returns raw bytes as a
        # media content block, which three of four measured routes reject with a
        # 400 that the history can never recover from. Replace it with text that
        # names the tool to use instead. See binary_read_guard.
        BinaryReadGuardMiddleware(),
        GroundTruthArtifactMiddleware(tools=tools),
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
            LedgerScopedCompactionMiddleware(
                model=model,
                backend=backend,
                ground_truth_tool_names=compaction_protected_tool_names(tools),
            ),
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


def _orchestrator_tools(
    tools: Sequence[BaseTool], *, allow_reply_options: bool
) -> list[BaseTool]:
    """Tools the ORCHESTRATOR itself holds (personas get the full ``tools``).

    Besides the UI-control ``propose_reply_options`` card (non-headless only),
    the orchestrator needs progressive artifact disclosure plus ``record_answer``:
    grounding/answer follow-ups
    ("what's the hotspot?", "what is the CVaR?") are synthesized by the
    orchestrator DIRECTLY from context — it delegates the domain tool-work to a
    persona, then produces the final answer itself. Without the recorder here the
    orchestrator can only answer in prose (it literally reports "record_answer
    isn't available in my toolset"), so every ``answer_field_*`` check scores 0.
    Pull the already scope-gated instance out of ``tools`` rather than
    re-registering it.
    """
    from ..reply_options.tool import ProposeReplyOptionsTool
    from .artifact_access import ARTIFACT_DISCLOSURE_TOOL_NAMES

    out: list[BaseTool] = [ProposeReplyOptionsTool()] if allow_reply_options else []
    direct_names = ARTIFACT_DISCLOSURE_TOOL_NAMES | {"record_answer"}
    out += [t for t in tools if getattr(t, "name", None) in direct_names]
    return out


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

    # Orchestrator has no business-domain tools, but it needs reply options,
    # progressive artifact reads, and record_answer — see _orchestrator_tools.
    # Personas receive their own copy of everything through ``tools``.
    orchestrator_tools = _orchestrator_tools(
        tools, allow_reply_options=allow_reply_options
    )
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
        subagents=[
            *all_personas(
                model,
                persona_tools,
                skills_backend=backend,
                yolo_mode=yolo_mode,
                allow_reply_options=allow_reply_options,
            ),
            # Claim `general-purpose` so deepagents does not auto-add an
            # unguarded, unaudited one. See _general_purpose_subagent.
            _general_purpose_subagent(persona_tools, yolo_mode=yolo_mode),
        ],
        interrupt_on=interrupt_on if interrupt_on is not None else interrupt_on_config(),
        checkpointer=checkpointer,
        backend=backend,
        permissions=_filesystem_permissions(),
        # P3.8: compound routing is covered by explicit prompt contracts and
        # router tests, not by a runtime routing skill catalog.
        skills=[],
        name="otc_desk_orchestrator",
    )
