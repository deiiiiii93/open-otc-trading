"""Per-persona workflow-domain visibility.

Single source of truth consumed by persona specs (skill source lists) and by
skill lint (routing persona-visibility cross-check). Tuple order matters: it
is preserved into each persona's skill source list, which controls catalog
listing order in subagent prompts.
"""
from __future__ import annotations

from typing import Final

PERSONA_WORKFLOW_DOMAINS: Final[dict[str, tuple[str, ...]]] = {
    "trader": (
        "positions",
        "products",
        "try-solve",
        "pricing",
        "hedging",
        "market-data",
        "portfolios",
        "rfq",
        "snowballs",
        "desk-workflows",
    ),
    "risk_manager": (
        "positions",
        "risk",
        "hedging",
        "pricing",
        "market-data",
        "portfolios",
        "reporting",
        "snowballs",
        "desk-workflows",
    ),
    "high_board": (
        "portfolios",
        "reporting",
    ),
}


def workflow_skill_sources(persona: str) -> list[str]:
    """Skill source prefixes for one persona, in declared order."""
    return [
        f"/skills/workflows/{domain}/"
        for domain in PERSONA_WORKFLOW_DOMAINS[persona]
    ]


__all__ = ["PERSONA_WORKFLOW_DOMAINS", "workflow_skill_sources"]
