"""Routing-table generation from workflow skill frontmatter."""
from __future__ import annotations

import pytest

from app.services.deep_agent.routing_table import (
    KNOWN_SKILLS_SENTINEL,
    RoutingRow,
    collect_routing_rows,
    inject_known_skills_table,
    render_known_skills_table,
)

SKILL_TEMPLATE = """---
name: {name}
description: A test skill description for routing collection.
domain: {domain}
workflow_type: read
allowed_envelopes:
  - desk_workflow
may_escalate_to: []
required_context: []
optional_context: []
write_actions: false
confirmation_required: false
success_criteria:
  - done
routing:
{routing}---

## Example

User: hi.
Assistant: hi.
"""


@pytest.fixture
def workflows_root(tmp_path):
    root = tmp_path / "workflows"
    a = root / "market-data" / "fetch-market-data"
    a.mkdir(parents=True)
    (a / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(
            name="fetch-market-data",
            domain="market-data",
            routing='  - request: "Fetch current market data"\n    persona: trader\n',
        ),
        encoding="utf-8",
    )
    b = root / "pricing" / "price-portfolio"
    b.mkdir(parents=True)
    (b / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(
            name="price-portfolio",
            domain="pricing",
            routing=(
                '  - request: "Reprice a portfolio"\n    persona: trader\n'
                '  - request: "Reprice for risk"\n    persona: risk_manager\n'
            ),
        ),
        encoding="utf-8",
    )
    # A skill without routing must not appear.
    c = root / "risk" / "read-risk-result"
    c.mkdir(parents=True)
    (c / "SKILL.md").write_text(
        SKILL_TEMPLATE.format(
            name="read-risk-result", domain="risk", routing=""
        ).replace("routing:\n---", "---"),
        encoding="utf-8",
    )
    return root


def test_collect_is_sorted_and_skips_unrouted(workflows_root) -> None:
    rows = collect_routing_rows(workflows_root)
    assert [(r.skill, r.request, r.persona) for r in rows] == [
        ("fetch-market-data", "Fetch current market data", "trader"),
        ("price-portfolio", "Reprice a portfolio", "trader"),
        ("price-portfolio", "Reprice for risk", "risk_manager"),
    ]


def test_render_is_a_markdown_table() -> None:
    rows = [
        RoutingRow(domain="pricing", skill="price-portfolio",
                   request="Reprice a portfolio", persona="trader"),
    ]
    table = render_known_skills_table(rows)
    lines = table.splitlines()
    assert lines[0].startswith("| Request shape")
    assert lines[1].startswith("|---")
    import re
    assert "| Reprice a portfolio | trader | price-portfolio |" in re.sub(r" {2,}", " ", table)


def test_inject_replaces_sentinel(workflows_root) -> None:
    prompt = f"intro\n\n{KNOWN_SKILLS_SENTINEL}\n\noutro"
    injected = inject_known_skills_table(prompt, workflows_root)
    assert KNOWN_SKILLS_SENTINEL not in injected
    assert "fetch-market-data" in injected
    assert injected.startswith("intro") and injected.endswith("outro")


def test_inject_without_sentinel_raises(workflows_root) -> None:
    with pytest.raises(ValueError, match="KNOWN_SKILLS_TABLE sentinel"):
        inject_known_skills_table("no sentinel here", workflows_root)


def test_inject_with_duplicate_sentinels_raises(workflows_root) -> None:
    prompt = f"{KNOWN_SKILLS_SENTINEL}\n\n{KNOWN_SKILLS_SENTINEL}"
    with pytest.raises(ValueError, match="multiple KNOWN_SKILLS_TABLE sentinels"):
        inject_known_skills_table(prompt, workflows_root)


def test_render_empty_rows_is_header_only() -> None:
    table = render_known_skills_table([])
    lines = table.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("| Request shape")
    assert lines[1].startswith("|---")


