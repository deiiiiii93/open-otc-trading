"""Phase 3.6 workflow skill migration tests."""
from __future__ import annotations

import re
from pathlib import Path

from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import _list_skills

from app.services.deep_agent.orchestrator import _build_backend, _orchestrator_prompt
from app.services.deep_agent.personas import risk_spec, trader_spec
from app.services.deep_agent.skill_lint import lint_skill_file, parse_skill_file
from app.services.deep_agent.skills_paths import (
    REFERENCES_DIR,
    SKILLS_ROOT,
    WORKFLOWS_DIR,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PROMPT = (
    REPO_ROOT / "backend/app/services/deep_agent/prompts/orchestrator.md"
)
FRONTEND_TRY_SOLVE = REPO_ROOT / "frontend/src/routes/TrySolve.tsx"


P3_6_WORKFLOW_FILES = {
    "risk/run-risk/SKILL.md",
    "risk/read-risk-result/SKILL.md",
    "risk/create-risk-report/SKILL.md",
    "risk/run-greeks-landscape/SKILL.md",
    "positions/position-snapshot/SKILL.md",
    "positions/position-inputs/SKILL.md",
    "positions/position-diagnosis/SKILL.md",
    "try-solve/solve-imported-row/SKILL.md",
    "try-solve/create-request-queue-item/SKILL.md",
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


def _page_action_endpoint(frontend_source: str, action_name: str) -> str:
    match = re.search(
        rf"name: '{re.escape(action_name)}'.*?backend_endpoint: '([^']+)'",
        frontend_source,
        flags=re.DOTALL,
    )
    assert match, f"missing page action block for {action_name}"
    return match.group(1)


def test_phase3_workflow_file_set_includes_p3_6_target() -> None:
    actual = {
        path.relative_to(WORKFLOWS_DIR).as_posix()
        for path in WORKFLOWS_DIR.rglob("SKILL.md")
    }

    assert P3_6_WORKFLOW_FILES <= actual


def test_phase3_workflow_skills_are_ci_lint_clean() -> None:
    for relative in P3_6_WORKFLOW_FILES:
        path = WORKFLOWS_DIR / relative
        warnings = lint_skill_file(path, mode="ci", root=SKILLS_ROOT)
        assert [warning for warning in warnings if warning.severity == "error"] == []
        parsed = parse_skill_file(path)
        assert parsed.frontmatter["name"] == path.parent.name
        assert parsed.frontmatter["domain"] in {"risk", "positions", "try-solve"}
        assert parsed.frontmatter["success_criteria"]
        assert "## Example" in parsed.body


def test_workflow_sources_are_readable_via_skills_backend() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _names(_list_skills(backend, "/workflows/risk/")) == {
        "run-risk",
        "read-risk-result",
        "create-risk-report",
        "run-scenario-test",
        "run-backtest",
        "run-greeks-landscape",
    }
    assert _names(_list_skills(backend, "/workflows/positions/")) == {
        "position-snapshot",
        "position-inputs",
        "position-diagnosis",
        "book-position",
        "asian-fixings",
    }
    assert _names(_list_skills(backend, "/workflows/try-solve/")) == {
        "solve-imported-row",
        "create-request-queue-item",
    }


def test_persona_sources_use_migrated_workflow_directories() -> None:
    trader_sources = _source_list(trader_spec(object(), []))
    risk_sources = _source_list(risk_spec(object(), []))

    assert "/skills/workflows/positions/" in trader_sources
    assert "/skills/workflows/try-solve/" in trader_sources
    assert "/skills/domains/position/" not in trader_sources

    assert "/skills/workflows/positions/" in risk_sources
    assert "/skills/workflows/risk/" in risk_sources
    assert "/skills/domains/position/" not in risk_sources
    assert "/skills/domains/risk/" not in risk_sources


def test_runtime_persona_catalogs_do_not_shadow_migrated_workflows() -> None:
    backend = _build_backend()
    trader_catalog = _catalog_names(backend, _source_list(trader_spec(object(), [])))
    risk_catalog = _catalog_names(backend, _source_list(risk_spec(object(), [])))

    assert "position-diagnosis" in trader_catalog
    assert "position-diagnosis" in risk_catalog
    assert "run-risk" in risk_catalog
    assert "read-risk-result" in risk_catalog
    assert "create-risk-report" in risk_catalog

    assert "snowball-position-diagnostics" not in trader_catalog
    assert "snowball-position-diagnostics" not in risk_catalog
    assert "risk-report-workflow" not in risk_catalog
    assert "portfolio-pricing-run" not in trader_catalog
    assert "portfolio-pricing-run" not in risk_catalog
    assert "rfq-intake-and-quote" not in trader_catalog
    assert "market-data-profile" not in trader_catalog


def test_runtime_prompt_routing_names_migrated_workflows() -> None:
    prompt_files = [
        ORCHESTRATOR_PROMPT,
        REPO_ROOT / "backend/app/services/deep_agent/prompts/trader.md",
        REPO_ROOT / "backend/app/services/deep_agent/prompts/risk_manager.md",
        REPO_ROOT / "backend/app/services/deep_agent/prompts/high_board.md",
        REFERENCES_DIR / "products/snowball-cn.md",
    ]
    combined = "\n".join(
        [
            _orchestrator_prompt(),
            *(
                path.read_text(encoding="utf-8")
                for path in prompt_files
                if path != ORCHESTRATOR_PROMPT
            ),
        ]
    )

    assert "position-diagnosis" in combined
    assert "create-risk-report" in combined
    assert "generate-report" in combined
    assert "price-portfolio" in combined
    assert "explain-market-data-drift" in combined
    assert "display-report" in combined
    # The orchestrator routes from its own prompt (it cannot see persona skill
    # catalogs), so the pricing-parameter write capability must be named THERE —
    # smoke thread #47 proved natural phrasing is refused otherwise.
    assert "pricing-parameter-maintenance" in _orchestrator_prompt()
    assert "pricing-parameter-maintenance" in (
        REPO_ROOT / "backend/app/services/deep_agent/prompts/risk_manager.md"
    ).read_text(encoding="utf-8")
    assert "snowball-position-diagnostics" not in combined
    assert "risk-report-workflow" not in combined
    assert "portfolio-pricing-run" not in combined
    assert "rfq-intake-and-quote" not in combined
    assert "market-data-profile" not in combined
    assert "`pricing-run-propose`" not in combined
    assert "`market-data-drift`" not in combined


def test_try_solve_page_action_endpoints_match_current_contracts() -> None:
    frontend_source = FRONTEND_TRY_SOLVE.read_text(encoding="utf-8")

    assert (
        _page_action_endpoint(frontend_source, "solve_imported_row")
        == "POST /api/rfq/try-solve/solve"
    )
    assert (
        _page_action_endpoint(frontend_source, "create_request_queue_item")
        == "local:try-solve/request-queue-item"
    )


def test_try_solve_workflows_use_page_action_contract_not_agent_tool_names() -> None:
    expected_actions = {
        "try-solve/solve-imported-row/SKILL.md": "solve_imported_row",
        "try-solve/create-request-queue-item/SKILL.md": (
            "create_request_queue_item"
        ),
    }

    for relative, action_name in expected_actions.items():
        body = (WORKFLOWS_DIR / relative).read_text(encoding="utf-8")
        assert f"page-action request `{action_name}`" in body
        assert f"invoke the page action `{action_name}`" not in body
        assert "not a backend domain tool" in body


def test_legacy_sources_are_removed_after_p3_9() -> None:
    backend = FilesystemBackend(root_dir=str(SKILLS_ROOT), virtual_mode=True)

    assert _list_skills(backend, "/domains/position/") == []
    assert _list_skills(backend, "/domains/risk/") == []
    assert _list_skills(backend, "/procedures/risk_manager/") == []
