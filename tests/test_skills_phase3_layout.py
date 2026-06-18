"""Phase 3 skill-catalog layout tests.

Phase 3 moves the live catalog out of ``services/deep_agent/skills`` and into
``app/skills``. P3.9 removes the transitional legacy compatibility catalog; only
workflow-first runtime sources remain.
"""
from __future__ import annotations

from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills


_REPO_ROOT = Path(__file__).resolve().parents[1]
_APP_SKILLS_ROOT = _REPO_ROOT / "backend" / "app" / "skills"


def _names(skills) -> set[str]:
    return {s["name"] for s in skills}


def test_phase3_skills_root_has_final_workflow_catalog_only() -> None:
    assert not (_APP_SKILLS_ROOT / "legacy").exists()
    assert not (_APP_SKILLS_ROOT / "domains").exists()
    assert not (_APP_SKILLS_ROOT / "procedures").exists()
    assert not (_APP_SKILLS_ROOT / "products").exists()
    assert (_APP_SKILLS_ROOT / "workflows").is_dir()
    assert (_APP_SKILLS_ROOT / "meta").is_dir()
    assert (_APP_SKILLS_ROOT / "references").is_dir()
    assert not (_APP_SKILLS_ROOT / "routing").exists()


def test_legacy_skill_sources_no_longer_resolve_from_new_root() -> None:
    backend = FilesystemBackend(root_dir=str(_APP_SKILLS_ROOT), virtual_mode=True)

    assert _list_skills(backend, "/procedures/trader/") == []
    assert _list_skills(backend, "/domains/position/") == []
    assert _list_skills(backend, "/products/") == []
    assert _list_skills(backend, "/routing/") == []


def test_runtime_skills_root_points_at_app_skills() -> None:
    from app.services.deep_agent.skills_paths import SKILLS_ROOT

    assert SKILLS_ROOT == _APP_SKILLS_ROOT
