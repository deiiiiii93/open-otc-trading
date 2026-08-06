"""Seeded report templates shipped with the product.

The YAML files here are the source of truth for the four templates migration
0054 inserts with ``source='seed'``. A guard test validates every one of them
against the live block registry, so a seed can never reference a block that
does not exist.
"""
from __future__ import annotations

from pathlib import Path

SEEDS_DIR = Path(__file__).parent


def load_seed_specs() -> dict[str, str]:
    """Return {slug: yaml_text} for every shipped template, keyed by filename stem."""
    return {
        path.stem: path.read_text(encoding="utf-8")
        for path in sorted(SEEDS_DIR.glob("*.yaml"))
    }


__all__ = ["SEEDS_DIR", "load_seed_specs"]