# --- migration equivalence pin -------------------------------------------
# The exact 21 rows of the hand-written table this migration replaced.
# (One row updated at merge time: main's batch-pricing unification retired
# the price-portfolio risk-lens route in favor of run-risk.)
# Extended at Task 3.4 with the run-backtest routing row (22 rows total).
# Extended with the run-greeks-landscape routing row (23 rows total).
# Extended with the asian-fixings routing row (24 rows total).
OLD_TABLE_ROWS: set[tuple[str, str, str]] = {
    ("Create or edit a reusable desk workflow", "risk_manager", "build-workflow"),
    ("Build a slash-command workflow playbook", "trader", "build-workflow"),
    ("Snowball terms or payoff interpretation", "trader", "snowball-term-interpretation"),
    ("Snowball pricing or valuation drivers", "trader", "snowball-pricing"),
    ("Snowball risk, hedge feasibility, gamma near KI", "risk_manager", "snowball-risk-explain"),
    ("Unexpected position value, Greek, PnL, or contribution", "trader", "position-diagnosis"),
    ("RFQ intake / client request capture", "trader", "intake-request"),
    ("RFQ draft from natural language", "trader", "draft-rfq"),
    ("Construct/validate a quant-ark product from terms", "trader", "build-product"),
    ("Book a product directly into a portfolio from terms", "trader", "book-position"),
    ("Solve/size a portfolio greek hedge (strategies, bands)", "risk_manager", "hedge-portfolio"),
    ("Book stated hedge legs / act on a hedge recommendation", "trader", "hedge-portfolio"),
    ("Create or manage a portfolio (views, rules, sources)", "trader", "portfolio-maintenance"),
    ("RFQ solve / quote a product spec", "trader", "quote-rfq"),
    ("Submit quoted RFQ for approval", "trader", "submit-for-approval"),
    ("Reprice a portfolio (trader lens — pricing freshness)", "trader", "price-portfolio"),
    ("Audit market-data freshness/coverage on a portfolio", "trader", "explain-market-data-drift"),
    ("Fetch current market data", "trader", "fetch-market-data"),
    ("Refresh persisted risk (also refreshes valuations)", "risk_manager", "run-risk"),
    ("Generate a custom or formal in-thread report artifact", "high_board", "generate-report"),
    ("Generate a risk report end-to-end", "risk_manager", "create-risk-report"),
    ("Review/quote from a persisted report", "high_board", "display-report"),
    ("Stress test or scenario analysis of a portfolio", "risk_manager", "run-scenario-test"),
    ("Historical backtest or hedge replay of a portfolio", "risk_manager", "run-backtest"),
    ("Read or run a portfolio Greeks Landscape", "risk_manager", "run-greeks-landscape"),
    ("Set up or refresh the Asian fixing calendar, or capture a due fixing for an Asian position", "trader", "asian-fixings"),
}


def test_backfilled_catalog_reproduces_old_table_rows() -> None:
    rows = collect_routing_rows()
    triples = {(r.request, r.persona, r.skill) for r in rows}
    assert triples == OLD_TABLE_ROWS
    assert len(rows) == len(OLD_TABLE_ROWS)


def test_orchestrator_prompt_contains_generated_table() -> None:
    from app.services.deep_agent.orchestrator import _orchestrator_prompt

    prompt = _orchestrator_prompt()
    assert KNOWN_SKILLS_SENTINEL not in prompt
    assert render_known_skills_table(collect_routing_rows()) in prompt


def test_every_routing_entry_lands_in_the_rendered_table() -> None:
    table = render_known_skills_table(collect_routing_rows())
    for request, persona, skill in OLD_TABLE_ROWS:
        matching = [
            line
            for line in table.splitlines()
            if request in line and persona in line and skill in line
        ]
        assert matching, f"missing row for {skill}: {request}"


def test_live_catalog_rows_all_render_into_the_prompt_table() -> None:
    """Permanent drift guard: every CURRENT frontmatter routing entry renders.

    Unlike OLD_TABLE_ROWS (a one-time migration pin that may be retired once
    the catalog evolves), this is parametrized over the live catalog and
    survives future skill additions.
    """
    rows = collect_routing_rows()
    table = render_known_skills_table(rows)
    lines = table.splitlines()
    assert len(lines) == 2 + len(rows)
    for row in rows:
        matching = [
            line
            for line in lines
            if row.request in line and row.persona in line and row.skill in line
        ]
        assert matching, f"missing row for {row.skill}: {row.request}"
