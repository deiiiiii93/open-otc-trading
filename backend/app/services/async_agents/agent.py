"""build_async_agent — flat deep-agent builder for async-agent dispatch.

Mirrors backend/app/services/deep_agent/orchestrator.py:build_orchestrator,
but without persona subagents (flat) and with a broader skills allowlist
plus per-task scratch write permission.
"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from ..deep_agent.envelope_skills import (
    EnvelopeSkillsMiddleware,
    WORKFLOW_SKILL_SOURCES,
)
from ..deep_agent.envelopes import Envelope
from ..deep_agent.cost_preview_hitl import LongRunningCostHITLMiddleware
from ..deep_agent.hitl import interrupt_on_config
from ..deep_agent.run_python_hitl import RunPythonArtifactHITLMiddleware
from ..deep_agent.skills_loader import compose_persona_prompt
from ..deep_agent.skills_paths import SKILLS_ROOT
from .policy import ASYNC_POLICY_FRAGMENTS, scratch_dir_for_task

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SKILLS_FS_ROOT = SKILLS_ROOT


def _artifacts_root() -> Path:
    try:
        from ... import database

        return Path(database.settings.artifact_dir)
    except Exception:  # pragma: no cover
        return Path(__file__).parent.parent.parent.parent.parent / "artifacts"


def _identity_prompt() -> str:
    return (_PROMPTS_DIR / "async_agent.md").read_text(encoding="utf-8")


def _build_backend() -> Any:
    """Same CompositeBackend shape as build_orchestrator: routes /skills/
    and /artifacts/ to FilesystemBackends, CAS for large tool blobs."""
    from deepagents.backends import StateBackend
    from deepagents.backends.composite import CompositeBackend
    from deepagents.backends.filesystem import FilesystemBackend

    from ..deep_agent.cas_backend import ContentAddressedFilesystemBackend

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


def _filesystem_permissions(*, task_id: int | str) -> list[Any]:
    """Read-everywhere, write-only-to-per-task-scratch."""
    from deepagents.middleware.permissions import FilesystemPermission

    scratch = scratch_dir_for_task(task_id).rstrip("/")
    return [
        FilesystemPermission(operations=["read"], paths=["/"], mode="allow"),
        FilesystemPermission(
            operations=["read", "write"],
            paths=[scratch, f"{scratch}/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"],
            paths=["/large_tool_results", "/large_tool_results/**"],
            mode="allow",
        ),
        FilesystemPermission(
            operations=["read"], paths=["/skills", "/skills/**"], mode="allow"
        ),
        FilesystemPermission(
            operations=["read"], paths=["/artifacts", "/artifacts/**"], mode="allow"
        ),
        FilesystemPermission(
            operations=["read", "write"], paths=["/", "/**"], mode="deny"
        ),
    ]


def _interrupt_on_for_async(*, yolo_mode: bool = False) -> dict[str, Any]:
    """Async agent uses the same interrupt_on map as personas."""
    return interrupt_on_config(yolo_mode=yolo_mode)


def build_async_agent(
    *,
    model: BaseChatModel,
    tools: Sequence[BaseTool],
    checkpointer: Any,
    task_id: int | str,
    yolo_mode: bool = False,
) -> Any:
    """Build a flat deep-agent for one async-agent task.

    Same tool list as personas. Broad workflow skills allowlist. Filesystem
    permission grants write to the per-task scratch under
    /trading_desk/async/<task_id>/.
    """
    from deepagents import create_deep_agent

    from ..deep_agent.audit_trail_middleware import AuditTrailMiddleware
    from ..deep_agent.ground_truth import GroundTruthArtifactMiddleware

    backend = _build_backend()
    # Head of the stack: always-on dangerous-action audit (audit spec §5.2a) —
    # background async agents run write tools too.
    middleware: list[Any] = [
        AuditTrailMiddleware(tools=tools),
        GroundTruthArtifactMiddleware(tools=tools),
    ]
    if yolo_mode:
        middleware.append(LongRunningCostHITLMiddleware(tools=tools))
    middleware.extend(
        [
            RunPythonArtifactHITLMiddleware(enabled=not yolo_mode),
            EnvelopeSkillsMiddleware(
                backend=backend,
                sources=WORKFLOW_SKILL_SOURCES,
                default_envelope=Envelope.DESK_ASYNC,
            ),
        ]
    )
    return create_deep_agent(
        model=model,
        tools=list(tools),
        system_prompt=compose_persona_prompt(
            identity_prompt=_identity_prompt(),
            policy_fragment_names=ASYNC_POLICY_FRAGMENTS,
        ),
        subagents=[],  # flat — no personas
        interrupt_on=_interrupt_on_for_async(yolo_mode=yolo_mode),
        checkpointer=checkpointer,
        backend=backend,
        permissions=_filesystem_permissions(task_id=task_id),
        skills=[],
        middleware=middleware,
        name=f"async_agent_{task_id}",
    )
