import pytest

from app.services.reporting import blocks  # noqa: F401 - registers producers


def test_all_six_tools_are_in_the_quant_agent_tools_registry():
    from app.tools import QUANT_AGENT_TOOLS

    names = {tool.name for tool in QUANT_AGENT_TOOLS}
    assert {
        "list_report_blocks", "list_report_templates", "get_report_template",
        "resolve_report_block", "save_report_template", "generate_report",
    } <= names


def test_all_six_tools_are_allowlisted_for_the_deep_agent():
    """Registered but not allowlisted means silently dropped from every persona."""
    from app.services.agents import DEEP_AGENT_TOOL_NAMES

    assert {
        "list_report_blocks", "list_report_templates", "get_report_template",
        "resolve_report_block", "save_report_template", "generate_report",
    } <= set(DEEP_AGENT_TOOL_NAMES)


def test_save_report_template_is_hitl_gated_at_write_level():
    from app.services.deep_agent.hitl import (
        INTERRUPT_TOOL_NAMES,
        _LABEL_BY_TOOL,
        _RISK_LEVEL_BY_TOOL,
    )

    assert "save_report_template" in INTERRUPT_TOOL_NAMES
    assert _RISK_LEVEL_BY_TOOL["save_report_template"] == "write"
    assert "save_report_template" in _LABEL_BY_TOOL


def test_generate_report_is_not_hitl_gated():
    """Write-class but not gated, like parse_trade_confirmation."""
    from app.services.deep_agent.hitl import INTERRUPT_TOOL_NAMES

    assert "generate_report" not in INTERRUPT_TOOL_NAMES


def test_create_report_remains_registered():
    """It is a graded tool_not_called prohibition in three golden workflows.

    Deleting it would make those checks trivially always-pass, inflating scores
    and breaking comparability with 11 boards of arena history. See spec §5.7.
    """
    from app.services.agents import DEEP_AGENT_TOOL_NAMES
    from app.tools import QUANT_AGENT_TOOLS

    assert "create_report" in {tool.name for tool in QUANT_AGENT_TOOLS}
    assert "create_report" in set(DEEP_AGENT_TOOL_NAMES)


def test_list_report_blocks_returns_the_catalog():
    from app.tools.report_templates import list_report_blocks_tool

    result = list_report_blocks_tool.invoke({})
    assert result["total"] == 18
    keys = {block["key"] for block in result["blocks"]}
    assert "pnl.explain" in keys
    entry = next(b for b in result["blocks"] if b["key"] == "pnl.explain")
    assert entry["shape"] == "waterfall"
    assert "compare_to_run_id" in entry["requires"]
    assert entry["description"]


def test_list_report_templates_returns_the_seeded_four(session):
    from app.services.reporting import templates as templates_svc
    from app.services.reporting.seeds import load_seed_specs
    from app.tools.report_templates import list_report_templates_tool

    for slug, spec_yaml in load_seed_specs().items():
        templates_svc.save_template(
            slug=slug, spec_yaml=spec_yaml, source="seed", session=session
        )
    session.commit()

    result = list_report_templates_tool.invoke({})
    slugs = {row["slug"] for row in result["templates"]}
    assert {"trader-daily", "risk-manager-daily", "high-board-daily",
            "portfolio-snapshot"} <= slugs


def test_get_report_template_returns_the_spec(session):
    from app.services.reporting import templates as templates_svc
    from app.services.reporting.seeds import load_seed_specs
    from app.tools.report_templates import get_report_template_tool

    templates_svc.save_template(
        slug="portfolio-snapshot",
        spec_yaml=load_seed_specs()["portfolio-snapshot"],
        source="seed",
        session=session,
    )
    session.commit()

    result = get_report_template_tool.invoke({"slug": "portfolio-snapshot"})
    assert result["slug"] == "portfolio-snapshot"
    assert "sections:" in result["spec"]
    assert result["source"] == "seed"


def test_get_report_template_raises_for_an_unknown_slug(session):
    from app.tools.report_templates import get_report_template_tool

    with pytest.raises(ValueError, match="nope"):
        get_report_template_tool.invoke({"slug": "nope"})


def test_save_report_template_reports_validation_errors_as_data(session):
    """An invalid spec is a result the agent can act on, not a crash."""
    from app.tools.report_templates import save_report_template_tool

    bad = """
meta:
  slug: agent-made
  title: Agent Made
  persona: trader
sections:
  - id: s
    title: S
    blocks:
      - { key: risk.nonexistent, render: metric_row }
"""
    result = save_report_template_tool.invoke(
        {"slug": "agent-made", "spec_yaml": bad}
    )
    assert result["ok"] is False
    assert any("risk.nonexistent" in message for message in result["errors"])


def test_resolve_report_block_returns_status_and_reason(session):
    from app.tools.report_templates import resolve_report_block_tool

    result = resolve_report_block_tool.invoke(
        {"key": "scenario.latest_grid", "portfolio_id": 2}
    )
    assert result["status"] in ("ok", "empty", "unavailable")
    if result["status"] != "ok":
        assert result["reason"]
