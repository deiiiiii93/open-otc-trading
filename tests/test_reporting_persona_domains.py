from app.services.deep_agent.persona_domains import (
    PERSONA_WORKFLOW_DOMAINS,
    workflow_skill_sources,
)


def test_every_persona_that_narrates_a_template_can_see_reporting():
    """A template's persona narrates it, so each must reach the reporting domain."""
    for persona in ("trader", "risk_manager", "high_board"):
        assert "reporting" in PERSONA_WORKFLOW_DOMAINS[persona], (
            f"{persona} cannot see the reporting domain, so its report skills "
            "are unroutable"
        )


def test_trader_skill_sources_include_the_reporting_prefix():
    assert "/skills/workflows/reporting/" in workflow_skill_sources("trader")


def test_existing_trader_domains_are_preserved():
    """Adding reporting must not reorder or drop what trader already had."""
    domains = PERSONA_WORKFLOW_DOMAINS["trader"]
    for expected in ("positions", "products", "try-solve", "pricing", "hedging",
                     "market-data", "portfolios", "rfq", "snowballs",
                     "desk-workflows"):
        assert expected in domains
