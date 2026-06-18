"""Phase 3.8 router-contract tests.

P3.8 deletes routing skills and keeps compound-flow behavior as explicit
orchestrator prompt contracts.
"""
from __future__ import annotations

from pathlib import Path

from app.services.deep_agent import orchestrator
from app.services.deep_agent.orchestrator import _orchestrator_prompt
from app.services.deep_agent.skills_paths import SKILLS_ROOT

REPO_ROOT = Path(__file__).resolve().parents[1]


def _raw_orchestrator_prompt() -> str:
    return (
        REPO_ROOT / "backend/app/services/deep_agent/prompts/orchestrator.md"
    ).read_text(encoding="utf-8")


def test_routing_skill_tree_deleted() -> None:
    assert not (SKILLS_ROOT / "routing").exists()
    assert not (SKILLS_ROOT / "legacy" / "routing").exists()


def test_orchestrator_prompt_uses_inline_compound_routing_contracts() -> None:
    prompt = _raw_orchestrator_prompt()

    assert "## Compound Routing Contracts" in prompt
    assert "/skills/routing/" not in prompt
    assert "read_file` the matching routing skill" not in prompt
    assert "pricing-and-risk-compound" not in prompt
    assert "snowball-book-audit" not in prompt
    assert "market-data-then-reprice" not in prompt

    pricing_idx = prompt.index("Compound pricing + risk health")
    assert "trader" in prompt[pricing_idx:]
    assert "price-portfolio" in prompt[pricing_idx:]
    assert "risk_manager" in prompt[pricing_idx:]
    assert "create-risk-report" in prompt[pricing_idx:]

    snowball_idx = prompt.index("Snowball book audit")
    assert "snowball-pricing" in prompt[snowball_idx:]
    assert "snowball-risk-explain" in prompt[snowball_idx:]

    market_idx = prompt.index("Market-data audit followed by repricing")
    assert "explain-market-data-drift" in prompt[market_idx:]
    assert "price-portfolio" in prompt[market_idx:]


def _raw_trader_prompt() -> str:
    return (
        REPO_ROOT / "backend/app/services/deep_agent/prompts/trader.md"
    ).read_text(encoding="utf-8")


def test_orchestrator_has_direct_booking_quote_first_contract() -> None:
    prompt = _orchestrator_prompt()
    assert "book-position" in prompt
    assert "build-product" in prompt
    assert "quote" in prompt.lower()
    booking_idx = prompt.find("Book a product")
    assert booking_idx != -1, "missing direct-booking routing row"
    # The direct-booking row routes to the trader, not high_board.
    assert "trader" in prompt[booking_idx:booking_idx + 200]


def test_orchestrator_treats_pending_confirmation_as_terminal() -> None:
    """A delegated HITL-gated write that pauses for approval is the turn's
    result. The orchestrator must not re-delegate it (re-proposing the same
    action loops and risks duplicate persisted writes).
    """
    prompt = _raw_orchestrator_prompt()
    lower = prompt.lower()
    assert "pending confirmation" in lower
    assert "do not re-delegate" in lower
    # book_position is a persisted write subject to this rule.
    assert "book_position" in prompt


def test_trader_prompt_uses_build_product_not_regex_drafter() -> None:
    trader = _raw_trader_prompt()
    assert "build_product" in trader
    assert "draft_rfq_from_natural_language" not in trader


def test_build_orchestrator_no_longer_loads_routing_skill_source(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs):
        captured.update(kwargs)

        class Graph:
            name = kwargs["name"]

        return Graph()

    monkeypatch.setattr("deepagents.create_deep_agent", fake_create_deep_agent)

    orchestrator.build_orchestrator(
        model=object(),
        tools=[],
        checkpointer=object(),
        interrupt_on={},
    )

    assert captured["skills"] == []
