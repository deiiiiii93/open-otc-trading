"""Tier-C read_file smoke tests for the v2 surface.

Verifies that read_file:
1. Works on workflow skills.
2. Works on durable reference docs.
3. Works on HTML artifacts under /artifacts/ via the new mount.
4. Does not appear in the HITL interrupt config (no HITL pause on reads).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from deepagents.backends.filesystem import FilesystemBackend

from app.services.deep_agent.hitl import interrupt_on_config
from app.services.deep_agent.skills_paths import SKILLS_ROOT


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_ROOT = SKILLS_ROOT
_ARTIFACTS_ROOT = _REPO_ROOT / "artifacts"


@pytest.fixture
def skills_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(_SKILLS_ROOT), virtual_mode=True)


@pytest.fixture
def artifacts_backend(tmp_path: Path) -> FilesystemBackend:
    """Artifacts backend. If /artifacts/ has no HTML, synthesize one so the
    smoke test doesn't depend on environment state."""
    if not list(_ARTIFACTS_ROOT.glob("*.html")):
        (tmp_path / "report-test.html").write_text(
            "<html><body><h1>Test report</h1><p>Body.</p></body></html>",
            encoding="utf-8",
        )
        return FilesystemBackend(root_dir=str(tmp_path), virtual_mode=True)
    return FilesystemBackend(root_dir=str(_ARTIFACTS_ROOT), virtual_mode=True)


def _extract_text(result) -> str:
    """deepagents FilesystemBackend.read() returns a ReadResult with
    .file_data ({"content": str, "encoding": str}) and .error.
    Older releases returned a raw string or bytes. Tolerate all shapes.
    """
    if hasattr(result, "file_data"):
        if getattr(result, "error", None):
            raise RuntimeError(f"backend.read returned error: {result.error}")
        data = result.file_data
        if data is None:
            return ""
        if isinstance(data, dict):
            return data.get("content", "") or ""
        if isinstance(data, str):
            return data
        if isinstance(data, (bytes, bytearray)):
            return data.decode("utf-8", errors="replace")
        raise RuntimeError(f"Unexpected file_data type: {type(data).__name__}")
    if isinstance(result, str):
        return result
    if isinstance(result, (bytes, bytearray)):
        return result.decode("utf-8", errors="replace")
    raise RuntimeError(f"Unexpected read result type: {type(result).__name__}")


def _read(backend: FilesystemBackend, path: str) -> str:
    """Adapter for FilesystemBackend's read API. Tries common method names
    and extracts text from whatever shape the backend returns."""
    for attr in ("read_file", "read_text", "read"):
        if hasattr(backend, attr):
            method = getattr(backend, attr)
            try:
                result = method(path)
            except Exception:  # noqa: BLE001
                continue
            return _extract_text(result)
    raise RuntimeError("No usable read method on FilesystemBackend")


def test_read_portfolio_workflow(skills_backend: FilesystemBackend):
    """Read a workflow skill and assert content."""
    text = _read(
        skills_backend,
        "/workflows/portfolios/portfolio-membership/SKILL.md",
    )
    assert "name: portfolio-membership" in text
    assert "view-derived" in text


def test_read_position_workflow(skills_backend: FilesystemBackend):
    """Read a workflow skill and assert structure."""
    text = _read(skills_backend, "/workflows/positions/position-snapshot/SKILL.md")
    assert "name: position-snapshot" in text
    assert "## When to use" in text
    assert "## Procedure" in text


def test_read_product_reference(skills_backend: FilesystemBackend):
    """Read a durable reference document and assert content."""
    text = _read(skills_backend, "/references/products/snowball-cn.md")
    assert "name: snowball-cn" in text
    assert "## Product Definition" in text


def test_workflow_skills_use_agent_visible_reference_paths():
    """Workflow skills should not invite relative traversal to references."""
    offenders: list[str] = []
    for path in (_SKILLS_ROOT / "workflows").glob("*/*/SKILL.md"):
        text = path.read_text(encoding="utf-8")
        if "`references/" in text or "Read references/" in text:
            offenders.append(str(path.relative_to(_SKILLS_ROOT)))

    assert offenders == []


def test_routing_skill_tree_removed(skills_backend: FilesystemBackend):
    """Routing skills are gone; workflow skills remain readable."""
    text = _read(skills_backend, "/workflows/pricing/price-portfolio/SKILL.md")
    assert "name: price-portfolio" in text
    assert "run_batch_pricing" in text


def test_read_html_artifact(artifacts_backend: FilesystemBackend):
    """Read an HTML artifact (real or synthesized) via the artifacts mount."""
    candidates = list(_ARTIFACTS_ROOT.glob("*.html"))
    if candidates:
        path = f"/{candidates[0].name}"
    else:
        path = "/report-test.html"  # Synthesized in fixture
    text = _read(artifacts_backend, path)
    assert "<html" in text.lower()
    assert "</html>" in text.lower() or len(text) >= 200  # truncation tolerant


def test_read_file_is_not_hitl_gated():
    """read_file must never appear in interrupt-on config — skills depend on it."""
    config = interrupt_on_config()
    if isinstance(config, dict):
        assert "read_file" not in config, (
            f"read_file must not be HITL-gated, got config: {list(config)}"
        )
    else:
        names = list(config)
        assert "read_file" not in names, (
            f"read_file must not be HITL-gated, got: {names}"
        )
