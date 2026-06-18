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


def _orchestrator_prompt() -> str:
    from .routing_table import inject_known_skills_table

    base = (_PROMPTS_DIR / "orchestrator.md").read_text(encoding="utf-8").rstrip()
    base = inject_known_skills_table(base)
    pickable_options = load_policy_fragments(("reply-options-policy",))
    return base + "\n\n" + pickable_options


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
) -> list[Any]:
    from .compaction import LedgerScopedCompactionMiddleware
    from .cost_preview_hitl import LongRunningCostHITLMiddleware
    from .run_python_hitl import RunPythonArtifactHITLMiddleware
    from .tool_error_boundary import ToolErrorBoundaryMiddleware

    # Outermost (first = outermost): convert any tool-body exception into an error
    # ToolMessage so the agent recovers instead of crashing the run. Interrupts
    # (GraphBubbleUp) still propagate. See tool_error_boundary for the rationale.
    middleware: list[Any] = [ToolErrorBoundaryMiddleware()]
    if yolo_mode:
        middleware.append(LongRunningCostHITLMiddleware(tools=tools))
    middleware.extend(
        [
            RunPythonArtifactHITLMiddleware(enabled=not yolo_mode),
            LedgerScopedCompactionMiddleware(model=model, backend=backend),
        ]
    )
    if not enable_code_interpreter:
        return middleware

    from langchain_quickjs import (  # pyright: ignore[reportMissingImports]
        CodeInterpreterMiddleware,
    )

    middleware.append(
        CodeInterpreterMiddleware(
            ptc=["task"],
            max_ptc_calls=64,
            timeout=5.0,
        )
    )
    return middleware


def build_orchestrator(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    interrupt_on: dict[str, Any] | None = None,
    enable_code_interpreter: bool = False,
    yolo_mode: bool = False,
) -> Any:
    """Create the desk deep-agent orchestrator with three persona subagents."""
    from deepagents import create_deep_agent

    # Orchestrator has no DOMAIN tools, but it also needs the UI-control tool
    # ``propose_reply_options`` so final synthesis replies can attach pickable
    # buttons. Personas receive their own copy through ``tools``.
    from ..reply_options.tool import ProposeReplyOptionsTool
    orchestrator_tools = [ProposeReplyOptionsTool()]
    backend = _build_backend()

    return create_deep_agent(
        model=model,
        tools=orchestrator_tools,
        system_prompt=_orchestrator_prompt(),
        middleware=_agent_middleware(
            enable_code_interpreter,
            model=model,
            backend=backend,
            tools=tools,
            yolo_mode=yolo_mode,
        ),
        subagents=all_personas(
            model,
            tools,
            skills_backend=backend,
            yolo_mode=yolo_mode,
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
