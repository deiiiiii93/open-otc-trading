"""Integration tests for the final Phase 3 workflow skill catalog.

These tests exercise SkillsMiddleware source loading without spinning up a
model session. After P3.9, runtime skills are workflow-first only; legacy
domain/procedure/product sources must stay absent.
"""
from __future__ import annotations

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.hitl import interrupt_on_config
from app.services.deep_agent.skills_paths import SKILLS_ROOT


@pytest.fixture
def skills_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)


def _names(skills) -> set[str]:
    return {skill["name"] for skill in skills}


def test_workflow_sources_are_readable(skills_backend: FilesystemBackend) -> None:
    assert _names(_list_skills(skills_backend, "/workflows/positions/")) == {
        "position-snapshot",
        "position-inputs",
        "position-diagnosis",
        "book-position",
    }
    assert _names(_list_skills(skills_backend, "/workflows/risk/")) == {
        "run-risk",
        "read-risk-result",
        "create-risk-report",
        "run-scenario-test",
        "run-backtest",
        "run-greeks-landscape",
    }
    assert _names(_list_skills(skills_backend, "/workflows/snowballs/")) == {
        "snowball-term-interpretation",
        "snowball-pricing",
        "snowball-risk-explain",
    }


def test_legacy_compatibility_sources_are_removed(
    skills_backend: FilesystemBackend,
) -> None:
    assert _list_skills(skills_backend, "/procedures/trader/") == []
    assert _list_skills(skills_backend, "/procedures/risk_manager/") == []
    assert _list_skills(skills_backend, "/procedures/high_board/") == []
    assert _list_skills(skills_backend, "/domains/position/") == []
    assert _list_skills(skills_backend, "/products/") == []


def test_production_composite_backend_resolves_workflow_prefix() -> None:
    from app.services.deep_agent.orchestrator import _build_backend

    backend = _build_backend()

    assert _names(_list_skills(backend, "/skills/workflows/pricing/")) == {
        "price-product",
        "price-portfolio",
        "pricing-parameter-maintenance",
    }
    assert _names(_list_skills(backend, "/skills/workflows/reporting/")) == {
        "generate-report",
        "batch-run-reports",
        "display-report",
    }
    assert _list_skills(backend, "/skills/procedures/trader/") == []
    assert _list_skills(backend, "/skills/products/") == []


def test_read_file_is_not_hitl_gated() -> None:
    """Skill and reference reads must never pause for confirmation."""
    config = interrupt_on_config()
    if isinstance(config, dict):
        assert "read_file" not in config, (
            f"read_file must not be HITL-gated, got config keys: {list(config)}"
        )
    else:
        names = list(config)
        assert "read_file" not in names, (
            f"read_file must not be HITL-gated, got: {names}"
        )
