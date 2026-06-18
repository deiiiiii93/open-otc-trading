"""Phase 3.7 remaining workflow skill migration tests."""
from __future__ import annotations

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.orchestrator import _build_backend
from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
from app.services.deep_agent.skills_paths import SKILLS_ROOT, WORKFLOWS_DIR

P3_7_WORKFLOW_FILES = {
    "pricing/price-product/SKILL.md",
    "pricing/price-portfolio/SKILL.md",
    "market-data/fetch-market-data/SKILL.md",
    "market-data/explain-market-data-drift/SKILL.md",
    "portfolios/portfolio-membership/SKILL.md",
    "portfolios/portfolio-view-counting/SKILL.md",
    "rfq/intake-request/SKILL.md",
    "rfq/draft-rfq/SKILL.md",
    "rfq/quote-rfq/SKILL.md",
    "rfq/submit-for-approval/SKILL.md",
    "reporting/generate-report/SKILL.md",
    "reporting/batch-run-reports/SKILL.md",
    "reporting/display-report/SKILL.md",
    "snowballs/snowball-term-interpretation/SKILL.md",
    "snowballs/snowball-pricing/SKILL.md",
    "snowballs/snowball-risk-explain/SKILL.md",
}

P3_7_DOMAINS = {
    "pricing",
    "market-data",
    "portfolios",
    "rfq",
    "reporting",
    "snowballs",
}


def _names(skills) -> set[str]:
    return {skill["name"] for skill in skills}


def _source_list(spec: dict) -> list[str]:
    return list(spec["skills"])


def _catalog_names(backend, sources: list[str]) -> set[str]:
    names: set[str] = set()
    for source in sources:
        names.update(_names(_list_skills(backend, source)))
    return names


def test_phase3_remaining_workflow_files_exist_and_lint_clean() -> None:
    actual = {
        path.relative_to(WORKFLOWS_DIR).as_posix()
        for path in WORKFLOWS_DIR.rglob("SKILL.md")
    }

    assert P3_7_WORKFLOW_FILES <= actual
    for relative in P3_7_WORKFLOW_FILES:
        path = WORKFLOWS_DIR / relative
        warnings = lint_skill_file(path, mode="ci", root=SKILLS_ROOT)
        assert [warning for warning in warnings if warning.severity == "error"] == []
        parsed = parse_skill_file(path)
        assert parsed.frontmatter["name"] == path.parent.name
        assert parsed.frontmatter["domain"] in P3_7_DOMAINS
        assert parsed.frontmatter["success_criteria"]
        assert "## Example" in parsed.body


def test_phase3_remaining_workflow_sources_are_readable() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _names(_list_skills(backend, "/workflows/pricing/")) == {
        "price-product",
        "price-portfolio",
        "pricing-parameter-maintenance",
    }
    assert _names(_list_skills(backend, "/workflows/market-data/")) == {
        "fetch-market-data",
        "explain-market-data-drift",
    }
    assert _names(_list_skills(backend, "/workflows/portfolios/")) == {
        "portfolio-membership",
        "portfolio-view-counting",
        "portfolio-maintenance",
    }
    assert _names(_list_skills(backend, "/workflows/rfq/")) == {
        "intake-request",
        "draft-rfq",
        "quote-rfq",
        "submit-for-approval",
    }
    assert _names(_list_skills(backend, "/workflows/reporting/")) == {
        "generate-report",
        "batch-run-reports",
        "display-report",
    }
    assert _names(_list_skills(backend, "/workflows/snowballs/")) == {
        "snowball-term-interpretation",
        "snowball-pricing",
        "snowball-risk-explain",
    }


def test_persona_sources_use_remaining_workflow_directories() -> None:
    trader_sources = _source_list(trader_spec(object(), []))
    risk_sources = _source_list(risk_spec(object(), []))
    board_sources = _source_list(board_spec(object(), []))

    for source in [
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/rfq/",
        "/skills/workflows/snowballs/",
    ]:
        assert source in trader_sources

    for source in [
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
        "/skills/workflows/snowballs/",
    ]:
        assert source in risk_sources

    assert board_sources == [
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
    ]

    forbidden = (
        "/skills/domains/pricing/",
        "/skills/domains/market-data/",
        "/skills/domains/rfq/",
        "/skills/domains/reporting/",
        "/skills/domains/portfolio/",
        "/skills/products/",
        "/skills/procedures/high_board/",
    )
    for source in forbidden:
        assert source not in trader_sources
        assert source not in risk_sources
        assert source not in board_sources


def test_runtime_persona_catalogs_use_remaining_workflows_without_legacy_shadowing() -> None:
    backend = _build_backend()
    catalogs = {
        "trader": _catalog_names(backend, _source_list(trader_spec(object(), []))),
        "risk": _catalog_names(backend, _source_list(risk_spec(object(), []))),
        "board": _catalog_names(backend, _source_list(board_spec(object(), []))),
    }

    assert {
        "price-product",
        "price-portfolio",
        "fetch-market-data",
        "explain-market-data-drift",
    } <= catalogs["trader"]
    assert {
        "portfolio-membership",
        "portfolio-view-counting",
        "draft-rfq",
        "quote-rfq",
    } <= catalogs["trader"]
    assert {"generate-report", "batch-run-reports"} <= catalogs["risk"]
    assert {"portfolio-membership", "display-report"} <= catalogs["board"]
    assert {
        "snowball-term-interpretation",
        "snowball-pricing",
        "snowball-risk-explain",
    } <= catalogs["trader"]
    assert {
        "snowball-term-interpretation",
        "snowball-pricing",
        "snowball-risk-explain",
    } <= catalogs["risk"]

    forbidden = {
        "price-product-adhoc",
        "pricing-run-propose",
        "pricing-engines",
        "market-data-fetch",
        "market-data-drift",
        "market-data-conventions",
        "portfolio-model",
        "rfq-draft",
        "rfq-lifecycle",
        "rfq-quote",
        "rfq-submit-for-approval",
        "report-batch-run",
        "report-create-propose",
        "report-query-and-display",
        "snowball-cn",
    }
    for catalog in catalogs.values():
        assert forbidden.isdisjoint(catalog)


def test_legacy_sources_are_removed_after_p3_9() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _list_skills(backend, "/domains/pricing/") == []
    assert _list_skills(backend, "/domains/market-data/") == []
    assert _list_skills(backend, "/domains/portfolio/") == []
    assert _list_skills(backend, "/domains/rfq/") == []
    assert _list_skills(backend, "/procedures/high_board/") == []
    assert _list_skills(backend, "/products/") == []
