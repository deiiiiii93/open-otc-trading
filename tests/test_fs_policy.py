"""The /artifacts/ mount policy: restricted subtrees + own-thread scoping.

Run #133 (2026-08-31) measured one contestant reading other contestants' arena
transcripts (``/artifacts/arena/**``), raw CAS blobs
(``/artifacts/artifact_blobs/**`` — bypassing the scoped
``/large_tool_results/`` route), and other threads' reports
(``/artifacts/agent/thread-N/**``) through the blanket-read ``/artifacts/``
mount. Desk threads 1 and 3 exhibited the blob bypass organically.

Two layers, both consumed by BOTH backend builders (orchestrator and async
agent — the async stack keeps its own copies of ``_build_backend`` /
``_filesystem_permissions``, so a fix in one stack alone leaves the other as
the next open door; this file pins registration in both, mirroring
``test_audit_registration.py``):

- ``restricted_artifact_permissions()``: static deny rules for subtrees no
  agent should ever raw-read (arena evidence, CAS blobs, sandbox state).
- ``ScopedArtifactsBackend``: dynamic own-thread rule for
  ``agent/thread-<N>/`` — static permissions cannot express "own thread"
  because the default-selection orchestrator graph is built once and reused
  across every thread, so identity must resolve per operation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.audit_trail import AUDIT_CONTEXT_KEY
from app.services.deep_agent.cas_backend import desk_execution_context
from app.services.deep_agent.fs_policy import (
    RESTRICTED_ARTIFACT_SUBTREES,
    ScopedArtifactsBackend,
    build_scoped_artifacts_backend,
    restricted_artifact_permissions,
)


# ---------------------------------------------------------------------------
# Static layer: permission rules, evaluated with deepagents' own evaluator so
# first-match-wins semantics are pinned against the real implementation.
# ---------------------------------------------------------------------------


def _evaluate(rules, operation: str, path: str) -> str | None:
    from deepagents.middleware.filesystem import _check_fs_permission

    return _check_fs_permission(rules, operation, path)


@pytest.mark.parametrize(
    "stack_rules",
    ["orchestrator", "async_agent"],
)
def test_restricted_subtrees_denied_in_both_stacks(stack_rules):
    if stack_rules == "orchestrator":
        from app.services.deep_agent.orchestrator import _filesystem_permissions

        rules = _filesystem_permissions()
    else:
        from app.services.async_agents.agent import _filesystem_permissions

        rules = _filesystem_permissions(task_id=7)

    for subtree in RESTRICTED_ARTIFACT_SUBTREES:
        assert _evaluate(rules, "read", f"/artifacts/{subtree}") == "deny"
        assert (
            _evaluate(rules, "read", f"/artifacts/{subtree}/deep/nested/file.json")
            == "deny"
        )
    # Legitimate agent data stays readable.
    assert (
        _evaluate(rules, "read", "/artifacts/uploads/confirmations/conf-01.pdf")
        == "allow"
    )
    assert _evaluate(rules, "read", "/artifacts/some-report.html") == "allow"
    # Thread dirs pass the STATIC layer; the dynamic own-thread rule is the
    # scoped backend's job (a build-time rule cannot know the caller).
    assert (
        _evaluate(rules, "read", "/artifacts/agent/thread-7/trading_desk/r.md")
        == "allow"
    )


def test_restricted_subtree_constant_covers_measured_leaks():
    assert "arena" in RESTRICTED_ARTIFACT_SUBTREES
    assert "artifact_blobs" in RESTRICTED_ARTIFACT_SUBTREES
    assert "sandbox_sessions" in RESTRICTED_ARTIFACT_SUBTREES


def test_restricted_rules_are_deny_rules():
    for rule in restricted_artifact_permissions():
        assert rule.mode == "deny"


# ---------------------------------------------------------------------------
# Dynamic layer: the scoped backend. Paths are in the backend's own coordinate
# system (CompositeBackend strips the /artifacts/ route prefix before
# delegating), so thread dirs appear as /agent/thread-<N>/...
# ---------------------------------------------------------------------------


@pytest.fixture()
def artifact_tree(tmp_path: Path) -> Path:
    (tmp_path / "agent" / "thread-1" / "trading_desk" / "reports").mkdir(parents=True)
    (
        tmp_path / "agent" / "thread-1" / "trading_desk" / "reports" / "own.md"
    ).write_text("own secret alpha", encoding="utf-8")
    (tmp_path / "agent" / "thread-2" / "trading_desk" / "reports").mkdir(parents=True)
    (
        tmp_path / "agent" / "thread-2" / "trading_desk" / "reports" / "foreign.md"
    ).write_text("foreign secret bravo", encoding="utf-8")
    (tmp_path / "uploads" / "confirmations").mkdir(parents=True)
    (tmp_path / "uploads" / "confirmations" / "conf-01.txt").write_text(
        "confirmation body", encoding="utf-8"
    )
    (tmp_path / "arena" / "133").mkdir(parents=True)
    (tmp_path / "arena" / "133" / "transcript.json").write_text(
        '{"answers": "the grade book"}', encoding="utf-8"
    )
    return tmp_path


def _backend(root: Path) -> ScopedArtifactsBackend:
    return build_scoped_artifacts_backend(root_dir=root)


def _as_thread(thread_id: int):
    return desk_execution_context({AUDIT_CONTEXT_KEY: {"thread_id": thread_id}})


def test_scoped_backend_allows_own_thread_and_blocks_foreign(artifact_tree):
    backend = _backend(artifact_tree)
    with _as_thread(1):
        own = backend.read("/agent/thread-1/trading_desk/reports/own.md")
        assert own.error is None
        assert "own secret alpha" in own.file_data["content"]

        foreign = backend.read("/agent/thread-2/trading_desk/reports/foreign.md")
        assert foreign.error is not None
        assert "another desk thread" in foreign.error
        assert foreign.file_data is None

        # Enumeration filters foreign thread dirs everywhere.
        listing = backend.ls("/agent")
        paths = [fi["path"] for fi in (listing.entries or [])]
        assert any("thread-1" in p for p in paths)
        assert not any("thread-2" in p for p in paths)

        matches = backend.glob("**/*.md")
        match_paths = [fi["path"] for fi in (matches.matches or [])]
        assert any("thread-1" in p for p in match_paths)
        assert not any("thread-2" in p for p in match_paths)

        grep = backend.grep("secret", "/")
        grep_paths = [m["path"] for m in (grep.matches or [])]
        assert any("thread-1" in p for p in grep_paths)
        assert not any("thread-2" in p for p in grep_paths)

        # Listing a foreign dir directly is an honest refusal, not not-found.
        foreign_ls = backend.ls("/agent/thread-2")
        assert foreign_ls.error is not None
        assert "another desk thread" in foreign_ls.error


def test_scoped_backend_denies_restricted_subtrees_as_belt_and_braces(artifact_tree):
    backend = _backend(artifact_tree)
    with _as_thread(1):
        blocked = backend.read("/arena/133/transcript.json")
        assert blocked.error is not None
        assert "restricted" in blocked.error
        # Restricted subtrees also vanish from enumeration.
        root_ls = backend.ls("/")
        paths = [fi["path"] for fi in (root_ls.entries or [])]
        assert not any("arena" in p for p in paths)
        grep = backend.grep("grade book", "/")
        assert not (grep.matches or [])


def test_scoped_backend_fails_closed_without_context(artifact_tree):
    backend = _backend(artifact_tree)
    # No graph config, no desk execution context: thread identity is
    # unresolvable, so the whole /agent/ subtree refuses — but shared data
    # stays readable (an unresolvable desk turn must not lose uploads).
    denied = backend.read("/agent/thread-1/trading_desk/reports/own.md")
    assert denied.error is not None
    shared = backend.read("/uploads/confirmations/conf-01.txt")
    assert shared.error is None
    assert "confirmation body" in shared.file_data["content"]


def test_scoped_backend_resolves_composite_checkpointer_thread_ids(artifact_tree):
    backend = _backend(artifact_tree)
    # Inside a persona namespace the checkpointer key is a composite string
    # ("<id>:<ns>"); the int prefix is the AgentThread id.
    with desk_execution_context({"thread_id": "1:persona-suffix"}):
        own = backend.read("/agent/thread-1/trading_desk/reports/own.md")
        assert own.error is None
        foreign = backend.read("/agent/thread-2/trading_desk/reports/foreign.md")
        assert foreign.error is not None


# ---------------------------------------------------------------------------
# Registration: both stacks must route /artifacts/ through the scoped backend
# and carry the restricted-subtree denies. A stack that keeps the plain
# FilesystemBackend re-opens every measured leak.
# ---------------------------------------------------------------------------


def test_orchestrator_backend_routes_artifacts_through_scoped_backend():
    from app.services.deep_agent.orchestrator import _build_backend

    backend = _build_backend()
    assert isinstance(backend.routes["/artifacts/"], ScopedArtifactsBackend)


def test_async_agent_backend_routes_artifacts_through_scoped_backend():
    from app.services.async_agents.agent import _build_backend

    backend = _build_backend()
    assert isinstance(backend.routes["/artifacts/"], ScopedArtifactsBackend)
