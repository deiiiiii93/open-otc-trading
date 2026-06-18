"""PERSONA_WORKFLOW_DOMAINS is the single source of truth for persona skill scoping."""
from __future__ import annotations

from app.services.deep_agent.persona_domains import (
    PERSONA_WORKFLOW_DOMAINS,
    workflow_skill_sources,
)
from app.services.deep_agent.personas import board_spec, risk_spec, trader_spec
from app.services.deep_agent.skills_paths import WORKFLOWS_DIR


def test_trader_sources_unchanged() -> None:
    assert workflow_skill_sources("trader") == [
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


def test_risk_manager_sources_unchanged() -> None:
    assert workflow_skill_sources("risk_manager") == [
        "/skills/workflows/positions/",
        "/skills/workflows/risk/",
        "/skills/workflows/hedging/",
        "/skills/workflows/pricing/",
        "/skills/workflows/market-data/",
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
        "/skills/workflows/snowballs/",
    ]


def test_high_board_sources_unchanged() -> None:
    assert workflow_skill_sources("high_board") == [
        "/skills/workflows/portfolios/",
        "/skills/workflows/reporting/",
    ]


def test_persona_specs_consume_the_constant() -> None:
    assert trader_spec(object(), [])["skills"] == workflow_skill_sources("trader")
    assert risk_spec(object(), [])["skills"] == workflow_skill_sources("risk_manager")
    assert board_spec(object(), [])["skills"] == workflow_skill_sources("high_board")


def test_domain_union_covers_every_workflow_dir() -> None:
    on_disk = {d.name for d in WORKFLOWS_DIR.iterdir() if d.is_dir()}
    declared = {d for domains in PERSONA_WORKFLOW_DOMAINS.values() for d in domains}
    assert declared == on_disk
