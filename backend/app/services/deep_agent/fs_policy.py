"""Shared /artifacts/ filesystem policy for every agent stack.

Measured on arena run #133 (2026-08-31): the blanket-read ``/artifacts/``
mount let one contestant read other contestants' arena transcripts
(``/artifacts/arena/**`` — the grade book), raw CAS blobs
(``/artifacts/artifact_blobs/**`` — bypassing the workflow-scoped
``/large_tool_results/`` route AND the sanctioned ``list_artifacts`` /
``read_artifact`` progressive-disclosure tools), and other threads' desk
reports (``/artifacts/agent/thread-N/**``). Desk threads 1 and 3 exhibited
the blob bypass organically, so this is a desk invariant, not arena hygiene.

Two layers, and BOTH must be consumed by BOTH backend builders
(``deep_agent/orchestrator._build_backend`` and
``async_agents/agent._build_backend`` keep parallel copies of the mount —
fixing one stack alone leaves the other as the next open door, the same
lesson as the auto-added general-purpose subagent):

- :func:`restricted_artifact_permissions` — static deny rules for subtrees
  no agent may ever raw-read. Expressible as build-time permissions because
  the paths are constants.
- :class:`ScopedArtifactsBackend` — the dynamic own-thread rule for
  ``agent/thread-<N>/``. This CANNOT be a build-time permission: the
  default-selection orchestrator graph is built once and reused across every
  thread, so caller identity must resolve per operation (the same reason the
  CAS backend resolves scope per read).

``tests/test_fs_policy.py`` pins both layers in both stacks, mirroring
``test_audit_registration.py``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.backends.protocol import (
    GlobResult,
    GrepResult,
    LsResult,
    ReadResult,
)

#: Subtrees of the artifacts root that are never agent-readable:
#: - ``arena``: match transcripts — benchmark evidence, i.e. the grade book.
#: - ``artifact_blobs``: the CAS store; agents read captured results through
#:   the scoped /large_tool_results/ route or the artifact tools only.
#: - ``sandbox_sessions``: run_python session state (written by
#:   ``sandbox_tool``); results reach the agent through the tool result.
RESTRICTED_ARTIFACT_SUBTREES: tuple[str, ...] = (
    "arena",
    "artifact_blobs",
    "sandbox_sessions",
)

_THREAD_DIR_RE = re.compile(r"^agent/thread-(\d+)(?:/|$)")


def artifacts_root() -> Path:
    """Resolve the artifacts directory at agent build time.

    Sourced from ``database.settings.artifact_dir`` so the mount tracks
    whatever Settings the app is running under; falls back to repo-root
    ``artifacts/`` if no setting is available.
    """
    try:
        from ... import database

        return Path(database.settings.artifact_dir)
    except Exception:  # pragma: no cover — defensive default
        return Path(__file__).resolve().parents[4] / "artifacts"


def restricted_artifact_permissions() -> list[Any]:
    """Deny rules for the restricted subtrees, as seen from the tool layer.

    Must be placed BEFORE the ``/artifacts/**`` read allow in a permission
    list — deepagents' ``_check_fs_permission`` is first-match-wins.
    """
    from deepagents.middleware.permissions import FilesystemPermission

    rules: list[Any] = []
    for subtree in RESTRICTED_ARTIFACT_SUBTREES:
        rules.append(
            FilesystemPermission(
                operations=["read", "write"],
                paths=[f"/artifacts/{subtree}", f"/artifacts/{subtree}/**"],
                mode="deny",
            )
        )
    return rules


class ScopedArtifactsBackend(FilesystemBackend):
    """The /artifacts/ filesystem view, scoped to the calling thread.

    Paths arrive in this backend's own coordinate system (CompositeBackend
    strips the ``/artifacts/`` route prefix), so thread workspaces appear as
    ``/agent/thread-<N>/...``. Reads, listings, globs and greps of a foreign
    thread's workspace are refused/filtered; the restricted subtrees are
    refused here as well, as belt-and-braces under the permission layer
    (``ls_info``/``glob_info`` funnel through ``ls``/``glob``, so filtering
    the core four also covers the permission-interrupt path).

    Identity resolves per operation from the active graph config (or the
    desk-execution ContextVar): ``AUDIT_CONTEXT_KEY['thread_id']`` — the
    documented stable turn identity, readable inside ``task()`` personas —
    with the checkpointer-key int-prefix as fallback. Unresolvable identity
    fails CLOSED for the ``/agent/`` subtree only: shared data (uploads,
    root-level reports) needs no caller identity and stays readable.
    """

    def _scope_thread_id(self) -> int | None:
        from ..audit_trail import AUDIT_CONTEXT_KEY
        from .cas_backend import (
            _DESK_EXECUTION_CONTEXT,
            _active_graph_config,
            _configurable,
            _thread_id_from_config,
        )

        try:
            values = _configurable(_active_graph_config())
            if not values:
                values = _DESK_EXECUTION_CONTEXT.get() or {}
            audit_ctx = values.get(AUDIT_CONTEXT_KEY) or {}
            if audit_ctx.get("thread_id") is not None:
                return int(audit_ctx["thread_id"])
            return _thread_id_from_config(values)
        except Exception:
            return None

    @staticmethod
    def _denial(path: str, own_thread_id: int | None) -> str | None:
        """Reason ``path`` is invisible to the caller, or None if visible."""
        norm = str(path).lstrip("/")
        for subtree in RESTRICTED_ARTIFACT_SUBTREES:
            if norm == subtree or norm.startswith(f"{subtree}/"):
                return (
                    f"Path '/artifacts/{norm}' is a restricted store "
                    f"('{subtree}') and is not agent-readable. Use the "
                    "artifact tools (list_artifacts/read_artifact) for "
                    "captured results."
                )
        match = _THREAD_DIR_RE.match(norm)
        if match is None:
            return None
        if own_thread_id is None:
            return (
                f"Path '/artifacts/{norm}' is inside a per-thread workspace "
                "and the calling thread could not be resolved; refusing the "
                "cross-thread read."
            )
        if int(match.group(1)) != own_thread_id:
            return (
                f"Path '/artifacts/{norm}' belongs to another desk thread's "
                f"workspace (this thread is {own_thread_id}); artifacts "
                "under /artifacts/agent/ are scoped per thread."
            )
        return None

    # -- read-path overrides -------------------------------------------------

    def read(
        self,
        file_path: str,
        offset: int = 0,
        limit: int = 2000,
    ) -> ReadResult:
        reason = self._denial(file_path, self._scope_thread_id())
        if reason is not None:
            return ReadResult(error=reason)
        return super().read(file_path, offset=offset, limit=limit)

    def ls(self, path: str) -> LsResult:
        own = self._scope_thread_id()
        reason = self._denial(path, own)
        if reason is not None:
            return LsResult(error=reason)
        result = super().ls(path)
        if result.entries is None:
            return result
        return LsResult(
            entries=[
                fi
                for fi in result.entries
                if self._denial(fi.get("path", ""), own) is None
            ]
        )

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        own = self._scope_thread_id()
        if path is not None:
            reason = self._denial(path, own)
            if reason is not None:
                return GlobResult(error=reason)
        result = super().glob(pattern, path)
        if result.matches is None:
            return result
        return GlobResult(
            matches=[
                fi
                for fi in result.matches
                if self._denial(fi.get("path", ""), own) is None
            ]
        )

    def grep(
        self,
        pattern: str,
        path: str | None = None,
        glob: str | None = None,
    ) -> GrepResult:
        own = self._scope_thread_id()
        if path is not None:
            reason = self._denial(path, own)
            if reason is not None:
                return GrepResult(error=reason)
        result = super().grep(pattern, path=path, glob=glob)
        if result.matches is None:
            return result
        return GrepResult(
            matches=[
                m
                for m in result.matches
                if self._denial(m.get("path", ""), own) is None
            ]
        )


def build_scoped_artifacts_backend(
    root_dir: str | Path | None = None,
) -> ScopedArtifactsBackend:
    """The one sanctioned constructor for the /artifacts/ route backend."""
    root = Path(root_dir) if root_dir is not None else artifacts_root()
    return ScopedArtifactsBackend(root_dir=str(root), virtual_mode=True)
