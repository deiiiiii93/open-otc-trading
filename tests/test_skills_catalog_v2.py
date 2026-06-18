"""Extended final-catalog assertions for the Phase 3 skills layer."""
from __future__ import annotations

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.orchestrator import _build_backend
from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.skills_paths import SKILLS_ROOT


@pytest.fixture
def skills_backend() -> FilesystemBackend:
    return FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)


def _names(skills) -> set[str]:
    return {skill["name"] for skill in skills}


def _persona_catalog(
    skills_backend: FilesystemBackend, sources: list[str]
) -> set[str]:
    seen: set[str] = set()
    for source in sources:
        for skill in _list_skills(skills_backend, source):
            seen.add(skill["name"])
    return seen


def _source_list(spec: dict) -> list[str]:
    return list(spec["skills"])


def test_persona_sources_are_workflow_only() -> None:
    trader_sources = _source_list(trader_spec(object(), []))
    risk_sources = _source_list(risk_spec(object(), []))
    board_sources = _source_list(board_spec(object(), []))

    assert trader_sources == [
        "/skills/workflows/positions/",
        "/skills/workflows/products/",
        "/skills/workflows/try-solve/",
        "/skills/workflows/pricing/",
        "/skills/workflows/hedging/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/rfq/",
        "/skills/workflows/snowballs/",
    ]
    assert risk_sources == [
        "/skills/workflows/positions/",
        "/skills/workflows/risk/",
        "/skills/workflows/hedging/",
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
        "/skills/workflows/snowballs/",
    ]
    assert board_sources == [
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
    ]


def test_all_workflow_domains_have_expected_skills(
    skills_backend: FilesystemBackend,
) -> None:
    expected = {
        "/workflows/positions/": {
            "position-snapshot",
            "position-inputs",
            "position-diagnosis",
            "book-position",
        },
        "/workflows/products/": {"build-product"},
        "/workflows/try-solve/": {
            "solve-imported-row",
            "create-request-queue-item",
        },
        "/workflows/pricing/": {
            "price-product",
            "price-portfolio",
            "pricing-parameter-maintenance",
        },
        "/workflows/market-data/": {
            "fetch-market-data",
            "explain-market-data-drift",
        },
        "/workflows/portfolios/": {
            "portfolio-membership",
            "portfolio-view-counting",
            "portfolio-maintenance",
        },
        "/workflows/rfq/": {
            "intake-request",
            "draft-rfq",
            "quote-rfq",
            "submit-for-approval",
        },
        "/workflows/risk/": {
            "run-risk",
            "read-risk-result",
            "create-risk-report",
            "run-scenario-test",
            "run-backtest",
            "run-greeks-landscape",
        },
        "/workflows/reporting/": {
            "generate-report",
            "batch-run-reports",
            "display-report",
        },
        "/workflows/snowballs/": {
            "snowball-term-interpretation",
            "snowball-pricing",
            "snowball-risk-explain",
        },
        "/workflows/hedging/": {"hedge-portfolio"},
    }

    for source, names in expected.items():
        assert _names(_list_skills(skills_backend, source)) == names


def test_legacy_sources_are_empty(skills_backend: FilesystemBackend) -> None:
    for source in [
        "/procedures/trader/",
        "/procedures/risk_manager/",
        "/procedures/high_board/",
        "/domains/position/",
        "/domains/portfolio/",
        "/domains/pricing/",
        "/domains/risk/",
        "/domains/market-data/",
        "/domains/rfq/",
        "/domains/reporting/",
        "/products/",
        "/routing/",
    ]:
        assert _list_skills(skills_backend, source) == []


def test_trader_total_workflow_catalog(skills_backend: FilesystemBackend) -> None:
    catalog = _persona_catalog(_build_backend(), _source_list(trader_spec(object(), [])))

    assert len(catalog) == 23, f"Expected 23 entries, got {len(catalog)}: {catalog}"  # 22 + pricing-parameter-maintenance
    assert {
        "position-snapshot",
        "solve-imported-row",
        "price-portfolio",
        "fetch-market-data",
        "portfolio-membership",
        "quote-rfq",
        "snowball-pricing",
        "hedge-portfolio",
    } <= catalog
    assert "snowball-position-diagnostics" not in catalog
    assert "snowball-cn" not in catalog


def test_risk_manager_total_workflow_catalog(
    skills_backend: FilesystemBackend,
) -> None:
    catalog = _persona_catalog(_build_backend(), _source_list(risk_spec(object(), [])))

    assert len(catalog) == 25, f"Expected 25 entries, got {len(catalog)}: {catalog}"
    assert {
        "position-diagnosis",
        "run-risk",
        "run-greeks-landscape",
        "price-portfolio",
        "explain-market-data-drift",
        "portfolio-view-counting",
        "generate-report",
        "snowball-risk-explain",
        "hedge-portfolio",
    } <= catalog
    assert "risk-report-workflow" not in catalog
    assert "snowball-cn" not in catalog


def test_high_board_total_workflow_catalog(
    skills_backend: FilesystemBackend,
) -> None:
    catalog = _persona_catalog(_build_backend(), _source_list(board_spec(object(), [])))

    # portfolio-maintenance is visible here too (high_board sources
    # /workflows/portfolios/); catalog visibility != capability — every
    # write in it is HITL-gated.
    assert catalog == {
        "portfolio-membership",
        "portfolio-view-counting",
        "portfolio-maintenance",
        "generate-report",
        "batch-run-reports",
        "display-report",
    }


def test_shared_price_portfolio_workflow_is_visible_to_trader_and_risk(
    skills_backend: FilesystemBackend,
) -> None:
    backend = _build_backend()
    trader_catalog = _persona_catalog(backend, _source_list(trader_spec(object(), [])))
    risk_catalog = _persona_catalog(backend, _source_list(risk_spec(object(), [])))

    assert "price-portfolio" in trader_catalog
    assert "price-portfolio" in risk_catalog
    skills = _list_skills(skills_backend, "/workflows/pricing/")
    price_portfolio = next(skill for skill in skills if skill["name"] == "price-portfolio")
    assert price_portfolio["path"].endswith(
        "/workflows/pricing/price-portfolio/SKILL.md"
    )


def test_portfolio_maintenance_skill_is_ci_lint_clean() -> None:
    """No phase3 pin covers post-phase3 skills; without this, nothing gates
    the 500-token body budget or frontmatter schema of portfolio-maintenance."""
    from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
    from app.services.deep_agent.skills_paths import WORKFLOWS_DIR

    path = WORKFLOWS_DIR / "portfolios/portfolio-maintenance/SKILL.md"
    warnings = lint_skill_file(path, mode="ci", root=SKILLS_ROOT)
    assert [w for w in warnings if w.severity == "error"] == []
    parsed = parse_skill_file(path)
    assert parsed.frontmatter["name"] == "portfolio-maintenance"
    assert parsed.frontmatter["write_actions"] is True
    assert parsed.frontmatter["confirmation_required"] is True
    assert "## Example" in parsed.body
