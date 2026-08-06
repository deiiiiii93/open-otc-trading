import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers
from app.services.reporting.seeds import load_seed_specs
from app.services.reporting.template_spec import parse_spec, validate_spec

EXPECTED_SLUGS = {
    "trader-daily",
    "risk-manager-daily",
    "high-board-daily",
    "portfolio-snapshot",
}


def test_all_four_seeds_are_present():
    assert set(load_seed_specs()) == EXPECTED_SLUGS


@pytest.mark.parametrize("slug", sorted(EXPECTED_SLUGS))
def test_every_seed_validates_against_the_live_registry(slug):
    """A seed that names a nonexistent block must fail here, not in production."""
    spec = parse_spec(load_seed_specs()[slug])
    validate_spec(spec)
    assert spec.meta.slug == slug


def test_seed_filename_matches_its_declared_slug():
    for slug, text in load_seed_specs().items():
        assert parse_spec(text).meta.slug == slug


def test_high_board_template_is_narrative_light():
    """The board one-pager has exactly one narrating section, so it costs one LLM call."""
    spec = parse_spec(load_seed_specs()["high-board-daily"])
    narrating = [s for s in spec.sections if (s.narrative or "").strip()]
    assert len(narrating) == 1
    assert narrating[0].id == "executive_summary"


def test_portfolio_snapshot_is_fully_deterministic():
    """The create_report compatibility template must generate with ZERO LLM calls."""
    spec = parse_spec(load_seed_specs()["portfolio-snapshot"])
    assert all(not (s.narrative or "").strip() for s in spec.sections)


def test_risk_manager_template_includes_the_honest_empty_stress_section():
    """scenario.latest_grid ships knowing it renders 'unavailable' today."""
    spec = parse_spec(load_seed_specs()["risk-manager-daily"])
    keys = [ref.key for section in spec.sections for ref in section.blocks]
    assert "scenario.latest_grid" in keys


def test_trader_template_covers_the_daily_story():
    spec = parse_spec(load_seed_specs()["trader-daily"])
    keys = {ref.key for section in spec.sections for ref in section.blocks}
    assert {"pnl.daily", "pnl.explain", "pnl.by_position"} <= keys


def test_every_persona_has_at_least_one_seeded_template():
    personas = {parse_spec(text).meta.persona for text in load_seed_specs().values()}
    assert {"trader", "risk_manager", "high_board"} <= personas


def test_seed_specs_parse_without_the_registry_for_migration_use():
    """The migration inserts raw YAML; it must be readable without app imports."""
    import yaml

    for slug, text in load_seed_specs().items():
        raw = yaml.safe_load(text)
        assert raw["meta"]["slug"] == slug
        assert raw["meta"]["title"]
        assert raw["meta"]["persona"]
        assert isinstance(raw["sections"], list) and raw["sections"]
